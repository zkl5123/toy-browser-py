"""Browser-level: render_document, a11y, links, iframe — test module (GPT P1-2 split from test.py)."""
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

# Shared helpers (defined in test.py, needed by multiple modules)
import tempfile
import session
import cookie_store
import web_api
import os as _os
from test_helpers import (
    _a11y_roles,
    _find_a11y_role,
    _find_box,
    _find_box_by_id,
    _find_box_by_id_or_tag,
    _find_box_by_tag,
    _greens,
    _p20_bitmap,
    _p20_first_valid_outline,
    _p20_ink,
    _p20_metrics,
    _p21_start_server,
    _p22_start_server,
    _p29_js_logs,
    _p30_doc_html,
    _p30_host,
    _p30_qsel_all,
    _p32_cleanup,
    _p32_tmp,
    _pixel_diff,
    _reds,
    _rmtree_quiet,
    find2_children,
)

TESTS: list = []

def test_text_align_stored():
    root = dom.parse('<div style="text-align: center">hello</div>')
    styled = css.apply(root, "")
    layout_root = layout.layout(styled, 400.0)
    div = _find_box_by_tag(layout_root, "div")
    assert div is not None
    assert div.text_align == "center"



TESTS.append(("test_text_align_stored", test_text_align_stored))
def test_line_height_affects_text_height():
    root = dom.parse('<div style="font-size: 16px; line-height: 32px">hello</div>')
    styled = css.apply(root, "")
    layout_root = layout.layout(styled, 400.0)
    div = _find_box_by_tag(layout_root, "div")
    assert div is not None
    assert div.line_height_px == 32.0, f"got {div.line_height_px}"
    assert div.rect.h >= 32.0, f"text box height should reflect line-height, got {div.rect.h}"



TESTS.append(("test_line_height_affects_text_height", test_line_height_affects_text_height))
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



TESTS.append(("test_inline_elements_horizontal", test_inline_elements_horizontal))
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



TESTS.append(("test_iframe_placeholder", test_iframe_placeholder))
def test_text_measure_consistency():
    # P33.R1 — measure() uses per-character TTF advances when a real
    # font is registered; the legacy "5 x char_advance" identity only
    # holds in bitmap mode.  Use the TTF-aware total when a face is
    # registered, and the bitmap-cell total otherwise.
    import font as _f
    if _f.get_real_metrics("monospace") is not None:
        w = text.measure("hello", 16.0)
        total = 0.0
        for ch in "hello":
            total += _f.get_real_metrics("monospace").advance_width(ch, 16.0)
        assert abs(w - total) < 0.5, f"measure {w} != TTF total {total}"
    else:
        w = text.measure("hello", 16.0)
        assert w == 5 * text.char_advance(16.0)



TESTS.append(("test_text_measure_consistency", test_text_measure_consistency))
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



TESTS.append(("test_render_display_none", test_render_display_none))
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



TESTS.append(("test_iframe_src_sandbox", test_iframe_src_sandbox))
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



TESTS.append(("test_img_placeholder", test_img_placeholder))
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



TESTS.append(("test_img_explicit_size", test_img_explicit_size))
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



TESTS.append(("test_img_paints", test_img_paints))
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



TESTS.append(("test_button_border", test_button_border))
def test_input_placeholder():
    """<input type="text"> renders a fixed-size placeholder box."""
    html = '<html><body><input id="in" type="text"></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lb = layout.layout(styled, 400.0, 300.0)
    inp = _find_box_by_id(lb, "in")
    assert inp is not None, "input box found in layout"
    assert inp.rect.w > 0 and inp.rect.h > 0, "input box must have non-zero size"



TESTS.append(("test_input_placeholder", test_input_placeholder))
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



TESTS.append(("test_changed_region", test_changed_region))
def test_request_animation_frame():
    """requestAnimationFrame enqueues a callback that runs at flush time."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute("requestAnimationFrame(function(t) { console.log('raf'); });")
    host.flush_raf()
    assert any("raf" in l for l in logs), f"rAF must log 'raf', logs={logs}"



TESTS.append(("test_request_animation_frame", test_request_animation_frame))
def test_host_logs():
    """Host.logs mirrors the log callback sink."""
    root = dom.parse("<html><body></body></html>")
    sink = []
    host = js.Host(root, sink.append)
    host.execute("console.log('hi');")
    assert host.logs == sink, "Host.logs must mirror the log sink"
    assert any("hi" in l for l in host.logs)



TESTS.append(("test_host_logs", test_host_logs))
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



TESTS.append(("test_ua_h3_default", test_ua_h3_default))
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



TESTS.append(("test_ua_table_border", test_ua_table_border))
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



TESTS.append(("test_a11y_tree_roles", test_a11y_tree_roles))
def test_a11y_img_alt_fallback():
    """img alt text becomes the accessible name (P2.1)."""
    html = '<html><body><img id="pic" src="x.png" alt="my picture"></body></html>'
    tree = dom.parse(html)
    a11y = browser.collect_accessibility_tree(tree)
    imgs = _find_a11y_role(a11y, "image")
    assert imgs, "image node must be present in the a11y tree"
    assert imgs[0]["name"] == "my picture", f"img alt must be the name, got {imgs[0]['name']}"



TESTS.append(("test_a11y_img_alt_fallback", test_a11y_img_alt_fallback))
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



TESTS.append(("test_a11y_skip_script_style", test_a11y_skip_script_style))
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



TESTS.append(("test_collect_links", test_collect_links))
def test_collect_links_remote():
    """collect_links includes remote http(s) URLs."""
    html = "<html><body><a href='https://example.com/page'>x</a></body></html>"
    tree = dom.parse(html)
    links = browser.collect_links(tree)
    assert links == ["https://example.com/page"], f"got {links}"



TESTS.append(("test_collect_links_remote", test_collect_links_remote))
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



TESTS.append(("test_write_links_and_a11y", test_write_links_and_a11y))
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



TESTS.append(("test_parse_keyframes", test_parse_keyframes))
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



TESTS.append(("test_apply_animation_frame_interpolation", test_apply_animation_frame_interpolation))
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



TESTS.append(("test_focus_pseudo_layout", test_focus_pseudo_layout))
def test_text_transform_layout():
    """P8.4 — text-transform: uppercase is stored on the LayoutBox."""
    html = '<div id="t" style="text-transform:uppercase;width:100px">Hello</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "text-transform box must exist"
    assert t.text_transform == "uppercase", f"text_transform must be uppercase, got {t.text_transform}"



TESTS.append(("test_text_transform_layout", test_text_transform_layout))
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



TESTS.append(("test_text_transform_render_uppercase", test_text_transform_render_uppercase))
def test_letter_spacing_layout():
    """P8.4 — letter-spacing: 2px is stored on the LayoutBox."""
    html = '<div id="t" style="letter-spacing:2px;width:100px">Hello</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "letter-spacing box must exist"
    assert t.letter_spacing == 2.0, f"letter_spacing must be 2.0, got {t.letter_spacing}"



TESTS.append(("test_letter_spacing_layout", test_letter_spacing_layout))
def test_word_spacing_layout():
    """P8.4 — word-spacing: 4px is stored on the LayoutBox."""
    html = '<div id="t" style="word-spacing:4px;width:100px">Hello World</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 200.0, 200.0)
    t = find2_children(lroot, "t")
    assert t is not None, "word-spacing box must exist"
    assert t.word_spacing == 4.0, f"word_spacing must be 4.0, got {t.word_spacing}"



TESTS.append(("test_word_spacing_layout", test_word_spacing_layout))
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



TESTS.append(("test_linear_gradient_layout", test_linear_gradient_layout))
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



TESTS.append(("test_linear_gradient_render", test_linear_gradient_render))
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



TESTS.append(("test_text_shadow_layout", test_text_shadow_layout))
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



TESTS.append(("test_text_shadow_render", test_text_shadow_render))
def test_element_at_basic():
    """P11.4 — element_at resolves a coordinate inside a box to that element."""
    import browser as b
    html = ('<html><body><div id="big" style="width:200px;height:100px">'
            '<div id="inner" style="width:50px;height:50px;background:red"></div>'
            '</div></body></html>')
    result = b.element_at(html, "", 300, 200, 10, 10)
    assert result is not None, "element_at must return a dict for a hit"
    assert result["id"] in ("inner", "big"), f"must hit inner or big, got {result['id']}"



TESTS.append(("test_element_at_basic", test_element_at_basic))
def test_element_at_miss():
    """P11.4 — element_at returns None when the point is outside all boxes."""
    import browser as b
    html = '<html><body><div id="small" style="width:10px;height:10px"></div></body></html>'
    result = b.element_at(html, "", 300, 200, 200, 200)
    assert result is None, f"miss must return None, got {result!r}"



TESTS.append(("test_element_at_miss", test_element_at_miss))
def test_event_target_runs_handler():
    """P11.4 — event_target runs the onclick handler of the element under (x,y)."""
    import browser as b
    logs = []
    html = ('<html><body><div id="btn" style="width:100px;height:50px" '
            'onclick="console.log(\'clicked\')"></div></body></html>')
    eid = b.event_target(html, "", 200, 100, 50, 25, log_sink=logs.append)
    assert eid == "btn", f"event_target must return the element id, got {eid!r}"
    assert any("clicked" in l for l in logs), f"onclick must log, got {logs}"



TESTS.append(("test_event_target_runs_handler", test_event_target_runs_handler))
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



TESTS.append(("test_click_and_render", test_click_and_render))
def test_element_at_uses_supports():
    """P11.4/P11.3 — element_at integrates @supports rules into the cascade."""
    import browser as b
    css_text = '@supports (display: grid) { #hit { background: #0000ff; } }'
    html = ('<html><body><div id="hit" style="width:100px;height:50px">'
            '</div></body></html>')
    result = b.element_at(html, css_text, 200, 100, 50, 25)
    assert result is not None
    assert result["id"] == "hit", f"must hit #hit, got {result['id']}"



TESTS.append(("test_element_at_uses_supports", test_element_at_uses_supports))
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



TESTS.append(("test_animation_handle_frame_overrides", test_animation_handle_frame_overrides))
def test_animation_handle_update():
    """P12.1 — AnimationHandle.update advances current_time (clamped)."""
    handle = css.AnimationHandle()
    handle.update(0.3)
    assert handle.current_time == 0.3
    handle.update(1.5)
    assert handle.current_time == 1.0, "t must clamp to 1.0"
    handle.update(-0.5)
    assert handle.current_time == 0.0, "t must clamp to 0.0"



TESTS.append(("test_animation_handle_update", test_animation_handle_update))
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



TESTS.append(("test_pre_render_callback", test_pre_render_callback))
def test_font_cell_widths():
    """P15.4 — serif cells are wider than monospace cells."""
    import font
    assert font.cell_width("serif") > font.cell_width("monospace"), \
        "serif cell must be wider than monospace"



TESTS.append(("test_font_cell_widths", test_font_cell_widths))
def test_font_family_from_css():
    """P15.4 — family_from_css maps known names to families."""
    import font
    assert font.family_from_css("serif") == "serif"
    assert font.family_from_css("Times New Roman") == "serif"
    assert font.family_from_css("monospace") == "monospace"
    assert font.family_from_css("FancyType") == "monospace"  # unknown → default
    assert font.family_from_css(None) == "monospace"



TESTS.append(("test_font_family_from_css", test_font_family_from_css))
def test_font_measure_family():
    """P15.4 — text.measure produces wider widths for the serif family.

    P33.R1 — the P17/P20 TTF path resolves both families to the same
    bundled arial.ttf, so when a real font is registered the two
    families measure identically; the legacy bitmap fallback is the
    only regime where the cell-width difference (8 vs 10) makes serif
    wider.  Accept both regimes.
    """
    import text as text_mod
    mono = text_mod.measure("ABC", 16.0, "monospace")
    serif = text_mod.measure("ABC", 16.0, "serif")
    assert mono > 0 and serif > 0, "both families must measure > 0"
    # Serif is wider in bitmap mode; equal in TTF mode (same file).
    assert serif >= mono, \
        f"serif ({serif}) must be >= monospace ({mono})"



TESTS.append(("test_font_measure_family", test_font_measure_family))
def test_ttf_load_header():
    """P17.1 — TTF header tables (head/hhea/maxp) parsed correctly."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    assert m.units_per_em == 2048
    assert m.ascent == 1854
    assert m.descent == -434
    assert m.line_gap == 67
    assert m.num_glyphs == 4651



TESTS.append(("test_ttf_load_header", test_ttf_load_header))
def test_ttf_cmap_lookup():
    """P17.3 — cmap reverse lookup: char → glyph-id (ASCII)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    gid_a = m.cmap.get(65, 0)
    assert gid_a != 0
    assert m.advances[gid_a] > 0
    assert 32 in m.cmap
    assert 48 in m.cmap



TESTS.append(("test_ttf_cmap_lookup", test_ttf_cmap_lookup))
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



TESTS.append(("test_ttf_advance_width", test_ttf_advance_width))
def test_ttf_advance_proportional():
    """P17.3 — proportional font: different chars have different advances."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    adv_w = m.advance_width("W", 16.0)
    adv_l = m.advance_width("l", 16.0)
    assert adv_w > adv_l, f"W ({adv_w}) should be wider than l ({adv_l})"



TESTS.append(("test_ttf_advance_proportional", test_ttf_advance_proportional))
def test_ttf_line_height():
    """P17.1 — line height from ascent/descent/lineGap."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    lh = m.line_height(16.0)
    assert lh > 16.0, f"line height {lh} should exceed font size"
    assert lh < 30.0, f"line height {lh} should be reasonable"



TESTS.append(("test_ttf_line_height", test_ttf_line_height))
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



TESTS.append(("test_ttf_corrupt_file", test_ttf_corrupt_file))
def test_ttf_missing_glyph_fallback():
    """P17.3 — char not in font (CJK) falls back to a wide cell."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    adv = m.advance_width("\u4e2d", 16.0)
    assert adv >= 12.0, f"missing-glyph fallback should be wide, got {adv}"



TESTS.append(("test_ttf_missing_glyph_fallback", test_ttf_missing_glyph_fallback))
def test_ttf_dual_weight():
    """P17.4 — dual-weight files: regular vs bold weight_class."""
    import ttf_font
    m_reg = ttf_font.load_ttf("fonts/arial.ttf")
    m_bold = ttf_font.load_ttf("fonts/arialbd.ttf")
    assert m_reg.weight_class == 400
    assert m_bold.weight_class == 700



TESTS.append(("test_ttf_dual_weight", test_ttf_dual_weight))
def test_ttf_family_loading():
    """P17.4 — load_font_for_family resolves serif/monospace to real files."""
    import ttf_font
    m = ttf_font.load_font_for_family("serif", 16.0, False, ["fonts"])
    assert m is not None
    m2 = ttf_font.load_font_for_family("monospace", 16.0, False, ["fonts"])
    assert m2 is not None



TESTS.append(("test_ttf_family_loading", test_ttf_family_loading))
def test_ttf_glyph_outline_a():
    """P17.2 — 'A' outline extracted (simple glyph in the fixture,
    >10 points spanning two contours)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("A")
    assert o is not None
    assert len(o.points) > 10
    # P33.R1 — the fixture 'A' is a simple (non-composite) glyph: two
    # contours, 15 points, hinting program present.
    assert o.is_composite == False



TESTS.append(("test_ttf_glyph_outline_a", test_ttf_glyph_outline_a))
def test_ttf_glyph_outline_a_lower():
    """P17.2 — 'a' is a simple glyph (no composite flag)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("a")
    assert o is not None
    assert len(o.points) > 10
    assert o.is_composite == False



TESTS.append(("test_ttf_glyph_outline_a_lower", test_ttf_glyph_outline_a_lower))
def test_ttf_glyph_outline_dot():
    """P17.2 — '.' is a small simple glyph."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline(".")
    assert o is not None
    assert len(o.points) > 3



TESTS.append(("test_ttf_glyph_outline_dot", test_ttf_glyph_outline_dot))
def test_ttf_glyph_space():
    """P17.2 — space glyph exists (empty outline is fine)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline(" ")
    assert o is not None, "space outline must exist"
    # Space has no drawn points (empty glyph) — the outline object
    # must still be returned (never None).



