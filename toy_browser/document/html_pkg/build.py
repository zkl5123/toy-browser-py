"""P24.2/P24.4 — HTML tree builder (byte-stream → DOM tree).

Implements a fault-tolerant, simplified subset of the WHATWG tree
construction algorithm:

* **Void elements** (``<br>`` ``<img>`` ``<input>`` …) never push onto
  the open-element stack (P24.2).
* **Implicit sibling close** (P24.2): a second ``<p>`` closes the
  first; a second ``<li>`` closes the first; a block-level start tag
  closes an open ``<p>``.
* **Raw-text mode** (P24.1): ``<script>`` / ``<style>`` content is not
  parsed as HTML (the tokenizer owns that; see
  :mod:`html_pkg.tokenize`).
* **Formatting-element reconstitution** (P24.4, simplified adoption
  agency): ``<b><i>x</b></i>`` — when ``</b>`` arrives but ``i`` is
  open on top, the in-between element ``<i>`` is re-parented under
  ``<b>`` and closed; ``<b>`` stays open so the subsequent ``</i>``
  (if any) or document end pops it.  Text already attached to ``<i>``
  moves with it.
* **Case-insensitive tag names** (P24.3): ``<DIV>`` == ``<div>``.

R7-H01 — the builder now distinguishes **document** parsing from
**fragment** parsing and implements the insertion modes the assessment
called out:

* **Implicit ``html`` / ``head`` / ``body``** — a byte stream carrying
  a doctype or an ``<html>`` start tag builds ``html > head + body``
  even when the author omits them (``<!DOCTYPE html><p>x</p>``).
  Fragment inputs (the common ``dom.parse("<div>…</div>")`` shape used
  by the test-suite and by ``innerHTML``-style callers) keep the
  historical flat tree — see :func:`build`'s *document_mode*.
* **Implicit ``tbody`` / ``tr``** — ``<table><tr><td>`` produces
  ``table > tbody > tr > td`` in both modes (a ``<tr>`` directly inside
  ``<table>`` is invalid markup that browsers repair).
* **Foster parenting** — content that does not belong directly inside a
  ``<table>`` is inserted *before* the table (the spec's insertion
  location) instead of being appended to a far-away ancestor.
* **Table insertion modes** — ``in table`` / ``in table body`` /
  ``in row`` / ``in cell`` are derived from the open-element stack (the
  spec's "reset the insertion mode appropriately"), so closing a cell
  or a row restores the right mode.
* **Quirks mode** — a missing or non-``html`` doctype sets
  ``document.quirks_mode`` so later layers (CSS/layout) can consult it.

J02 interface — :func:`build` / :func:`parse` accept a *script_hook*
that is invoked with every inserted ``<script>`` element.  A future
script-aware driver can fetch an external ``src`` there and return the
script text to inject, which is the pause/resume seam the assessment
asks the parser to expose up front ("parser 与 J02 的脚本暂停/恢复接口
一并设计").

The public entry points are :func:`build` (tokens → Node tree),
:func:`parse` (bytes/str → Node tree) and :func:`parse_fragment`
(fragment, no implicit document structure).
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

#: R7-H01 — elements that may live inside <head> (WHATWG "in head").
_HEAD_TAGS = {"base", "basefont", "bgsound", "link", "meta", "title",
              "noscript", "script", "style", "template", "noframes"}

#: R7-H01 — the table context: an element stack inside these tags means
#: the parser is in a table-related insertion mode.
_TABLE_CONTEXT = {"table", "thead", "tbody", "tfoot", "tr", "td", "th",
                  "caption", "colgroup", "template"}

#: R7-H01 — row groups: the implicit wrapper a bare <tr> needs.
_ROW_GROUP_TAGS = ("tbody", "thead", "tfoot")


def _foster_parent(stack: list[Node]) -> Node:
    """P24.3 — toy foster parenting.

    When a table-row / cell start tag appears *outside* a table
    context (i.e. the current open element is not a table-related
    element), attach it to the nearest ancestor that is a *non-table*
    container — the foster parent.  We walk the stack from the top and
    return the first node whose tag is not in the table context.

    R7-H01 — this legacy helper is only used by the *fragment* builder
    for the historical ``<div><tr>`` shape; :meth:`_DocumentBuilder._foster`
    implements the spec's insertion location (immediately before the
    table) for the document path.
    """
    for k in range(len(stack) - 1, -1, -1):
        tag = stack[k].tag
        if tag not in _TABLE_CONTEXT:
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
    if stack_idx >= len(stack):
        return
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


def _add_text(parent: Node, text: str) -> None:
    """Append *text* to *parent*, merging with a trailing text node
    (P24.2 — the tokenizer may split a run on a ``<`` that is not a
    tag, and whitespace-only runs must survive: dropping them loses
    the space between two inline elements)."""
    if not text:
        return
    if parent.children and parent.children[-1].node_type == Node.TEXT:
        parent.children[-1].text += text
    else:
        node = Node.text_node(text)
        node.parent = parent
        parent.children.append(node)


def _apply_script_hook(node: Node, script_hook) -> None:
    """R7-H01/J02 — the parser's script pause/resume seam.

    *script_hook* is called with the freshly inserted ``<script>``
    element.  A driver (J02) can resolve an external ``src`` there and
    return the script text, which replaces the element's content;
    returning ``None`` leaves the element untouched.  A hook that
    raises is ignored (a script must never break tree construction).
    """
    try:
        text = script_hook(node)
    except Exception:
        return
    if text is None:
        return
    node.children = []
    child = Node.text_node(text)
    child.parent = node
    node.children.append(child)


def _looks_like_document(tokens: list) -> bool:
    """R7-H01 — decide whether *tokens* describe a full document.

    A doctype or an ``<html>`` start tag is the unambiguous signal;
    everything else (``<body><p>x</p></body>``, ``<div id=a>…</div>``,
    ``<table><tr>…``) is treated as a *fragment*, which keeps the
    historical flat tree for the ~50 tests and callers that address
    ``root.children[0]`` directly.
    """
    for tok in tokens:
        kind = tok[0]
        if kind == "doctype":
            return True
        if kind == "start" and tok[1] == "html":
            return True
    return False


# ---------------------------------------------------------------------------
# Fragment builder (P24.2/3/4 — the historical flat tree)
# ---------------------------------------------------------------------------

def _build_flat(root: Node, tokens: list, script_hook=None) -> Node:
    """Fragment tree construction — no implicit html/head/body."""
    stack: list[Node] = [root]
    open_tags: list[str] = []

    for tok in tokens:
        kind = tok[0]
        if kind == "text":
            _add_text(stack[-1], tok[1])
        elif kind in ("comment", "doctype"):
            # P24.3 — comments and DOCTYPEs are dropped from the DOM.
            continue
        elif kind == "start":
            tag = tok[1].lower()
            attrs = tok[2]
            # P24.2 — implicit sibling close.
            if tag in _IMPLICIT_SIBLING_CLOSE and open_tags and \
                    open_tags[-1] == tag:
                open_tags.pop()
                stack.pop()
            # P24.2 — a block-level start tag closes an open <p>.
            if tag in _BLOCK_TAGS and tag != "p" and "p" in open_tags:
                while open_tags and open_tags[-1] != "p":
                    open_tags.pop()
                    stack.pop()
                if open_tags and open_tags[-1] == "p":
                    open_tags.pop()
                    stack.pop()
            # R7-H01 — implicit <tbody> / <tr> repair.
            if tag == "tr" and stack[-1].tag == "table":
                tbody = Node.element("tbody")
                tbody.parent = stack[-1]
                stack[-1].children.append(tbody)
                stack.append(tbody)
                open_tags.append("tbody")
            elif tag in ("td", "th") and stack[-1].tag in _ROW_GROUP_TAGS:
                tr = Node.element("tr")
                tr.parent = stack[-1]
                stack[-1].children.append(tr)
                stack.append(tr)
                open_tags.append("tr")
            elif tag in ("td", "th") and stack[-1].tag == "table":
                tbody = Node.element("tbody")
                tbody.parent = stack[-1]
                stack[-1].children.append(tbody)
                stack.append(tbody)
                open_tags.append("tbody")
                tr = Node.element("tr")
                tr.parent = tbody
                tbody.children.append(tr)
                stack.append(tr)
                open_tags.append("tr")
            node = Node.element(tag, attrs)
            # P24.3 — toy foster parenting for table parts that appear
            # outside any table context (kept: the historical
            # ``<div><tr>`` shape must stay addressable).
            if tag in ("tr", "td", "th", "colgroup") and \
                    stack[-1].tag not in _TABLE_CONTEXT:
                parent = _foster_parent(stack)
            else:
                parent = stack[-1]
            node.parent = parent
            parent.children.append(node)
            if script_hook is not None and tag == "script":
                _apply_script_hook(node, script_hook)
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
                if tag in _TABLE_CONTEXT:
                    # R7-H01 — a table end tag closes the whole row /
                    # cell / row-group stack opened inside it.
                    while open_tags and open_tags[-1] != tag:
                        open_tags.pop()
                        stack.pop()
                    if open_tags:
                        open_tags.pop()
                        stack.pop()
                else:
                    # P24.4 — formatting-element reconstitution.
                    ot_idx = len(open_tags) - 1 - open_tags[::-1].index(tag)
                    stack_idx = ot_idx + 1
                    if stack_idx < len(stack):
                        _reconstitute_formatting(stack, open_tags, tag, stack_idx)
                        open_tags.pop()
                        stack.pop()
            # else: stray close tag → ignored (P24.2).
    return root


# ---------------------------------------------------------------------------
# R7-H01 — document builder with insertion modes
# ---------------------------------------------------------------------------

class _DocumentBuilder:
    """Tree construction for a *complete* HTML document.

    Mirrors the WHATWG algorithm's structural phases
    (``initial`` → ``before html`` → ``before head`` → ``in head`` →
    ``after head`` → ``in body`` → ``after body``) plus the
    table-related modes, which are derived from the open-element stack
    (the spec's "reset the insertion mode appropriately") so closing a
    cell or a row group restores the correct mode.
    """

    def __init__(self, root: Node, script_hook=None):
        self.root = root
        self.stack: list[Node] = [root]
        self.open_tags: list[str] = []
        #: structural phase; table modes are stack-derived (see :meth:`_mode`).
        self.phase = "initial"
        self.html: Node | None = None
        self.head: Node | None = None
        self.body: Node | None = None
        self.script_hook = script_hook
        #: R7-H01 — re-process budget for one start tag (see :meth:`_start`).
        self._reprocess = 0

    # -- stack helpers ---------------------------------------------------
    def _current(self) -> Node:
        return self.stack[-1]

    def _insert(self, node: Node) -> Node:
        node.parent = self._current()
        self._current().children.append(node)
        return node

    def _push(self, node: Node, tag: str) -> Node:
        self._insert(node)
        self.stack.append(node)
        self.open_tags.append(tag)
        return node

    def _pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self.open_tags.pop()

    def _pop_until(self, stop_tags, inclusive: bool = True) -> None:
        """Pop while the top tag is not in *stop_tags*; also pop the
        stopping tag when *inclusive*."""
        while self.open_tags and self.open_tags[-1] not in stop_tags:
            self._pop()
        if inclusive and self.open_tags:
            self._pop()

    def _pop_to_table_context(self) -> None:
        while self.open_tags and self.open_tags[-1] not in _TABLE_CONTEXT:
            self._pop()

    def _pop_to_row_group_context(self) -> None:
        while self.open_tags and self.open_tags[-1] not in (
                _ROW_GROUP_TAGS + ("table", "html", "template")):
            self._pop()

    def _pop_to_row_context(self) -> None:
        while self.open_tags and self.open_tags[-1] not in (
                ("tr",) + _ROW_GROUP_TAGS + ("table", "html", "template")):
            self._pop()

    # -- insertion mode --------------------------------------------------
    def _stack_mode(self) -> str:
        """WHATWG "reset the insertion mode appropriately" (toy subset):
        the innermost open table-related element decides the mode."""
        for k in range(len(self.open_tags) - 1, -1, -1):
            t = self.open_tags[k]
            if t in ("td", "th"):
                return "in_cell"
            if t == "tr":
                return "in_row"
            if t in _ROW_GROUP_TAGS:
                return "in_table_body"
            if t == "caption":
                return "in_caption"
            if t == "colgroup":
                return "in_column_group"
            if t == "table":
                return "in_table"
            if t == "body":
                return "in_body"
            if t == "head":
                return "in_head"
            if t == "html":
                return "before_head"
        return "initial"

    def _mode(self) -> str:
        if self.phase in ("initial", "before_html", "before_head",
                          "after_head", "after_body", "after_after_body"):
            return self.phase
        return self._stack_mode()

    # -- structural element creation -------------------------------------
    def _ensure_html(self) -> Node:
        if self.html is None:
            self.html = Node.element("html")
            self.html.parent = self.root
            self.root.children.append(self.html)
            self.stack.append(self.html)
            self.open_tags.append("html")
        elif self.html not in self.stack:
            self.stack.append(self.html)
            self.open_tags.append("html")
        return self.html

    def _open_html(self, attrs: dict) -> Node:
        html = self._ensure_html()
        for k, v in (attrs or {}).items():
            if k not in html.attrs:      # duplicate <html>: first wins
                html.attrs[k] = v
        return html

    def _ensure_head(self) -> Node:
        self._ensure_html()
        if self.head is None:
            self.head = Node.element("head")
            self.head.parent = self.html
            self.html.children.append(self.head)
            self.stack.append(self.head)
            self.open_tags.append("head")
        return self.head

    def _close_head(self) -> None:
        if self.head is not None and self.open_tags and \
                self.open_tags[-1] == "head":
            self._pop()
        self.phase = "after_head"

    def _ensure_body(self) -> Node:
        self._ensure_html()
        if self.head is not None and self.open_tags and \
                self.open_tags[-1] == "head":
            self._close_head()
        if self.body is None:
            self.body = Node.element("body")
            self.body.parent = self.html
            self.html.children.append(self.body)
            self.stack.append(self.body)
            self.open_tags.append("body")
        elif self.body not in self.stack:
            self.stack.append(self.body)
            self.open_tags.append("body")
        self.phase = "in_body"
        return self.body

    def _open_body(self, attrs: dict) -> Node:
        body = self._ensure_body()
        for k, v in (attrs or {}).items():
            if k not in body.attrs:      # duplicate <body>: first wins
                body.attrs[k] = v
        return body

    # -- foster parenting ------------------------------------------------
    def _foster(self, node: Node) -> Node:
        """R7-H01 — insert *node* immediately **before** the innermost
        open ``<table>`` (the spec's foster-parenting insertion
        location), rather than appending it to a distant ancestor.
        """
        table = None
        for k in range(len(self.stack) - 1, 0, -1):
            if self.stack[k].tag == "table":
                table = self.stack[k]
                break
        if table is None or table.parent is None:
            return self._insert(node)
        p = table.parent
        idx = p.children.index(table) if table in p.children else len(p.children)
        node.parent = p
        p.children.insert(idx, node)
        return node

    # -- token dispatch --------------------------------------------------
    def run(self, tokens: list) -> Node:
        for tok in tokens:
            kind = tok[0]
            if kind == "doctype":
                self._doctype(tok[1])
            elif kind == "comment":
                # P24.3 — comments are dropped from the DOM.
                continue
            elif kind == "text":
                self._text(tok[1])
            elif kind == "start":
                self._reprocess = 0
                self._start(tok[1].lower(), tok[2])
            elif kind == "end":
                self._end(tok[1].lower())
        # R7-H01 — quirks mode: a document with no doctype at all is in
        # quirks mode too (only a doctype token can clear it).
        if self.root.doctype_name is None:
            self.root.quirks_mode = True
        return self.root

    def _doctype(self, name: str) -> None:
        if self.root.doctype_name is None:
            self.root.doctype_name = name
            # R7-H01 — quirks mode: no doctype, or a doctype whose name
            # is not "html", puts the document in quirks mode.
            self.root.quirks_mode = (name != "html")
        if self.phase == "initial":
            self.phase = "before_html"

    def _text(self, text: str) -> None:
        if not text:
            return
        # R7-H01 — text inside a head-level raw-text / RCDATA element
        # (<title>, <script>, <style>, …) belongs to that element; it
        # does NOT end the head (the tokenizer already resolved the
        # content model, so the text is simply the element's content).
        if self.open_tags and self.open_tags[-1] in _HEAD_TAGS:
            _add_text(self._current(), text)
            return
        m = self._mode()
        if m in ("initial", "before_html", "before_head"):
            # Whitespace outside the body is dropped (the spec ignores
            # it; keeping it would push the body down by one line).
            if not text.strip():
                return
            self._ensure_body()
            _add_text(self._current(), text)
            return
        if m == "in_head":
            if not text.strip():
                _add_text(self._current(), text)
                return
            self._close_head()
            self._ensure_body()
            _add_text(self._current(), text)
            return
        if m == "after_head":
            if not text.strip():
                return
            self._ensure_body()
            _add_text(self._current(), text)
            return
        if m in ("in_table", "in_table_body", "in_row"):
            # WHATWG "in table text": whitespace-only characters stay in
            # the table; any run containing non-whitespace is
            # foster-parented before it.
            if not text.strip():
                _add_text(self._current(), text)
            else:
                self._foster(Node.text_node(text))
            return
        _add_text(self._current(), text)

    def _start(self, tag: str, attrs: dict) -> None:
        # R7-H01 — the mode transitions re-process a token ("reprocess
        # the token in the new insertion mode"); guard against a
        # pathological loop so a malformed document can never blow the
        # Python stack (``self._reprocess`` is reset per token in
        # :meth:`run`).
        if self._reprocess > 12:
            self._start_in_body(tag, attrs)
            return
        self._reprocess += 1
        m = self._mode()
        if m == "initial":
            self._ensure_html()
            self.phase = "before_head"
            self._start(tag, attrs)
            return
        if m == "before_html":
            self._open_html(attrs) if tag == "html" else self._ensure_html()
            if tag == "html":
                self.phase = "before_head"
                return
            self.phase = "before_head"
            self._start(tag, attrs)
            return
        if m == "before_head":
            if tag == "html":
                # WHATWG — a repeated <html> merges its attributes into
                # the document element; it never opens a second one.
                self._open_html(attrs)
                return
            if tag == "head":
                self._ensure_html()
                head = Node.element("head", attrs)
                self.head = self._push(head, "head")
                self.phase = "in_head"
                return
            self._ensure_head()
            self.phase = "in_head"
            self._start(tag, attrs)
            return
        if m == "in_head":
            if tag == "html":
                self._close_head()
                self._start(tag, attrs)
                return
            if tag in _HEAD_TAGS:
                node = Node.element(tag, attrs)
                self._insert(node)
                if tag not in VOID_TAGS:
                    self.stack.append(node)
                    self.open_tags.append(tag)
                if self.script_hook is not None and tag == "script":
                    _apply_script_hook(node, self.script_hook)
                return
            # Anything else ends <head>: content belongs to the body.
            self._close_head()
            self._start(tag, attrs)
            return
        if m == "after_head":
            if tag == "body":
                self._open_body(attrs)
                return
            self._ensure_body()
            self._start(tag, attrs)
            return
        if m == "after_body" or m == "after_after_body":
            # Content after </body> re-opens the body (spec behaviour).
            self._ensure_body()
            self._start(tag, attrs)
            return
        if m == "in_table":
            self._start_in_table(tag, attrs)
            return
        if m == "in_table_body":
            self._start_in_table_body(tag, attrs)
            return
        if m == "in_row":
            self._start_in_row(tag, attrs)
            return
        # in_body / in_cell / in_caption / in_column_group
        self._start_in_body(tag, attrs)

    def _start_in_body(self, tag: str, attrs: dict) -> None:
        if tag == "html":
            # WHATWG — a stray <html> in body merges its attributes into
            # the document element (parse error, no new element).
            if self.html is not None:
                for k, v in (attrs or {}).items():
                    if k not in self.html.attrs:
                        self.html.attrs[k] = v
            return
        if tag in ("head", "body"):
            # A stray <head>/<body> merges into the existing one.
            target = self.head if tag == "head" else self.body
            if target is not None:
                for k, v in (attrs or {}).items():
                    if k not in target.attrs:
                        target.attrs[k] = v
            return
        # P24.2 — implicit sibling close (second <p> closes first, …).
        if tag in _IMPLICIT_SIBLING_CLOSE and self.open_tags and \
                self.open_tags[-1] == tag:
            self._pop()
        # P24.2 — a block-level start tag closes an open <p>.
        if tag in _BLOCK_TAGS and tag != "p" and "p" in self.open_tags:
            while self.open_tags and self.open_tags[-1] != "p":
                self._pop()
            if self.open_tags and self.open_tags[-1] == "p":
                self._pop()
        node = Node.element(tag, attrs)
        if tag in VOID_TAGS:
            self._insert(node)
        else:
            self._push(node, tag)
        if self.script_hook is not None and tag == "script":
            _apply_script_hook(node, self.script_hook)

    def _start_in_table(self, tag: str, attrs: dict) -> None:
        if tag in ("caption", "colgroup") or tag in _ROW_GROUP_TAGS:
            self._pop_to_table_context()
            node = Node.element(tag, attrs)
            self._push(node, tag)
            return
        if tag == "col":
            self._pop_to_table_context()
            self._insert(Node.element(tag, attrs))
            return
        if tag == "tr":
            # R7-H01 — implicit <tbody>.
            self._pop_to_table_context()
            self._push(Node.element("tbody"), "tbody")
            self._start_in_table_body(tag, attrs)
            return
        if tag in ("td", "th"):
            # R7-H01 — implicit <tbody> + <tr>.
            self._pop_to_table_context()
            self._push(Node.element("tbody"), "tbody")
            self._push(Node.element("tr"), "tr")
            self._start_in_row(tag, attrs)
            return
        if tag == "table":
            # A nested <table> closes the current one first.
            self._pop_until(("table",), inclusive=True)
            self._start(tag, attrs)
            return
        if tag in ("style", "script"):
            node = Node.element(tag, attrs)
            self._push(node, tag)
            if self.script_hook is not None and tag == "script":
                _apply_script_hook(node, self.script_hook)
            return
        # Anything else is foster-parented before the table.
        node = Node.element(tag, attrs)
        self._foster(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)
            self.open_tags.append(tag)

    def _start_in_table_body(self, tag: str, attrs: dict) -> None:
        if tag == "tr":
            self._pop_to_row_group_context()
            self._push(Node.element(tag, attrs), tag)
            return
        if tag in ("td", "th"):
            # R7-H01 — implicit <tr>.
            self._pop_to_row_group_context()
            self._push(Node.element("tr"), "tr")
            self._start_in_row(tag, attrs)
            return
        if tag in _ROW_GROUP_TAGS:
            self._pop_to_row_group_context()
            if self.open_tags and self.open_tags[-1] in _ROW_GROUP_TAGS:
                self._pop()
            self._push(Node.element(tag, attrs), tag)
            return
        if tag == "table":
            self._pop_to_row_group_context()
            if self.open_tags and self.open_tags[-1] in _ROW_GROUP_TAGS:
                self._pop()
            self._start(tag, attrs)
            return
        node = Node.element(tag, attrs)
        self._foster(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)
            self.open_tags.append(tag)

    def _start_in_row(self, tag: str, attrs: dict) -> None:
        if tag in ("td", "th"):
            self._pop_to_row_context()
            self._push(Node.element(tag, attrs), tag)
            return
        if tag == "tr":
            self._pop_to_row_context()
            if self.open_tags and self.open_tags[-1] == "tr":
                self._pop()
            self._push(Node.element(tag, attrs), tag)
            return
        if tag in _ROW_GROUP_TAGS or tag == "table":
            self._pop_to_row_context()
            if self.open_tags and self.open_tags[-1] == "tr":
                self._pop()
            self._start(tag, attrs)
            return
        node = Node.element(tag, attrs)
        self._foster(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)
            self.open_tags.append(tag)

    def _end(self, tag: str) -> None:
        m = self._mode()
        if tag == "head" and m == "in_head":
            self._close_head()
            return
        if tag == "body":
            self._pop_until(("body",), inclusive=False)
            self.phase = "after_body"
            return
        if tag == "html":
            self._pop_until(("html",), inclusive=False)
            self.phase = "after_after_body"
            return
        if tag in _TABLE_CONTEXT:
            if tag in self.open_tags:
                self._pop_until((tag,), inclusive=True)
            return
        if self.open_tags and self.open_tags[-1] == tag:
            self._pop()
            return
        if tag in self.open_tags:
            if tag in _FORMATTING_TAGS:
                ot_idx = len(self.open_tags) - 1 - self.open_tags[::-1].index(tag)
                stack_idx = ot_idx + 1
                if stack_idx < len(self.stack):
                    _reconstitute_formatting(self.stack, self.open_tags, tag,
                                             stack_idx)
                    self._pop()
                return
            if tag in _IMPLICIT_SIBLING_CLOSE:
                self._pop_until((tag,), inclusive=True)
                return
            # P24.2 — a stray close tag for an element that is not open
            # at the top and is not a formatting element: ignore it.
            return
        # Unknown close tag → ignored (P24.2 fault tolerance).


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def build(tokens: list, root: Node | None = None, script_hook=None,
          document_mode: "bool | None" = None) -> Node:
    """P24.2/4 — build a DOM tree from a flat *tokens* list.

    *tokens* is the output of :func:`html_pkg.tokenize.tokenize_rawtext`.
    Returns the root ``document`` node.

    R7-H01 — *document_mode* selects the construction algorithm:

    * ``True`` — full document: implicit ``html`` / ``head`` / ``body``,
      the table insertion modes and proper foster parenting.
    * ``False`` — fragment: the historical flat tree (the element
      children live directly under the document node).
    * ``None`` (default) — auto-detected from the tokens: a doctype or
      an ``<html>`` start tag means document mode.

    *script_hook* (J02 seam) is called with every ``<script>`` element
    as it is inserted.
    """
    root = root or Node.element("document")
    if document_mode is None:
        document_mode = _looks_like_document(tokens)
    if not document_mode:
        return _build_flat(root, tokens, script_hook=script_hook)
    return _DocumentBuilder(root, script_hook=script_hook).run(tokens)


def parse(data: "str | bytes", root: Node | None = None, script_hook=None,
          document_mode: "bool | None" = None) -> Node:
    """P24.5 — parse *data* (bytes or str) into a DOM tree."""
    return build(tokenize_rawtext(data), root=root, script_hook=script_hook,
                 document_mode=document_mode)


def parse_fragment(data: "str | bytes", context: "str | Node | None" = None,
                   script_hook=None) -> Node:
    """R7-H01 — fragment parsing (``innerHTML``-style).

    No implicit ``html`` / ``head`` / ``body`` is created.  When
    *context* is given (a tag name or a node, e.g. ``"tr"``), the
    fragment is composed inside a synthetic element of that tag and
    that element is returned — so ``parse_fragment("<td>x</td>",
    context="tr")`` yields ``<tr><td>x</td></tr>`` rather than a bare
    ``td`` the table machinery cannot place.
    """
    tokens = tokenize_rawtext(data) if isinstance(data, (str, bytes)) else list(data)
    ctx_tag = None
    if context is not None:
        ctx_tag = context if isinstance(context, str) else (context.tag or None)
    if ctx_tag:
        root = Node.element(ctx_tag.lower())
        return _build_flat(root, tokens, script_hook=script_hook)
    return build(tokens, script_hook=script_hook, document_mode=False)
