"""P34 round-2 — regression tests for the P34.2 performance + robustness
fixes.  Run: py -3.11 test_p34_r2.py
"""
from __future__ import annotations

import time

import dom
import text
import font


def test_get_glyph_mask_uses_resolved_face():
    """P34.2 — the draw_text hot path must pass the *resolved* face
    (the CJK fallback for CJK codepoints on a Latin primary) to
    _get_glyph_mask, not the primary face.  Before P34.2 the mask
    was always looked up on the primary face, so a CJK char on a
    Latin font missed the cache and fell through to the ~44 ms
    curve path on every occurrence."""
    font.init_real_fonts(["fonts"])
    real = text._real_metrics_for("monospace")
    if real is None:
        return  # no real font — skip
    assert not real.has_glyph("你"), "primary face must lack the CJK glyph"
    # Primary face must NOT have a mask for 你 (its cmap lacks the
    # codepoint, so the outline lookup returns None → no mask).
    mask_on_primary = text._get_glyph_mask("你", real, 12.0, 1.0)
    assert mask_on_primary is None, \
        "primary face must not produce a mask for a CJK glyph"
    # The resolved face (CJK fallback) must have a valid mask.
    face = text._resolve_face("你", real)
    import font as _fontmod
    cjk = _fontmod.get_cjk_fallback()
    if cjk is None:
        return  # no CJK fallback — the curve path is acceptable
    assert face is cjk, "resolve_face must return the CJK fallback for CJK codepoints"
    mask = text._get_glyph_mask("你", face, 12.0, 1.0)
    assert mask is not None, "P34.2 must hit the mask cache via the resolved face"
    # A second call must be a cache hit (same mask object).
    mask2 = text._get_glyph_mask("你", face, 12.0, 1.0)
    assert mask2 is mask, "mask cache must return the cached mask object"


def test_draw_text_cjk_is_fast():
    """P34.2 — a CJK draw_text must not take the 44 ms curve path per
    character; the mask cache should make it an O(1) blit per char."""
    font.init_real_fonts(["fonts"])
    real = text._real_metrics_for("monospace")
    if real is None:
        return
    from paint import Bitmap, Color
    bmp = Bitmap(200, 100)
    text.draw_text(bmp, 0, 0, "你好", 12.0, Color(0, 0, 0), 100.0)
    t0 = time.perf_counter()
    text.draw_text(bmp, 0, 0, "你好世界", 12.0, Color(0, 0, 0), 100.0)
    dt = time.perf_counter() - t0
    # A CJK draw of 4 chars must be well under 50 ms (the legacy
    # curve path was ~44 ms *per char*, so 4 chars would be ~180 ms).
    assert dt < 0.050, f"P34.2 CJK draw_text too slow: {dt*1000:.1f} ms"


def test_advance_text_is_linear():
    """P34.2 — advance_text on a 10k-char string must run in O(n);
    the legacy O(n²) prefix-slice in wrap would be ~10⁸ char
    operations for 10k chars."""
    font.init_real_fonts(["fonts"])
    real = text._real_metrics_for("monospace")
    if real is None:
        return
    s = "a" * 10000
    t0 = time.perf_counter()
    total = text.advance_text(s, 12.0)
    dt = time.perf_counter() - t0
    assert total > 0
    # O(n) advance over 10k chars should complete well under 50 ms.
    assert dt < 0.050, f"P34.2 advance_text too slow: {dt*1000:.1f} ms for 10k chars"


def test_wrap_is_linear():
    """P34.2 — wrap() on a 5k-char string must run in O(n); the legacy
    implementation called measure(text[:i]) per break opportunity,
    giving O(n²) total work."""
    font.init_real_fonts(["fonts"])
    real = text._real_metrics_for("monospace")
    if real is None:
        return
    s = "word " * 1000  # 5000 chars
    t0 = time.perf_counter()
    lines = text.wrap(s, 100.0, 12.0)
    dt = time.perf_counter() - t0
    assert len(lines) > 1, "wrap must produce multiple lines"
    # O(n) wrap over 5k chars should complete well under 200 ms.
    assert dt < 0.200, f"P34.2 wrap too slow: {dt*1000:.1f} ms for 5k chars"
    # Sanity: every line must fit in the width (allow trailing space).
    for line in lines:
        w = text.measure(line, 12.0)
        # A line can slightly exceed width via the kinsoku pull, but
        # it must not be wildly over (5× width).
        assert w < 500.0, f"line too wide: {w:.0f}px ({line!r})"


def test_adv_width_table_is_bounded():
    """P34.2 — _adv_width_tables must not grow unboundedly."""
    import text as _t
    n_tables_before = len(_t._adv_width_tables)
    # Force a new (family, px) table.
    _t._adv_width_for("x", 12.0, "monospace")
    _t._adv_width_for("x", 14.0, "serif")
    assert len(_t._adv_width_tables) <= _t._adv_tables_max, \
        f"table count {len(_t._adv_width_tables)} exceeds cap"


# ---------------------------------------------------------------------------
# 5. P34.1 carry-over: blit_glyph_mask negative-x0 + c==255 fast path
# ---------------------------------------------------------------------------