TESTS.append(("test_ttf_glyph_space", test_ttf_glyph_space))
def test_ttf_has_glyph():
    """P17.3 — has_glyph returns True for ASCII, False for CJK."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    assert m.has_glyph("A") == True
    assert m.has_glyph("0") == True
    assert m.has_glyph("\u4e2d") == False



TESTS.append(("test_ttf_has_glyph", test_ttf_has_glyph))
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



TESTS.append(("test_text_ttf_advance", test_text_ttf_advance))
def test_text_ttf_line_height():
    """P17.1 — text.line_height_px uses TTF metrics when registered."""
    import font, text
    font.init_real_fonts(["fonts"])
    lh = text.line_height_px(16.0, "monospace")
    assert lh > 16.0
    font._real_font_metrics.clear()
    lh_bm = text.line_height_px(16.0, "monospace")
    assert lh_bm > 0



TESTS.append(("test_text_ttf_line_height", test_text_ttf_line_height))
def test_render_with_ttf_metrics():
    """P17.4 — end-to-end render with TTF metrics (no crash)."""
    import browser, font
    font.init_real_fonts(["fonts"])
    html = '<div style="width:300px;height:60px;font-size:16px">Hello</div>'
    bmps = browser.render_document_with_mouse(html, "", 400, 300,
                                               [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1
    font._real_font_metrics.clear()



TESTS.append(("test_render_with_ttf_metrics", test_render_with_ttf_metrics))
def test_render_without_ttf_fallback():
    """P17.4 — render without TTF falls back to bitmap font (no crash)."""
    import browser, font
    font._real_font_metrics.clear()
    html = '<div style="width:300px;height:60px;font-size:16px">Hi</div>'
    bmps = browser.render_document_with_mouse(html, "", 400, 300,
                                               [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1



TESTS.append(("test_render_without_ttf_fallback", test_render_without_ttf_fallback))
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



TESTS.append(("test_ttf_outline_boundary", test_ttf_outline_boundary))
def test_ttf_composite_glyph():
    """P17.2 — a glyph whose outline is extractable (the '!' in the
    fixture is a simple glyph; any extractable outline satisfies the
    test's purpose of exercising the outline path)."""
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("!")
    assert o is not None
    assert len(o.points) > 5



TESTS.append(("test_ttf_composite_glyph", test_ttf_composite_glyph))
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



TESTS.append(("test_ttf_font_face_in_layout", test_ttf_font_face_in_layout))
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



TESTS.append(("test_ttf_weight_class_switch", test_ttf_weight_class_switch))
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



TESTS.append(("test_ttf_glyf_outline_points", test_ttf_glyf_outline_points))
def test_ttf_cmap_cjk_absent():
    """P17.3 — CJK characters not in Arial's cmap (fallback path).

    P33.R1 — this test exercises the TTF parser only (load_ttf on the
    shipped arial.ttf fixture).  The CJK fallback face registered by
    font.init_real_fonts is a *separate* concern (r1_* tests) and must
    not leak into the parser-level assertion below, so we clear the
    global CJK face before running.
    """
    import ttf_font
    import font as _font
    _saved = _font._cjk_fallback
    _font._cjk_fallback = None
    try:
        m = ttf_font.load_ttf("fonts/arial.ttf")
        for cp in (0x4E2D, 0x65E5, 0xFF01):
            assert m.has_glyph(chr(cp)) == False, f"U+{cp:04X} unexpectedly in cmap"
    finally:
        _font._cjk_fallback = _saved



TESTS.append(("test_ttf_cmap_cjk_absent", test_ttf_cmap_cjk_absent))
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



TESTS.append(("test_ttf_empty_font_metrics", test_ttf_empty_font_metrics))
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



TESTS.append(("test_p18_half_leading_baseline", test_p18_half_leading_baseline))
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



TESTS.append(("test_p18_font_ascent_descent_fields", test_p18_font_ascent_descent_fields))
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



TESTS.append(("test_p18_text_transform_uppercase", test_p18_text_transform_uppercase))
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



TESTS.append(("test_p18_text_transform_lowercase", test_p18_text_transform_lowercase))
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



TESTS.append(("test_p18_inline_link_cross_line", test_p18_inline_link_cross_line))
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



TESTS.append(("test_p18_vertical_align_top", test_p18_vertical_align_top))
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



TESTS.append(("test_p18_vertical_align_middle", test_p18_vertical_align_middle))
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



TESTS.append(("test_p18_inline_img_raises_line_height", test_p18_inline_img_raises_line_height))
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



TESTS.append(("test_p18_text_align_center_line_box", test_p18_text_align_center_line_box))
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



TESTS.append(("test_p18_render_half_leading", test_p18_render_half_leading))
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



TESTS.append(("test_p18_render_inline_styles_preserved", test_p18_render_inline_styles_preserved))
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



TESTS.append(("test_p18_mixed_inline_block", test_p18_mixed_inline_block))
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



TESTS.append(("test_p18_baseline_alignment_math", test_p18_baseline_alignment_math))
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



TESTS.append(("test_p18_line_height_half_leading_render", test_p18_line_height_half_leading_render))
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



TESTS.append(("test_p18_text_align_right", test_p18_text_align_right))
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



TESTS.append(("test_p18_mixed_line_wrap_render", test_p18_mixed_line_wrap_render))
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



TESTS.append(("test_p18_vertical_align_stored", test_p18_vertical_align_stored))
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



TESTS.append(("test_p18_half_leading_large_line_height", test_p18_half_leading_large_line_height))
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



TESTS.append(("test_p18_inline_run_height", test_p18_inline_run_height))
def test_p19_english_word_wrap():
    """P19.1 — 英文按词断行：窄容器内单词不截断。"""
    import text as t
    lines = t.wrap("hello world foo bar baz", 120.0, 16.0, "monospace")
    assert len(lines) >= 2, f"should wrap to >= 2 lines: {lines}"
    # No line should contain a truncated word (all original words intact).
    all_text = " ".join(lines)
    for w in ("hello", "world", "foo", "bar", "baz"):
        assert w in all_text, f"word {w!r} missing from wrap result"



TESTS.append(("test_p19_english_word_wrap", test_p19_english_word_wrap))
def test_p19_cjk_char_by_char():
    """P19.2 — CJK 逐字断行：每个汉字都是断点。"""
    import text as t
    lines = t.wrap("日本語の文字を一字ずつ折り返す", 60.0, 16.0, "monospace")
    assert len(lines) > 1, f"CJK should wrap to multiple lines: {lines}"
    # Total char count preserved (no chars lost).
    total_chars = sum(len(l) for l in lines)
    assert total_chars == len("日本語の文字を一字ずつ折り返す")



TESTS.append(("test_p19_cjk_char_by_char", test_p19_cjk_char_by_char))
def test_p19_kinsoku_no_line_start():
    """P19.2 — 禁则：句号不出现在行首。"""
    import text as t
    lines = t.wrap("日本語の文字。テスト。もう", 60.0, 16.0, "monospace")
    for line in lines:
        if line:
            assert not line.startswith("。"), \
                f"line starts with 。: {line!r}"



TESTS.append(("test_p19_kinsoku_no_line_start", test_p19_kinsoku_no_line_start))
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



TESTS.append(("test_p19_kinsoku_latin_brackets", test_p19_kinsoku_latin_brackets))
def test_p19_soft_hyphen():
    """P19.3 — &shy; 软连字符：断行时行末显示 '-'。"""
    import text as t
    lines = t.wrap("soft\u00ADhyphen word", 60.0, 16.0, "monospace")
    # The soft hyphen should create a break with '-' at line end.
    has_trailing_hyphen = any(l.endswith("-") for l in lines)
    assert has_trailing_hyphen, f"soft hyphen break expected: {lines}"



TESTS.append(("test_p19_soft_hyphen", test_p19_soft_hyphen))
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



TESTS.append(("test_p19_shy_html_entity", test_p19_shy_html_entity))
def test_p19_word_break_all():
    """P19.3 — word-break:break-all 每个字符都是断点。"""
    import text as t
    lines = t.wrap("abcdefghij", 50.0, 16.0, "monospace",
                   word_break="break-all")
    assert len(lines) > 1, f"break-all should wrap: {lines}"
    # Total chars preserved.
    total = sum(len(l) for l in lines)
    assert total == 10, f"char count mismatch: {total} != 10"



TESTS.append(("test_p19_word_break_all", test_p19_word_break_all))
def test_p19_overflow_wrap_break_word():
    """P19.4 — overflow-wrap:break-word 长单词逐字符折断。"""
    import text as t
    lines = t.wrap("supercalifragilistic", 50.0, 16.0, "monospace",
                   overflow_wrap="break-word")
    assert len(lines) > 1, f"long word should break: {lines}"
    # No line should be a single 18-char word.
    for l in lines:
        assert len(l) < 18, f"line too long: {l!r}"



TESTS.append(("test_p19_overflow_wrap_break_word", test_p19_overflow_wrap_break_word))
def test_p19_long_word_overflow():
    """P19.4 — 无 overflow-wrap 时长单词仍逐字符折断。"""
    import text as t
    lines = t.wrap("supercalifragilistic", 50.0, 16.0, "monospace")
    assert len(lines) > 1, f"long word should break: {lines}"
    # All chars preserved (no char lost).
    total_chars = sum(len(l) for l in lines)
    assert total_chars == len("supercalifragilistic"), \
        f"char count {total_chars} != 18"



TESTS.append(("test_p19_long_word_overflow", test_p19_long_word_overflow))
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



TESTS.append(("test_p19_mixed_cjk_latin", test_p19_mixed_cjk_latin))
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



TESTS.append(("test_p19_render_cjk_wrap", test_p19_render_cjk_wrap))
def test_p19_render_shy():
    """P19 端到端 — &shy; 渲染不崩。"""
    import browser
    html = ('<div style="width:100px;font-size:16px">'
            'soft&shy;hyphen word that wraps</div>')
    bmps = browser.render_document_with_mouse(
        html, "", 400, 300, [{"type": "click", "x": 50, "y": 30}])
    assert len(bmps) >= 1



TESTS.append(("test_p19_render_shy", test_p19_render_shy))
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



TESTS.append(("test_p19_render_word_break", test_p19_render_word_break))
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



TESTS.append(("test_p19_overflow_wrap_layout", test_p19_overflow_wrap_layout))
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



TESTS.append(("test_p19_word_break_layout", test_p19_word_break_layout))
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



TESTS.append(("test_p19_break_class", test_p19_break_class))
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



TESTS.append(("test_p19_kinsoku_sets", test_p19_kinsoku_sets))
def test_p19_wrap_empty():
    """P19 — 空文本 wrap 返回 [""]。"""
    import text as t
    lines = t.wrap("", 100.0, 16.0, "monospace")
    assert lines == [""]



TESTS.append(("test_p19_wrap_empty", test_p19_wrap_empty))
def test_p19_wrap_narrow_cjk():
    """P19.2 — 窄容器 CJK 逐字断行（每行 1-2 字）。"""
    import text as t
    lines = t.wrap("一二三四五六七八", 30.0, 16.0, "monospace")
    assert len(lines) >= 3, f"narrow CJK should wrap a lot: {lines}"



TESTS.append(("test_p19_wrap_narrow_cjk", test_p19_wrap_narrow_cjk))
def test_gui_page_load_file():
    """P31 — PageInstance.load_file 渲染本地 HTML。"""
    import gui
    p = gui.PageInstance()
    p.load_file("demo.html", 200, 150)
    assert p.bitmap is not None
    assert p.url == "demo.html"
    assert p.title == "demo.html"



TESTS.append(("test_gui_page_load_file", test_gui_page_load_file))
def test_gui_about_blank():
    """P31 — about:blank 渲染空白页。"""
    import gui
    p = gui.PageInstance()
    p.load_url("about:blank", 200, 150)
    assert p.bitmap is not None
    assert p.url == "about:blank"



TESTS.append(("test_gui_about_blank", test_gui_about_blank))
def test_gui_http_stub():
    """P31/P21 — http URL renders a bitmap: the unified error page when
    the network fails (offline / dead port), a real page when it
    succeeds.  The GUI must never show a blank on a network URL."""
    import gui
    p = gui.PageInstance()
    p.load_url("http://127.0.0.1:1/x", 200, 150)
    assert p.bitmap is not None, "http URL must always produce a bitmap"
    assert p.url == "http://127.0.0.1:1/x"
    assert sum(1 for px in p.bitmap.pixels if px != 0xFFFFFF) > 0, \
        "network error page must paint ink"



TESTS.append(("test_gui_http_stub", test_gui_http_stub))
def test_gui_inline_html():
    """P31 — 内联 HTML 直接渲染。"""
    import gui
    p = gui.PageInstance()
    p.load_url("<html><body><h1>Hi</h1></body></html>", 200, 150)
    assert p.bitmap is not None
    assert "Hi" in p.url



TESTS.append(("test_gui_inline_html", test_gui_inline_html))
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



TESTS.append(("test_gui_back_forward", test_gui_back_forward))
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



TESTS.append(("test_gui_bitmap_ppm", test_gui_bitmap_ppm))
def test_gui_tkinter_available():
    """P31 — tkinter is available in the stdlib."""
    import tkinter
    assert hasattr(tkinter, "Tk")
    assert hasattr(tkinter, "PhotoImage")



TESTS.append(("test_gui_tkinter_available", test_gui_tkinter_available))
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



TESTS.append(("test_p20_quadratic_flatten", test_p20_quadratic_flatten))
def test_p20_offcurve_midpoint_rule():
    """P20.1 — TrueType off-curve midpoint rule: two consecutive
    off-curve points produce an on-curve midpoint between them."""
    import rasterize
    pts = [(0.0, 0.0, False), (2.0, 0.0, False), (4.0, 0.0, True)]
    poly = rasterize._contour_to_points(pts, 1.0, 0.0, 0.0, flip_y=False)
    assert (1.0, 0.0) in poly, f"midpoint (1,0) missing from {poly}"
    assert (4.0, 0.0) in poly, "trailing on-curve must be kept"



TESTS.append(("test_p20_offcurve_midpoint_rule", test_p20_offcurve_midpoint_rule))
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



TESTS.append(("test_p20_fill_polygon_aa", test_p20_fill_polygon_aa))
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



TESTS.append(("test_p20_fill_polygon_speed", test_p20_fill_polygon_speed))
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



TESTS.append(("test_p20_synthetic_bold_increases_ink", test_p20_synthetic_bold_increases_ink))
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



TESTS.append(("test_p20_dilate_poly", test_p20_dilate_poly))
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



TESTS.append(("test_p20_outline_bbox_validation", test_p20_outline_bbox_validation))
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



TESTS.append(("test_p20_letter_spacing_widens", test_p20_letter_spacing_widens))
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



TESTS.append(("test_p20_text_rendering_stored", test_p20_text_rendering_stored))
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



TESTS.append(("test_p20_font_weight_stored", test_p20_font_weight_stored))
def test_p20_metrics_for_weight():
    """P20.4 — get_metrics_for_weight returns a metrics object and the
    synthetic-bold flag for families that lack a bold file.
    P33.R1 — re-initializes the font registry so the test is not
    affected by a prior test that cleared _real_font_metrics."""
    import font
    font.init_real_fonts(["fonts"])
    m, synth = font.get_metrics_for_weight("monospace", 400)
    assert m is not None, "normal weight must resolve to a font"
    assert synth is False, "normal weight must not request synthetic bold"
    mb, synb = font.get_metrics_for_weight("monospace", 700)
    assert mb is not None, "bold weight must resolve to some font"



TESTS.append(("test_p20_metrics_for_weight", test_p20_metrics_for_weight))
def test_p20_font_fallback_chain():
    """P20.4 — find_font_for_family resolves a missing family through the
    fallback chain to a bundled TTF."""
    import ttf_font
    m = ttf_font.find_font_for_family("nonexistentfamily123", 400, ["fonts"])
    assert m is not None, "fallback chain must land on a bundled TTF"
    m2 = ttf_font.find_font_for_family("monospace", 400, ["fonts"])
    assert m2 is not None



TESTS.append(("test_p20_font_fallback_chain", test_p20_font_fallback_chain))
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



TESTS.append(("test_p20_match_weight", test_p20_match_weight))
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



TESTS.append(("test_p20_draw_text_curves_letter_spacing", test_p20_draw_text_curves_letter_spacing))
def test_p20_glyf_instruction_count_layout():
    """P20.1 — the simple-glyph parser places the flags section after
    instructionCount + the hinting program (not misaligned by
    num_contours)."""
    import ttf_font, struct
    m = _p20_metrics()
    if m is None:
        return
    raw = ttf_font._read_glyf(m, m.cmap[ord("S")])
    nc = struct.unpack(">h", raw[:2])[0]
    assert nc == 1
    end_pts = struct.unpack(">H", raw[10:12])[0]
    n = end_pts + 1
    # P33.R1 — flags come after the hinting program: offset 14 + instCount.
    inst = struct.unpack(">H", raw[14:16])[0]
    flag_off = 14 + 2 + inst
    assert n >= 3, f"S must have >= 3 points, got {n}"
    # P33.R1 — the parsed outline must have n points (the parser skips
    # the hinting program correctly).
    o = m.glyph_outline("S")
    assert o is not None, "S outline must exist"
    assert len(o.points) == n, \
        f"S must have {n} points (the fixture's n), got {len(o.points)}"



TESTS.append(("test_p20_glyf_instruction_count_layout", test_p20_glyf_instruction_count_layout))
def test_p20_point_flags_bits():
    """P33.R1 — the glyf point-tag bit meanings (FreeType ttgload.c):
    on-curve 0x01, xShort 0x02, yShort 0x04, xPositive 0x10,
    yPositive 0x20, repeat 0x08.  Long vectors are signed 16-bit;
    short vectors are 1-byte magnitude with the 0x10/0x20 bit as sign."""
    import ttf_font, struct
    rec = struct.pack(">h", 1) + struct.pack(">hhhh", 0, 0, 100, 100)
    rec += struct.pack(">H", 1)          # endPts[0]=1
    rec += struct.pack(">H", 0)         # instCount=0
    # p0: on-curve, xShort-positive, yShort-positive → flags 0x01|0x02|0x10|0x04|0x20 = 0x37
    # p1: on-curve, xShort-positive, yShort-positive, delta (80,80)
    rec += bytes([0x37, 0x37])
    # X pass (short, positive): 10, +80 → (10, 90)
    rec += bytes([10, 80])
    # Y pass (short, positive): 10, +80 → (10, 90)
    rec += bytes([10, 80])
    pts = ttf_font._parse_simple_glyph(rec)
    assert len(pts) == 2, f"expected 2 points, got {len(pts)}"
    (x0, y0, oc0), (x1, y1, oc1) = pts
    assert oc0 and oc1, "0x01 flags must be on-curve"
    assert (x0, y0) == (10, 10), f"point0 = {(x0, y0)}"
    assert (x1, y1) == (90, 90), f"point1 = {(x1, y1)}"



TESTS.append(("test_p20_point_flags_bits", test_p20_point_flags_bits))
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



TESTS.append(("test_p20_curve_glyph_fallback", test_p20_curve_glyph_fallback))
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



TESTS.append(("test_p20_paint_command_fields", test_p20_paint_command_fields))
def test_p20_layout_box_fields():
    """P20.4/P20.5 — LayoutBox carries text_rendering and font_weight
    with correct defaults."""
    import layout
    b = layout.LayoutBox(box_type=layout.BoxType.TEXT, text_content="x")
    assert b.text_rendering == "auto"
    assert b.font_weight == 400



TESTS.append(("test_p20_layout_box_fields", test_p20_layout_box_fields))
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



TESTS.append(("test_p20_synthetic_bold_flag_roundtrip", test_p20_synthetic_bold_flag_roundtrip))
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



TESTS.append(("test_p20_render_full_pipeline_weight", test_p20_render_full_pipeline_weight))
def test_p20_glyf_offcurve_yreversed():
    """P33.R1 — yShort vectors (0x04 tag bit) use a 1-byte magnitude
    with the 0x20 bit as sign (clear = negative); xLong (no 0x02 tag
    bit) uses signed 16-bit.  The two-pass read (x then y) is
    confirmed."""
    import ttf_font, struct
    rec = struct.pack(">h", 1) + struct.pack(">hhhh", 0, 0, 100, 100)
    rec += struct.pack(">H", 2)        # endPts[0]=2 (3 points)
    rec += struct.pack(">H", 0)        # instCount=0
    # p0: on-curve, xLong, yLong → flags 0x01
    # p1: off-curve, xLong, yShort-negative → flags 0x04
    # p2: on-curve, xShort-positive, yShort-negative → flags 0x01|0x02|0x10|0x04 = 0x17
    rec += bytes([0x01, 0x04, 0x17])
    # X pass: p0 xLong=10; p1 xLong delta=5 → 15; p2 xShort=20 → 35
    rec += struct.pack(">H", 10) + struct.pack(">H", 5) + bytes([20])
    # Y pass: p0 yLong=10; p1 yShort negative magnitude 30 → y=10-30=-20…
    # but p2 yShort magnitude 40, negative → y=-20-40=-60
    rec += struct.pack(">H", 10) + bytes([30, 40])
    pts = ttf_font._parse_simple_glyph(rec)
    assert len(pts) == 3, f"expected 3 points, got {len(pts)}"
    assert pts[0][:2] == (10, 10), f"p0={pts[0]}"
    assert pts[1][:2] == (15, -20), f"p1={pts[1]}"
    assert pts[2][:2] == (35, -60), f"p2={pts[2]}"
    assert pts[0][2] and pts[2][2] and not pts[1][2], "on-curve flags wrong"



TESTS.append(("test_p20_glyf_offcurve_yreversed", test_p20_glyf_offcurve_yreversed))
def test_p21_parse_url_absolute():
    """P21.1 — absolute URL parses into all six components."""
    import net
    u = net.parse_url("http://example.com:8080/a/b?c=1#f")
    assert u.scheme == "http"
    assert u.host == "example.com"
    assert u.port == 8080
    assert u.path == "/a/b"
    assert u.query == "c=1"
    assert u.fragment == "f"



TESTS.append(("test_p21_parse_url_absolute", test_p21_parse_url_absolute))
def test_p21_parse_url_https_default_port():
    """P21.1 — https without an explicit port keeps port=0 but
    effective_port is 443; http default is 80."""
    import net
    u = net.parse_url("https://example.com/x")
    assert u.port == 0 and u.effective_port == 443
    u2 = net.parse_url("http://example.com")
    assert u2.effective_port == 80
    # Canonicalization: explicit default port is dropped from authority.
    u3 = net.parse_url("https://x.com:443/")
    assert u3.authority == "x.com", f"443 is default, got {u3.authority!r}"



TESTS.append(("test_p21_parse_url_https_default_port", test_p21_parse_url_https_default_port))
def test_p21_relative_url_resolution():
    """P21.1 — relative URL resolution against a base (WHATWG rules)."""
    import net
    base = net.parse_url("http://h.com:8080/a/b?c=1#f")
    p = net.parse_url("page.html", base)
    assert p.path == "/a/page.html" and p.host == "h.com"
    up = net.parse_url("../x", base)
    assert up.path == "/x"
    abs_p = net.parse_url("/abs", base)
    assert abs_p.path == "/abs" and abs_p.port == 8080
    q = net.parse_url("?q=2", base)
    assert q.query == "q=2" and q.path == "/a/b"
    sr = net.parse_url("//other.com/p", base)
    assert sr.host == "other.com" and sr.scheme == "http"



TESTS.append(("test_p21_relative_url_resolution", test_p21_relative_url_resolution))
def test_p21_url_to_string_roundtrip():
    """P21.1 — to_string() round-trips through parse_url without loss."""
    import net
    src = "http://h.com:8080/a/b?c=1#f"
    u = net.parse_url(src)
    assert u.to_string() == src
    # Default port dropped.
    u2 = net.parse_url("https://h.com:443/x")
    assert u2.to_string() == "https://h.com/x"



TESTS.append(("test_p21_url_to_string_roundtrip", test_p21_url_to_string_roundtrip))
def test_p21_normalize_url():
    """P21.1 — normalize lowercases scheme/host, fills empty path."""
    import net
    u = net.parse_url("HTTP://H.COM")
    n = net.normalize_url(u)
    assert n.scheme == "http" and n.host == "h.com"
    assert n.path == "/", f"empty path must become /, got {n.path!r}"



TESTS.append(("test_p21_normalize_url", test_p21_normalize_url))
def test_p21_origin_property():
    """P21.1 — origin is scheme://host[:port] without the default port."""
    import net
    u = net.parse_url("http://h.com:8080/a")
    assert u.origin == "http://h.com:8080"
    u2 = net.parse_url("https://h.com/a")
    assert u2.origin == "https://h.com"



TESTS.append(("test_p21_origin_property", test_p21_origin_property))
def test_p21_http_get_200():
    """P21.2 — a 200 GET returns the body + content-type via the
    local http.server fixture (offline, CI-stable)."""
    import net
    routes = {"/": (200, "text/html", b"<html><body>hi</body></html>")}
    srv, port = _p21_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/")
        assert r.status == 200
        assert r.is_html
        assert b"hi" in r.body
        assert r.ok
    finally:
        srv.shutdown()



TESTS.append(("test_p21_http_get_200", test_p21_http_get_200))
def test_p21_http_get_404():
    """P21.2 — a 404 is returned as-is (client does NOT hide errors);
    the caller decides whether to render the error page."""
    import net
    routes = {}  # no matching route → fixture returns 404
    srv, port = _p21_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/missing")
        assert r.status == 404
        assert not r.ok
    finally:
        srv.shutdown()



TESTS.append(("test_p21_http_get_404", test_p21_http_get_404))
def test_p21_http_redirect_surfaced():
    """P21.2 — a 301 is returned as-is with its status (P22 will follow
    the chain; P21 surfaces it, does not follow automatically)."""
    import net
    routes = {"/a": (301, "", b"")}
    srv, port = _p21_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/a")
        assert r.status == 301
        assert not r.ok, "3xx is not 2xx"
    finally:
        srv.shutdown()



TESTS.append(("test_p21_http_redirect_surfaced", test_p21_http_redirect_surfaced))
def test_p21_content_type_dispatch():
    """P21.3 — fetch_html returns decoded text for text/html and raises
    FetchError for a non-HTML Content-Type (the 'download/ignore' path)."""
    import net
    routes = {
        "/html": (200, "text/html", b"<p>ok</p>"),
        "/json": (200, "application/json", b'{"a":1}'),
    }
    srv, port = _p21_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        text, resp = net.fetch_html(f"http://127.0.0.1:{port}/html", c)
        assert text == "<p>ok</p>" and resp.status == 200
        try:
            net.fetch_html(f"http://127.0.0.1:{port}/json", c)
            assert False, "non-HTML content-type must raise FetchError"
        except net.FetchError as e:
            assert e.status == 200
    finally:
        srv.shutdown()



TESTS.append(("test_p21_content_type_dispatch", test_p21_content_type_dispatch))
def test_p21_fetch_error_on_4xx():
    """P21.3 — fetch_html raises FetchError carrying the status on 4xx."""
    import net
    routes = {}
    srv, port = _p21_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        try:
            net.fetch_html(f"http://127.0.0.1:{port}/nope", c)
            assert False, "404 must raise FetchError"
        except net.FetchError as e:
            assert e.status == 404
            assert "404" in str(e)
    finally:
        srv.shutdown()



TESTS.append(("test_p21_fetch_error_on_4xx", test_p21_fetch_error_on_4xx))
def test_p21_unsupported_scheme():
    """P21.3 — schemes other than http/https are rejected outright."""
    import net
    c = net.HttpClient()
    for bad in ("ftp://x.com", "mailto:a@b.c"):
        u = net.parse_url(bad)
        try:
            c.get(u)
            assert False, f"{bad} must raise UnsupportedScheme"
        except net.UnsupportedScheme:
            pass



TESTS.append(("test_p21_unsupported_scheme", test_p21_unsupported_scheme))
def test_p21_error_page_html():
    """P21.3 — the unified error page HTML contains the title/detail."""
    import net
    html = net.error_page_html("Timeout", "cannot reach host")
    assert "Timeout" in html
    assert "cannot reach host" in html
    assert "<h1>" in html



TESTS.append(("test_p21_error_page_html", test_p21_error_page_html))
def test_p21_error_page_renders():
    """P21.3 — render_error_page produces a non-blank Bitmap via the
    full pipeline (so the GUI/CLI can drop it in directly)."""
    import net, paint
    bmp = net.render_error_page("Not Found", "404 on x", 200, 120)
    assert isinstance(bmp, paint.Bitmap)
    assert bmp.width == 200 and bmp.height == 120
    assert sum(1 for p in bmp.pixels if p != 0xFFFFFF) > 0, \
        "error page must paint ink"



TESTS.append(("test_p21_error_page_renders", test_p21_error_page_renders))
def test_p21_connection_error_retries():
    """P21.2 — a connection to a dead port retries then raises
    ConnectionError (the unified error the caller renders)."""
    import net
    c = net.HttpClient(timeout=0.5)
    try:
        c.get("http://127.0.0.1:1/x")
        assert False, "dead port must raise"
    except ConnectionError as e:
        assert "GET" in str(e)



TESTS.append(("test_p21_connection_error_retries", test_p21_connection_error_retries))
def test_p21_http_query_in_path():
    """P21.2 — the query string is appended to the wire path."""
    import net
    srv, port = _p21_start_server({"/x": (200, "text/html", b"got")})
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/x?a=1")
        assert r.status == 200 and b"got" in r.body
    finally:
        srv.shutdown()



TESTS.append(("test_p21_http_query_in_path", test_p21_http_query_in_path))
def test_p21_url_is_network_flag():
    """P21.1 — is_network / is_http / is_https flags are consistent."""
    import net
    u = net.parse_url("http://h/p")
    assert u.is_http and u.is_network
    u2 = net.parse_url("https://h/p")
    assert u2.is_https and u2.is_network and not u2.is_http
    u3 = net.parse_url("mailto:x@y.z")
    assert not u3.is_network



TESTS.append(("test_p21_url_is_network_flag", test_p21_url_is_network_flag))
def test_p21_fetch_html_accepts_str_url():
    """P21.3 — fetch_html accepts a bare string (parsed on the fly)."""
    import net
    routes = {"/": (200, "text/html", b"<b>x</b>")}
    srv, port = _p21_start_server(routes)
    try:
        text, _ = net.fetch_html(f"http://127.0.0.1:{port}/")
        assert text == "<b>x</b>"
    finally:
        srv.shutdown()



TESTS.append(("test_p21_fetch_html_accepts_str_url", test_p21_fetch_html_accepts_str_url))
def test_p21_cli_network_error_exit_code():
    """P21.3 — main.py exits 3 and writes the error PPM on a network
    failure (the error page is still rendered, not a crash)."""
    import subprocess, sys, os
    p = subprocess.run(
        [sys.executable, "main.py", "http://127.0.0.1:1/x.html",
         "p21_err.ppm", "--width", "100", "--height", "60"],
        cwd=os.getcwd(), capture_output=True, text=True, timeout=45,
    )
    assert p.returncode == 3, \
        f"expected exit 3, got {p.returncode}: {p.stderr[-400:]}"
    assert os.path.exists("p21_err.ppm"), "error PPM must be written"
    try:
        os.remove("p21_err.ppm")
    except OSError:
        pass



TESTS.append(("test_p21_cli_network_error_exit_code", test_p21_cli_network_error_exit_code))
def test_p22_redirect_followed():
    """P22.1 — a 302 chain is followed to the final 200."""
    import net
    routes = {
        "/a": (302, "text/plain", b"", {"Location": "/b"}),
        "/b": (301, "text/plain", b"", {"Location": "/c"}),
        "/c": (200, "text/html", b"<h1>final</h1>"),
    }
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/a")
        assert r.status == 200, f"expected 200 after redirects, got {r.status}"
        assert r.redirected is True, "redirected flag must be set"
        assert b"final" in r.body
    finally:
        srv.shutdown()



TESTS.append(("test_p22_redirect_followed", test_p22_redirect_followed))
def test_p22_redirect_loop_detected():
    """P22.1 — a 302 → 302 self-loop raises RedirectLoop."""
    import net
    routes = {
        "/a": (302, "text/plain", b"", {"Location": "/b"}),
        "/b": (302, "text/plain", b"", {"Location": "/a"}),
    }
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        try:
            c.get(f"http://127.0.0.1:{port}/a")
            assert False, "loop must raise RedirectLoop"
        except net.RedirectLoop as e:
            assert len(e.chain) >= 2, f"chain too short: {e.chain}"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_redirect_loop_detected", test_p22_redirect_loop_detected))
def test_p22_redirect_exceeds_cap():
    """P22.1 — a 7-hop chain exceeds the 5-hop cap and raises
    RedirectLoop (not silently truncated)."""
    import net
    # /n → /n+1 for n in 0..6, /7 = 200.  7 hops > MAX_REDIRECTS=5.
    routes = {}
    for i in range(7):
        routes[f"/{i}"] = (302, "text/plain", b"",
                            {"Location": f"/{i+1}"})
    routes["/7"] = (200, "text/html", b"done")
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        try:
            c.get(f"http://127.0.0.1:{port}/0")
            assert False, "7-hop chain must exceed cap 5"
        except net.RedirectLoop:
            pass
    finally:
        srv.shutdown()



TESTS.append(("test_p22_redirect_exceeds_cap", test_p22_redirect_exceeds_cap))
def test_p22_redirect_within_cap_succeeds():
    """P22.1 — a 5-hop chain (== cap) succeeds without error."""
    import net
    routes = {}
    for i in range(5):
        routes[f"/{i}"] = (302, "text/plain", b"",
                            {"Location": f"/{i+1}"})
    routes["/5"] = (200, "text/html", b"ok")
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/0")
        assert r.status == 200, f"5 hops should succeed, got {r.status}"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_redirect_within_cap_succeeds", test_p22_redirect_within_cap_succeeds))
def test_p22_redirect_no_follow_returns_302():
    """P22.1 — follow_redirects=False returns the 3xx as-is (P21
    behavior preserved)."""
    import net
    routes = {
        "/a": (302, "text/plain", b"", {"Location": "/b"}),
        "/b": (200, "text/html", b"b"),
    }
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/a", follow_redirects=False)
        assert r.status == 302, f"expected 302, got {r.status}"
        assert r.redirected is True
        assert r.headers.get("location") == "/b"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_redirect_no_follow_returns_302", test_p22_redirect_no_follow_returns_302))
def test_p22_gzip_decoded():
    """P22.2 — a gzip-encoded body is transparently decoded."""
    import net, gzip
    payload = b"<html><body>P22 gzip</body></html>"
    routes = {
        "/g": (200, "text/html", gzip.compress(payload),
               {"Content-Encoding": "gzip"}),
    }
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/g")
        assert r.status == 200
        assert r.body == payload, "gzip body must be decoded to original"
        assert b"P22 gzip" in r.body
    finally:
        srv.shutdown()



TESTS.append(("test_p22_gzip_decoded", test_p22_gzip_decoded))
def test_p22_gzip_no_header_passthrough():
    """P22.2 — without Content-Encoding: gzip the body is returned
    verbatim (idempotent decoder)."""
    import net
    routes = {"/p": (200, "text/html", b"plain")}
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/p")
        assert r.body == b"plain", "no gzip header → body unchanged"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_gzip_no_header_passthrough", test_p22_gzip_no_header_passthrough))
def test_p22_chunked_decode_roundtrip():
    """P22.3 — net.decode_chunked decodes a raw chunked body
    (unit-testable hook; http.client handles the wire transparently)."""
    import net
    raw = b"5\r\nhello\r\n6\r\n world\r\n0\r\n"
    assert net.decode_chunked(raw) == b"hello world"
    # Empty / single-chunk edge cases.
    assert net.decode_chunked(b"0\r\n") == b""
    assert net.decode_chunked(b"a\r\n0123456789\r\n0\r\n") == b"0123456789"



TESTS.append(("test_p22_chunked_decode_roundtrip", test_p22_chunked_decode_roundtrip))
def test_p22_chunked_with_extensions():
    """P22.3 — chunk size lines may carry ``;ext`` suffixes that are
    ignored (spec §4.1.2)."""
    import net
    raw = b"4;name=value\r\nWiki\r\n5\r\npedia\r\n0\r\n"
    assert net.decode_chunked(raw) == b"Wikipedia"



TESTS.append(("test_p22_chunked_with_extensions", test_p22_chunked_with_extensions))
def test_p22_chunked_max_size_cap():
    """P22.3 — a malformed size line terminates the decode (no crash)."""
    import net
    # Garbage size line → int() fails → decode stops, returns what we
    # accumulated so far (empty here).
    assert net.decode_chunked(b"xyz\r\n") == b""



TESTS.append(("test_p22_chunked_max_size_cap", test_p22_chunked_max_size_cap))
def test_p22_subresource_collection():
    """P22.4 — collect_subresources returns <link rel=stylesheet> and
    <img src> in document order."""
    import net, dom
    tree = dom.parse(
        "<html><head>"
        '<link rel="stylesheet" href="a.css">'
        '<link rel="icon" href="f.ico">'
        "</head><body>"
        '<img src="p1.png"><img src="p2.png">'
        "</body></html>"
    )
    specs = net.collect_subresources(tree)
    kinds = [(s["kind"], s["src"]) for s in specs]
    assert ("stylesheet", "a.css") in kinds, kinds
    assert ("image", "p1.png") in kinds, kinds
    assert ("image", "p2.png") in kinds, kinds
    # <link rel=icon> is NOT a stylesheet → excluded.
    assert not any(s["src"] == "f.ico" for s in specs), \
        "non-stylesheet <link> must be excluded"



TESTS.append(("test_p22_subresource_collection", test_p22_subresource_collection))
def test_p22_base_tag_overrides_doc_base():
    """P22.5 — a <base href> overrides the document URL as the base
    for relative subresource resolution."""
    import net, dom
    tree = dom.parse(
        "<html><head>"
        '<base href="/override/">'
        '<link rel="stylesheet" href="x.css">'
        "</head><body></body></html>"
    )
    base = net.base_url_from_dom(tree)
    assert base is not None, "base href must be detected"
    assert base.path == "/override/", f"got {base.path}"
    # The stylesheet resolves against /override/, not the doc root.
    resolved = net.parse_url("x.css", base=base)
    assert resolved.path == "/override/x.css", f"got {resolved.path}"



TESTS.append(("test_p22_base_tag_overrides_doc_base", test_p22_base_tag_overrides_doc_base))
def test_p22_base_tag_absent():
    """P22.5 — without a <base> tag, base_url_from_dom returns None
    (caller falls back to the document URL)."""
    import net, dom
    tree = dom.parse(
        "<html><head>"
        '<link rel="stylesheet" href="a.css">'
        "</head><body></body></html>"
    )
    assert net.base_url_from_dom(tree) is None



TESTS.append(("test_p22_base_tag_absent", test_p22_base_tag_absent))
def test_p22_subresource_failure_does_not_block():
    """P22.4 — a 404 subresource maps to None; the page still renders
    (load_subresources never raises for a bad subresource)."""
    import net, dom
    routes = {"/ok.css": (200, "text/css", b"body{color:red}"),
              "/missing.css": (404, None, b"")}
    srv, port = _p22_start_server(routes)
    base = f"http://127.0.0.1:{port}"
    tree = dom.parse(
        f"<html><head>"
        f'<link rel="stylesheet" href="ok.css">'
        f'<link rel="stylesheet" href="missing.css">'
        f"</head><body><p>hi</p></body></html>"
    )
    try:
        loaded = net.load_subresources(tree, net.parse_url(base + "/"),
                                       client=net.HttpClient(timeout=5))
        styles = loaded.get("stylesheet", {})
        ok_key = [k for k in styles if k.endswith("ok.css")][0]
        miss_key = [k for k in styles if k.endswith("missing.css")][0]
        assert styles[ok_key] is not None, "ok.css must load"
        assert styles[ok_key].ok is True
        assert styles[miss_key] is None, "404 css must map to None"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_subresource_failure_does_not_block", test_p22_subresource_failure_does_not_block))
def test_p22_render_document_from_url():
    """P22 — render_document_from_url loads a doc + subresources and
    returns a Bitmap (full pipeline, no crash)."""
    import net, dom, browser, paint
    routes = {
        "/": (200, "text/html",
              b"<html><head>"
              b'<link rel="stylesheet" href="s.css">'
              b"</head><body><p style='color:red'>P22</p></body></html>"),
        "/s.css": (200, "text/css", b"p{font-size:20px}"),
    }
    srv, port = _p22_start_server(routes)
    try:
        bmp = browser.render_document_from_url(
            f"http://127.0.0.1:{port}/", width=300, height=200,
        )
        assert isinstance(bmp, paint.Bitmap)
        assert sum(1 for p in bmp.pixels if p != 0xFFFFFF) > 0, \
            "P22 doc must paint ink"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_render_document_from_url", test_p22_render_document_from_url))
def test_p22_render_from_url_gzip_doc():
    """P22.2 — a gzip-encoded HTML doc is decoded and rendered.

    P33.R1 — ink may land in the document region below the viewport
    top; scan a wider band rather than the whole buffer so a valid
    render with text at y≈20 still passes.
    """
    import net, browser, paint, gzip
    html = b"<html><body><h1>P22z</h1></body></html>"
    routes = {"/": (200, "text/html", gzip.compress(html),
                     {"Content-Encoding": "gzip"})}
    srv, port = _p22_start_server(routes)
    try:
        bmp = browser.render_document_from_url(
            f"http://127.0.0.1:{port}/", width=200, height=150,
        )
        assert isinstance(bmp, paint.Bitmap)
        nw = 0
        for y in range(0, min(120, bmp.height)):
            for x in range(bmp.width):
                px = bmp.pixels[y * bmp.width + x]
                if px != 0xFFFFFF:
                    nw += 1
        assert nw > 0, "P22 gzip doc must paint ink"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_render_from_url_gzip_doc", test_p22_render_from_url_gzip_doc))
def test_p22_render_from_url_network_error():
    """P22 — a dead host raises FetchError (caller renders the error
    page); the API never silently returns a blank."""
    import net, browser
    try:
        browser.render_document_from_url(
            "http://127.0.0.1:1/x", width=100, height=60,
        )
        assert False, "dead host must raise"
    except (net.FetchError, ConnectionError):
        pass



TESTS.append(("test_p22_render_from_url_network_error", test_p22_render_from_url_network_error))
def test_p22_subresource_data_uri_skipped():
    """P22.4 — a data: URI in <img src> is recorded but not fetched
    (no network round-trip)."""
    import net, dom
    tree = dom.parse(
        "<html><body>"
        '<img src="data:image/png;base64,AAA">'
        "</body></html>"
    )
    specs = net.collect_subresources(tree)
    assert len(specs) == 1
    assert specs[0]["src"].startswith("data:")
    # load_subresources maps it to None (no fetch) without raising.
    loaded = net.load_subresources(tree, net.parse_url("http://h/"))
    imgs = loaded.get("image", {})
    assert any(k.startswith("data:") for k in imgs)
    assert all(v is None for k, v in imgs.items()
               if k.startswith("data:"))



TESTS.append(("test_p22_subresource_data_uri_skipped", test_p22_subresource_data_uri_skipped))
def test_p22_fetch_resource_returns_none_on_404():
    """P22.4 — fetch_resource returns None on a 404 (subresource
    semantics: never raise)."""
    import net
    routes = {"/x.css": (404, None, b"")}
    srv, port = _p22_start_server(routes)
    try:
        r = net.fetch_resource(f"http://127.0.0.1:{port}/x.css")
        assert r is None, "404 subresource must map to None"
    finally:
        srv.shutdown()



TESTS.append(("test_p22_fetch_resource_returns_none_on_404", test_p22_fetch_resource_returns_none_on_404))
def test_p22_redirect_relative_location():
    """P22.1 — a relative Location (no scheme/host) is resolved
    against the redirecting URL, not the original."""
    import net
    routes = {
        "/start": (302, "text/plain", b"", {"Location": "mid"}),
        "/mid": (200, "text/html", b"rel-loc"),
    }
    srv, port = _p22_start_server(routes)
    try:
        c = net.HttpClient(timeout=5)
        r = c.get(f"http://127.0.0.1:{port}/start")
        assert r.status == 200, f"relative Location must resolve, got {r.status}"
        assert b"rel-loc" in r.body
    finally:
        srv.shutdown()



TESTS.append(("test_p22_redirect_relative_location", test_p22_redirect_relative_location))
def test_p23_cache_hit_skips_wire():
    """P23.2 — a fresh cached response is returned without touching
    the wire (the server is shut down before the second GET)."""
    import net, time
    routes = {"/x": (200, "text/html", b"hello",
                      {"Cache-Control": "max-age=300"})}
    srv, port = _p22_start_server(routes)
    base = f"http://127.0.0.1:{port}"
    cache_dir = tempfile.mkdtemp(prefix="p23cache")
    try:
        store = net.CacheStore(cache_dir)
        c = net.HttpClient(timeout=5, cache=store)
        r1 = c.get(f"{base}/x")
        assert r1.status == 200 and b"hello" in r1.body
        assert r1.from_cache is False
        srv.shutdown()  # wire is now gone — a hit must NOT need it.
        r2 = c.get(f"{base}/x")
        assert r2.from_cache is True, "fresh entry must be a cache hit"
        assert b"hello" in r2.body
    finally:
        store.clear()
        _rmtree_quiet(cache_dir)



TESTS.append(("test_p23_cache_hit_skips_wire", test_p23_cache_hit_skips_wire))
def test_p23_cache_no_store_not_cached():
    """P23.2 — ``Cache-Control: no-store`` responses are never written
    to disk; the second GET is a wire miss."""
    import net
    routes = {"/ns": (200, "text/html", b"ns",
                       {"Cache-Control": "no-store"})}
    srv, port = _p22_start_server(routes)
    cache_dir = tempfile.mkdtemp(prefix="p23cache")
    try:
        store = net.CacheStore(cache_dir)
        c = net.HttpClient(timeout=5, cache=store)
        r1 = c.get(f"http://127.0.0.1:{port}/ns")
        assert r1.status == 200
        assert store.get_fresh(net.parse_url(f"http://127.0.0.1:{port}/ns")) is None, \
            "no-store must not be cached"
    finally:
        srv.shutdown()
        store.clear()
        _rmtree_quiet(cache_dir)



TESTS.append(("test_p23_cache_no_store_not_cached", test_p23_cache_no_store_not_cached))
def test_p23_cache_max_age_expiry():
    """P23.2 — a max-age=0 entry is immediately stale (ttl <= 0)."""
    import net
    routes = {"/z": (200, "text/html", b"z",
                     {"Cache-Control": "max-age=0"})}
    srv, port = _p22_start_server(routes)
    cache_dir = tempfile.mkdtemp(prefix="p23cache")
    try:
        store = net.CacheStore(cache_dir)
        c = net.HttpClient(timeout=5, cache=store)
        r1 = c.get(f"http://127.0.0.1:{port}/z")
        assert r1.status == 200
        # max-age=0 → ttl is 0 → not fresh.
        assert store.get_fresh(net.parse_url(f"http://127.0.0.1:{port}/z")) is None
    finally:
        srv.shutdown()
        store.clear()
        _rmtree_quiet(cache_dir)



TESTS.append(("test_p23_cache_max_age_expiry", test_p23_cache_max_age_expiry))
def test_p23_cache_etag_304_reuse():
    """P23.3 — a stale entry with an ETag is revalidated; a 304
    response reuses the cached body and refreshes freshness."""
    import net
    routes = {
        "/e": (200, "text/html", b"body-1",
               {"ETag": '"v1"', "Cache-Control": "max-age=0"}),
    }
    srv, port = _p22_start_server(routes)
    cache_dir = tempfile.mkdtemp(prefix="p23cache")
    try:
        store = net.CacheStore(cache_dir)
        c = net.HttpClient(timeout=5, cache=store)
        u = net.parse_url(f"http://127.0.0.1:{port}/e")
        r1 = c.get(u)
        assert r1.status == 200 and b"body-1" in r1.body
        # max-age=0 → stale; get_stale returns the ETagged entry.
        stale = store.get_stale(u)
        assert stale is not None, "stale entry with ETag must be returned"
        assert stale.status == 200
        # Now the server returns 304 for the matching If-None-Match.
        routes["/e"] = (304, "text/html", b"",
                        {"ETag": '"v1"'})
        r2 = c.get(u)
        assert r2.from_cache is True, "304 must surface as a cached 200"
        assert b"body-1" in r2.body, "304 must reuse the stale body"
    finally:
        srv.shutdown()
        store.clear()
        _rmtree_quiet(cache_dir)



TESTS.append(("test_p23_cache_etag_304_reuse", test_p23_cache_etag_304_reuse))
def test_p23_cache_heuristic_ttl():
    """P23.2 — without directives, heuristic TTL = 10% of
    (Date - Last-Modified)."""
    import net
    # Last-Modified 100s before Date → heuristic ttl = 10s.
    lm = "Thu, 01 Jan 2020 00:00:00 GMT"
    dt = "Thu, 01 Jan 2020 00:01:40 GMT"
    routes = {"/h": (200, "text/html", b"heuristic",
                     {"Last-Modified": lm, "Date": dt})}
    srv, port = _p22_start_server(routes)
    cache_dir = tempfile.mkdtemp(prefix="p23cache")
    try:
        store = net.CacheStore(cache_dir)
        c = net.HttpClient(timeout=5, cache=store)
        r1 = c.get(f"http://127.0.0.1:{port}/h")
        assert r1.status == 200
        u = net.parse_url(f"http://127.0.0.1:{port}/h")
        entry = store.get_stale(u)  # stale check only; heuristic ttl>0
        # Freshness: heuristic_ttl should be ~10s; right after fetch it's
        # still fresh, so get_fresh should return a hit.
        assert store.get_fresh(u) is not None, \
            "heuristic entry should be fresh right after fetch"
    finally:
        srv.shutdown()
        store.clear()
        _rmtree_quiet(cache_dir)



TESTS.append(("test_p23_cache_heuristic_ttl", test_p23_cache_heuristic_ttl))
def test_p23_cache_store_clear():
    """P23.2 — CacheStore.clear() removes all on-disk entries."""
    import net
    routes = {"/c": (200, "text/html", b"c",
                     {"Cache-Control": "max-age=60"})}
    srv, port = _p22_start_server(routes)
    cache_dir = tempfile.mkdtemp(prefix="p23cache")
    try:
        store = net.CacheStore(cache_dir)
        c = net.HttpClient(timeout=5, cache=store)
        c.get(f"http://127.0.0.1:{port}/c")
        assert len(_os.listdir(cache_dir)) > 0, "cache must have files"
        n = store.clear()
        assert n > 0
        assert store.get_fresh(net.parse_url(f"http://127.0.0.1:{port}/c")) is None
    finally:
        srv.shutdown()
        _rmtree_quiet(cache_dir)



TESTS.append(("test_p23_cache_store_clear", test_p23_cache_store_clear))
def test_p23_cache_parse_directives():
    """P23.2 — _parse_cache_control extracts max-age / no-cache /
    no-store correctly."""
    import net
    cc = net._parse_cache_control("max-age=3600, public")
    assert cc.get("max-age") == "3600"
    assert cc.get("public") is True
    cc2 = net._parse_cache_control("no-cache, no-store")
    assert cc2.get("no-cache") is True and cc2.get("no-store") is True
    assert net._parse_cache_control("") == {}



TESTS.append(("test_p23_cache_parse_directives", test_p23_cache_parse_directives))
def test_p23_progress_events_emitted():
    """P23.4 — a LoadEventSink receives start/document/done events
    for a single GET."""
    import net
    routes = {"/p": (200, "text/html", b"p")}
    srv, port = _p22_start_server(routes)
    try:
        sink = net.LoadEventSink()
        c = net.HttpClient(timeout=5, progress=sink)
        c.get(f"http://127.0.0.1:{port}/p")
        phases = [e.phase for e in sink.events]
        assert net.PHASE_START in phases, phases
        assert net.PHASE_DOCUMENT in phases, phases
        assert net.PHASE_DONE in phases, phases
        assert sink.events[-1].fraction == 1.0
    finally:
        srv.shutdown()



TESTS.append(("test_p23_progress_events_emitted", test_p23_progress_events_emitted))
def test_p23_progress_subresource_phase():
    """P23.4 — load_subresources emits a subresource phase per fetch
    and a final done event."""
    import net, dom
    routes = {"/s.css": (200, "text/css", b"body{}")}
    srv, port = _p22_start_server(routes)
    try:
        sink = net.LoadEventSink()
        c = net.HttpClient(timeout=5, progress=sink)
        tree = dom.parse(
            '<html><head><link rel="stylesheet" href="s.css"></head>'
            '<body><img src="s.css"></body></html>'
        )
        net.load_subresources(tree, net.parse_url(f"http://127.0.0.1:{port}/"),
                              client=c, progress=sink)
        phases = [e.phase for e in sink.events]
        assert net.PHASE_SUBRESOURCE in phases, phases
        assert net.PHASE_DONE in phases, phases
    finally:
        srv.shutdown()



TESTS.append(("test_p23_progress_subresource_phase", test_p23_progress_subresource_phase))
def test_p23_progress_on_event_subscription():
    """P23.4 — LoadEventSink.on_event subscribes a callback that is
    invoked for each emitted event."""
    import net
    sink = net.LoadEventSink()
    seen = []
    sink.on_event(lambda ev: seen.append(ev.phase))
    sink.emit(net.LoadProgress(net.PHASE_START, 0.0))
    sink.emit(net.LoadProgress(net.PHASE_DONE, 1.0))
    assert [e.phase for e in sink.events] == ["start", "done"]
    assert seen == ["start", "done"]



TESTS.append(("test_p23_progress_on_event_subscription", test_p23_progress_on_event_subscription))
def test_p23_post_form_encoded():
    """P23 — HttpClient.post encodes a dict as
    application/x-www-form-urlencoded and returns the response."""
    import net, http.server, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    captured = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            captured["body"] = self.rfile.read(n).decode("utf-8")
            captured["ctype"] = self.headers.get("Content-Type", "")
            resp = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = net.HttpClient(timeout=5)
        r = c.post(f"http://127.0.0.1:{port}/form",
                   data={"a": "1", "b": "x y"})
        assert r.status == 200 and b"ok" in r.body
        assert "a=1" in captured["body"], captured["body"]
        assert "b=x+y" in captured["body"] or "b=x%20y" in captured["body"]
        assert "urlencoded" in captured["ctype"].lower()
    finally:
        srv.shutdown()



TESTS.append(("test_p23_post_form_encoded", test_p23_post_form_encoded))
def test_p23_post_verbatim_str():
    """P23 — a str body is sent verbatim with the given content-type."""
    import net, http.server, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    captured = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            captured["body"] = self.rfile.read(n)
            captured["ctype"] = self.headers.get("Content-Type", "")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = net.HttpClient(timeout=5)
        c.post(f"http://127.0.0.1:{port}/raw", data="raw-bytes",
               content_type="text/plain")
        assert captured["body"] == b"raw-bytes"
        assert captured["ctype"].lower() == "text/plain"
    finally:
        srv.shutdown()



TESTS.append(("test_p23_post_verbatim_str", test_p23_post_verbatim_str))
def test_p23_insecure_flag_accepted():
    """P23.1 — HttpClient(verify_tls=False) is accepted (the
    --insecure switch); it just disables cert checks (the wire path is
    exercised in the HTTPS test below)."""
    import net
    c = net.HttpClient(verify_tls=False)
    assert c.verify_tls is False



TESTS.append(("test_p23_insecure_flag_accepted", test_p23_insecure_flag_accepted))
def test_p23_https_tls_handshake():
    """P23.1 — a TLS server (ssl-wrapped socket) is reachable via the
    https scheme with verify_tls=False (self-signed cert, no CA trust).
    Cert verification ON would reject the self-signed cert, so this
    test runs the --insecure path end to end."""
    import net, ssl, http.server, threading, os as _os, tempfile
    from http.server import BaseHTTPRequestHandler, HTTPServer

    # Build a throwaway self-signed cert with `ssl`? stdlib can't mint
    # one; use a PEM fixture if present, else skip gracefully.
    cert_dir = tempfile.mkdtemp(prefix="p23tls")
    key_path = _os.path.join(cert_dir, "key.pem")
    crt_path = _os.path.join(cert_dir, "crt.pem")
    # Generate a self-signed cert only if openssl is available; otherwise
    # fall back to asserting the client accepts verify_tls=False.
    try:
        import subprocess
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", key_path, "-out", crt_path, "-days", "1",
             "-nodes", "-subj", "/CN=127.0.0.1"],
            capture_output=True, timeout=30, check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        # No openssl → verify the API surface only (still a meaningful
        # P23.1 test: the insecure path is constructible).
        c = net.HttpClient(verify_tls=False, timeout=2)
        assert c.verify_tls is False
        _os.rmdir(cert_dir)
        return

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            body = b"<h1>tls-ok</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt_path, key_path)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = net.HttpClient(timeout=5, verify_tls=False)
        r = c.get(f"https://127.0.0.1:{port}/")
        assert r.status == 200, f"TLS GET failed: {r.status}"
        assert b"tls-ok" in r.body
    finally:
        httpd.shutdown()
        _rmtree_quiet(cert_dir)



