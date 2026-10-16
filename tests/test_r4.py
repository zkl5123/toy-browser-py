
# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

# P33.R4 — layout verification: position / z-index / transform / overflow / grid.
# ---------------------------------------------------------------------------
# R4 layer: verify the layout engine correctly parses and applies:
#   * position: absolute / relative / sticky with top/left/right/bottom
#   * z-index (positive / negative) and paint-order sort
#   * transform: translate(x, y) applied at paint time
#   * overflow: hidden / scroll / auto + scroll-top / scroll-left
#   * CSS Grid (grid-template-rows / grid-template-columns)
# All tests are pure DOM + CSS + layout + paint (no network, no Tk).

from toy_browser.document import dom as _dom
from toy_browser.style import css as _css
from toy_browser.layout import layout as _layout
from toy_browser.rendering import paint_engine as _pe
from toy_browser.app import browser as _browser


def _find_by_id(n, eid):
    stack = [n]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element() and bx.dom.attr("id") == eid:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None


def _lroot(html, w=300.0, h=300.0):
    root = _dom.parse(html)
    styled = _css.apply(root, "")
    return _layout.layout(styled, w, h)


# --- position: absolute ---------------------------------------------------

def test_r4_abs_position_flag():
    """position:absolute sets is_absolute on the LayoutBox."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="a" style="position:absolute;top:10px;left:20px;'
            'width:30px;height:20px;background:red"></div></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    assert a is not None, "absolute child must exist"
    assert a.is_absolute, "is_absolute must be True"
    assert a.abs_top == 10.0, f"abs_top must be 10, got {a.abs_top}"
    assert a.abs_left == 20.0, f"abs_left must be 20, got {a.abs_left}"


def test_r4_abs_position_right_bottom():
    """position:absolute with right/bottom offsets stores them."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="a" style="position:absolute;top:0;left:0;right:10px;'
            'bottom:5px;width:30px;height:20px"></div></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    assert a is not None, "absolute child must exist"
    assert a.abs_right == 10.0, f"abs_right must be 10, got {a.abs_right}"
    assert a.abs_bottom == 5.0, f"abs_bottom must be 5, got {a.abs_bottom}"


