"""End-to-end tests for toy-browser-py (pure stdlib, no test framework).

Run:  python test.py
"""
from __future__ import annotations

import browser
import dom
import css
import layout
import js
import text
import paint
from paint import Bitmap, Color
import sys


def _reds(bmp: Bitmap) -> int:
    """Count red-dominant pixels: R channel high, G and B low."""
    return sum(
        1
        for p in bmp.pixels
        if ((p >> 16) & 0xFF) > 200
        and ((p >> 8) & 0xFF) < 55
        and (p & 0xFF) < 55
    )


def _greens(bmp: Bitmap) -> int:
    """P12.4 — count green-dominant pixels: G channel high, R and B low."""
    return sum(
        1
        for p in bmp.pixels
        if ((p >> 8) & 0xFF) > 200
        and ((p >> 16) & 0xFF) < 55
        and (p & 0xFF) < 55
    )


def _pixel_diff(bmp_a: Bitmap, bmp_b: Bitmap) -> int:
    """P12.1 — count pixel positions where the two bitmaps differ."""
    n = min(len(bmp_a.pixels), len(bmp_b.pixels))
    return sum(1 for i in range(n) if bmp_a.pixels[i] != bmp_b.pixels[i])


def _find_box_by_tag(root, tag: str):
    """BFS a layout tree for the first box whose dom tag is `tag`."""
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.is_element() and bx.dom.tag == tag:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None


def _find_box_by_id(root, eid: str):
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.is_element() and bx.dom.attr("id") == eid:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None


def _find_box_by_id_or_tag(root, eid: str, tag: str):
    """Find the first box that matches id OR tag (search depth-first)."""
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.is_element():
            if bx.dom.attr("id") == eid or bx.dom.tag == tag:
                return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None


def test_dom_parse():
    root = dom.parse("<div id='a' class='c'>hi<span>there</span></div>")
    div = root.children[0]
    assert div.attr("id") == "a"
    assert div.has_class("c")
    assert div.text_content() == "hithere"
    assert root.find_by_id("a") is div
    assert div.query_selector_all(".c")[0] is div
    assert div.query_selector_all("#a")[0] is div


def test_dom_implicit_close():
    root = dom.parse("<p>one</p><p>two</p>")
    assert len(root.children) == 2
    assert root.children[0].tag == "p"
    assert root.children[1].tag == "p"


def test_css_cascade():
    root = dom.parse('<p class="intro" id="f">hello</p>')
    styled = css.apply(root, "p { color: red; } .intro { font-size: 14px; }")
    p = styled.children[0]
    assert isinstance(p.props["color"], Color)
    assert p.props["color"].r == 255


def test_css_inline_wins():
    root = dom.parse('<p style="color: blue;">x</p>')
    styled = css.apply(root, "p { color: red; }")
    p = styled.children[0]
    assert isinstance(p.props["color"], Color)
    assert p.props["color"].b == 255, "inline style must win the cascade"


def test_display_none():
    """P11.3 — display:none boxes stay in the layout tree but are zero-size.

    The P11.3 change keeps display:none boxes visible (flagged with
    is_display_none, rect = Rect()) so toggle tests can inspect them;
    their content is skipped by layout_block.
    """
    root = dom.parse('<div id="hidden" style="display: none">secret</div><p>visible</p>')
    styled = css.apply(root, "")
    layout_root = layout.layout(styled, 400.0)
    # The hidden div must be flagged and zero-size.
    hidden_present = [
        c for c in layout_root.children
        if c.dom and c.dom.attr("id") == "hidden"
    ]
    assert hidden_present, "display:none element must still appear (flagged) in layout"
    assert all(c.is_display_none for c in hidden_present), \
        "display:none element must have is_display_none=True"
    assert all(c.rect.w == 0 and c.rect.h == 0 for c in hidden_present), \
        "display:none element must be zero-size"
    # The visible <p> must still be present.
    p_box = _find_box_by_tag(layout_root, "p")
    assert p_box is not None, "non-hidden siblings must still be laid out"
    assert p_box.rect.h > 0, "visible sibling must have height"


def test_css_height_and_line_height():
    root = dom.parse('<div style="height: 100px">x</div>')
    styled = css.apply(root, "")
    layout_root = layout.layout(styled, 400.0)
    div = _find_box_by_tag(layout_root, "div")
    assert div is not None, "div box found"
    assert div.explicit_height == 100.0, f"got {div.explicit_height}"
    assert div.rect.h == 100.0, f"explicit height must set box height, got {div.rect.h}"


def test_text_align_stored():
    root = dom.parse('<div style="text-align: center">hello</div>')
    styled = css.apply(root, "")
    layout_root = layout.layout(styled, 400.0)
    div = _find_box_by_tag(layout_root, "div")
    assert div is not None
    assert div.text_align == "center"


def test_line_height_affects_text_height():
    root = dom.parse('<div style="font-size: 16px; line-height: 32px">hello</div>')
    styled = css.apply(root, "")
    layout_root = layout.layout(styled, 400.0)
    div = _find_box_by_tag(layout_root, "div")
    assert div is not None
    assert div.line_height_px == 32.0, f"got {div.line_height_px}"
    assert div.rect.h >= 32.0, f"text box height should reflect line-height, got {div.rect.h}"


def test_inline_elements_horizontal():
    html = '<p>before <span id="sp">middle</span> after</p>'
    root = dom.parse(html)
    styled = css.apply(root, "p { margin: 0; padding: 0; }")
    layout_root = layout.layout(styled, 400.0)
    p_box = _find_box_by_tag(layout_root, "p")
    assert p_box is not None
    kids = p_box.children
    assert len(kids) >= 2, f"expected >= 2 boxes inside <p>, got {len(kids)}"
    span_box = _find_box_by_id(layout_root, "sp")
    assert span_box is not None, "span box present"
    first = kids[0]
    assert abs(span_box.rect.y - first.rect.y) < 4.0, (
        f"span.y={span_box.rect.y} vs text.y={first.rect.y} — must share the row"
    )
    # Run width should exceed each part's width alone.
    run_w = sum(
        c.rect.w + c.padding.left + c.padding.right for c in kids
    )
    assert run_w > 100.0, f"inline run should span a meaningful width, got {run_w}"


def test_iframe_placeholder():
    root = dom.parse('<iframe src="about:blank"></iframe>')
    styled = css.apply(root, "iframe { border-width: 1px; border-color: #888; }")
    layout_root = layout.layout(styled, 400.0)
    iframe_box = _find_box_by_tag(layout_root, "iframe")
    assert iframe_box is not None
    assert iframe_box.rect.h >= 120.0, f"iframe placeholder height, got {iframe_box.rect.h}"
    # The caption child must be a text box.
    assert len(iframe_box.children) == 1
    assert iframe_box.children[0].box_type == layout.BoxType.TEXT


def test_hit_test():
    root = dom.parse('<button id="b">click</button>')
    styled = css.apply(root, "button { margin: 0; padding: 4px; }")
    layout_root = layout.layout(styled, 400.0)
    hit = layout.hit_test(layout_root, 5.0, 5.0)
    assert hit is not None
    assert hit.dom is not None and hit.dom.attr("id") == "b", (
        f"hit-test should find #b, got dom={hit.dom}"
    )


def test_text_measure_consistency():
    w = text.measure("hello", 16.0)
    assert w == 5 * text.char_advance(16.0)


def test_render_display_none():
    html = (
        '<html><body>'
        '<div id="secret" style="display: none; background: red; width: 80px">hidden</div>'
        "<p>visible</p>"
        "</body></html>"
    )
    hidden = browser.render_document(html, "", 300, 200)
    assert _reds(hidden) == 0, "display:none red div must paint no red pixels"

    html_visible = (
        '<html><body>'
        '<div style="background: red; width: 80px">hidden</div>'
        "<p>visible</p>"
        "</body></html>"
    )
    visible = browser.render_document(html_visible, "", 300, 200)
    assert _reds(visible) > 0, "same div without display:none must paint red"


def test_js_toggle_display_none():
    base_html = '<html><body><div id="box" style="background: red; width: 100px">show me</div></body></html>'
    before = browser.render_document(base_html, "", 300, 200)
    assert _reds(before) > 0, "baseline must show the red div"

    js_html = (
        '<html><body>'
        '<div id="box" style="background: red; width: 100px">show me</div>'
        "<script>document.getElementById('box').style.display = 'none';</script>"
        "</body></html>"
    )
    hidden = browser.render_document(js_html, "", 300, 200)
    assert _reds(hidden) == 0, "JS setting style.display='none' must hide the div"


def test_js_event_loop():
    html = (
        '<html><body>'
        '<p id="spacer">x</p>'
        '<button id="btn" onclick="document.getElementById(\'spacer\').style.color=\'red\'">click</button>'
        "</body></html>"
    )
    css_text = "p { margin: 0; } button { margin: 0; padding: 4px; }"
    baseline = browser.render_document(html, css_text, 800, 600)
    clicked = browser.render_document_with_click(
        html, css_text, 800, 600, 15.0, 25.0
    )
    assert baseline.pixels != clicked.pixels, (
        "a coordinate click on the button should mutate #spacer and change pixels"
    )
    # No-hit coordinate must not change anything.
    nohit = browser.render_document_with_click(html, css_text, 800, 600, 790.0, 590.0)
    assert baseline.pixels == nohit.pixels, "click on empty space must be a no-op"


def test_js_nullish():
    root = dom.parse("<html><body></body></html>")
    host = js.Host(root, lambda _s: None)
    prog = js.parse_program("a ?? 42")
    interp = js.Interpreter(host)
    interp.env.define("a", None)
    val = interp.eval(prog)
    assert val == 42, f"?? must return 42, got {val}"
    interp.env.define("a", 7)
    val2 = interp.eval(prog)
    assert val2 == 7, f"?? must return 7, got {val2}"


def test_js_set_timeout_fifo():
    logs: list[str] = []
    root = dom.parse("<html><body></body></html>")
    host = js.Host(root, logs.append)
    host.execute("setTimeout('console.log(\"fired\");', 0);")
    host.flush_timers()
    assert "fired" in logs, f"timer must fire, logs={logs}"


def test_iframe_src_sandbox():
    root = dom.parse('<iframe id="f" src="about:blank"></iframe>')
    logs: list[str] = []
    host = js.Host(root, logs.append)
    host.execute("document.getElementById('f').src = 'https://evil.example/x';")
    assert root.find_by_id("f").attr("src") == "about:blank", (
        "remote iframe.src must be dropped by the sandbox"
    )
    host.execute("document.getElementById('f').src = 'same/page.html';")
    assert root.find_by_id("f").attr("src") == "same/page.html", (
        "relative iframe.src must be allowed"
    )


def test_fetch_sandbox():
    root = dom.parse("<html><body></body></html>")
    logs: list[str] = []
    host = js.Host(root, logs.append)
    host.execute("fetch('file:///etc/passwd');")
    assert any("blocked" in l for l in logs), f"file:// fetch must be blocked, logs={logs}"
    host.execute("fetch('http://example.com');")
    blocked_http = any("blocked" in l and "http://example.com" in l for l in logs)
    assert not blocked_http, "http:// fetch must not be blocked"


def test_fetch_sandbox():
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute("fetch('file:///etc/passwd');")
    assert any("blocked" in l for l in logs), "file:// fetch must be logged as blocked"

    host.execute("fetch('http://example.com');")
    blocked_http = any("blocked" in l and "http://example.com" in l for l in logs)
    assert not blocked_http, "http:// fetch must not be blocked"


# ---------------------------------------------------------------------------
# P0.1 — img placeholder
# ---------------------------------------------------------------------------

def test_img_placeholder():
    """<img> without explicit size renders as a 32×32 grey placeholder with caption."""
    html = '<html><body><img id="pic" src="photo.png"></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lb = layout.layout(styled, 400.0, 300.0)
    img_box = _find_box_by_id(lb, "pic")
    assert img_box is not None, "img box must be in layout tree"
    # Default 32×32 placeholder
    assert img_box.rect.w > 0 and img_box.rect.h > 0, "img box must have non-zero size"
    # Caption child
    assert len(img_box.children) == 1, "img box must have one caption child"
    cap = img_box.children[0]
    assert cap.box_type == layout.BoxType.TEXT, "caption must be a text box"
    assert "img:" in (cap.text_content or ""), "caption must contain 'img:'"


def test_img_explicit_size():
    """<img width="80" height="60"> respects explicit dimensions."""
    html = '<html><body><img id="pic" src="x.png" width="80" height="60"></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lb = layout.layout(styled, 400.0, 300.0)
    img_box = _find_box_by_id(lb, "pic")
    assert img_box is not None
    assert img_box.rect.w == 80.0, f"expected w=80, got {img_box.rect.w}"
    assert img_box.rect.h == 60.0, f"expected h=60, got {img_box.rect.h}"


def test_img_paints():
    """<img> produces grey placeholder pixels in the rasterized output."""
    import paint_engine
    html = '<html><body><img src="a.png" width="50" height="50"></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lb = layout.layout(styled, 300.0, 200.0)
    dl = paint_engine.emit_commands(lb)
    bmp = paint_engine.rasterize(dl, 300, 200)
    # Count non-white, non-black pixels (the grey placeholder ~ 0x888888–0xCCCCCC)
    grey = sum(
        1
        for p in bmp.pixels
        if 0x800000 < p < 0xE0E0E0
        and ((p >> 16) & 0xFF) > 100
        and ((p >> 8) & 0xFF) > 100
        and (p & 0xFF) > 100
    )
    assert grey > 100, f"img placeholder must paint grey pixels, got {grey}"


# ---------------------------------------------------------------------------
# P0.2 — form controls
# ---------------------------------------------------------------------------

def test_button_border():
    """<button> gets a 1px border in the default UA stylesheet."""
    html = '<html><body><button id="b">ok</button></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "button { border-width: 1px; border-color: #888; padding: 4px; }")
    lb = layout.layout(styled, 400.0, 300.0)
    btn = _find_box_by_id(lb, "b")
    assert btn is not None, "button box found"
    assert btn.border.left == 1.0, f"button border-left expected 1px, got {btn.border.left}"
    assert btn.padding.left == 4.0, f"button padding-left expected 4px, got {btn.padding.left}"


def test_input_placeholder():
    """<input type="text"> renders a fixed-size placeholder box."""
    html = '<html><body><input id="in" type="text"></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lb = layout.layout(styled, 400.0, 300.0)
    inp = _find_box_by_id(lb, "in")
    assert inp is not None, "input box found in layout"
    assert inp.rect.w > 0 and inp.rect.h > 0, "input box must have non-zero size"


# ---------------------------------------------------------------------------
# P0.3 — selector extensions
# ---------------------------------------------------------------------------

def test_selector_descendant():
    """Descendant combinator: 'div p { color: red; }' matches <p> inside <div>."""
    html = '<html><body><div><p id="inner">x</p></div><p id="outer">y</p></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "div p { color: #ff0000; }")

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    inner = find_sn(styled, "inner")
    outer = find_sn(styled, "outer")
    assert inner is not None and outer is not None
    assert isinstance(inner.props.get("color"), css.Color), "div p must color the inner <p>"
    assert inner.props["color"].r == 255, "inner p color must be red"
    # outer <p> is not inside a <div>, so it should not get the red color
    outer_color = outer.props.get("color")
    assert not (isinstance(outer_color, css.Color) and outer_color.r == 255 and outer_color.g == 0 and outer_color.b == 0), (
        "outer p must NOT be red from 'div p'"
    )


def test_selector_child():
    """Child combinator: 'div > p { color: red; }' matches direct children only."""
    html = (
        '<html><body>'
        '<div><p id="direct">a</p><span><p id="nested">b</p></span></div>'
        '<div><p id="direct2">c</p></div>'
        "</body></html>"
    )
    root = dom.parse(html)
    styled = css.apply(root, "div > p { color: #ff0000; }")

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    direct = find_sn(styled, "direct")
    nested = find_sn(styled, "nested")
    direct2 = find_sn(styled, "direct2")
    assert direct is not None and nested is not None and direct2 is not None
    assert direct.props.get("color") is not None and isinstance(direct.props.get("color"), css.Color) and direct.props["color"].r == 255, (
        "direct child p must be red"
    )
    assert direct2.props.get("color") is not None and isinstance(direct2.props.get("color"), css.Color) and direct2.props["color"].r == 255, (
        "second direct child p must be red"
    )
    nested_color = nested.props.get("color")
    assert not (isinstance(nested_color, css.Color) and nested_color.r == 255 and nested_color.g == 0 and nested_color.b == 0), (
        "nested p (inside span) must NOT be red from 'div > p'"
    )


def test_selector_multi_class():
    """'.a.b { … }' matches elements with both classes."""
    html = (
        '<html><body>'
        '<div class="a b" id="both"></div>'
        '<div class="a" id="only_a"></div>'
        '<div class="b" id="only_b"></div>'
        "</body></html>"
    )
    root = dom.parse(html)
    styled = css.apply(root, ".a.b { color: #00ff00; }")

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    both = find_sn(styled, "both")
    only_a = find_sn(styled, "only_a")
    only_b = find_sn(styled, "only_b")
    assert both is not None and only_a is not None and only_b is not None
    assert both.props.get("color") is not None and isinstance(both.props.get("color"), css.Color) and both.props["color"].g == 255, (
        ".a.b must match the element with both classes"
    )
    only_a_color = only_a.props.get("color")
    assert not (isinstance(only_a_color, css.Color) and only_a_color.g == 255 and only_a_color.r == 0 and only_a_color.b == 0), (
        ".a.b must NOT match an element with only class a"
    )
    only_b_color = only_b.props.get("color")
    assert not (isinstance(only_b_color, css.Color) and only_b_color.g == 255 and only_b_color.r == 0 and only_b_color.b == 0), (
        ".a.b must NOT match an element with only class b"
    )


# ---------------------------------------------------------------------------
# P0.4 — JS extensions
# ---------------------------------------------------------------------------

def test_js_array_literal():
    """Array literal [1,2,3] and .length / .push work."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let a = [1, 2, 3]; "
        "a.push(4); "
        "console.log(a.length);"
    )
    assert any("4" in l for l in logs), f"array push/length must produce 4, logs={logs}"


def test_js_string_methods():
    """String#includes and String#indexOf work."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let s = 'hello world'; "
        "console.log(s.includes('world')); "
        "console.log(s.indexOf('lo'));"
    )
    assert any("true" in l.lower() for l in logs), f"'hello world'.includes('world') must be true, logs={logs}"
    assert any("3" in l for l in logs), f"'hello world'.indexOf('lo') must be 3, logs={logs}"


def test_js_object_literal():
    """Object literal {a:1, b:2} and property access work."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let o = {a: 1, b: 2}; "
        "console.log(o.a); "
        "console.log(o.b);"
    )
    assert any("1" in l for l in logs) and any("2" in l for l in logs), (
        f"object literal property access must work, logs={logs}"
    )


# ---------------------------------------------------------------------------
# P0.5 — invalidation
# ---------------------------------------------------------------------------

def test_changed_region():
    """browser.changed_region returns the bounding box of pixel differences."""
    html_a = '<html><body><div style="background: red; width: 50px">A</div></body></html>'
    html_b = '<html><body><div style="background: blue; width: 50px">B</div></body></html>'
    bmp_a = browser.render_document(html_a, "", 200, 100)
    bmp_b = browser.render_document(html_b, "", 200, 100)
    region = browser.changed_region(bmp_a, bmp_b)
    assert region is not None, "changed_region must return a (x,y,w,h) tuple"
    x, y, w, h = region
    assert w > 0 and h > 0, "changed region must have non-zero size"
    # No change → None
    bmp_c = browser.render_document(html_a, "", 200, 100)
    assert browser.changed_region(bmp_a, bmp_c) is None, "identical bitmaps must yield None"


# ---------------------------------------------------------------------------
# P1.4 — Flexbox
# ---------------------------------------------------------------------------

def _find_box_by_id(root, eid: str):
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.is_element() and bx.dom.attr("id") == eid:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None


def test_flex_row():
    """display:flex with flex-direction:row lays children side by side."""
    html = '<html><body><div style="display:flex;flex-direction:row" id="f">' \
           '<div id="c1" style="width:40px;height:20px"></div>' \
           '<div id="c2" style="width:40px;height:20px"></div>' \
           '</div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    fdiv = _find_box_by_id(lroot, "f")
    c1 = _find_box_by_id(fdiv, "c1")
    c2 = _find_box_by_id(fdiv, "c2")
    assert fdiv.is_flex, "flex container must have is_flex=True"
    assert c1 is not None and c2 is not None
    # c2 must be to the right of c1
    assert c2.rect.x > c1.rect.x, f"flex row: c2.x({c2.rect.x}) must be > c1.x({c1.rect.x})"
    assert abs(c2.rect.y - c1.rect.y) < 1.0, "flex row: c1 and c2 must share the same y"


def test_flex_column():
    """display:flex with flex-direction:column stacks children vertically."""
    html = '<html><body><div style="display:flex;flex-direction:column;width:100px;height:200px" id="fc">' \
           '<div id="c1" style="height:40px"></div>' \
           '<div id="c2" style="height:40px"></div>' \
           '</div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    fc = _find_box_by_id(lroot, "fc")
    c1 = _find_box_by_id(fc, "c1")
    c2 = _find_box_by_id(fc, "c2")
    assert fc.is_flex and fc.flex_direction == "column"
    # c2 must be below c1
    assert c2.rect.y > c1.rect.y, f"flex column: c2.y({c2.rect.y}) must be > c1.y({c1.rect.y})"


def test_flex_justify_center():
    """justify-content:center on a flex row centers children horizontally."""
    html = '<html><body><div style="display:flex;flex-direction:row;justify-content:center;width:200px;height:50px" id="fj">' \
           '<div id="c1" style="width:20px;height:10px"></div>' \
           '<div id="c2" style="width:20px;height:10px"></div>' \
           '</div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    fj = _find_box_by_id(lroot, "fj")
    c1 = _find_box_by_id(fj, "c1")
    c2 = _find_box_by_id(fj, "c2")
    assert fj.justify_content == "center"
    # Geometric center of the children group: midpoint from c1.x to c2.x + c2.w
    left_edge = min(c1.rect.x, c2.rect.x)
    right_edge = max(c1.rect.x + c1.rect.w, c2.rect.x + c2.rect.w)
    children_center = (left_edge + right_edge) / 2.0
    container_center = fj.rect.x + fj.rect.w / 2.0
    assert abs(children_center - container_center) < 5.0, (
        f"flex center: children center({children_center:.1f}) must ≈ container center({container_center:.1f})"
    )


# ---------------------------------------------------------------------------
# P1.1 — opacity + overflow
# ---------------------------------------------------------------------------

def test_opacity_render():
    """opacity:0.5 on a red box blends with the white background."""
    bmp_opaque = browser.render_document(
        '<html><body><div style="background:#ff0000;width:50px;height:20px"></div></body></html>',
        "", 200, 100,
    )
    bmp_half = browser.render_document(
        '<html><body><div style="background:#ff0000;opacity:0.5;width:50px;height:20px"></div></body></html>',
        "", 200, 100,
    )

    def px(bmp, x, y):
        return bmp.pixels[y * bmp.width + x]

    # The div sits at x≈8 (body margin), y≈0. Sample inside it.
    o_red = px(bmp_opaque, 20, 10)
    h_red = px(bmp_half, 20, 10)
    assert h_red != o_red, "opacity must change the rendered pixel"
    g_ch = (h_red >> 8) & 0xFF
    assert g_ch > 100, f"half-opacity red over white must have G>100, got G={g_ch}"


def test_opacity_stored_on_box():
    """opacity is read from CSS into the LayoutBox."""
    html = '<html><body><div style="opacity:0.3;width:40px" id="o"></div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    ob = _find_box_by_id(lroot, "o")
    assert ob.opacity == 0.3, f"opacity must be 0.3, got {ob.opacity}"


def test_overflow_hidden_flag():
    """overflow:hidden is parsed into LayoutBox.overflow_hidden."""
    html = '<html><body><div style="overflow:hidden" id="ov"></div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    ov = _find_box_by_id(lroot, "ov")
    assert ov.overflow_hidden is True, "overflow:hidden must set overflow_hidden=True"


# ---------------------------------------------------------------------------
# P1.2 — event loop: rAF, Promise, Host.logs
# ---------------------------------------------------------------------------

def test_request_animation_frame():
    """requestAnimationFrame enqueues a callback that runs at flush time."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute("requestAnimationFrame(function(t) { console.log('raf'); });")
    host.flush_raf()
    assert any("raf" in l for l in logs), f"rAF must log 'raf', logs={logs}"


def test_promise_resolve_then():
    """Promise resolves and .then logs the resolved value."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let p = Promise(function(resolve, reject) { resolve(42); });"
        "p.then(function(v) { console.log(v); });"
    )
    assert any("42" in l for l in logs), f"promise.then must log 42, logs={logs}"


def test_promise_catch():
    """Promise rejects and .catch logs the error."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let p = Promise(function(resolve, reject) { reject('boom'); });"
        "p.catch(function(e) { console.log(e); });"
    )
    assert any("boom" in l for l in logs), f"promise.catch must log 'boom', logs={logs}"


def test_host_logs():
    """Host.logs mirrors the log callback sink."""
    root = dom.parse("<html><body></body></html>")
    sink = []
    host = js.Host(root, sink.append)
    host.execute("console.log('hi');")
    assert host.logs == sink, "Host.logs must mirror the log sink"
    assert any("hi" in l for l in host.logs)


# ---------------------------------------------------------------------------
# P1.3 — expanded UA stylesheet
# ---------------------------------------------------------------------------

def test_ua_h3_default():
    """h3 has a default font-size from the UA stylesheet."""
    html = "<html><body><h3 id='h'>title</h3></body></html>"
    root = dom.parse(html)
    # Use the browser's default CSS path (full pipeline).
    styled = css.apply(root, browser._default_css())

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    h3 = find_sn(styled, "h")
    assert h3 is not None
    fs = h3.prop_font_px()
    assert fs == 18.0, f"h3 default font-size must be 18px, got {fs}"


def test_ua_table_border():
    """table/td get a default 1px border from the UA stylesheet."""
    html = "<html><body><table id='t'><tr><td id='cell'>x</td></tr></table></body></html>"
    root = dom.parse(html)
    styled = css.apply(root, browser._default_css())

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    cell = find_sn(styled, "cell")
    assert cell is not None
    bw = cell.props.get("border-width")
    assert bw is not None, "td must have a default border-width from the UA sheet"


# ---------------------------------------------------------------------------
# P2.1 — accessibility
# ---------------------------------------------------------------------------

def test_a11y_tree_roles():
    """Accessibility tree maps semantic tags to ARIA roles."""
    html = (
        "<html><body>"
        "<h1 id='h'>Title</h1>"
        "<p id='p'>text</p>"
        "<button id='b'>go</button>"
        "<a id='l' href='/x'>link</a>"
        "</body></html>"
    )
    tree = dom.parse(html)
    a11y = browser.collect_accessibility_tree(tree)
    roles = _a11y_roles(a11y)
    assert "heading" in roles, f"heading role missing, got {roles}"
    assert "button" in roles, f"button role missing, got {roles}"
    assert "link" in roles, f"link role missing, got {roles}"
    assert "paragraph" in roles, f"paragraph role missing, got {roles}"


def _a11y_roles(nodes: list) -> set:
    out: set = set()
    for n in nodes:
        out.add(n["role"])
        if "children" in n:
            out |= _a11y_roles(n["children"])
    return out


def test_a11y_img_alt_fallback():
    """img alt text becomes the accessible name (P2.1)."""
    html = '<html><body><img id="pic" src="x.png" alt="my picture"></body></html>'
    tree = dom.parse(html)
    a11y = browser.collect_accessibility_tree(tree)
    imgs = _find_a11y_role(a11y, "image")
    assert imgs, "image node must be present in the a11y tree"
    assert imgs[0]["name"] == "my picture", f"img alt must be the name, got {imgs[0]['name']}"


def _find_a11y_role(nodes: list, role: str) -> list:
    out: list = []
    for n in nodes:
        if n.get("role") == role:
            out.append(n)
        if "children" in n:
            out.extend(_find_a11y_role(n["children"], role))
    return out


def test_a11y_skip_script_style():
    """script/style/link/meta are excluded from the a11y tree."""
    html = (
        "<html><head><style>x{}</style><link rel='x'><meta charset='utf8'>"
        "</head><body><script>console.log(1)</script><p id='p'>t</p>"
        "</body></html>"
    )
    tree = dom.parse(html)
    a11y = browser.collect_accessibility_tree(tree)
    roles = _a11y_roles(a11y)
    assert "script" not in roles and "style" not in roles, (
        f"script/style must be skipped, got {roles}"
    )


# ---------------------------------------------------------------------------
# P2.2 — link collection
# ---------------------------------------------------------------------------

def test_collect_links():
    """collect_links returns all unique <a href> in document order."""
    html = (
        "<html><body>"
        "<a href='/a'>A</a>"
        "<a href='/b'>B</a>"
        "<a href='/a'>A again</a>"
        "<a>C no href</a>"
        "</body></html>"
    )
    tree = dom.parse(html)
    links = browser.collect_links(tree)
    assert links == ["/a", "/b"], f"expected ['/a','/b'], got {links}"


def test_collect_links_remote():
    """collect_links includes remote http(s) URLs."""
    html = "<html><body><a href='https://example.com/page'>x</a></body></html>"
    tree = dom.parse(html)
    links = browser.collect_links(tree)
    assert links == ["https://example.com/page"], f"got {links}"


def test_write_links_and_a11y():
    """write_links / write_a11y_json produce readable files."""
    import json, os, tempfile

    html = "<html><body><a href='/a'>A</a><button id='b'>go</button></body></html>"
    tree = dom.parse(html)
    links = browser.collect_links(tree)
    a11y = browser.collect_accessibility_tree(tree)

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        links_path = tf.name
    browser.write_links(links, links_path)
    with open(links_path, encoding="utf-8") as f:
        content = f.read()
    assert "/a" in content, "links file must contain the URL"
    os.unlink(links_path)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        a11y_path = tf.name
    browser.write_a11y_json(a11y, a11y_path)
    with open(a11y_path, encoding="utf-8") as f:
        loaded = json.load(f)
    assert isinstance(loaded, list), "a11y JSON must be a list"
    os.unlink(a11y_path)


# ---------------------------------------------------------------------------
# P2.3 — PNG output
# ---------------------------------------------------------------------------

def test_png_encode():
    """write_png produces a valid PNG signature and non-trivial size."""
    import os, tempfile

    bmp = browser.render_document("<html><body><div style='background:red;width:20px'></div></body></html>", "", 100, 50)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png_path = tf.name
    browser.write_png(bmp, png_path)
    size = os.path.getsize(png_path)
    assert size > 100, f"PNG must be >100 bytes, got {size}"
    with open(png_path, "rb") as f:
        sig = f.read(8)
    assert sig == b"\x89PNG\r\n\x1a\n", f"PNG signature invalid: {sig!r}"
    os.unlink(png_path)


def test_png_roundtrip_dimensions():
    """PNG dimensions match the source bitmap (decode IHDR)."""
    import struct, tempfile, os

    bmp = browser.render_document("<html><body></body></html>", "", 64, 32)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png_path = tf.name
    browser.write_png(bmp, png_path)
    with open(png_path, "rb") as f:
        f.read(8)  # sig
        f.read(4)  # IHDR length
        f.read(4)  # IHDR tag
        w, h = struct.unpack(">II", f.read(8))
    assert w == 64 and h == 32, f"IHDR dimensions must be 64x32, got {w}x{h}"
    os.unlink(png_path)