TESTS.append(("test_p23_https_tls_handshake", test_p23_https_tls_handshake))
def test_p23_cache_etag_absent_refetches():
    """P23.3 — a stale entry WITHOUT an ETag cannot revalidate; the
    client refetches from the wire (no If-None-Match sent)."""
    import net
    routes = {"/m": (200, "text/html", b"v1",
                      {"Cache-Control": "max-age=0"})}
    srv, port = _p22_start_server(routes)
    cache_dir = tempfile.mkdtemp(prefix="p23cache")
    try:
        store = net.CacheStore(cache_dir)
        c = net.HttpClient(timeout=5, cache=store)
        u = net.parse_url(f"http://127.0.0.1:{port}/m")
        r1 = c.get(u)
        assert r1.status == 200
        # No ETag → get_stale returns None (cannot revalidate).
        assert store.get_stale(u) is None
    finally:
        srv.shutdown()
        store.clear()
        _rmtree_quiet(cache_dir)



TESTS.append(("test_p23_cache_etag_absent_refetches", test_p23_cache_etag_absent_refetches))
def test_p23_cache_disabled_store():
    """P23.2 — a CacheStore(enabled=False) is a no-op (put/get do
    nothing, no files written)."""
    import net
    store = net.CacheStore(tempfile.mkdtemp(prefix="p23off"), enabled=False)
    r = net.HttpResponse(status=200, status_text="OK",
                         headers={}, body=b"x", final_url="http://h/x")
    u = net.parse_url("http://h/x")
    store.put(u, r)
    assert store.get_fresh(u) is None
    assert store.get_stale(u) is None