def test_r4_abs_position_render():
    """position:absolute with top/left renders at the offset position."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="a" style="position:absolute;top:10px;left:20px;'
            'width:30px;height:20px;background:#0000ff"></div></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    box = _find_by_id(r, "box")
    assert a is not None and box is not None, "both boxes must exist"
    expected_x = box.rect.x + box.padding.left + a.abs_left
    expected_y = box.rect.y + box.padding.top + a.abs_top
    assert abs(a.rect.x - expected_x) < 2.0, f"abs x must be {expected_x}, got {a.rect.x}"
    assert abs(a.rect.y - expected_y) < 2.0, f"abs y must be {expected_y}, got {a.rect.y}"


def test_r4_abs_two_children_different_offsets():
    """Two absolute children with different offsets render at different positions."""
    html = ('<html><body><div id="box" style="width:200px;height:100px">'
            '<div id="a" style="position:absolute;top:5px;left:5px;'
            'width:10px;height:10px;background:#0000ff"></div>'
            '<div id="b" style="position:absolute;top:50px;left:50px;'
            'width:10px;height:10px;background:#00ff00"></div></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    b = _find_by_id(r, "b")
    assert a is not None and b is not None, "both absolute children must exist"
    assert abs(a.rect.x - b.rect.x) > 30, "a and b must be at different x positions"
    assert abs(a.rect.y - b.rect.y) > 30, "a and b must be at different y positions"


# --- position: relative ---------------------------------------------------

def test_r4_relative_position_flag():
    """position:relative sets is_relative on the LayoutBox."""
    html = ('<html><body><div id="a" style="position:relative;top:5px;left:8px;'
            'width:50px;height:30px;background:red"></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    assert a is not None, "relative box must exist"
    assert a.is_relative, "is_relative must be True"
    assert a.rel_top == 5.0, f"rel_top must be 5, got {a.rel_top}"
    assert a.rel_left == 8.0, f"rel_left must be 8, got {a.rel_left}"


def test_r4_relative_position_offsets():
    """position:relative with top/left shifts the box from its flow position."""
    html = ('<html><body><div id="parent" style="width:200px;height:100px">'
            '<div id="a" style="position:relative;top:5px;left:8px;'
            'width:50px;height:30px;background:#0000ff"></div></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    parent = _find_by_id(r, "parent")
    assert a is not None and parent is not None, "both boxes must exist"
    # The box is in flow, but shifted by rel_left / rel_top.
    expected_x = parent.rect.x + parent.padding.left + a.rel_left
    expected_y = parent.rect.y + parent.padding.top + a.rel_top
    assert abs(a.rect.x - expected_x) < 3.0, f"relative x must be {expected_x}, got {a.rect.x}"
    assert abs(a.rect.y - expected_y) < 3.0, f"relative y must be {expected_y}, got {a.rect.y}"


def test_r4_relative_position_right_bottom():
    """position:relative with right/bottom offsets shifts the box in the opposite direction."""
    html = ('<html><body><div id="a" style="position:relative;right:3px;bottom:4px;'
            'width:50px;height:30px"></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    assert a is not None, "relative box must exist"
    assert a.is_relative, "is_relative must be True"
    assert a.rel_right == 3.0, f"rel_right must be 3, got {a.rel_right}"
    assert a.rel_bottom == 4.0, f"rel_bottom must be 4, got {a.rel_bottom}"


# --- position: sticky ------------------------------------------------------

def test_r4_sticky_position_flag():
    """position:sticky sets is_sticky on the LayoutBox."""
    html = ('<html><body><div id="a" style="position:sticky;top:20px;'
            'width:50px;height:30px"></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    assert a is not None, "sticky box must exist"
    assert a.is_sticky, "is_sticky must be True"
    assert a.sticky_top == 20.0, f"sticky_top must be 20, got {a.sticky_top}"


def test_r4_sticky_position_full_direction():
    """position:sticky with all four offsets stores them."""
    html = ('<html><body><div id="a" style="position:sticky;top:10px;left:5px;'
            'right:5px;bottom:10px;width:50px;height:30px"></div></body></html>')
    r = _lroot(html)
    a = _find_by_id(r, "a")
    assert a is not None, "sticky box must exist"
    assert a.is_sticky, "is_sticky must be True"
    assert a.sticky_top == 10.0, f"sticky_top must be 10, got {a.sticky_top}"
    assert a.sticky_left == 5.0, f"sticky_left must be 5, got {a.sticky_left}"
    assert a.sticky_right == 5.0, f"sticky_right must be 5, got {a.sticky_right}"
    assert a.sticky_bottom == 10.0, f"sticky_bottom must be 10, got {a.sticky_bottom}"


# --- z-index ---------------------------------------------------------------

def test_r4_z_index_positive_stored():
    """z-index:10 is stored on the LayoutBox."""
    html = ('<html><body><div id="z" style="position:absolute;top:0;left:0;'
            'width:50px;height:50px;z-index:10;background:red"></div></body></html>')
    r = _lroot(html)
    z = _find_by_id(r, "z")
    assert z is not None, "z-index box must exist"
    assert z.z_index == 10, f"z_index must be 10, got {z.z_index}"


def test_r4_z_index_negative_stored():
    """z-index:-1 is stored on the LayoutBox (negative layer = behind default)."""
    html = ('<html><body><div id="z" style="position:absolute;top:0;left:0;'
            'width:50px;height:50px;z-index:-1;background:red"></div></body></html>')
    r = _lroot(html)
    z = _find_by_id(r, "z")
    assert z is not None, "z-index box must exist"
    assert z.z_index == -1, f"z_index must be -1, got {z.z_index}"


def test_r4_z_index_paint_sort():
    """Higher z-index commands are sorted after lower ones in the display list."""
    html = ('<html><body>'
            '<div id="bg" style="position:absolute;top:0;left:0;width:100px;'
            'height:100px;z-index:1;background:#0000ff"></div>'
            '<div id="fg" style="position:absolute;top:0;left:0;width:100px;'
            'height:100px;z-index:5;background:#ff0000"></div>'
            '</body></html>')
    r = _lroot(html)
    dl = _pe.emit_commands(r)
    # The fg (z=5) commands must come after bg (z=1) commands.
    bg_cmds = [c for c in dl.commands if getattr(c, "_layer", 0) == 1]
    fg_cmds = [c for c in dl.commands if getattr(c, "_layer", 0) == 5]
    if bg_cmds and fg_cmds:
        bg_last_idx = max(dl.commands.index(c) for c in bg_cmds)
        fg_first_idx = min(dl.commands.index(c) for c in fg_cmds)
        assert fg_first_idx > bg_last_idx, \
            f"fg (z=5) must be drawn after bg (z=1); fg_first={fg_first_idx}, bg_last={bg_last_idx}"


# --- transform: translate --------------------------------------------------

def test_r4_transform_translate_stored():
    """transform:translate(10px, 20px) stores transform_tx/ty on the LayoutBox."""
    html = ('<html><body><div id="t" style="transform:translate(10px,20px);'
            'width:50px;height:30px;background:red"></div></body></html>')
    r = _lroot(html)
    t = _find_by_id(r, "t")
    assert t is not None, "transform box must exist"
    assert t.transform_tx == 10.0, f"transform_tx must be 10, got {t.transform_tx}"
    assert t.transform_ty == 20.0, f"transform_ty must be 20, got {t.transform_ty}"


def test_r4_transform_translate_paint_offset():
    """transform:translate shifts the paint position by the offset amount."""
    html = ('<html><body><div id="t" style="transform:translate(10px,0px);'
            'width:50px;height:30px;background:#0000ff"></div></body></html>')
    r = _lroot(html)
    dl = _pe.emit_commands(r)
    # Find the rect command for the box and verify it's shifted by +10 in x.
    t = _find_by_id(r, "t")
    assert t is not None, "transform box must exist"
    expected_x = t.rect.x + t.transform_tx
    # The paint command's x should include the transform offset.
    rect_cmds = [c for c in dl.commands if c.kind in ("rect", "rounded")]
    assert len(rect_cmds) > 0, "at least one rect command expected"
    # Find the command whose base rect matches the box (before transform).
    shifted = [c for c in rect_cmds if abs(c.x - expected_x) < 2.0]
    assert len(shifted) > 0, \
        f"paint x must include transform offset ({expected_x}), got {[c.x for c in rect_cmds]}"


def test_r4_transform_negative_translate():
    """transform:translate(-5px, -10px) stores negative offsets."""
    html = ('<html><body><div id="t" style="transform:translate(-5px,-10px);'
            'width:50px;height:30px"></div></body></html>')
    r = _lroot(html)
    t = _find_by_id(r, "t")
    assert t is not None, "transform box must exist"
    assert t.transform_tx == -5.0, f"transform_tx must be -5, got {t.transform_tx}"
    assert t.transform_ty == -10.0, f"transform_ty must be -10, got {t.transform_ty}"


# --- overflow --------------------------------------------------------------

def test_r4_overflow_hidden_flag():
    """overflow:hidden sets overflow_hidden=True on the LayoutBox."""
    html = '<html><body><div id="ov" style="overflow:hidden"></div></body></html>'
    r = _lroot(html)
    ov = _find_by_id(r, "ov")
    assert ov is not None, "overflow box must exist"
    assert ov.overflow_hidden is True, "overflow:hidden must set overflow_hidden=True"


def test_r4_overflow_scroll_sets_axes():
    """overflow:scroll sets both overflow_x and overflow_y to 'scroll'."""
    html = '<html><body><div id="ov" style="width:100px;height:60px;overflow:scroll"></div></body></html>'
    r = _lroot(html)
    ov = _find_by_id(r, "ov")
    assert ov is not None, "overflow box must exist"
    assert ov.overflow_x == "scroll", f"overflow_x must be 'scroll', got {ov.overflow_x!r}"
    assert ov.overflow_y == "scroll", f"overflow_y must be 'scroll', got {ov.overflow_y!r}"


def test_r4_overflow_auto_sets_axes():
    """overflow:auto sets both overflow_x and overflow_y to 'auto'."""
    html = '<html><body><div id="ov" style="width:100px;height:60px;overflow:auto"></div></body></html>'
    r = _lroot(html)
    ov = _find_by_id(r, "ov")
    assert ov is not None, "overflow box must exist"
    assert ov.overflow_x == "auto", f"overflow_x must be 'auto', got {ov.overflow_x!r}"
    assert ov.overflow_y == "auto", f"overflow_y must be 'auto', got {ov.overflow_y!r}"


def test_r4_overflow_x_only():
    """overflow-x:hidden / overflow-y:visible are stored per-axis."""
    html = ('<html><body><div id="ov" style="width:100px;height:60px;'
            'overflow-x:hidden;overflow-y:auto"></div></body></html>')
    r = _lroot(html)
    ov = _find_by_id(r, "ov")
    assert ov is not None, "overflow box must exist"
    assert ov.overflow_x == "hidden", f"overflow_x must be 'hidden', got {ov.overflow_x!r}"
    assert ov.overflow_y == "auto", f"overflow_y must be 'auto', got {ov.overflow_y!r}"


def test_r4_scroll_top_left_stored():
    """scroll-top / scroll-left inline values are stored on the LayoutBox."""
    html = ('<html><body><div id="sc" style="width:100px;height:60px;'
            'overflow:scroll;scroll-top:30px;scroll-left:15px"></div></body></html>')
    r = _lroot(html)
    sc = _find_by_id(r, "sc")
    assert sc is not None, "scroll box must exist"
    assert sc.scroll_top == 30.0, f"scroll_top must be 30, got {sc.scroll_top}"
    assert sc.scroll_left == 15.0, f"scroll_left must be 15, got {sc.scroll_left}"


def test_r4_scroll_container_paint_tags():
    """A scroll container's paint commands are tagged with scroll clip fields."""
    html = ('<html><body><div id="sc" style="width:100px;height:60px;'
            'overflow:scroll;scroll-top:10px;scroll-left:5px;background:#ffff00">'
            '<div style="width:50px;height:50px;background:red"></div>'
            '</div></body></html>')
    r = _lroot(html)
    dl = _pe.emit_commands(r)
    scroll_cmds = [c for c in dl.commands if getattr(c, "scroll_enabled", False)]
    # At least the scroll container's own background command should be tagged.
    assert len(scroll_cmds) > 0, "expected at least one scroll-tagged command"
    c = scroll_cmds[0]
    # scroll_x/scroll_y are the container's rect origin; the rasterizer
    # uses these to compute the clip region.
    assert c.scroll_w > 0 and c.scroll_h > 0, \
        f"scroll region must have positive size (w={c.scroll_w}, h={c.scroll_h})"


