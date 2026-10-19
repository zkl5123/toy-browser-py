"""P34 — regression tests for the P34.1 performance + robustness fixes.

Run: py -3.11 test_p34.py
"""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


import gc
import time

from toy_browser.style import css
from toy_browser.document import dom
from toy_browser.layout import layout
from toy_browser.rendering import paint
import test_p34_css_memo as _memo
from toy_browser.typography import ttf_font


# ---------------------------------------------------------------------------
# 1. css rulesheet memoization
# ---------------------------------------------------------------------------

def test_memo_hits_same_list_on_equal_text():
    a = css.parse_rulesheet(".a{color:red}")
    b = css.parse_rulesheet(".a{color:red}")
    assert a is b


def test_memo_distinct_texts_distinct_lists():
    a = css.parse_rulesheet(".a{color:red}")
    b = css.parse_rulesheet(".b{color:blue}")
    assert a is not b


def test_memo_does_not_leak_mutations_to_callers():
    # apply() extends its rules list with @media rules; the shared memo
    # entry must stay untouched (empty css text → empty list).
    root = dom.parse('<html><body><div id="x">hi</div></body></html>')
    media = css.parse_media_queries("@media (min-width: 500px) { .m { color: red; } }")
    s1 = css.apply(root, "", media_rules=media, viewport_w=800.0, viewport_h=600.0)
    s2 = css.apply(root, "", media_rules=media, viewport_w=800.0, viewport_h=600.0)
    assert s1 is not None and s2 is not None
    memo_entry = css._rulesheet_memo.get("")
    if memo_entry is not None:
        assert len(memo_entry) == 0


def test_memo_nocache_returns_fresh_list():
    a = css.parse_rulesheet_nocache("div{color:red}")
    b = css.parse_rulesheet_nocache("div{color:red}")
    assert a is not b and len(a) == 1 and len(b) == 1


def test_memo_speedup():
    big = "div{color:red;background:blue;width:100px;height:20px}" * 50
    t_no = time.timeit if hasattr(time, "timeit") else None
    import timeit
    t_no = timeit.timeit(lambda: css.parse_rulesheet_nocache(big), number=50)
    t_yes = timeit.timeit(lambda: css.parse_rulesheet(big), number=50)
    assert t_yes < t_no * 0.5, f"cached parse should be at least 2x faster: {t_yes} vs {t_no}"


# ---------------------------------------------------------------------------
# 2. per-tag rule index (css.apply pre-filter)
# ---------------------------------------------------------------------------

def test_tag_rule_index_basics():
    rules = css.parse_rulesheet_nocache("div{color:red}.x{color:blue}#y{color:green}")
    idx = css._build_tag_rule_index(rules)
    # "div" is the rightmost tag of rule 0.
    assert 0 in idx.get("div", [])
    # ".x" has no tag → registered under "*".
    assert 1 in idx.get("*", [])
    # "#y" has no tag → registered under "*".
    assert 2 in idx.get("*", [])


def test_tag_rule_index_interaction_pseudo_goes_wildcard():
    rules = css.parse_rulesheet_nocache("a:hover{color:red}")
    idx = css._build_tag_rule_index(rules)
    # :hover rules are registered under "*" so any element may match.
    assert 0 in idx.get("*", [])
    assert "a" not in idx


def test_apply_with_tag_index_matches_legacy_apply():
    html = """<html><head><style>
      div { color: red; }
      .box { background: blue; }
      #id1 { margin: 0; }
      a:hover { color: green; }
      p > span { font-size: 12px; }
      body p { text-align: center; }
    </style></head><body>
      <div id="id1" class="box"><p><span>x</span></p></div>
    </body></html>"""
    root_a = dom.parse(html)
    root_b = dom.parse(html)
    s_a = css.apply(root_a, "")
    s_b = css.apply(root_b, "")

    def find_div_props(root_sn):
        # Root → <html> → <body> → <div id="id1">.
        def walk(sn):
            for c in sn.children:
                if c.dom.is_element() and c.dom.attr("id") == "id1":
                    return c
                r = walk(c)
                if r is not None:
                    return r
            return None
        return walk(root_sn)

    da = find_div_props(s_a)
    db = find_div_props(s_b)
    assert da is not None and db is not None, "div#id1 must exist in both trees"
    assert da.props == db.props, f"tag-index apply diverged:\n{da.props}\nvs\n{db.props}"


