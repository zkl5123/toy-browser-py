"""P34.3 regression tests: viewport culling, text fast path, wrap decision."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dom
import css
import layout
import paint_engine
from paint import Bitmap, Color, WHITE

_pass = 0
_fail = 0


def ok(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"ok   {name}")
    else:
        _fail += 1
        print(f"FAIL {name}  {extra}")


# --- P34.3a: viewport culling in rasterize -------------------------------

def test_culling_offscreen_right():
    dl = paint_engine.DisplayList()
    dl.commands.append(paint_engine.DisplayCommand(
        "rect", 1200, 0, 100, 100, color=Color(255, 0, 0)))
    dl.commands.append(paint_engine.DisplayCommand(
        "rect", 0, 0, 100, 100, color=Color(0, 0, 255)))
    bmp = paint_engine.rasterize(dl, 1200, 800)
    # The red rect at x=1200 is fully off-screen → not painted.
    ok("culling: offscreen-right rect skipped",
       bmp.pixel_at(1199, 50) == (255, 255, 255),
       f"got {bmp.pixel_at(1199, 50)}")
    # The blue rect at (0,0) is painted.
    ok("culling: on-screen rect painted",
       bmp.pixel_at(50, 50) == (0, 0, 255),
       f"got {bmp.pixel_at(50, 50)}")


def test_culling_offscreen_bottom():
    dl = paint_engine.DisplayList()
    dl.commands.append(paint_engine.DisplayCommand(
        "rect", 0, 800, 100, 100, color=Color(255, 0, 0)))
    bmp = paint_engine.rasterize(dl, 1200, 800)
    ok("culling: offscreen-bottom rect skipped",
       bmp.pixel_at(50, 799) == (255, 255, 255))


def test_culling_negative_left():
    dl = paint_engine.DisplayList()
    dl.commands.append(paint_engine.DisplayCommand(
        "rect", -50, 0, 100, 100, color=Color(0, 255, 0)))
    bmp = paint_engine.rasterize(dl, 1200, 800)
    # -50 + 100 = 50 > 0 → partially visible: cols 0..49 are green.
    ok("culling: negative-left rect clipped",
       bmp.pixel_at(10, 50) == (0, 255, 0))
    # Fully off to the left (-100..-20) → skipped.
    dl2 = paint_engine.DisplayList()
    dl2.commands.append(paint_engine.DisplayCommand(
        "rect", -100, 0, 80, 100, color=Color(0, 0, 255)))
    bmp2 = paint_engine.rasterize(dl2, 1200, 800)
    ok("culling: fully-off-left rect skipped",
       bmp2.pixel_at(0, 50) == (255, 255, 255))


def test_culling_text_at_origin_zero_size():
    """A zero-area text cmd at (0,0) *is* drawn (its line sits at y=0)."""
    dl = paint_engine.DisplayList()
    dl.commands.append(paint_engine.DisplayCommand(
        "text", 0, 0, 0, 0, text="X", color=Color(0, 0, 0),
        font_px=14, max_width=100))
    bmp = paint_engine.rasterize(dl, 1200, 800)
    # Some pixel in the top-left region must be non-white.
    painted = any(bmp.pixels[y * 1200 + x] != WHITE
                  for y in range(0, 20) for x in range(0, 40))
    ok("culling: zero-area text at origin drawn", painted)


# --- P34.3b: text-node layout fast path ----------------------------------

def test_text_node_fast_path_box():
    html = '<div>内容 123</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.build_box(styled, 0.0)

    def walk(bx):
        yield bx
        for c in bx.children:
            yield from walk(c)
    text_boxes = [b for b in walk(tree) if b.box_type == layout.BoxType.TEXT]
    ok("text fast path: text box exists", len(text_boxes) >= 1,
       f"found {len(text_boxes)}")
    tb = text_boxes[0]
    ok("text fast path: text_content set", tb.text_content == "内容 123")
    ok("text fast path: dom is None", tb.dom is None)
    ok("text fast path: child_index set",
       isinstance(tb.child_index, int) and tb.child_index >= 0)
    # Inherit the parent's font family.
    ok("text fast path: font_family inherited",
       tb.font_family == (tb.parent.font_family if tb.parent else "monospace")
       or tb.font_family == "monospace",
       f"got {tb.font_family!r}")


def test_text_node_fast_path_color():
    html = '<div style="color:#ff0000">内容</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.build_box(styled, 0.0)

    def walk(bx):
        yield bx
        for c in bx.children:
            yield from walk(c)
    tb = [b for b in walk(tree) if b.box_type == layout.BoxType.TEXT][0]
    ok("text fast path: color resolved",
       tb.text_color is not None and tb.text_color.r == 255
       and tb.text_color.g == 0 and tb.text_color.b == 0,
       f"got {tb.text_color}")


def test_text_node_special_prop_goes_slow_path():
    """A text node carrying a special prop (line-clamp) skips the fast
    path (line-clamp is in _SPECIAL_PROPS) and preserves the value
    through the slow path instead.

    Mechanism (P6.2): line-clamp is read from the *element* box that owns
    the prop, then _build_children propagates it to TEXT-type children.
    So we set the prop on the <span> element's StyledNode and check the
    inner text box inherits it.
    """
    import layout as _L
    ok("line-clamp is a special prop", "line-clamp" in _L._SPECIAL_PROPS)
    root = dom.parse('<div><span>abc</span></div>')
    styled = css.apply(root, "")
    # Force a special prop onto the <span> element's StyledNode so that
    # build_box takes the slow path for it and stores line_clamp on the
    # inline box; _build_children then propagates it to the text child.
    for sn in styled.children:
        for csn in sn.children:
            csn.props["line-clamp"] = 2
    tree = layout.build_box(styled, 0.0)

    def walk(bx):
        yield bx
        for c in bx.children:
            yield from walk(c)
    tbs = [b for b in walk(tree) if b.box_type == layout.BoxType.TEXT]
    clamped = [b for b in tbs if b.line_clamp == 2]
    ok("text slow path: line-clamp propagated to text box",
       len(clamped) >= 1,
       f"text boxes={len(tbs)}, clamped={len(clamped)}")


# --- P34.3c: layout_block wrap decision ----------------------------------

def test_wrap_decision_single_line():
    """Short text that fits the available width → single-line box, no wrap."""
    import text as text_mod
    w = text_mod.measure("内容 123", 14.0)
    ok("wrap decision: short text measures under 100px", w < 100.0,
       f"w={w}")
    # The layout_block no-wrap path produces exactly one line.
    lines = [text_mod.wrap("内容 123", 1000.0, 14.0)][0]
    ok("wrap decision: single-line content",
       len(text_mod.wrap("内容 123", 1000.0, 14.0)) == 1)


def test_wrap_decision_overflow():
    """Text wider than available → wrap produces multiple lines."""
    import text as text_mod
    w = text_mod.measure("aaaa bbbb cccc dddd eeee", 14.0)
    ok("wrap decision: long text exceeds 50px", w > 50.0, f"w={w}")
    lines = text_mod.wrap("aaaa bbbb cccc dddd eeee", 50.0, 14.0)
    ok("wrap decision: overflow wraps to >1 lines", len(lines) > 1,
       f"lines={lines}")


def test_layout_block_no_wrap_short_text():
    """End-to-end: a div with short text lays out to a single-line box."""
    html = '<div>短文本</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.layout(styled, 1200, 800)

    def walk(bx):
        yield bx
        for c in bx.children:
            yield from walk(c)
    tbs = [b for b in walk(tree) if b.box_type == layout.BoxType.TEXT]
    ok("e2e: text box present", len(tbs) == 1, f"n={len(tbs)}")
    tb = tbs[0]
    # One line of 14px text: height = line_height_px (≈16.8 at 14px).
    ok("e2e: single-line box height ≈ line height",
       14.0 <= tb.rect.h <= 24.0,
       f"h={tb.rect.h}")


# --- P34.3d: emit stage micro-optimizations --------------------------------

def test_emit_layer_field_default():
    """P34.3: DisplayCommand declares _layer as a dataclass field
    defaulting to 0, so emit_commands can sort by it without a
    hasattr-based defaulting loop."""
    import paint_engine
    ok("_layer is a DisplayCommand field",
       "_layer" in paint_engine.DisplayCommand.__dataclass_fields__)
    ok("_layer defaults to 0",
       paint_engine.DisplayCommand.__dataclass_fields__["_layer"].default == 0)
    cmd = paint_engine.DisplayCommand("text", 0, 0, 0, 0)
    ok("fresh DisplayCommand._layer is 0", cmd._layer == 0)


def test_emit_commands_sorts_by_layer():
    """emit_commands sorts display commands by _layer (z-index)."""
    import paint_engine
    html = '<div style="z-index:2">B</div><div style="z-index:1">A</div>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.layout(styled, 1200, 800)
    d = paint_engine.emit_commands(tree)
    layers = [c._layer for c in d.commands]
    ok("emit_commands: layers are non-decreasing",
       all(layers[i] <= layers[i + 1] for i in range(len(layers) - 1)),
       f"layers={layers}")


def test_emit_commands_empty_tree():
    """emit_commands on a leaf box with no paintable fields still
    returns a well-formed DisplayList without error."""
    import paint_engine
    from layout import Rect, EdgeSizes
    bx = layout.LayoutBox(layout.BoxType.BLOCK, Rect(0, 0, 0, 0),
                         EdgeSizes(), EdgeSizes(), EdgeSizes())
    d = paint_engine.emit_commands(bx)
    ok("empty box: emit_commands returns a DisplayList",
       isinstance(d, paint_engine.DisplayList))


# --------------------------------------------------------------------------

def main():
    test_culling_offscreen_right()
    test_culling_offscreen_bottom()
    test_culling_negative_left()
    test_culling_text_at_origin_zero_size()
    test_text_node_fast_path_box()
    test_text_node_fast_path_color()
    test_text_node_special_prop_goes_slow_path()
    test_wrap_decision_single_line()
    test_wrap_decision_overflow()
    test_layout_block_no_wrap_short_text()
    test_emit_layer_field_default()
    test_emit_commands_sorts_by_layer()
    test_emit_commands_empty_tree()
    print(f"\n{_pass} passed, {_fail} failed")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_culling_offscreen_right", test_culling_offscreen_right))
TESTS.append(("test_culling_offscreen_bottom", test_culling_offscreen_bottom))
TESTS.append(("test_culling_negative_left", test_culling_negative_left))
TESTS.append(("test_culling_text_at_origin_zero_size", test_culling_text_at_origin_zero_size))
TESTS.append(("test_text_node_fast_path_box", test_text_node_fast_path_box))
TESTS.append(("test_text_node_fast_path_color", test_text_node_fast_path_color))
TESTS.append(("test_text_node_special_prop_goes_slow_path", test_text_node_special_prop_goes_slow_path))
TESTS.append(("test_wrap_decision_single_line", test_wrap_decision_single_line))
TESTS.append(("test_wrap_decision_overflow", test_wrap_decision_overflow))
TESTS.append(("test_layout_block_no_wrap_short_text", test_layout_block_no_wrap_short_text))
TESTS.append(("test_emit_layer_field_default", test_emit_layer_field_default))
TESTS.append(("test_emit_commands_sorts_by_layer", test_emit_commands_sorts_by_layer))
TESTS.append(("test_emit_commands_empty_tree", test_emit_commands_empty_tree))