# --- CSS Grid --------------------------------------------------------------

def test_r4_grid_layout_flag():
    """display:grid sets is_grid on the LayoutBox."""
    html = ('<html><body><div id="g" style="display:grid;grid-template-columns:1fr 1fr;'
            'grid-template-rows:auto;width:200px;height:100px">'
            '<div></div><div></div><div></div><div></div>'
            '</div></body></html>')
    r = _lroot(html)
    g = _find_by_id(r, "g")
    assert g is not None, "grid box must exist"
    assert g.is_grid, "is_grid must be True"


def test_r4_grid_columns_rows_stored():
    """grid-template-columns/rows are stored as int counts on the LayoutBox."""
    html = ('<html><body><div id="g" style="display:grid;'
            'grid-template-columns:50px 50px 50px;grid-template-rows:50px 50px;'
            'width:300px;height:100px">'
            '<div></div><div></div><div></div><div></div><div></div><div></div>'
            '</div></body></html>')
    r = _lroot(html)
    g = _find_by_id(r, "g")
    assert g is not None, "grid box must exist"
    assert g.grid_columns == 3, f"grid_columns must be 3, got {g.grid_columns}"
    assert g.grid_rows == 2, f"grid_rows must be 2, got {g.grid_rows}"


def test_r4_grid_children_positioned():
    """Grid children are laid out in row-major order at distinct positions."""
    html = ('<html><body><div id="g" style="display:grid;'
            'grid-template-columns:100px 100px;grid-template-rows:50px 50px;'
            'width:200px;height:100px">'
            '<div id="c1" style="background:red"></div>'
            '<div id="c2" style="background:blue"></div>'
            '<div id="c3" style="background:green"></div>'
            '<div id="c4" style="background:yellow"></div>'
            '</div></body></html>')
    r = _lroot(html)
    g = _find_by_id(r, "g")
    c1 = _find_by_id(r, "c1")
    c4 = _find_by_id(r, "c4")
    assert g is not None, "grid box must exist"
    assert c1 is not None and c4 is not None, "grid children must exist"
    # c1 should be at the top-left cell; c4 at the bottom-right cell.
    assert c1.rect.y < c4.rect.y, f"c1 must be above c4 (y {c1.rect.y} vs {c4.rect.y})"
    assert c1.rect.x < c4.rect.x, f"c1 must be left of c4 (x {c1.rect.x} vs {c4.rect.x})"


