"""R7-H01 — HTML tokenizer / tree-builder contract.

Round-7 assessment (逐项改进实施方案.md §5, R7-H01) and the concrete
反例 in 实测评估报告.md §6:

* a ``>`` inside a quoted attribute value truncated the tag
  (``<a title="a>b">`` → ``a``);
* duplicate attributes kept the LAST instead of the FIRST;
* ``textarea`` / ``title`` (RCDATA) did not decode ``&amp;``;
* whitespace-only text between tags was dropped;
* documents that omit ``html`` / ``body`` / ``tbody`` got no implicit
  structure.

Every check below asserts the **tree structure**, not a screenshot:
the acceptance criterion is "树结构 fixture 与标准期望比较".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.document.html_pkg import (
    Node, build, parse_fragment, iter_tokens, tokenize_rawtext,
)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# helpers — structural dumping / lookup
# ---------------------------------------------------------------------------

def _dump(node, depth=0, out=None):
    """A compact, order-preserving structural dump used by the fixtures:
    ``[('div', {'id': 'a'}), ('#text', 'hi')]`` flattened with a depth
    prefix, so two trees can be compared as text."""
    out = out if out is not None else []
    indent = "  " * depth
    if node.is_element():
        attrs = ",".join(f"{k}={node.attrs[k]}" for k in sorted(node.attrs))
        out.append(f"{indent}<{node.tag}{' ' + attrs if attrs else ''}>")
    elif node.node_type == Node.TEXT:
        out.append(f"{indent}{node.text!r}")
    for c in node.children:
        _dump(c, depth + 1, out)
    return out


def _tree(html, **kw):
    return "\n".join(_dump(dom.parse(html, **kw)))


def _find(node, tag):
    if node.is_element() and node.tag == tag:
        return node
    for c in node.children:
        f = _find(c, tag)
        if f is not None:
            return f
    return None


def _find_all(node, tag, out=None):
    out = out if out is not None else []
    if node.is_element() and node.tag == tag:
        out.append(node)
    for c in node.children:
        _find_all(c, tag, out)
    return out


def _child_tags(node):
    return [c.tag for c in node.children if c.is_element()]


# ---------------------------------------------------------------------------
# 1. tokenizer — tag scanning
# ---------------------------------------------------------------------------

@_reg
def test_quoted_gt_in_attribute_value():
    """反例: 引号属性值 ``a>b`` 不再截断标签。"""
    root = dom.parse('<a title="a>b" href="/x">t</a>')
    a = _find(root, "a")
    assert a is not None, "the <a> element must survive"
    assert a.attrs["title"] == "a>b", f"got {a.attrs['title']!r}"
    assert a.attrs["href"] == "/x", f"got {a.attrs!r}"
    assert a.text_content() == "t"


@_reg
def test_quoted_attribute_keeps_spaces_and_angle_brackets():
    root = dom.parse("<div data-x='1 < 2 and 3 > 2'>y</div>")
    d = _find(root, "div")
    assert d.attrs["data-x"] == "1 < 2 and 3 > 2", f"got {d.attrs!r}"


@_reg
def test_duplicate_attribute_keeps_first():
    """WHATWG — the FIRST duplicate attribute wins."""
    root = dom.parse('<div id="first" id="second" class="a" class="b">x</div>')
    d = _find(root, "div")
    assert d.attrs["id"] == "first", f"got {d.attrs['id']!r}"
    assert d.attrs["class"] == "a", f"got {d.attrs['class']!r}"


@_reg
def test_attribute_value_entities_decoded():
    root = dom.parse('<a href="/x?a=1&amp;b=2" title="&lt;t&gt;">l</a>')
    a = _find(root, "a")
    assert a.attrs["href"] == "/x?a=1&b=2", f"got {a.attrs['href']!r}"
    assert a.attrs["title"] == "<t>", f"got {a.attrs['title']!r}"


@_reg
def test_unterminated_tag_is_text():
    """P24.2 — an unclosed ``<`` is literal text."""
    root = dom.parse("<div>abc<div")
    d = _find(root, "div")
    assert "abc" in d.text_content()
    assert "<div" in d.text_content(), f"got {d.text_content()!r}"


@_reg
def test_literal_lt_does_not_swallow_markup():
    """``a < b`` — the ``<`` is text and the following markup still parses."""
    root = dom.parse("<p>a < b</p><p>c</p>")
    ps = _find_all(root, "p")
    assert len(ps) == 2, f"both <p> must exist, got {len(ps)}"
    assert "a < b" in ps[0].text_content(), f"got {ps[0].text_content()!r}"
    assert ps[1].text_content() == "c"


@_reg
def test_comment_may_contain_gt():
    root = dom.parse("<div><!-- a > b --><span>x</span></div>")
    d = _find(root, "div")
    assert _find(d, "span") is not None, "the comment must not eat the <span>"
    # comments are dropped from the DOM (P24.3)
    assert "a > b" not in d.text_content(), f"got {d.text_content()!r}"


@_reg
def test_self_closing_slash_records_tag():
    root = dom.parse("<div><br/><img src='x.png'/><p>t</p></div>")
    assert _find(root, "br") is not None
    img = _find(root, "img")
    assert img is not None and img.attrs["src"] == "x.png", f"got {img and img.attrs}"
    assert _find(root, "p") is not None


# ---------------------------------------------------------------------------
# 2. tokenizer — RAWTEXT / RCDATA / PLAINTEXT
# ---------------------------------------------------------------------------

@_reg
def test_textarea_rcdata_decodes_entities():
    """反例: textarea 是 RCDATA，``&amp;`` 必须解码为 ``&``。"""
    root = dom.parse("<textarea>a &amp; b &#65;</textarea>")
    ta = _find(root, "textarea")
    assert ta.text_content() == "a & b A", f"got {ta.text_content()!r}"


@_reg
def test_title_rcdata_decodes_entities():
    root = dom.parse("<title>a &amp; b</title>")
    t = _find(root, "title")
    assert t.text_content() == "a & b", f"got {t.text_content()!r}"


@_reg
def test_script_rawtext_does_not_decode_entities():
    root = dom.parse("<script>var x = '&amp;';</script>")
    s = _find(root, "script")
    assert s.text_content() == "var x = '&amp;';", f"got {s.text_content()!r}"


@_reg
def test_markup_inside_textarea_stays_text():
    root = dom.parse("<textarea><b>x</b></textarea>")
    ta = _find(root, "textarea")
    assert ta.text_content() == "<b>x</b>", f"got {ta.text_content()!r}"
    assert _find(root, "b") is None, "RCDATA must not build elements"


@_reg
def test_end_tag_boundary_requires_name_boundary():
    """``</scriptx>`` must NOT close ``<script>``."""
    root = dom.parse("<script>var s='</scriptx>';</script><p>ok</p>")
    s = _find(root, "script")
    assert s.text_content() == "var s='</scriptx>';", f"got {s.text_content()!r}"
    p = _find(root, "p")
    assert p is not None and p.text_content() == "ok"


@_reg
def test_end_tag_with_attributes_still_closes():
    """``</p class=x>`` is still a close tag for ``p`` (attrs ignored)."""
    root = dom.parse("<div><p>a</p class=x><b>b</b></div>")
    ps = _find_all(root, "p")
    assert len(ps) == 1, f"exactly one <p>, got {len(ps)}"
    assert ps[0].text_content() == "a"


@_reg
def test_script_data_escaped_state():
    """WHATWG: ``<!--`` inside script data enters the escaped state, so a
    ``</script>`` inside it does not end the element until ``-->``."""
    root = dom.parse("<script>a<!--<b></script>c--></script><p>ok</p>")
    s = _find(root, "script")
    assert s is not None
    assert s.text_content() == "a<!--<b></script>c-->", f"got {s.text_content()!r}"
    # after the closing --> the real </script> ends the element
    p = _find(root, "p")
    assert p is not None and p.text_content() == "ok"


@_reg
def test_plaintext_consumes_rest_of_input():
    root = dom.parse("<p>a</p><plaintext><b>x</b>")
    assert _find(root, "plaintext") is not None
    assert _find(root, "b") is None, "PLAINTEXT must not build elements"
    pt = _find(root, "plaintext")
    assert pt.text_content() == "<b>x</b>", f"got {pt.text_content()!r}"


# ---------------------------------------------------------------------------
# 3. whitespace (反例: 纯空白文本丢失)
# ---------------------------------------------------------------------------

@_reg
def test_whitespace_between_inline_elements_survives():
    root = dom.parse("<div><b>a</b> <b>b</b></div>")
    d = _find(root, "div")
    kinds = [c.node_type for c in d.children]
    assert Node.TEXT in kinds, f"the space between the two <b> must exist, got {kinds}"
    ws = [c for c in d.children if c.node_type == Node.TEXT]
    assert ws[0].text == " ", f"got {ws[0].text!r}"
    assert d.text_content() == "a b", f"got {d.text_content()!r}"


@_reg
def test_whitespace_between_blocks_is_preserved_in_dom():
    root = dom.parse("<div><p>a</p> <p>b</p></div>")
    d = _find(root, "div")
    texts = [c.text for c in d.children if c.node_type == Node.TEXT]
    assert texts == [" "], f"got {texts!r}"
    assert _child_tags(d) == ["p", "p"]


@_reg
def test_newline_between_tags_preserved():
    root = dom.parse("<div>\n  <p>a</p>\n</div>")
    d = _find(root, "div")
    texts = [c.text for c in d.children if c.node_type == Node.TEXT]
    assert len(texts) == 2, f"leading + trailing whitespace text nodes, got {texts!r}"
    assert texts[0].strip() == "" and texts[1].strip() == ""


@_reg
def test_layout_ignores_collapsible_whitespace():
    """A whitespace-only text node between two blocks must not consume a
    line box (CSS2.1 §16.6.1) — the DOM keeps it, layout collapses it."""
    from toy_browser.style import css
    from toy_browser.layout import layout

    def body_top(html):
        root = dom.parse(html)
        styled = css.apply(root, "")
        lroot = layout.layout(styled, 400.0, 400.0)
        found = None

        def walk(bx):
            nonlocal found
            if bx.dom is not None and bx.dom.is_element() and bx.dom.tag == "p" \
                    and bx.dom.attr("id") == "target" and found is None:
                found = bx
            for c in bx.children:
                walk(c)
        walk(lroot)
        return found.rect.y if found is not None else None

    a = body_top("<html><body><p id=target>x</p></body></html>")
    b = body_top("<html><body>\n   <p id=target>x</p>\n</body></html>")
    assert a is not None and b is not None, "the <p> must be laid out"
    assert abs(a - b) < 1.0, f"whitespace must not shift the block: {a} vs {b}"


@_reg
def test_pre_whitespace_preserved_in_layout():
    from toy_browser.style import css
    from toy_browser.layout import layout
    root = dom.parse("<html><body><pre id=p>\n  x</pre></body></html>")
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 400.0)
    found = None

    def walk(bx):
        nonlocal found
        if bx.dom is not None and bx.dom.is_element() and bx.dom.tag == "pre" and found is None:
            found = bx
        for c in bx.children:
            walk(c)
    walk(lroot)
    assert found is not None
    assert found.text_content.startswith("\n"), f"pre must keep the newline: {found.text_content!r}"


# ---------------------------------------------------------------------------
# 4. implicit document structure (反例: 省略 html/body/tbody)
# ---------------------------------------------------------------------------

@_reg
def test_doctype_document_builds_html_head_body():
    root = dom.parse("<!DOCTYPE html><p>x</p>")
    assert _child_tags(root) == ["html"], f"got {_child_tags(root)}"
    html = root.children[0]
    assert _child_tags(html) == ["head", "body"], f"got {_child_tags(html)}"
    body = _find(root, "body")
    assert _child_tags(body) == ["p"]
    assert body.text_content() == "x"
    # the comment / doctype never become nodes
    assert _find(root, "head") is not None
    assert root.quirks_mode is False, "a full html doctype is standards mode"


@_reg
def test_html_without_body_gets_implicit_body():
    root = dom.parse("<html><head><title>t</title></head><p>x</p></html>")
    html = _find(root, "html")
    assert _child_tags(html) == ["head", "body"], f"got {_child_tags(html)}"
    title = _find(root, "title")
    assert title.text_content() == "t", f"got {title.text_content()!r}"
    body = _find(root, "body")
    assert body.text_content() == "x", f"got {body.text_content()!r}"


@_reg
def test_head_only_content_lands_in_head():
    root = dom.parse("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                     "<style>p{}</style><script>var a=1;</script></head>"
                     "<body><p>x</p></body></html>")
    head = _find(root, "head")
    assert _child_tags(head) == ["meta", "style", "script"], f"got {_child_tags(head)}"
    assert _find(head, "script").text_content() == "var a=1;"
    body = _find(root, "body")
    assert _child_tags(body) == ["p"]


@_reg
def test_content_after_head_without_body_goes_to_body():
    root = dom.parse("<html><head><base href='http://x/'></head>"
                     "<form id='f' action='/a'></form></html>")
    body = _find(root, "body")
    assert body is not None, "an implicit <body> must be created"
    assert _child_tags(body) == ["form"], f"got {_child_tags(body)}"


@_reg
def test_quirks_mode_without_doctype():
    root = dom.parse("<html><body>x</body></html>")
    assert root.quirks_mode is True, "no doctype → quirks mode"
    assert root.doctype_name is None


@_reg
def test_quirks_mode_with_legacy_doctype():
    root = dom.parse('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">'
                     "<html><body>x</body></html>")
    assert root.quirks_mode is True, f"non-html doctype → quirks, got {root.doctype_name!r}"


@_reg
def test_standards_mode_doctype_html():
    root = dom.parse("<!DOCTYPE html><html><body>x</body></html>")
    assert root.doctype_name == "html"
    assert root.quirks_mode is False


@_reg
def test_fragment_mode_keeps_flat_tree():
    """The historical ``dom.parse("<div>…")`` shape is a *fragment*: no
    implicit html/head/body (the contract the test-suite relies on)."""
    root = dom.parse("<div id='a' class='c'>hi<span>there</span></div>")
    assert _child_tags(root) == ["div"], f"got {_child_tags(root)}"
    assert root.children[0].attrs["id"] == "a"
    assert root.children[0].text_content() == "hithere"


@_reg
def test_parse_fragment_with_table_context():
    frag = parse_fragment("<td>x</td><td>y</td>", context="tr")
    assert frag.tag == "tr", f"got {frag.tag!r}"
    assert _child_tags(frag) == ["td", "td"], f"got {_child_tags(frag)}"


@_reg
def test_parse_fragment_no_document_wrapper():
    root = parse_fragment("<p>a</p><p>b</p>")
    assert _child_tags(root) == ["p", "p"], f"got {_child_tags(root)}"


# ---------------------------------------------------------------------------
# 5. table structure + foster parenting
# ---------------------------------------------------------------------------

@_reg
def test_implicit_tbody_for_bare_tr():
    root = dom.parse("<table><tr><td>x</td></tr></table>")
    table = _find(root, "table")
    assert _child_tags(table) == ["tbody"], f"got {_child_tags(table)}"
    tbody = _find(root, "tbody")
    assert _child_tags(tbody) == ["tr"]
    assert _child_tags(_find(root, "tr")) == ["td"]


@_reg
def test_implicit_tr_and_tbody_for_bare_td():
    root = dom.parse("<table><td>x</td></table>")
    table = _find(root, "table")
    assert _child_tags(table) == ["tbody"]
    assert _child_tags(_find(root, "tbody")) == ["tr"]
    assert _find(root, "td").text_content() == "x"


@_reg
def test_explicit_tbody_is_not_duplicated():
    root = dom.parse("<table><tbody><tr><td>x</td></tr></tbody></table>")
    assert len(_find_all(root, "tbody")) == 1, "no implicit tbody when one is given"
    root2 = dom.parse("<!DOCTYPE html><table><tbody><tr><td>x</td></tr>"
                      "<tr><td>y</td></tr></tbody></table>")
    assert len(_find_all(root2, "tbody")) == 1


@_reg
def test_table_rows_are_laid_out_through_tbody():
    """The implicit <tbody> must not collapse the table into one row."""
    from toy_browser.style import css
    from toy_browser.layout import layout
    html = ('<html><body><table id="t" style="display:table;width:200px">'
            '<tr><td id="a">A</td></tr><tr><td id="b">B</td></tr>'
            '</table></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    boxes = {}

    def walk(bx):
        if bx.dom is not None and bx.dom.is_element() and bx.dom.attr("id"):
            boxes.setdefault(bx.dom.attr("id"), bx)
        for c in bx.children:
            walk(c)
    walk(lroot)
    assert "a" in boxes and "b" in boxes, f"both cells must be laid out, got {list(boxes)}"
    assert boxes["b"].rect.y > boxes["a"].rect.y, \
        f"row 2 must sit below row 1 (a.y={boxes['a'].rect.y}, b.y={boxes['b'].rect.y})"


@_reg
def test_foster_parenting_moves_text_before_table():
    root = dom.parse("<!DOCTYPE html><html><body><div><table>mixed"
                     "<tr><td>c</td></tr></table>after</div></body></html>")
    div = _find(root, "div")
    table = _find(div, "table")
    kids = div.children
    idx_table = kids.index(table)
    # "mixed" must be fostered immediately BEFORE the table
    assert idx_table >= 1, "the fostered node must precede the table"
    assert kids[idx_table - 1].node_type == Node.TEXT, \
        f"expected the fostered text before the table, got {[k.tag for k in kids]}"
    assert kids[idx_table - 1].text == "mixed"
    assert table.text_content().strip() == "c", "the table keeps only its cells"


@_reg
def test_foster_parenting_moves_element_before_table():
    root = dom.parse("<!DOCTYPE html><html><body><table><div id=d>x</div>"
                     "<tr><td>c</td></tr></table></body></html>")
    body = _find(root, "body")
    d = _find(root, "div")
    table = _find(root, "table")
    assert d.parent is body, f"the fostered <div> must be a child of body, got {d.parent.tag}"
    assert body.children.index(d) < body.children.index(table), \
        "the fostered element must sit before the table"


@_reg
def test_nested_table_closes_inner_first():
    root = dom.parse("<!DOCTYPE html><body><table><tr><td>a"
                     "<table><tr><td>b</td></tr></table></td></tr></table></body></html>")
    tables = _find_all(root, "table")
    assert len(tables) == 2, f"got {len(tables)} tables"
    outer, inner = tables[0], tables[1]
    assert inner.parent is _find(outer, "td"), \
        f"the inner table must live in the outer cell, got {inner.parent.tag}"


@_reg
def test_caption_stays_first_child_of_table():
    root = dom.parse("<!DOCTYPE html><body><table><caption>cap</caption>"
                     "<tr><td>c</td></tr></table></body></html>")
    table = _find(root, "table")
    assert _child_tags(table)[0] == "caption", f"got {_child_tags(table)}"
    assert _find(root, "caption").text_content() == "cap"


@_reg
def test_table_end_tag_closes_row_group_stack():
    root = dom.parse("<!DOCTYPE html><body><table><tr><td>x</table><p>after</p></body></html>")
    p = _find(root, "p")
    body = _find(root, "body")
    assert p.parent is body, f"the <p> must leave the table, got parent {p.parent.tag}"


# ---------------------------------------------------------------------------
# 6. insertion-mode fault tolerance (kept from P24)
# ---------------------------------------------------------------------------

@_reg
def test_implicit_p_close_in_document():
    root = dom.parse("<!DOCTYPE html><body><p>one<div>two</div></body></html>")
    body = _find(root, "body")
    assert _child_tags(body) == ["p", "div"], f"got {_child_tags(body)}"


@_reg
def test_adoption_agency_in_document():
    root = dom.parse("<!DOCTYPE html><body><b><i>x</b></i></body></html>")
    b = _find(root, "b")
    assert b.tag == "b"
    assert b.children and b.children[0].tag == "i", f"got {[c.tag for c in b.children]}"
    assert b.text_content() == "x"


@_reg
def test_stray_close_tag_ignored():
    root = dom.parse("<!DOCTYPE html><body><div id=d>x</div></div></body></html>")
    body = _find(root, "body")
    assert len(_find_all(body, "div")) == 1, "exactly one <div>"


@_reg
def test_content_after_body_reopens_body():
    root = dom.parse("<!DOCTYPE html><body><p>x</p></body><p>y</p></html>")
    body = _find(root, "body")
    assert len(_find_all(body, "p")) == 2, "content after </body> belongs to the body"


@_reg
def test_case_insensitive_tags():
    # Tag + attribute NAMES are lowercased; attribute VALUES keep their case.
    root = dom.parse("<!DOCTYPE html><BODY><DIV ID=D>hi</DIV></BODY></html>")
    d = _find(root, "div")
    assert d is not None, "the <DIV> must become a 'div' node"
    assert d.attrs["id"] == "D", f"name lowercased, value preserved: {d.attrs!r}"
    assert d.text_content() == "hi"


# ---------------------------------------------------------------------------
# 7. J02 pause/resume seam + streaming tokenizer
# ---------------------------------------------------------------------------

@_reg
def test_script_hook_receives_script_elements():
    seen = []
    dom.parse("<!DOCTYPE html><body><script src='/a.js'></script>"
              "<script>var b=1;</script></body></html>",
              script_hook=lambda node: seen.append(node) or None)
    assert len(seen) == 2, f"both script elements must be offered, got {len(seen)}"
    assert seen[0].attrs.get("src") == "/a.js"


@_reg
def test_script_hook_can_inject_fetched_source():
    def hook(node):
        if node.attrs.get("src") == "/ext.js":
            return "window.__ext = 1;"
        return None
    root = dom.parse("<!DOCTYPE html><body><script src='/ext.js'></script></body></html>",
                     script_hook=hook)
    s = _find(root, "script")
    assert s.text_content() == "window.__ext = 1;", f"got {s.text_content()!r}"


@_reg
def test_iter_tokens_matches_tokenize_rawtext():
    data = "<!DOCTYPE html><html><body><p>a &amp; b</p></body></html>"
    assert list(iter_tokens(data)) == tokenize_rawtext(data)


@_reg
def test_build_accepts_explicit_document_mode():
    tokens = tokenize_rawtext("<p>x</p>")
    flat = build(tokens, document_mode=False)
    full = build(tokens, document_mode=True)
    assert _child_tags(flat) == ["p"], f"fragment: got {_child_tags(flat)}"
    assert _child_tags(full) == ["html"], f"document: got {_child_tags(full)}"
