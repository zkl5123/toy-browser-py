"""P24.2/P24.4 — HTML tree builder (byte-stream → DOM tree).

Implements a fault-tolerant, simplified subset of the WHATWG tree
construction algorithm:

* **Void elements** (``<br>`` ``<img>`` ``<input>`` …) never push onto
  the open-element stack (P24.2).
* **Implicit sibling close** (P24.2): a second ``<p>`` closes the
  first; a second ``<li>`` closes the first; a block-level start tag
  closes an open ``<p>``.
* **Raw-text mode** (P24.1): ``<script>`` / ``<style>`` /
  ``<textarea>`` content is not parsed as HTML.
* **Formatting-element reconstitution** (P24.4, simplified adoption
  agency): ``<b><i>x</b></i>`` — when ``</b>`` arrives but ``i`` is
  open on top, the in-between element ``<i>`` is re-parented under
  ``<b>`` and closed; ``<b>`` stays open so the subsequent ``</i>``
  (if any) or document end pops it.  Text already attached to ``<i>``
  moves with it.
* **Foster parenting** (P24.3, toy): start tags that don't belong
  inside ``<table>`` (e.g. ``<tr>`` appearing outside ``<table>``) are
  appended to the nearest non-table ancestor.
* **Case-insensitive tag names** (P24.3): ``<DIV>`` == ``<div>``.

The public entry point is :func:`build` (tokens → Node tree) and
:func:`parse` (bytes/str → Node tree).  The DOM ``Node`` class lives
in :mod:`html_pkg.node` (kept separate so the tree builder has no
import cycle with the rest of the browser).
"""
from __future__ import annotations

from .node import Node, VOID_TAGS
from .tokenize import tokenize_rawtext

# ---------------------------------------------------------------------------
# P24.2 — implicit-close tables
# ---------------------------------------------------------------------------

#: Tags that close an open sibling of the same tag (P24.2).
_IMPLICIT_SIBLING_CLOSE = {"p", "li", "dt", "dd"}

#: Block-level tags that close an open <p> (P24.2).
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "main", "nav", "ol", "p", "pre",
    "section", "table", "ul",
}

#: P24.4 — format tags that trigger the simplified adoption agency
#: (they may be nested without explicit close; a stray closer
#: reconstitutes the nesting).
_FORMATTING_TAGS = {"b", "i", "u", "s", "em", "strong", "small",
                    "mark", "sub", "sup", "span"}


def _foster_parent(stack: list[Node]) -> Node:
    """P24.3 — toy foster parenting.

    When a table-row / cell start tag appears *outside* a table
    context (i.e. the current open element is not a table-related
    element), attach it to the nearest ancestor that is a *non-table*
    container — the foster parent.  We walk the stack from the top and
    return the first node whose tag is not in the table context.
    """
    TABLE_CONTEXT = {"table", "thead", "tbody", "tfoot", "tr",
                    "td", "th", "caption", "colgroup"}
    for k in range(len(stack) - 1, -1, -1):
        tag = stack[k].tag
        if tag not in TABLE_CONTEXT:
            return stack[k]
    return stack[0]


def _reconstitute_formatting(stack: list[Node],
                             open_tags: list[str],
                             tag: str,
                             stack_idx: int) -> None:
    """P24.4 — simplified adoption agency.

    *stack_idx* is the position of *tag* in *stack* (which always has
    one more entry than *open_tags* because the synthetic document root
    is on the stack but not in the open-tag list).  The elements
    between *stack_idx* and the open top are re-parented under *tag*
    and closed; *tag* itself stays open so its own close tag can pop
    it.

    Example: ``<b><i>x</b></i>`` — when ``</b>`` arrives, ``i`` is
    open on top of ``b``.  The in-between element ``<i>`` is moved
    under ``<b>`` and closed; ``<b>`` remains open for the subsequent
    ``</i>``.
    """
    match_node = stack[stack_idx]
    in_between = list(stack[stack_idx + 1:])
    # Move each in-between element under the match, preserving
    # document order; their children (text) follow them.
    for el in in_between:
        cur = el.parent
        if cur is not None and el in cur.children:
            cur.children.remove(el)
        el.parent = match_node
        match_node.children.append(el)
    # Close only the in-between elements (the stray ones); the match
    # element stays open for its own close tag.
    open_offset = stack_idx - 1  # stack has one extra entry (root)
    del open_tags[open_offset + 1:]
    del stack[stack_idx + 1:]