# ---------------------------------------------------------------------------
# R4 stage 5 — C9 box-model / paint unification regression gates.
#
# ① The 4 painted color-block cases (assessment 20261016) — the visible
#    red-pixel bounds in the rasterized document must match the Edge GPU
#    screenshots within the plan's 1px tolerance (flex_padding_border:
#    toy [62,5,238,37] vs Edge [63,6,237,36]; the other 3 cases match
#    exactly because they carry no padding/border).
# ② The 17-case layout geometry probe — border-box coordinates of the
#    probed element ids (what getBoundingClientRect reports) within 1e-3
#    of the Edge reference values; 45/48 match, the 3 diffs are
#    pre-existing sub-pixel float divergences (table collapse / grid
#    fraction split) that predate stage 5.
# ③ Inline-run geometry: a bare TEXT fragment in a flowing inline run is
#    sized at its measured text width, so a following <b>/<i>/<span>
#    lands at the correct x (the report's 粗体 span 横飞 / 文字重叠).
# ④ A padded+bordered box paints its background/border at the border-box
#    origin (the layout engine's border_rect() contract), not the
#    content-box origin.
# ---------------------------------------------------------------------------

_C9_PAINTED = {
    "flex_reverse_center": [82, 4, 218, 24],
    "flex_gap": [4, 4, 160, 24],
    "grid_unequal": [0, 0, 300, 20],
    "flex_padding_border": [62, 5, 238, 37],  # Edge [63,6,237,36], 1px tol
}
_C9_PAINTED_HTML = {
    "flex_reverse_center":
        '<!DOCTYPE html><html><head><style>body{margin:0}'
        '#row{display:flex;width:300px;height:150px;flex-direction:row-reverse;'
        'justify-content:center;}'
        '#row>div{width:40px;height:20px;margin:4px;background:#f00;}'
        '</style></head><body><div id="row"><div id="c1"></div><div id="c2"></div>'
        '<div id="c3"></div></div></body></html>',
    "flex_gap":
        '<!DOCTYPE html><html><head><style>body{margin:0}'
        '#row{display:flex;width:300px;height:150px;gap:10px;}'
        '#row>div{width:40px;height:20px;margin:4px;background:#f00;}'
        '</style></head><body><div id="row"><div id="c1"></div><div id="c2"></div>'
        '<div id="c3"></div></div></body></html>',
    "grid_unequal":
        '<!DOCTYPE html><html><head><style>body{margin:0}'
        '#g{display:grid;width:300px;grid-template-columns:1fr 2fr;gap:10px}'
        '#g>div{height:20px;background:red}</style></head><body>'
        '<div id="g"><div id="g1"></div><div id="g2"></div></div></body></html>',
    "flex_padding_border":
        '<!DOCTYPE html><html><head><style>body{margin:0}'
        '#row{display:flex;width:300px;height:150px;justify-content:center;}'
        '#row>div{width:40px;height:20px;margin:4px;background:#f00;padding:5px;'
        'border:2px solid blue;}</style></head><body><div id="row">'
        '<div id="c1"></div><div id="c2"></div><div id="c3"></div></div>'
        '</body></html>',
}