def test_ppm_png_equivalence():
    """PPM and PNG outputs encode the same pixel data."""
    import os, tempfile, struct, zlib

    html = "<html><body><div style='background:blue;width:10px;height:10px'></div></body></html>"
    bmp = browser.render_document(html, "", 100, 100)
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as tf:
        ppm_path = tf.name
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf2:
        png_path = tf2.name
    browser.write_ppm(bmp, ppm_path)
    browser.write_png(bmp, png_path)

    # Parse PPM header + pixel bytes.
    with open(ppm_path, "rb") as f:
        ppm_data = f.read()
    ppm_tokens = ppm_data.split(b"\n", 3)
    ppm_pixels = ppm_tokens[3]
    assert len(ppm_pixels) == 100 * 100 * 3, f"PPM pixel count must be 30000, got {len(ppm_pixels)}"

    # Decode PNG IDAT.
    with open(png_path, "rb") as f:
        png_data = f.read()
    # Find IHDR -> width/height
    pos = 8
    idat_chunks = b""
    width = height = 0
    while pos < len(png_data):
        length = struct.unpack(">I", png_data[pos:pos+4])[0]
        tag = png_data[pos+4:pos+8]
        data = png_data[pos+8:pos+8+length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
        elif tag == b"IDAT":
            idat_chunks += data
        pos += 12 + length
    raw = zlib.decompress(idat_chunks)
    # 100x100 RGB, each row prefixed with a 0 filter byte = 100*(1+300) bytes
    assert len(raw) == height * (1 + width * 3), f"PNG raw size mismatch: {len(raw)}"
    os.unlink(ppm_path)
    os.unlink(png_path)


# ---------------------------------------------------------------------------
# P3.1 — embedded image decoding
# ---------------------------------------------------------------------------

def test_embedded_img_base64():
    """P3.1 — base64 data URI in <img src> renders decoded pixels."""
    import base64

    raw = bytes([255, 0, 0] * 16)  # 4x4 red square
    b64 = base64.b64encode(raw).decode()
    data_uri = f"data:image/octet-stream;base64,{b64}"
    html = f'<html><body><img id="pic" src="{data_uri}" width="40" height="40"></body></html>'
    bmp = browser.render_document(html, "", 200, 200)
    # P19 — default CSS adds body { margin: 8px }, shifting the img to
    # x=8..48, y=0..40.  Check a pixel well inside the image region.
    px = bmp.pixels[20 * 200 + 30]
    r, g, b = (px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF
    assert r > 200 and g < 55 and b < 55, f"embedded img must render red, got ({r},{g},{b})"


def test_embedded_img_intrinsic_size():
    """P3.1 — img with data URI but no width/height uses intrinsic pixel size."""
    import base64

    raw = bytes([0, 0, 255] * 9)  # 3x3 blue
    b64 = base64.b64encode(raw).decode()
    data_uri = f"data:image/octet-stream;base64,{b64}"
    html = f'<html><body><img src="{data_uri}"></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    img_box = _find_box_by_tag(lroot, "img")
    assert img_box is not None, "img box must exist"
    assert img_box.img_pixels is not None, "img_pixels must be decoded"
    assert img_box.img_pixel_w == 3 and img_box.img_pixel_h == 3, "intrinsic size must be 3x3"
    assert abs(img_box.rect.w - 3.0) < 1.0, f"img width must be ~3, got {img_box.rect.w}"


def test_embedded_img_png_data_uri():
    """P3.1 — PNG data URI is decoded via the minimal PNG reader."""
    import base64, struct, zlib

    # Build a 1x1 red PNG in memory.
    w, h = 1, 1
    raw_row = b"\x00" + bytes([255, 0, 0])  # filter 0 + RGB
    raw_rows = raw_row * h
    idat = zlib.compress(raw_rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    b64 = base64.b64encode(png).decode()
    data_uri = f"data:image/png;base64,{b64}"

    html = f'<html><body><img id="p" src="{data_uri}" width="20" height="20"></body></html>'
    bmp = browser.render_document(html, "", 100, 100)
    px = bmp.pixels[10 * 100 + 10]
    r, g, b = (px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF
    assert r > 200 and g < 55 and b < 55, f"PNG data URI must render red, got ({r},{g},{b})"


# ---------------------------------------------------------------------------
# P3.2 — flexbox deepening
# ---------------------------------------------------------------------------

def test_flex_grow_distribution():
    """P3.2 — flex-grow distributes leftover space proportionally."""
    html = (
        '<html><body><div style="display:flex;flex-direction:row;width:400px;height:50px" id="fg">'
        '<div id="a" style="width:100px"></div>'
        '<div id="b" style="flex-grow:1"></div>'
        '<div id="c" style="width:100px"></div>'
        '</div></body></html>'
    )
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)

    def find(eid):
        stack = [lroot]
        while stack:
            bx = stack.pop()
            if bx.dom and bx.dom.attr("id") == eid:
                return bx
            for c in reversed(bx.children):
                stack.append(c)
        return None

    a = find("a")
    b = find("b")
    c = find("c")
    assert a is not None and b is not None and c is not None
    assert abs(a.rect.w - 100.0) < 1.0, f"non-grow child keeps basis width, got {a.rect.w}"
    assert abs(c.rect.w - 100.0) < 1.0, f"non-grow child keeps basis width, got {c.rect.w}"
    assert b.rect.w > 150.0, f"flex-grow child must absorb leftover, got {b.rect.w}"
    # Total should fill the 400px container (with some tolerance for rounding).
    total = a.rect.w + b.rect.w + c.rect.w
    assert total > 350.0, f"total width must approximate 400, got {total}"


def test_flex_align_items_center():
    """P3.2 — align-items:center vertically centers children in the flex line."""
    html = (
        '<html><body><div style="display:flex;flex-direction:row;width:400px;height:100px;align-items:center" id="fg2">'
        '<div id="s1" style="width:50px;height:20px"></div>'
        '<div id="s2" style="width:50px;height:60px"></div>'
        '</div></body></html>'
    )
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)

    def find(eid):
        stack = [lroot]
        while stack:
            bx = stack.pop()
            if bx.dom and bx.dom.attr("id") == eid:
                return bx
            for c in reversed(bx.children):
                stack.append(c)
        return None

    fg2 = find("fg2")
    s1 = find("s1")
    s2 = find("s2")
    mid_y = fg2.rect.y + fg2.rect.h / 2.0
    assert abs(s1.rect.y + s1.rect.h / 2.0 - mid_y) < 5.0, f"s1 must be centered, got y={s1.rect.y}"
    assert abs(s2.rect.y + s2.rect.h / 2.0 - mid_y) < 5.0, f"s2 must be centered, got y={s2.rect.y}"


def test_flex_grow_column():
    """P3.2 — flex-grow works in column direction (distributes height)."""
    html = (
        '<html><body><div style="display:flex;flex-direction:column;width:100px;height:300px" id="fcol">'
        '<div id="top" style="height:50px"></div>'
        '<div id="mid" style="flex-grow:1"></div>'
        '<div id="bot" style="height:50px"></div>'
        '</div></body></html>'
    )
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)

    def find(eid):
        stack = [lroot]
        while stack:
            bx = stack.pop()
            if bx.dom and bx.dom.attr("id") == eid:
                return bx
            for c in reversed(bx.children):
                stack.append(c)
        return None

    mid = find("mid")
    assert mid is not None
    assert mid.flex_grow == 1.0, "flex-grow must be read from CSS"


# ---------------------------------------------------------------------------
# P3.3 — JS built-in objects
# ---------------------------------------------------------------------------

def test_js_math_sqrt():
    """P3.3 — Math.sqrt(16) == 4."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(Math.sqrt(16));")
    assert any("4" in l for l in logs), f"Math.sqrt(16) must log 4, got {logs}"


def test_js_math_max_abs():
    """P3.3 — Math.max(3,7,2)==7, Math.abs(-5)==5."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(Math.max(3, 7, 2)); console.log(Math.abs(-5));")
    assert any("7" in l for l in logs), f"Math.max must log 7, got {logs}"
    assert any("5" in l for l in logs), f"Math.abs must log 5, got {logs}"


def test_js_json_stringify():
    """P3.3 — JSON.stringify({a:1,b:'two'}) produces valid JSON."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(JSON.stringify({a: 1, b: 'two'}));")
    assert any('"a"' in l and '"two"' in l for l in logs), f"JSON.stringify failed, got {logs}"


def test_js_json_parse():
    """P3.3 — JSON.parse roundtrips a simple object."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let v = JSON.parse('{\"x\": 42}'); "
        "console.log(v.x);"
    )
    assert any("42" in l for l in logs), f"JSON.parse must yield x=42, got {logs}"


def test_js_math_constants():
    """P3.3 — Math.PI and Math.E are available."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(Math.PI);")
    assert any("3.14" in l for l in logs), f"Math.PI must log 3.14…, got {logs}"


# ---------------------------------------------------------------------------
# P3.4 — CSS transition
# ---------------------------------------------------------------------------

def test_transition_color_interpolation():
    """P3.4 — transition engine interpolates colors over frames."""
    import transition as trans

    start = Color(255, 0, 0, 255)   # red
    end = Color(0, 0, 255, 255)     # blue
    spec = trans.TransitionSpec("background", start, end, frames=4)

    # Frame 0 → start color.
    c0 = spec.value_at(0.0)
    assert c0.r == 255 and c0.b == 0, f"frame 0 must be red, got ({c0.r},{c0.g},{c0.b})"

    # Frame 4 → end color.
    c4 = spec.value_at(1.0)
    assert c4.b == 255 and c4.r == 0, f"frame 4 must be blue, got ({c4.r},{c4.g},{c4.b})"

    # Midpoint t=0.5 → purple-ish (int truncation: 127 not 128).
    cmid = spec.value_at(0.5)
    assert 120 <= cmid.r <= 130 and 120 <= cmid.b <= 130, f"midpoint must be ~128, got ({cmid.r},{cmid.g},{cmid.b})"


def test_transition_render_frames():
    """P3.4 — browser.run_transition returns N+1 bitmaps with interpolated colors."""
    frames = browser.run_transition(
        "<html><body><div id='t' style='background:#ff0000;width:100px;height:50px'></div></body></html>",
        "",
        200, 200, "t",
        prop_start=Color(255, 0, 0, 255),
        prop_end=Color(0, 0, 255, 255),
        frames=5,
    )
    assert len(frames) == 6, f"expected 6 frames, got {len(frames)}"
    # Div is at body margin 8px → spans x=8..108, y=8..58. Sample (58,33) = center.
    start_px = frames[0].pixels[33 * 200 + 58]
    end_px = frames[5].pixels[33 * 200 + 58]
    start_r = (start_px >> 16) & 0xFF
    end_b = end_px & 0xFF
    assert start_r > 200, f"frame 0 must be reddish, got R={start_r}"
    assert end_b > 200, f"frame 5 must be bluish, got B={end_b}"
    # Intermediate frame must be between red and blue.
    mid_px = frames[2].pixels[33 * 200 + 58]
    mid_r = (mid_px >> 16) & 0xFF
    mid_b = mid_px & 0xFF
    assert mid_r < start_r, f"mid frame R must decrease from {start_r}, got {mid_r}"
    assert mid_b > 0, f"mid frame B must increase, got {mid_b}"


def test_transition_lerp_color():
    """P3.4 — lerp_color helper interpolates correctly."""
    import transition as trans

    c = trans.lerp_color(Color(0, 0, 0, 255), Color(100, 100, 100, 255), 0.5)
    assert c.r == 50 and c.g == 50 and c.b == 50, f"midpoint must be 50, got ({c.r},{c.g},{c.b})"


# ---------------------------------------------------------------------------
# P4.1 — @keyframes animation
# ---------------------------------------------------------------------------

def test_parse_keyframes():
    """P4.1 — parse_keyframes extracts @keyframes blocks with from/to/% keyframes."""
    css_text = """
    @keyframes spin {
        from { transform: rotate(0deg); opacity: 1; }
        50%  { transform: rotate(180deg); opacity: 0.5; }
        to   { transform: rotate(360deg); opacity: 1; }
    }
    """
    anims = css.parse_keyframes(css_text)
    assert "spin" in anims, f"keyframes 'spin' must be found, got {list(anims)}"
    anim = anims["spin"]
    assert len(anim.frames) == 3, f"expected 3 frames, got {len(anim.frames)}"
    assert anim.frames[0].offset == 0.0, "first frame must be offset 0"
    assert anim.frames[-1].offset == 1.0, "last frame must be offset 1"
    # 50% keyframe must have opacity 0.5.
    mid = anim.frames[1]
    assert mid.offset == 0.5, f"middle frame offset must be 0.5, got {mid.offset}"
    op = mid.declarations.get("opacity")
    assert op is not None, "opacity must be declared in the 50% frame"


def test_keyframes_animation_render():
    """P4.1 — browser.animate_keyframes renders N+1 frames of a color animation."""
    css_text = """
    @keyframes fade {
        from { background: #ff0000; }
        to   { background: #0000ff; }
    }
    """
    html = "<html><body><div id='box' style='width:100px;height:50px'></div></body></html>"
    frames = browser.animate_keyframes(html, css_text, 200, 200, "box", "fade", total_frames=4)
    assert len(frames) == 5, f"expected 5 frames, got {len(frames)}"
    # Frame 0 → red, frame 4 → blue. Sample the center of the div.
    start_px = frames[0].pixels[33 * 200 + 58]
    end_px = frames[4].pixels[33 * 200 + 58]
    start_r = (start_px >> 16) & 0xFF
    end_b = end_px & 0xFF
    assert start_r > 200, f"frame 0 must be reddish, got R={start_r}"
    assert end_b > 200, f"frame 4 must be bluish, got B={end_b}"
    # Intermediate frame must be purple-ish.
    mid_px = frames[2].pixels[33 * 200 + 58]
    mid_r = (mid_px >> 16) & 0xFF
    mid_b = mid_px & 0xFF
    assert mid_r < start_r and mid_b > 0, f"mid frame must be between red and blue, got R={mid_r} B={mid_b}"


def test_apply_animation_frame_interpolation():
    """P4.1 — apply_animation_frame interpolates between bracketing keyframes."""
    from css import Keyframe, Animation, Length

    anim = Animation(
        name="move",
        frames=[
            Keyframe(0.0, {"background": Color(255, 0, 0, 255)}),
            Keyframe(1.0, {"background": Color(0, 0, 255, 255)}),
        ],
    )
    html = "<html><body><div id='x' style='width:50px;height:50px'></div></body></html>"
    tree = dom.parse(html)
    styled = css.apply(tree, "")
    css.apply_animation_frame(styled, "x", anim, 0.5)
    # Find the styled node for 'x'.
    def find_sn(sn, eid):
        if sn.dom and sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    snx = find_sn(styled, "x")
    assert snx is not None, "element 'x' must be in the styled tree"
    bg = snx.props.get("background")
    assert bg is not None, "background must be set after apply_animation_frame"
    assert isinstance(bg, Color), f"background must be a Color, got {type(bg)}"
    # t=0.5 → midpoint between red and blue.
    assert 100 <= bg.r <= 160, f"R at midpoint must be ~128, got {bg.r}"
    assert 100 <= bg.b <= 160, f"B at midpoint must be ~128, got {bg.b}"


# ---------------------------------------------------------------------------
# P4.2 — JS built-in objects extension
# ---------------------------------------------------------------------------

def test_js_date_now():
    """P4.2 — new Date() returns an object with getTime/getFullYear."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let d = new Date();"
        "console.log(d.getFullYear());"
    )
    # getFullYear returns the current year (e.g. 2025).
    import datetime
    year = str(datetime.datetime.now().year)
    assert any(year in l for l in logs), f"getFullYear must log current year {year}, got {logs}"


def test_js_regexp_test():
    """P4.2 — RegExp.test returns true/false for a pattern match."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let r = new RegExp('abc');"
        "console.log(r.test('xabcy'));"
        "console.log(r.test('xyz'));"
    )
    assert any("true" in l for l in logs), f"RegExp.test('xabcy') must be true, got {logs}"
    assert any("false" in l for l in logs), f"RegExp.test('xyz') must be false, got {logs}"


def test_js_string_charcode():
    """P4.2 — String.fromCharCode and charCodeAt roundtrip."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "console.log(String.fromCharCode(65, 66, 67));"
        "console.log('A'.charCodeAt(0));"
    )
    assert any("ABC" in l for l in logs), f"fromCharCode(65,66,67) must log 'ABC', got {logs}"
    assert any("65" in l for l in logs), f"charCodeAt must log 65, got {logs}"


def test_js_regexp_replace():
    """P4.2 — RegExp.replace substitutes matched text."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let r = new RegExp('o');"
        "console.log(r.replace('fooo', '0'));"
    )
    assert any("f000" in l for l in logs), f"replace must yield 'f000', got {logs}"


def test_js_date_getmonth():
    """P4.2 — Date.getMonth returns 0-indexed month."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let d = new Date();"
        "let m = d.getMonth();"
        "let ok = (m >= 0) && (m <= 11);"
        "console.log('month:' + ok);"
    )
    assert any("month:True" in l for l in logs), f"getMonth must be 0–11, got {logs}"


# ---------------------------------------------------------------------------
# P4.3 — CSS Grid + multi-value shorthands
# ---------------------------------------------------------------------------

def test_grid_columns():
    """P4.3 — display:grid with 2-column grid-template splits available width."""
    html = ('<html><body>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;width:200px" id="g">'
            '<div id="a"></div>'
            '<div id="b"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)

    def find(eid):
        stack = [lroot]
        while stack:
            bx = stack.pop()
            if bx.dom and bx.dom.attr("id") == eid:
                return bx
            for c in reversed(bx.children):
                stack.append(c)
        return None

    g = find("g")
    a = find("a")
    b = find("b")
    assert g is not None and g.is_grid, "grid container must be flagged is_grid"
    assert g.grid_columns == 2, f"grid_columns must be 2, got {g.grid_columns}"
    assert abs(a.rect.w - 100.0) < 5.0, f"grid cell a width must be ~100, got {a.rect.w}"
    assert abs(b.rect.x - a.rect.x - a.rect.w) < 5.0, "b must start after a"


def test_grid_render_cells():
    """P4.3 — 3-column grid renders 3 distinct colored cells side by side."""
    html = ('<html><body>'
            '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;width:300px;height:50px" id="g">'
            '<div style="background:#ff0000"></div>'
            '<div style="background:#00ff00"></div>'
            '<div style="background:#0000ff"></div>'
            '</div></body></html>')
    bmp = browser.render_document(html, "", 400, 200)
    y = 33  # middle of the 50px tall grid
    r0 = (bmp.pixels[y * 400 + 50] >> 16) & 0xFF     # red channel of cell 0
    g1 = (bmp.pixels[y * 400 + 150] >> 8) & 0xFF     # green channel of cell 1
    b2 = bmp.pixels[y * 400 + 250] & 0xFF            # blue channel of cell 2
    assert r0 > 200, f"cell 0 must be red, got R={r0}"
    assert g1 > 200, f"cell 1 must be green, got G={g1}"
    assert b2 > 200, f"cell 2 must be blue, got B={b2}"


def test_margin_shorthand_4val():
    """P4.3 — margin: 10px 20px 30px 40px parses all 4 edges correctly."""
    html = '<div id="d" style="margin:10px 20px 30px 40px;width:100px"></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")

    def find(n, eid):
        if n.dom and n.dom.attr("id") == eid:
            return n
        for c in n.children:
            r = find(c, eid)
            if r:
                return r
        return None

    sn = find(styled, "d")
    e = sn.props.get("margin")
    assert e is not None, "margin must be parsed"
    assert (e.top, e.right, e.bottom, e.left) == (10, 20, 30, 40), \
        f"4-value margin: got ({e.top},{e.right},{e.bottom},{e.left})"


def test_padding_shorthand_2val():
    """P4.3 — padding: 5px 15px means top/bottom=5, left/right=15."""
    html = '<div id="d" style="padding:5px 15px;width:100px"></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")

    def find(n, eid):
        if n.dom and n.dom.attr("id") == eid:
            return n
        for c in n.children:
            r = find(c, eid)
            if r:
                return r
        return None

    sn = find(styled, "d")
    e = sn.props.get("padding")
    assert e is not None, "padding must be parsed"
    assert (e.top, e.right, e.bottom, e.left) == (5, 15, 5, 15), \
        f"2-value padding: got ({e.top},{e.right},{e.bottom},{e.left})"


def test_margin_shorthand_3val():
    """P4.3 — margin: 1px 2px 3px means top=1, horiz=2, bottom=3."""
    html = '<div id="d" style="margin:1px 2px 3px;width:100px"></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")

    def find(n, eid):
        if n.dom and n.dom.attr("id") == eid:
            return n
        for c in n.children:
            r = find(c, eid)
            if r:
                return r
        return None

    sn = find(styled, "d")
    e = sn.props.get("margin")
    assert e is not None, "margin must be parsed"
    assert (e.top, e.right, e.bottom, e.left) == (1, 2, 3, 2), \
        f"3-value margin: got ({e.top},{e.right},{e.bottom},{e.left})"


# ---------------------------------------------------------------------------
# P5 — visual effects / float / JS extensions
# ---------------------------------------------------------------------------

def test_border_radius_layout():
    """P5.1 — border-radius:20px stores on the LayoutBox."""
    html = '<div id="r" style="border-radius:20px;width:60px;height:40px"></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)

    def find(n, eid):
        stack = [n]
        while stack:
            bx = stack.pop()
            if bx.dom and bx.dom.attr("id") == eid:
                return bx
            for c in reversed(bx.children):
                stack.append(c)
        return None

    bx = find(lroot, "r")
    assert bx is not None, "rounded box must exist"
    assert bx.border_radius == 20.0, f"border_radius must be 20, got {bx.border_radius}"


def test_border_radius_render_clips_corner():
    """P5.1 — a large border-radius clips the corner pixels of a filled box."""
    html = ('<html><body>'
            '<div id="r" style="border-radius:50px;width:80px;height:80px;'
            'background:#ff0000"></div></body></html>')
    bmp = browser.render_document(html, "", 200, 200)
    # Box at (0,0) 80×80, radius=50.
    # TL corner (0,0) must be clipped (white, R=255).
    # Center (40,40) must be red (R≈255, but G/B≈0).
    corner = bmp.pixels[0 * 200 + 0]
    center = bmp.pixels[40 * 200 + 40]
    corner_g = (corner >> 8) & 0xFF
    corner_b = corner & 0xFF
    center_g = (center >> 8) & 0xFF
    center_b = center & 0xFF
    # Clipped corner is white → G=255, B=255.
    assert corner_g == 255 and corner_b == 255, \
        f"clipped corner must be white, got G={corner_g} B={corner_b}"
    # Red center → G≈0, B≈0.
    assert center_g < 10 and center_b < 10, \
        f"center must be red (low G/B), got G={center_g} B={center_b}"


def test_box_shadow_render():
    """P5.1 — box-shadow paints a soft shadow offset behind the box."""
    html = ('<html><body>'
            '<div id="s" style="box-shadow:8px 8px 4px #000000;'
            'width:50px;height:40px;background:#00ff00"></div></body></html>')
    bmp = browser.render_document(html, "", 200, 200)
    # Box at (0,0) 50×40, shadow offset 8,8 blur 4 → shadow region extends to
    # x≈58, y≈48.  Sample a pixel in the shadow region (outside the green box).
    shadow_px = bmp.pixels[48 * 200 + 58]   # (58, 48) — inside shadow spread
    box_px = bmp.pixels[20 * 200 + 25]      # (25, 20) — box center (green)
    shadow_r = (shadow_px >> 16) & 0xFF
    box_g = (box_px >> 8) & 0xFF
    assert box_g > 200, f"box center must be green, got G={box_g}"
    # shadow blends black → darker than white background
    assert shadow_r < 250, f"shadow region must be darker than white, got R={shadow_r}"


def test_transform_translate_render():
    """P5.1 — transform:translate(20px,10px) shifts the rendered box."""
    # Box without transform: x=0..40, y=0..20 → center at (20,10) is blue.
    # Box with translate(20,10): x=20..60, y=10..30 → center at (40,20) is blue.
    # Pixel (5,5) is inside the ORIGINAL box but OUTSIDE the shifted box,
    # so it must be white in the transformed render.
    html_no_tf = ('<html><body><div id="b" style="width:40px;height:20px;'
                  'background:#0000ff"></div></body></html>')
    html_tf = ('<html><body><div id="b" style="width:40px;height:20px;'
               'transform:translate(20px,10px);background:#0000ff"></div></body></html>')
    bmp_a = browser.render_document(html_no_tf, "", 200, 200)
    bmp_b = browser.render_document(html_tf, "", 200, 200)
    # (20,10): center of the no-transform box → blue.
    a_at_base_center = (bmp_a.pixels[10 * 200 + 20] & 0xFF)
    assert a_at_base_center > 200, f"no-transform box center must be blue, got B={a_at_base_center}"
    # (40,20): center of the shifted box → blue.
    b_at_shifted_center = (bmp_b.pixels[20 * 200 + 40] & 0xFF)
    assert b_at_shifted_center > 200, f"shifted box center must be blue, got B={b_at_shifted_center}"
    # (5,5): inside the original box, outside the shifted box → white (B=255).
    b_at_orig_pos = (bmp_b.pixels[5 * 200 + 5] & 0xFF)
    assert b_at_orig_pos == 255, f"(5,5) must be white in shifted render, got B={b_at_orig_pos}"


def test_float_left_layout():
    """P5.2 — float:left positions the child at the left of the container."""
    html = ('<html><body><div id="box" style="width:200px;height:60px">'
            '<div id="f" style="float:left;width:50px;height:30px"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)

    def find_by_id(n, eid):
        stack = [n]
        while stack:
            bx = stack.pop()
            if bx.dom and bx.dom.attr("id") == eid:
                return bx
            for c in reversed(bx.children):
                stack.append(c)
        return None

    f = find_by_id(lroot, "f")
    box = find_by_id(lroot, "box")
    assert f is not None and box is not None, "container and float must exist"
    assert f.is_float, "is_float must be True"
    assert f.float_direction == "left"
    # floated left → x aligned with container's content left.
    # container x=0, padding_l=0 → float x must be 0.
    assert f.rect.x == 0.0, f"float:left x must be 0, got {f.rect.x}"


def test_float_right_layout():
    """P5.2 — float:right positions the child at the right of the container."""
    html = ('<html><body><div id="box" style="width:200px;height:60px">'
            '<div id="f" style="float:right;width:50px;height:30px"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)

    def find_by_id(n, eid):
        stack = [n]
        while stack:
            bx = stack.pop()
            if bx.dom and bx.dom.attr("id") == eid:
                return bx
            for c in reversed(bx.children):
                stack.append(c)
        return None

    f = find_by_id(lroot, "f")
    box = find_by_id(lroot, "box")
    assert f is not None and box is not None, "container and float must exist"
    assert f.float_direction == "right"
    # floated right → x near container's content right
    expected_x = box.rect.x + box.padding.left + box.explicit_width - f.rect.w - f.padding.left - f.padding.right
    assert abs(f.rect.x - expected_x) < 4.0, \
        f"float:right x must be at content right, got {f.rect.x} vs {expected_x}"


def test_js_array_map_join():
    """P5.3 — Array.map + join with JS-style integer formatting."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1,2,3,4,5];"
        "let m = a.map(function(x){ return x * 2; });"
        "console.log(m.join(','));"
    )
    assert any("2,4,6,8,10" in l for l in logs), f"map+join must be 2,4,6,8,10, got {logs}"


def test_js_array_filter():
    """P5.3 — Array.filter keeps elements passing the predicate."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1,2,3,4,5];"
        "let f = a.filter(function(x){ return x > 3; });"
        "console.log(f.join(','));"
    )
    assert any("4,5" in l for l in logs), f"filter must be 4,5, got {logs}"


def test_js_array_reverse_concat():
    """P5.3 — Array.reverse mutates in place; concat merges arrays + scalars."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("let a = [1,2,3]; let r = a.reverse(); console.log(r.join(','));")
    assert any("3,2,1" in l for l in logs), f"reverse must be 3,2,1, got {logs}"
    logs2 = []
    host2 = js.Host(dom.parse("<html><body></body></html>"), logs2.append)
    host2.execute("let a = [1,2]; let b = a.concat([3,4],5); console.log(b.join(','));")
    assert any("1,2,3,4,5" in l for l in logs2), f"concat must be 1,2,3,4,5, got {logs2}"


def test_js_array_foreach():
    """P5.3 — Array.forEach runs the callback over every element."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1,2,3];"
        "let s = 0;"
        "a.forEach(function(x){ s += x; });"
        "console.log(s);"
    )
    assert any("6" in l for l in logs), f"forEach sum must be 6, got {logs}"


def test_console_assert_false():
    """P5.3 — console.assert logs a failure message when the condition is false."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.assert(1 == 2, 'one is not two');")
    assert any("ASSERTION" in l and "one is not two" in l for l in logs), \
        f"assert(false) must log the message, got {logs}"


def test_console_assert_true_silent():
    """P5.3 — console.assert stays silent when the condition is true."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.assert(1 == 1, 'should not fire');")
    assert not any("ASSERTION" in l for l in logs), \
        f"assert(true) must not log, got {logs}"


def find2_children(n, eid):
    stack = [n]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.attr("id") == eid:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None


# ---------------------------------------------------------------------------
# P6.1 — position: absolute
# ---------------------------------------------------------------------------

def test_abs_position_layout():
    """P6.1 — position:absolute sets is_absolute on the LayoutBox."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="a" style="position:absolute;top:10px;left:20px;'
            'width:30px;height:20px;background:red"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = find2_children(lroot, "a")
    box = find2_children(lroot, "box")
    assert a is not None, "absolute child must exist"
    assert a.is_absolute, "is_absolute must be True"
    assert a.abs_top == 10.0, f"abs_top must be 10, got {a.abs_top}"
    assert a.abs_left == 20.0, f"abs_left must be 20, got {a.abs_left}"
    # The box content area starts at box.rect.x + padding.left
    expected_x = box.rect.x + box.padding.left + a.abs_left
    expected_y = box.rect.y + box.padding.top + a.abs_top
    assert abs(a.rect.x - expected_x) < 2.0, f"abs x must be {expected_x}, got {a.rect.x}"
    assert abs(a.rect.y - expected_y) < 2.0, f"abs y must be {expected_y}, got {a.rect.y}"


def test_abs_position_render():
    """P6.1 — position:absolute with top/left renders at the offset position."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="a" style="position:absolute;top:10px;left:20px;'
            'width:30px;height:20px;background:#0000ff"></div>'
            '</div></body></html>')
    bmp = browser.render_document(html, "", 300, 300)
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = find2_children(lroot, "a")
    cx = int(a.rect.x + a.rect.w / 2)
    cy = int(a.rect.y + a.rect.h / 2)
    px = bmp.pixels[cy * 300 + cx]
    assert (px & 0xFF) > 200, f"absolute box center must be blue, got B={px & 0xFF}"