def test_blit_glyph_mask_negative_origin_clips():
    """P34.2 — the negative-x0 path must read the *source* mask row with
    the right column offset (the legacy implementation re-used the same
    destination loop bounds and indexed mrow[col] out of range)."""
    import rasterize
    from paint import Bitmap, Color
    m = rasterize.GlyphMask(4, 4)
    for r in range(4):
        for c in range(4):
            m.rows[r][c] = 255
    b = Bitmap(6, 6)
    # Mask 4 wide; origin_x = -1 → first 3 columns are clipped, last
    # column lands at destination 2.
    rasterize.blit_glyph_mask(b, m, -1, 4, Color(0, 0, 255, 255))
    # Rows 0..3, destination columns 0..2 must be blue; the rest white.
    assert b.pixels[0 * 6 + 0] == 0x0000FF
    assert b.pixels[3 * 6 + 1] == 0x0000FF
    assert b.pixels[0 * 6 + 2] == 0x0000FF
    assert b.pixels[0 * 6 + 3] == 0xFFFFFF
    assert b.pixels[0 * 6 + 5] == 0xFFFFFF


def test_blit_glyph_mask_fully_clipped_left():
    """A mask entirely to the left of the bitmap must not touch it."""
    import rasterize
    from paint import Bitmap, Color
    m = rasterize.GlyphMask(2, 2)
    for r in range(2):
        for c in range(2):
            m.rows[r][c] = 255
    b = Bitmap(4, 4)
    rasterize.blit_glyph_mask(b, m, -10, 2, Color(0, 0, 255, 255))
    assert b.pixels == [0xFFFFFF] * 16


def test_blit_glyph_mask_c255_fast_path():
    """P34.2 — full-coverage pixels must be written directly as the
    packed source color (the legacy formula lost 1 to >>8 truncation)."""
    import rasterize
    from paint import Bitmap, Color
    m = rasterize.GlyphMask(3, 3)
    for r in range(3):
        for c in range(3):
            m.rows[r][c] = 255
    b = Bitmap(6, 6)
    rasterize.blit_glyph_mask(b, m, 1, 3, Color(10, 20, 30, 255))
    assert b.pixels[0 * 6 + 1] == 0x0A141E, \
        f"c==255 fast path must write the packed color exactly, got {hex(b.pixels[0*6+1])}"


# ---------------------------------------------------------------------------
# 6. P34.1 carry-over: layout._child_index_for address-reuse guard
# ---------------------------------------------------------------------------

def test_child_index_guard_rebuilds_on_reuse():
    import layout
    import gc
    layout.clear_caches()
    n1 = dom.parse("<html><body><div>a</div><div>b</div></body></html>")
    body1 = n1.children[0].children[0]
    d1 = body1.children[0]
    assert layout._child_index_for(d1, body1) == 1
    del n1, body1, d1
    gc.collect()
    n2 = dom.parse("<html><body><div>x</div><div>y</div><div>z</div></body></html>")
    body2 = n2.children[0].children[0]
    third = body2.children[2]
    assert layout._child_index_for(third, body2) == 3, \
        "child index must be rebuilt when the parent id is reused"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

_TESTS = [
    ("get_glyph_mask_uses_resolved_face", test_get_glyph_mask_uses_resolved_face),
    ("draw_text_cjk_is_fast", test_draw_text_cjk_is_fast),
    ("advance_text_is_linear", test_advance_text_is_linear),
    ("wrap_is_linear", test_wrap_is_linear),
    ("adv_width_table_is_bounded", test_adv_width_table_is_bounded),
    ("blit_glyph_mask_negative_origin_clips", test_blit_glyph_mask_negative_origin_clips),
    ("blit_glyph_mask_fully_clipped_left", test_blit_glyph_mask_fully_clipped_left),
    ("blit_glyph_mask_c255_fast_path", test_blit_glyph_mask_c255_fast_path),
    ("child_index_guard_rebuilds_on_reuse", test_child_index_guard_rebuilds_on_reuse),
]


def main():
    import sys
    failed = []
    for name, fn in _TESTS:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:
            import traceback
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
            failed.append(name)
    print(f"\n{len(_TESTS) - len(failed)}/{len(_TESTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_get_glyph_mask_uses_resolved_face", test_get_glyph_mask_uses_resolved_face))
TESTS.append(("test_draw_text_cjk_is_fast", test_draw_text_cjk_is_fast))
TESTS.append(("test_advance_text_is_linear", test_advance_text_is_linear))
TESTS.append(("test_wrap_is_linear", test_wrap_is_linear))
TESTS.append(("test_adv_width_table_is_bounded", test_adv_width_table_is_bounded))
TESTS.append(("test_blit_glyph_mask_negative_origin_clips", test_blit_glyph_mask_negative_origin_clips))
TESTS.append(("test_blit_glyph_mask_fully_clipped_left", test_blit_glyph_mask_fully_clipped_left))
TESTS.append(("test_blit_glyph_mask_c255_fast_path", test_blit_glyph_mask_c255_fast_path))
TESTS.append(("test_child_index_guard_rebuilds_on_reuse", test_child_index_guard_rebuilds_on_reuse))