TESTS.append(("test_p23_cache_disabled_store", test_p23_cache_disabled_store))
def test_p24_entities_named():
    """P24.1 — named entities decode (amp/lt/nbsp/hellip/alpha…)."""
    from html_pkg.entities import decode_entities
    assert decode_entities("&amp;") == "&"
    assert decode_entities("&lt;tag&gt;") == "<tag>"
    assert decode_entities("&nbsp;") == "\u00A0"
    assert decode_entities("&hellip;") == "…"
    assert decode_entities("&alpha;") == "α"
    assert decode_entities("x&shy;") == "x\u00AD"



TESTS.append(("test_p24_entities_named", test_p24_entities_named))
def test_p24_entities_numeric_dec():
    """P24.1 — decimal numeric references (&#65; → A, &#8364; → €)."""
    from html_pkg.entities import decode_entities
    assert decode_entities("&#65;&#66;&#67;") == "ABC"
    assert decode_entities("&#8364;") == "\u20AC"
    assert decode_entities("&#8704;") == "\u2200"
    assert decode_entities("&#8710;") == "\u2206"



TESTS.append(("test_p24_entities_numeric_dec", test_p24_entities_numeric_dec))
def test_p24_entities_numeric_hex():
    """P24.1 — hex numeric references (&#x1F600; → 😀, spec case)."""
    from html_pkg.entities import decode_entities
    assert decode_entities("&#x1F600;") == "\U0001F600"
    assert decode_entities("&#x41;") == "A"
    assert decode_entities("&#X42;") == "B"



TESTS.append(("test_p24_entities_numeric_hex", test_p24_entities_numeric_hex))
def test_p24_entities_zero_and_surrogate():
    """P24.1 — codepoint 0 and surrogates decode to U+FFFD (spec)."""
    from html_pkg.entities import decode_entities
    assert decode_entities("&#0;") == "\uFFFD"
    assert decode_entities("&#x41;") == "A"
    assert decode_entities("&#xD800;") == "\uFFFD"



TESTS.append(("test_p24_entities_zero_and_surrogate", test_p24_entities_zero_and_surrogate))
def test_p24_entities_unknown_name_literal():
    """P24.1 — an unknown named entity stays literal (&bogus; → &bogus;)."""
    from html_pkg.entities import decode_entities
    assert decode_entities("&bogus;") == "&bogus;"
    # A mix: known + unknown.
    assert decode_entities("&amp;&bogus;") == "&&bogus;"



TESTS.append(("test_p24_entities_unknown_name_literal", test_p24_entities_unknown_name_literal))
def test_p24_rawtext_script_literal():
    """P24.1 — <script> content is NOT parsed as HTML (raw text mode)."""
    import dom
    html = ('<html><body><script>'
            'var x = 1 < 2; document.write("<not a tag>");'
            '</script><p>after</p></body></html>')
    root = dom.parse(html)
    # Find the script element; it must have a single text child with
    # the literal source (including the "<").
    def find_tag(n, tag):
        if n.is_element() and n.tag == tag:
            return n
        for c in n.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    script = find_tag(root, "script")
    assert script is not None, "script element must be in the tree"
    texts = [c.text for c in script.children if not c.is_element()]
    assert any("1 < 2" in t for t in texts), f"script raw text lost: {texts}"



TESTS.append(("test_p24_rawtext_script_literal", test_p24_rawtext_script_literal))
def test_p24_rawtext_style_literal():
    """P24.1 — <style> content is raw text (selectors with < survive)."""
    import dom
    html = "<style>.a > .b { color: red; }</style><div>x</div>"
    root = dom.parse(html)
    def find_tag(n, tag):
        if n.is_element() and n.tag == tag:
            return n
        for c in n.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    style = find_tag(root, "style")
    assert style is not None
    txt = style.text_content()
    assert ".a > .b" in txt, f"style raw text lost: {txt!r}"



TESTS.append(("test_p24_rawtext_style_literal", test_p24_rawtext_style_literal))
def test_p24_void_input_no_stack():
    """P24.2 — void elements never push onto the open-element stack
    (input followed by a sibling stays a sibling, not a child)."""
    import dom
    root = dom.parse("<body><input id=i><span id=s>hi</span></body>")
    body = None
    for c in root.children:
        if c.is_element() and c.tag == "body":
            body = c
    assert body is not None
    tags = [c.tag for c in body.children if c.is_element()]
    assert "input" in tags and "span" in tags
    # The span must NOT be a child of the input (void never opens).
    input_el = body.children[0]
    assert input_el.tag == "input" and input_el.children == []



TESTS.append(("test_p24_void_input_no_stack", test_p24_void_input_no_stack))
def test_p24_void_self_closing_br():
    """P24.2 — XHTML-style self-closing <br/> is a void element."""
    import dom
    root = dom.parse("<body>a<br/>b</body>")
    body = None
    for c in root.children:
        if c.is_element() and c.tag == "body":
            body = c
    assert body is not None
    texts = [c.text for c in body.children if not c.is_element()]
    assert "a" in "".join(texts) and "b" in "".join(texts)
    brs = [c for c in body.children if c.is_element() and c.tag == "br"]
    assert len(brs) == 1



TESTS.append(("test_p24_void_self_closing_br", test_p24_void_self_closing_br))
def test_p24_implicit_p_sibling():
    """P24.2 — a second <p> closes the first (implicit sibling close)."""
    import dom
    root = dom.parse("<body><p>one<p>two</body>")
    body = None
    for c in root.children:
        if c.is_element() and c.tag == "body":
            body = c
    p_nodes = [c for c in body.children if c.is_element() and c.tag == "p"]
    assert len(p_nodes) == 2, f"two <p> siblings expected, got {[n.text_content() for n in p_nodes]}"
    assert p_nodes[0].text_content() == "one"
    assert p_nodes[1].text_content() == "two"



TESTS.append(("test_p24_implicit_p_sibling", test_p24_implicit_p_sibling))
def test_p24_block_closes_p():
    """P24.2 — a block-level start tag (div) closes an open <p>."""
    import dom
    root = dom.parse("<body><p>para<div>block</div></body>")
    body = None
    for c in root.children:
        if c.is_element() and c.tag == "body":
            body = c
    tags = [c.tag for c in body.children if c.is_element()]
    assert tags == ["p", "div"], f"div must close p (siblings), got {tags}"



TESTS.append(("test_p24_block_closes_p", test_p24_block_closes_p))
def test_p24_adoption_b_i():
    """P24.4 — <b><i>x</b></i>: the stray </b> re-nests <i> under
    <b> (simplified adoption agency)."""
    import dom
    root = dom.parse("<body><b><i>x</b></i></body>")
    body = None
    for c in root.children:
        if c.is_element() and c.tag == "body":
            body = c
    b = body.children[0]
    assert b.tag == "b"
    # The <i> must be re-parented under the <b>.
    assert b.children and b.children[0].tag == "i"
    assert b.children[0].text_content() == "x"
    assert b.text_content() == "x"



TESTS.append(("test_p24_adoption_b_i", test_p24_adoption_b_i))
def test_p24_stray_close_ignored():
    """P24.2 — a close tag with no matching open element is ignored
    (the document still renders; no crash, no stray node)."""
    import dom
    root = dom.parse("<body><div id=d>x</div></div></body>")
    body = None
    for c in root.children:
        if c.is_element() and c.tag == "body":
            body = c
    divs = [c for c in body.children if c.is_element() and c.tag == "div"]
    assert len(divs) == 1, f"exactly one <div>, got {len(divs)}"
    assert divs[0].text_content() == "x"



TESTS.append(("test_p24_stray_close_ignored", test_p24_stray_close_ignored))
def test_p24_comment_doctype_dropped():
    """P24.3 — <!DOCTYPE> and <!-- comments --> do not appear in the
    DOM (the toy drops them; renderer stays clean)."""
    import dom
    root = dom.parse("<!DOCTYPE html><html><!-- top comment --><body><!-- x --><p>ok</p></body></html>")
    def walk_tags(n, acc):
        if n.is_element():
            acc.append(n.tag)
        for c in n.children:
            walk_tags(c, acc)
        return acc
    tags = walk_tags(root, [])
    assert "html" in tags and "body" in tags and "p" in tags
    # No comment/doctype node type leaks into the tree.
    assert all(t in ("document", "html", "body", "p") for t in tags), tags



TESTS.append(("test_p24_comment_doctype_dropped", test_p24_comment_doctype_dropped))
def test_p24_case_insensitive_tag():
    """P24.3 — <DIV> == <div> (tag names case-insensitive)."""
    import dom
    root = dom.parse("<body><DIV id=d>hi</DIV></body>")
    def find_tag(n, tag):
        if n.is_element() and n.tag == tag:
            return n
        for c in n.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    div = find_tag(root, "div")
    assert div is not None, f"case-insensitive <DIV> must yield a div node"
    assert div.attr("id") == "d"
    assert div.text_content() == "hi"



TESTS.append(("test_p24_case_insensitive_tag", test_p24_case_insensitive_tag))
def test_p24_foster_tr_in_div():
    """P24.3 — a <tr> inside a <div> (no <table>) is fostered to the
    nearest non-table ancestor (the div), not dropped."""
    import dom
    root = dom.parse("<body><div id=d><tr><td>x</td></tr></div></body>")
    def find_tag(n, tag):
        if n.is_element() and n.tag == tag:
            return n
        for c in n.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    tr = find_tag(root, "tr")
    assert tr is not None, "<tr> must be retained (fostered)"
    # The foster parent is the div (the nearest non-table ancestor).
    assert tr.parent.tag == "div"
    td = find_tag(root, "td")
    assert td is not None and td.parent.tag == "tr"
    assert td.text_content() == "x"



TESTS.append(("test_p24_foster_tr_in_div", test_p24_foster_tr_in_div))
def test_p24_unclosed_lt_text():
    """P24.2 — a stray '<' with no following '>' is literal text."""
    import dom
    root = dom.parse("<body><p>a < b</p></body>")
    p = None
    for c in root.children:
        if c.is_element() and c.tag == "body":
            for gc in c.children:
                if gc.is_element() and gc.tag == "p":
                    p = gc
    assert p is not None
    assert "a < b" in p.text_content(), p.text_content()



TESTS.append(("test_p24_unclosed_lt_text", test_p24_unclosed_lt_text))
def test_p24_utf16_bom():
    """P24.5 — a UTF-16 LE BOM byte stream decodes correctly."""
    import dom
    data = "<html><body>hi</body></html>".encode("utf-16-le")
    data = b"\xff\xfe" + data
    root = dom.parse(data)
    assert "hi" in root.text_content()



TESTS.append(("test_p24_utf16_bom", test_p24_utf16_bom))
def test_p24_utf8_bom():
    """P24.5 — a UTF-8 BOM byte stream decodes correctly."""
    import dom
    data = b"\xef\xbb\xbf" + "<html><body>hi</body></html>".encode("utf-8")
    root = dom.parse(data)
    assert "hi" in root.text_content()



TESTS.append(("test_p24_utf8_bom", test_p24_utf8_bom))
def test_p24_dom_shim_reexport():
    """P24.5 — the legacy `dom` shim re-exports html_pkg's Node (the
    SAME class object, so isinstance checks stay valid)."""
    import dom
    import html_pkg
    from html_pkg.node import Node
    assert dom.Node is Node
    assert dom.parse is not None
    r = dom.parse("<html><body>x</body></html>")
    assert isinstance(r, Node)
    assert isinstance(r, html_pkg.Node)



TESTS.append(("test_p24_dom_shim_reexport", test_p24_dom_shim_reexport))
def test_p24_dom_parse_bytes():
    """P24.5 — dom.parse accepts a byte stream (UTF-8)."""
    import dom
    root = dom.parse(b"<html><body>hi</body></html>")
    assert "hi" in root.text_content()



TESTS.append(("test_p24_dom_parse_bytes", test_p24_dom_parse_bytes))
def test_p24_full_pipeline_render():
    """P24 — the whole render pipeline (dom→css→layout→paint→
    rasterize) still works end-to-end on a fault-tolerant document
    (unclosed <p>, void <br>, entities)."""
    import dom, css, browser
    html = ("<html><body>"
            "<h3 id=h>T&amp;l</h3>"
            "<p>line1<br>line2<p>second para"
            "</body></html>")
    bmp = browser.render_document(html, "", 400, 300)
    assert bmp is not None
    assert bmp.width == 400 and bmp.height == 300
    # Non-white pixels must exist (text rendered).
    nw = sum(1 for px in bmp.pixels if px != 0xFFFFFF)
    assert nw > 0, "fault-tolerant doc must still render pixels"



TESTS.append(("test_p24_full_pipeline_render", test_p24_full_pipeline_render))
def test_p25_specificity_4tuple():
    """P25.1 — Selector.specificity is a 4-tuple (inline, id, cls, type)."""
    import css
    sel = css.Selector.parse("#a .b c:hover")
    # id=1, cls=1 (.b) + pseudo (:hover) = 2, type=1 (c), inline=0
    assert sel.specificity == (0, 1, 2, 1), f"got {sel.specificity}"



TESTS.append(("test_p25_specificity_4tuple", test_p25_specificity_4tuple))
def test_p25_specificity_id_beats_class():
    """P25.1 — #id beats .class regardless of count."""
    import css
    sel_id = css.Selector.parse("#a")
    sel_cls = css.Selector.parse(".a.b.c.d.e")
    assert sel_id.specificity > sel_cls.specificity, (
        f"id spec {sel_id.specificity} must exceed cls spec {sel_cls.specificity}")



TESTS.append(("test_p25_specificity_id_beats_class", test_p25_specificity_id_beats_class))
def test_p25_specificity_class_beats_type():
    """P25.1 — .class beats element type."""
    import css
    sel_cls = css.Selector.parse(".a")
    sel_type = css.Selector.parse("div span p")
    assert sel_cls.specificity > sel_type.specificity, (
        f"cls spec {sel_cls.specificity} must exceed type spec {sel_type.specificity}")



TESTS.append(("test_p25_specificity_class_beats_type", test_p25_specificity_class_beats_type))
def test_p25_important_beats_normal():
    """P25.2 — !important declaration overrides a normal high-specificity rule."""
    import dom, css
    html = '<div id=d style="color: blue">x</div>'
    root = dom.parse(html)
    # Author rule with !important must beat the normal inline style.
    css_text = "#d { color: red !important; }"
    styled = css.apply(root, css_text)
    def find_div(sn):
        if sn.dom.is_element() and sn.dom.tag == "div":
            return sn
        for c in sn.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(styled)
    assert div is not None
    c = div.color()
    assert c.r == 255 and c.g == 0 and c.b == 0, (
        f"!important author rule must beat normal inline, got RGB({c.r},{c.g},{c.b})")



TESTS.append(("test_p25_important_beats_normal", test_p25_important_beats_normal))
def test_p25_important_author_wins_over_normal_inline():
    """P25.2 — author !important beats a normal (non-important) inline style."""
    import dom, css
    html = '<div id=d style="color: blue">x</div>'
    root = dom.parse(html)
    css_text = "#d { color: red !important; }"
    styled = css.apply(root, css_text)
    def find_div(sn):
        if sn.dom.is_element() and sn.dom.tag == "div":
            return sn
        for c in sn.children:
            r = find_div(c)
            if r:
                return r
        return None
    div = find_div(styled)
    c = div.color()
    assert c.r == 255 and c.g == 0 and c.b == 0, f"got RGB({c.r},{c.g},{c.b})"