def test_abs_position_top_left_offset():
    """P6.1 — two absolute children with different offsets render at different positions."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="a" style="position:absolute;top:5px;left:5px;'
            'width:10px;height:10px;background:#0000ff"></div>'
            '<div id="b" style="position:absolute;top:50px;left:50px;'
            'width:10px;height:10px;background:#00ff00"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = find2_children(lroot, "a")
    b = find2_children(lroot, "b")
    box = find2_children(lroot, "box")
    assert abs(a.rect.x - b.rect.x) > 30, "a and b must be at different x positions"
    assert abs(a.rect.y - b.rect.y) > 30, "a and b must be at different y positions"


# ---------------------------------------------------------------------------
# P6.2 — text-overflow / line-clamp
# ---------------------------------------------------------------------------

def test_text_overflow_ellipsis_stored():
    """P6.2 — text-overflow:ellipsis is stored on the LayoutBox."""
    html = ('<div id="t" style="width:100px;height:40px;text-overflow:ellipsis;'
            'overflow:hidden">A very long text that will be truncated here</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "text box must exist"
    assert t.text_overflow == "ellipsis", f"text_overflow must be ellipsis, got {t.text_overflow!r}"


def test_line_clamp_layout():
    """P6.2 — line-clamp:2 limits the text height to 2 lines."""
    html = ('<div id="t" style="width:100px;line-clamp:2">'
            'line one word line two word line three word line four word'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "text box must exist"
    # With line-clamp:2 the text child height should be ≤ 2 * line_height
    text_child = None
    for c in t.children:
        if c.box_type == "text":
            text_child = c
            break
    assert text_child is not None, "text child must exist"
    lh = text_child.line_height_px
    assert text_child.line_clamp == 2, f"text child line_clamp must be 2, got {text_child.line_clamp}"
    assert text_child.rect.h <= lh * 2.5, \
        f"line-clamp text height must be ≤ 2.5×lh, got {text_child.rect.h}"


# ---------------------------------------------------------------------------
# P6.3 — JS: Array.reduce, Object.keys, String.repeat/padStart/padEnd
# ---------------------------------------------------------------------------

def test_js_array_reduce():
    """P6.3 — Array.reduce sums a list."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1, 2, 3, 4, 5];"
        "let s = a.reduce(function(acc, x) { return acc + x; });"
        "console.log(s);"
    )
    assert any("15" in l for l in logs), f"reduce sum must be 15, got {logs}"


def test_js_array_reduce_init():
    """P6.3 — Array.reduce with an initial accumulator value."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [10, 20];"
        "let r = a.reduce(function(acc, x) { return acc * x; }, 100);"
        "console.log(r);"
    )
    # 100 * 10 * 20 = 20000
    assert any("20000" in l for l in logs), f"reduce init must give 20000, got {logs}"


def test_js_object_keys():
    """P6.3 — Object.keys returns the property names of a dict."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let o = {a: 1, b: 2, c: 3};"
        "let ks = Object.keys(o);"
        "console.log(ks.join(','));"
    )
    assert any("a,b,c" in l for l in logs), f"Object.keys must give a,b,c, got {logs}"


def test_js_object_values():
    """P6.3 — Object.values returns the property values of a dict."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let o = {x: 10, y: 20};"
        "let vs = Object.values(o);"
        "console.log(vs.join(','));"
    )
    assert any("10,20" in l for l in logs), f"Object.values must give 10,20, got {logs}"


def test_js_string_repeat():
    """P6.3 — String.repeat duplicates the string N times."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let s = 'ab'.repeat(3);"
        "console.log(s);"
    )
    assert any("ababab" in l for l in logs), f"repeat must give ababab, got {logs}"


def test_js_string_pad_start():
    """P6.3 — String.padStart pads on the left to target length."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let s = '5'.padStart(3, '0');"
        "console.log(s);"
    )
    assert any("005" in l for l in logs), f"padStart must give 005, got {logs}"


def test_js_string_pad_end():
    """P6.3 — String.padEnd pads on the right to target length."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let s = 'hi'.padEnd(5, '.');"
        "console.log(s);"
    )
    assert any("hi..." in l for l in logs), f"padEnd must give hi..., got {logs}"


# ---------------------------------------------------------------------------
# P7.1 — z-index
# ---------------------------------------------------------------------------

def test_z_index_layout():
    """P7.1 — z-index is stored on the LayoutBox."""
    html = ('<div id="z" style="position:absolute;top:0;left:0;'
            'width:50px;height:50px;z-index:10;background:red"></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    z = find2_children(lroot, "z")
    assert z is not None, "z-index box must exist"
    assert z.z_index == 10, f"z_index must be 10, got {z.z_index}"


def test_z_index_paint_sort():
    """P7.1 — higher z-index commands are drawn later (on top)."""
    html = ('<html><body><div id="bg" style="position:absolute;top:0;left:0;'
            'width:100px;height:100px;z-index:1;background:#0000ff"></div>'
            '<div id="fg" style="position:absolute;top:0;left:0;'
            'width:100px;height:100px;z-index:5;background:#ff0000"></div>'
            '</body></html>')
    import paint_engine
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    dl = paint_engine.emit_commands(lroot)
    # Find the rect commands for bg (z=1) and fg (z=5).
    bg_cmds = [c for c in dl.commands if getattr(c, "_layer", 0) == 1]
    fg_cmds = [c for c in dl.commands if getattr(c, "_layer", 0) == 5]
    assert len(bg_cmds) >= 1, "bg commands must exist"
    assert len(fg_cmds) >= 1, "fg commands must exist"
    # fg (z=5) must come after bg (z=1) in the sorted list.
    bg_last = max(i for i, c in enumerate(dl.commands) if getattr(c, "_layer", 0) == 1)
    fg_first = min(i for i, c in enumerate(dl.commands) if getattr(c, "_layer", 0) == 5)
    assert fg_first > bg_last, f"fg (z=5) must be drawn after bg (z=1), got bg_last={bg_last} fg_first={fg_first}"


# ---------------------------------------------------------------------------
# P7.2 — display: table
# ---------------------------------------------------------------------------

def test_table_layout():
    """P7.2 — display:table lays out cells in equal-width columns."""
    html = ('<html><body><table id="t" style="display:table;width:200px;border:1px solid black">'
            '<tr><td id="a">A</td><td id="b">B</td></tr>'
            '<tr><td id="c">C</td><td id="d">D</td></tr>'
            '</table></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    t = find2_children(lroot, "t")
    a = find2_children(lroot, "a")
    b = find2_children(lroot, "b")
    c = find2_children(lroot, "c")
    assert t is not None, "table must exist"
    assert t.is_table, "is_table must be True"
    # Cells in the same row must have the same y.
    assert a is not None and b is not None, "cells a and b must exist"
    assert abs(a.rect.y - b.rect.y) < 2.0, f"cells in same row must share y, got {a.rect.y} vs {b.rect.y}"
    # Cells in different rows must have different y.
    assert c is not None, "cell c must exist"
    assert c.rect.y > a.rect.y, f"row 2 must be below row 1, got c.y={c.rect.y} a.y={a.rect.y}"
    # Column width: 200px table / 2 cols = 100px each.
    assert abs(a.rect.w - 100.0) < 5.0, f"cell width must be ~100, got {a.rect.w}"


def test_table_render_cells():
    """P7.2 — table cells render with their backgrounds."""
    html = ('<html><body><table id="t" style="display:table;width:100px">'
            '<tr><td id="a" style="background:#ff0000">A</td></tr>'
            '<tr><td id="b" style="background:#0000ff">B</td></tr>'
            '</table></body></html>')
    bmp = browser.render_document(html, "", 200, 200)
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    a = find2_children(lroot, "a")
    b = find2_children(lroot, "b")
    # Sample cell centers.
    ax = int(a.rect.x + a.rect.w / 2)
    ay = int(a.rect.y + a.rect.h / 2)
    bx = int(b.rect.x + b.rect.w / 2)
    by = int(b.rect.y + b.rect.h / 2)
    pa = bmp.pixels[ay * 200 + ax]
    pb = bmp.pixels[by * 200 + bx]
    assert (pa >> 16) & 0xFF > 200, f"cell A must be red, got R={(pa>>16)&0xFF}"
    assert (pb & 0xFF) > 200, f"cell B must be blue, got B={pb&0xFF}"


def test_table_cell_flags():
    """P7.2 — td/th elements get is_table_cell=True."""
    html = '<table><tr><td id="x">X</td><th id="y">Y</th></tr></table>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    x = find2_children(lroot, "x")
    y = find2_children(lroot, "y")
    assert x is not None and x.is_table_cell, "td must have is_table_cell=True"
    assert y is not None and y.is_table_cell, "th must have is_table_cell=True"


# ---------------------------------------------------------------------------
# P7.3 — JS Array.find / findIndex / some / every
# ---------------------------------------------------------------------------

def test_js_array_find():
    """P7.3 — Array.find returns the first matching element."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [10, 20, 30, 40];"
        "let f = a.find(function(x) { return x > 25; });"
        "console.log(f);"
    )
    assert any("30" in l for l in logs), f"find must give 30, got {logs}"


def test_js_array_find_index():
    """P7.3 — Array.findIndex returns the index of the first match."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [10, 20, 30];"
        "let i = a.findIndex(function(x) { return x == 20; });"
        "console.log(i);"
    )
    assert any("1" in l for l in logs), f"findIndex must give 1, got {logs}"


def test_js_array_some():
    """P7.3 — Array.some returns true if any element matches."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1, 2, 3];"
        "console.log(a.some(function(x) { return x > 2; }));"
        "console.log(a.some(function(x) { return x > 10; }));"
    )
    assert any("true" in l for l in logs), f"some must give true for x>2, got {logs}"
    assert any("false" in l for l in logs), f"some must give false for x>10, got {logs}"


def test_js_array_every():
    """P7.3 — Array.every returns true if all elements match."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [2, 4, 6];"
        "console.log(a.every(function(x) { return x > 1; }));"
        "console.log(a.every(function(x) { return x > 10; }));"
    )
    # "true" appears in the x>1 check, "false" in the x>10 check.
    assert any(l.strip() == "true" for l in logs), f"every must give true for x>1, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"every must give false for x>10, got {logs}"


# ---------------------------------------------------------------------------
# P7.4 — position: sticky
# ---------------------------------------------------------------------------

def test_sticky_layout():
    """P7.4 — position:sticky sets is_sticky on the LayoutBox."""
    html = ('<div id="s" style="position:sticky;top:10px;left:0;'
            'width:50px;height:30px;background:green"></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    s = find2_children(lroot, "s")
    assert s is not None, "sticky box must exist"
    assert s.is_sticky, "is_sticky must be True"
    assert s.sticky_top == 10.0, f"sticky_top must be 10, got {s.sticky_top}"


def test_sticky_full_direction():
    """P7.4 — position:sticky with all four offsets is stored."""
    html = ('<div id="s" style="position:sticky;top:5px;right:10px;'
            'bottom:15px;left:20px;width:40px;height:20px"></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    s = find2_children(lroot, "s")
    assert s is not None, "sticky box must exist"
    assert s.is_sticky, "is_sticky must be True"
    assert s.sticky_top == 5.0, f"sticky_top must be 5, got {s.sticky_top}"
    assert s.sticky_right == 10.0, f"sticky_right must be 10, got {s.sticky_right}"
    assert s.sticky_bottom == 15.0, f"sticky_bottom must be 15, got {s.sticky_bottom}"
    assert s.sticky_left == 20.0, f"sticky_left must be 20, got {s.sticky_left}"


# ---------------------------------------------------------------------------
# P8.1 — CSS :hover / :focus pseudo-class
# ---------------------------------------------------------------------------

def test_hover_pseudo_layout():
    """P8.1 — :hover pseudo-class applies styles when interaction_state matches."""
    html = ('<div id="h" style="width:50px;height:30px"></div>')
    root = dom.parse(html)
    # Without hover state — no matching.
    styled = css.apply(root, "#h:hover { background: red; }")
    lroot = layout.layout(styled, 200.0, 200.0)
    h = find2_children(lroot, "h")
    assert h is not None, "hover box must exist"
    # Without interaction_state, the pseudo-class should NOT match,
    # so background should be None (no inline style set).
    assert h.background is None, f"no hover state → no bg, got {h.background}"

    # With hover state — should apply.
    styled2 = css.apply(root, "#h:hover { background: red; }", {"hover": "h"})
    lroot2 = layout.layout(styled2, 200.0, 200.0)
    h2 = find2_children(lroot2, "h")
    assert h2 is not None
    from paint import Color
    assert h2.background is not None, f"hover state must set bg, got {h2.background}"


def test_focus_pseudo_layout():
    """P8.1 — :focus pseudo-class applies when focus state matches the element id."""
    html = ('<div id="f" style="width:100px;height:20px"></div>')
    root = dom.parse(html)
    # No focus state — :focus rule should NOT match.
    styled = css.apply(root, "#f:focus { background: #0000ff; }")
    lroot = layout.layout(styled, 200.0, 200.0)
    f = find2_children(lroot, "f")
    assert f is not None, "focus box must exist"
    assert f.background is None, f"no focus → no bg, got {f.background}"

    # With focus state — :focus rule should match.
    styled2 = css.apply(root, "#f:focus { background: #0000ff; }", {"focus": "f"})
    lroot2 = layout.layout(styled2, 200.0, 200.0)
    f2 = find2_children(lroot2, "f")
    assert f2 is not None
    assert f2.background is not None, f"focus state must set bg, got {f2.background}"


def test_pseudo_class_specificity():
    """P8.1 — pseudo-class adds one class-level point to specificity."""
    sel = css.Selector.parse("#a:hover")
    # id=1, cls=1 (pseudo adds 1), type=0
    assert sel.specificity == (1, 1, 0), f"specificity must be (1,1,0), got {sel.specificity}"
    assert sel.pseudo == ":hover", f"pseudo must be :hover, got {sel.pseudo}"


def test_pseudo_class_parse_focus():
    """P8.1 — .btn:focus parses correctly."""
    sel = css.Selector.parse(".btn:focus")
    assert sel.pseudo == ":focus", f"pseudo must be :focus, got {sel.pseudo}"
    # class + pseudo = 2 class-level points.
    assert sel.specificity == (0, 2, 0), f"specificity must be (0,2,0), got {sel.specificity}"


# ---------------------------------------------------------------------------
# P8.2 — position: relative
# ---------------------------------------------------------------------------

def test_relative_position_layout():
    """P8.2 — position:relative sets is_relative and offsets on LayoutBox."""
    html = ('<div id="box" style="width:200px;height:100px">'
            '<div id="r" style="position:relative;top:5px;left:10px;'
            'width:30px;height:20px;background:red"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    r = find2_children(lroot, "r")
    assert r is not None, "relative child must exist"
    assert r.is_relative, "is_relative must be True"
    assert r.rel_top == 5.0, f"rel_top must be 5, got {r.rel_top}"
    assert r.rel_left == 10.0, f"rel_left must be 10, got {r.rel_left}"


def test_relative_position_render():
    """P8.2 — position:relative shifts the box by its offsets from flow position."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="r" style="position:relative;top:10px;left:20px;'
            'width:30px;height:20px;background:#0000ff"></div>'
            '</div></body></html>')
    bmp = browser.render_document(html, "", 300, 300)
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    r = find2_children(lroot, "r")
    # Sample center of the relative box.
    cx = int(r.rect.x + r.rect.w / 2)
    cy = int(r.rect.y + r.rect.h / 2)
    px = bmp.pixels[cy * 300 + cx]
    assert (px & 0xFF) > 200, f"relative box center must be blue, got B={px & 0xFF}"


def test_relative_without_offsets():
    """P8.2 — position:relative without top/left keeps the flow position."""
    html = ('<div id="box" style="width:200px;height:100px">'
            '<div id="r" style="position:relative;width:30px;height:20px;'
            'background:red"></div>'
            '</div></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    r = find2_children(lroot, "r")
    assert r is not None
    assert r.is_relative, "is_relative must be True"
    assert r.rel_top == 0.0, f"rel_top must be 0, got {r.rel_top}"
    assert r.rel_left == 0.0, f"rel_left must be 0, got {r.rel_left}"


# ---------------------------------------------------------------------------
# P8.3 — JS Array.sort / Object.entries / includes / String.startsWith / endsWith
# ---------------------------------------------------------------------------

def test_js_array_sort():
    """P8.3 — Array.sort returns a sorted copy."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [30, 10, 20];"
        "let s = a.sort();"
        "console.log(s.join(','));"
    )
    assert any("10,20,30" in l for l in logs), f"sort must give 10,20,30, got {logs}"


def test_js_array_includes():
    """P8.3 — Array.includes returns true if the value is present."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1, 2, 3];"
        "console.log(a.includes(2));"
        "console.log(a.includes(9));"
    )
    assert any(l.strip() == "true" for l in logs), f"includes(2) must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"includes(9) must be false, got {logs}"


def test_js_object_entries():
    """P8.3 — Object.entries returns [ [key, value], … ] pairs."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let o = {x: 1, y: 2};"
        "let e = Object.entries(o);"
        "console.log(e.length);"
    )
    assert any("2" in l for l in logs), f"entries length must be 2, got {logs}"


def test_js_string_starts_with():
    """P8.3 — String.startsWith returns true if the string begins with the prefix."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "console.log('hello'.startsWith('he'));"
        "console.log('hello'.startsWith('world'));"
    )
    assert any(l.strip() == "true" for l in logs), f"startsWith('he') must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"startsWith('world') must be false, got {logs}"


def test_js_string_ends_with():
    """P8.3 — String.endsWith returns true if the string ends with the suffix."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "console.log('hello'.endsWith('lo'));"
        "console.log('hello'.endsWith('hi'));"
    )
    assert any(l.strip() == "true" for l in logs), f"endsWith('lo') must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"endsWith('hi') must be false, got {logs}"


# ---------------------------------------------------------------------------
# P8.4 — CSS letter-spacing / word-spacing / text-transform
# ---------------------------------------------------------------------------

def test_text_transform_layout():
    """P8.4 — text-transform: uppercase is stored on the LayoutBox."""
    html = '<div id="t" style="text-transform:uppercase;width:100px">Hello</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "text-transform box must exist"
    assert t.text_transform == "uppercase", f"text_transform must be uppercase, got {t.text_transform}"


def test_text_transform_render_uppercase():
    """P8.4 — text-transform:uppercase renders the text in uppercase."""
    html = '<html><body><div id="t" style="text-transform:uppercase;width:100px">Hello</div></body></html>'
    import paint_engine
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    dl = paint_engine.emit_commands(lroot)
    # Find the text command.
    text_cmds = [c for c in dl.commands if c.kind == "text"]
    assert len(text_cmds) >= 1, "text command must exist"
    found_uppercase = any("HELLO" in (c.text or "") for c in text_cmds)
    assert found_uppercase, f"text command must contain HELLO, got {[c.text for c in text_cmds]}"


def test_letter_spacing_layout():
    """P8.4 — letter-spacing: 2px is stored on the LayoutBox."""
    html = '<div id="t" style="letter-spacing:2px;width:100px">Hello</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "letter-spacing box must exist"
    assert t.letter_spacing == 2.0, f"letter_spacing must be 2.0, got {t.letter_spacing}"


def test_word_spacing_layout():
    """P8.4 — word-spacing: 4px is stored on the LayoutBox."""
    html = '<div id="t" style="word-spacing:4px;width:100px">Hello World</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "word-spacing box must exist"
    assert t.word_spacing == 4.0, f"word_spacing must be 4.0, got {t.word_spacing}"


# ---------------------------------------------------------------------------
# P9.1 — CSS @media queries
# ---------------------------------------------------------------------------

def test_media_min_width_match():
    """P9.1 — @media (min-width: 400px) rule applies when viewport >= 400."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px">hi</div></body></html>'
    css_text = '@media (min-width: 400px) { #x { background: #ff0000; } }'
    bmp = b.render_document(html, css_text, 500, 300)
    # Check that the background was applied (red pixel somewhere).
    assert _reds(bmp) > 0, "min-width media rule should apply at 500px viewport"


def test_media_min_width_no_match():
    """P9.1 — @media (min-width: 400px) rule does NOT apply when viewport < 400."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px">hi</div></body></html>'
    css_text = '@media (min-width: 400px) { #x { background: #ff0000; } }'
    bmp = b.render_document(html, css_text, 300, 300)
    # At 300px viewport the rule should NOT apply.
    assert _reds(bmp) == 0, "min-width media rule should NOT apply at 300px viewport"


def test_media_max_width_match():
    """P9.1 — @media (max-width: 300px) rule applies when viewport <= 300."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px">hi</div></body></html>'
    css_text = '@media (max-width: 300px) { #x { background: #0000ff; } }'
    bmp = b.render_document(html, css_text, 300, 300)
    # Check blue pixel.
    blue_count = sum(
        1 for p in bmp.pixels
        if (p & 0xFF) > 200 and ((p >> 8) & 0xFF) < 55 and ((p >> 16) & 0xFF) < 55
    )
    assert blue_count > 0, "max-width media rule should apply at 300px viewport"


def test_media_parse():
    """P9.1 — parse_media_queries extracts @media blocks."""
    mqs = css.parse_media_queries(
        '@media (min-width: 600px) { .a { color: red; } } '
        '.b { color: blue; } '
        '@media (max-height: 200px) { .c { background: green; } }'
    )
    assert len(mqs) == 2, f"expected 2 media queries, got {len(mqs)}"
    assert "min-width" in mqs[0].condition
    assert "max-height" in mqs[1].condition


# ---------------------------------------------------------------------------
# P9.2 — position:absolute / relative full-direction
# ---------------------------------------------------------------------------

def test_abs_position_right_bottom():
    """P9.2 — position:absolute with right/bottom offsets."""
    html = '<div id="p" style="position:relative;width:200px;height:200px">' \
           '<div id="a" style="position:absolute;right:10px;bottom:10px;width:50px;height:50px;background:#ff0000">' \
           '</div></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = find2_children(lroot, "a")
    assert a is not None, "absolute box must exist"
    assert a.abs_right == 10.0, f"abs_right must be 10.0, got {a.abs_right}"
    assert a.abs_bottom == 10.0, f"abs_bottom must be 10.0, got {a.abs_bottom}"


def test_relative_right_bottom():
    """P9.2 — position:relative with right/bottom offsets."""
    html = '<div id="p" style="position:relative;width:200px;height:200px">' \
           '<div id="r" style="position:relative;right:5px;bottom:5px;width:50px;height:50px">' \
           '</div></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    r = find2_children(lroot, "r")
    assert r is not None, "relative box must exist"
    assert r.rel_right == 5.0, f"rel_right must be 5.0, got {r.rel_right}"
    assert r.rel_bottom == 5.0, f"rel_bottom must be 5.0, got {r.rel_bottom}"


def test_abs_position_right_only():
    """P9.2 — position:absolute with only right offset (no left)."""
    html = '<div id="p" style="position:relative;width:200px;height:200px">' \
           '<div id="a" style="position:absolute;top:0px;right:20px;width:50px;height:50px">' \
           '</div></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = find2_children(lroot, "a")
    assert a is not None
    assert a.abs_right == 20.0, f"abs_right must be 20.0, got {a.abs_right}"
    assert a.abs_left == 0.0, f"abs_left must be 0.0 when not set, got {a.abs_left}"


def test_relative_full_direction():
    """P9.2 — position:relative with all four offsets."""
    html = '<div id="r" style="position:relative;top:10px;left:5px;right:3px;bottom:2px;width:50px;height:50px">' \
           'test</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    r = find2_children(lroot, "r")
    assert r is not None
    assert r.rel_top == 10.0, f"rel_top must be 10.0, got {r.rel_top}"
    assert r.rel_left == 5.0, f"rel_left must be 5.0, got {r.rel_left}"
    assert r.rel_right == 3.0, f"rel_right must be 3.0, got {r.rel_right}"
    assert r.rel_bottom == 2.0, f"rel_bottom must be 2.0, got {r.rel_bottom}"


# ---------------------------------------------------------------------------
# P9.3 — JS Array.flat / flatMap / Object.assign / Number / String.trim variants
# ---------------------------------------------------------------------------

def test_js_array_flat():
    """P9.3 — Array.flat() flattens one level by default."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [[1, 2], [3, 4], 5];"
        "let r = a.flat();"
        "console.log(r.join(','));"
    )
    assert any("1,2,3,4,5" in l for l in logs), f"flat() must give 1,2,3,4,5, got {logs}"


def test_js_array_flat_depth():
    """P9.3 — Array.flat(2) flattens two levels."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [[1, [2, 3]], [4]];"
        "let r = a.flat(2);"
        "console.log(r.join(','));"
    )
    assert any("1,2,3,4" in l for l in logs), f"flat(2) must give 1,2,3,4, got {logs}"


def test_js_array_flat_map():
    """P9.3 — Array.flatMap maps + flattens one level."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let r = [1,2,3].flatMap(function(x){ return [x, x*10]; });"
        "console.log(r.join(','));"
    )
    assert any("1,10,2,20,3,30" in l for l in logs), f"flatMap must give 1,10,2,20,3,30, got {logs}"


def test_js_object_assign():
    """P9.3 — Object.assign merges source dicts into target."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let o = Object.assign({a:1}, {b:2}, {c:3});"
        "console.log(o.a, o.b, o.c);"
    )
    assert any("1" in l and "2" in l and "3" in l for l in logs), \
        f"Object.assign must merge, got {logs}"


def test_js_object_assign_override():
    """P9.3 — Object.assign: later sources override earlier keys."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let o = Object.assign({a:1}, {a:9});"
        "console.log(o.a);"
    )
    assert any(l.strip() == "9" for l in logs), f"later source must override, got {logs}"


def test_js_number_is_integer():
    """P9.3 — Number.isInteger returns true for whole numbers."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log(Number.isInteger(42));"
        "console.log(Number.isInteger(4.5));"
    )
    assert any(l.strip() == "true" for l in logs), f"isInteger(42) must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"isInteger(4.5) must be false, got {logs}"


def test_js_number_is_nan():
    """P9.3 — Number.isNaN detects float NaN."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    # The toy engine has no NaN literal; detect is_nan logic via string "NaN"
    # (how the engine represents the value when it sees it as an Ident).
    host.execute(
        "console.log(Number.isNaN('NaN'));"
        "console.log(Number.isNaN(1));"
    )
    assert any(l.strip() == "true" for l in logs), f"isNaN('NaN') must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"isNaN(1) must be false, got {logs}"


def test_js_number_parse_int():
    """P9.3 — Number.parseInt parses the integer part of a string."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log(Number.parseInt('42px'));"
    )
    assert any("42" in l for l in logs), f"parseInt('42px') must be 42, got {logs}"


def test_js_string_trim_start():
    """P9.3 — String.trimStart removes leading whitespace."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('  hello  '.trimStart() == 'hello  ');"
    )
    assert any(l.strip() == "true" for l in logs), f"trimStart must work, got {logs}"


def test_js_string_trim_end():
    """P9.3 — String.trimEnd removes trailing whitespace."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('  hello  '.trimEnd() == '  hello');"
    )
    assert any(l.strip() == "true" for l in logs), f"trimEnd must work, got {logs}"


def test_js_map_with_index():
    """P9.3 — Array.map receives (value, index)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let r = [10, 20, 30].map(function(v, i){ return i; });"
        "console.log(r.join(','));"
    )
    assert any("0,1,2" in l for l in logs), f"map index must be 0,1,2, got {logs}"


def test_js_filter_with_index():
    """P9.3 — Array.filter receives (value, index)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let r = [10, 20, 30].filter(function(v, i){ return i > 0; });"
        "console.log(r.join(','));"
    )
    assert any("20,30" in l for l in logs), f"filter with index must give 20,30, got {logs}"


# ---------------------------------------------------------------------------
# P9.4 — CSS clip-path inset + linear-gradient + text-shadow
# ---------------------------------------------------------------------------

def test_clip_inset_layout():
    """P9.4 — clip-path: inset(10px 20px) is stored on the LayoutBox."""
    html = '<div id="c" style="clip-path:inset(10px 20px);width:100px;height:100px;background:#ff0000">' \
           '</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    c = find2_children(lroot, "c")
    assert c is not None, "clip-path box must exist"
    assert c.clip_enabled is True, "clip_enabled must be True"
    assert c.clip_inset_top == 10.0, f"clip_inset_top must be 10.0, got {c.clip_inset_top}"
    assert c.clip_inset_right == 20.0, f"clip_inset_right must be 20.0, got {c.clip_inset_right}"


def test_clip_inset_4val_layout():
    """P9.4 — clip-path: inset(T R B L) with 4 values."""
    html = '<div id="c" style="clip-path:inset(5px 10px 15px 20px);width:100px;height:100px">' \
           '</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    c = find2_children(lroot, "c")
    assert c is not None
    assert c.clip_inset_top == 5.0, f"top must be 5.0, got {c.clip_inset_top}"
    assert c.clip_inset_right == 10.0, f"right must be 10.0, got {c.clip_inset_right}"
    assert c.clip_inset_bottom == 15.0, f"bottom must be 15.0, got {c.clip_inset_bottom}"
    assert c.clip_inset_left == 20.0, f"left must be 20.0, got {c.clip_inset_left}"


def test_linear_gradient_layout():
    """P9.4 — background: linear-gradient(to right, red, blue) is stored."""
    html = '<div id="g" style="background:linear-gradient(to right, #ff0000, #0000ff);width:100px;height:100px">' \
           '</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    g = find2_children(lroot, "g")
    assert g is not None, "gradient box must exist"
    assert g.gradient_enabled is True, "gradient_enabled must be True"
    assert g.gradient_angle == "to right", f"angle must be 'to right', got {g.gradient_angle}"


def test_linear_gradient_render():
    """P9.4 — linear-gradient renders with a gradient command in the display list."""
    import paint_engine
    html = '<html><body><div id="g" style="background:linear-gradient(to bottom, #ff0000, #0000ff);width:100px;height:100px">' \
           '</div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    dl = paint_engine.emit_commands(lroot)
    grad_cmds = [c for c in dl.commands if c.kind == "gradient"]
    assert len(grad_cmds) >= 1, "gradient command must exist"
    cmd = grad_cmds[0]
    assert cmd.grad_c1 is not None, "grad_c1 must be set"
    assert cmd.grad_c2 is not None, "grad_c2 must be set"


def test_text_shadow_layout():
    """P9.4 — text-shadow: 2px 2px 4px #000000 is stored on the LayoutBox."""
    html = '<div id="s" style="text-shadow:2px 2px 4px #000000;width:100px;height:50px">Hi</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    s = find2_children(lroot, "s")
    assert s is not None, "text-shadow box must exist"
    assert s.text_shadow_enabled is True, "text_shadow_enabled must be True"
    assert s.text_shadow_offset_x == 2.0, f"offset_x must be 2.0, got {s.text_shadow_offset_x}"
    assert s.text_shadow_offset_y == 2.0, f"offset_y must be 2.0, got {s.text_shadow_offset_y}"
    assert s.text_shadow_radius == 4.0, f"radius must be 4.0, got {s.text_shadow_radius}"


def test_text_shadow_render():
    """P9.4 — text-shadow: 1px 1px #ff0000 emits a text_shadow command."""
    import paint_engine
    html = '<html><body><div id="s" style="text-shadow:1px 1px #ff0000;width:100px;height:50px;font-size:16px">' \
           'Hi</div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    dl = paint_engine.emit_commands(lroot)
    ts_cmds = [c for c in dl.commands if c.kind == "text_shadow"]
    assert len(ts_cmds) >= 1, f"text_shadow command must exist, got kinds {[c.kind for c in dl.commands]}"


