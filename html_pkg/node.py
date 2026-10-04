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
    )

    ELEMENT = "element"
    TEXT = "text"

    def __init__(
        self,
        node_type: str,
        tag: Optional[str] = None,
        attrs: Optional[dict] = None,
        text: Optional[str] = None,
    ):
        self.node_type = node_type
        self.tag = tag
        self.attrs = attrs if attrs is not None else {}
        self.text = text
        self.children: list[Node] = []
        self.parent: Optional[Node] = None

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

    def has_class(self, cls: str) -> bool:
        if not self.is_element():
            return False
        v = self.attrs.get("class", "")
        return cls in v.split()

    def query_selector_all(self, selector: str) -> list:
        """Supports ``.cls`` and ``#id`` only (toy scope, mirrors the
        legacy ``dom`` Node so the JS engine's querySelectorAll keeps
        working)."""
        selector = selector.strip()
        results: list[Node] = []
        if selector.startswith("."):
            cls = selector[1:]

            def walk_cls(n: Node) -> None:
                if n.has_class(cls):
                    results.append(n)
                for c in n.children:
                    walk_cls(c)

            walk_cls(self)
            return results
        if selector.startswith("#"):
            _id = selector[1:]

            def walk(n: Node) -> None:
                if n.is_element() and n.attrs.get("id") == _id:
                    results.append(n)
                for c in n.children:
                    walk(c)

            walk(self)
            return results
        raise ValueError(f"unsupported selector: {selector}")


#: P24.2 — WHATWG void elements (no closing tag, never pushed).
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def implicitly_closes(tag: str) -> bool:
    """P24.2 — legacy compat helper (a second same-tag start closes
    the first)."""
    return tag in ("p", "li")