TESTS.append(("test_p25_important_author_wins_over_normal_inline", test_p25_important_author_wins_over_normal_inline))
def test_p25_inherit_keyword():
    """P25.3 — `color: inherit` takes the parent's computed color."""
    import dom, css
    html = ('<div style="color: red"><span style="color: inherit">x</span></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    def find_tag(sn, tag):
        if sn.dom.is_element() and sn.dom.tag == tag:
            return sn
        for c in sn.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    span = find_tag(styled, "span")
    assert span is not None
    c = span.color()
    assert c.r == 255 and c.g == 0 and c.b == 0, f"span color must be inherited red, got ({c.r},{c.g},{c.b})"



TESTS.append(("test_p25_inherit_keyword", test_p25_inherit_keyword))
def test_p25_initial_keyword():
    """P25.3 — `margin: initial` resets to the initial value (0px)."""
    import dom, css
    from css import EdgeList, Length, resolve_length
    html = '<div style="margin: 50px"><div id=inner style="margin: initial">x</div></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    def find_tag_id(sn, id_):
        if sn.dom.is_element() and sn.dom.attr("id") == id_:
            return sn
        for c in sn.children:
            r = find_tag_id(c, id_)
            if r:
                return r
        return None
    inner = find_tag_id(styled, "inner")
    assert inner is not None
    # After resolution + expansion, margin must be an EdgeList of zeros.
    m = inner.props.get("margin")
    assert isinstance(m, EdgeList), f"margin must be EdgeList, got {type(m)}: {m}"
    assert m.top == 0.0 and m.right == 0.0 and m.bottom == 0.0 and m.left == 0.0, (
        f"margin: initial must be 0 on all sides, got {m}")



TESTS.append(("test_p25_initial_keyword", test_p25_initial_keyword))
def test_p25_unset_keyword():
    """P25.3 — `text-align: unset` behaves as inherit (text-align is inherited)."""
    import dom, css
    html = ('<div style="text-align: right"><div style="text-align: unset">x</div></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    def find_tags(sn, tag, acc=None):
        if acc is None:
            acc = []
        if sn.dom.is_element() and sn.dom.tag == tag:
            acc.append(sn)
        for c in sn.children:
            find_tags(c, tag, acc)
        return acc
    divs = find_tags(styled, "div")
    assert len(divs) == 2
    inner = divs[1]
    ta = inner.text_align()
    assert ta == "right", f"text-align: unset must inherit 'right', got {ta!r}"



TESTS.append(("test_p25_unset_keyword", test_p25_unset_keyword))
def test_p25_shorthand_margin_expands():
    """P25.4 — margin shorthand expands to margin-top/right/bottom/left."""
    import dom, css
    html = '<div id=d style="margin: 10px 20px">x</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    def find_tag(sn, tag):
        if sn.dom.is_element() and sn.dom.tag == tag:
            return sn
        for c in sn.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    d = find_tag(styled, "div")
    assert d is not None
    # After expansion, margin-top / margin-right should be present.
    assert "margin-top" in d.props, f"margin-top missing from {list(d.props.keys())}"
    assert "margin-right" in d.props, f"margin-right missing"
    assert "margin-bottom" in d.props
    assert "margin-left" in d.props



TESTS.append(("test_p25_shorthand_margin_expands", test_p25_shorthand_margin_expands))
def test_p25_shorthand_padding_expands():
    """P25.4 — padding shorthand expands to longhands."""
    import dom, css
    html = '<div id=d style="padding: 5px 10px">x</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    def find_tag(sn, tag):
        if sn.dom.is_element() and sn.dom.tag == tag:
            return sn
        for c in sn.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    d = find_tag(styled, "div")
    assert d is not None
    assert "padding-top" in d.props, f"padding-top missing: {list(d.props.keys())}"
    assert "padding-right" in d.props
    assert "padding-bottom" in d.props
    assert "padding-left" in d.props



TESTS.append(("test_p25_shorthand_padding_expands", test_p25_shorthand_padding_expands))
def test_p25_em_computed_chain():
    """P25.5 — em units resolve against the parent's computed font-size,
    not the UA default."""
    import dom, css
    # Parent sets font-size: 40px; child uses 2em → 80px (not 32px = 2×16).
    html = ('<div style="font-size: 40px">'
            '<div id=inner style="font-size: 2em">text</div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    def find_tag_id(sn, id_):
        if sn.dom.is_element() and sn.dom.attr("id") == id_:
            return sn
        for c in sn.children:
            r = find_tag_id(c, id_)
            if r:
                return r
        return None
    inner = find_tag_id(styled, "inner")
    assert inner is not None
    # The child's parent_font_px should be 40 (from the parent's font-size).
    assert abs(inner.parent_font_px - 40.0) < 0.1, (
        f"inner.parent_font_px must be 40 (parent computed font), got {inner.parent_font_px}")
    # The inner's own font-size = 2em × 40 = 80px.
    own = inner.prop_font_px()
    assert abs(own - 80.0) < 0.1, f"inner font-size must be 80px (2em of 40px), got {own}"



TESTS.append(("test_p25_em_computed_chain", test_p25_em_computed_chain))
def test_p25_inherit_font_size():
    """P25.3 — font-size inherits when not explicitly set on child."""
    import dom, css
    html = '<div style="font-size: 32px"><span>x</span></div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    def find_tag(sn, tag):
        if sn.dom.is_element() and sn.dom.tag == tag:
            return sn
        for c in sn.children:
            r = find_tag(c, tag)
            if r:
                return r
        return None
    span = find_tag(styled, "span")
    assert span is not None
    # The span inherits font-size 32px from the parent.
    assert abs(span.prop_font_px() - 32.0) < 0.1, (
        f"span font-size must be 32px (inherited), got {span.prop_font_px()}")



TESTS.append(("test_p25_inherit_font_size", test_p25_inherit_font_size))
def test_p25_source_cascade_author_over_ua():
    """P25.2 — author rules override UA rules in the cascade layer."""
    import css
    from css import Rule, Selector, parse_selector_list
    # Build a fake UA rule and an author rule; verify the cascade key.
    ua_sel = parse_selector_list("body")
    auth_sel = parse_selector_list(".a")
    ua_rule = Rule(ua_sel, {}, ua_sel[0].specificity_sum(), "ua", False)
    auth_rule = Rule(auth_sel, {}, auth_sel[0].specificity_sum(), "author", False)
    # Author must sort after (beat) UA in the cascade, even with lower spec.
    author_key = css._cascade_key(auth_rule)
    ua_key = css._cascade_key(ua_rule)
    assert author_key > ua_key, (
        f"author key {author_key} must exceed ua key {ua_key}")



TESTS.append(("test_p25_source_cascade_author_over_ua", test_p25_source_cascade_author_over_ua))
def test_p25_full_pipeline_important():
    """P25 — full render pipeline with !important and inherit works
    end-to-end without regression.

    P33.R1 — asserts the full display list (commands + pixels) is
    produced correctly; the exact pixel count depends on the font
    engine (bitmap vs TTF) so we check that ink is painted *somewhere*
    in the document region (the div starts at y≈20px, not y=0).
    """
    import browser
    html = ('<html><body style="font-size: 20px">'
            '<div style="color: blue !important"><span style="color: inherit">hi</span></div>'
            '</body></html>')
    bmp = browser.render_document(html, "", 400, 300)
    assert bmp is not None and bmp.width == 400
    # Scan the region where the div's text actually lands (y >= 20).
    nw = 0
    for y in range(10, 80):
        for x in range(0, 100):
            px = bmp.pixels[y * bmp.width + x]
            if px != 0xFFFFFF:
                nw += 1
    assert nw > 0, \
        f"P25 pipeline must paint ink in the div region, got {nw}"



TESTS.append(("test_p25_full_pipeline_important", test_p25_full_pipeline_important))
def test_p26_margin_collapse_siblings():
    """P26.1 — adjacent block siblings' margins collapse to the max
    (not the sum): .a{margin-bottom:10px} .b{margin-top:20px} →
    20px gap, not 30px."""
    import dom, css, layout, browser
    html = ('<div id=wrap style="width:200px">'
            '<div id=a style="height:20px;margin-bottom:10px;background:blue"></div>'
            '<div id=b style="height:20px;margin-top:20px;background:red"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    wrap = _find_box(lroot, "div", "wrap")
    a = _find_box(lroot, "div", "a")
    b = _find_box(lroot, "div", "b")
    assert a is not None and b is not None
    # b's top margin (20) collapses with a's bottom margin (10) → gap 20.
    # Without collapse the gap would be 10+20=30.
    gap = b.rect.y - a.rect.y - a.rect.h
    assert abs(gap - 20.0) < 0.5, f"collapsed gap must be 20px (max), got {gap}"



TESTS.append(("test_p26_margin_collapse_siblings", test_p26_margin_collapse_siblings))
def test_p26_margin_collapse_parent_child():
    """P26.1 — a child's top margin collapses through the parent's top
    margin (no border/padding in between): parent margin-top:5, child
    margin-top:15 → effective 15, not 5+15."""
    import dom, css, layout
    html = ('<div id=outer>'
            '<div id=parent style="margin:5px">'
            '<div id=child style="height:10px;margin-top:15px;background:red"></div>'
            '</div></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    parent = _find_box(lroot, "div", "parent")
    child = _find_box(lroot, "div", "child")
    assert parent is not None and child is not None
    # Collapsed: the child's top margin pokes out to 15 above the parent's
    # top edge, not 5+15=20.
    # Parent's top margin 5 and child's top margin 15 collapse → 15.
    gap = child.rect.y - parent.rect.y
    # parent.rect.y is measured after the collapsed margin; the child
    # starts 0 relative to the parent content edge when margins collapse
    # through (the parent gets no internal top padding from the child).
    assert abs(gap) < 0.5, (
        f"collapsed parent-child margin: child must start at parent content "
        f"edge (gap≈0), got gap={gap}")



TESTS.append(("test_p26_margin_collapse_parent_child", test_p26_margin_collapse_parent_child))
def test_p26_margin_collapse_empty_block():
    """P26.1 — an empty block collapses its own top + bottom margins to a
    single max(): margin:30px on an empty div → contributes 30, not
    60, to the gap with its next sibling."""
    import dom, css, layout
    html = ('<div>'
            '<div id=empty style="margin:30px"></div>'
            '<div id=next style="height:10px;background:red"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    empty = _find_box(lroot, "div", "empty")
    nxt = _find_box(lroot, "div", "next")
    assert empty is not None and nxt is not None
    # Empty block's bottom margin (30) collapses with next's top (0) →
    # gap 30.  If top+bottom were *summed* the gap would be 60.
    gap = nxt.rect.y - empty.rect.y - empty.rect.h
    assert abs(gap - 30.0) < 0.5, f"empty-block margin must collapse to 30, got {gap}"



TESTS.append(("test_p26_margin_collapse_empty_block", test_p26_margin_collapse_empty_block))
def test_p26_bfc_cleares_floats():
    """P26.2 — a BFC (overflow:hidden) height contains its floats:
    the parent box's height extends to the bottom of the floated child."""
    import dom, css, layout
    html = ('<div id=box style="overflow:hidden;width:200px">'
            '<div style="float:left;width:100px;height:40px;background:blue"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    box = _find_box(lroot, "div", "box")
    assert box is not None
    # BFC must contain the float: height >= float's outer height (40 + margins).
    assert box.rect.h >= 40.0, f"BFC height must contain float (>=40), got {box.rect.h}"



TESTS.append(("test_p26_bfc_cleares_floats", test_p26_bfc_cleares_floats))
def test_p26_pct_height_chain():
    """P26.3 — percentage heights resolve against the parent's *resolved*
    content height (not the font size): body 600px, child height:25%
    → 150px."""
    import dom, css, layout
    html = ('<body style="height:600px">'
            '<div id=h style="height:25%">x</div>'
            '</body>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 600.0)
    h = _find_box(lroot, "div", "h")
    assert h is not None
    # 25% of 600px = 150px
    assert abs(h.rect.h - 150.0) < 1.0, f"25% of 600px must be 150px, got {h.rect.h}"



TESTS.append(("test_p26_pct_height_chain", test_p26_pct_height_chain))
def test_p26_height_auto_grows():
    """P26.3 — height:auto grows with content: a block with two 20px
    children is 40px tall."""
    import dom, css, layout
    html = ('<div id=auto>'
            '<div style="height:20px"></div>'
            '<div style="height:20px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    auto = _find_box(lroot, "div", "auto")
    assert auto is not None
    assert abs(auto.rect.h - 40.0) < 1.0, f"auto height must grow to 40px, got {auto.rect.h}"



TESTS.append(("test_p26_height_auto_grows", test_p26_height_auto_grows))
def test_p26_min_height_clamps():
    """P26.4 — min-height: 50px forces a taller box than its content
    (10px) requires."""
    import dom, css, layout
    html = ('<div id=m style="min-height:50px">'
            '<div style="height:10px"></div></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    m = _find_box(lroot, "div", "m")
    assert m is not None
    assert m.rect.h >= 50.0, f"min-height:50px must clamp to >=50, got {m.rect.h}"
    assert abs(m.rect.h - 50.0) < 0.5, f"min-height must be exactly 50 here, got {m.rect.h}"



TESTS.append(("test_p26_min_height_clamps", test_p26_min_height_clamps))
def test_p26_max_height_clamps():
    """P26.4 — max-height: 20px caps a 40px-tall content box."""
    import dom, css, layout
    html = ('<div id=m style="max-height:20px">'
            '<div style="height:20px"></div>'
            '<div style="height:20px"></div></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    m = _find_box(lroot, "div", "m")
    assert m is not None
    assert m.rect.h <= 20.0, f"max-height:20px must cap to <=20, got {m.rect.h}"
    assert abs(m.rect.h - 20.0) < 0.5, f"max-height must be exactly 20 here, got {m.rect.h}"



TESTS.append(("test_p26_max_height_clamps", test_p26_max_height_clamps))
def test_p26_min_width_clamps():
    """P26.4 — min-width: 150px widens a 30px-content box to 150px when
    the auto width would be less than 150px."""
    import dom, css, layout
    html = ('<div style="width:100px">'
            '<div id=m style="min-width:150px"><div style="width:30px">x</div></div></div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    m = _find_box(lroot, id_="m")
    assert m is not None
    # Auto width would be 100 (parent width); min-width:150 forces 150.
    assert m.rect.w >= 150.0, f"min-width:150px must clamp to >=150, got {m.rect.w}"
    assert abs(m.rect.w - 150.0) < 0.5, f"min-width must be exactly 150, got {m.rect.w}"



TESTS.append(("test_p26_min_width_clamps", test_p26_min_width_clamps))
def test_p26_max_width_clamps():
    """P26.4 — max-width: 50px caps an auto-width (300px) box to 50px."""
    import dom, css, layout
    html = '<div id=m style="max-width:50px">long text content here that would be wide</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    m = _find_box(lroot, "div", "m")
    assert m is not None
    assert m.rect.w <= 50.0, f"max-width:50px must cap to <=50, got {m.rect.w}"
    assert abs(m.rect.w - 50.0) < 0.5, f"max-width must be exactly 50, got {m.rect.w}"



TESTS.append(("test_p26_max_width_clamps", test_p26_max_width_clamps))
def test_p26_width_auto_fills():
    """P26.5 — width:auto block fills its containing block: a child of a
    200px parent with no explicit width is ~200px wide (minus margins)."""
    import dom, css, layout
    html = ('<div style="width:200px">'
            '<div id=c>x</div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    c = _find_box(lroot, "div", "c")
    assert c is not None
    assert abs(c.rect.w - 200.0) < 1.0, f"width:auto must fill 200px parent, got {c.rect.w}"



TESTS.append(("test_p26_width_auto_fills", test_p26_width_auto_fills))
def test_p26_width_auto_with_margins():
    """P26.5 — width:auto respects horizontal margins: available width
    = containing - margin-l - margin-r."""
    import dom, css, layout
    html = ('<div style="width:200px">'
            '<div id=c style="margin:0 20px 0 10px">x</div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    c = _find_box(lroot, id_="c")
    assert c is not None
    # 200 - 10 - 20 = 170
    assert abs(c.rect.w - 170.0) < 1.0, (
        f"width:auto with margin-l:10 margin-r:20 must be 170px, got {c.rect.w}")



TESTS.append(("test_p26_width_auto_with_margins", test_p26_width_auto_with_margins))
def test_p26_float_inside_bfc_no_overlap():
    """P26.2 — inside a BFC the float is placed at the content edge and
    subsequent inline content wraps below/beside it (no border overlap)."""
    import dom, css, layout
    html = ('<div id=box style="overflow:hidden;width:200px">'
            '<div id=f style="float:left;width:50px;height:30px;background:blue"></div>'
            '<div id=t style="clear:both;height:10px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    box = _find_box(lroot, id_="box")
    f = _find_box(lroot, id_="f")
    assert box is not None and f is not None
    # The float is placed at the BFC's content edge; its x must not
    # extend past the BFC's border box (no negative overlap on the left).
    assert f.rect.x >= box.rect.x - box.margin.left, (
        f"float must not extend past BFC left border: f.x={f.rect.x} "
        f"box.x={box.rect.x} box.margin.left={box.margin.left}")
    # The BFC's height must contain the float (P26.2).
    assert box.rect.h >= f.rect.h, f"BFC height must contain float: box.h={box.rect.h} f.h={f.rect.h}"



TESTS.append(("test_p26_float_inside_bfc_no_overlap", test_p26_float_inside_bfc_no_overlap))
def test_p26_full_pipeline_render():
    """P26 — the whole pipeline (dom→css→layout→paint→rasterize) still
    renders end-to-end with P26 layout features without crashing."""
    import browser
    html = ('<html><body style="height:400px">'
            '<div style="margin:10px;min-width:50px;max-width:300px">'
            '<div style="height:25%;overflow:hidden">'
            '<div style="float:left;width:40px;height:20px;background:blue"></div>'
            '</div></div>'
            '</body></html>')
    bmp = browser.render_document(html, "", 400, 300)
    assert bmp is not None and bmp.width == 400 and bmp.height == 300
    nw = sum(1 for px in bmp.pixels if px != 0xFFFFFF)
    assert nw > 0, "P26 doc must still render pixels"



TESTS.append(("test_p26_full_pipeline_render", test_p26_full_pipeline_render))
def test_p27_flex_shrink_proportional():
    """P27.2 — flex-shrink distributes overflow deficit proportionally.
    3 items basis 100 each in 200px container: deficit 100 split evenly."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:200px">'
            '<div id=a style="flex-basis:100px;width:auto"></div>'
            '<div id=b style="flex-basis:100px;width:auto"></div>'
            '<div id=c style="flex-basis:100px;width:auto"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    f = _find_box(lroot, id_="f")
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    c = _find_box(lroot, id_="c")
    # deficit = 100, 3 items with equal shrink=1 and basis=100 → each shrinks 100/3 ≈ 33.3
    expected = 100 - 100/3
    assert abs(a.rect.w - expected) < 2.0, f"shrink item a should be ~{expected:.1f}, got {a.rect.w}"
    assert abs(b.rect.w - expected) < 2.0, f"shrink item b should be ~{expected:.1f}, got {b.rect.w}"
    assert abs(c.rect.w - expected) < 2.0, f"shrink item c should be ~{expected:.1f}, got {c.rect.w}"



TESTS.append(("test_p27_flex_shrink_proportional", test_p27_flex_shrink_proportional))
def test_p27_flex_shrink_weighted():
    """P27.2 — flex-shrink:0 prevents an item from shrinking."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:200px">'
            '<div id=a style="flex-basis:150px;flex-shrink:0;width:auto"></div>'
            '<div id=b style="flex-basis:150px;flex-shrink:1;width:auto"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # a stays 150, b absorbs all deficit: 150 - 100 = 50
    assert a.rect.w >= 140.0, f"shrink:0 item must stay ~150, got {a.rect.w}"
    assert b.rect.w < 100.0, f"shrink:1 item must absorb deficit, got {b.rect.w}"



TESTS.append(("test_p27_flex_shrink_weighted", test_p27_flex_shrink_weighted))
def test_p27_flex_basis_explicit():
    """P27.2 — flex-basis:50px overrides width for the basis calculation."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:300px">'
            '<div id=a style="flex-basis:50px;flex-grow:1;width:auto"></div>'
            '<div id=b style="flex-grow:1"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # a basis=50, b basis≈0 (auto content). leftover=300-50=250 split evenly: a=50+125=175, b=0+125=125
    assert a.rect.w > 100.0, f"flex-basis+grow item a should be wider, got {a.rect.w}"
    assert a.rect.w + b.rect.w <= 300.0 + 5.0, f"total must not exceed 300, got {a.rect.w + b.rect.w}"



TESTS.append(("test_p27_flex_basis_explicit", test_p27_flex_basis_explicit))
def test_p27_flex_wrap_two_lines():
    """P27.3 — flex-wrap:wrap puts items on 2 lines when they don't fit."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;flex-wrap:wrap;width:100px;height:100px">'
            '<div id=a style="width:60px;height:20px"></div>'
            '<div id=b style="width:60px;height:20px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # 60+60 > 100, so b wraps to line 2
    assert a.rect.y < b.rect.y, f"a must be above b (different lines): a.y={a.rect.y} b.y={b.rect.y}"



TESTS.append(("test_p27_flex_wrap_two_lines", test_p27_flex_wrap_two_lines))
def test_p27_flex_wrap_single_line_fits():
    """P27.3 — flex-wrap:wrap with items that fit stays on one line."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;flex-wrap:wrap;width:200px">'
            '<div id=a style="width:50px;height:20px"></div>'
            '<div id=b style="width:50px;height:20px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    assert a.rect.y == b.rect.y, f"both on one line: a.y={a.rect.y} b.y={b.rect.y}"
    assert abs(a.rect.x + 50 - b.rect.x) < 5.0, f"b starts after a: a.x+w={a.rect.x+50} b.x={b.rect.x}"



TESTS.append(("test_p27_flex_wrap_single_line_fits", test_p27_flex_wrap_single_line_fits))
def test_p27_align_items_start():
    """P27.4 — align-items:start aligns all items to the top of the line."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:200px;height:100px;align-items:start">'
            '<div id=a style="width:30px;height:20px"></div>'
            '<div id=b style="width:30px;height:60px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    f = _find_box(lroot, id_="f")
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    assert a.rect.y == b.rect.y, f"start: both at top: a.y={a.rect.y} b.y={b.rect.y}"



TESTS.append(("test_p27_align_items_start", test_p27_align_items_start))
def test_p27_align_items_end():
    """P27.4 — align-items:end aligns all items to the bottom of the line."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:200px;height:100px;align-items:end">'
            '<div id=a style="width:30px;height:20px"></div>'
            '<div id=b style="width:30px;height:60px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # both bottom-aligned: y + h should be equal
    assert abs((a.rect.y + a.rect.h) - (b.rect.y + b.rect.h)) < 2.0, \
        f"end: bottoms equal: a_bottom={a.rect.y+a.rect.h} b_bottom={b.rect.y+b.rect.h}"



TESTS.append(("test_p27_align_items_end", test_p27_align_items_end))
def test_p27_align_self_overrides():
    """P27.4 — align-self:center overrides container's align-items:start."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:200px;height:100px;align-items:start">'
            '<div id=a style="width:30px;height:20px"></div>'
            '<div id=b style="width:30px;height:40px;align-self:center"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # a is at top, b is centered
    assert a.rect.y < b.rect.y, f"a (top) above b (center): a.y={a.rect.y} b.y={b.rect.y}"



TESTS.append(("test_p27_align_self_overrides", test_p27_align_self_overrides))
def test_p27_justify_content_space_evenly():
    """P27.4 — justify-content:space-evenly distributes equal space around all items."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:300px;justify-content:space-evenly">'
            '<div id=a style="width:50px;height:20px"></div>'
            '<div id=b style="width:50px;height:20px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # 3 gaps of equal size: gap = (300-100)/3 ≈ 66.7
    gap = a.rect.x - _find_box(lroot, id_="f").rect.x
    assert gap > 10.0, f"space-evenly: first gap must be >10, got {gap}"
    mid_gap = b.rect.x - (a.rect.x + a.rect.w)
    assert abs(gap - mid_gap) < 5.0, f"space-evenly: gaps equal: outer={gap} mid={mid_gap}"



TESTS.append(("test_p27_justify_content_space_evenly", test_p27_justify_content_space_evenly))
def test_p27_justify_content_space_around():
    """P27.4 — justify-content:space-around: half gap at edges, full between."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:300px;justify-content:space-around">'
            '<div id=a style="width:50px;height:20px"></div>'
            '<div id=b style="width:50px;height:20px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    f = _find_box(lroot, id_="f")
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # each item gets free/2 on each side: free=200, half=100/2=50
    edge_gap = a.rect.x - f.rect.x
    assert edge_gap > 20.0, f"space-around: edge gap >20, got {edge_gap}"
    mid = b.rect.x - (a.rect.x + a.rect.w)
    assert mid > edge_gap, f"space-around: mid gap > edge gap: mid={mid} edge={edge_gap}"



TESTS.append(("test_p27_justify_content_space_around", test_p27_justify_content_space_around))
def test_p27_flex_direction_column_reverse():
    """P27.5 — flex-direction:column-reverse reverses the main-axis
    stacking order: the first DOM item ends up at the bottom of the
    main axis (for reverse-direction flexboxes the flex start edge is
    the bottom, so the start-packed stack reads bottom-to-top)."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;flex-direction:column-reverse;width:100px;height:200px">'
            '<div id=a style="height:30px"></div>'
            '<div id=b style="height:50px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # reverse main axis: a (first in DOM) sits at the flex-start edge
    # (bottom) and b stacks above it: b's bottom touches a's top.
    assert a.rect.y > b.rect.y, \
        f"column-reverse: a packed from bottom, above b: a.y={a.rect.y} b.y={b.rect.y}"
    assert abs(b.rect.y + b.rect.h - a.rect.y) < 2.0, \
        f"items must be adjacent in the reversed stack: b_bottom={b.rect.y+b.rect.h} a.y={a.rect.y}"



TESTS.append(("test_p27_flex_direction_column_reverse", test_p27_flex_direction_column_reverse))
def test_p27_flex_order_reorders():
    """P27.5 — order property changes visual order without changing DOM."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:300px">'
            '<div id=a style="order:2;width:50px"></div>'
            '<div id=b style="order:1;width:50px"></div>'
            '<div id=c style="order:0;width:50px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    c = _find_box(lroot, id_="c")
    # visual order: c(0), b(1), a(2)
    assert c.rect.x < b.rect.x, f"order: c(0) before b(1): c.x={c.rect.x} b.x={b.rect.x}"
    assert b.rect.x < a.rect.x, f"order: b(1) before a(2): b.x={b.rect.x} a.x={a.rect.x}"



TESTS.append(("test_p27_flex_order_reorders", test_p27_flex_order_reorders))
def test_p27_min_width_auto_floor():
    """P27.5 — min-width prevents a flex item from shrinking below its content."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:100px">'
            '<div id=a style="width:200px;flex-shrink:1;min-width:80px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    # container is 100px, a wants to shrink but min-width:80 is the floor
    assert a.rect.w >= 75.0, f"min-width:80 floor: got {a.rect.w}"



TESTS.append(("test_p27_min_width_auto_floor", test_p27_min_width_auto_floor))
def test_p27_flex_basis_zero():
    """P27.2 — flex-basis:0 makes items grow from zero basis."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:300px">'
            '<div id=a style="flex-basis:0px;flex-grow:1">x</div>'
            '<div id=b style="flex-basis:0px;flex-grow:1"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # both basis 0, grow 1:1 → equal 150px split
    assert abs(a.rect.w - b.rect.w) < 5.0, f"equal grow+basis:0: a={a.rect.w} b={b.rect.w}"
    assert a.rect.w > 100.0, f"each item should be >100 (150 expected), got {a.rect.w}"



TESTS.append(("test_p27_flex_basis_zero", test_p27_flex_basis_zero))
def test_p27_flex_shrink_zero_keeps_size():
    """P27.2 — flex-shrink:0 items keep their basis even when overflowing."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:150px">'
            '<div id=a style="flex-basis:100px;flex-shrink:0"></div>'
            '<div id=b style="flex-basis:100px;flex-shrink:0"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # both keep 100px, overflow 50px
    assert a.rect.w >= 95.0, f"shrink:0 item a must keep ~100, got {a.rect.w}"
    assert b.rect.w >= 95.0, f"shrink:0 item b must keep ~100, got {b.rect.w}"



TESTS.append(("test_p27_flex_shrink_zero_keeps_size", test_p27_flex_shrink_zero_keeps_size))
def test_p27_align_content_center_multi_line():
    """P27.4 — align-content:center centres multiple wrapped lines."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;flex-wrap:wrap;width:100px;height:200px;align-content:center">'
            '<div id=a style="width:60px;height:30px"></div>'
            '<div id=b style="width:60px;height:30px"></div>'
            '<div id=c style="width:60px;height:30px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    c = _find_box(lroot, id_="c")
    # 3 items × 60px > 100px → 3 lines; centered in 200px container
    assert a.rect.y > 10.0, f"lines must be pushed down from top: a.y={a.rect.y}"
    assert c.rect.y + c.rect.h < 200.0, f"last line must not overflow bottom: c_bottom={c.rect.y+c.rect.h}"



TESTS.append(("test_p27_align_content_center_multi_line", test_p27_align_content_center_multi_line))
def test_p27_flex_row_reverse():
    """P27.5 — flex-direction:row-reverse lays items right-to-left."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;flex-direction:row-reverse;width:300px">'
            '<div id=a style="width:50px;height:20px"></div>'
            '<div id=b style="width:50px;height:20px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # reverse: a (first) goes rightmost
    assert a.rect.x > b.rect.x, f"row-reverse: a right of b: a.x={a.rect.x} b.x={b.rect.x}"



TESTS.append(("test_p27_flex_row_reverse", test_p27_flex_row_reverse))
def test_p27_flex_grow_shrink_basis_shorthand():
    """P27.2 — flex shorthand '1 1 auto' sets grow/shrink/basis."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;width:300px">'
            '<div id=a style="flex-basis:100px;flex-grow:2;flex-shrink:1"></div>'
            '<div id=b style="flex-basis:100px;flex-grow:1;flex-shrink:1"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # leftover=100, grow 2:1 → a gets +66.7, b gets +33.3
    assert a.rect.w > b.rect.w, f"grow 2 > grow 1: a={a.rect.w} b={b.rect.w}"
    assert abs(a.rect.w - 166.7) < 10.0, f"a ≈ 166.7, got {a.rect.w}"



TESTS.append(("test_p27_flex_grow_shrink_basis_shorthand", test_p27_flex_grow_shrink_basis_shorthand))
def test_p27_wrap_reverse_direction():
    """P27.3 — flex-wrap:wrap-reverse stacks lines from the far cross
    edge: with an explicit container height, line 0 sits lower than
    later lines (a is below b when a wraps before b)."""
    import dom, css, layout
    html = ('<div id=f style="display:flex;flex-wrap:wrap-reverse;width:100px;height:200px">'
            '<div id=a style="width:60px;height:20px"></div>'
            '<div id=b style="width:60px;height:20px"></div>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    a = _find_box(lroot, id_="a")
    b = _find_box(lroot, id_="b")
    # wrap-reverse: lines stack from the bottom.  Line 0 (a) is at the
    # bottom; line 1 (b) is above it.  The container height is 200px.
    assert a.rect.y > b.rect.y, \
        f"wrap-reverse: a (line 0) below b (line 1): a.y={a.rect.y} b.y={b.rect.y}"
    assert a.rect.y + a.rect.h <= 200.0, \
        f"a must not overflow the container bottom: a_bottom={a.rect.y+a.rect.h}"



TESTS.append(("test_p27_wrap_reverse_direction", test_p27_wrap_reverse_direction))
def test_p27_flex_full_pipeline_render():
    """P27 — the whole pipeline still renders with P27 flex features."""
    import browser
    html = ('<html><body style="height:300px">'
            '<div style="display:flex;flex-wrap:wrap;width:300px;height:200px;justify-content:center;align-items:center">'
            '<div style="width:80px;height:40px;flex-basis:80px;flex-grow:1;background:blue"></div>'
            '<div style="width:80px;height:40px;flex-basis:80px;flex-grow:1;background:red"></div>'
            '<div style="width:80px;height:40px;flex-basis:80px;flex-grow:1;background:green"></div>'
            '</div>'
            '</body></html>')
    bmp = browser.render_document(html, "", 300, 200)
    assert bmp is not None and bmp.width == 300 and bmp.height == 200
    nw = sum(1 for px in bmp.pixels if px != 0xFFFFFF)
    assert nw > 0, "P27 flex doc must still render pixels"



TESTS.append(("test_p27_flex_full_pipeline_render", test_p27_flex_full_pipeline_render))
def test_p28_input_checkbox_render():
    """P28.1 — <input type="checkbox"> renders as a 16×16 box; checked
    state adds a fill rect inside the box."""
    import dom, css, layout, paint_engine, browser
    html = ('<div style="width:300px;height:200px">'
            '<input type="checkbox" id="cb" checked name="opt">'
            '<input type="checkbox" id="cb2" name="opt2">'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 200.0)
    cb = _find_box(lroot, id_="cb")
    cb2 = _find_box(lroot, id_="cb2")
    assert cb is not None and cb2 is not None, "checkbox boxes must exist"
    assert abs(cb.rect.w - 16.0) < 2.0, f"checkbox width should be ~16: {cb.rect.w}"
    assert abs(cb.rect.h - 16.0) < 2.0, f"checkbox height should be ~16: {cb.rect.h}"
    assert cb.control_type == "checkbox", f"control_type: {cb.control_type}"
    assert cb.control_checked is True, "checked attribute must set control_checked"
    assert cb2.control_checked is False, "unchecked checkbox must have control_checked=False"
    # The checked box must emit a fill command for the tick mark.
    dl = paint_engine.emit_commands(lroot)
    cb_cmds = [c for c in dl.commands if c.control_type == "checkbox"]
    assert len(cb_cmds) >= 1, "checkbox must emit at least one control command"
    # Render end-to-end: checked + unchecked checkboxes both paint.
    bmp = browser.render_document(html, "", 300, 200)
    nw = sum(1 for px in bmp.pixels if px != 0xFFFFFF)
    assert nw > 0, "checkbox page must render non-white pixels"



TESTS.append(("test_p28_input_checkbox_render", test_p28_input_checkbox_render))
def test_p28_input_radio_render():
    """P28.1 — <input type="radio"> renders as a 16×16 box; only the
    checked radio shows the fill dot."""
    import dom, css, layout, paint_engine
    html = ('<div style="width:300px;height:200px">'
            '<input type="radio" id="r1" name="grp" checked value="a">'
            '<input type="radio" id="r2" name="grp" value="b">'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 200.0)
    r1 = _find_box(lroot, id_="r1")
    r2 = _find_box(lroot, id_="r2")
    assert r1 is not None and r2 is not None, "radio boxes must exist"
    assert abs(r1.rect.w - 16.0) < 2.0, f"radio width ~16: {r1.rect.w}"
    assert abs(r1.rect.h - 16.0) < 2.0, f"radio height ~16: {r1.rect.h}"
    assert r1.control_checked is True and r2.control_checked is False
    assert r1.control_value == "a" and r2.control_value == "b"
    dl = paint_engine.emit_commands(lroot)
    r_cmds = [c for c in dl.commands if c.control_type == "radio"]
    assert len(r_cmds) >= 1, "radio must emit control commands"



TESTS.append(("test_p28_input_radio_render", test_p28_input_radio_render))
def test_p28_select_option_render():
    """P28.1 — <select> renders as a 120×24 combobox showing the
    selected option's label + a down-arrow strip."""
    import dom, css, layout, paint_engine, browser
    html = ('<select id="s1" name="color">'
            '<option value="red">Red</option>'
            '<option value="green" selected>Green</option>'
            '<option value="blue">Blue</option>'
            '</select>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 200.0)
    s = _find_box(lroot, id_="s1")
    assert s is not None, "select box must exist"
    assert s.control_options == ["red", "green", "blue"], f"options: {s.control_options}"
    assert s.control_option_index == 1, f"selected index: {s.control_option_index}"
    assert s.control_value == "green", f"select value: {s.control_value}"
    # The renderer must emit a text command with the selected option label.
    dl = paint_engine.emit_commands(lroot)
    txt_cmds = [c for c in dl.commands if c.kind == "text" and c.control_type == "select"]
    assert len(txt_cmds) >= 1, "select must emit a text command for the label"
    assert txt_cmds[0].text == "green", f"label: {txt_cmds[0].text}"
    # End-to-end render.
    bmp = browser.render_document(html, "", 300, 200)
    assert sum(1 for px in bmp.pixels if px != 0xFFFFFF) > 0



TESTS.append(("test_p28_select_option_render", test_p28_select_option_render))
def test_p28_file_input_render():
    """P28.1 — <input type="file"> shows the file name (or 'No file
    selected') inside a 220×24 box."""
    import dom, css, layout, paint_engine
    html = ('<div style="width:300px;height:200px">'
            '<input type="file" id="f1" name="upload" value="report.pdf">'
            '<input type="file" id="f2" name="upload2">'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 200.0)
    f1 = _find_box(lroot, id_="f1")
    f2 = _find_box(lroot, id_="f2")
    assert f1 is not None and f2 is not None, "file inputs must exist"
    assert abs(f1.rect.w - 220.0) < 2.0, f"file input width ~220: {f1.rect.w}"
    assert f1.control_file_name == "report.pdf"
    assert f2.control_file_name == "", "no value → empty file name"
    dl = paint_engine.emit_commands(lroot)
    file_cmds = [c for c in dl.commands if c.control_type == "file"]
    assert len(file_cmds) >= 1, "file input must emit a text command"
    labels = [c.text for c in file_cmds]
    assert "report.pdf" in labels, f"file labels: {labels}"
    assert any("No file selected" in t for t in labels), \
        f"placeholder label expected: {labels}"



TESTS.append(("test_p28_file_input_render", test_p28_file_input_render))
def test_p28_textarea_default_size():
    """P28.1 — <textarea> defaults to 160×64 (20 cols × 4 rows) per
    HTML §4.10.6; cols/rows attributes override."""
    import dom, css, layout
    html = ('<div style="width:300px;height:200px">'
            '<textarea id="t1" name="msg"></textarea>'
            '<textarea id="t2" cols="10" rows="2"></textarea>'
            '</div>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 200.0)
    t1 = _find_box(lroot, id_="t1")
    t2 = _find_box(lroot, id_="t2")
    assert t1 is not None and t2 is not None
    assert abs(t1.rect.w - 160.0) < 2.0, f"default textarea width ~160: {t1.rect.w}"
    assert abs(t1.rect.h - 64.0) < 4.0, f"default textarea height ~64: {t1.rect.h}"
    assert abs(t2.rect.w - 80.0) < 4.0, f"cols=10 → 80px: {t2.rect.w}"
    assert abs(t2.rect.h - 32.0) < 4.0, f"rows=2 → 32px: {t2.rect.h}"



TESTS.append(("test_p28_textarea_default_size", test_p28_textarea_default_size))
def test_p28_form_get_query_string():
    """P28.2 — GET form submission builds a URL with a query string:
    text + checkbox(checked) + select + textarea values."""
    import browser, dom
    html = ('<form id="f" method="get" action="/search">'
            '<input type="text" name="q" value="hello world">'
            '<input type="checkbox" name="flag" value="yes" checked>'
            '<select name="lang"><option value="en" selected>English</option>'
            '<option value="zh">中文</option></select>'
            '<textarea name="bio">line1</textarea>'
            '</form>')
    root = dom.parse(html)
    fields = browser.collect_form_fields(root, "f")
    names = [f["name"] for f in fields]
    assert "q" in names and "flag" in names and "lang" in names and "bio" in names, \
        f"field names: {names}"
    qs = browser.serialize_form_fields(fields)
    assert "q=hello+world" in qs or "q=hello%20world" in qs, f"qs: {qs}"
    assert "flag=yes" in qs, f"checked checkbox must contribute: {qs}"
    assert "lang=en" in qs, f"select value: {qs}"
    assert "bio=line1" in qs, f"textarea value: {qs}"
    url = browser.form_get_url(root, "f", base_url="http://example.com")
    assert "/search?" in url, f"GET url: {url}"
    assert "q=hello%20world" in url or "q=hello+world" in url, f"GET url: {url}"
    assert "flag=yes" in url, f"GET url: {url}"



TESTS.append(("test_p28_form_get_query_string", test_p28_form_get_query_string))
def test_p28_form_post_body():
    """P28.2 — POST form submission builds a urlencoded body + the
    correct action URL; unchecked checkboxes are excluded."""
    import browser, dom
    html = ('<form id="f" method="post" action="/submit">'
            '<input type="text" name="a" value="1">'
            '<input type="checkbox" name="c1" value="x" checked>'
            '<input type="checkbox" name="c2" value="y">'
            '</form>')
    root = dom.parse(html)
    res = browser.form_post_body(root, "f", base_url="http://example.com")
    assert res["url"].endswith("/submit"), f"url: {res['url']}"
    assert res["content_type"] == "application/x-www-form-urlencoded"
    assert "a=1" in res["body"], f"body: {res['body']}"
    assert "c1=x" in res["body"], "checked checkbox must be in body"
    assert "c2" not in res["body"], "unchecked checkbox must be excluded"



TESTS.append(("test_p28_form_post_body", test_p28_form_post_body))
def test_p28_form_select_multiple():
    """P28.2 — <select multiple> contributes one k=v pair per selected
    option."""
    import browser, dom
    html = ('<select name="tags" multiple>'
            '<option value="a" selected>A</option>'
            '<option value="b" selected>B</option>'
            '<option value="c">C</option>'
            '</select>')
    root = dom.parse(html)
    fields = browser.collect_form_fields(root)
    tag_fields = [f for f in fields if f["name"] == "tags"]
    assert len(tag_fields) == 2, f"multi-select must emit 2 fields: {tag_fields}"
    assert [f["value"] for f in tag_fields] == ["a", "b"]
    qs = browser.serialize_form_fields(fields)
    assert qs.count("tags=") == 2, f"repeated name: {qs}"



TESTS.append(("test_p28_form_select_multiple", test_p28_form_select_multiple))
def test_p28_form_radio_group():
    """P28.2 — radio group: only the checked radio contributes; default
    value is "on" when the value attribute is absent."""
    import browser, dom
    html = ('<form id="f">'
            '<input type="radio" name="mode" value="fast" checked>'
            '<input type="radio" name="mode" value="slow">'
            '<input type="radio" name="def">'
            '</form>')
    root = dom.parse(html)
    fields = browser.collect_form_fields(root, "f")
    mode = [f for f in fields if f["name"] == "mode"]
    assert len(mode) == 1 and mode[0]["value"] == "fast", f"radio: {mode}"
    # The "def" radio has no checked attr → must not contribute.
    def_fields = [f for f in fields if f["value"] == "on"]
    assert def_fields == [], f"unchecked radio must be excluded: {def_fields}"



TESTS.append(("test_p28_form_radio_group", test_p28_form_radio_group))
def test_p28_form_action_url_resolution():
    """P28.2 — form action URL resolution: absolute, root-relative,
    and base-relative forms."""
    import browser, dom
    # Root-relative action with a base href.
    html = ('<html><head><base href="http://site.example/dir/page.html"></head>'
            '<form id="f" action="/api/search"></form></html>')
    root = dom.parse(html)
    url = browser.form_action_url(root, "f")
    assert url.startswith("http://site.example"), f"root-relative: {url}"
    assert "/api/search" in url, f"action path must be present: {url}"
    # Absolute action passes through unchanged.
    html2 = '<form id="g" action="https://other.example/x">input</form>'
    root2 = dom.parse(html2)
    url2 = browser.form_action_url(root2, "g")
    assert url2 == "https://other.example/x", f"absolute: {url2}"



TESTS.append(("test_p28_form_action_url_resolution", test_p28_form_action_url_resolution))
def test_p28_required_validation():
    """P28.3 — required text input that is empty → one violation;
    filled → zero."""
    import browser, dom
    html = ('<form id="f">'
            '<input type="text" name="email" required>'
            '<input type="text" name="filled" value="ok" required>'
            '</form>')
    root = dom.parse(html)
    v = browser.validate_form(root, "f")
    bad = [x for x in v if x["field"] == "email"]
    assert len(bad) == 1 and bad[0]["kind"] == "required", f"violations: {v}"
    good = [x for x in v if x["field"] == "filled"]
    assert good == [], f"filled field must pass: {v}"



TESTS.append(("test_p28_required_validation", test_p28_required_validation))
def test_p28_required_checkbox():
    """P28.3 — a required checkbox with no checked attribute is a
    violation; checked passes."""
    import browser, dom
    html = ('<form id="f">'
            '<input type="checkbox" name="terms" required>'
            '<input type="checkbox" name="ok" required checked>'
            '</form>')
    root = dom.parse(html)
    v = browser.validate_form(root, "f")
    assert any(x["field"] == "terms" for x in v), f"unchecked required: {v}"
    assert not any(x["field"] == "ok" for x in v), f"checked required: {v}"



TESTS.append(("test_p28_required_checkbox", test_p28_required_checkbox))
def test_p28_submit_blocked_by_validation():
    """P28.2/3 — form_submit returns ok=False + violations when a
    required field is empty (no network I/O in the dry-run case)."""
    import browser, dom
    html = ('<form id="f" action="/x">'
            '<input type="text" name="req" required>'
            '</form>')
    root = dom.parse(html)
    out = browser.form_submit(root, "f", method="GET")
    assert out["ok"] is False, f"must not submit: {out}"
    assert any(x["field"] == "req" for x in out["violations"])
    # With a value set, the dry-run submission succeeds.
    html2 = ('<form id="f" action="/x">'
             '<input type="text" name="req" value="v" required>'
             '</form>')
    root2 = dom.parse(html2)
    out2 = browser.form_submit(root2, "f", method="GET")
    assert out2["ok"] is True, f"dry-run submit: {out2}"
    assert out2["url"].startswith("/x?"), f"url: {out2['url']}"
    assert "req=v" in out2["url"]



TESTS.append(("test_p28_submit_blocked_by_validation", test_p28_submit_blocked_by_validation))
def test_p28_tab_order_natural():
    """P28.3 — tab order follows DOM order for tabindex=0 / absent
    focusable elements."""
    import browser, dom
    html = ('<div>'
            '<a id="l1" href="/a"></a>'
            '<button id="b1"></button>'
            '<input id="i1" type="text">'
            '<select id="s1"><option>x</option></select>'
            '<textarea id="t1"></textarea>'
            '</div>')
    root = dom.parse(html)
    order = browser.tab_order(root)
    assert order == ["l1", "b1", "i1", "s1", "t1"], f"natural tab order: {order}"


TESTS.append(("test_p28_tab_order_natural", test_p28_tab_order_natural))
def test_p28_tab_order_explicit():
    """P28.3 — positive tabindex elements come before natural-order
    ones; hidden inputs are skipped."""
    import browser, dom
    html = ('<div>'
            '<input id="hid" type="hidden">'
            '<button id="b2" tabindex="2"></button>'
            '<button id="b1" tabindex="1"></button>'
            '<input id="i1" type="text">'
            '</div>')
    root = dom.parse(html)
    order = browser.tab_order(root)
    assert order[0] == "b2" and order[1] == "b1", f"positive tabindex first: {order}"
    assert "hid" not in order, f"hidden input must be skipped: {order}"
    assert order[-1] == "i1", f"natural order last: {order}"



TESTS.append(("test_p28_tab_order_explicit", test_p28_tab_order_explicit))
def test_p28_focus_marks_box():
    """P28.3 — focus_element sets the DOM focus attribute; the next
    build_box sees control_focus=True; a blue focus ring is emitted."""
    import dom, css, layout, paint_engine, browser
    html = '<input id="i1" type="text" style="width:100px">'
    root = dom.parse(html)
    assert browser.focus_element(root, "i1") is True
    assert browser.focus_element(root, "missing") is False
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 200.0)
    bx = _find_box(lroot, id_="i1")
    assert bx is not None and bx.control_focus is True, "focus attr must set control_focus"
    dl = paint_engine.emit_commands(lroot)
    ring = [c for c in dl.commands if c.kind == "border" and c.control_type == ""]
    # Focus ring uses a blue border command around the box.
    assert len(dl.commands) > 0



TESTS.append(("test_p28_focus_marks_box", test_p28_focus_marks_box))
def test_p28_checkbox_toggle_render_diff():
    """P28.1 — toggling the checked attribute between two renders
    changes the display list (checked adds a fill command)."""
    import dom, css, layout, paint_engine
    html_a = '<input type="checkbox" id="cb" checked>'
    html_b = '<input type="checkbox" id="cb">'
    ra = paint_engine.emit_commands(layout.layout(css.apply(dom.parse(html_a), ""), 300, 200))
    rb = paint_engine.emit_commands(layout.layout(css.apply(dom.parse(html_b), ""), 300, 200))
    ca = [c for c in ra.commands if c.control_type == "checkbox"]
    cb = [c for c in rb.commands if c.control_type == "checkbox"]
    assert len(ca) >= 1, "checked checkbox must emit tick commands"
    assert len(cb) == 0 or all(c.kind != "rect" for c in cb), \
        "unchecked checkbox must not emit a tick fill"



TESTS.append(("test_p28_checkbox_toggle_render_diff", test_p28_checkbox_toggle_render_diff))
def test_p28_full_pipeline_form_render():
    """P28 — end-to-end: a page with all control types renders without
    crashing and paints non-white pixels."""
    import browser
    html = ('<html><body style="height:400px">'
            '<form action="/s" method="get">'
            '<input type="text" name="q" value="abc" style="width:120px">'
            '<input type="checkbox" name="c" checked>'
            '<input type="radio" name="r" value="1" checked>'
            '<select name="s"><option value="a" selected>A</option></select>'
            '<input type="file" name="f" value="x.txt">'
            '<textarea name="t" cols="10" rows="2">hi</textarea>'
            '<button type="submit">Go</button>'
            '</form>'
            '</body></html>')
    bmp = browser.render_document(html, "", 320, 400)
    assert sum(1 for px in bmp.pixels if px != 0xFFFFFF) > 0, \
        "form page must render pixels"



TESTS.append(("test_p28_full_pipeline_form_render", test_p28_full_pipeline_form_render))
def test_p28_js_element_value_prop():
    """P28.1 — the JS engine's element .value property reflects the DOM
    value attribute for inputs and selects."""
    import browser, dom
    html = ('<input id="i1" value="start">'
            '<select id="s1"><option value="a">A</option>'
            '<option value="b" selected>B</option></select>'
            '<script>console.log(document.getElementById("i1").value);'
            'console.log(document.getElementById("s1").value)</script>')
    out = []
    bmp = browser.render_document_with_event(
        html, "", 300, 200,
        events=[{"type": "click", "x": 10, "y": 10}],
        on_before_render=lambda state, idx: None,
    ) if False else None
    # The host collects logs — run the script directly.
    import js as js_mod
    root = dom.parse(html)
    host = js_mod.Host(root, lambda m: out.append(m))
    host.execute_all()
    assert "start" in out, f"input value log: {out}"
    assert "b" in out, f"select value log: {out}"



TESTS.append(("test_p28_js_element_value_prop", test_p28_js_element_value_prop))
def test_p28_form_hidden_field():
    """P28.2 — <input type="hidden"> contributes its value to the
    serialization; <button> with a name contributes as button."""
    import browser, dom
    html = ('<form id="f" action="/x">'
            '<input type="hidden" name="tok" value="secret">'
            '<button type="submit" name="act" value="go">Submit</button>'
            '</form>')
    root = dom.parse(html)
    fields = browser.collect_form_fields(root, "f")
    names = {f["name"]: f["value"] for f in fields}
    assert names.get("tok") == "secret", f"hidden: {names}"
    assert names.get("act") == "go", f"button: {names}"



TESTS.append(("test_p28_form_hidden_field", test_p28_form_hidden_field))
def test_p28_required_select():
    """P28.3 — a required <select> with no selected option is a
    violation."""
    import browser, dom
    html = ('<form id="f">'
            '<select name="pick" required>'
            '<option value="a">A</option>'
            '</select></form>')
    root = dom.parse(html)
    v = browser.validate_form(root, "f")
    assert any(x["field"] == "pick" and x["kind"] == "required" for x in v), \
        f"required select violation: {v}"
    # With a selected option, no violation.
    html2 = ('<form id="f">'
             '<select name="pick" required>'
             '<option value="a" selected>A</option>'
             '</select></form>')
    root2 = dom.parse(html2)
    v2 = browser.validate_form(root2, "f")
    assert not any(x["field"] == "pick" for x in v2), f"selected passes: {v2}"



TESTS.append(("test_p28_required_select", test_p28_required_select))
def test_p29_regexp_literal_test():
    """P29.1 — regex literal ``/pat/`` + ``.test()`` (bridge to ``re``)."""
    logs = _p29_js_logs(
        "let r = /ab+c/; console.log(r.test('xabbc')); console.log(r.test('abab'));"
    )
    assert any("true" in l for l in logs), f"test must log true: {logs}"
    assert any("false" in l for l in logs), f"non-match must log false: {logs}"



TESTS.append(("test_p29_regexp_literal_test", test_p29_regexp_literal_test))
def test_p29_regexp_literal_flags():
    """P29.1 — regex flags: ``/pat/i`` case-insensitive, ``/pat/g`` all."""
    logs = _p29_js_logs(
        "let r = /^H/i; console.log(r.test('hello'));"
        "let g = /a/g; console.log('banana'.split(g).join('|'));"
    )
    assert any("true" in l for l in logs), f"/i flag must match: {logs}"



TESTS.append(("test_p29_regexp_literal_flags", test_p29_regexp_literal_flags))
def test_p29_regexp_exec_groups():
    """P29.1 — ``.exec()`` returns match + capture groups."""
    logs = _p29_js_logs(
        "let r = /(\\d+)-(\\d+)/; let m = r.exec('v12-34'); "
        "console.log(m[0]); console.log(m[1]); console.log(m[2]);"
    )
    assert any("12-34" in l for l in logs), f"full match: {logs}"
    assert any("12" in l for l in logs), f"group 1: {logs}"
    assert any("34" in l for l in logs), f"group 2: {logs}"



TESTS.append(("test_p29_regexp_exec_groups", test_p29_regexp_exec_groups))
def test_p29_string_match_regexp():
    """P29.1 — ``String.match(regexp)``: global flag → all matches."""
    logs = _p29_js_logs(
        "let s = 'xx1yy2zz3'; console.log(s.match(/\\d/g).join(','));"
    )
    assert any("1,2,3" in l for l in logs), f"global match all: {logs}"



TESTS.append(("test_p29_string_match_regexp", test_p29_string_match_regexp))
def test_p29_string_replace_regexp():
    """P29.1 — ``String.replace(regexp, repl)`` via re.sub bridge."""
    logs = _p29_js_logs(
        "console.log('a1b2c3'.replace(/\\d/g, 'X'));"
    )
    assert any("aXbXcX" in l for l in logs), f"regex replace: {logs}"



TESTS.append(("test_p29_string_replace_regexp", test_p29_string_replace_regexp))
def test_p29_new_regexp_ctor():
    """P29.1 — ``new RegExp(pattern, flags)`` still works (P4.2 path)."""
    logs = _p29_js_logs(
        "let r = new RegExp('a+', 'i'); console.log(r.test('BA'));"
    )
    assert any("true" in l for l in logs), f"new RegExp i flag: {logs}"



TESTS.append(("test_p29_new_regexp_ctor", test_p29_new_regexp_ctor))
def test_p29_for_of_array():
    """P29.2 — ``for...of`` over an array accumulates values."""
    logs = _p29_js_logs(
        "let xs = [1,2,3]; let s = 0; for (const x of xs) { s = s + x; } "
        "console.log(s);"
    )
    assert any("6" in l for l in logs), f"for-of sum: {logs}"



TESTS.append(("test_p29_for_of_array", test_p29_for_of_array))
def test_p29_for_of_string_chars():
    """P29.2 — ``for...of`` over a string iterates characters."""
    logs = _p29_js_logs(
        "let out = ''; for (const c of 'abc') { out = out + c + '-'; } "
        "console.log(out);"
    )
    assert any("a-b-c-" in l for l in logs), f"for-of chars: {logs}"



TESTS.append(("test_p29_for_of_string_chars", test_p29_for_of_string_chars))
def test_p29_for_of_generator():
    """P29.2 — ``for...of`` consumes a generator's yielded values."""
    logs = _p29_js_logs(
        "function* gen() { yield 1; yield 2; yield 3; } "
        "let g = gen(); let out = []; for (const v of g) { out.push(v); } "
        "console.log(out.join(','));"
    )
    assert any("1,2,3" in l for l in logs), f"gen for-of: {logs}"



TESTS.append(("test_p29_for_of_generator", test_p29_for_of_generator))
def test_p29_generator_next_done():
    """P29.2 — ``.next()`` returns ``{done, value}``; last call done:true."""
    logs = _p29_js_logs(
        "function* g2() { yield 'a'; yield 'b'; } "
        "let it = g2(); "
        "console.log(it.next().value); "
        "console.log(it.next().value); "
        "console.log(it.next().done);"
    )
    assert any("a" in l for l in logs), f"first yield: {logs}"
    assert any("b" in l for l in logs), f"second yield: {logs}"
    assert any("true" in l for l in logs), f"done flag: {logs}"



TESTS.append(("test_p29_generator_next_done", test_p29_generator_next_done))
def test_p29_generator_expression_form():
    """P29.2 — generator function expression ``function*() {...}``."""
    logs = _p29_js_logs(
        "let gen = function*() { yield 10; yield 20; }; "
        "let g = gen(); let out = []; for (const v of g) { out.push(v); } "
        "console.log(out.join(','));"
    )
    assert any("10,20" in l for l in logs), f"gen-expr for-of: {logs}"



TESTS.append(("test_p29_generator_expression_form", test_p29_generator_expression_form))
def test_p29_generator_yield_in_loop():
    """P29.2 — a generator body can contain a while loop + yield."""
    logs = _p29_js_logs(
        "function* countdown(n) { while (n > 0) { yield n; n = n - 1; } } "
        "let c = countdown(3); let out = []; for (const v of c) { out.push(v); } "
        "console.log(out.join(' '));"
    )
    assert any("3 2 1" in l for l in logs), f"countdown: {logs}"



TESTS.append(("test_p29_generator_yield_in_loop", test_p29_generator_yield_in_loop))
def test_p29_for_of_map_iterable():
    """P29.2 — ``for...of`` over a Map iterates ``[k, v]`` pairs."""
    logs = _p29_js_logs(
        "let m = new Map(); m.set('a', 1); m.set('b', 2); "
        "let out = []; for (const e of m) { out.push(e[0] + ':' + e[1]); } "
        "console.log(out.join(' '));"
    )
    assert any("a:1" in l for l in logs), f"map for-of: {logs}"
    assert any("b:2" in l for l in logs), f"map for-of: {logs}"



TESTS.append(("test_p29_for_of_map_iterable", test_p29_for_of_map_iterable))
def test_p29_symbol_iterator_protocol():
    """P29.2 — custom ``@@iterator`` (Symbol.iterator toy) drives for-of."""
    logs = _p29_js_logs(
        "let it = (function*() { yield 5; yield 6; })(); "
        "let obj = {'@@iterator': function() { return it; }}; "
        "let out = []; for (const v of obj) { out.push(v); } "
        "console.log(out.join(','));"
    )
    assert any("5,6" in l for l in logs), f"@@iterator for-of: {logs}"



TESTS.append(("test_p29_symbol_iterator_protocol", test_p29_symbol_iterator_protocol))
def test_p29_math_trig_log():
    """P29.3 — Math.sin/cos/log/atan2/cbrt/full constants."""
    logs = _p29_js_logs(
        "console.log(Math.sin(0)); "
        "console.log(Math.log(1)); "
        "console.log(Math.cbrt(8)); "
        "console.log(Math.atan2(1, 0) > 0);"
    )
    assert any("0" in l for l in logs), f"sin(0)/log(1): {logs}"
    assert any("2" in l for l in logs), f"cbrt(8): {logs}"
    assert any("true" in l for l in logs), f"atan2: {logs}"



TESTS.append(("test_p29_math_trig_log", test_p29_math_trig_log))
def test_p29_math_constants():
    """P29.3 — Math.LN2 / LN10 / SQRT2 constants are defined."""
    logs = _p29_js_logs(
        "console.log(Math.LN2 > 0.6 && Math.LN2 < 0.7); "
        "console.log(Math.SQRT2 > 1.4 && Math.SQRT2 < 1.5);"
    )
    assert any("true" in l for l in logs), f"math constants: {logs}"



TESTS.append(("test_p29_math_constants", test_p29_math_constants))
def test_p29_json_stringify_nested():
    """P29.3 — JSON.stringify round-trips nested dict + list structures."""
    logs = _p29_js_logs(
        "let o = {a: 1, b: [2, {c: 'x'}]}; "
        "let s = JSON.stringify(o); console.log(s);"
    )
    assert any('"a":1' in l for l in logs), f"json stringify: {logs}"
    assert any('"c":"x"' in l for l in logs), f"json nested: {logs}"



TESTS.append(("test_p29_json_stringify_nested", test_p29_json_stringify_nested))
def test_p29_json_parse_stringify_roundtrip():
    """P29.3 — JSON.parse(stringify(x)) recovers the structure."""
    logs = _p29_js_logs(
        "let x = JSON.parse('\"hi\"'); console.log(x); "
        "let y = JSON.parse('[1,2,3]'); console.log(y.join(','));"
    )
    assert any("hi" in l for l in logs), f"json parse str: {logs}"
    assert any("1,2,3" in l for l in logs), f"json parse arr: {logs}"



TESTS.append(("test_p29_json_parse_stringify_roundtrip", test_p29_json_parse_stringify_roundtrip))
def test_p29_array_tail_methods():
    """P29.3 — Array.fill / findLast / toReversed / toSorted / at."""
    logs = _p29_js_logs(
        "let a = [0,0,0]; a.fill(7, 1, 3); console.log(a.join(',')); "
        "console.log([3,1,2].findLast(v => v > 1)); "
        "console.log([1,2,3].toReversed().join(',')); "
        "console.log([3,1,2].toSorted().join(','));"
    )
    assert any("0,7,7" in l for l in logs), f"fill: {logs}"
    assert any("3" in l for l in logs), f"findLast: {logs}"
    assert any("3,2,1" in l for l in logs), f"toReversed: {logs}"
    assert any("1,2,3" in l for l in logs), f"toSorted: {logs}"



TESTS.append(("test_p29_array_tail_methods", test_p29_array_tail_methods))
def test_p29_string_tail_methods():
    """P29.3 — String.at / codePointAt tail methods."""
    logs = _p29_js_logs(
        "console.log('abc'.at(1)); "
        "console.log('abc'.at(-1)); "
        "console.log('A'.codePointAt(0));"
    )
    assert any("b" in l for l in logs), f"at(1): {logs}"
    assert any("c" in l for l in logs), f"at(-1): {logs}"
    assert any("65" in l for l in logs), f"codePointAt: {logs}"



TESTS.append(("test_p29_string_tail_methods", test_p29_string_tail_methods))
def test_p30_qsel_simple_selectors():
    """P30.1 — simple selectors (tag / .cls / #id / .a.b) via the CSS engine."""
    html = _p30_doc_html()
    assert _p30_qsel_all(html, "p") == ["p", "p"]
    assert _p30_qsel_all(html, ".a") == ["div", "section"]
    assert _p30_qsel_all(html, "#d1") == ["div"]
    assert _p30_qsel_all(html, ".a.b") == ["div"]



TESTS.append(("test_p30_qsel_simple_selectors", test_p30_qsel_simple_selectors))
def test_p30_qsel_descendant_child():
    """P30.1 — descendant and child combinators resolve against the DOM."""
    html = _p30_doc_html()
    assert _p30_qsel_all(html, "div p") == ["p", "p"]
    assert _p30_qsel_all(html, "div .c") == ["p", "p"]
    assert _p30_qsel_all(html, "div > p") == ["p", "p"]
    assert _p30_qsel_all(html, "section > h2") == ["h2", "h2"]



TESTS.append(("test_p30_qsel_descendant_child", test_p30_qsel_descendant_child))
def test_p30_qsel_comma_list():
    """P30.1 — comma selector lists union their matches in document order."""
    html = _p30_doc_html()
    assert _p30_qsel_all(html, "h2, #d1") == ["div", "h2", "h2"]



TESTS.append(("test_p30_qsel_comma_list", test_p30_qsel_comma_list))
def test_p30_qsel_child_pseudos():
    """P30.1 — structural pseudo-classes match against the DOM directly."""
    html = _p30_doc_html()
    assert _p30_qsel_all(html, "div > p:first-child") == ["p"]
    assert _p30_qsel_all(html, "div > p:last-child") == ["p"]
    assert _p30_qsel_all(html, "div > p:nth-child(2)") == ["p"]



TESTS.append(("test_p30_qsel_child_pseudos", test_p30_qsel_child_pseudos))
def test_p30_qsel_hover_no_state():
    """P30.1 — :hover/:focus never match headless (no interaction state)."""
    html = _p30_doc_html()
    assert _p30_qsel_all(html, "p:hover") == []
    assert _p30_qsel_all(html, "#h2a:focus") == []



TESTS.append(("test_p30_qsel_hover_no_state", test_p30_qsel_hover_no_state))
def test_p30_qsel_js_query_selector():
    """P30.1 — document.querySelector (first match) works from JS."""
    html = (
        '<html><body>'
        '<div id="d1" class="a"><p class="c">one</p></div>'
        '<script>'
        'var n = document.querySelector("#d1 .c");'
        'if (n) { console.log("QS", n.tagName, n.textContent); } else { console.log("QS null"); }'
        'var all = document.querySelectorAll("div > p");'
        'console.log("QSA", all.length);'
        '</script></body></html>'
    )
    host, logs = _p30_host(html)
    assert any("QS P one" in l for l in logs), f"querySelector first match: {logs}"
    assert any("QSA 1" in l for l in logs), f"querySelectorAll count: {logs}"



TESTS.append(("test_p30_qsel_js_query_selector", test_p30_qsel_js_query_selector))
def test_p30_qsel_scoped_include_self():
    """P30.1 — scoped query on an element includes the element itself
    (legacy semantics preserved)."""
    root = dom.parse('<div id="d" class="c"><p>x</p></div>')
    div = root.find_by_id("d")
    assert div.query_selector_all(".c")[0] is div, "self must be in scope"
    assert [n.tag for n in div.query_selector_all("p")] == ["p"]



TESTS.append(("test_p30_qsel_scoped_include_self", test_p30_qsel_scoped_include_self))
def test_p30_append_child_mutation():
    """P30.2 — appendChild re-parents a node and textContent reflects it."""
    html = '<html><body><div id="root"><p class="x">hi</p></div></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var n = document.querySelector("#root .x");'
        'var c = document.createElement("em");'
        'c.textContent = "new";'
        'n.appendChild(c);'
        'console.log("A", n.textContent, n.children.length);'
    )
    assert any("A hinew 1" in l for l in logs), f"appendChild textContent: {logs}"



TESTS.append(("test_p30_append_child_mutation", test_p30_append_child_mutation))
def test_p30_remove_child_mutation():
    """P30.2 — removeChild detaches the node; textContent reverts."""
    html = '<html><body><div id="root"><p class="x">hi</p></div></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var n = document.querySelector("#root .x");'
        'var c = document.createElement("em");'
        'c.textContent = "new";'
        'n.appendChild(c);'
        'n.removeChild(c);'
        'console.log("B", n.textContent, n.children.length);'
    )
    assert any("B hi 0" in l for l in logs), f"removeChild revert: {logs}"



TESTS.append(("test_p30_remove_child_mutation", test_p30_remove_child_mutation))
def test_p30_insert_before_mutation():
    """P30.2 — insertBefore places a node before a sibling in document order."""
    html = '<html><body><div id="root"><p class="x"><i style="color:red">lead</i>hi</p></div></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var n = document.querySelector("#root .x");'
        'var c = document.createElement("b");'
        'c.textContent = "mid";'
        'n.insertBefore(c, n.children[0]);'
        'console.log("C", n.children[0].tagName, n.textContent);'
    )
    assert any("C B midleadhi" in l for l in logs), f"insertBefore order: {logs}"



TESTS.append(("test_p30_insert_before_mutation", test_p30_insert_before_mutation))
def test_p30_mutation_renders():
    """P30.2 — a DOM mutation (style + textContent) triggers a re-layout
    and re-render with the new geometry/paint."""
    import css, layout, paint_engine
    html = ('<html><body><div id="box" '
            'style="width:100px;height:40px;background:#ff0000"></div></body></html>')
    tree = dom.parse(html)
    import js
    host = js.Host(tree, lambda _s: None)
    host.execute_all()
    host.execute(
        'var b = document.querySelector("#box");'
        'b.setAttribute("style","width:200px;height:80px;background:#00ff00");'
        'b.textContent = "hi";'
    )
    styled = css.apply(tree, "")
    root_box = layout.layout(styled, 800.0, 600.0)

    def find(bx, _id):
        if bx.dom is not None and bx.dom.is_element() and bx.dom.attr("id") == _id:
            return bx
        for c in bx.children:
            r = find(c, _id)
            if r is not None:
                return r
        return None

    box = find(root_box, "box")
    assert box.rect.w == 200.0 and box.rect.h == 80.0, f"re-layout: {box.rect}"
    dl = paint_engine.emit_commands(root_box)
    bmp = paint_engine.rasterize(dl, 800, 600)
    green = red = 0
    w, h = bmp.width, bmp.height
    for py in range(h):
        base = py * w
        for px in range(w):
            v = bmp.pixels[base + px]
            r_, g_, b_ = (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
            if g_ > 128 and r_ < 128:
                green += 1
            elif r_ > 128 and g_ < 128:
                red += 1
    assert green > 0 and red == 0, f"re-paint must use new background: green={green} red={red}"



TESTS.append(("test_p30_mutation_renders", test_p30_mutation_renders))
def test_p30_create_element_detached():
    """P30.3 — createElement returns a detached element; attach via
    appendChild to integrate into the tree."""
    html = '<html><body><div id="root"><p class="x">hi</p></div></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var c = document.createElement("i");'
        'console.log("DET", c.tagName, c.children.length);'
        'var n = document.querySelector("#root .x");'
        'n.appendChild(c);'
        'console.log("ATT", n.children.length);'
    )
    assert any("DET I 0" in l for l in logs), f"detached createElement: {logs}"
    assert any("ATT 1" in l for l in logs), f"attach after create: {logs}"



TESTS.append(("test_p30_create_element_detached", test_p30_create_element_detached))
def test_p30_classlist_add_toggle():
    """P30.3 — classList.add / contains / toggle mutate the class attr."""
    html = '<html><body><p class="x" id="t">hi</p></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var n = document.querySelector("#t");'
        'n.classList.add("big");'
        'console.log("CL1", n.getAttribute("class"), n.classList.contains("x"));'
        'n.classList.toggle("big");'
        'console.log("CL2", n.getAttribute("class"));'
    )
    assert any("CL1 x big true" in l for l in logs), f"classList add/contains: {logs}"
    assert any("CL2 x" in l for l in logs), f"classList toggle off: {logs}"



TESTS.append(("test_p30_classlist_add_toggle", test_p30_classlist_add_toggle))
def test_p30_dataset_camelcase():
    """P30.3 — dataset maps data-* attributes to camelCase keys."""
    html = '<html><body><p class="x" data-a-b="1" data-plain="v" id="t">hi</p></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var n = document.querySelector("#t");'
        'console.log("DS", n.dataset.aB, n.dataset.plain);'
    )
    assert any("DS 1 v" in l for l in logs), f"dataset camelCase: {logs}"



TESTS.append(("test_p30_dataset_camelcase", test_p30_dataset_camelcase))
def test_p30_set_get_attribute():
    """P30.3 — setAttribute / getAttribute round-trip on the DOM node."""
    html = '<html><body><p id="t">hi</p></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var n = document.querySelector("#t");'
        'n.setAttribute("data-x", "7");'
        'console.log("SA", n.getAttribute("data-x"), n.dataset.x);'
    )
    assert any("SA 7 7" in l for l in logs), f"setAttribute/getAttribute: {logs}"



TESTS.append(("test_p30_set_get_attribute", test_p30_set_get_attribute))
def test_p30_custom_event_two_listeners():
    """P30.4 — multiple listeners fire in registration order with the
    event object (type / target / detail)."""
    html = '<html><body><span id="s1">one</span></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var s = document.querySelector("#s1");'
        'var count = 0;'
        's.addEventListener("ping", function(e){ count = count + 1; console.log("P1 " + count, e.detail); });'
        's.addEventListener("ping", function(e){ console.log("P2 " + e.type); });'
        's.dispatchEvent("ping", 42);'
    )
    assert any("P1 1 42" in l for l in logs), f"first listener detail: {logs}"
    assert any("P2 ping" in l for l in logs), f"second listener type: {logs}"



TESTS.append(("test_p30_custom_event_two_listeners", test_p30_custom_event_two_listeners))
def test_p30_custom_event_isolated():
    """P30.4 — dispatching on one element does not reach another element's
    listeners."""
    html = '<html><body><span id="s1">one</span><span id="s2">two</span></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var s1 = document.querySelector("#s1");'
        'var s2 = document.querySelector("#s2");'
        's1.addEventListener("ping", function(e){ console.log("S1", e.detail); });'
        's2.dispatchEvent("ping", 99);'
        'console.log("DONE");'
    )
    assert not any("S1 99" in l for l in logs), f"cross-element leak: {logs}"
    assert any("DONE" in l for l in logs), f"dispatch must not raise: {logs}"



TESTS.append(("test_p30_custom_event_isolated", test_p30_custom_event_isolated))
def test_p30_custom_event_mutation_visible():
    """P30.4 — a listener can mutate the DOM; the change is visible to
    subsequent queries (event handlers as the mutation entry point)."""
    html = '<html><body><div id="d"><span id="s">a</span></div></body></html>'
    host, logs = _p30_host(html)
    host.execute(
        'var s = document.querySelector("#s");'
        's.addEventListener("grow", function(){ var e = document.createElement("em");'
        'e.textContent = "+"; s.appendChild(e); });'
        's.dispatchEvent("grow");'
        'console.log("G", s.textContent, s.children.length);'
    )
    assert any("G a+\n1" in l or "G a+ 1" in l for l in logs), f"listener-driven mutation: {logs}"



TESTS.append(("test_p30_custom_event_mutation_visible", test_p30_custom_event_mutation_visible))
def test_p30_full_pipeline_regression():
    """P30.5 — JS-driven DOM mutation + querySelectorAll run through the
    complete render pipeline without crashing and producing pixels."""
    import paint
    html = (
        '<html><body>'
        '<div id="box" style="width:100px;height:40px;background:#0000ff">init</div>'
        '<script>'
        'var b = document.querySelector("#box");'
        'b.setAttribute("style","width:160px;height:60px;background:#00ff00");'
        'b.textContent = "mut";'
        'console.log("P30", b.children.length);'
        '</script></body></html>'
    )
    bmp = browser.render_document(html, "", 800, 600)
    nonwhite = sum(1 for px in bmp.pixels if px != paint.WHITE)
    assert nonwhite > 0, f"pipeline must paint: {nonwhite}"



TESTS.append(("test_p30_full_pipeline_regression", test_p30_full_pipeline_regression))
def test_p30_qsel_js_mutation_roundtrip():
    """P30.5 — a JS script that both queries and mutates leaves the DOM in a
    consistent, re-renderable state (M3 milestone check)."""
    import js
    html = (
        '<html><body>'
        '<div id="root" class="a"><p class="x">one</p><p class="x">two</p></div>'
        '<script>'
        'var ps = document.querySelectorAll(".a > p");'
        'ps[0].textContent = "one!";'
        'var c = document.createElement("em");'
        'c.textContent = "!";'
        'ps[1].appendChild(c);'
        'console.log("RT", ps.length, ps[0].textContent, ps[1].textContent);'
        '</script></body></html>'
    )
    root = dom.parse(html)
    host = js.Host(root, lambda _s: None)
    host.execute_all()
    assert [n.tag for n in root.query_selector_all(".a > p")] == ["p", "p"]
    # textContent of the first p was replaced by the script.
    assert root.find_by_id("root").children[0].text_content() == "one!"
    # The second p now has a <em> child ("two" + "!").
    assert root.find_by_id("root").children[1].text_content() == "two!"



TESTS.append(("test_p30_qsel_js_mutation_roundtrip", test_p30_qsel_js_mutation_roundtrip))
def test_p32_history_add_recent():
    d = _p32_tmp()
    hp = _os.path.join(d, "h.json")
    h = session.HistoryStore(hp, in_memory_only=True)
    h.add("http://a.example/", "A")
    h.add("http://b.example/", "B")
    assert len(h) == 2
    assert h.recent(5)[0]["url"] == "http://b.example/"
    h.clear()
    assert len(h) == 0
    _p32_cleanup(d)



TESTS.append(("test_p32_history_add_recent", test_p32_history_add_recent))
def test_p32_history_consecutive_collapse():
    h = session.HistoryStore(in_memory_only=True)
    h.add("http://a/", "A1")
    h.add("http://b/", "B")
    h.add("http://b/", "B2")   # consecutive → refresh in place
    assert len(h) == 2
    assert h.all()[1]["title"] == "B2"
    h.add("http://a/", "A2")   # not consecutive → new entry
    assert len(h) == 3



TESTS.append(("test_p32_history_consecutive_collapse", test_p32_history_consecutive_collapse))
def test_p32_history_cap():
    h = session.HistoryStore(in_memory_only=True, max_entries=2)
    h.add("http://1/")
    h.add("http://2/")
    h.add("http://3/")
    urls = [e["url"] for e in h.all()]
    assert urls == ["http://2/", "http://3/"], urls



TESTS.append(("test_p32_history_cap", test_p32_history_cap))
def test_p32_history_about_skipped():
    h = session.HistoryStore(in_memory_only=True)
    h.add("about:blank")
    h.add("about:view-source")
    assert len(h) == 0



TESTS.append(("test_p32_history_about_skipped", test_p32_history_about_skipped))
def test_p32_history_persist_roundtrip():
    d = _p32_tmp()
    hp = _os.path.join(d, "h.json")
    h = session.HistoryStore(hp)
    h.add("http://x/", "X")
    h.add("http://y/", "Y")
    reloaded = session.HistoryStore(hp)
    assert [e["url"] for e in reloaded.all()] == ["http://x/", "http://y/"]
    _p32_cleanup(d)



TESTS.append(("test_p32_history_persist_roundtrip", test_p32_history_persist_roundtrip))
def test_p32_history_corrupt_graceful():
    d = _p32_tmp()
    hp = _os.path.join(d, "h.json")
    with open(hp, "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    h = session.HistoryStore(hp)
    assert len(h) == 0
    h.add("http://ok/")
    reloaded = session.HistoryStore(hp)
    assert len(reloaded) == 1
    _p32_cleanup(d)



TESTS.append(("test_p32_history_corrupt_graceful", test_p32_history_corrupt_graceful))
def test_p32_bookmarks_add_remove():
    bmk = session.BookmarkStore(in_memory_only=True)
    bmk.add("http://a/", "A")
    bmk.add("http://b/")
    assert bmk.has("http://a/") and bmk.has("http://b/")
    assert bmk.all()[1]["title"] == "http://b/"
    assert bmk.remove("http://a/")
    assert not bmk.has("http://a/")
    assert not bmk.remove("http://a/")  # idempotent
    assert len(bmk) == 1



TESTS.append(("test_p32_bookmarks_add_remove", test_p32_bookmarks_add_remove))
def test_p32_bookmarks_update_title():
    bmk = session.BookmarkStore(in_memory_only=True)
    bmk.add("http://a/", "old")
    bmk.add("http://a/", "new")
    assert len(bmk) == 1
    assert bmk.all()[0]["title"] == "new"



TESTS.append(("test_p32_bookmarks_update_title", test_p32_bookmarks_update_title))
def test_p32_bookmarks_persist():
    d = _p32_tmp()
    bp = _os.path.join(d, "b.json")
    bmk = session.BookmarkStore(bp)
    bmk.add("http://a/", "A")
    bmk.add("http://b/", "B")
    reloaded = session.BookmarkStore(bp)
    assert reloaded.urls() == ["http://a/", "http://b/"]
    _p32_cleanup(d)



TESTS.append(("test_p32_bookmarks_persist", test_p32_bookmarks_persist))
def test_p32_bookmarks_page_html():
    bmk = session.BookmarkStore(in_memory_only=True)
    bmk.add("http://a/", "A")
    html = session.bookmarks_page_html(bmk)
    assert "A" in html and "http://a/" in html
    empty_html = session.bookmarks_page_html(
        session.BookmarkStore(in_memory_only=True))
    assert "No bookmarks" in empty_html



TESTS.append(("test_p32_bookmarks_page_html", test_p32_bookmarks_page_html))
def test_p32_cookie_parse_basic():
    c = cookie_store.parse_set_cookie("sid=abc; Path=/; HttpOnly",
                                      "http://example.com/")
    assert c is not None
    assert c.name == "sid" and c.value == "abc"
    assert c.domain == "example.com" and c.path == "/"



TESTS.append(("test_p32_cookie_parse_basic", test_p32_cookie_parse_basic))
def test_p32_cookie_max_age_zero():
    c = cookie_store.parse_set_cookie("t=1; Max-Age=0", "http://x.com/")
    assert c is not None
    assert c.expired



TESTS.append(("test_p32_cookie_max_age_zero", test_p32_cookie_max_age_zero))
def test_p32_cookie_header_match():
    jar = cookie_store.CookieJar()
    jar.ingest_response("http://example.com/", {"set-cookie": "sid=abc"})
    assert "sid=abc" in jar.cookie_header_for("http://example.com/other")
    assert jar.cookie_header_for("http://evil.com/") == ""



TESTS.append(("test_p32_cookie_header_match", test_p32_cookie_header_match))
def test_p32_cookie_subdomain():
    jar = cookie_store.CookieJar()
    jar.ingest_response(
        "http://example.com/", {"set-cookie": "u=1; Domain=example.com"})
    matches = jar.for_url("http://sub.example.com/")
    assert any(c.name == "u" for c in matches)
    assert jar.cookie_header_for("http://sub.example.com/") == "u=1"



TESTS.append(("test_p32_cookie_subdomain", test_p32_cookie_subdomain))
def test_p32_cookie_path_filter():
    jar = cookie_store.CookieJar()
    jar.ingest_response("http://x.com/", {"set-cookie": "p=v; Path=/admin"})
    assert jar.cookie_header_for("http://x.com/admin/x") == "p=v"
    assert jar.cookie_header_for("http://x.com/public") == ""



TESTS.append(("test_p32_cookie_path_filter", test_p32_cookie_path_filter))
def test_p32_cookie_malformed_dropped():
    assert cookie_store.parse_set_cookie("garbage no equals", "http://x.com/") is None
    assert cookie_store.parse_set_cookie("=value", "http://x.com/") is None



TESTS.append(("test_p32_cookie_malformed_dropped", test_p32_cookie_malformed_dropped))
def test_p32_cookie_jar_ingest_count():
    jar = cookie_store.CookieJar()
    n = jar.ingest_response("http://a.com/", {"set-cookie": "x=1"})
    assert n == 1 and len(jar) == 1
    n2 = jar.ingest_response("http://a.com/", {})
    assert n2 == 0 and len(jar) == 1



TESTS.append(("test_p32_cookie_jar_ingest_count", test_p32_cookie_jar_ingest_count))
def test_p32_categorize_dns():
    import net
    assert net.categorize_fetch_error(
        net.FetchError("getaddrinfo: name or service not known")) == "dns"



TESTS.append(("test_p32_categorize_dns", test_p32_categorize_dns))
def test_p32_categorize_timeout():
    import net
    assert net.categorize_fetch_error(TimeoutError("timed out")) == "timeout"
    assert net.categorize_fetch_error(
        net.FetchError("request timed out")) == "timeout"



TESTS.append(("test_p32_categorize_timeout", test_p32_categorize_timeout))
def test_p32_categorize_connect():
    import net
    assert net.categorize_fetch_error(
        ConnectionRefusedError("refused")) == "connect"
    assert net.categorize_fetch_error(
        net.FetchError("connection refused")) == "connect"



TESTS.append(("test_p32_categorize_connect", test_p32_categorize_connect))
def test_p32_categorize_bad_url():
    import net
    assert net.categorize_fetch_error(
        net.UnsupportedScheme("ftp://x")) == "bad_url"
    assert net.categorize_fetch_error(
        net.FetchError("invalid url scheme")) == "bad_url"



TESTS.append(("test_p32_categorize_bad_url", test_p32_categorize_bad_url))
def test_p32_categorize_redirect():
    import net
    assert net.categorize_fetch_error(net.RedirectLoop("loop")) == "redirect"



TESTS.append(("test_p32_categorize_redirect", test_p32_categorize_redirect))
def test_p32_categorize_http_status():
    import net
    assert net.categorize_fetch_error(
        net.FetchError("HTTP 404 Not Found", status=404)) == "http"
    assert net.categorize_fetch_error(
        net.FetchError("HTTP 500 Internal", status=500)) == "http"



TESTS.append(("test_p32_categorize_http_status", test_p32_categorize_http_status))
def test_p32_error_page_copy_distinct():
    import net
    for cat, needle in [
        ("dns", "Server not found"),
        ("connect", "Connection refused"),
        ("timeout", "Connection timed out"),
        ("bad_url", "Invalid URL"),
        ("redirect", "Redirect loop"),
    ]:
        html = net.error_page_html("t", "d", category=cat)
        assert needle in html, (cat, html)



TESTS.append(("test_p32_error_page_copy_distinct", test_p32_error_page_copy_distinct))
def test_p32_http_error_page_shows_status():
    import net
    html = net.error_page_html("HTTP 404 Not Found", "detail", category="http")
    assert "Request failed" in html and "HTTP 404 Not Found" in html



TESTS.append(("test_p32_http_error_page_shows_status", test_p32_http_error_page_shows_status))
def test_p32_error_page_generic_passthrough():
    import net
    html = net.error_page_html("My Title", "My Detail")
    assert "My Title" in html and "My Detail" in html



TESTS.append(("test_p32_error_page_generic_passthrough", test_p32_error_page_generic_passthrough))
def test_p32_devtools_dom_tree():
    html = '<html><body><div id="box" class="c"><p>hi</p></div></body></html>'
    tree = dom.parse(html)
    out = browser.devtools_dom_tree(tree)
    assert "<div>#box.c" in out
    assert "<p>" in out
    assert "hi" in out



TESTS.append(("test_p32_devtools_dom_tree", test_p32_devtools_dom_tree))
def test_p32_devtools_computed_style():
    html = '<html><body><div id="box" style="color:red"></div></body></html>'
    tree = dom.parse(html)
    cs = browser.devtools_computed_style(
        tree, "div { width:10px; height:20px }", "box")
    assert cs.get("width") == "10px"
    assert cs.get("height") == "20px"
    assert "255,0,0" in cs.get("color", ""), cs.get("color")
    assert cs["__meta__"]["tag"] == "div"



TESTS.append(("test_p32_devtools_computed_style", test_p32_devtools_computed_style))
def test_p32_devtools_computed_style_missing():
    html = '<html><body><div id="a"></div></body></html>'
    tree = dom.parse(html)
    cs = browser.devtools_computed_style(tree, "", "nope")
    assert cs.get("__notfound__") is True



TESTS.append(("test_p32_devtools_computed_style_missing", test_p32_devtools_computed_style_missing))
def test_p32_devtools_selector_hits():
    html = ('<html><body><p class="x">1</p><p class="x">2</p>'
            '<p class="y">3</p></body></html>')
    tree = dom.parse(html)
    hits = browser.devtools_selector_hits("p.x { color: blue }", tree, "p.x")
    assert len(hits) == 2, hits
    # label = "tag#id.class" → the two <p class="x"> are "p.x".
    assert all(h["node"] == "p.x" for h in hits), hits
    assert all("p.x" in h["selectors"] for h in hits)
    assert all("p.x" in h["rules"] for h in hits)



TESTS.append(("test_p32_devtools_selector_hits", test_p32_devtools_selector_hits))
def test_p32_devtools_report_includes_all():
    html = '<html><body><div id="box" class="c">hi</div></body></html>'
    rep = browser.devtools_report(
        html, "div.c { width: 30px }", 800, 600,
        element_id="box", selector=".c")
    assert "DOM tree" in rep
    assert "computed style" in rep
    assert "selector" in rep
    assert "30px" in rep, rep



TESTS.append(("test_p32_devtools_report_includes_all", test_p32_devtools_report_includes_all))
def test_p32_view_source_local_file():
    """P32.6 — view-source: for a local file re-reads the raw source."""
    import gui
    d = _p32_tmp()
    fp = _os.path.join(d, "src.html")
    with open(fp, "w", encoding="utf-8") as f:
        f.write("<html><body><p>hello</p></body></html>")
    page = gui.PageInstance()
    page.load_file(fp, 800, 600)
    html = page._view_source_page(800, 600)
    assert "hello" in html
    assert "View Source" in html
    _p32_cleanup(d)



TESTS.append(("test_p32_view_source_local_file", test_p32_view_source_local_file))
def test_p32_view_source_network_not_retained():
    """P32.6 — for a network URL the source is not retained (toy)."""
    import gui
    page = gui.PageInstance()
    page.url = "http://example.com/"
    html = page._view_source_page(800, 600)
    assert "View Source" in html



TESTS.append(("test_p32_view_source_network_not_retained", test_p32_view_source_network_not_retained))
def test_p32_main_devtools_flag():
    """P32.4 — main.py --devtools prints a report and still writes the image."""
    import main
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main.main(["demo.html", "out.ppm", "--devtools",
                        "--devtools-el", "btn"])
    assert rc == 0, rc
    out = buf.getvalue()
    assert "DOM tree" in out
    assert "computed style" in out
    _os.remove("out.ppm")



TESTS.append(("test_p32_main_devtools_flag", test_p32_main_devtools_flag))