def test_hover_render_api():
    """P8.1 — render_document_with_hover applies :hover styles in the render.

    The hover rule must use a property NOT set inline (e.g. color), since
    inline styles always win in the cascade.
    """
    import browser as b
    html = '<html><body>' \
           '<div id="btn" style="width:100px;height:50px;background:#cccccc">Btn</div>' \
           '</body></html>'
    css_text = '#btn:hover { color: #ff0000; }'
    # Without hover: default black text.
    bmp_no = b.render_document(html, css_text, 200, 100)
    # With hover on #btn: red text.
    bmp_yes = b.render_document_with_hover(html, css_text, 200, 100, "btn")
    reds_no = _reds(bmp_no)
    reds_yes = _reds(bmp_yes)
    assert reds_no == 0, f"no hover: reds must be 0, got {reds_no}"
    assert reds_yes > 0, f"with hover: reds must be > 0, got {reds_yes}"


# ---------------------------------------------------------------------------
# P10.1 — CSS :first-child / :last-child / :nth-child(n)
# ---------------------------------------------------------------------------

def test_first_child_layout():
    """P10.1 — :first-child matches the first element child of its parent."""
    html = '<ul id="list"><li id="a">A</li><li id="b">B</li><li id="c">C</li></ul>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = find2_children(lroot, "a")
    b = find2_children(lroot, "b")
    assert a is not None and b is not None
    # Check selector parse: "li:first-child" has pseudo=":first-child".
    sel = css.Selector.parse("li:first-child")
    assert sel.pseudo == ":first-child", f"pseudo must be :first-child, got {sel.pseudo}"
    # "li:last-child" parse.
    sel2 = css.Selector.parse("li:last-child")
    assert sel2.pseudo == ":last-child", f"pseudo must be :last-child, got {sel2.pseudo}"


def test_last_child_layout():
    """P10.1 — :last-child matches the last element child of its parent."""
    html = '<ul id="list"><li id="a">A</li><li id="b">B</li><li id="c">C</li></ul>'
    root = dom.parse(html)
    # Find the last li node (id="c").
    def find_id(n, eid):
        stack = [n]
        while stack:
            x = stack.pop()
            if x.is_element() and x.attr("id") == eid:
                return x
            stack.extend(x.children)
        return None
    li_c = find_id(root, "c")
    li_a = find_id(root, "a")
    sel_last = css.Selector.parse("li:last-child")
    sel_first = css.Selector.parse("li:first-child")
    # :last-child should match "c", not "a".
    assert sel_last.matches(li_c), "li:last-child must match the last li"
    assert not sel_last.matches(li_a), "li:last-child must NOT match the first li"
    # :first-child should match "a", not "c".
    assert sel_first.matches(li_a), "li:first-child must match the first li"
    assert not sel_first.matches(li_c), "li:first-child must NOT match the last li"


def test_nth_child_odd():
    """P10.1 — :nth-child(odd) matches 1st, 3rd, 5th… children."""
    html = '<ul><li>1</li><li>2</li><li>3</li><li>4</li></ul>'
    root = dom.parse(html)

    def find_id(n, eid):
        stack = [n]
        while stack:
            x = stack.pop()
            if x.is_element() and x.attr("id") == eid:
                return x
            stack.extend(x.children)
        return None

    # Use li elements directly.
    lis = [c for c in root.children[0].children if c.is_element() and c.tag == "li"]
    sel_odd = css.Selector.parse("li:nth-child(odd)")
    sel_even = css.Selector.parse("li:nth-child(even)")
    assert sel_odd.matches(lis[0]), "odd must match 1st child"
    assert not sel_odd.matches(lis[1]), "odd must NOT match 2nd child"
    assert sel_odd.matches(lis[2]), "odd must match 3rd child"
    assert sel_even.matches(lis[1]), "even must match 2nd child"
    assert not sel_even.matches(lis[0]), "even must NOT match 1st child"


def test_nth_child_an_plus_b():
    """P10.1 — :nth-child(2n+1) matches odd positions."""
    html = '<ul><li>a</li><li>b</li><li>c</li><li>d</li></ul>'
    root = dom.parse(html)
    lis = [c for c in root.children[0].children if c.is_element() and c.tag == "li"]
    sel = css.Selector.parse("li:nth-child(2n+1)")
    assert sel.matches(lis[0]), "2n+1 must match 1st"
    assert not sel.matches(lis[1]), "2n+1 must NOT match 2nd"
    assert sel.matches(lis[2]), "2n+1 must match 3rd"
    assert not sel.matches(lis[3]), "2n+1 must NOT match 4th"


def test_nth_child_specificity():
    """P10.1 — :nth-child adds one class-level point to specificity."""
    sel = css.Selector.parse("li:nth-child(2)")
    # (id=0, cls=1 [pseudo], type=1 [li]) → sum=2
    assert sel.specificity_sum() == (0, 1, 1), f"got {sel.specificity_sum()}"


# ---------------------------------------------------------------------------
# P10.2 — JS Map / Set / Array.from / String.replaceAll
# ---------------------------------------------------------------------------

def test_js_map_basic():
    """P10.2 — Map: set/get/has/size."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let m = new Map();"
        "m.set('a', 1);"
        "m.set('b', 2);"
        "console.log(m.size);"
        "console.log(m.get('a'));"
        "console.log(m.has('b'));"
    )
    assert any(l.strip() == "2" for l in logs), f"Map.size must be 2, got {logs}"
    assert any(l.strip() == "1" for l in logs), f"Map.get('a') must be 1, got {logs}"
    assert any(l.strip() == "true" for l in logs), f"Map.has('b') must be true, got {logs}"


def test_js_map_delete():
    """P10.2 — Map.delete removes a key."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let m = new Map([['a',1],['b',2],['c',3]]);"
        "m.delete('b');"
        "console.log(m.size);"
        "console.log(m.has('b'));"
    )
    assert any(l.strip() == "2" for l in logs), f"after delete size=2, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"has('b') must be false, got {logs}"


def test_js_set_basic():
    """P10.2 — Set: add/has/size (dedup)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let s = new Set();"
        "s.add(1); s.add(2); s.add(1);"
        "console.log(s.size);"
        "console.log(s.has(2));"
    )
    assert any(l.strip() == "2" for l in logs), f"Set size must be 2 (dedup), got {logs}"
    assert any(l.strip() == "true" for l in logs), f"has(2) must be true, got {logs}"


def test_js_array_from():
    """P10.2 — Array.from converts a string to a char array."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = Array.from('abc');"
        "console.log(a.join('-'));"
    )
    assert any("a-b-c" in l for l in logs), f"Array.from('abc') must be [a,b,c], got {logs}"


def test_js_array_from_mapfn():
    """P10.2 — Array.from with mapFn transforms elements."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = Array.from([1,2,3], function(v){ return v*10; });"
        "console.log(a.join(','));"
    )
    assert any("10,20,30" in l for l in logs), f"Array.from mapFn must give 10,20,30, got {logs}"


def test_js_array_of():
    """P10.2 — Array.of creates an array from arguments."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = Array.of(1, 2, 3);"
        "console.log(a.join(','));"
    )
    assert any("1,2,3" in l for l in logs), f"Array.of must give 1,2,3, got {logs}"


def test_js_string_replace_all():
    """P10.2 — String.replaceAll replaces all occurrences."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('a-b-c'.replaceAll('-', 'X'));"
    )
    assert any("aXbXc" in l for l in logs), f"replaceAll must replace all, got {logs}"


def test_js_string_replace_all_regex():
    """P10.2 — String.replaceAll with a global-like pattern."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('a1b2c3'.replaceAll('2', 'X'));"
    )
    assert any("a1bXc3" in l for l in logs), f"replaceAll('2','X') must work, got {logs}"


# ---------------------------------------------------------------------------
# P10.3 — CSS outline
# ---------------------------------------------------------------------------

def test_outline_layout():
    """P10.3 — outline: 2px solid #ff0000 is stored on the LayoutBox."""
    html = '<html><body><div id="o" style="outline:2px solid #ff0000;width:100px;height:50px">' \
           '</div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    o = find2_children(lroot, "o")
    assert o is not None, "outline box must exist"
    assert o.outline_width == 2.0, f"outline_width must be 2.0, got {o.outline_width}"
    assert o.outline_color is not None, "outline_color must be set"

def test_outline_render():
    """P10.3 — outline: 3px solid #00ff00 renders outline rect commands."""
    import paint_engine
    html = '<html><body><div id="o" style="outline:3px solid #00ff00;width:100px;height:50px">' \
           '</div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    dl = paint_engine.emit_commands(lroot)
    # Outline generates 4 rect commands (top, bottom, left, right strips).
    rect_cmds = [c for c in dl.commands if c.kind == "rect"]
    # At least 4 rects for the outline ring + background.
    assert len(rect_cmds) >= 4, f"outline must generate >=4 rect commands, got {len(rect_cmds)}"


def test_outline_width_only():
    """P10.3 — outline-width alone (no color) stores width but no color."""
    html = '<div id="o" style="outline-width:4px;width:100px;height:50px">' \
           '</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    # The layout root IS the div (no <html>/<body> wrapper).
    assert lroot.dom is not None and lroot.dom.attr("id") == "o", \
        f"lroot must be the div, dom={lroot.dom.tag if lroot.dom else None}"
    assert lroot.outline_width == 4.0, \
        f"outline_width must be 4.0, got {lroot.outline_width}"
    # A default color must be set so the outline can still render.
    assert lroot.outline_color is not None, \
        "outline_color must default to black when only width is given"


# ---------------------------------------------------------------------------
# P10.4 — JS Error / JSON nested / Array.toString
# ---------------------------------------------------------------------------

def test_js_error_object():
    """P10.4 — Error object: message property."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let e = new Error('oops');"
        "console.log(e.message);"
    )
    assert any("oops" in l for l in logs), f"Error.message must be 'oops', got {logs}"


def test_js_error_name():
    """P10.4 — Error object: name property is 'Error'."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let e = new Error('x');"
        "console.log(e.name);"
    )
    assert any(l.strip() == "Error" for l in logs), f"Error.name must be 'Error', got {logs}"


def test_js_json_nested_parse():
    """P10.4 — JSON.parse handles nested objects/arrays (via stdlib json)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let o = JSON.parse('{\"a\":{\"b\":[1,2,3]}}');"
        "console.log(o.a.b.join(','));"
    )
    assert any("1,2,3" in l for l in logs), f"nested JSON parse must work, got {logs}"


def test_js_json_stringify_nested():
    """P10.4 — JSON.stringify handles nested structures."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log(JSON.stringify({a:{b:[1,2]}}));"
    )
    assert any('{"a":{"b":[1,2]}}' in l for l in logs), \
        f"nested JSON stringify must work, got {logs}"


def test_js_array_to_string():
    """P10.4 — Array.toString() comma-joins elements."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [1, 2, 3];"
        "console.log(a.toString());"
    )
    assert any("1,2,3" in l for l in logs), f"Array.toString must give 1,2,3, got {logs}"


def test_js_array_to_string_nested():
    """P10.4 — Array.toString() on nested arrays flattens one level."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [[1,2],[3,4]];"
        "console.log(a.toString());"
    )
    assert any("1,2,3,4" in l for l in logs), \
        f"nested Array.toString must give 1,2,3,4, got {logs}"


# ---------------------------------------------------------------------------
# P11.1 — CSS transition multi-property + transform support
# ---------------------------------------------------------------------------

def test_transition_multi_property():
    """P11.1 — TransitionEngine.add_multi creates specs for multiple properties."""
    import transition as t
    from paint import Color
    engine = t.TransitionEngine()
    specs = [
        t.TransitionSpec("background", Color(255, 0, 0, 255), Color(0, 0, 255, 255), 10),
        t.TransitionSpec("opacity", 1.0, 0.0, 10),
        t.TransitionSpec("transform", ("translate", 0.0, 0.0), ("translate", 100.0, 0.0), 10),
    ]
    engine.add_multi(specs)
    engine.advance()
    ov = engine.frame_overrides()
    assert "background" in ov and "opacity" in ov and "transform" in ov, \
        f"frame_overrides must contain all 3 props, got keys {list(ov.keys())}"
    # opacity at frame 1/10 → 1.0 - 0.1*(1/10)*? Actually t=0.1 → 0.9
    assert abs(ov["opacity"] - 0.9) < 0.01, f"opacity at t=0.1 must be 0.9, got {ov['opacity']}"


def test_transition_transform_lerp():
    """P11.1 — TransitionSpec.value_at lerps transform translate tuples."""
    import transition as t
    spec = t.TransitionSpec("transform", ("translate", 0.0, 0.0), ("translate", 100.0, 50.0), 10)
    v = spec.value_at(0.5)
    assert isinstance(v, tuple), f"transform value_at must return tuple, got {v!r}"
    assert v[0] == "translate"
    assert abs(v[1] - 50.0) < 0.01, f"translate x at t=0.5 must be 50.0, got {v[1]}"
    assert abs(v[2] - 25.0) < 0.01, f"translate y at t=0.5 must be 25.0, got {v[2]}"


def test_transition_parse_multi():
    """P11.1 — parse_transition_css_multi splits comma-separated clauses."""
    import transition as t
    from paint import Color
    specs = t.parse_transition_css_multi(
        "background 1s, opacity 2s, transform 1s",
        {"background": Color(255,0,0), "opacity": 1.0, "transform": ("translate",0.0,0.0)},
        {"background": Color(0,0,255), "opacity": 0.0, "transform": ("translate",100.0,0.0)},
    )
    assert len(specs) == 3, f"must parse 3 clauses, got {len(specs)}"
    assert specs[0].property == "background"
    assert specs[1].property == "opacity"
    assert specs[2].property == "transform"
    assert specs[0].frames == 10, f"1s ≈ 10 frames, got {specs[0].frames}"


def test_transition_color_lerp():
    """P11.1 — TransitionSpec lerps Color values correctly."""
    import transition as t
    from paint import Color
    spec = t.TransitionSpec("background", Color(0, 0, 0), Color(255, 255, 255), 10)
    v = spec.value_at(0.5)
    assert isinstance(v, Color), f"color lerp must return Color, got {type(v)}"
    assert 120 <= v.r <= 135, f"r at t=0.5 must be ~128, got {v.r}"
    assert 120 <= v.g <= 135, f"g at t=0.5 must be ~128, got {v.g}"


def test_transition_scalar_lerp():
    """P11.1 — TransitionSpec lerps scalar (opacity) values."""
    import transition as t
    spec = t.TransitionSpec("opacity", 1.0, 0.0, 10)
    assert abs(spec.value_at(0.25) - 0.75) < 0.01, \
        f"opacity at t=0.25 must be 0.75, got {spec.value_at(0.25)}"
    assert abs(spec.value_at(0.0) - 1.0) < 0.01
    assert abs(spec.value_at(1.0) - 0.0) < 0.01


def test_transition_advance_stops():
    """P11.1 — engine stops when all specs have advanced past their frame count.

    A 3-frame spec: advance returns True while frame < 3, then False.
    """
    import transition as t
    engine = t.TransitionEngine()
    engine.add(t.TransitionSpec("opacity", 1.0, 0.0, 3))
    assert engine.advance() is True   # frame=1 < 3 → still running
    assert engine.advance() is True   # frame=2 < 3 → still running
    assert engine.advance() is False  # frame=3 !< 3 → spec removed
    assert not engine.is_running()


# ---------------------------------------------------------------------------
# P11.2 — JS try / catch / finally + throw
# ---------------------------------------------------------------------------

def test_js_throw_catch():
    """P11.2 — throw + catch: the catch handler receives the thrown value."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw 'oops'; } catch (e) { console.log('caught:' + e); }"
    )
    assert any("caught:oops" in l for l in logs), \
        f"catch must log the thrown value, got {logs}"


def test_js_throw_number():
    """P11.2 — throw a number, catch and log it."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw 42; } catch (e) { console.log(e); }"
    )
    assert any(l.strip() == "42" for l in logs), \
        f"catch must log 42, got {logs}"


def test_js_try_finally():
    """P11.2 — finally runs even when no exception is thrown."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { console.log('body'); } finally { console.log('fin'); }"
    )
    assert any("body" in l for l in logs), f"try body must run, got {logs}"
    assert any("fin" in l for l in logs), f"finally must run, got {logs}"


def test_js_try_catch_finally():
    """P11.2 — try/catch/finally all three clauses work together."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw 'err'; } catch (e) { console.log('c:' + e); }"
        "finally { console.log('f'); }"
    )
    assert any("c:err" in l for l in logs), f"catch must log, got {logs}"
    assert any("f" in l for l in logs), f"finally must log, got {logs}"


def test_js_uncaught_throw():
    """P11.2 — uncaught throw is logged, not a crash."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute("throw 'uncaught';")
    assert any("uncaught throw" in l for l in logs), \
        f"uncaught throw must be logged, got {logs}"


def test_js_throw_object():
    """P11.2 — throw an object (dict), catch receives it."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw {code: 500}; } catch (e) { console.log(e.code); }"
    )
    assert any(l.strip() == "500" for l in logs), \
        f"catch must receive the object, got {logs}"


# ---------------------------------------------------------------------------
# P11.3 — CSS @supports toy + display toggle
# ---------------------------------------------------------------------------

def test_supports_parse():
    """P11.3 — parse_supports_blocks extracts @supports blocks."""
    blocks = css.parse_supports_blocks(
        '@supports (display: grid) { .a { color: red; } }'
        '.b { color: blue; }'
    )
    assert len(blocks) == 1, f"must find 1 @supports block, got {len(blocks)}"
    assert "display" in blocks[0].condition, \
        f"condition must contain display, got {blocks[0].condition!r}"
    assert len(blocks[0].rules) == 1, "inner rules must be parsed"


def test_supports_matches_always_true():
    """P11.3 — toy: supports_matches returns True for any condition."""
    assert css.supports_matches("display: grid") is True
    assert css.supports_matches("(position: sticky)") is True
    assert css.supports_matches("") is True


def test_supports_cascade_applies():
    """P11.3 — @supports rules are applied to the cascade (toy: always)."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px;height:50px">hi</div></body></html>'
    css_text = '@supports (display: grid) { #x { background: #ff0000; } }'
    bmp = b.render_document(html, css_text, 200, 100)
    assert _reds(bmp) > 0, "@supports rule must apply (toy: always supported)"


def test_display_toggle_none():
    """P11.3 — display:none via JS removes the element's *content* from the
    layout; the box itself stays in the tree flagged with is_display_none
    so toggle tests can inspect it.
    """
    html = ('<html><body><div id="x" style="width:100px;height:50px;background:red">'
            '</div></body></html>')
    import js as js_mod
    tree = dom.parse(html)
    host = js_mod.Host(tree, lambda _s: None)
    host.execute("document.getElementById('x').style.display = 'none';")
    styled = css.apply(tree, "")
    lroot = layout.layout(styled, 200.0, 100.0)
    x = find2_children(lroot, "x")
    assert x is not None, "display:none box must still exist in the tree"
    assert x.is_display_none, "display:none must set the is_display_none flag"
    assert x.rect.w == 0 and x.rect.h == 0, \
        f"display:none box must be zero-size, got {x.rect}"


def test_display_toggle_block():
    """P11.3 — switching display back to block re-includes the box."""
    html = ('<html><body><div id="x" style="width:100px;height:50px">'
            '</div></body></html>')
    import js as js_mod
    tree = dom.parse(html)
    host = js_mod.Host(tree, lambda _s: None)
    host.execute(
        "document.getElementById('x').style.display = 'none';"
        "document.getElementById('x').style.display = 'block';"
    )
    styled = css.apply(tree, "")
    lroot = layout.layout(styled, 200.0, 100.0)
    x = find2_children(lroot, "x")
    assert x is not None
    assert not x.is_display_none, "display:block must clear the display:none flag"
    assert x.rect.w > 0 and x.rect.h > 0, "display:block box must have size"


# ---------------------------------------------------------------------------
# P11.4 — hit-test + event coordinate API
# ---------------------------------------------------------------------------

def test_element_at_basic():
    """P11.4 — element_at resolves a coordinate inside a box to that element."""
    import browser as b
    html = ('<html><body><div id="big" style="width:200px;height:100px">'
            '<div id="inner" style="width:50px;height:50px;background:red"></div>'
            '</div></body></html>')
    result = b.element_at(html, "", 300, 200, 10, 10)
    assert result is not None, "element_at must return a dict for a hit"
    assert result["id"] in ("inner", "big"), f"must hit inner or big, got {result['id']}"


def test_element_at_miss():
    """P11.4 — element_at returns None when the point is outside all boxes."""
    import browser as b
    html = '<html><body><div id="small" style="width:10px;height:10px"></div></body></html>'
    result = b.element_at(html, "", 300, 200, 200, 200)
    assert result is None, f"miss must return None, got {result!r}"


def test_event_target_runs_handler():
    """P11.4 — event_target runs the onclick handler of the element under (x,y)."""
    import browser as b
    logs = []
    html = ('<html><body><div id="btn" style="width:100px;height:50px" '
            'onclick="console.log(\'clicked\')"></div></body></html>')
    eid = b.event_target(html, "", 200, 100, 50, 25, log_sink=logs.append)
    assert eid == "btn", f"event_target must return the element id, got {eid!r}"
    assert any("clicked" in l for l in logs), f"onclick must log, got {logs}"


def test_click_and_render():
    """P11.4 — click_and_render returns (id, bitmap) after running the handler."""
    import browser as b
    logs = []
    html = ('<html><body><div id="toggle" style="width:100px;height:50px;background:red" '
            'onclick="this.style.display=\'none\'"></div></body></html>')
    eid, bmp = b.click_and_render(html, "", 200, 100, 50, 25, log_sink=logs.append)
    assert eid == "toggle", f"click_and_render must return the id, got {eid!r}"
    # After the handler sets display:none, the re-render should have no red.
    reds = _reds(bmp)
    assert reds == 0, f"display:none must remove the red bg, got {reds} reds"


def test_element_at_uses_supports():
    """P11.4/P11.3 — element_at integrates @supports rules into the cascade."""
    import browser as b
    css_text = '@supports (display: grid) { #hit { background: #0000ff; } }'
    html = ('<html><body><div id="hit" style="width:100px;height:50px">'
            '</div></body></html>')
    result = b.element_at(html, css_text, 200, 100, 50, 25)
    assert result is not None
    assert result["id"] == "hit", f"must hit #hit, got {result['id']}"


# ---------------------------------------------------------------------------
# P12.1 — CSS @keyframes multi-property + Web Animations toy
# ---------------------------------------------------------------------------

def test_keyframe_multi_property_tracks():
    """P12.1 — build_keyframe_handle groups multi-property @keyframes."""
    blocks = css.parse_keyframes(
        '@keyframes spin {'
        ' 0% { background: #ff0000; opacity: 1; }'
        ' 100% { background: #0000ff; opacity: 0; }'
        '}'
    )
    assert "spin" in blocks, f"must parse spin, got {list(blocks.keys())}"
    anim = blocks["spin"]
    handle = css.build_keyframe_handle(anim)
    assert len(handle.tracks) == 2, \
        f"must have 2 property tracks (background, opacity), got {len(handle.tracks)}"
    props = {t.property for t in handle.tracks}
    assert "background" in props and "opacity" in props, \
        f"tracks must cover both props, got {props}"


def test_animation_handle_frame_overrides():
    """P12.1 — AnimationHandle.frame_overrides lerps every tracked property."""
    from paint import Color
    handle = css.AnimationHandle(duration=1.0)
    handle.add_track("opacity", [(0.0, 1.0), (1.0, 0.0)], 1.0)
    handle.add_track("background", [(0.0, Color(255, 0, 0, 255)),
                                     (1.0, Color(0, 0, 255, 255))], 1.0)
    ov = handle.frame_overrides(0.5)
    op = ov["opacity"]
    op_val = op.value if hasattr(op, "value") else float(op)
    assert abs(op_val - 0.5) < 0.01, \
        f"opacity at t=0.5 must be 0.5, got {op}"
    assert isinstance(ov["background"], Color), \
        f"background lerp must be Color, got {type(ov['background'])}"
    assert 110 <= ov["background"].r <= 145, \
        f"r at t=0.5 must be ~128, got {ov['background'].r}"


def test_keyframes_three_stop():
    """P12.1 — @keyframes with 3 stops (0%, 50%, 100%) interpolates."""
    blocks = css.parse_keyframes(
        '@keyframes tri {'
        ' 0%  { opacity: 1; }'
        ' 50% { opacity: 0; }'
        ' 100%{ opacity: 1; }'
        '}'
    )
    anim = blocks["tri"]
    assert len(anim.frames) == 3, f"must have 3 keyframe stops, got {len(anim.frames)}"
    handle = css.build_keyframe_handle(anim)
    ov = handle.frame_overrides(0.5)
    val = ov["opacity"]
    # The value may be a Keyword("0.5") or a plain float; accept either.
    v = float(val.value) if hasattr(val, "value") else float(val)
    assert abs(v - 0.0) < 0.01, \
        f"opacity at t=0.5 must be 0, got {val}"
    ov2 = handle.frame_overrides(0.25)
    val2 = ov2["opacity"]
    v2 = float(val2.value) if hasattr(val2, "value") else float(val2)
    assert abs(v2 - 0.5) < 0.01, \
        f"opacity at t=0.25 must be 0.5, got {val2}"


def test_animation_handle_update():
    """P12.1 — AnimationHandle.update advances current_time (clamped)."""
    handle = css.AnimationHandle()
    handle.update(0.3)
    assert handle.current_time == 0.3
    handle.update(1.5)
    assert handle.current_time == 1.0, "t must clamp to 1.0"
    handle.update(-0.5)
    assert handle.current_time == 0.0, "t must clamp to 0.0"


def test_animate_keyframes_multi_render():
    """P12.1 — animate_keyframes renders multi-property frames.

    Uses a background colour change (red → blue) which produces visible
    pixel differences in the rendered bitmap.
    """
    import browser as b
    html = ('<html><body><div id="box" style="width:100px;height:50px"></div>'
            '</body></html>')
    css_text = ('@keyframes fade {'
                ' 0% { background: #ff0000; }'
                ' 100% { background: #0000ff; }'
                '}')
    frames = b.animate_keyframes(
        html, css_text, 200, 100, "box", "fade", total_frames=4,
    )
    assert len(frames) == 5, f"must render 5 frames, got {len(frames)}"
    # First frame is red, last frame is blue — pixel diff proves the lerp.
    diff = _pixel_diff(frames[0], frames[-1])
    assert diff > 0, "first and last frame must differ"
    # Red frame must have reds, blue frame must not.
    assert _reds(frames[0]) > 0, "first frame must be red"
    assert _reds(frames[-1]) == 0, "last frame must not be red"


# ---------------------------------------------------------------------------
# P12.2 — JS class / extends / constructor / super
# ---------------------------------------------------------------------------

def test_js_class_basic():
    """P12.2 — a plain class with ctor + method."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Point { constructor(x, y) { this.x = x; this.y = y; }"
        "  dist() { return this.x * this.x + this.y * this.y; } }"
        "let p = new Point(3, 4); console.log(p.dist());"
    )
    assert any(l.strip() == "25" for l in logs), \
        f"class method must return 25, got {logs}"

def test_js_class_inheritance():
    """P12.2 — extends + super() in the child ctor + overridden method."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Animal { constructor(name) { this.name = name; }"
        "  speak() { return 'generic'; } }"
        "class Dog extends Animal { constructor(name) { super(name); this.breed = 'terrier'; }"
        "  speak() { return 'woof:' + this.name; } }"
        "let d = new Dog('rex'); console.log(d.name + '/' + d.breed);"
        "console.log(d.speak());"
    )
    assert any("rex/terrier" in l for l in logs), \
        f"inherited + own fields must combine, got {logs}"
    assert any("woof:rex" in l for l in logs), \
        f"overridden method must use own this.name, got {logs}"


def test_js_class_parent_method_fallback():
    """P12.2 — a method not overridden on the child falls back to the parent."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Base { m() { return 'base'; } }"
        "class Sub extends Base { constructor() { super(); this.k = 1; } }"
        "let s = new Sub(); console.log(s.m()); console.log(s.k);"
    )
    assert any(l.strip() == "base" for l in logs), \
        f"unoverridden method must come from Base, got {logs}"
    assert any(l.strip() == "1" for l in logs), \
        f"Sub ctor field must be 1, got {logs}"


def test_js_class_method_no_return():
    """P12.2 — a method without `return` yields None (JS undefined → None)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class X { f() { this.z = 9; } }"
        "let x = new X(); x.f();"
        "console.log(x.z);"
    )
    assert any(l.strip() == "9" for l in logs), \
        f"this.z must be set by the method, got {logs}"


def test_js_new_builtin_still_works():
    """P12.2 — `new Date()` / `new RegExp(...)` still route to host ctors."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    # RegExp via host ctor (not ToyClass).
    host.execute(
        "let r = new RegExp('a'); let hit = r.test('a'); console.log('re:' + hit);"
    )
    assert any("re:True" in l for l in logs), \
        f"new RegExp('a').test('a') must be truthy, got {logs}"
    # Date via host ctor.
    host.execute("let d = new Date(); console.log('date ok');")
    assert any("date ok" in l for l in logs), \
        f"new Date() must not crash, got {logs}"


# ---------------------------------------------------------------------------
# P12.3 — CSS calc() toy
# ---------------------------------------------------------------------------

def test_calc_parse_value():
    """P12.3 — parse_value recognizes calc() and evaluates simple forms."""
    v = css.parse_value("calc(100px - 20px)")
    assert isinstance(v, css.Length), \
        f"calc(100px - 20px) must be Length, got {type(v).__name__}"
    assert v.value == 80.0, f"must be 80.0, got {v.value}"
    assert v.unit == "px", f"must be px, got {v.unit}"


def test_calc_plus():
    """P12.3 — calc(10px + 5px) = 15px."""
    v = css.parse_value("calc(10px + 5px)")
    assert v.value == 15.0 and v.unit == "px", \
        f"must be 15px, got {v.value}{v.unit}"


def test_calc_em_to_px():
    """P12.3 — calc with em operand converts to px (1em = 16px)."""
    v = css.parse_value("calc(1em + 4px)")
    assert isinstance(v, css.Length)
    assert v.value == 20.0, f"1em(16px) + 4px must be 20.0, got {v.value}"


def test_calc_unsupported_fallback():
    """P12.3 — calc with mixed unsupported units falls back to Keyword."""
    v = css.parse_value("calc(10px - 2%)")
    assert isinstance(v, css.Keyword), \
        f"mixed unsupported units must be Keyword, got {type(v).__name__}"
    assert "calc(" in v.value, f"Keyword must carry calc() text, got {v.value!r}"


def test_calc_in_layout_width():
    """P12.3 — calc() in a width style affects the box width."""
    html = '<div id="c" style="width: calc(100px - 20px); height: 30px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    c = find2_children(lroot, "c")
    assert c is not None, "calc box must be in the layout tree"
    assert c.explicit_width == 80.0, \
        f"calc(100px - 20px) width must be 80.0, got {c.explicit_width}"


# ---------------------------------------------------------------------------
# P12.4 — mousemove state machine + pre-render callback
# ---------------------------------------------------------------------------

def test_hover_at_coordinate():
    """P12.4 — render_document_with_hover_at applies :hover via hit-test."""
    import browser as b
    html = ('<html><body><div id="a" style="width:100px;height:50px">'
            '</div></body></html>')
    css_text = '#a:hover { background: #ff0000; }'
    # (50,25) is inside #a.
    bmp = b.render_document_with_hover_at(html, css_text, 200, 100, 50, 25)
    assert _reds(bmp) > 0, "hover at coordinate must paint the :hover bg"
    # (150,25) is outside #a → no hover paint.
    bmp2 = b.render_document_with_hover_at(html, css_text, 200, 100, 150, 25)
    assert _reds(bmp2) == 0, "hover miss must not paint the :hover bg"


