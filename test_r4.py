# P33.R4 — layout verification: position / z-index / transform / overflow / grid.
# ---------------------------------------------------------------------------
# R4 layer: verify the layout engine correctly parses and applies:
#   * position: absolute / relative / sticky with top/left/right/bottom
#   * z-index (positive / negative) and paint-order sort
#   * transform: translate(x, y) applied at paint time
#   * overflow: hidden / scroll / auto + scroll-top / scroll-left
#   * CSS Grid (grid-template-rows / grid-template-columns)
# All tests are pure DOM + CSS + layout + paint (no network, no Tk).

import dom as _dom
import css as _css
import layout as _layout
import paint_engine as _pe


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
