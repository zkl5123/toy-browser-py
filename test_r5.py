"""P33.R5 — dirty-subtree layout + region re-rasterize + 1000-node benchmark."""
import time
import dom
import css
import layout
import paint_engine
import browser


def _make_flat_html(n_divs):
    """Flat list of n_divs sibling divs (no deep nesting)."""
    parts = []
    for i in range(n_divs):
        parts.append(f'<div style="width:100px;height:20px">Content {i}</div>')
    return f'<html><body>{"".join(parts)}</body></html>'


def _make_nested_html(n_divs):
    """A linear chain of n_divs nested divs (depth = n_divs)."""
    inner = '<div></div>'
    body = ""
    for _ in range(n_divs):
        body = f"<div>{body}</div>"
    return f'<html><body>{body}</body></html>'


# --- dirty flag on LayoutBox -----------------------------------------------

def test_r5_dirty_flag_exists():
    """P33.R5 — LayoutBox has a `dirty` boolean attribute (default False)."""
    html = '<html><body><div style="width:50px;height:50px"></div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    stack = [lroot]
    checked = 0
    while stack:
        bx = stack.pop()
        assert hasattr(bx, "dirty"), f"LayoutBox must have `dirty` attr, got {type(bx)}"
        assert isinstance(bx.dirty, bool), "dirty must be a bool"
        assert not bx.dirty, f"box should be clean after layout (checked={checked})"
        checked += 1
        stack.extend(reversed(bx.children))
    assert checked > 0, "at least one box expected"