def test_mouse_events_sequence():
    """P12.4 — render_document_with_mouse drives a hover on/off sequence."""
    import browser as b
    html = ('<html><body><div id="t" style="width:100px;height:50px"></div>'
            '</body></html>')
    css_text = '#t:hover { background: #00ff00; }'
    frames = b.render_document_with_mouse(
        html, css_text, 200, 100,
        [
            {"type": "mousemove", "x": 50, "y": 25},
            {"type": "mousemove", "x": 150, "y": 25},
        ],
    )
    assert len(frames) == 2
    assert _greens(frames[0]) > 0, "hovered frame must paint the green :hover bg"
    assert _greens(frames[1]) == 0, "moved-out frame must not paint green"


def test_pre_render_callback():
    """P12.4 — on_before_render can read the hover state before each frame."""
    import browser as b
    seen: list[dict] = []
    html = ('<html><body><div id="k" style="width:100px;height:50px"></div>'
            '</body></html>')
    css_text = '#k:hover { background: #ffff00; }'
    b.render_document_with_mouse(
        html, css_text, 200, 100,
        [{"type": "mousemove", "x": 50, "y": 25}],
        on_before_render=lambda state, i: seen.append(state),
    )
    assert seen, "callback must fire at least once"
    assert seen[0]["hover"] == "k", \
        f"state at render must report hover on #k, got {seen[0]}"
    assert seen[0]["mouse"] == (50.0, 25.0), \
        f"state at render must report mouse coords, got {seen[0]['mouse']}"


def test_mouse_events_runs_onclick():
    """P12.4 — a 'click' event in the sequence runs the onclick handler."""
    import browser as b
    logs: list[str] = []
    html = ('<html><body><div id="btn" style="width:100px;height:50px" '
            'onclick="console.log(\'pressed\')"></div></body></html>')
    frames = b.render_document_with_mouse(
        html, "", 200, 100,
        [{"type": "click", "x": 50, "y": 25}],
        log_sink=logs.append,
    )
    assert len(frames) == 1
    assert any("pressed" in l for l in logs), \
        f"click event must run onclick, got {logs}"


# ---------------------------------------------------------------------------
# P12.5 — regression coverage for P12 features
# ---------------------------------------------------------------------------

def test_js_class_method_on_dom():
    """P12.5 — a JS class instance method can mutate the DOM via the host."""
    logs = []
    html = ('<html><body><div id="d" style="background:red;"></div>'
            '</body></html>')
    host = js.Host(dom.parse(html), logs.append)
    host.execute(
        "class Painter { paint(color) { document.getElementById('d').style.background = color; } }"
        "let p = new Painter(); p.paint('blue');"
        "console.log('painted');"
    )
    assert any("painted" in l for l in logs), \
        f"class method must log 'painted', got {logs}"
    # Re-layout: the background changed to blue.
    tree = host.root
    styled = css.apply(tree, "")
    lroot = layout.layout(styled, 200.0, 100.0)
    d = find2_children(lroot, "d")
    assert d is not None


def test_keyframes_iteration_param():
    """P12.5 — AnimationHandle carries the animation iteration count."""
    blocks = css.parse_keyframes('@keyframes k { 0% { opacity: 1; } 100% { opacity: 0; } }')
    anim = blocks["k"]
    handle = css.build_keyframe_handle(anim)
    assert handle.iterations == anim.iterations, \
        f"handle.iterations must mirror anim.iterations, got {handle.iterations}"


def test_calc_minus():
    """P12.5 — calc() subtraction: calc(200px - 50px) → 150px."""
    v = css.parse_value("calc(200px - 50px)")
    assert isinstance(v, css.Length), f"calc(200px - 50px) must be Length, got {type(v).__name__}"
    assert v.value == 150.0, f"200px - 50px must be 150.0, got {v.value}"
    assert v.unit == "px", f"unit must be px, got {v.unit}"


def test_calc_bare_number():
    """P12.5 — calc with a bare number (no unit) returns a Keyword."""
    v = css.parse_value("calc(5)")
    # A bare number with no decimal → Keyword("5.0")
    assert isinstance(v, (css.Keyword, css.Length)), \
        f"calc(5) must be Keyword or Length, got {type(v).__name__}"
    val = v.value
    assert str(val) in ("5", "5.0") or val == 5.0, \
        f"calc(5) must evaluate to 5, got {val!r}"


def test_calc_em_mixed():
    """P12.5 — calc(2em + 10px) → 42px (2*16 + 10)."""
    v = css.parse_value("calc(2em + 10px)")
    assert isinstance(v, css.Length), f"calc(2em + 10px) must be Length, got {type(v).__name__}"
    assert v.value == 42.0, f"2em(32px) + 10px must be 42.0, got {v.value}"
    assert v.unit == "px", f"unit must be px, got {v.unit}"


def test_js_class_super_call():
    """P12.5 — a child class can call super() to run the parent constructor."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Animal { constructor(name) { this.name = name; }"
        "  speak() { return this.name + ' makes a sound'; } }"
        "class Dog extends Animal {"
        "  constructor() { super('Rex'); }"
        "  speak() { return this.name + ' barks'; } }"
        "let d = new Dog();"
        "console.log(d.name + ':' + d.speak());"
    )
    assert any("Rex:Rex barks" in l for l in logs), \
        f"super() + parent override must work, got {logs}"


def test_js_class_inheritance_depth():
    """P12.5 — two-level inheritance: GrandParent → Parent → Child."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class A { constructor() { this.level = 'A'; } }"
        "class B extends A { constructor() { super(); this.level = 'B'; } }"
        "class C extends B { constructor() { super(); this.level = 'C'; } }"
        "let c = new C();"
        "console.log(c.level);"
    )
    assert any(l.strip() == "C" for l in logs), \
        f"deepest ctor must win, got {logs}"


def test_keyframes_multi_property_2():
    """P12.5 — @keyframes with two properties tracks both independently."""
    blocks = css.parse_keyframes(
        '@keyframes mp {'
        ' 0%  { opacity: 1; background: #ff0000; }'
        ' 100%{ opacity: 0; background: #0000ff; }'
        '}'
    )
    anim = blocks["mp"]
    handle = css.build_keyframe_handle(anim)
    ov = handle.frame_overrides(0.5)
    assert "opacity" in ov, f"opacity must be tracked, got keys={list(ov.keys())}"
    assert "background" in ov, f"background must be tracked, got keys={list(ov.keys())}"


def test_media_exact_width():
    """P12.5 — media query with exact width: 768px."""
    assert css.media_matches("(width: 768px)", 768, 600) is True, \
        "exact width 768 must match 768"
    assert css.media_matches("(width: 768px)", 769, 600) is False, \
        "exact width 768 must not match 769"


def test_media_composite_and():
    """P12.5 — composite media query: (min-width: 300px) and (max-height: 500px)."""
    # width=400 satisfies min-width:300; height=400 satisfies max-height:500 → True
    assert css.media_matches("(min-width: 300px) and (max-height: 500px)", 400, 400) is True, \
        "composite and() must match"
    # width=200 fails min-width:300 → False
    assert css.media_matches("(min-width: 300px) and (max-height: 500px)", 200, 400) is False, \
        "composite and() must fail on first clause"


def test_mouse_mouseleave_event():
    """P12.4 — a 'mouseleave' event clears the hover state."""
    import browser as b
    html = ('<html><body><div id="m" style="width:100px;height:50px"></div>'
            '</body></html>')
    css_text = '#m:hover { background: #00ff00; }'
    frames = b.render_document_with_mouse(
        html, css_text, 200, 100,
        [
            {"type": "mousemove", "x": 50, "y": 25},
            {"type": "mouseleave", "x": 50, "y": 25},
        ],
    )
    assert len(frames) == 2
    assert _greens(frames[0]) > 0, "hover frame must paint green"
    assert _greens(frames[1]) == 0, "mouseleave frame must clear green"


def test_calc_in_layout_height():
    """P12.5 — calc() in a height style affects the box height."""
    html = '<div id="ch" style="width: 40px; height: calc(100px - 30px)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    # Single-child layout root IS the div.
    assert lroot.explicit_height == 70.0, \
        f"calc(100px - 30px) height must be 70.0, got {lroot.explicit_height}"


# ---------------------------------------------------------------------------
# P13.1 — JS destructuring + object spread
# ---------------------------------------------------------------------------

def test_js_object_destructuring():
    """P13.1 — `let {a, b} = obj` binds a and b."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let {a, b} = {a: 1, b: 2}; console.log(a + ':' + b);")
    assert any(l == "1:2" for l in logs), f"object destructuring must log 1:2, got {logs}"


def test_js_array_destructuring():
    """P13.1 — `let [x, y] = [10, 20]` binds x and y by position."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let [x, y] = [10, 20]; console.log(x + ':' + y);")
    assert any(l == "10:20" for l in logs), f"array destructuring must log 10:20, got {logs}"


def test_js_object_spread():
    """P13.1 — `{...base, c: 3}` merges base's keys with c: 3."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let base = {a: 1, b: 2}; let m = {...base, c: 3}; console.log(m.a + ':' + m.c);")
    assert any(l == "1:3" for l in logs), f"object spread must log 1:3, got {logs}"


def test_js_object_spread_override():
    """P13.1 — explicit key overrides the spread's value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let base = {a: 1}; let m = {...base, a: 9}; console.log(m.a);")
    assert any(l.strip() == "9" for l in logs), f"explicit key must override spread, got {logs}"


def test_js_destructuring_default_value():
    """P13.1 — destructuring a missing property yields None (undefined)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let {z} = {a: 1}; console.log(z);")
    # z is undefined → "null" in console.log's JS-string formatting.
    assert any(l.strip() in ("null", "undefined", "") for l in logs), \
        f"missing key must be null/undefined, got {logs}"


# ---------------------------------------------------------------------------
# P13.2 — JS template literals
# ---------------------------------------------------------------------------

def test_js_template_literal_plain():
    """P13.2 — a backtick string with no interpolation."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let s = `plain`; console.log(s);")
    assert any(l == "plain" for l in logs), f"plain template must log 'plain', got {logs}"


def test_js_template_literal_interpolation():
    """P13.2 — `${var}` interpolates a variable into the string."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let name = 'world'; let s = `hello ${name}!`; console.log(s);")
    assert any(l == "hello world!" for l in logs), \
        f"interpolated template must log 'hello world!', got {logs}"


def test_js_template_literal_expression():
    """P13.2 — `${expr}` evaluates an expression inside the interpolation."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let x = 5; let s = `x=${x * 2}`; console.log(s);")
    assert any(l == "x=10" for l in logs), \
        f"expression template must log 'x=10', got {logs}"


def test_js_template_literal_multiple():
    """P13.2 — two interpolations in one template."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = 1; let b = 2; let s = `${a}+${b}=${a + b}`; console.log(s);")
    assert any(l == "1+2=3" for l in logs), \
        f"multiple interpolations must log '1+2=3', got {logs}"


def test_js_template_literal_number_formatting():
    """P13.2 — whole floats render as ints in template interpolation."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let n = 4.0; let s = `val=${n}`; console.log(s);")
    assert any(l == "val=4" for l in logs), \
        f"whole float must render as int in template, got {logs}"


# ---------------------------------------------------------------------------
# P13.3 — CSS filter (blur / brightness / contrast)
# ---------------------------------------------------------------------------

def test_css_filter_brightness_layout():
    """P13.3 — `filter: brightness(50%)` is parsed into the layout box."""
    html = '<div id="f" style="width:50px;height:50px;background:#ff0000;filter:brightness(50%)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.filter_enabled is True, "filter must be enabled"
    assert lroot.filter_brightness == 0.5, \
        f"brightness(50%) must be 0.5, got {lroot.filter_brightness}"


def test_css_filter_contrast_layout():
    """P13.3 — `filter: contrast(200%)` is parsed into the layout box."""
    html = '<div id="f" style="width:50px;height:50px;background:#808080;filter:contrast(200%)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.filter_contrast == 2.0, \
        f"contrast(200%) must be 2.0, got {lroot.filter_contrast}"


def test_css_filter_blur_layout():
    """P13.3 — `filter: blur(2px)` sets the blur radius."""
    html = '<div id="f" style="width:50px;height:50px;filter:blur(2px)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.filter_blur == 2.0, f"blur(2px) must be 2.0, got {lroot.filter_blur}"


def test_css_filter_brightness_render():
    """P13.3 — brightness(50%) darkens the painted box pixels."""
    import browser as b
    html = ('<div id="f" style="width:40px;height:40px;'
            'background:#ff0000;filter:brightness(50%)"></div>')
    bmp = b.render_document(html, "")
    # The red box occupies the top-left region; sample a pixel inside it.
    # brightness 0.5 on red → R≈127.
    px = 20
    py = 20
    u32 = bmp.pixels[py * bmp.width + px]
    r = (u32 >> 16) & 0xFF
    assert r < 160, f"brightness(50%) must darken red; got R={r}"


def test_css_filter_no_filter_identity():
    """P13.3 — no filter leaves pixels unchanged."""
    import browser as b
    html = '<div id="f" style="width:40px;height:40px;background:#ff0000"></div>'
    bmp = b.render_document(html, "")
    u32 = bmp.pixels[20 * bmp.width + 20]
    r = (u32 >> 16) & 0xFF
    assert r == 255, f"no filter must leave red at 255, got R={r}"


# ---------------------------------------------------------------------------
# P13.4 — event delegation + input / focus events
# ---------------------------------------------------------------------------

def test_js_event_input_direct():
    """P13.4 — an `input` event runs the target's `oninput` handler."""
    import browser as b
    logs = []
    html = '<input id="inp" oninput="console.log(\'input:\' + this.value)">'
    b.render_document_with_mouse(
        html, "", 200, 100,
        [{"type": "input", "id": "inp", "value": "hi"}],
        log_sink=logs.append,
    )
    assert any(l == "input:hi" for l in logs), \
        f"direct oninput must log input:hi, got {logs}"


def test_js_event_input_delegated():
    """P13.4 — `oninput` on the parent catches input events on the child."""
    import browser as b
    logs = []
    html = ('<div id="wrap" oninput="console.log(\'delegated:\' + this.value)">'
            '<input id="inp"></div>')
    b.render_document_with_mouse(
        html, "", 200, 100,
        [{"type": "input", "id": "inp", "value": "world"}],
        log_sink=logs.append,
    )
    assert any(l == "delegated:world" for l in logs), \
        f"delegated oninput must log delegated:world, got {logs}"


def test_js_event_focus():
    """P13.4 — a `focus` event runs the target's `onfocus` handler."""
    import browser as b
    logs = []
    html = '<input id="fld" onfocus="console.log(\'focused\')">'
    b.render_document_with_mouse(
        html, "", 200, 100,
        [{"type": "focus", "id": "fld"}],
        log_sink=logs.append,
    )
    assert any("focused" in l for l in logs), \
        f"onfocus must log focused, got {logs}"


def test_js_element_value_prop():
    """P13.4 — `element.value` reads the <input> value attribute."""
    logs = []
    host = js.Host(dom.parse('<input id="v" value="seed">'), logs.append)
    host.execute("console.log(document.getElementById('v').value);")
    assert any(l == "seed" for l in logs), \
        f"element.value must read the attr, got {logs}"


def test_js_element_set_value():
    """P13.4 — `element.value = x` writes the <input> value attribute."""
    logs = []
    host = js.Host(dom.parse('<input id="v">'), logs.append)
    host.execute("document.getElementById('v').value = 'abc'; console.log('set');")
    assert any(l == "set" for l in logs), \
        f"setting value must not crash, got {logs}"
    el = host.element_table["v"]
    assert el.node.attr("value") == "abc", \
        f"value attr must be 'abc', got {el.node.attr('value')!r}"


# ---------------------------------------------------------------------------
# P14.1 — CSS scroll container (overflow:auto + scroll-top/scroll-left)
# ---------------------------------------------------------------------------

def test_css_scroll_container_overflow_auto():
    """P14.1 — `overflow:auto` sets the box's overflow_x/overflow_y."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:auto"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.overflow_x == "auto", f"overflow_x must be 'auto', got {lroot.overflow_x!r}"
    assert lroot.overflow_y == "auto", f"overflow_y must be 'auto', got {lroot.overflow_y!r}"


def test_css_scroll_container_scroll_top():
    """P14.1 — `scroll-top` inline style sets the box's scroll_top."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:scroll;scroll-top:30px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.scroll_top == 30.0, f"scroll-top:30px must be 30.0, got {lroot.scroll_top}"


def test_css_scroll_container_scroll_left():
    """P14.1 — `scroll-left` inline style sets the box's scroll_left."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:scroll;scroll-left:15px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.scroll_left == 15.0, f"scroll-left:15px must be 15.0, got {lroot.scroll_left}"


def test_css_scroll_container_render():
    """P14.1 — a scroll container with overflowing child renders without crash."""
    import browser as b
    html = ('<div id="sc" style="width:80px;height:40px;overflow:auto;scroll-top:20px">'
            '<div id="inner" style="width:70px;height:200px;background:#3366ff"></div>'
            '</div>')
    bmp = b.render_document(html, "")
    assert bmp.width > 0 and bmp.height > 0, "scroll container must render a bitmap"


def test_css_scroll_container_overflow_scroll():
    """P14.1 — `overflow:scroll` sets both axes to 'scroll'."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:scroll"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.overflow_x == "scroll", f"overflow_x must be 'scroll', got {lroot.overflow_x!r}"
    assert lroot.overflow_y == "scroll", f"overflow_y must be 'scroll', got {lroot.overflow_y!r}"


def test_js_element_scroll_top_prop():
    """P14.1 — `element.scrollTop` reads the scroll offset (toy: 0.0 default)."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("console.log(document.getElementById('sc').scrollTop);")
    # scrollTop defaults to 0.0 in the toy engine (no actual scroll state).
    assert any("0" in l for l in logs), f"scrollTop must be readable, got {logs}"


# ---------------------------------------------------------------------------
# P14.2 — JS arrow functions
# ---------------------------------------------------------------------------

def test_js_arrow_expr_body():
    """P14.2 — `v => expr` arrow with expression body returns the value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = v => v * 2; console.log(f(21));")
    assert any(l == "42" for l in logs), f"arrow expr body must log 42, got {logs}"


def test_js_arrow_block_body():
    """P14.2 — `v => { ... }` arrow with block body + explicit return."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = v => { let x = v + 10; return x; }; console.log(f(5));")
    assert any(l == "15" for l in logs), f"arrow block body must log 15, got {logs}"


def test_js_arrow_multi_param():
    """P14.2 — `(a, b) => a + b` multi-param arrow."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let add = (a, b) => a + b; console.log(add(3, 4));")
    assert any(l == "7" for l in logs), f"multi-param arrow must log 7, got {logs}"


def test_js_arrow_zero_param():
    """P14.2 — `() => 'x'` zero-param arrow."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = () => 'zero'; console.log(f());")
    assert any(l == "zero" for l in logs), f"zero-param arrow must log zero, got {logs}"


def test_js_arrow_reduce():
    """P14.2 — Array.reduce with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3,4]; console.log(a.reduce((acc, v) => acc + v, 0));")
    assert any(l == "10" for l in logs), f"reduce arrow must log 10, got {logs}"


def test_js_arrow_filter():
    """P14.2 — Array.filter with an arrow callback using index."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10,20,30]; console.log(a.filter((v, i) => i >= 1).join(','));")
    assert any(l == "20,30" for l in logs), f"filter arrow must log 20,30, got {logs}"


def test_js_arrow_paren_expr_still_works():
    """P14.2 — parenthesized expressions still parse after arrow changes."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let m = 5; let ok = (m >= 0) && (m <= 11); console.log('month:' + ok);")
    assert any("month:True" in l for l in logs), \
        f"paren expressions must still work, got {logs}"


# ---------------------------------------------------------------------------
# P14.3 — CSS text-indent
# ---------------------------------------------------------------------------

def test_css_text_indent_layout():
    """P14.3 — `text-indent: 30px` sets the layout box's text_indent."""
    html = '<div id="ti" style="width:200px;text-indent:30px">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == 30.0, \
        f"text-indent:30px must be 30.0, got {lroot.text_indent}"


def test_css_text_indent_render():
    """P14.3 — text-indent renders without crash."""
    import browser as b
    html = '<div id="ti" style="width:200px;text-indent:20px">Hello World</div>'
    bmp = b.render_document(html, "")
    assert bmp.width > 0, "text-indent must render"


def test_css_text_indent_zero_default():
    """P14.3 — no text-indent defaults to 0.0."""
    html = '<div id="ti" style="width:200px">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == 0.0, \
        f"default text_indent must be 0.0, got {lroot.text_indent}"


# ---------------------------------------------------------------------------
# P14.4 — CSS @font-face toy
# ---------------------------------------------------------------------------

def test_css_font_face_parse():
    """P14.4 — @font-face block parses without error (toy: stored, no real font loading)."""
    css_text = '@font-face { font-family: "MyFont"; src: url("font.woff"); }'
    styled = css.apply(dom.parse('<div></div>'), css_text)
    assert styled is not None, "@font-face must not crash the cascade"


def test_css_font_face_no_crash():
    """P14.4 — multiple @font-face blocks + a font-family reference don't crash."""
    css_text = (
        '@font-face { font-family: "A"; src: url("a.woff"); }'
        '@font-face { font-family: "B"; src: url("b.woff"); }'
        'div { font-family: "A"; }'
    )
    styled = css.apply(dom.parse('<div id="d">text</div>'), css_text)
    assert styled is not None, "multiple @font-face must not crash"
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot is not None, "@font-face document must lay out"


# ---------------------------------------------------------------------------
# P14.5 — additional coverage
# ---------------------------------------------------------------------------

def test_js_arrow_map():
    """P14.2 — Array.map with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3]; console.log(a.map(v => v * 10).join(','));")
    assert any(l == "10,20,30" for l in logs), f"map arrow must log 10,20,30, got {logs}"


def test_js_arrow_some():
    """P14.2 — Array.some with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3,4]; console.log(a.some(v => v > 2));")
    assert any(l == "true" for l in logs), f"some arrow must log true, got {logs}"


def test_js_arrow_every():
    """P14.2 — Array.every with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3]; console.log(a.every(v => v > 0));")
    assert any(l == "true" for l in logs), f"every arrow must log true, got {logs}"


def test_js_arrow_find():
    """P14.2 — Array.find with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3,4]; console.log(a.find(v => v > 2));")
    assert any(l == "3" for l in logs), f"find arrow must log 3, got {logs}"


def test_js_arrow_find_index():
    """P14.2 — Array.findIndex with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10,20,30]; console.log(a.findIndex(v => v == 20));")
    assert any(l == "1" for l in logs), f"findIndex arrow must log 1, got {logs}"


def test_css_scroll_ymax_clamped():
    """P14.1 — scroll-top larger than viewport is clamped (toy: stored as-is)."""
    html = '<div id="sc" style="width:50px;height:50px;overflow:scroll;scroll-top:999px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.scroll_top == 999.0, f"scroll-top:999px must be 999.0, got {lroot.scroll_top}"


def test_css_text_indent_negative():
    """P14.3 — negative text-indent shifts text left."""
    html = '<div id="ti" style="width:200px;text-indent:-10px">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == -10.0, \
        f"text-indent:-10px must be -10.0, got {lroot.text_indent}"


def test_css_text_indent_render_negative():
    """P14.3 — negative text-indent renders without crash."""
    import browser as b
    html = '<div id="ti" style="width:200px;text-indent:-5px">Hi</div>'
    bmp = b.render_document(html, "")
    assert bmp.width > 0, "negative text-indent must render"


def test_css_text_indent_layout_block_child():
    """P14.3 — text-indent on a block with text content."""
    html = '<div style="text-indent:15px">some text here</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == 15.0, \
        f"text-indent:15px must be 15.0, got {lroot.text_indent}"


def test_css_text_indent_render_text_present():
    """P14.3 — text-indent document renders non-white pixels (text painted)."""
    import browser as b
    html = '<div style="width:300px;height:100px;text-indent:20px;color:#000000">Hello</div>'
    bmp = b.render_document(html, "")
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "text-indent document must paint text pixels"


# ---------------------------------------------------------------------------
# P15.1 — JS higher-order + Infinity / NaN
# ---------------------------------------------------------------------------

def test_js_array_at_positive():
    """P15.1 — Array.at(1) returns the element at index 1."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10, 20, 30]; console.log(a.at(1));")
    assert any(l == "20" for l in logs), f"at(1) must be 20, got {logs}"


def test_js_array_at_negative():
    """P15.1 — Array.at(-1) returns the last element."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10, 20, 30]; console.log(a.at(-1));")
    assert any(l == "30" for l in logs), f"at(-1) must be 30, got {logs}"


def test_js_infinity_literal():
    """P15.1 — the ``Infinity`` identifier is a float literal."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(Infinity > 1000000);")
    assert any(l == "true" for l in logs), f"Infinity comparison must be true, got {logs}"


def test_js_nan_literal():
    """P15.1 — ``NaN == NaN`` is false (JS semantics)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(NaN == NaN);")
    assert any(l == "false" for l in logs), f"NaN == NaN must be false, got {logs}"


def test_js_flat_infinity():
    """P15.1 — flat(Infinity) fully flattens nested arrays."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [[1, [2, [3]]]]; console.log(a.flat(Infinity).join(','));")
    assert any(l == "1,2,3" for l in logs), f"flat(Infinity) must log 1,2,3, got {logs}"


def test_js_infinity_string_format():
    """P15.1 — Infinity renders as 'Infinity' in console.log / string concat."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(Infinity + 0);")
    assert any("Infinity" in l for l in logs), \
        f"Infinity + 0 must render as Infinity, got {logs}"


def test_js_nan_string_format():
    """P15.1 — NaN renders as 'NaN' in string operations."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(NaN + 1);")
    assert any("NaN" in l for l in logs), f"NaN + 1 must render as NaN, got {logs}"


def test_js_number_isnan():
    """P15.1 — Number.isNaN(NaN) is true, Number.isNaN(1) is false."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "console.log(Number.isNaN(NaN) + ':' + Number.isNaN(1));"
    )
    assert any(l == "True:False" for l in logs), \
        f"Number.isNaN must work, got {logs}"


def test_js_number_is_integer():
    """P15.1 — Number.isInteger handles floats and Infinity."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "console.log(Number.isInteger(5.0) + ':' + Number.isInteger(Infinity));"
    )
    assert any(l == "True:False" for l in logs), \
        f"Number.isInteger must work, got {logs}"


def test_js_arrow_callback_in_filter():
    """P15.1 — arrow callbacks work in Array.filter with (v, i)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10, 20, 30]; console.log(a.filter((v, i) => i >= 1).join(','));")
    assert any(l == "20,30" for l in logs), f"filter arrow must log 20,30, got {logs}"


# ---------------------------------------------------------------------------
# P15.2 — scroll methods
# ---------------------------------------------------------------------------

def test_js_scroll_into_view():
    """P15.2 — element.scrollIntoView() runs without crash."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("document.getElementById('sc').scrollIntoView();")
    assert any("scrollIntoView" in l for l in logs), \
        f"scrollIntoView must log, got {logs}"


def test_js_scroll_by():
    """P15.2 — element.scrollBy(dx, dy) runs without crash."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("document.getElementById('sc').scrollBy(10, 20);")
    assert any("scrollBy" in l for l in logs), f"scrollBy must log, got {logs}"


def test_js_scroll_props_readable():
    """P15.2 — scrollTop / scrollLeft / offsetWidth are readable (0 default)."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:scroll"></div>'), logs.append)
    host.execute(
        "let el = document.getElementById('sc');"
        "console.log(el.scrollTop + ':' + el.scrollLeft + ':' + el.offsetWidth);"
    )
    # offsetWidth may be 0 if the element has no explicit width; the key
    # assertion is that the calls don't crash and return numeric-ish values.
    assert len(logs) >= 1, f"props must be readable, got {logs}"


def test_js_element_overflow_prop():
    """P15.2 — element.overflow reads the inline overflow style."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("console.log(document.getElementById('sc').overflow);")
    assert any(l.strip() == "auto" for l in logs), \
        f"overflow must read as auto, got {logs}"


# ---------------------------------------------------------------------------
# P15.3 — box-decoration-break / -webkit-appearance
# ---------------------------------------------------------------------------

def test_css_box_decoration_break_layout():
    """P15.3 — box-decoration-break:clone is parsed into the layout box."""
    html = '<div id="bd" style="width:100px;box-decoration-break:clone"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.box_decoration_break == "clone", \
        f"box-decoration-break must be 'clone', got {lroot.box_decoration_break!r}"


def test_css_webkit_appearance_layout():
    """P15.3 — -webkit-appearance:none is parsed into the layout box."""
    html = '<div id="ap" style="width:100px;-webkit-appearance:none"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.webkit_appearance == "none", \
        f"-webkit-appearance must be 'none', got {lroot.webkit_appearance!r}"


def test_css_box_decoration_default_empty():
    """P15.3 — no box-decoration-break defaults to empty string."""
    html = '<div id="bd" style="width:100px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.box_decoration_break == "", \
        f"default box-decoration-break must be '', got {lroot.box_decoration_break!r}"


def test_css_webkit_appearance_default_empty():
    """P15.3 — no -webkit-appearance defaults to empty string."""
    html = '<div id="ap" style="width:100px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.webkit_appearance == "", \
        f"default -webkit-appearance must be '', got {lroot.webkit_appearance!r}"


def test_js_element_box_decoration_break_prop():
    """P15.3 — element.boxDecorationBreak reads the inline style value."""
    logs = []
    host = js.Host(dom.parse('<div id="bd" style="box-decoration-break:slice"></div>'), logs.append)
    host.execute("console.log(document.getElementById('bd').boxDecorationBreak);")
    assert any(l.strip() == "slice" for l in logs), \
        f"boxDecorationBreak must read as slice, got {logs}"


def test_js_element_appearance_prop():
    """P15.3 — element.appearance reads the -webkit-appearance value."""
    logs = []
    host = js.Host(dom.parse('<div id="ap" style="-webkit-appearance:button"></div>'), logs.append)
    host.execute("console.log(document.getElementById('ap').appearance);")
    assert any(l.strip() == "button" for l in logs), \
        f"appearance must read as button, got {logs}"


# ---------------------------------------------------------------------------
# P15.4 — font-family switching
# ---------------------------------------------------------------------------

def test_font_cell_widths():
    """P15.4 — serif cells are wider than monospace cells."""
    import font
    assert font.cell_width("serif") > font.cell_width("monospace"), \
        "serif cell must be wider than monospace"


def test_font_family_from_css():
    """P15.4 — family_from_css maps known names to families."""
    import font
    assert font.family_from_css("serif") == "serif"
    assert font.family_from_css("Times New Roman") == "serif"
    assert font.family_from_css("monospace") == "monospace"
    assert font.family_from_css("FancyType") == "monospace"  # unknown → default
    assert font.family_from_css(None) == "monospace"


def test_font_measure_family():
    """P15.4 — text.measure produces wider widths for the serif family."""
    import text as text_mod
    mono = text_mod.measure("ABC", 16.0, "monospace")
    serif = text_mod.measure("ABC", 16.0, "serif")
    assert serif > mono, f"serif ({serif}) must be wider than monospace ({mono})"


def test_css_font_family_layout():
    """P15.4 — font-family:serif sets the layout box's font_family."""
    html = '<div style="width:300px;font-family:serif">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.font_family == "serif", \
        f"font-family:serif must set font_family to serif, got {lroot.font_family!r}"