def build(tokens: list, root: Node | None = None) -> Node:
    """P24.2/4 — build a DOM tree from a flat *tokens* list.

    *tokens* is the output of :func:`html_pkg.tokenize.tokenize_rawtext`
    (or the legacy :func:`tokenize`).  Returns the root
    ``document`` node.

    The open-element stack (``open_tags``) tracks every real element
    that has been pushed; the *synthetic* document root is **not**
    in ``open_tags``.  Because of this, the index into ``stack`` is
    always one greater than the index into ``open_tags``.
    """
    root = root or Node.element("document")
    stack: list[Node] = [root]
    open_tags: list[str] = []

    for tok in tokens:
        kind = tok[0]
        if kind == "text":
            text = tok[1]
            if text.strip():
                # P24.2 — fault tolerance: merge adjacent text tokens
                # (the tokenizer may split on < that is not a tag).
                if stack[-1].children and \
                        stack[-1].children[-1].node_type == Node.TEXT:
                    stack[-1].children[-1].text += text
                else:
                    node = Node.text_node(text)
                    node.parent = stack[-1]
                    stack[-1].children.append(node)
        elif kind in ("comment", "doctype"):
            # P24.3 — comments and DOCTYPEs are ignored in the DOM
            # (the spec keeps them as nodes; the toy drops them to keep
            # the tree clean and the renderer simple).
            continue
        elif kind == "start":
            tag = tok[1].lower()
            attrs = tok[2]
            # P24.2 — implicit sibling close (second <p> closes first,
            # second <li> closes first, …).
            if tag in _IMPLICIT_SIBLING_CLOSE and open_tags and \
                    open_tags[-1] == tag:
                open_tags.pop()
                stack.pop()
            # P24.2 — a block-level start tag closes an open <p>
            # (spec "implicit close" for <p> before block elements).
            if tag in _BLOCK_TAGS and tag != "p" and "p" in open_tags:
                while open_tags and open_tags[-1] != "p":
                    open_tags.pop()
                    stack.pop()
                if open_tags and open_tags[-1] == "p":
                    open_tags.pop()
                    stack.pop()
            node = Node.element(tag, attrs)
            # P24.3 — foster parenting: a <tr>/<td>/<th>/<colgroup>
            # start tag that appears *outside* a table context is
            # attached to the nearest non-table ancestor (the foster
            # parent).  If the current open element is already a
            # table-related element, the tag belongs there (no foster).
            if tag in ("tr", "td", "th", "colgroup"):
                TABLE_CONTEXT = {"table", "thead", "tbody", "tfoot",
                                 "tr", "td", "th", "caption", "colgroup"}
                if stack[-1].tag in TABLE_CONTEXT:
                    parent = stack[-1]
                else:
                    parent = _foster_parent(stack)
            else:
                parent = stack[-1]
            node.parent = parent
            parent.children.append(node)
            if tag not in VOID_TAGS:
                stack.append(node)
                open_tags.append(tag)
        elif kind == "end":
            tag = tok[1].lower()
            if not open_tags:
                continue
            if open_tags[-1] == tag:
                open_tags.pop()
                stack.pop()
            elif tag in open_tags:
                # P24.4 — formatting-element reconstitution
                # (simplified adoption agency).
                ot_idx = len(open_tags) - 1 - open_tags[::-1].index(tag)
                stack_idx = ot_idx + 1
                _reconstitute_formatting(stack, open_tags, tag, stack_idx)
                # The match element stays open; pop it now (the close
                # tag *is* the match's close).
                open_tags.pop()
                stack.pop()
            else:
                # P24.2 — fault tolerance: a stray close tag whose
                # matching start was already closed (or never opened)
                # is ignored (spec: ignore mismatched end tags in
                # "in body" insertion mode).
                # The document end implicitly closes all open elements.
                pass
    # P24.2 — implicit close at document end: any still-open elements
    # are closed (the spec adopts them under the body).
    return root


def parse(data: "str | bytes", root: Node | None = None) -> Node:
    """P24.5 — parse *data* (bytes or str) into a DOM tree."""
    return build(tokenize_rawtext(data), root=root)