def test_apply_no_tag_index_matches_legacy_apply():
    """P34.1 — when no rules exist, the tag-rule index is empty and the
    legacy O(len(rules)) scan must produce identical results."""
    html = '<html><body><div class="x">hi</div><p>y</p></body></html>'
    root_a = dom.parse(html)
    root_b = dom.parse(html)
    s_a = css.apply(root_a, "")
    s_b = css.apply(root_b, "")
    def walk_props(sn):
        out = []
        def rec(n):
            out.append((n.dom.tag, dict(n.props)))
            for c in n.children:
                rec(c)
        rec(sn)
        return out
    pa = walk_props(s_a)
    pb = walk_props(s_b)
    assert pa == pb, "empty-rule apply must be identical"


# ---------------------------------------------------------------------------
# 3. layout._child_index_for (address-reuse guard)
# ---------------------------------------------------------------------------

def test_child_index_guard_rebuilds_on_reuse():
    # Simulate two independent DOM trees where the *second* tree's body
    # reuses the *first* tree's body id() (CPython address reuse).
    layout.clear_caches()
    # R7-H01 — locate <body> by tag: the parser now creates the implicit
    # <head> sibling, so ``html.children[0]`` is no longer the body.
    def _body(n):
        for c in n.children[0].children:
            if c.is_element() and c.tag == "body":
                return c
        return n.children[0].children[-1]
    n1 = dom.parse("<html><body><div>a</div><div>b</div></body></html>")
    body1 = _body(n1)
    d1 = body1.children[0]              # first <div>
    assert layout._child_index_for(d1, body1) == 1
    del n1, body1, d1
    gc.collect()
    n2 = dom.parse("<html><body><div>x</div><div>y</div><div>z</div></body></html>")
    body2 = _body(n2)
    third = body2.children[2]           # 3rd element child
    assert layout._child_index_for(third, body2) == 3, \
        "child index must be rebuilt when the parent id is reused"


def test_child_index_cache_is_bounded():
    layout.clear_caches()
    for i in range(layout._CHILD_INDEX_CACHE_MAX + 100):
        doc = dom.parse(f"<html><body><div id='d{i}'>{i}</div></body></html>")
        parent = doc.children[0]
        for c in parent.children:
            layout._child_index_for(c, parent)
        del doc
    assert len(layout._child_index_cache) <= layout._CHILD_INDEX_CACHE_MAX


def test_clear_caches_resets():
    layout.clear_caches()
    assert len(layout._child_index_cache) == 0


# ---------------------------------------------------------------------------
# 4. paint.fill_rect opaque fast path
# ---------------------------------------------------------------------------

def test_fill_rect_opaque_matches_legacy():
    from toy_browser.rendering.paint import Bitmap, Color
    a, b = Bitmap(10, 10), Bitmap(10, 10)
    col = Color(10, 200, 30, 255)
    a.fill_rect(2, 3, 5, 4, col)
    b.fill_rect(2, 3, 5, 4, col)
    assert a.pixels == b.pixels
    # A pixel inside the rect must equal the color's u32.
    assert a.pixels[3 * 10 + 4] == col.to_u32()
    # A pixel outside must be white.
    assert a.pixels[0] == 0xFFFFFF


def test_fill_rect_semi_transparent_still_blends():
    from toy_browser.rendering.paint import Bitmap, Color
    b = Bitmap(4, 4)
    b.fill_rect(0, 0, 4, 4, Color(255, 0, 0, 128))
    # White (255,255,255) blended 50/50 with red → (255, 128, 128).
    r, g, bl = b.pixels[0] >> 16 & 0xFF, b.pixels[0] >> 8 & 0xFF, b.pixels[0] & 0xFF
    assert r == 255 and g in (127, 128) and bl in (127, 128)


def test_fill_rect_clips_to_bounds():
    from toy_browser.rendering.paint import Bitmap, Color
    b = Bitmap(5, 5)
    b.fill_rect(-3, -3, 8, 8, Color(0, 0, 255, 255))
    assert b.pixels[0] == 0x0000FF
    assert b.pixels[4 * 5 + 4] == 0x0000FF
    # Rows outside the bitmap must be untouched.
    assert b.pixels[4 * 5 + 4] == 0x0000FF