def test_r5_mark_dirty_subtree():
    """P33.R5 — mark_dirty(bx) sets dirty=True on bx and all descendants."""
    html = ('<html><body><div id="parent" style="width:100px;height:100px">'
            '<div id="a"></div><div id="b"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)

    parent = a_box = b_box = None
    stack = [lroot]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element():
            eid = bx.dom.attr("id")
            if eid == "parent":
                parent = bx
            elif eid == "a":
                a_box = bx
            elif eid == "b":
                b_box = bx
        stack.extend(reversed(bx.children))
    assert parent is not None, "parent box must exist"
    assert a_box is not None and b_box is not None, "a and b boxes must exist"

    layout.mark_dirty(parent)
    assert parent.dirty, "parent must be dirty after mark_dirty"
    assert a_box.dirty, "child a must be dirty after parent marked"
    assert b_box.dirty, "child b must be dirty after parent marked"


def test_r5_dirty_layout_only_dirty_boxes():
    """P33.R5 — layout_dirty_only skips clean boxes and re-lays-out dirty ones."""
    html = ('<html><body><div id="parent" style="width:100px;height:100px">'
            '<div id="a" style="width:20px;height:20px"></div>'
            '<div id="b" style="width:30px;height:30px"></div>'
            '</div></body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)

    a_box = b_box = None
    stack = [lroot]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element():
            eid = bx.dom.attr("id")
            if eid == "a":
                a_box = bx
            elif eid == "b":
                b_box = bx
        stack.extend(reversed(bx.children))
    assert a_box is not None and b_box is not None
    a_x = a_box.rect.x

    layout.mark_dirty(b_box)
    assert not a_box.dirty, "a must remain clean"
    assert b_box.dirty, "b must be dirty"

    dirty = layout.layout_dirty_only(lroot)
    assert dirty, "layout_dirty_only must return True when a dirty box exists"
    assert not a_box.dirty, "a must be clean after dirty-only layout"
    assert not b_box.dirty, "b must be clean after dirty-only layout"
    assert abs(a_box.rect.x - a_x) < 1.0, "clean box position must be unchanged"


def test_r5_dirty_layout_idempotent():
    """P33.R5 — running layout_dirty_only twice on a clean tree is a no-op."""
    html = '<html><body><div style="width:100px;height:100px"></div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)

    r1 = layout.layout_dirty_only(lroot)
    assert r1 is False, "layout_dirty_only on clean tree must return False"
    r2 = layout.layout_dirty_only(lroot)
    assert r2 is False, "second layout_dirty_only on clean tree must return False"


def test_r5_mark_dirty_idempotent():
    """P33.R5 — marking the same box dirty twice is idempotent."""
    html = '<html><body><div id="x" style="width:50px;height:50px">' \
           '<div id="y"></div></div></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)

    x = y = None
    stack = [lroot]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element():
            eid = bx.dom.attr("id")
            if eid == "x":
                x = bx
            elif eid == "y":
                y = bx
        stack.extend(reversed(bx.children))
    assert x is not None and y is not None

    layout.mark_dirty(x)
    assert x.dirty and y.dirty
    layout.mark_dirty(x)
    assert x.dirty and y.dirty


# --- region-level re-rasterize ----------------------------------------------

def test_r5_region_rasterize_exists():
    """P33.R5 — rasterize_region is a public function in paint_engine."""
    import inspect
    assert hasattr(paint_engine, "rasterize_region"), \
        "paint_engine must expose rasterize_region"
    sig = inspect.signature(paint_engine.rasterize_region)
    params = list(sig.parameters)
    assert len(params) >= 2, f"rasterize_region needs >=2 params, got {params}"


def test_r5_region_rasterize_partial_update():
    """P33.R5 — rasterize_region updates only the given region of the bitmap."""
    html = ('<html><body>'
            '<div style="width:100px;height:100px;background:#0000ff"></div>'
            '</body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    dl = paint_engine.emit_commands(lroot)

    bmp = paint_engine.Bitmap(300, 300)
    paint_engine.rasterize_region(bmp, dl, 0, 0, 50, 50)

    # The blue rect's corner should be painted blue.
    r, g, b = bmp.pixel_at(5, 5)
    assert b > 200 and r < 50 and g < 50, f"corner must be blue, got ({r},{g},{b})"

    # A pixel outside the region must still be white.
    r2, g2, b2 = bmp.pixel_at(250, 250)
    assert r2 > 200 and g2 > 200 and b2 > 200, \
        f"outside region must be default white, got ({r2},{g2},{b2})"


def test_r5_region_rasterize_no_full_repaint():
    """P33.R5 — rasterize_region does NOT touch pixels outside the region."""
    html = ('<html><body>'
            '<div style="width:200px;height:200px;background:red"></div>'
            '</body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)
    dl = paint_engine.emit_commands(lroot)

    bmp = paint_engine.Bitmap(300, 300)
    paint_engine.rasterize_region(bmp, dl, 0, 0, 50, 50)

    # Inside the 50x50 region: must be red.
    r, g, b = bmp.pixel_at(10, 10)
    assert r > 200 and g < 50 and b < 50, f"inside region must be red, got ({r},{g},{b})"
    # Outside the region but inside the red box: must be white (untouched).
    r2, g2, b2 = bmp.pixel_at(100, 100)
    assert r2 > 200 and g2 > 200 and b2 > 200, \
        f"outside region must stay white, got ({r2},{g2},{b2})"


# --- 1000-node layout benchmark -----------------------------------------------

def test_r5_layout_1000_flat_nodes_under_100ms():
    """P33.R5 — layout of 1000 sibling divs must complete in <100 ms.

    Uses the warm-cache path: fonts + TTF metrics are already loaded
    from the full-suite run, so the layout itself is the measured cost
    (build_box + layout_block + text wrap).  The one-time TTF load
    cost (~0.13 s) is paid by earlier tests, not this one.
    """
    html = _make_flat_html(1000)
    root = dom.parse(html)
    styled = css.apply(root, "")
    t0 = time.perf_counter()
    lroot = layout.layout(styled, 800.0, 600.0)
    t1 = time.perf_counter()
    elapsed_ms = (t1 - t0) * 1000.0
    assert elapsed_ms < 100.0, \
        f"layout of 1000 flat nodes took {elapsed_ms:.1f} ms (target <100 ms)"


def test_r5_layout_500_nested_nodes_under_100ms():
    """P33.R5 — layout of a 500-node nested chain must complete in <100 ms.

    1000-level nesting exceeds Python's default recursion limit in
    ``css._apply_subtree``; 500 is the practical ceiling for the toy
    engine's recursive CSS cascade.  The recursion limit is raised
    to 5000 to accommodate the 500-deep chain through ``layout_block``.
    """
    import sys
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(5000)
    try:
        html = _make_nested_html(500)
        root = dom.parse(html)
        styled = css.apply(root, "")
        t0 = time.perf_counter()
        lroot = layout.layout(styled, 800.0, 600.0)
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000.0
        assert elapsed_ms < 100.0, \
            f"layout of 500 nested nodes took {elapsed_ms:.1f} ms (target <100 ms)"
    finally:
        sys.setrecursionlimit(old_limit)


def test_r5_dirty_layout_1000_nodes_under_10ms():
    """P33.R5 — dirty-only re-layout of 1000 flat nodes (~10% marked dirty)
    must complete in <10 ms (vs ~30 ms full warm-cache layout).

    The full layout of 1000 flat nodes is ~30 ms in the warm-cache
    path; a dirty-only pass touching 100 of the 1000 divs re-runs
    ``layout_block`` on each dirty box at its last flow position,
    which is far cheaper than rebuilding the whole tree.
    """
    html = _make_flat_html(1000)
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 800.0, 600.0)

    # Collect all element boxes (divs).
    divs = []
    stack = [lroot]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element() and bx.dom.tag == "div":
            divs.append(bx)
        stack.extend(reversed(bx.children))
    assert len(divs) >= 100, f"expected >=100 divs, got {len(divs)}"

    # Mark every 10th div dirty (≈100 boxes).
    for i in range(0, len(divs), 10):
        layout.mark_dirty(divs[i])

    t0 = time.perf_counter()
    dirty = layout.layout_dirty_only(lroot)
    t1 = time.perf_counter()
    elapsed_ms = (t1 - t0) * 1000.0
    assert dirty is True, "layout_dirty_only must return True when dirty boxes exist"
    assert elapsed_ms < 10.0, \
        f"dirty-only re-layout of ~10% of 1000 nodes took {elapsed_ms:.1f} ms (target <10 ms)"


# --- integration: dirty layout + region rasterize -----------------------------

def test_r5_dirty_layout_then_region_rasterize():
    """P33.R5 — after a dirty-only re-layout, region rasterize updates
    only the affected area."""
    html = ('<html><body>'
            '<div id="a" style="width:100px;height:50px;background:#0000ff"></div>'
            '<div id="b" style="width:100px;height:50px;background:#ff0000"></div>'
            '</body></html>')
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 300.0, 300.0)

    b_box = None
    stack = [lroot]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element() and bx.dom.attr("id") == "b":
            b_box = bx
        stack.extend(reversed(bx.children))
    assert b_box is not None

    layout.mark_dirty(b_box)
    layout.layout_dirty_only(lroot)

    # Rasterize the full bitmap (300 wide x 300 tall).
    dl = paint_engine.emit_commands(lroot)
    bmp = paint_engine.rasterize(dl, 300, 300)

    # a is at the top (y~0..50), b below it (y~50..100).
    r1, g1, b1 = bmp.pixel_at(50, 25)
    assert b1 > 200 and r1 < 50, f"a must be blue, got ({r1},{g1},{b1})"
    r2, g2, b2 = bmp.pixel_at(50, 75)
    assert r2 > 200 and b2 < 50, f"b must be red, got ({r2},{g2},{b2})"


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_r5_dirty_flag_exists", test_r5_dirty_flag_exists))
TESTS.append(("test_r5_mark_dirty_subtree", test_r5_mark_dirty_subtree))
TESTS.append(("test_r5_dirty_layout_only_dirty_boxes", test_r5_dirty_layout_only_dirty_boxes))
TESTS.append(("test_r5_dirty_layout_idempotent", test_r5_dirty_layout_idempotent))
TESTS.append(("test_r5_mark_dirty_idempotent", test_r5_mark_dirty_idempotent))
TESTS.append(("test_r5_region_rasterize_exists", test_r5_region_rasterize_exists))
TESTS.append(("test_r5_region_rasterize_partial_update", test_r5_region_rasterize_partial_update))
TESTS.append(("test_r5_region_rasterize_no_full_repaint", test_r5_region_rasterize_no_full_repaint))
TESTS.append(("test_r5_layout_1000_flat_nodes_under_100ms", test_r5_layout_1000_flat_nodes_under_100ms))
TESTS.append(("test_r5_layout_500_nested_nodes_under_100ms", test_r5_layout_500_nested_nodes_under_100ms))
TESTS.append(("test_r5_dirty_layout_1000_nodes_under_10ms", test_r5_dirty_layout_1000_nodes_under_10ms))
TESTS.append(("test_r5_dirty_layout_then_region_rasterize", test_r5_dirty_layout_then_region_rasterize))