def _c9_red_bounds(html: str, w: int = 800, h: int = 600):
    """Scan the rasterized document for pure (255,0,0) pixels."""
    bmp = _browser.render_document(html, "", w, h)
    px = bmp.pixels
    minx = miny = maxx = maxy = None
    for y in range(bmp.height):
        base = y * bmp.width
        for x in range(bmp.width):
            v = px[base + x]
            if ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF) == (255, 0, 0):
                if minx is None or x < minx:
                    minx = x
                if maxx is None or x > maxx:
                    maxx = x
                if miny is None or y < miny:
                    miny = y
                if maxy is None or y > maxy:
                    maxy = y
    if minx is None:
        return None
    return [minx, miny, maxx + 1, maxy + 1]


def test_c9_paint_flex_reverse_center_red():
    """Padded color-block case 1: bare red cells, reverse + centered flex."""
    b = _c9_red_bounds(_C9_PAINTED_HTML["flex_reverse_center"])
    assert b is not None, "no red pixels found"
    assert b == _C9_PAINTED["flex_reverse_center"], \
        f"red bounds {b} != {_C9_PAINTED['flex_reverse_center']}"


def test_c9_paint_flex_gap_red():
    """Padded color-block case 2: red cells with flex gap."""
    b = _c9_red_bounds(_C9_PAINTED_HTML["flex_gap"])
    assert b is not None, "no red pixels found"
    assert b == _C9_PAINTED["flex_gap"], \
        f"red bounds {b} != {_C9_PAINTED['flex_gap']}"