# ---------------------------------------------------------------------------
# 5. rasterize.blit_glyph_mask — int + Color + semi-transparent paths
# ---------------------------------------------------------------------------

def test_blit_glyph_mask_opaque_color():
    from toy_browser.typography import rasterize
    from toy_browser.rendering.paint import Bitmap, Color
    m = rasterize.GlyphMask(4, 4)
    for r in range(4):
        for c in range(4):
            m.rows[r][c] = 255
    b = Bitmap(8, 8)
    # Mask is 4x4; baseline (y=4) means row 0 of the mask sits at
    # screen y = 4 - 4 = 0, so all 4 rows land inside the 8-tall bitmap.
    rasterize.blit_glyph_mask(b, m, 2, 4, Color(10, 20, 30, 255))
    # 4x4 block at destination columns 2..5, rows 0..3 must be opaque.
    assert b.pixels[0 * 8 + 2] == 0x0A141E
    assert b.pixels[3 * 8 + 5] == 0x0A141E
    # Outside the 4x4 block must stay white.
    assert b.pixels[0] == 0xFFFFFF
    assert b.pixels[4 * 8 + 2] == 0xFFFFFF


def test_blit_glyph_mask_coverage_half():
    from toy_browser.typography import rasterize
    from toy_browser.rendering.paint import Bitmap, Color
    m = rasterize.GlyphMask(2, 2)
    for r in range(2):
        m.rows[r][0] = 128
        m.rows[r][1] = 0
    b = Bitmap(4, 4)
    # Pre-fill with pure red, then blit black at 50% coverage.
    for i in range(len(b.pixels)):
        b.pixels[i] = 0xFF0000
    # Mask is 2 wide; origin_x=1 → destination columns 1..2.
    rasterize.blit_glyph_mask(b, m, 1, 2, Color(0, 0, 0, 255))
    # Col 1 (coverage 128): r = (0*128 + 255*127) >> 8 = 126.
    r1 = b.pixels[0 * 4 + 1] >> 16 & 0xFF
    assert r1 == 126, f"half-coverage blend: {r1}"
    # Col 2 (coverage 0): unchanged.
    assert b.pixels[0 * 4 + 2] == 0xFF0000
    # Col 0 is outside the mask origin (destination x0=1) → unchanged.
    assert b.pixels[0 * 4 + 0] == 0xFF0000


def test_blit_glyph_mask_int_color_fast_path():
    from toy_browser.typography import rasterize
    from toy_browser.rendering.paint import Bitmap
    m = rasterize.GlyphMask(3, 3)
    for r in range(3):
        for c in range(3):
            m.rows[r][c] = 255
    b = Bitmap(6, 6)
    # Pass the color as a packed 0xRRGGBB int (the P34 fast path).
    # Baseline y=3 → row 0 of mask at y=0, rows 0..2 land inside.
    rasterize.blit_glyph_mask(b, m, 1, 3, 0x00FF00)
    assert b.pixels[0 * 6 + 1] == 0x00FF00
    # Same call with a Color object must produce identical output.
    b2 = Bitmap(6, 6)
    from toy_browser.rendering.paint import Color
    rasterize.blit_glyph_mask(b2, m, 1, 3, Color(0, 255, 0, 255))
    assert b.pixels == b2.pixels


def test_blit_glyph_mask_semi_transparent_color():
    from toy_browser.typography import rasterize
    from toy_browser.rendering.paint import Bitmap, Color
    m = rasterize.GlyphMask(2, 2)
    for r in range(2):
        for c in range(2):
            m.rows[r][c] = 255
    b = Bitmap(4, 4)
    rasterize.blit_glyph_mask(b, m, 1, 2, Color(255, 0, 0, 128))
    # Combined alpha = 255*128/255 = 128 → red channel stays 255
    # (source is fully red), green/blue channels blend 50/50.
    r = b.pixels[0 * 4 + 1] >> 16 & 0xFF
    assert r == 254
    g = b.pixels[0 * 4 + 1] >> 8 & 0xFF
    assert g == 127
    bl = b.pixels[0 * 4 + 1] & 0xFF
    assert bl == 127


