"""Paint: PNG, transitions, keyframes, opacity, filters — test module (GPT P1-2 split from test.py)."""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


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



TESTS.append(("test_opacity_render", test_opacity_render))
def test_opacity_stored_on_box():
    """opacity is read from CSS into the LayoutBox."""
    html = '<html><body><div style="opacity:0.3;width:40px" id="o"></div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    ob = _find_box_by_id(lroot, "o")
    assert ob.opacity == 0.3, f"opacity must be 0.3, got {ob.opacity}"



TESTS.append(("test_opacity_stored_on_box", test_opacity_stored_on_box))
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



TESTS.append(("test_png_encode", test_png_encode))
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



TESTS.append(("test_png_roundtrip_dimensions", test_png_roundtrip_dimensions))
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



TESTS.append(("test_ppm_png_equivalence", test_ppm_png_equivalence))
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



TESTS.append(("test_transition_color_interpolation", test_transition_color_interpolation))
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



TESTS.append(("test_transition_render_frames", test_transition_render_frames))
def test_transition_lerp_color():
    """P3.4 — lerp_color helper interpolates correctly."""
    import transition as trans

    c = trans.lerp_color(Color(0, 0, 0, 255), Color(100, 100, 100, 255), 0.5)
    assert c.r == 50 and c.g == 50 and c.b == 50, f"midpoint must be 50, got ({c.r},{c.g},{c.b})"



TESTS.append(("test_transition_lerp_color", test_transition_lerp_color))
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



TESTS.append(("test_keyframes_animation_render", test_keyframes_animation_render))
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



TESTS.append(("test_hover_pseudo_layout", test_hover_pseudo_layout))
def test_pseudo_class_specificity():
    """P8.1 — pseudo-class adds one class-level point to specificity."""
    sel = css.Selector.parse("#a:hover")
    # P25.1 — 4-tuple (inline, id, cls, type): id=1, cls=1 (pseudo adds 1), type=0
    assert sel.specificity == (0, 1, 1, 0), f"specificity must be (0,1,1,0), got {sel.specificity}"
    assert sel.pseudo == ":hover", f"pseudo must be :hover, got {sel.pseudo}"



TESTS.append(("test_pseudo_class_specificity", test_pseudo_class_specificity))
def test_pseudo_class_parse_focus():
    """P8.1 — .btn:focus parses correctly."""
    sel = css.Selector.parse(".btn:focus")
    assert sel.pseudo == ":focus", f"pseudo must be :focus, got {sel.pseudo}"
    # P25.1 — class + pseudo = 2 class-level points → (0,0,2,0)
    assert sel.specificity == (0, 0, 2, 0), f"specificity must be (0,0,2,0), got {sel.specificity}"



TESTS.append(("test_pseudo_class_parse_focus", test_pseudo_class_parse_focus))
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



TESTS.append(("test_hover_render_api", test_hover_render_api))
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



TESTS.append(("test_first_child_layout", test_first_child_layout))
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



TESTS.append(("test_last_child_layout", test_last_child_layout))
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



TESTS.append(("test_nth_child_odd", test_nth_child_odd))
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



TESTS.append(("test_nth_child_an_plus_b", test_nth_child_an_plus_b))
def test_nth_child_specificity():
    """P10.1 — :nth-child adds one class-level point to specificity."""
    sel = css.Selector.parse("li:nth-child(2)")
    # P25.1 — 4-tuple (inline, id, cls, type): li type=1, :nth-child cls=1
    assert sel.specificity_sum() == (0, 0, 1, 1), f"got {sel.specificity_sum()}"



TESTS.append(("test_nth_child_specificity", test_nth_child_specificity))
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


TESTS.append(("test_outline_layout", test_outline_layout))
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



TESTS.append(("test_outline_render", test_outline_render))
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



TESTS.append(("test_outline_width_only", test_outline_width_only))
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



TESTS.append(("test_transition_multi_property", test_transition_multi_property))
def test_transition_transform_lerp():
    """P11.1 — TransitionSpec.value_at lerps transform translate tuples."""
    import transition as t
    spec = t.TransitionSpec("transform", ("translate", 0.0, 0.0), ("translate", 100.0, 50.0), 10)
    v = spec.value_at(0.5)
    assert isinstance(v, tuple), f"transform value_at must return tuple, got {v!r}"
    assert v[0] == "translate"
    assert abs(v[1] - 50.0) < 0.01, f"translate x at t=0.5 must be 50.0, got {v[1]}"
    assert abs(v[2] - 25.0) < 0.01, f"translate y at t=0.5 must be 25.0, got {v[2]}"



TESTS.append(("test_transition_transform_lerp", test_transition_transform_lerp))
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



TESTS.append(("test_transition_parse_multi", test_transition_parse_multi))
def test_transition_color_lerp():
    """P11.1 — TransitionSpec lerps Color values correctly."""
    import transition as t
    from paint import Color
    spec = t.TransitionSpec("background", Color(0, 0, 0), Color(255, 255, 255), 10)
    v = spec.value_at(0.5)
    assert isinstance(v, Color), f"color lerp must return Color, got {type(v)}"
    assert 120 <= v.r <= 135, f"r at t=0.5 must be ~128, got {v.r}"
    assert 120 <= v.g <= 135, f"g at t=0.5 must be ~128, got {v.g}"



TESTS.append(("test_transition_color_lerp", test_transition_color_lerp))
def test_transition_scalar_lerp():
    """P11.1 — TransitionSpec lerps scalar (opacity) values."""
    import transition as t
    spec = t.TransitionSpec("opacity", 1.0, 0.0, 10)
    assert abs(spec.value_at(0.25) - 0.75) < 0.01, \
        f"opacity at t=0.25 must be 0.75, got {spec.value_at(0.25)}"
    assert abs(spec.value_at(0.0) - 1.0) < 0.01
    assert abs(spec.value_at(1.0) - 0.0) < 0.01



TESTS.append(("test_transition_scalar_lerp", test_transition_scalar_lerp))
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



TESTS.append(("test_transition_advance_stops", test_transition_advance_stops))
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



TESTS.append(("test_keyframe_multi_property_tracks", test_keyframe_multi_property_tracks))
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



TESTS.append(("test_keyframes_three_stop", test_keyframes_three_stop))
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



TESTS.append(("test_animate_keyframes_multi_render", test_animate_keyframes_multi_render))
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



TESTS.append(("test_hover_at_coordinate", test_hover_at_coordinate))
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



TESTS.append(("test_mouse_events_sequence", test_mouse_events_sequence))
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



TESTS.append(("test_mouse_events_runs_onclick", test_mouse_events_runs_onclick))
def test_keyframes_iteration_param():
    """P12.5 — AnimationHandle carries the animation iteration count."""
    blocks = css.parse_keyframes('@keyframes k { 0% { opacity: 1; } 100% { opacity: 0; } }')
    anim = blocks["k"]
    handle = css.build_keyframe_handle(anim)
    assert handle.iterations == anim.iterations, \
        f"handle.iterations must mirror anim.iterations, got {handle.iterations}"



TESTS.append(("test_keyframes_iteration_param", test_keyframes_iteration_param))
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



TESTS.append(("test_keyframes_multi_property_2", test_keyframes_multi_property_2))
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



TESTS.append(("test_mouse_mouseleave_event", test_mouse_mouseleave_event))
