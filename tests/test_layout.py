"""Layout: flex, grid, table, position, float, overflow — test module (GPT P1-2 split from test.py)."""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


from toy_browser.app import browser
from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout
from toy_browser.scripting import js
from toy_browser.typography import text
from toy_browser.rendering import paint
from toy_browser.rendering.paint import Bitmap, Color
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



TESTS.append(("test_display_none", test_display_none))
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



TESTS.append(("test_flex_row", test_flex_row))
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



TESTS.append(("test_flex_column", test_flex_column))
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



TESTS.append(("test_flex_justify_center", test_flex_justify_center))
def test_overflow_hidden_flag():
    """overflow:hidden is parsed into LayoutBox.overflow_hidden."""
    html = '<html><body><div style="overflow:hidden" id="ov"></div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    ov = _find_box_by_id(lroot, "ov")
    assert ov.overflow_hidden is True, "overflow:hidden must set overflow_hidden=True"



TESTS.append(("test_overflow_hidden_flag", test_overflow_hidden_flag))
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



TESTS.append(("test_flex_grow_distribution", test_flex_grow_distribution))
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



TESTS.append(("test_flex_align_items_center", test_flex_align_items_center))
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



TESTS.append(("test_flex_grow_column", test_flex_grow_column))
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



TESTS.append(("test_grid_columns", test_grid_columns))
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



TESTS.append(("test_grid_render_cells", test_grid_render_cells))
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



TESTS.append(("test_margin_shorthand_4val", test_margin_shorthand_4val))
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



TESTS.append(("test_padding_shorthand_2val", test_padding_shorthand_2val))
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



TESTS.append(("test_margin_shorthand_3val", test_margin_shorthand_3val))
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



TESTS.append(("test_border_radius_layout", test_border_radius_layout))
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



TESTS.append(("test_border_radius_render_clips_corner", test_border_radius_render_clips_corner))
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



TESTS.append(("test_box_shadow_render", test_box_shadow_render))
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



TESTS.append(("test_transform_translate_render", test_transform_translate_render))
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



TESTS.append(("test_float_left_layout", test_float_left_layout))
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



TESTS.append(("test_float_right_layout", test_float_right_layout))
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



TESTS.append(("test_abs_position_layout", test_abs_position_layout))
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



TESTS.append(("test_abs_position_render", test_abs_position_render))
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



TESTS.append(("test_abs_position_top_left_offset", test_abs_position_top_left_offset))
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



TESTS.append(("test_text_overflow_ellipsis_stored", test_text_overflow_ellipsis_stored))
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



TESTS.append(("test_line_clamp_layout", test_line_clamp_layout))
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



TESTS.append(("test_z_index_layout", test_z_index_layout))
def test_z_index_paint_sort():
    """P7.1 — higher z-index commands are drawn later (on top)."""
    html = ('<html><body><div id="bg" style="position:absolute;top:0;left:0;'
            'width:100px;height:100px;z-index:1;background:#0000ff"></div>'
            '<div id="fg" style="position:absolute;top:0;left:0;'
            'width:100px;height:100px;z-index:5;background:#ff0000"></div>'
            '</body></html>')
    from toy_browser.rendering import paint_engine
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



TESTS.append(("test_z_index_paint_sort", test_z_index_paint_sort))
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



TESTS.append(("test_table_layout", test_table_layout))
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



TESTS.append(("test_table_render_cells", test_table_render_cells))
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



TESTS.append(("test_table_cell_flags", test_table_cell_flags))
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



TESTS.append(("test_sticky_layout", test_sticky_layout))
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



TESTS.append(("test_sticky_full_direction", test_sticky_full_direction))
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



TESTS.append(("test_relative_position_layout", test_relative_position_layout))
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



TESTS.append(("test_relative_position_render", test_relative_position_render))
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



TESTS.append(("test_relative_without_offsets", test_relative_without_offsets))
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



TESTS.append(("test_abs_position_right_bottom", test_abs_position_right_bottom))
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



TESTS.append(("test_relative_right_bottom", test_relative_right_bottom))
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



TESTS.append(("test_abs_position_right_only", test_abs_position_right_only))
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



TESTS.append(("test_relative_full_direction", test_relative_full_direction))
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



TESTS.append(("test_clip_inset_layout", test_clip_inset_layout))
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



TESTS.append(("test_clip_inset_4val_layout", test_clip_inset_4val_layout))
def test_display_toggle_none():
    """P11.3 — display:none via JS removes the element's *content* from the
    layout; the box itself stays in the tree flagged with is_display_none
    so toggle tests can inspect it.
    """
    html = ('<html><body><div id="x" style="width:100px;height:50px;background:red">'
            '</div></body></html>')
    from toy_browser.scripting import js as js_mod
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



TESTS.append(("test_display_toggle_none", test_display_toggle_none))
def test_display_toggle_block():
    """P11.3 — switching display back to block re-includes the box."""
    html = ('<html><body><div id="x" style="width:100px;height:50px">'
            '</div></body></html>')
    from toy_browser.scripting import js as js_mod
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



TESTS.append(("test_display_toggle_block", test_display_toggle_block))