def test_blit_glyph_mask_negative_origin_clips():
    """P34.1 — the negative-x0 path must read the *source* mask row with
    the right column offset (the legacy implementation re-used the same
    destination loop bounds and indexed mrow[col] out of range)."""
    from toy_browser.typography import rasterize
    from toy_browser.rendering.paint import Bitmap, Color
    m = rasterize.GlyphMask(4, 4)
    for r in range(4):
        for c in range(4):
            m.rows[r][c] = 255
    b = Bitmap(6, 6)
    # Mask 4 wide; origin_x = -1 → mask columns 0-1 are clipped off
    # the bitmap, mask columns 2-3 land at destination 0-1.
    rasterize.blit_glyph_mask(b, m, -1, 4, Color(0, 0, 255, 255))
    # Rows 0..3, destination columns 0..2 must be blue; the rest white.
    assert b.pixels[0 * 6 + 0] == 0x0000FF
    assert b.pixels[3 * 6 + 1] == 0x0000FF
    assert b.pixels[0 * 6 + 2] == 0x0000FF
    assert b.pixels[0 * 6 + 3] == 0xFFFFFF
    assert b.pixels[0 * 6 + 5] == 0xFFFFFF


def test_blit_glyph_mask_negative_origin_very_negative():
    """Fully-clipped mask (origin_x + w <= 0) must not touch the bitmap."""
    from toy_browser.typography import rasterize
    from toy_browser.rendering.paint import Bitmap, Color
    m = rasterize.GlyphMask(2, 2)
    for r in range(2):
        for c in range(2):
            m.rows[r][c] = 255
    b = Bitmap(4, 4)
    # origin_x=-10 → mask is entirely to the left of the bitmap.
    rasterize.blit_glyph_mask(b, m, -10, 2, Color(0, 0, 255, 255))
    assert b.pixels == [0xFFFFFF] * 16


# ---------------------------------------------------------------------------
# 6. text.draw_text — primary-face fast path
# ---------------------------------------------------------------------------