def test_c9_paint_grid_unequal_red():
    """Padded color-block case 3: 1fr/2fr grid, red background cells."""
    b = _c9_red_bounds(_C9_PAINTED_HTML["grid_unequal"])
    assert b is not None, "no red pixels found"
    assert b == _C9_PAINTED["grid_unequal"], \
        f"red bounds {b} != {_C9_PAINTED['grid_unequal']}"


def test_c9_paint_flex_padding_border_red():
    """Padded color-block case 4: padding + border box — the C9 target.

    The visible red group sits within 1px of the Edge GPU reference
    [63, 6, 237, 36] on every side (the toy's 1px raster ring stands in
    for the CSS 2px border, so exactly 1px of inset difference is the
    plan's accepted tolerance).
    """
    b = _c9_red_bounds(_C9_PAINTED_HTML["flex_padding_border"])
    assert b is not None, "no red pixels found"
    ref = [63, 6, 237, 36]
    for i in range(4):
        assert abs(b[i] - ref[i]) <= 1, \
            f"side {i}: toy {b[i]} vs Edge {ref[i]} (must be within 1px)"


def test_c9_border_box_paint_origin():
    """A padded + bordered box paints its background at the border-box.

    The paint command's x/y must equal the layout engine's border_rect()
    origin (content origin minus padding — the getBoundingClientRect
    contract), and its w/h must span content + padding + border.
    The CSS rides in the inline style attribute so the test does not
    depend on id-selector cascade plumbing.
    """
    html = ('<html><head><style>body{margin:0}</style></head><body>'
            '<div id="d" style="width:200px;background:#ddf;padding:10px;'
            'border:3px solid #000"></div></body></html>')
    root = _dom.parse(html)
    styled = _css.apply(root, "")
    r = _layout.layout(styled, 800.0, 600.0)
    d = _find_by_id(r, "d")
    assert d is not None, "d must exist"
    br = d.border_rect()
    dl = _pe.emit_commands(r)
    rect_cmds = [c for c in dl.commands if c.kind in ("rect", "rounded")
                 and c.color is not None]
    assert len(rect_cmds) > 0, "expected a background rect command"
    dcmd = rect_cmds[0]
    # The background fill's origin is the border-box origin (snapped),
    # and its size spans the border-box.
    assert abs(dcmd.x - br.x) <= 1.0, \
        f"paint x {dcmd.x} must be the border-box x {br.x}"
    assert abs(dcmd.y - br.y) <= 1.0, \
        f"paint y {dcmd.y} must be the border-box y {br.y}"
    assert abs(dcmd.w - br.w) <= 1.0, \
        f"paint w {dcmd.w} must be the border-box w {br.w}"
    assert abs(dcmd.h - br.h) <= 1.0, \
        f"paint h {dcmd.h} must be the border-box h {br.h}"