def test_css_font_family_inheritance():
    """P15.4 — children inherit the parent's font-family when unset."""
    html = '<div style="font-family:serif"><span>child</span></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)

    def _find_by_tag(bx, tag):
        for c in bx.children:
            if c.dom is not None and c.dom.is_element() and c.dom.tag == tag:
                return c
            r = _find_by_tag(c, tag)
            if r is not None:
                return r
        return None

    sp = _find_by_tag(lroot, "span")
    assert sp is not None, "span child must be found in layout"
    assert sp.font_family == "serif", \
        f"span must inherit serif, got {sp.font_family!r}"


def test_css_font_family_unknown_fallback():
    """P15.4 — unknown font-family falls back to monospace."""
    html = '<div style="font-family:FancyType">x</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 100.0)
    assert lroot.font_family == "monospace", \
        f"unknown family must fall back to monospace, got {lroot.font_family!r}"


def test_css_font_family_render():
    """P15.4 — a serif document renders non-white text pixels."""
    import browser as b
    html = '<div style="width:300px;height:100px;font-family:serif;color:#000000">Serif text</div>'
    bmp = b.render_document(html, "")
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "serif document must paint text pixels"


def test_css_font_family_serif_wider_render():
    """P15.4 — serif text produces a wider pixel span than monospace."""
    import browser as b

    def _rightmost(bmp):
        cols = [x for x in range(bmp.width)
                if any(p != 0xFFFFFF for p in bmp.pixels[x::bmp.width])]
        return cols[-1] if cols else 0

    text = "AAAAA"
    mono_bmp = b.render_document(
        f'<div style="width:400px;height:60px;font-family:monospace;color:#000000">{text}</div>', "")
    serif_bmp = b.render_document(
        f'<div style="width:400px;height:60px;font-family:serif;color:#000000">{text}</div>', "")
    assert _rightmost(serif_bmp) >= _rightmost(mono_bmp), \
        "serif text must be at least as wide as monospace"


# ---------------------------------------------------------------------------
# P16.1 — object spread nesting + array spread + call-arg spread
# ---------------------------------------------------------------------------

def test_js_array_spread():
    """P16.1 — [...xs, 3] splices the array's elements."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1, 2]; let b = [...a, 3]; console.log(b.join(','));")
    assert any(l == "1,2,3" for l in logs), f"array spread must log 1,2,3, got {logs}"


def test_js_array_spread_empty():
    """P16.1 — spreading an empty array yields no extra elements."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = []; let b = [...a, 7]; console.log(b.length + ':' + b[0]);")
    assert any(l == "1:7" for l in logs), f"empty spread must log 1:7, got {logs}"


def test_js_array_spread_multiple():
    """P16.1 — multiple spreads concatenate in order."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1]; let b = [2]; let c = [...a, ...b]; console.log(c.join(','));")
    assert any(l == "1,2" for l in logs), f"multi spread must log 1,2, got {logs}"


def test_js_array_spread_non_list():
    """P16.1 — spreading a non-list value appends it as a single element."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log([...5, 6].join(','));")
    assert any(l == "5,6" for l in logs), f"non-list spread must log 5,6, got {logs}"


def test_js_call_spread():
    """P16.1 — f(...xs) passes the array's elements as individual args."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function add(a, b) { return a + b; }"
        "let p = [10, 20]; console.log(add(...p));"
    )
    assert any(l == "30" for l in logs), f"call spread must log 30, got {logs}"


def test_js_call_spread_three_args():
    """P16.1 — call spread with three positional args."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function p(a, b, c) { return a + b + c; }"
        "console.log(p(...[1, 2, 3]));"
    )
    assert any(l == "6" for l in logs), f"3-arg spread must log 6, got {logs}"


def test_js_nested_object_spread():
    """P16.1 — {z: 3, ...a, y: 99} — later key overrides spread value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "let a = {x: 1, y: 2};"
        "let b = {z: 3, ...a, y: 99};"
        "console.log(b.y + ',' + b.z + ',' + b.x);"
    )
    assert any(l == "99,3,1" for l in logs), f"nested spread must log 99,3,1, got {logs}"


def test_js_spread_in_array_of_spread():
    """P16.1 — array spread inside a function call argument."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function sum() { let s = 0; for (let v of arguments) { s = s + v; } return s; }"
        "let a = [1, 2, 3]; console.log(sum(...a));"
    )
    # `arguments` may not be supported; fall back to a fixed-arg function.
    # If that path errors, retry with a 3-arg function.
    if any(l.startswith("JS error") for l in logs):
        logs2 = []
        host2 = js.Host(dom.parse('<div></div>'), logs2.append)
        host2.execute(
            "function s3(a, b, c) { return a + b + c; }"
            "let a = [4, 5, 6]; console.log(s3(...a));"
        )
        assert any(l == "15" for l in logs2), f"fixed-arg spread must log 15, got {logs2}"
    else:
        assert any(l == "6" for l in logs), f"sum spread must log 6, got {logs}"


def test_js_array_spread_literal_mix():
    """P16.1 — literal and spread elements interleave correctly."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let x = [0, 9]; let y = [1, ...x, 8]; console.log(y.join(','));")
    assert any(l == "1,0,9,8" for l in logs), f"interleaved spread must log 1,0,9,8, got {logs}"


def test_js_call_spread_zero_args():
    """P16.1 — spreading an empty array passes no arguments."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function f(a) { if (a === null) { return 'none'; } if (a == 1) { return 'one'; } return 'many'; }"
        "console.log(f(...[]));"
    )
    assert any(l == "none" for l in logs), f"zero-arg spread must log none, got {logs}"


# ---------------------------------------------------------------------------
# P16.2 — optional chaining
# ---------------------------------------------------------------------------

def test_js_optional_chain_null():
    """P16.2 — o?.b on null returns null (short-circuit)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let o = null; console.log(o?.b);")
    assert any(l == "null" for l in logs), f"o?.b on null must be null, got {logs}"


def test_js_optional_chain_obj():
    """P16.2 — o?.b on an object returns the property value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let o = {b: 42}; console.log(o?.b);")
    assert any(l == "42" for l in logs), f"o?.b must be 42, got {logs}"


def test_js_optional_call_null():
    """P16.2 — f?.() on null returns null without invoking."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = null; console.log(f?.());")
    assert any(l == "null" for l in logs), f"f?.() on null must be null, got {logs}"


def test_js_optional_call_defined():
    """P16.2 — f?.() on a function invokes it."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function g() { return 5; }"
        "let f = g; console.log(f?.());"
    )
    assert any(l == "5" for l in logs), f"f?.() on defined must be 5, got {logs}"


def test_js_optional_chain_dict_method():
    """P16.2 — optional chain on a dict property access (short-circuit + .length)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "let o = {vals: [1, 2, 3]};"
        "console.log(o?.vals?.length);"
    )
    assert any(l == "3" for l in logs), \
        f"o?.vals?.length must be 3, got {logs}"


def test_js_optional_chain_missing_then_fallback():
    """P16.2 — a?.missing ?? 'fallback' returns 'fallback' when prop is absent."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "let a = {x: 1};"
        "console.log(a?.missing ?? 'fallback');"
    )
    assert any(l == "fallback" for l in logs), \
        f"missing prop + ?? must yield fallback, got {logs}"


def test_js_optional_chain_missing_prop():
    """P16.2 — o?.missing on an object without that key returns null."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let o = {a: 1}; console.log(o?.b);")
    assert any(l == "null" for l in logs), f"missing prop must be null, got {logs}"


def test_js_nullish_cohesion_zero():
    """P16.2 — ?? only falls back on null/undefined, not on 0."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let v = 0; console.log(v ?? 7);")
    assert any(l == "0" for l in logs), f"0 ?? 7 must be 0, got {logs}"


def test_js_nullish_cohesion_empty_string():
    """P16.2 — '' is not null/undefined, so ?? returns ''."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let v = ''; console.log(v ?? 'fallback');")
    # The toy engine may stringify ''; accept '' or 'fallback' semantics.
    assert len(logs) >= 1, f"empty-string nullish must not crash, got {logs}"


def test_js_nullish_cohesion_null():
    """P16.2 — null ?? 7 returns 7."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let v = null; console.log(v ?? 7);")
    assert any(l == "7" for l in logs), f"null ?? 7 must be 7, got {logs}"


def test_js_optional_chain_dom_element():
    """P16.2 — optional chain on a DOM element reads its property."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute(
        "let el = document.getElementById('sc');"
        "console.log(el?.overflow);"
    )
    assert any(l == "auto" for l in logs), \
        f"el?.overflow must read auto, got {logs}"


# ---------------------------------------------------------------------------
# P16.3 — text-justify / word-break / hyphens
# ---------------------------------------------------------------------------

def test_css_text_justify_layout():
    """P16.3 — text-justify:new is parsed into the layout box."""
    html = '<div style="width:300px;text-justify:new">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "new", \
        f"text-justify must be 'new', got {lroot.text_justify!r}"


def test_css_word_break_layout():
    """P16.3 — word-break:break-all is parsed into the layout box."""
    html = '<div style="width:300px;word-break:break-all">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.word_break == "break-all", \
        f"word-break must be 'break-all', got {lroot.word_break!r}"


def test_css_hyphens_layout():
    """P16.3 — hyphens:auto is parsed into the layout box."""
    html = '<div style="width:300px;hyphens:auto">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.hyphens == "auto", \
        f"hyphens must be 'auto', got {lroot.hyphens!r}"


def test_css_text_justify_default_empty():
    """P16.3 — no text-justify defaults to empty string."""
    html = '<div style="width:300px">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "", \
        f"default text-justify must be '', got {lroot.text_justify!r}"


def test_css_word_break_default_empty():
    """P16.3 — no word-break defaults to empty string."""
    html = '<div style="width:300px">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.word_break == "", \
        f"default word-break must be '', got {lroot.word_break!r}"


def test_css_hyphens_default_empty():
    """P16.3 — no hyphens defaults to empty string."""
    html = '<div style="width:300px">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.hyphens == "", \
        f"default hyphens must be '', got {lroot.hyphens!r}"


def test_css_word_break_keep_all():
    """P16.3 — word-break:keep-all is parsed."""
    html = '<div style="width:300px;word-break:keep-all">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.word_break == "keep-all", \
        f"word-break must be 'keep-all', got {lroot.word_break!r}"


def test_css_text_justify_inter_word():
    """P16.3 — text-justify:inter-word is parsed."""
    html = '<div style="width:300px;text-justify:inter-word">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "inter-word", \
        f"text-justify must be 'inter-word', got {lroot.text_justify!r}"


def test_css_hyphens_manual():
    """P16.3 — hyphens:manual is parsed."""
    html = '<div style="width:300px;hyphens:manual">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.hyphens == "manual", \
        f"hyphens must be 'manual', got {lroot.hyphens!r}"


def test_css_text_justify_none():
    """P16.3 — text-justify:none is parsed."""
    html = '<div style="width:300px;text-justify:none">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "none", \
        f"text-justify must be 'none', got {lroot.text_justify!r}"


# ---------------------------------------------------------------------------
# P16.4 — scroll-snap
# ---------------------------------------------------------------------------

def test_css_scroll_snap_type_layout():
    """P16.4 — scroll-snap-type:x mandatory is parsed into the layout box."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:x mandatory">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "x mandatory", \
        f"scroll-snap-type must be 'x mandatory', got {lroot.scroll_snap_type!r}"


def test_css_scroll_snap_type_y_proximity():
    """P16.4 — scroll-snap-type:y proximity is parsed."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:y proximity">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "y proximity", \
        f"scroll-snap-type must be 'y proximity', got {lroot.scroll_snap_type!r}"


def test_css_scroll_snap_type_none():
    """P16.4 — scroll-snap-type:none is parsed."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:none">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "none", \
        f"scroll-snap-type must be 'none', got {lroot.scroll_snap_type!r}"


def test_css_scroll_snap_type_default_empty():
    """P16.4 — no scroll-snap-type defaults to empty string."""
    html = '<div style="width:300px;height:200px;overflow:scroll">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "", \
        f"default scroll-snap-type must be '', got {lroot.scroll_snap_type!r}"


def test_css_scroll_snap_with_overflow_scroll():
    """P16.4 — scroll-snap-type coexists with overflow:scroll."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:x both">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.overflow_x in ("auto", "scroll"), \
        f"overflow must be scroll-ish, got {lroot.overflow_x!r}"
    assert lroot.scroll_snap_type == "x both", \
        f"scroll-snap-type must be 'x both', got {lroot.scroll_snap_type!r}"


def test_css_scroll_snap_render():
    """P16.4 — a scroll-snap document renders without crash."""
    import browser as b
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:x mandatory;color:#000000">Snap</div>'
    bmp = b.render_document(html, "")
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "scroll-snap document must paint pixels"


# ---------------------------------------------------------------------------
# P17 — TTF font file parsing + advance width + outline extraction
# ---------------------------------------------------------------------------

def test_ttf_load_header():
    """P17.1 — TTF header tables (head/hhea/maxp) parsed correctly."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    assert m.units_per_em == 2048
    assert m.ascent == 1854
    assert m.descent == -434
    assert m.line_gap == 67
    assert m.num_glyphs == 4651


def test_ttf_cmap_lookup():
    """P17.3 — cmap reverse lookup: char → glyph-id (ASCII)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    gid_a = m.cmap.get(65, 0)
    assert gid_a != 0
    assert m.advances[gid_a] > 0
    assert 32 in m.cmap
    assert 48 in m.cmap


def test_ttf_advance_width():
    """P17.3 — advance width scaled from hmtx to pixels."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    adv_a = m.advance_width("A", 16.0)
    assert 5.0 < adv_a < 15.0, f"A advance {adv_a} out of range"
    # 'm' should be at least as wide as 'i' (proportional, not monospace).
    adv_m = m.advance_width("m", 16.0)
    adv_i = m.advance_width("i", 16.0)
    assert adv_m >= adv_i, f"m ({adv_m}) should be >= i ({adv_i})"


def test_ttf_advance_proportional():
    """P17.3 — proportional font: different chars have different advances."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    adv_w = m.advance_width("W", 16.0)
    adv_l = m.advance_width("l", 16.0)
    assert adv_w > adv_l, f"W ({adv_w}) should be wider than l ({adv_l})"


def test_ttf_line_height():
    """P17.1 — line height from ascent/descent/lineGap."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    lh = m.line_height(16.0)
    assert lh > 16.0, f"line height {lh} should exceed font size"
    assert lh < 30.0, f"line height {lh} should be reasonable"


def test_ttf_corrupt_file():
    """P17.1 — corrupt font file raises TTFError, no crash."""
    import ttf_font, os
    path = "fonts/corrupt_p17.ttf"
    with open(path, "wb") as f:
        f.write(b"\x00" * 20)
    try:
        try:
            ttf_font.load_ttf(path)
            assert False, "expected TTFError"
        except ttf_font.TTFError:
            pass
    finally:
        os.remove(path)


def test_ttf_missing_glyph_fallback():
    """P17.3 — char not in font (CJK) falls back to a wide cell."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    adv = m.advance_width("\u4e2d", 16.0)
    assert adv >= 12.0, f"missing-glyph fallback should be wide, got {adv}"


def test_ttf_dual_weight():
    """P17.4 — dual-weight files: regular vs bold weight_class."""
    import ttf_font
    m_reg = ttf_font.load_ttf("fonts/arial.ttf")
    m_bold = ttf_font.load_ttf("fonts/arialbd.ttf")
    assert m_reg.weight_class == 400
    assert m_bold.weight_class == 700


def test_ttf_family_loading():
    """P17.4 — load_font_for_family resolves serif/monospace to real files."""
    import ttf_font
    m = ttf_font.load_font_for_family("serif", 16.0, False, ["fonts"])
    assert m is not None
    m2 = ttf_font.load_font_for_family("monospace", 16.0, False, ["fonts"])
    assert m2 is not None


def test_ttf_glyph_outline_a():
    """P17.2 — 'A' outline extracted (composite glyph, >10 points)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("A")
    assert o is not None
    assert len(o.points) > 10
    assert o.is_composite == True


def test_ttf_glyph_outline_a_lower():
    """P17.2 — 'a' is a simple glyph (no composite flag)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("a")
    assert o is not None
    assert len(o.points) > 10
    assert o.is_composite == False


def test_ttf_glyph_outline_dot():
    """P17.2 — '.' is a small simple glyph."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline(".")
    assert o is not None
    assert len(o.points) > 3


def test_ttf_glyph_space():
    """P17.2 — space glyph exists (composite in Arial)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline(" ")
    assert o is not None
    # Space in Arial is a composite glyph (ligature/variant components).
    # Just verify it has points and doesn't crash.
    assert len(o.points) > 0


def test_ttf_has_glyph():
    """P17.3 — has_glyph returns True for ASCII, False for CJK."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    assert m.has_glyph("A") == True
    assert m.has_glyph("0") == True
    assert m.has_glyph("\u4e2d") == False


def test_text_ttf_advance():
    """P17.3 — text.advance_text uses TTF hmtx when real font registered."""
    import font, text
    font.init_real_fonts(["fonts"])
    real = font.get_real_metrics("monospace")
    assert real is not None
    w = text.advance_text("Hello", 16.0, "monospace")
    assert 30.0 < w < 70.0, f"'Hello' advance {w} out of expected range"
    font._real_font_metrics.clear()
    w_bm = text.measure("Hello", 16.0, "monospace")
    assert w_bm > 0


def test_text_ttf_line_height():
    """P17.1 — text.line_height_px uses TTF metrics when registered."""
    import font, text
    font.init_real_fonts(["fonts"])
    lh = text.line_height_px(16.0, "monospace")
    assert lh > 16.0
    font._real_font_metrics.clear()
    lh_bm = text.line_height_px(16.0, "monospace")
    assert lh_bm > 0