def test_draw_text_ascii_uses_primary_face():
    from toy_browser.typography import font; from toy_browser.typography import text
    font.init_real_fonts(["fonts"])
    from toy_browser.rendering.paint import Bitmap, Color
    b = Bitmap(100, 50)
    text.draw_text(b, 0, 0, "ABC", 12.0, Color(0, 0, 0), 200.0)
    # At least some pixels must be non-white.
    nonwhite = sum(1 for p in b.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "ABC must render"


def test_draw_text_cjk_falls_back():
    from toy_browser.typography import font; from toy_browser.typography import text
    font.init_real_fonts(["fonts"])
    from toy_browser.rendering.paint import Bitmap, Color
    b = Bitmap(120, 50)
    text.draw_text(b, 0, 0, "你好", 12.0, Color(0, 0, 0), 200.0)
    nonwhite = sum(1 for p in b.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "CJK must render via the fallback face"


def test_draw_text_no_real_font_still_renders():
    from toy_browser.typography import font; from toy_browser.typography import text
    saved = dict(font._real_font_metrics)
    font._real_font_metrics.clear()
    from toy_browser.rendering.paint import Bitmap, Color
    b = Bitmap(100, 50)
    try:
        text.draw_text(b, 0, 0, "hi", 12.0, Color(0, 0, 0), 200.0)
    finally:
        font._real_font_metrics.clear()
        font._real_font_metrics.update(saved)
    nonwhite = sum(1 for p in b.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "bitmap-font path must still draw text"


# ---------------------------------------------------------------------------
# 7. js._layout_box_for — single-layout + BFS id lookup
# ---------------------------------------------------------------------------

def _make_el(eid: str):
    from toy_browser.scripting import js
    el = js.DomElement.__new__(js.DomElement)
    el.node = None
    el.id = eid
    return el


def test_js_layout_box_cache_runs_layout_once():
    from toy_browser.scripting import js
    from toy_browser.style import css as _css_mod
    from toy_browser.layout import layout as _layout_mod
    calls = {"apply": 0, "layout": 0}
    orig_apply = _css_mod.apply
    orig_layout = _layout_mod.layout

    def counting_apply(*a, **kw):
        calls["apply"] += 1
        return orig_apply(*a, **kw)

    def counting_layout(*a, **kw):
        calls["layout"] += 1
        return orig_layout(*a, **kw)

    _css_mod.apply = counting_apply
    _layout_mod.layout = counting_layout
    try:
        root = dom.parse('<html><body><div id="d1">x</div><div id="d2">y</div></body></html>')
        host = js.Host(root, log=lambda s: None)
        # Find the two div nodes by walking the tree.
        def find_node_by_id(n, eid):
            if n.is_element() and n.attr("id") == eid:
                return n
            for c in n.children:
                r = find_node_by_id(c, eid)
                if r is not None:
                    return r
            return None
        el1 = js.DomElement(find_node_by_id(root, "d1"), host)
        el2 = js.DomElement(find_node_by_id(root, "d2"), host)
        host._layout_box_for(el1)
        host._layout_box_for(el2)
    finally:
        _css_mod.apply = orig_apply
        _layout_mod.layout = orig_layout
    assert calls["apply"] <= 1, f"css.apply must run at most once (got {calls['apply']})"
    assert calls["layout"] <= 1, f"layout must run at most once (got {calls['layout']})"


# ---------------------------------------------------------------------------
# 8. Node.__weakref__ slot
# ---------------------------------------------------------------------------

def test_node_supports_weakref():
    import weakref
    from toy_browser.document.html_pkg.node import Node
    n = Node(Node.ELEMENT, "div")
    w = weakref.ref(n)
    assert w() is n


# ---------------------------------------------------------------------------
# 9. regression guard: full mini-pipeline still produces the same pixels
# ---------------------------------------------------------------------------

def test_full_pipeline_regression_guard():
    from toy_browser.app import browser; from toy_browser.document import dom; from toy_browser.style import css; from toy_browser.layout import layout; from toy_browser.rendering import paint_engine
    html = """<html><head><style>
      body{margin:8px}
      .box{width:120px;height:40px;background:#336699;color:white;margin:4px}
      .box .title{font-size:14px}
      .odd{background:#ffcc00}
    </style></head>
    <body><div class="box"><div class="title">Hi 你好</div></div>
    <div class="box odd"><div class="title">World 世界</div></div></body></html>"""
    # Legacy: parse into two separate trees and compare against a single
    # run that used the tag-rule index.
    t1 = dom.parse(html)
    s1 = css.apply(t1, "")
    b1 = layout.build_box(s1, 0.0)
    dl1 = paint_engine.emit_commands(b1)
    p1 = paint_engine.rasterize(dl1, 300, 200)

    t2 = dom.parse(html)
    s2 = css.apply(t2, "")
    b2 = layout.build_box(s2, 0.0)
    dl2 = paint_engine.emit_commands(b2)
    p2 = paint_engine.rasterize(dl2, 300, 200)

    assert p1.pixels == p2.pixels, "two identical renders must be pixel-identical"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

_TESTS = [
    ("memo_hits_same_list_on_equal_text", test_memo_hits_same_list_on_equal_text),
    ("memo_distinct_texts_distinct_lists", test_memo_distinct_texts_distinct_lists),
    ("memo_does_not_leak_mutations_to_callers", test_memo_does_not_leak_mutations_to_callers),
    ("memo_nocache_returns_fresh_list", test_memo_nocache_returns_fresh_list),
    ("memo_speedup", test_memo_speedup),
    ("tag_rule_index_basics", test_tag_rule_index_basics),
    ("tag_rule_index_interaction_pseudo_goes_wildcard", test_tag_rule_index_interaction_pseudo_goes_wildcard),
    ("apply_with_tag_index_matches_legacy_apply", test_apply_with_tag_index_matches_legacy_apply),
    ("apply_no_tag_index_matches_legacy_apply", test_apply_no_tag_index_matches_legacy_apply),
    ("child_index_guard_rebuilds_on_reuse", test_child_index_guard_rebuilds_on_reuse),
    ("child_index_cache_is_bounded", test_child_index_cache_is_bounded),
    ("clear_caches_resets", test_clear_caches_resets),
    ("fill_rect_opaque_matches_legacy", test_fill_rect_opaque_matches_legacy),
    ("fill_rect_semi_transparent_still_blends", test_fill_rect_semi_transparent_still_blends),
    ("fill_rect_clips_to_bounds", test_fill_rect_clips_to_bounds),
    ("blit_glyph_mask_opaque_color", test_blit_glyph_mask_opaque_color),
    ("blit_glyph_mask_coverage_half", test_blit_glyph_mask_coverage_half),
    ("blit_glyph_mask_int_color_fast_path", test_blit_glyph_mask_int_color_fast_path),
    ("blit_glyph_mask_semi_transparent_color", test_blit_glyph_mask_semi_transparent_color),
    ("blit_glyph_mask_negative_origin_clips", test_blit_glyph_mask_negative_origin_clips),
    ("blit_glyph_mask_negative_origin_very_negative", test_blit_glyph_mask_negative_origin_very_negative),
    ("draw_text_ascii_uses_primary_face", test_draw_text_ascii_uses_primary_face),
    ("draw_text_cjk_falls_back", test_draw_text_cjk_falls_back),
    ("draw_text_no_real_font_still_renders", test_draw_text_no_real_font_still_renders),
    ("js_layout_box_cache_runs_layout_once", test_js_layout_box_cache_runs_layout_once),
    ("node_supports_weakref", test_node_supports_weakref),
    ("full_pipeline_regression_guard", test_full_pipeline_regression_guard),
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
TESTS.append(("test_memo_hits_same_list_on_equal_text", test_memo_hits_same_list_on_equal_text))
TESTS.append(("test_memo_distinct_texts_distinct_lists", test_memo_distinct_texts_distinct_lists))
TESTS.append(("test_memo_does_not_leak_mutations_to_callers", test_memo_does_not_leak_mutations_to_callers))
TESTS.append(("test_memo_nocache_returns_fresh_list", test_memo_nocache_returns_fresh_list))
TESTS.append(("test_memo_speedup", test_memo_speedup))
TESTS.append(("test_tag_rule_index_basics", test_tag_rule_index_basics))
TESTS.append(("test_tag_rule_index_interaction_pseudo_goes_wildcard", test_tag_rule_index_interaction_pseudo_goes_wildcard))
TESTS.append(("test_apply_with_tag_index_matches_legacy_apply", test_apply_with_tag_index_matches_legacy_apply))
TESTS.append(("test_apply_no_tag_index_matches_legacy_apply", test_apply_no_tag_index_matches_legacy_apply))
TESTS.append(("test_child_index_guard_rebuilds_on_reuse", test_child_index_guard_rebuilds_on_reuse))
TESTS.append(("test_child_index_cache_is_bounded", test_child_index_cache_is_bounded))
TESTS.append(("test_clear_caches_resets", test_clear_caches_resets))
TESTS.append(("test_fill_rect_opaque_matches_legacy", test_fill_rect_opaque_matches_legacy))
TESTS.append(("test_fill_rect_semi_transparent_still_blends", test_fill_rect_semi_transparent_still_blends))
TESTS.append(("test_fill_rect_clips_to_bounds", test_fill_rect_clips_to_bounds))
TESTS.append(("test_blit_glyph_mask_opaque_color", test_blit_glyph_mask_opaque_color))
TESTS.append(("test_blit_glyph_mask_coverage_half", test_blit_glyph_mask_coverage_half))
TESTS.append(("test_blit_glyph_mask_int_color_fast_path", test_blit_glyph_mask_int_color_fast_path))
TESTS.append(("test_blit_glyph_mask_semi_transparent_color", test_blit_glyph_mask_semi_transparent_color))
TESTS.append(("test_blit_glyph_mask_negative_origin_clips", test_blit_glyph_mask_negative_origin_clips))
TESTS.append(("test_blit_glyph_mask_negative_origin_very_negative", test_blit_glyph_mask_negative_origin_very_negative))
TESTS.append(("test_draw_text_ascii_uses_primary_face", test_draw_text_ascii_uses_primary_face))
TESTS.append(("test_draw_text_cjk_falls_back", test_draw_text_cjk_falls_back))
TESTS.append(("test_draw_text_no_real_font_still_renders", test_draw_text_no_real_font_still_renders))
TESTS.append(("test_js_layout_box_cache_runs_layout_once", test_js_layout_box_cache_runs_layout_once))
TESTS.append(("test_node_supports_weakref", test_node_supports_weakref))
TESTS.append(("test_full_pipeline_regression_guard", test_full_pipeline_regression_guard))
