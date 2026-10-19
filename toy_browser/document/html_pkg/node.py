"""P24.5 — DOM Node class (extracted from the legacy ``dom.py`` so the
``html_pkg`` tree builder has no import cycle with the rest of the
browser).

``Node`` is the single element/text node type used across
``css`` / ``layout`` / ``paint`` / ``js``.  ``VOID_TAGS`` is the
WHATWG void-element set (P24.2).
"""
from __future__ import annotations

from typing import Optional


class Node:
    __slots__ = (
        "node_type",
        "tag",
        "attrs",
        "text",
        "children",
        "parent",
        # R7-H01 — document-level parsing metadata (quirks mode / doctype
        # / namespace).  Only the synthetic ``document`` node carries
        # meaningful values; element nodes keep the defaults.
        "quirks_mode",
        "doctype_name",
        "namespace",
        "__weakref__",
    )

    ELEMENT = "element"
    TEXT = "text"

    def __init__(
        self,
        node_type: str,
        tag: Optional[str] = None,
        attrs: Optional[dict] = None,
        text: Optional[str] = None,
        quirks_mode: bool = False,
        doctype_name: Optional[str] = None,
        namespace: Optional[str] = None,
    ):
        self.node_type = node_type
        self.tag = tag
        self.attrs = attrs if attrs is not None else {}
        self.text = text
        self.children: list[Node] = []
        self.parent: Optional[Node] = None
        # R7-H01 — quirks mode: a document with no doctype (or with a
        # doctype whose name is not "html") is parsed in quirks mode.
        self.quirks_mode = quirks_mode
        self.doctype_name = doctype_name
        # R7-H01 — the element's namespace (HTML / SVG / MathML).  The
        # parser only records the basics for now; "html" is the default.
        self.namespace = namespace or ("html" if node_type == Node.ELEMENT else None)

    @classmethod
    def element(cls, tag: str, attrs: Optional[dict] = None) -> "Node":
        return cls(cls.ELEMENT, tag=tag, attrs=attrs)

    @classmethod
    def text_node(cls, text: str) -> "Node":
        return cls(cls.TEXT, text=text)

    def is_element(self) -> bool:
        return self.node_type == Node.ELEMENT

    def tag_name(self) -> Optional[str]:
        return self.tag if self.is_element() else None

    def attr(self, name: str) -> Optional[str]:
        return self.attrs.get(name)

    def set_attr(self, name: str, value: str) -> None:
        self.attrs[name] = value

    def text_content(self) -> str:
        """Concatenated text of this node's subtree (P24.5 — unchanged
        from the legacy ``dom`` Node so all downstream consumers keep
        working)."""
        if not self.is_element():
            return self.text or ""
        return "".join(
            c.text if not c.is_element() else c.text_content()
            for c in self.children
        )

    def find_by_id(self, _id: str) -> Optional["Node"]:
        def walk(n: "Node") -> Optional["Node"]:
            if n.is_element() and n.attrs.get("id") == _id:
                return n
            for c in n.children:
                f = walk(c)
                if f:
                    return f
            return None

        return walk(self)

    # --- P30.2 — DOM node mutation helpers ---------------------------------
    # (querySelectorAll, appendChild, removeChild, insertBefore,
    #  replaceChild live on the DOM Node so every downstream consumer
    #  (css / layout / js / browser) sees the same tree.)

    def append_child(self, child: "Node") -> "Node":
        """Append *child* as the last child, re-parenting it (toy:
        detaches it from any previous parent).  Returns *child*."""
        self._detach(child)
        child.parent = self
        self.children.append(child)
        return child

    def insert_before(self, child: "Node", ref: Optional["Node"]) -> "Node":
        """Insert *child* before sibling *ref* (or append when *ref* is
        ``None`` / not a child of this node)."""
        self._detach(child)
        if ref is not None and ref in self.children:
            i = self.children.index(ref)
            self.children.insert(i, child)
        else:
            self.children.append(child)
        child.parent = self
        return child

    def remove_child(self, child: "Node") -> "Node":
        """Detach *child*; returns the removed node (no-op if not a child)."""
        if child in self.children:
            self.children.remove(child)
        child.parent = None
        return child

    def replace_child(self, new_node: "Node", old_node: "Node") -> "Node":
        """Swap *old_node* for *new_node* at the same position."""
        self._detach(new_node)
        if old_node in self.children:
            i = self.children.index(old_node)
            self.children[i] = new_node
            old_node.parent = None
            new_node.parent = self
        else:
            self.children.append(new_node)
            new_node.parent = self
        return old_node

    @staticmethod
    def _detach(child: "Node") -> None:
        p = child.parent
        if p is not None:
            try:
                p.children.remove(child)
            except ValueError:
                pass
            child.parent = None

    def has_class(self, cls: str) -> bool:
        if not self.is_element():
            return False
        v = self.attrs.get("class", "")
        return cls in v.split()

    def _iterate_elements(self, include_self: bool = False):
        """Depth-first pre-order walk over elements in document order.

        When *include_self* is True and this node is an element, it is
        yielded first (legacy ``Node.query_selector_all`` semantics: a
        selector search on an element includes that element itself, so
        ``div.query_selector_all('.c')`` finds ``div`` when it has
        class ``c``).  Document-node scope (``document``) has no
        element identity, so it naturally excludes itself.
        """
        if include_self and self.is_element():
            yield self
        for c in self.children:
            if c.is_element():
                yield c
            yield from c._iterate_elements()

    def query_selector_all(self, selector: str, interaction_state: dict | None = None) -> list:
        """P30.1 — full CSS selector engine (reuses ``css.Selector``).

        Supports simple selectors (``tag`` / ``.cls`` / ``#id`` /
        ``.a.b``), descendant (``a b``), child (``a > b``), comma
        selector lists, and pseudo-classes (``:hover`` / ``:focus`` /
        ``:first-child`` / ``:last-child`` / ``:nth-child(...)``).
        Results are in document order, and the search scope includes
        the calling element itself (legacy semantics) when the caller
        is an element; a document node's scope covers its descendants
        only.  Pseudo-classes that depend on interaction state
        (``:hover`` / ``:focus``) only match when an *interaction_state*
        dict is supplied; structural pseudo-classes match against the
        DOM directly.
        """
        from toy_browser.style import css

        selector = (selector or "").strip()
        if not selector:
            return []
        sels = css.parse_selector_list(selector)
        results: list[Node] = []
        for node in self._iterate_elements(include_self=True):
            for s in sels:
                try:
                    hit = s.matches(node, interaction_state)
                except Exception:
                    hit = False
                if hit:
                    results.append(node)
                    break  # one hit per node, document order
        return results

    def query_selector(self, selector: str, interaction_state: dict | None = None):
        """P30.1 — first match of :meth:`query_selector_all` or ``None``."""
        out = self.query_selector_all(selector, interaction_state)
        return out[0] if out else None


#: P24.2 — WHATWG void elements (no closing tag, never pushed).
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def implicitly_closes(tag: str) -> bool:
    """P24.2 — legacy compat helper (a second same-tag start closes
    the first)."""
    return tag in ("p", "li")