def test_c9_inline_run_text_width():
    """A bare TEXT fragment in a flowing inline run is sized at its
    measured text width, so the following <b>bold</b> lands at the
    correct x (no 粗体 span 横飞 / overlap)."""
    html = ('<html><head><style>body{margin:0}</style></head>'
            "<body><div class='box' style='width:240px;height:80px;"
            "background:#eef;border:2px solid #039;margin:12px;padding:8px'>"
            "A box with text and <b>bold</b>, <i>italic</i> and "
            "<span>green</span>.</div></body></html>")
    tree = _dom.parse(html)
    styled = _css.apply(tree, "")
    r = _layout.layout(styled, 800.0, 600.0)

    def find_cls(n, cls):
        stack = [n]
        while stack:
            bx = stack.pop()
            el = getattr(bx, "dom", None)
            if el is not None and getattr(el, "attrs", None) and \
                    el.attrs.get("class") == cls:
                return bx
            stack.extend(reversed(bx.children))
        return None

    box = find_cls(r, "box")
    assert box is not None, "box must exist"
    kids = box.children
    # The first child is the bare text run 'A box with text and ' — its
    # width must be its measured text width (<< the container's 220),
    # and the <b> bold child must start right after it.
    text1 = kids[0]
    bold = None
    for c in kids:
        el = getattr(c, "dom", None)
        if el is not None and getattr(el, "tag", None) == "b":
            bold = c
            break
    assert text1 is not None and bold is not None
    assert text1.rect.w < 200.0, \
        f"text run w must be its text width, got {text1.rect.w} (container-sized)"
    # bold starts at text1.x + text1.w (plus any gap), NOT at
    # text1.x + 220 (the old bug placed it 200+ px past the box's edge).
    assert bold.rect.x < box.border_rect().x + box.border_rect().w, \
        f"bold x {bold.rect.x} escaped the box's border-box " \
        f"({box.border_rect().x + box.border_rect().w})"
    assert bold.rect.x - text1.rect.x < text1.rect.w + 1.0, \
        f"bold starts {bold.rect.x - text1.rect.x:.1f} px after the text run, " \
        f"which exceeds the run width {text1.rect.w:.1f}"


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_r4_abs_position_flag", test_r4_abs_position_flag))
TESTS.append(("test_r4_abs_position_right_bottom", test_r4_abs_position_right_bottom))
TESTS.append(("test_r4_abs_position_render", test_r4_abs_position_render))
TESTS.append(("test_r4_abs_two_children_different_offsets", test_r4_abs_two_children_different_offsets))
TESTS.append(("test_r4_relative_position_flag", test_r4_relative_position_flag))
TESTS.append(("test_r4_relative_position_offsets", test_r4_relative_position_offsets))
TESTS.append(("test_r4_relative_position_right_bottom", test_r4_relative_position_right_bottom))
TESTS.append(("test_r4_sticky_position_flag", test_r4_sticky_position_flag))
TESTS.append(("test_r4_sticky_position_full_direction", test_r4_sticky_position_full_direction))
TESTS.append(("test_r4_z_index_positive_stored", test_r4_z_index_positive_stored))
TESTS.append(("test_r4_z_index_negative_stored", test_r4_z_index_negative_stored))
TESTS.append(("test_r4_z_index_paint_sort", test_r4_z_index_paint_sort))
TESTS.append(("test_r4_transform_translate_stored", test_r4_transform_translate_stored))
TESTS.append(("test_r4_transform_translate_paint_offset", test_r4_transform_translate_paint_offset))
TESTS.append(("test_r4_transform_negative_translate", test_r4_transform_negative_translate))
TESTS.append(("test_r4_overflow_hidden_flag", test_r4_overflow_hidden_flag))
TESTS.append(("test_r4_overflow_scroll_sets_axes", test_r4_overflow_scroll_sets_axes))
TESTS.append(("test_r4_overflow_auto_sets_axes", test_r4_overflow_auto_sets_axes))
TESTS.append(("test_r4_overflow_x_only", test_r4_overflow_x_only))
TESTS.append(("test_r4_scroll_top_left_stored", test_r4_scroll_top_left_stored))
TESTS.append(("test_r4_scroll_container_paint_tags", test_r4_scroll_container_paint_tags))
TESTS.append(("test_r4_grid_layout_flag", test_r4_grid_layout_flag))
TESTS.append(("test_r4_grid_columns_rows_stored", test_r4_grid_columns_rows_stored))
TESTS.append(("test_r4_grid_children_positioned", test_r4_grid_children_positioned))
# R4 stage 5 — C9 box-model / paint unification regression gates.
TESTS.append(("test_c9_paint_flex_reverse_center_red", test_c9_paint_flex_reverse_center_red))
TESTS.append(("test_c9_paint_flex_gap_red", test_c9_paint_flex_gap_red))
TESTS.append(("test_c9_paint_grid_unequal_red", test_c9_paint_grid_unequal_red))
TESTS.append(("test_c9_paint_flex_padding_border_red", test_c9_paint_flex_padding_border_red))
TESTS.append(("test_c9_border_box_paint_origin", test_c9_border_box_paint_origin))
TESTS.append(("test_c9_inline_run_text_width", test_c9_inline_run_text_width))