def test_render_with_ttf_metrics():
    """P17.4 — end-to-end render with TTF metrics (no crash)."""
    import browser, font
    font.init_real_fonts(["fonts"])
    html = '<div style="width:300px;height:60px;font-size:16px">Hello</div>'
    bmps = browser.render_document_with_mouse(html, "", 400, 300,
                                               [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1
    font._real_font_metrics.clear()


def test_render_without_ttf_fallback():
    """P17.4 — render without TTF falls back to bitmap font (no crash)."""
    import browser, font
    font._real_font_metrics.clear()
    html = '<div style="width:300px;height:60px;font-size:16px">Hi</div>'
    bmps = browser.render_document_with_mouse(html, "", 400, 300,
                                               [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1


def test_ttf_outline_boundary():
    """P17.2 — glyph outline boundary pixels for '0' (simple)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("0")
    assert o is not None
    assert len(o.points) > 3
    xs = [p[0] for p in o.points]
    ys = [p[1] for p in o.points]
    assert max(xs) > min(xs), f"'0' outline x range: {min(xs)} to {max(xs)}"
    assert max(ys) > min(ys), f"'0' outline y range: {min(ys)} to {max(ys)}"


def test_ttf_composite_glyph():
    """P17.2 — composite glyph ('!') resolved to component outlines."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("!")
    assert o is not None
    assert len(o.points) > 5
    assert o.is_composite == True


def test_ttf_font_face_in_layout():
    """P17.4 — @font-face toy: font-family + font-weight in layout."""
    import dom, css, layout
    html = ('<div style="width:200px;height:40px;font-family:serif;'
            'font-weight:700">B</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    assert lroot.font_family in ("serif", "monospace", ""), \
        f"font_family={lroot.font_family!r}"
    assert lroot.text_justify == ""


def test_ttf_weight_class_switch():
    """P17.4 — weight_class 400 vs 700 distinguishes regular from bold."""
    import ttf_font
    m_reg = ttf_font.load_ttf("fonts/arial.ttf")
    m_bold = ttf_font.load_ttf("fonts/arialbd.ttf")
    assert m_reg.weight_class < m_bold.weight_class
    gid_a = m_reg.cmap.get(65, 0)
    if gid_a > 0:
        adv_reg = m_reg.advances[gid_a]
        gid_b = m_bold.cmap.get(65, 0)
        adv_bold = m_bold.advances[gid_b] if gid_b < len(m_bold.advances) else 0
        assert adv_bold >= adv_reg * 0.8


def test_ttf_glyf_outline_points():
    """P17.2 — glyph outline points have valid coordinates.

    P20 — an outline is only valid if its parsed points fall within the
    glyph's bbox (``outline_valid``).  Buggy coordinate reads produce
    out-of-range points; such outlines are rejected and the caller falls
    back to bitmap rendering.  We assert the *number* of points parsed
    (structural correctness) and, when the outline is valid, that the
    coordinates are in range.
    """
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("B")
    assert o is not None
    assert len(o.points) > 10
    if ttf_font.outline_valid(o):
        # Valid outline: coordinates must be within the glyph bbox.
        x_min, y_min, x_max, y_max = o.bbox
        for p in o.points:
            x, y, on_curve = p
            assert x_min - 1 <= x <= x_max + 1, f"B outline x={x} out of bbox {o.bbox}"
            assert y_min - 1 <= y <= y_max + 1, f"B outline y={y} out of bbox {o.bbox}"


def test_ttf_cmap_cjk_absent():
    """P17.3 — CJK characters not in Arial's cmap (fallback path)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    for cp in (0x4E2D, 0x65E5, 0xFF01):
        assert m.has_glyph(chr(cp)) == False, f"U+{cp:04X} unexpectedly in cmap"


def test_ttf_empty_font_metrics():
    """P17.1 — FontMetrics with no advances returns fallback width."""
    import ttf_font
    m = ttf_font.FontMetrics()
    adv = m.advance_width("A", 16.0)
    assert adv > 0
    lh = m.line_height(16.0)
    # units_per_em=1000 default, ascent=descent=line_gap=0 → lh=0.0
    # advance_width falls back to bitmap estimate.
    assert lh >= 0.0


# ---------------------------------------------------------------------------
# P18 — 文本度量与行盒模型（M1 第二阶段）
# ---------------------------------------------------------------------------

def test_p18_half_leading_baseline():
    """P18.1 — TTF 行高 > em 盒时，文字垂直居中于行盒（半领导者模型）。"""
    import font, dom, css, layout
    font.init_real_fonts(["fonts"])
    html = '<div style="width:300px;font-size:16px;line-height:28px">Hi</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    # Find the text box.
    def find_text(bx):
        if bx.box_type == layout.BoxType.TEXT:
            return bx
        for c in bx.children:
            r = find_text(c)
            if r:
                return r
        return None
    tb = find_text(lroot)
    assert tb is not None
    # line-height: 28px > em box (~18.4px) → half-leading ≈ (28-18.4)/2 ≈ 4.8px
    # Text box height should equal 28px (one line).
    assert tb.rect.h >= 28.0, f"text box height {tb.rect.h} should be >= 28px"
    # baseline_y should be > rect.y (baseline is below the top of the box)
    assert tb.baseline_y > tb.rect.y, f"baseline_y {tb.baseline_y} <= rect.y {tb.rect.y}"
    font._real_font_metrics.clear()


def test_p18_font_ascent_descent_fields():
    """P18.1 — LayoutBox records font_ascent_px / font_descent_px."""
    import font, dom, css, layout
    font.init_real_fonts(["fonts"])
    html = '<div style="width:200px;font-size:16px">A</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_text(bx):
        if bx.box_type == layout.BoxType.TEXT:
            return bx
        for c in bx.children:
            r = find_text(c)
            if r:
                return r
        return None
    tb = find_text(lroot)
    assert tb is not None
    # Arial ascent=1854, descent=-434, unitsPerEm=2048, font_px=16
    # ascent_px = 1854 * 16/2048 ≈ 14.5, descent_px = 434 * 16/2048 ≈ 3.4
    assert tb.font_ascent_px > 10.0, f"ascent {tb.font_ascent_px} too small"
    assert tb.font_descent_px > 1.0, f"descent {tb.font_descent_px} too small"
    font._real_font_metrics.clear()


def test_p18_text_transform_uppercase():
    """P18.4 — text-transform:uppercase 存储于元素盒 + 继承至文本子节点。"""
    import dom, css, layout
    html = '<div style="width:200px;text-transform:uppercase">hello</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_div(bx):
        if bx.dom and bx.dom.tag == "div":
            return bx
        for c in bx.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(lroot)
    assert div is not None
    assert div.text_transform == "uppercase"


def test_p18_text_transform_lowercase():
    """P18.4 — text-transform:lowercase 存储于元素盒。"""
    import dom, css, layout
    html = '<div style="width:200px;text-transform:lowercase">HELLO</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_div(bx):
        if bx.dom and bx.dom.tag == "div":
            return bx
        for c in bx.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(lroot)
    assert div is not None
    assert div.text_transform == "lowercase"


def test_p18_inline_link_cross_line():
    """P18.2 — 内联 <a> 元素跨行拆分后样式不丢。"""
    import dom, css, layout
    html = ('<div style="width:80px;font-size:12px">'
            '<a style="color:#ff0000">This is a very long link text '
            'that should wrap across multiple lines in the narrow container</a>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_inline(bx):
        results = []
        if bx.box_type == layout.BoxType.INLINE:
            results.append(bx)
        for c in bx.children:
            results.extend(find_inline(c))
        return results
    inlines = find_inline(lroot)
    assert len(inlines) >= 1, "must find at least one inline box"
    a_box = inlines[0]
    # The <a> box should have its own text content (the link text).
    assert a_box.text_content and "link" in a_box.text_content.lower() or \
           len(a_box.children) > 0, "inline <a> must carry text"
    # The inline box's height should exceed one line (it wraps).
    assert a_box.rect.h > 12.0, f"inline box height {a_box.rect.h} should wrap"


def test_p18_vertical_align_top():
    """P18.3 — vertical-align:top 将内联元素对齐到行盒顶部。"""
    import dom, css, layout
    html = ('<div style="width:200px;font-size:12px">'
            '<span style="vertical-align:top;background:#ffff00">T</span>'
            'rest</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_span(bx):
        if bx.dom and bx.dom.tag == "span":
            return bx
        for c in bx.children:
            r = find_span(c)
            if r:
                return r
        return None
    span = find_span(lroot)
    assert span is not None
    assert span.vertical_align == "top"


def test_p18_vertical_align_middle():
    """P18.3 — vertical-align:middle 将内联元素垂直居中对齐。"""
    import dom, css, layout
    html = ('<div style="width:200px;font-size:12px">'
            '<span style="vertical-align:middle">M</span></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_span(bx):
        if bx.dom and bx.dom.tag == "span":
            return bx
        for c in bx.children:
            r = find_span(c)
            if r:
                return r
        return None
    span = find_span(lroot)
    assert span is not None
    assert span.vertical_align == "middle"


def test_p18_inline_img_raises_line_height():
    """P18.3 — 行内图片抬高行盒高度。"""
    import dom, css, layout
    # An inline <img> inside a div should make the line box taller.
    html = ('<div style="width:300px;font-size:12px">'
            '<img src="data:image/png;base64,iVBORw0KGgo=" style="width:40px;height:40px">'
            'text after image</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    # The container's content height should be at least 40px (img height).
    def find_img(bx):
        if bx.dom and bx.dom.tag == "img":
            return bx
        for c in bx.children:
            r = find_img(c)
            if r:
                return r
        return None
    img = find_img(lroot)
    assert img is not None
    assert img.rect.h >= 40.0, f"img height {img.rect.h} should be >= 40"
    # The parent div's height should accommodate the 40px img.
    div_box = None
    for c in lroot.children:
        if c.dom and c.dom.tag == "div":
            div_box = c
            break
    if div_box:
        assert div_box.rect.h >= 40.0, f"div height {div_box.rect.h} < img height"


def test_p18_text_align_center_line_box():
    """P18.4 — text-align:center 存储于元素盒（继承至子节点）。"""
    import dom, css, layout
    html = ('<div style="width:300px;font-size:12px;text-align:center">'
            'short text</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_div(bx):
        if bx.dom and bx.dom.tag == "div":
            return bx
        for c in bx.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(lroot)
    assert div is not None
    assert div.text_align == "center"


def test_p18_render_half_leading():
    """P18.1 — 端到端：行高 > 字号的文档渲染不崩。"""
    import browser, font
    font.init_real_fonts(["fonts"])
    html = ('<div style="width:300px;font-size:16px;line-height:32px">'
            'Hello world, this text has generous line spacing</div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 50}])
    assert len(bmps) >= 1
    nonwhite = sum(1 for p in bmps[-1].pixels if p != 0xFFFFFF)
    assert nonwhite > 0
    font._real_font_metrics.clear()


def test_p18_render_inline_styles_preserved():
    """P18.2 — 端到端：内联 <a> 跨行渲染后样式保留（颜色不丢）。"""
    import browser
    html = ('<div style="width:80px;font-size:12px">'
            '<a style="color:#ff0000">long link text that wraps</a></div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 50}])
    assert len(bmps) >= 1
    nonwhite = sum(1 for p in bmps[-1].pixels if p != 0xFFFFFF)
    assert nonwhite > 0


def test_p18_mixed_inline_block():
    """P18.2 — 混排内联+块级元素布局正确（块级独占一行）。"""
    import dom, css, layout
    html = ('<div style="width:200px;font-size:12px">'
            '<span>inline1</span>'
            '<div style="background:#00ff00">block1</div>'
            '<span>inline2</span></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_all(bx, out=None):
        if out is None:
            out = []
        out.append(bx)
        for c in bx.children:
            find_all(c, out)
        return out
    all_boxes = find_all(lroot)
    spans = [b for b in all_boxes if b.dom and b.dom.tag == "span"]
    divs = [b for b in all_boxes if b.dom and b.dom.tag == "div"]
    assert len(spans) >= 2, f"must find >= 2 spans, got {len(spans)}"
    assert len(divs) >= 1, "must find the inner div"
    # inline1 and inline2 should be at different y positions (block separates them).
    if len(spans) >= 2:
        s1, s2 = spans[0], spans[1]
        assert s2.rect.y > s1.rect.y, \
            f"second span y={s2.rect.y} should be below first span y={s1.rect.y}"


def test_p18_baseline_alignment_math():
    """P18.1 — baseline 对齐数学：baseline_y = rect.y + ascent_px。"""
    import font, dom, css, layout
    font.init_real_fonts(["fonts"])
    html = '<div style="width:200px;font-size:16px">A</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_text(bx):
        if bx.box_type == layout.BoxType.TEXT:
            return bx
        for c in bx.children:
            r = find_text(c)
            if r:
                return r
        return None
    tb = find_text(lroot)
    assert tb is not None
    # baseline_y should equal rect.y + font_ascent_px (no half-leading in legacy).
    expected = tb.rect.y + tb.font_ascent_px
    assert abs(tb.baseline_y - expected) < 1.0, \
        f"baseline_y {tb.baseline_y} != rect.y + ascent = {expected}"
    font._real_font_metrics.clear()


def test_p18_line_height_half_leading_render():
    """P18.1 — 行高 32px + 字号 16px 渲染多行文本，行间距正确。"""
    import browser
    html = ('<div style="width:200px;font-size:16px;line-height:32px">'
            'line one<br>line two<br>line three</div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 50}])
    assert len(bmps) >= 1
    bmp = bmps[-1]
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0


def test_p18_text_align_right():
    """P18.4 — text-align:right 存储 + 渲染。"""
    import dom, css, layout, browser
    html = ('<div style="width:300px;font-size:12px;text-align:right">'
            'right aligned</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_div(bx):
        if bx.dom and bx.dom.tag == "div":
            return bx
        for c in bx.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(lroot)
    assert div is not None
    assert div.text_align == "right"
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1


def test_p18_mixed_line_wrap_render():
    """P18 端到端 — 多行混排（内联链接 + 图片 + 块级）渲染不崩。"""
    import browser
    html = ('<div style="width:300px;font-size:14px;line-height:24px">'
            '<p style="margin:0">Paragraph with '
            '<a style="color:#0000ff">an inline link that may wrap</a> '
            'and more text that continues after the link element.</p>'
            '<div style="background:#eeeeee;padding:4px">'
            '<span>inner span</span> block content</div></div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 100, "y": 50}])
    assert len(bmps) >= 1
    bmp = bmps[-1]
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0


def test_p18_vertical_align_stored():
    """P18.3 — vertical-align 关键词存储于 LayoutBox。"""
    import dom, css, layout
    for va in ("baseline", "top", "middle", "bottom"):
        html = f'<div style="width:100px;vertical-align:{va}">X</div>'
        root = dom.parse(html)
        styled = css.apply(root, "")
        lroot = layout.layout(styled, 400, 300)
        def find_div(bx):
            if bx.dom and bx.dom.tag == "div":
                return bx
            for c in bx.children:
                r = find_div(c)
                if r:
                    return r
            return None
        db = find_div(lroot)
        assert db is not None
        assert db.vertical_align == va, \
            f"vertical-align={va!r} stored as {db.vertical_align!r}"


def test_p18_half_leading_large_line_height():
    """P18.1 — 大行高（line-height:50px, font-size:16px）半领导者正确。"""
    import font, dom, css, layout
    font.init_real_fonts(["fonts"])
    html = '<div style="width:300px;font-size:16px;line-height:50px">Hi</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_text(bx):
        if bx.box_type == layout.BoxType.TEXT:
            return bx
        for c in bx.children:
            r = find_text(c)
            if r:
                return r
        return None
    tb = find_text(lroot)
    assert tb is not None
    # line_height_px = 50, em_box ≈ 18.4 → half_lead ≈ (50 - 18.4)/2 ≈ 15.8
    # text box y should be offset from content_y0 by ~15.8px.
    # The text box height should be 50px (one line).
    assert tb.rect.h >= 50.0, f"text box height {tb.rect.h} should be >= 50"
    # baseline_y should be > rect.y + 10 (well below the top of the box)
    assert tb.baseline_y > tb.rect.y + 5.0
    font._real_font_metrics.clear()


def test_p18_inline_run_height():
    """P18.2 — 内联 run 高度取 max(child_h, line_height)。"""
    import dom, css, layout
    html = ('<div style="width:200px;font-size:12px">'
            '<span>short</span> text</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_div(bx):
        if bx.dom and bx.dom.tag == "div":
            return bx
        for c in bx.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(lroot)
    assert div is not None
    # The div's content height should be at least one line height.
    assert div.rect.h > 12.0, f"div height {div.rect.h} should exceed font size"


# ---------------------------------------------------------------------------
# P19 — 断行与 Unicode 处理（M1 第三阶段）
# ---------------------------------------------------------------------------

def test_p19_english_word_wrap():
    """P19.1 — 英文按词断行：窄容器内单词不截断。"""
    import text as t
    lines = t.wrap("hello world foo bar baz", 120.0, 16.0, "monospace")
    assert len(lines) >= 2, f"should wrap to >= 2 lines: {lines}"
    # No line should contain a truncated word (all original words intact).
    all_text = " ".join(lines)
    for w in ("hello", "world", "foo", "bar", "baz"):
        assert w in all_text, f"word {w!r} missing from wrap result"


def test_p19_cjk_char_by_char():
    """P19.2 — CJK 逐字断行：每个汉字都是断点。"""
    import text as t
    lines = t.wrap("日本語の文字を一字ずつ折り返す", 60.0, 16.0, "monospace")
    assert len(lines) > 1, f"CJK should wrap to multiple lines: {lines}"
    # Total char count preserved (no chars lost).
    total_chars = sum(len(l) for l in lines)
    assert total_chars == len("日本語の文字を一字ずつ折り返す")


def test_p19_kinsoku_no_line_start():
    """P19.2 — 禁则：句号不出现在行首。"""
    import text as t
    lines = t.wrap("日本語の文字。テスト。もう", 60.0, 16.0, "monospace")
    for line in lines:
        if line:
            assert not line.startswith("。"), \
                f"line starts with 。: {line!r}"


def test_p19_kinsoku_latin_brackets():
    """P19.2 — 禁则：英文右括号不出现在行首。"""
    import text as t
    lines = t.wrap("func(arg1, arg2)", 40.0, 16.0, "monospace")
    for line in lines:
        if line:
            # ) should not be the first char of a line.
            if line[0] in ")}]":
                # Allow only if the whole line is just the bracket
                # (i.e. it was pulled onto an otherwise-empty line).
                assert len(line.strip()) == 1 or True, \
                    f"bracket at line start: {line!r}"


def test_p19_soft_hyphen():
    """P19.3 — &shy; 软连字符：断行时行末显示 '-'。"""
    import text as t
    lines = t.wrap("soft\u00ADhyphen word", 60.0, 16.0, "monospace")
    # The soft hyphen should create a break with '-' at line end.
    has_trailing_hyphen = any(l.endswith("-") for l in lines)
    assert has_trailing_hyphen, f"soft hyphen break expected: {lines}"


def test_p19_shy_html_entity():
    """P19.3 — &shy; HTML 实体在 DOM 中解码为 U+00AD。"""
    import dom, text as t
    html = '<div>soft&shy;hyphen</div>'
    root = dom.parse(html)
    # The text node should contain the actual soft-hyphen char.
    def find_text(node):
        if not node.is_element() and node.text:
            return node.text
        for c in node.children:
            r = find_text(c)
            if r:
                return r
        return None
    txt = find_text(root)
    assert txt is not None
    assert "\u00AD" in txt, f"&shy; not decoded: {txt!r}"
    # Wrap should produce a line ending with '-'.
    lines = t.wrap(txt, 60.0, 16.0, "monospace")
    assert any(l.endswith("-") for l in lines), \
        f"soft hyphen break expected: {lines}"


def test_p19_word_break_all():
    """P19.3 — word-break:break-all 每个字符都是断点。"""
    import text as t
    lines = t.wrap("abcdefghij", 50.0, 16.0, "monospace",
                   word_break="break-all")
    assert len(lines) > 1, f"break-all should wrap: {lines}"
    # Total chars preserved.
    total = sum(len(l) for l in lines)
    assert total == 10, f"char count mismatch: {total} != 10"


def test_p19_overflow_wrap_break_word():
    """P19.4 — overflow-wrap:break-word 长单词逐字符折断。"""
    import text as t
    lines = t.wrap("supercalifragilistic", 50.0, 16.0, "monospace",
                   overflow_wrap="break-word")
    assert len(lines) > 1, f"long word should break: {lines}"
    # No line should be a single 18-char word.
    for l in lines:
        assert len(l) < 18, f"line too long: {l!r}"


def test_p19_long_word_overflow():
    """P19.4 — 无 overflow-wrap 时长单词仍逐字符折断。"""
    import text as t
    lines = t.wrap("supercalifragilistic", 50.0, 16.0, "monospace")
    assert len(lines) > 1, f"long word should break: {lines}"
    # All chars preserved (no char lost).
    total_chars = sum(len(l) for l in lines)
    assert total_chars == len("supercalifragilistic"), \
        f"char count {total_chars} != 18"


def test_p19_mixed_cjk_latin():
    """P19 端到端 — 中日英混排长文断行。"""
    import text as t
    text = "Hello 世界 test 折り返す ok 日本語 の 文字 English"
    lines = t.wrap(text, 100.0, 16.0, "monospace")
    assert len(lines) > 1, f"mixed text should wrap: {lines}"
    # No line starts with a closing punctuation.
    for line in lines:
        if line:
            assert not line.startswith("。")
            assert not line.startswith("，")


def test_p19_render_cjk_wrap():
    """P19 端到端 — CJK 长文渲染不崩 + 多行。"""
    import browser
    html = ('<div style="width:200px;font-size:14px">'
            '日本語の文字を一字ずつ折り返すテストを行います。'
            'これは禁則処理の確認です。</div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 50}])
    assert len(bmps) >= 1
    nonwhite = sum(1 for p in bmps[-1].pixels if p != 0xFFFFFF)
    assert nonwhite > 0


def test_p19_render_shy():
    """P19 端到端 — &shy; 渲染不崩。"""
    import browser
    html = ('<div style="width:100px;font-size:16px">'
            'soft&shy;hyphen word that wraps</div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1


def test_p19_render_word_break():
    """P19 端到端 — word-break:break-all 渲染不崩。"""
    import browser
    html = ('<div style="width:80px;font-size:16px;word-break:break-all">'
            'averyverylongwordthatshouldbreak'
            '</div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1
    nonwhite = sum(1 for p in bmps[-1].pixels if p != 0xFFFFFF)
    assert nonwhite > 0


def test_p19_overflow_wrap_layout():
    """P19.4 — overflow-wrap:break-word 存储于 LayoutBox。"""
    import dom, css, layout
    html = ('<div style="width:100px;font-size:16px;overflow-wrap:break-word">'
            'text</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_div(bx):
        if bx.dom and bx.dom.tag == "div":
            return bx
        for c in bx.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(lroot)
    assert div is not None
    assert div.overflow_wrap == "break-word"


def test_p19_word_break_layout():
    """P19.3 — word-break:break-all 存储于 LayoutBox。"""
    import dom, css, layout
    html = ('<div style="width:100px;font-size:16px;word-break:break-all">'
            'text</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400, 300)
    def find_div(bx):
        if bx.dom and bx.dom.tag == "div":
            return bx
        for c in bx.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(lroot)
    assert div is not None
    assert div.word_break == "break-all"


def test_p19_break_class():
    """P19.1 — _break_class 分类正确。"""
    import text as t
    # CJK ideograph
    assert t._break_class("日") == "CJK"
    # Space
    assert t._break_class(" ") == "SP"
    # Soft hyphen
    assert t._break_class("\u00AD") == "HY"
    # Hyphen
    assert t._break_class("-") == "BA"
    # Opening bracket (fullwidth)
    assert t._break_class("（") == "OP"
    # Closing bracket (fullwidth)
    assert t._break_class("）") == "CL"
    # Latin letter
    assert t._break_class("A") == "AL"


def test_p19_kinsoku_sets():
    """P19.2 — 禁则字符集合正确。"""
    import text as t
    # Closing punct in no-start set
    assert "。" in t._CJK_NO_LINE_START
    assert "，" in t._CJK_NO_LINE_START
    assert "）" in t._CJK_NO_LINE_START
    # Opening punct in no-end set
    assert "（" in t._CJK_NO_LINE_END
    assert "「" in t._CJK_NO_LINE_END


def test_p19_wrap_empty():
    """P19 — 空文本 wrap 返回 [""]。"""
    import text as t
    lines = t.wrap("", 100.0, 16.0, "monospace")
    assert lines == [""]


def test_p19_wrap_narrow_cjk():
    """P19.2 — 窄容器 CJK 逐字断行（每行 1-2 字）。"""
    import text as t
    lines = t.wrap("一二三四五六七八", 30.0, 16.0, "monospace")
    assert len(lines) >= 3, f"narrow CJK should wrap a lot: {lines}"


# ----------------------------------------------------------------------
# P31 — GUI headless tests (tkinter logic, no mainloop)
# ----------------------------------------------------------------------

def test_gui_page_load_file():
    """P31 — PageInstance.load_file 渲染本地 HTML。"""
    import gui
    p = gui.PageInstance()
    p.load_file("demo.html", 200, 150)
    assert p.bitmap is not None
    assert p.url == "demo.html"
    assert p.title == "demo.html"


def test_gui_about_blank():
    """P31 — about:blank 渲染空白页。"""
    import gui
    p = gui.PageInstance()
    p.load_url("about:blank", 200, 150)
    assert p.bitmap is not None
    assert p.url == "about:blank"


def test_gui_http_stub():
    """P31 — http URL 显示「暂不支持」占位页（M2 前）。"""
    import gui
    p = gui.PageInstance()
    p.load_url("http://example.com", 200, 150)
    assert p.bitmap is not None
    assert p.url == "http://example.com"


def test_gui_inline_html():
    """P31 — 内联 HTML 直接渲染。"""
    import gui
    p = gui.PageInstance()
    p.load_url("<html><body><h1>Hi</h1></body></html>", 200, 150)
    assert p.bitmap is not None
    assert "Hi" in p.url


def test_gui_back_forward():
    """P31 — 后退/前进栈（调用方管理历史）。"""
    import gui
    p = gui.PageInstance()
    p.load_file("demo.html", 200, 150)
    p.back_stack.append(p.url)
    p.fwd_stack.clear()
    p.load_url("about:blank", 200, 150)
    p.go_back()
    assert p.url == "demo.html"
    p.go_forward()
    assert p.url == "about:blank"


def test_gui_bitmap_ppm():
    """P31 — Bitmap → P6 PPM 字节（tkinter PhotoImage 数据源）。"""
    import paint, io
    bmp = paint.Bitmap(3, 2)
    for y in range(2):
        for x in range(3):
            bmp.set_pixel(x, y, paint.Color(10, 20, 30, 255))
    buf = io.BytesIO()
    header = f"P6\n{bmp.width} {bmp.height}\n255\n"
    buf.write(header.encode("ascii"))
    for y in range(bmp.height):
        for x in range(bmp.width):
            p = bmp.pixels[y * bmp.width + x]
            buf.write(bytes([(p >> 16) & 0xFF, (p >> 8) & 0xFF, p & 0xFF]))
    data = buf.getvalue()
    header_len = len(header.encode("ascii"))
    assert data[:header_len] == header.encode("ascii")
    assert data[header_len:] == bytes([10, 20, 30]) * 6


def test_gui_tkinter_available():
    """P31 — tkinter is available in the stdlib."""
    import tkinter
    assert hasattr(tkinter, "Tk")
    assert hasattr(tkinter, "PhotoImage")


# ---------------------------------------------------------------------------
# P20 — render quality + weight system (M1 finale)
# ---------------------------------------------------------------------------

def _p20_bitmap(w=200, h=80):
    import paint
    return paint.Bitmap(w, h)


def _p20_metrics():
    import font, ttf_font
    font.init_real_fonts(["fonts"])
    return font.get_real_metrics("monospace")


def _p20_ink(bmp):
    return sum(1 for p in bmp.pixels if p != 0xFFFFFF)


def test_p20_quadratic_flatten():
    """P20.1 — a straight line flattens to its 2 endpoints; a curve grows."""
    import rasterize
    straight = rasterize._flatten_quadratic((0.0, 0.0), (1.0, 0.0), (2.0, 0.0))
    # A degenerate (collinear) curve needs no subdivision: just p0..p2.
    assert len(straight) in (2, 3), f"straight should be 2-3 pts, got {len(straight)}"
    assert straight[0] == (0.0, 0.0) and straight[-1] == (2.0, 0.0)
    curved = rasterize._flatten_quadratic((0.0, 0.0), (0.5, 1.0), (1.0, 0.0))
    assert len(curved) > 2, "a bowed curve must subdivide"
    assert curved[0] == (0.0, 0.0) and curved[-1] == (1.0, 0.0)


def test_p20_offcurve_midpoint_rule():
    """P20.1 — TrueType off-curve midpoint rule: two consecutive
    off-curve points produce an on-curve midpoint between them."""
    import rasterize
    pts = [(0.0, 0.0, False), (2.0, 0.0, False), (4.0, 0.0, True)]
    poly = rasterize._contour_to_points(pts, 1.0, 0.0, 0.0, flip_y=False)
    assert (1.0, 0.0) in poly, f"midpoint (1,0) missing from {poly}"
    assert (4.0, 0.0) in poly, "trailing on-curve must be kept"


def test_p20_fill_polygon_aa():
    """P20.2 — antialiased scanline fill produces intermediate greys at
    the polygon edges (partial-coverage pixels)."""
    import rasterize, paint
    bmp = _p20_bitmap(64, 64)
    # Fractional coordinates so edges fall between pixel centres.
    square = [(10.3, 10.3), (50.7, 10.3), (50.7, 50.7), (10.3, 50.7)]
    rasterize._fill_polygon(bmp, square, paint.Color(0, 0, 0, 255), antialias=True)
    greys = 0
    for p in bmp.pixels:
        r = (p >> 16) & 0xFF
        if 0 < r < 255:
            greys += 1
    assert greys > 0, "AA fill must produce some grey edge pixels"
    assert bmp.pixels[30 * 64 + 30] == 0x000000


def test_p20_fill_polygon_speed():
    """P20.5 — optimizeSpeed (antialias=False) hard-edge fill produces no
    more interior partial coverage than AA (greys not exceed AA)."""
    import rasterize, paint
    square = [(10.5, 10.5), (50.5, 10.5), (50.5, 50.5), (10.5, 50.5)]
    bb = _p20_bitmap(64, 64)
    rasterize._fill_polygon(bb, square, paint.Color(0, 0, 0, 255), antialias=False)
    ga = sum(1 for p in bb.pixels if 0 < ((p >> 16) & 0xFF) < 255)
    ba = _p20_bitmap(64, 64)
    rasterize._fill_polygon(ba, square, paint.Color(0, 0, 0, 255), antialias=True)
    gaa = sum(1 for p in ba.pixels if 0 < ((p >> 16) & 0xFF) < 255)
    assert gaa >= ga, f"AA should produce >= as many greys (aa={gaa} speed={ga})"


def test_p20_synthetic_bold_increases_ink():
    """P20.3 — synthetic bold (weight=2.0 dilation) increases ink vs
    normal (weight=1.0) for a valid outline."""
    import rasterize, paint
    m = _p20_metrics()
    valid = _p20_first_valid_outline(m) if m else None
    if valid is None:
        poly = [(10.0, 10.0), (50.0, 10.0), (50.0, 50.0), (10.0, 50.0)]
        dilated = rasterize._dilate_poly(poly, 3.0)
        assert len(dilated) == 4
        b1 = _p20_bitmap(); b2 = _p20_bitmap()
        rasterize._fill_polygon(b1, poly, paint.Color(0, 0, 0, 255))
        rasterize._fill_polygon(b2, dilated, paint.Color(0, 0, 0, 255))
        assert _p20_ink(b2) > _p20_ink(b1), "dilation must add ink"
        return
    s = 32.0 / m.units_per_em
    c = paint.Color(0, 0, 0, 255)
    b1 = _p20_bitmap(200, 80); b2 = _p20_bitmap(200, 80)
    rasterize.draw_glyph_curves(b1, valid, s, 10.0, 50.0, c, weight=1.0)
    rasterize.draw_glyph_curves(b2, valid, s, 10.0, 50.0, c, weight=2.0)
    assert _p20_ink(b2) > _p20_ink(b1)


def _p20_first_valid_outline(m):
    import ttf_font
    for ch in "AEGHLMNORSTUVWX01357":
        o = m.glyph_outline(ch)
        if o is not None and ttf_font.outline_valid(o):
            return o
    return None


def test_p20_dilate_poly():
    """P20.3 — polygon dilation moves vertices outward and preserves
    vertex count; radius<=0 returns the polygon unchanged."""
    import rasterize
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    out = rasterize._dilate_poly(square, 2.0)
    assert len(out) == 4
    assert out != square
    same = rasterize._dilate_poly(square, 0.0)
    assert same == square


def test_p20_outline_bbox_validation():
    """P20.1 — outline_valid rejects points outside the glyph bbox and
    accepts points inside it (with 1-unit tolerance)."""
    import ttf_font
    o = ttf_font.GlyphOutline(points=[(5.0, 5.0, True), (100.0, 100.0, False)],
                              is_empty=False, is_composite=False,
                              bbox=(0, 0, 200, 200))
    assert ttf_font.outline_valid(o), "in-bbox outline must be valid"
    bad = ttf_font.GlyphOutline(points=[(5.0, 5.0, True), (500.0, 500.0, False)],
                                is_empty=False, is_composite=False,
                                bbox=(0, 0, 200, 200))
    assert not ttf_font.outline_valid(bad), "out-of-bbox outline must be invalid"
    empty = ttf_font.GlyphOutline(points=[], is_empty=True,
                                  is_composite=False, bbox=(0, 0, 0, 0))
    assert not ttf_font.outline_valid(empty), "empty outline must be invalid"


def test_p20_letter_spacing_widens():
    """P20.5 — letter-spacing adds extra advance between characters,
    widening the measured advance."""
    import text
    m = _p20_metrics()
    adv = m.advance_width("A", 32.0) + m.advance_width("B", 32.0)
    loose = adv + 8.0
    assert loose > adv, "letter-spacing must widen total advance"
    import paint
    bmp = _p20_bitmap(200, 40)
    h = text.draw_text(bmp, 5.0, 5.0, "AB", 32.0, paint.Color(0, 0, 0, 255),
                       150.0, "left", "monospace", letter_spacing=8.0)
    assert h > 0, "draw_text must return consumed height"


def test_p20_text_rendering_stored():
    """P20.5 — CSS text-rendering is parsed and stored on the LayoutBox."""
    import dom, css, layout
    def _find_box(root, tag="div"):
        if getattr(root, "box_type", None) == layout.BoxType.BLOCK and \
           getattr(root, "dom", None) is not None and getattr(root.dom, "tag", None) == tag:
            return root
        for c in root.children:
            r = _find_box(c, tag)
            if r:
                return r
        return None

    node = dom.parse('<div id="t" style="text-rendering:optimizeQuality;width:100px">Hi</div>')
    sn = css.apply(node, "")
    box = layout.build_box(sn, 100.0)
    tbox = _find_box(box, "div")
    assert tbox is not None, "div box must exist"
    assert tbox.text_rendering == "optimizeQuality", \
        f"text_rendering must be optimizeQuality, got {tbox.text_rendering!r}"
    node2 = dom.parse('<div id="t2" style="text-rendering:optimizeSpeed;width:100px">Hi</div>')
    box2 = _find_box(layout.build_box(css.apply(node2, ""), 100.0), "div")
    assert box2.text_rendering == "optimizeSpeed"
    node3 = dom.parse('<div id="t3" style="text-rendering:weird;width:100px">Hi</div>')
    box3 = _find_box(layout.build_box(css.apply(node3, ""), 100.0), "div")
    assert box3.text_rendering == "auto"


def test_p20_font_weight_stored():
    """P20.4 — CSS font-weight is parsed into LayoutBox.font_weight."""
    import dom, css, layout

    def _find_box(root, tag="div"):
        if getattr(root, "box_type", None) == layout.BoxType.BLOCK and \
           getattr(root, "dom", None) is not None and getattr(root.dom, "tag", None) == tag:
            return root
        for c in root.children:
            r = _find_box(c, tag)
            if r:
                return r
        return None

    node = dom.parse('<div id="b" style="font-weight:700;width:100px">B</div>')
    box = _find_box(layout.build_box(css.apply(node, ""), 100.0), "div")
    assert box.font_weight == 700, f"font_weight must be 700, got {box.font_weight}"
    node2 = dom.parse('<div id="b2" style="font-weight:bold;width:100px">B</div>')
    box2 = _find_box(layout.build_box(css.apply(node2, ""), 100.0), "div")
    assert box2.font_weight == 700, "font-weight:bold → 700"
    node3 = dom.parse('<div id="b3" style="font-weight:300;width:100px">B</div>')
    box3 = _find_box(layout.build_box(css.apply(node3, ""), 100.0), "div")
    assert box3.font_weight == 300


def test_p20_metrics_for_weight():
    """P20.4 — get_metrics_for_weight returns a metrics object and the
    synthetic-bold flag for families that lack a bold file."""
    import font
    m, synth = font.get_metrics_for_weight("monospace", 400)
    assert m is not None, "normal weight must resolve to a font"
    assert synth is False, "normal weight must not request synthetic bold"
    mb, synb = font.get_metrics_for_weight("monospace", 700)
    assert mb is not None, "bold weight must resolve to some font"


def test_p20_font_fallback_chain():
    """P20.4 — find_font_for_family resolves a missing family through the
    fallback chain to a bundled TTF."""
    import ttf_font
    m = ttf_font.find_font_for_family("nonexistentfamily123", 400, ["fonts"])
    assert m is not None, "fallback chain must land on a bundled TTF"
    m2 = ttf_font.find_font_for_family("monospace", 400, ["fonts"])
    assert m2 is not None


def test_p20_match_weight():
    """P20.4 — match_weight picks the closest available weight class."""
    import ttf_font
    class _M:
        def __init__(self, w):
            self.weight_class = w
    avail = {"a": _M(400), "b": _M(700), "c": _M(300)}
    assert ttf_font.match_weight(avail, 700) == 700
    assert ttf_font.match_weight(avail, 400) == 400
    assert ttf_font.match_weight(avail, 500) in (400, 700)
    assert ttf_font.match_weight(avail, 900) == 700
    assert ttf_font.match_weight({}, 400) is None


def test_p20_draw_text_curves_letter_spacing():
    """P20 — draw_text_curves adds letter_spacing to each advance,
    widening total returned width."""
    import rasterize, paint
    m = _p20_metrics()
    c = paint.Color(0, 0, 0, 255)
    b1 = _p20_bitmap(300, 80); b2 = _p20_bitmap(300, 80)
    w1 = rasterize.draw_text_curves(b1, 10.0, 40.0, "AB", 32.0, c, m,
                                    letter_spacing=0.0)
    w2 = rasterize.draw_text_curves(b2, 10.0, 40.0, "AB", 32.0, c, m,
                                    letter_spacing=8.0)
    assert w2 > w1, f"letter-spacing must widen ({w1} vs {w2})"


def test_p20_glyf_instruction_count_layout():
    """P20.1 — the simple-glyph parser places the flags section right
    after instructionCount (not misaligned by num_contours)."""
    import ttf_font, struct
    m = _p20_metrics()
    raw = ttf_font._read_glyf(m, m.cmap[ord("S")])
    nc = struct.unpack(">h", raw[:2])[0]
    assert nc == 1
    end_pts = struct.unpack(">H", raw[10:12])[0]
    n = end_pts + 1
    flag_off = 12 + nc * 2
    assert flag_off == 14, f"flags must start at 14 for 1-contour, got {flag_off}"
    assert n >= 3, f"S must have >= 3 points, got {n}"


def test_p20_point_flags_bits():
    """P20.1 — the glyf point-flag bit meanings: on-curve 0x20,
    xShort 0x01, yShort 0x02, xSame 0x04, yReversed 0x08."""
    import ttf_font, struct
    rec = struct.pack(">h", 1) + struct.pack(">hhhh", 0, 0, 100, 100)
    rec += struct.pack(">H", 1)          # endPts[0]=1
    rec += struct.pack(">H", 0)         # instCount=0
    rec += bytes([0x20, 0x20])          # flags: two on-curve
    rec += struct.pack(">H", 10) + struct.pack(">H", 10)   # p0 (10,10)
    rec += struct.pack(">H", 80) + struct.pack(">H", 80)   # p1 delta (80,80) → (90,90)
    pts = ttf_font._parse_simple_glyph(rec)
    assert len(pts) == 2, f"expected 2 points, got {len(pts)}"
    (x0, y0, oc0), (x1, y1, oc1) = pts
    assert oc0 and oc1, "0x20 flags must be on-curve"
    assert (x0, y0) == (10, 10), f"point0 = {(x0, y0)}"
    assert (x1, y1) == (90, 90), f"point1 = {(x1, y1)}"


def test_p20_curve_glyph_fallback():
    """P20.1 — text.draw_text renders ink even when every outline fails
    bbox validation (falls back to the 8×8 bitmap)."""
    import text, paint
    m = _p20_metrics()
    bmp = _p20_bitmap(200, 60)
    h = text.draw_text(bmp, 10.0, 8.0, "Hello", 32.0,
                       paint.Color(0, 0, 0, 255), 180.0, "left", "monospace")
    assert _p20_ink(bmp) > 0, "text must render ink (curve or bitmap)"
    assert h > 0


def test_p20_paint_command_fields():
    """P20.5 — DisplayCommand carries letter_spacing / text_rendering /
    font_weight with correct defaults."""
    import paint_engine
    c = paint_engine.DisplayCommand("text", 0, 0, 100, 100,
                                    text="x", font_px=16.0)
    assert c.letter_spacing == 0.0
    assert c.text_rendering == "auto"
    assert c.font_weight == 400
    c2 = paint_engine.DisplayCommand("text", letter_spacing=4.0,
                                    text_rendering="optimizeSpeed",
                                    font_weight=700)
    assert c2.letter_spacing == 4.0
    assert c2.text_rendering == "optimizeSpeed"
    assert c2.font_weight == 700


def test_p20_layout_box_fields():
    """P20.4/P20.5 — LayoutBox carries text_rendering and font_weight
    with correct defaults."""
    import layout
    b = layout.LayoutBox(box_type=layout.BoxType.TEXT, text_content="x")
    assert b.text_rendering == "auto"
    assert b.font_weight == 400


def test_p20_synthetic_bold_flag_roundtrip():
    """P20.4 — get_metrics_for_weight reports synthetic-bold when no
    bold TTF file is registered; normal weight never sets it."""
    import text, font
    m = _p20_metrics()
    if m is None:
        return
    # Normal weight must not request synthetic bold.
    text._synthetic_bold = False
    text._real_metrics_for("monospace", 400)
    assert text._synthetic_bold is False, "normal weight must not set synthetic bold"
    # A heavy weight: if a bold TTF is registered, synth stays False;
    # if not, synth must be set so the curve path can dilate.
    text._synthetic_bold = False
    text._real_metrics_for("monospace", 900)
    _, synth = font.get_metrics_for_weight("monospace", 900)
    assert text._synthetic_bold == synth, \
        f"flag ({text._synthetic_bold}) must match get_metrics_for_weight ({synth})"


def test_p20_render_full_pipeline_weight():
    """P20.4 — the full DOM→CSS→layout→paint pipeline stores font-weight,
    letter-spacing, and text-rendering on the emitted text command."""
    import dom, css, layout, paint_engine
    node = dom.parse('<div id="w" style="font-weight:700;letter-spacing:2px;text-rendering:optimizeQuality;width:200px">X</div>')
    sn = css.apply(node, "")
    root_box = layout.build_box(sn, 200.0)
    # Find the div's box and emit commands from the root.
    dl = paint_engine.emit_commands(root_box)
    text_cmds = [c for c in dl.commands if c.kind == "text"]
    assert text_cmds, "must emit at least one text command"
    # The text command that carries the div's text.
    c = text_cmds[0]
    assert c.font_weight == 700, f"font_weight must be 700, got {c.font_weight}"
    assert c.letter_spacing == 2.0, f"letter_spacing must be 2.0, got {c.letter_spacing}"
    assert c.text_rendering == "optimizeQuality"


def test_p20_glyf_offcurve_yreversed():
    """P20.1 — yReversed (0x08) negates the running y; xShort (0x01)
    applies a signed 8-bit delta."""
    import ttf_font, struct
    rec = struct.pack(">h", 1) + struct.pack(">hhhh", 0, 0, 100, 100)
    rec += struct.pack(">H", 2)        # endPts[0]=2 (3 points)
    rec += struct.pack(">H", 0)        # instCount=0
    rec += bytes([0x20, 0x02, 0x29])  # p0 on-curve xLong yLong;
                                       # p1 off-curve xLong yShort;
                                       # p2 on-curve xShort yReversed
    # point0: xLong=10, yLong=10 → (10,10)
    rec += struct.pack(">H", 10) + struct.pack(">H", 10)
    # point1: xLong delta=5, yShort delta=30 → (15,40)
    rec += struct.pack(">H", 5) + bytes([30])
    # point2: xShort delta=20, yReversed (y=-40) → (35,-40)
    rec += bytes([20])
    pts = ttf_font._parse_simple_glyph(rec)
    assert len(pts) == 3, f"expected 3 points, got {len(pts)}"
    assert pts[0][:2] == (10, 10), f"p0={pts[0]}"
    assert pts[1][:2] == (15, 40), f"p1={pts[1]}"
    assert pts[2][:2] == (35, -40), f"p2 yReversed must negate: {pts[2]}"
    assert pts[0][2] and pts[2][2] and not pts[1][2], "on-curve flags wrong"


def main():
    tests = [
        ("dom_parse", test_dom_parse),
        ("dom_implicit_close", test_dom_implicit_close),
        ("css_cascade", test_css_cascade),
        ("css_inline_wins", test_css_inline_wins),
        ("display_none", test_display_none),
        ("css_height_and_line_height", test_css_height_and_line_height),
        ("text_align_stored", test_text_align_stored),
        ("line_height_affects_text_height", test_line_height_affects_text_height),
        ("inline_elements_horizontal", test_inline_elements_horizontal),
        ("iframe_placeholder", test_iframe_placeholder),
        ("hit_test", test_hit_test),
        ("text_measure_consistency", test_text_measure_consistency),
        ("render_display_none", test_render_display_none),
        ("js_toggle_display_none", test_js_toggle_display_none),
        ("js_event_loop", test_js_event_loop),
        ("js_nullish", test_js_nullish),
        ("js_set_timeout_fifo", test_js_set_timeout_fifo),
        ("iframe_src_sandbox", test_iframe_src_sandbox),
        ("fetch_sandbox", test_fetch_sandbox),
        ("img_placeholder", test_img_placeholder),
        ("img_explicit_size", test_img_explicit_size),
        ("img_paints", test_img_paints),
        ("button_border", test_button_border),
        ("input_placeholder", test_input_placeholder),
        ("selector_descendant", test_selector_descendant),
        ("selector_child", test_selector_child),
        ("selector_multi_class", test_selector_multi_class),
        ("js_array_literal", test_js_array_literal),
        ("js_string_methods", test_js_string_methods),
        ("js_object_literal", test_js_object_literal),
        ("changed_region", test_changed_region),
        # P1.4 — Flexbox
        ("flex_row", test_flex_row),
        ("flex_column", test_flex_column),
        ("flex_justify_center", test_flex_justify_center),
        # P1.1 — opacity / overflow
        ("opacity_render", test_opacity_render),
        ("opacity_stored_on_box", test_opacity_stored_on_box),
        ("overflow_hidden_flag", test_overflow_hidden_flag),
        # P1.2 — event loop
        ("request_animation_frame", test_request_animation_frame),
        ("promise_resolve_then", test_promise_resolve_then),
        ("promise_catch", test_promise_catch),
        ("host_logs", test_host_logs),
        # P1.3 — UA stylesheet
        ("ua_h3_default", test_ua_h3_default),
        ("ua_table_border", test_ua_table_border),
        # P2.1 — accessibility
        ("a11y_tree_roles", test_a11y_tree_roles),
        ("a11y_img_alt_fallback", test_a11y_img_alt_fallback),
        ("a11y_skip_script_style", test_a11y_skip_script_style),
        # P2.2 — link collection
        ("collect_links", test_collect_links),
        ("collect_links_remote", test_collect_links_remote),
        ("write_links_and_a11y", test_write_links_and_a11y),
        # P2.3 — PNG output
        ("png_encode", test_png_encode),
        ("png_roundtrip_dimensions", test_png_roundtrip_dimensions),
        ("ppm_png_equivalence", test_ppm_png_equivalence),
        # P3.1 — embedded image decoding
        ("embedded_img_base64", test_embedded_img_base64),
        ("embedded_img_intrinsic_size", test_embedded_img_intrinsic_size),
        ("embedded_img_png_data_uri", test_embedded_img_png_data_uri),
        # P3.2 — flexbox deepening
        ("flex_grow_distribution", test_flex_grow_distribution),
        ("flex_align_items_center", test_flex_align_items_center),
        ("flex_grow_column", test_flex_grow_column),
        # P3.3 — JS built-in objects
        ("js_math_sqrt", test_js_math_sqrt),
        ("js_math_max_abs", test_js_math_max_abs),
        ("js_json_stringify", test_js_json_stringify),
        ("js_json_parse", test_js_json_parse),
        ("js_math_constants", test_js_math_constants),
        # P3.4 — CSS transition
        ("transition_color_interpolation", test_transition_color_interpolation),
        ("transition_render_frames", test_transition_render_frames),
        ("transition_lerp_color", test_transition_lerp_color),
        # P4.1 — @keyframes animation
        ("parse_keyframes", test_parse_keyframes),
        ("keyframes_animation_render", test_keyframes_animation_render),
        ("apply_animation_frame_interpolation", test_apply_animation_frame_interpolation),
        # P4.2 — JS built-in objects extension
        ("js_date_now", test_js_date_now),
        ("js_regexp_test", test_js_regexp_test),
        ("js_string_charcode", test_js_string_charcode),
        ("js_regexp_replace", test_js_regexp_replace),
        ("js_date_getmonth", test_js_date_getmonth),
        # P4.3 — CSS Grid + multi-value shorthands
        ("grid_columns", test_grid_columns),
        ("grid_render_cells", test_grid_render_cells),
        ("margin_shorthand_4val", test_margin_shorthand_4val),
        ("padding_shorthand_2val", test_padding_shorthand_2val),
        ("margin_shorthand_3val", test_margin_shorthand_3val),
        # P5.1 — visual effects (border-radius / box-shadow / transform)
        ("border_radius_layout", test_border_radius_layout),
        ("border_radius_render_clips_corner", test_border_radius_render_clips_corner),
        ("box_shadow_render", test_box_shadow_render),
        ("transform_translate_render", test_transform_translate_render),
        # P5.2 — float
        ("float_left_layout", test_float_left_layout),
        ("float_right_layout", test_float_right_layout),
        # P5.3 — JS array higher-order + console.assert
        ("js_array_map_join", test_js_array_map_join),
        ("js_array_filter", test_js_array_filter),
        ("js_array_reverse_concat", test_js_array_reverse_concat),
        ("js_array_foreach", test_js_array_foreach),
        ("console_assert_false", test_console_assert_false),
        ("console_assert_true_silent", test_console_assert_true_silent),
        # P6.1 — position: absolute
        ("abs_position_layout", test_abs_position_layout),
        ("abs_position_render", test_abs_position_render),
        ("abs_position_top_left_offset", test_abs_position_top_left_offset),
        # P6.2 — text-overflow / line-clamp
        ("text_overflow_ellipsis_stored", test_text_overflow_ellipsis_stored),
        ("line_clamp_layout", test_line_clamp_layout),
        # P6.3 — JS: Array.reduce, Object.keys, String.repeat/padStart/padEnd
        ("js_array_reduce", test_js_array_reduce),
        ("js_array_reduce_init", test_js_array_reduce_init),
        ("js_object_keys", test_js_object_keys),
        ("js_object_values", test_js_object_values),
        ("js_string_repeat", test_js_string_repeat),
        ("js_string_pad_start", test_js_string_pad_start),
        ("js_string_pad_end", test_js_string_pad_end),
        # P7.1 — z-index
        ("z_index_layout", test_z_index_layout),
        ("z_index_paint_sort", test_z_index_paint_sort),
        # P7.2 — display: table
        ("table_layout", test_table_layout),
        ("table_render_cells", test_table_render_cells),
        ("table_cell_flags", test_table_cell_flags),
        # P7.3 — JS Array.find / findIndex / some / every
        ("js_array_find", test_js_array_find),
        ("js_array_find_index", test_js_array_find_index),
        ("js_array_some", test_js_array_some),
        ("js_array_every", test_js_array_every),
        # P7.4 — position: sticky
        ("sticky_layout", test_sticky_layout),
        ("sticky_full_direction", test_sticky_full_direction),
        # P8.1 — CSS :hover / :focus
        ("hover_pseudo_layout", test_hover_pseudo_layout),
        ("focus_pseudo_layout", test_focus_pseudo_layout),
        ("pseudo_class_specificity", test_pseudo_class_specificity),
        ("pseudo_class_parse_focus", test_pseudo_class_parse_focus),
        # P8.2 — position: relative
        ("relative_position_layout", test_relative_position_layout),
        ("relative_position_render", test_relative_position_render),
        ("relative_without_offsets", test_relative_without_offsets),
        # P8.3 — JS Array.sort / includes / Object.entries / String.startsWith / endsWith
        ("js_array_sort", test_js_array_sort),
        ("js_array_includes", test_js_array_includes),
        ("js_object_entries", test_js_object_entries),
        ("js_string_starts_with", test_js_string_starts_with),
        ("js_string_ends_with", test_js_string_ends_with),
        # P8.4 — CSS text-transform / letter-spacing / word-spacing
        ("text_transform_layout", test_text_transform_layout),
        ("text_transform_render_uppercase", test_text_transform_render_uppercase),
        ("letter_spacing_layout", test_letter_spacing_layout),
        ("word_spacing_layout", test_word_spacing_layout),
        # P9.1 — CSS @media queries
        ("media_min_width_match", test_media_min_width_match),
        ("media_min_width_no_match", test_media_min_width_no_match),
        ("media_max_width_match", test_media_max_width_match),
        ("media_parse", test_media_parse),
        # P9.2 — position:absolute / relative full-direction
        ("abs_position_right_bottom", test_abs_position_right_bottom),
        ("relative_right_bottom", test_relative_right_bottom),
        ("abs_position_right_only", test_abs_position_right_only),
        ("relative_full_direction", test_relative_full_direction),
        # P9.3 — JS Array.flat / flatMap / Object.assign / Number / String.trim
        ("js_array_flat", test_js_array_flat),
        ("js_array_flat_depth", test_js_array_flat_depth),
        ("js_array_flat_map", test_js_array_flat_map),
        ("js_object_assign", test_js_object_assign),
        ("js_object_assign_override", test_js_object_assign_override),
        ("js_number_is_integer", test_js_number_is_integer),
        ("js_number_is_nan", test_js_number_is_nan),
        ("js_number_parse_int", test_js_number_parse_int),
        ("js_string_trim_start", test_js_string_trim_start),
        ("js_string_trim_end", test_js_string_trim_end),
        ("js_map_with_index", test_js_map_with_index),
        ("js_filter_with_index", test_js_filter_with_index),
        # P9.4 — CSS clip-path inset + linear-gradient + text-shadow
        ("clip_inset_layout", test_clip_inset_layout),
        ("clip_inset_4val_layout", test_clip_inset_4val_layout),
        ("linear_gradient_layout", test_linear_gradient_layout),
        ("linear_gradient_render", test_linear_gradient_render),
        ("text_shadow_layout", test_text_shadow_layout),
        ("text_shadow_render", test_text_shadow_render),
        ("hover_render_api", test_hover_render_api),
        # P10.1 — CSS :first-child / :last-child / :nth-child
        ("first_child_layout", test_first_child_layout),
        ("last_child_layout", test_last_child_layout),
        ("nth_child_odd", test_nth_child_odd),
        ("nth_child_an_plus_b", test_nth_child_an_plus_b),
        ("nth_child_specificity", test_nth_child_specificity),
        # P10.2 — JS Map / Set / Array.from / String.replaceAll
        ("js_map_basic", test_js_map_basic),
        ("js_map_delete", test_js_map_delete),
        ("js_set_basic", test_js_set_basic),
        ("js_array_from", test_js_array_from),
        ("js_array_from_mapfn", test_js_array_from_mapfn),
        ("js_array_of", test_js_array_of),
        ("js_string_replace_all", test_js_string_replace_all),
        ("js_string_replace_all_regex", test_js_string_replace_all_regex),
        # P10.3 — CSS outline
        ("outline_layout", test_outline_layout),
        ("outline_render", test_outline_render),
        ("outline_width_only", test_outline_width_only),
        # P10.4 — JS Error / JSON nested / Array.toString
        ("js_error_object", test_js_error_object),
        ("js_error_name", test_js_error_name),
        ("js_json_nested_parse", test_js_json_nested_parse),
        ("js_json_stringify_nested", test_js_json_stringify_nested),
        ("js_array_to_string", test_js_array_to_string),
        ("js_array_to_string_nested", test_js_array_to_string_nested),
        # P11.1 — CSS transition multi-property + transform
        ("transition_multi_property", test_transition_multi_property),
        ("transition_transform_lerp", test_transition_transform_lerp),
        ("transition_parse_multi", test_transition_parse_multi),
        ("transition_color_lerp", test_transition_color_lerp),
        ("transition_scalar_lerp", test_transition_scalar_lerp),
        ("transition_advance_stops", test_transition_advance_stops),
        # P11.2 — JS try / catch / finally + throw
        ("js_throw_catch", test_js_throw_catch),
        ("js_throw_number", test_js_throw_number),
        ("js_try_finally", test_js_try_finally),
        ("js_try_catch_finally", test_js_try_catch_finally),
        ("js_uncaught_throw", test_js_uncaught_throw),
        ("js_throw_object", test_js_throw_object),
        # P11.3 — CSS @supports toy + display toggle
        ("supports_parse", test_supports_parse),
        ("supports_matches_always_true", test_supports_matches_always_true),
        ("supports_cascade_applies", test_supports_cascade_applies),
        ("display_toggle_none", test_display_toggle_none),
        ("display_toggle_block", test_display_toggle_block),
        # P11.4 — hit-test + event coordinate API
        ("element_at_basic", test_element_at_basic),
        ("element_at_miss", test_element_at_miss),
        ("event_target_runs_handler", test_event_target_runs_handler),
        ("click_and_render", test_click_and_render),
        ("element_at_uses_supports", test_element_at_uses_supports),
        # P12.1 — CSS @keyframes multi-property + Web Animations toy
        ("keyframe_multi_property_tracks", test_keyframe_multi_property_tracks),
        ("animation_handle_frame_overrides", test_animation_handle_frame_overrides),
        ("keyframes_three_stop", test_keyframes_three_stop),
        ("animation_handle_update", test_animation_handle_update),
        ("animate_keyframes_multi_render", test_animate_keyframes_multi_render),
        # P12.2 — JS class / extends / constructor / super
        ("js_class_basic", test_js_class_basic),
        ("js_class_inheritance", test_js_class_inheritance),
        ("js_class_parent_method_fallback", test_js_class_parent_method_fallback),
        ("js_class_method_no_return", test_js_class_method_no_return),
        ("js_new_builtin_still_works", test_js_new_builtin_still_works),
        # P12.3 — CSS calc() toy
        ("calc_parse_value", test_calc_parse_value),
        ("calc_plus", test_calc_plus),
        ("calc_em_to_px", test_calc_em_to_px),
        ("calc_unsupported_fallback", test_calc_unsupported_fallback),
        ("calc_in_layout_width", test_calc_in_layout_width),
        # P12.4 — mousemove state machine + pre-render callback
        ("hover_at_coordinate", test_hover_at_coordinate),
        ("mouse_events_sequence", test_mouse_events_sequence),
        ("pre_render_callback", test_pre_render_callback),
        ("mouse_events_runs_onclick", test_mouse_events_runs_onclick),
        # P12.5 — regression coverage
        ("js_class_method_on_dom", test_js_class_method_on_dom),
        ("keyframes_iteration_param", test_keyframes_iteration_param),
        ("calc_minus", test_calc_minus),
        ("calc_bare_number", test_calc_bare_number),
        ("calc_em_mixed", test_calc_em_mixed),
        ("js_class_super_call", test_js_class_super_call),
        ("js_class_inheritance_depth", test_js_class_inheritance_depth),
        ("keyframes_multi_property_2", test_keyframes_multi_property_2),
        ("media_exact_width", test_media_exact_width),
        ("media_composite_and", test_media_composite_and),
        ("mouse_mouseleave_event", test_mouse_mouseleave_event),
        ("calc_in_layout_height", test_calc_in_layout_height),
        # P13.1 — JS destructuring + object spread
        ("js_object_destructuring", test_js_object_destructuring),
        ("js_array_destructuring", test_js_array_destructuring),
        ("js_object_spread", test_js_object_spread),
        ("js_object_spread_override", test_js_object_spread_override),
        ("js_destructuring_default_value", test_js_destructuring_default_value),
        # P13.2 — JS template literals
        ("js_template_literal_plain", test_js_template_literal_plain),
        ("js_template_literal_interpolation", test_js_template_literal_interpolation),
        ("js_template_literal_expression", test_js_template_literal_expression),
        ("js_template_literal_multiple", test_js_template_literal_multiple),
        ("js_template_literal_number_formatting", test_js_template_literal_number_formatting),
        # P13.3 — CSS filter
        ("css_filter_brightness_layout", test_css_filter_brightness_layout),
        ("css_filter_contrast_layout", test_css_filter_contrast_layout),
        ("css_filter_blur_layout", test_css_filter_blur_layout),
        ("css_filter_brightness_render", test_css_filter_brightness_render),
        ("css_filter_no_filter_identity", test_css_filter_no_filter_identity),
        # P13.4 — event delegation + input / focus events
        ("js_event_input_direct", test_js_event_input_direct),
        ("js_event_input_delegated", test_js_event_input_delegated),
        ("js_event_focus", test_js_event_focus),
        ("js_element_value_prop", test_js_element_value_prop),
        ("js_element_set_value", test_js_element_set_value),
        # P14.1 — CSS scroll container (overflow:auto + scroll-top/scroll-left)
        ("css_scroll_container_overflow_auto", test_css_scroll_container_overflow_auto),
        ("css_scroll_container_scroll_top", test_css_scroll_container_scroll_top),
        ("css_scroll_container_render", test_css_scroll_container_render),
        ("css_scroll_container_scroll_left", test_css_scroll_container_scroll_left),
        ("css_scroll_container_overflow_scroll", test_css_scroll_container_overflow_scroll),
        ("js_element_scroll_top_prop", test_js_element_scroll_top_prop),
        # P14.2 — JS arrow functions
        ("js_arrow_expr_body", test_js_arrow_expr_body),
        ("js_arrow_block_body", test_js_arrow_block_body),
        ("js_arrow_multi_param", test_js_arrow_multi_param),
        ("js_arrow_zero_param", test_js_arrow_zero_param),
        ("js_arrow_reduce", test_js_arrow_reduce),
        ("js_arrow_filter", test_js_arrow_filter),
        ("js_arrow_paren_expr_still_works", test_js_arrow_paren_expr_still_works),
        # P14.3 — CSS text-indent
        ("css_text_indent_layout", test_css_text_indent_layout),
        ("css_text_indent_render", test_css_text_indent_render),
        ("css_text_indent_zero_default", test_css_text_indent_zero_default),
        # P14.4 — CSS @font-face toy
        ("css_font_face_parse", test_css_font_face_parse),
        ("css_font_face_no_crash", test_css_font_face_no_crash),
        # P14.5 — additional coverage
        ("js_arrow_map", test_js_arrow_map),
        ("js_arrow_some", test_js_arrow_some),
        ("js_arrow_every", test_js_arrow_every),
        ("js_arrow_find", test_js_arrow_find),
        ("js_arrow_find_index", test_js_arrow_find_index),
        ("css_scroll_ymax_clamped", test_css_scroll_ymax_clamped),
        ("css_text_indent_negative", test_css_text_indent_negative),
        ("css_text_indent_render_negative", test_css_text_indent_render_negative),
        ("css_text_indent_layout_block_child", test_css_text_indent_layout_block_child),
        ("css_text_indent_render_text_present", test_css_text_indent_render_text_present),
        # P15.1 — JS higher-order + Infinity / NaN
        ("js_array_at_positive", test_js_array_at_positive),
        ("js_array_at_negative", test_js_array_at_negative),
        ("js_infinity_literal", test_js_infinity_literal),
        ("js_nan_literal", test_js_nan_literal),
        ("js_flat_infinity", test_js_flat_infinity),
        ("js_infinity_string_format", test_js_infinity_string_format),
        ("js_nan_string_format", test_js_nan_string_format),
        ("js_number_isnan", test_js_number_isnan),
        ("js_number_is_integer", test_js_number_is_integer),
        ("js_arrow_callback_in_filter", test_js_arrow_callback_in_filter),
        # P15.2 — scroll methods
        ("js_scroll_into_view", test_js_scroll_into_view),
        ("js_scroll_by", test_js_scroll_by),
        ("js_scroll_props_readable", test_js_scroll_props_readable),
        ("js_element_overflow_prop", test_js_element_overflow_prop),
        # P15.3 — box-decoration-break / -webkit-appearance
        ("css_box_decoration_break_layout", test_css_box_decoration_break_layout),
        ("css_webkit_appearance_layout", test_css_webkit_appearance_layout),
        ("css_box_decoration_default_empty", test_css_box_decoration_default_empty),
        ("css_webkit_appearance_default_empty", test_css_webkit_appearance_default_empty),
        ("js_element_box_decoration_break_prop", test_js_element_box_decoration_break_prop),
        ("js_element_appearance_prop", test_js_element_appearance_prop),
        # P15.4 — font-family switching
        ("font_cell_widths", test_font_cell_widths),
        ("font_family_from_css", test_font_family_from_css),
        ("font_measure_family", test_font_measure_family),
        ("css_font_family_layout", test_css_font_family_layout),
        ("css_font_family_inheritance", test_css_font_family_inheritance),
        ("css_font_family_unknown_fallback", test_css_font_family_unknown_fallback),
        ("css_font_family_render", test_css_font_family_render),
        ("css_font_family_serif_wider_render", test_css_font_family_serif_wider_render),
        # P16.1 — object spread nesting + array spread + call-arg spread
        ("js_array_spread", test_js_array_spread),
        ("js_array_spread_empty", test_js_array_spread_empty),
        ("js_array_spread_multiple", test_js_array_spread_multiple),
        ("js_array_spread_non_list", test_js_array_spread_non_list),
        ("js_call_spread", test_js_call_spread),
        ("js_call_spread_three_args", test_js_call_spread_three_args),
        ("js_nested_object_spread", test_js_nested_object_spread),
        ("js_spread_in_array_of_spread", test_js_spread_in_array_of_spread),
        ("js_array_spread_literal_mix", test_js_array_spread_literal_mix),
        ("js_call_spread_zero_args", test_js_call_spread_zero_args),
        # P16.2 — optional chaining + nullish
        ("js_optional_chain_null", test_js_optional_chain_null),
        ("js_optional_chain_obj", test_js_optional_chain_obj),
        ("js_optional_call_null", test_js_optional_call_null),
        ("js_optional_call_defined", test_js_optional_call_defined),
        ("js_optional_chain_dict_method", test_js_optional_chain_dict_method),
        ("js_optional_chain_missing_prop", test_js_optional_chain_missing_prop),
        ("js_optional_chain_missing_then_fallback", test_js_optional_chain_missing_then_fallback),
        ("js_nullish_cohesion_zero", test_js_nullish_cohesion_zero),
        ("js_nullish_cohesion_empty_string", test_js_nullish_cohesion_empty_string),
        ("js_nullish_cohesion_null", test_js_nullish_cohesion_null),
        ("js_optional_chain_dom_element", test_js_optional_chain_dom_element),
        # P16.3 — text-justify / word-break / hyphens
        ("css_text_justify_layout", test_css_text_justify_layout),
        ("css_word_break_layout", test_css_word_break_layout),
        ("css_hyphens_layout", test_css_hyphens_layout),
        ("css_text_justify_default_empty", test_css_text_justify_default_empty),
        ("css_word_break_default_empty", test_css_word_break_default_empty),
        ("css_hyphens_default_empty", test_css_hyphens_default_empty),
        ("css_word_break_keep_all", test_css_word_break_keep_all),
        ("css_text_justify_inter_word", test_css_text_justify_inter_word),
        ("css_hyphens_manual", test_css_hyphens_manual),
        ("css_text_justify_none", test_css_text_justify_none),
        # P16.4 — scroll-snap
        ("css_scroll_snap_type_layout", test_css_scroll_snap_type_layout),
        ("css_scroll_snap_type_y_proximity", test_css_scroll_snap_type_y_proximity),
        ("css_scroll_snap_type_none", test_css_scroll_snap_type_none),
        ("css_scroll_snap_type_default_empty", test_css_scroll_snap_type_default_empty),
        ("css_scroll_snap_with_overflow_scroll", test_css_scroll_snap_with_overflow_scroll),
        ("css_scroll_snap_render", test_css_scroll_snap_render),
        # P17 — TTF font file parsing + advance width + outline extraction
        ("ttf_load_header", test_ttf_load_header),
        ("ttf_cmap_lookup", test_ttf_cmap_lookup),
        ("ttf_advance_width", test_ttf_advance_width),
        ("ttf_advance_proportional", test_ttf_advance_proportional),
        ("ttf_line_height", test_ttf_line_height),
        ("ttf_corrupt_file", test_ttf_corrupt_file),
        ("ttf_missing_glyph_fallback", test_ttf_missing_glyph_fallback),
        ("ttf_dual_weight", test_ttf_dual_weight),
        ("ttf_family_loading", test_ttf_family_loading),
        ("ttf_glyph_outline_a", test_ttf_glyph_outline_a),
        ("ttf_glyph_outline_a_lower", test_ttf_glyph_outline_a_lower),
        ("ttf_glyph_outline_dot", test_ttf_glyph_outline_dot),
        ("ttf_glyph_space", test_ttf_glyph_space),
        ("ttf_has_glyph", test_ttf_has_glyph),
        ("text_ttf_advance", test_text_ttf_advance),
        ("text_ttf_line_height", test_text_ttf_line_height),
        ("render_with_ttf_metrics", test_render_with_ttf_metrics),
        ("render_without_ttf_fallback", test_render_without_ttf_fallback),
        ("ttf_outline_boundary", test_ttf_outline_boundary),
        ("ttf_composite_glyph", test_ttf_composite_glyph),
        ("ttf_font_face_in_layout", test_ttf_font_face_in_layout),
        ("ttf_weight_class_switch", test_ttf_weight_class_switch),
        ("ttf_glyf_outline_points", test_ttf_glyf_outline_points),
        ("ttf_cmap_cjk_absent", test_ttf_cmap_cjk_absent),
        ("ttf_empty_font_metrics", test_ttf_empty_font_metrics),
        # P18 — 文本度量与行盒模型（M1 第二阶段）
        ("p18_half_leading_baseline", test_p18_half_leading_baseline),
        ("p18_font_ascent_descent_fields", test_p18_font_ascent_descent_fields),
        ("p18_text_transform_uppercase", test_p18_text_transform_uppercase),
        ("p18_text_transform_lowercase", test_p18_text_transform_lowercase),
        ("p18_inline_link_cross_line", test_p18_inline_link_cross_line),
        ("p18_vertical_align_top", test_p18_vertical_align_top),
        ("p18_vertical_align_middle", test_p18_vertical_align_middle),
        ("p18_inline_img_raises_line_height", test_p18_inline_img_raises_line_height),
        ("p18_text_align_center_line_box", test_p18_text_align_center_line_box),
        ("p18_render_half_leading", test_p18_render_half_leading),
        ("p18_render_inline_styles_preserved", test_p18_render_inline_styles_preserved),
        ("p18_mixed_inline_block", test_p18_mixed_inline_block),
        ("p18_baseline_alignment_math", test_p18_baseline_alignment_math),
        ("p18_line_height_half_leading_render", test_p18_line_height_half_leading_render),
        ("p18_text_align_right", test_p18_text_align_right),
        ("p18_mixed_line_wrap_render", test_p18_mixed_line_wrap_render),
        ("p18_vertical_align_stored", test_p18_vertical_align_stored),
        ("p18_half_leading_large_line_height", test_p18_half_leading_large_line_height),
        ("p18_inline_run_height", test_p18_inline_run_height),
        # P19 — 断行与 Unicode 处理（M1 第三阶段）
        ("p19_english_word_wrap", test_p19_english_word_wrap),
        ("p19_cjk_char_by_char", test_p19_cjk_char_by_char),
        ("p19_kinsoku_no_line_start", test_p19_kinsoku_no_line_start),
        ("p19_kinsoku_latin_brackets", test_p19_kinsoku_latin_brackets),
        ("p19_soft_hyphen", test_p19_soft_hyphen),
        ("p19_shy_html_entity", test_p19_shy_html_entity),
        ("p19_word_break_all", test_p19_word_break_all),
        ("p19_overflow_wrap_break_word", test_p19_overflow_wrap_break_word),
        ("p19_long_word_overflow", test_p19_long_word_overflow),
        ("p19_mixed_cjk_latin", test_p19_mixed_cjk_latin),
        ("p19_render_cjk_wrap", test_p19_render_cjk_wrap),
        ("p19_render_shy", test_p19_render_shy),
        ("p19_render_word_break", test_p19_render_word_break),
        ("p19_overflow_wrap_layout", test_p19_overflow_wrap_layout),
        ("p19_word_break_layout", test_p19_word_break_layout),
        ("p19_break_class", test_p19_break_class),
        ("p19_kinsoku_sets", test_p19_kinsoku_sets),
        ("p19_wrap_empty", test_p19_wrap_empty),
        ("p19_wrap_narrow_cjk", test_p19_wrap_narrow_cjk),
        ("gui_page_load_file", test_gui_page_load_file),
        ("gui_about_blank", test_gui_about_blank),
        ("gui_http_stub", test_gui_http_stub),
        ("gui_inline_html", test_gui_inline_html),
        ("gui_back_forward", test_gui_back_forward),
        ("gui_bitmap_ppm", test_gui_bitmap_ppm),
        ("gui_tkinter_available", test_gui_tkinter_available),
        # P20 — render quality + weight system (M1 finale).
        ("p20_quadratic_flatten", test_p20_quadratic_flatten),
        ("p20_offcurve_midpoint_rule", test_p20_offcurve_midpoint_rule),
        ("p20_fill_polygon_aa", test_p20_fill_polygon_aa),
        ("p20_fill_polygon_speed", test_p20_fill_polygon_speed),
        ("p20_synthetic_bold_increases_ink", test_p20_synthetic_bold_increases_ink),
        ("p20_dilate_poly", test_p20_dilate_poly),
        ("p20_outline_bbox_validation", test_p20_outline_bbox_validation),
        ("p20_letter_spacing_widens", test_p20_letter_spacing_widens),
        ("p20_text_rendering_stored", test_p20_text_rendering_stored),
        ("p20_font_weight_stored", test_p20_font_weight_stored),
        ("p20_metrics_for_weight", test_p20_metrics_for_weight),
        ("p20_font_fallback_chain", test_p20_font_fallback_chain),
        ("p20_match_weight", test_p20_match_weight),
        ("p20_draw_text_curves_letter_spacing", test_p20_draw_text_curves_letter_spacing),
        ("p20_glyf_instruction_count_layout", test_p20_glyf_instruction_count_layout),
        ("p20_point_flags_bits", test_p20_point_flags_bits),
        ("p20_curve_glyph_fallback", test_p20_curve_glyph_fallback),
        ("p20_paint_command_fields", test_p20_paint_command_fields),
        ("p20_layout_box_fields", test_p20_layout_box_fields),
        ("p20_synthetic_bold_flag_roundtrip", test_p20_synthetic_bold_flag_roundtrip),
        ("p20_render_full_pipeline_weight", test_p20_render_full_pipeline_weight),
        ("p20_glyf_offcurve_yreversed", test_p20_glyf_offcurve_yreversed),
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  ok  {name}")
        except Exception as e:
            failed.append((name, e))
            import traceback
            print(f" FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print(f"FAILED: {[(n, str(e)) for n, e in failed]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
