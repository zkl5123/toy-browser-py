
# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

# P33.R1 — CJK font rendering tests (real TTF/TTC + glyph cache + fallback)
# ---------------------------------------------------------------------------
# These tests exercise the R1 layer: the TTF/TTC parser (cmap, glyf,
# hmtx, loca), the CJK fallback face registration, the 8-bit glyph
# coverage cache, and the per-character face-resolution path in
# text.draw_text.  All tests are self-contained: they target the
# system's simhei.ttf (a clean plain-TTF CJK face) or the bundled
# arial.ttf fixture, and skip gracefully when the file is absent
# (non-Windows / CI hosts without CJK fonts).

import os as _os

_R1_SIMHEI = r"C:\Windows\Fonts\simhei.ttf"
_R1_SIMSUN = r"C:\Windows\Fonts\simsun.ttc"


def test_r1_is_ttc_detects_collection():
    import ttf_font
    if not _os.path.isfile(_R1_SIMSUN):
        return
    assert ttf_font.is_ttc(_R1_SIMSUN) is True
    assert ttf_font.is_ttc(_R1_SIMHEI) is False
    assert ttf_font.is_ttc("nonexistent_file.xyz") is False


def test_r1_ttc_face_count():
    import ttf_font
    if not _os.path.isfile(_R1_SIMSUN):
        return
    n = ttf_font.ttc_face_count(_R1_SIMSUN)
    assert isinstance(n, int) and n >= 1, f"expected >=1 face, got {n}"
    assert ttf_font.ttc_face_count(_R1_SIMHEI) == 0


def test_r1_load_ttc_face_zero():
    import ttf_font
    if not _os.path.isfile(_R1_SIMSUN):
        return
    fm = ttf_font.load_ttc(_R1_SIMSUN, 0)
    assert fm.num_glyphs >= 1000, f"too few glyphs: {fm.num_glyphs}"


def test_r1_load_face_best_cjk():
    import ttf_font
    if not _os.path.isfile(_R1_SIMSUN):
        return
    fm = ttf_font.load_face_best_cjk(_R1_SIMSUN)
    cjk = sum(1 for c in fm.cmap if 0x4E00 <= c <= 0x9FFF)
    assert cjk > 100, f"CJK face should map many codepoints, got {cjk}"


def test_r1_simhei_cjk_glyphs():
    import ttf_font
    if not _os.path.isfile(_R1_SIMHEI):
        return
    fm = ttf_font.load_ttf(_R1_SIMHEI)
    cjk = sum(1 for c in fm.cmap if 0x4E00 <= c <= 0x9FFF)
    assert cjk > 100, f"simhei should have CJK, got {cjk}"


def test_r1_find_cjk_font():
    import ttf_font
    m = ttf_font.find_cjk_font(ttf_font._system_font_dirs())
    assert m is not None, "no CJK font found on this system"
    assert m.num_glyphs > 1000


def test_r1_advance_width_missing_glyph():
    import ttf_font
    if not _os.path.isfile(_R1_SIMHEI):
        return
    fm = ttf_font.load_ttf(_R1_SIMHEI)
    adv = fm.advance_width("\uE000", 16.0)
    assert adv > 0


def test_r1_font_search_dirs():
    import ttf_font
    dirs = ttf_font._system_font_dirs()
    assert any(d.lower().endswith("fonts") for d in dirs), \
        f"system font dirs should include a fonts dir: {dirs}"


def test_r1_get_cjk_fallback_is_fontmetrics():
    import font
    font.init_real_fonts([])
    cjk = font.get_cjk_fallback()
    if cjk is not None:
        assert hasattr(cjk, "cmap")
        assert hasattr(cjk, "units_per_em")
        assert cjk.units_per_em > 0


def test_r1_text_draw_cjk_returns_height():
    import font, text, paint
    font.init_real_fonts([])
    bm = paint.Bitmap(200, 60, paint.WHITE)
    h = text.draw_text(bm, 0, 10, "中文", 16.0, paint.Color(0, 0, 0), 200.0)
    assert h > 0, f"draw_text should return height, got {h}"
    nonwhite = sum(1 for p in bm.pixels if p != paint.WHITE)
    assert nonwhite > 0, "CJK draw_text should render pixels"


def test_r1_glyph_mask_cache_cap():
    import text
    assert text._glyph_cache_max == 4096


def test_r1_cjk_glyph_outline_extractable():
    import ttf_font
    if not _os.path.isfile(_R1_SIMHEI):
        return
    fm = ttf_font.load_ttf(_R1_SIMHEI)
    outline = fm.glyph_outline("中")
    assert outline is not None, "中 outline should be extractable"
    assert len(outline.points) > 2, f"outline should have points, got {len(outline.points)}"


def test_r1_ttc_cjk_face_score():
    import ttf_font
    if not _os.path.isfile(_R1_SIMSUN):
        return
    fm = ttf_font.load_face_best_cjk(_R1_SIMSUN)
    cjk_count = sum(1 for c in fm.cmap if 0x4E00 <= c <= 0x9FFF)
    assert cjk_count > 500, f"best CJK face should map >500 ideographs, got {cjk_count}"


def test_r1_cjk_div_render():
    import browser, font, paint
    font.init_real_fonts([])
    html = '<div style="font-size:16px;width:200px;height:40px">中文</div>'
    bmp = browser.render_document(html, "", 300, 200)
    nonwhite = sum(1 for p in bmp.pixels if p != paint.WHITE)
    assert nonwhite > 20, f"CJK div should render pixels, got {nonwhite}"


def test_r1_advance_width_clamped():
    import ttf_font
    if not _os.path.isfile(_R1_SIMHEI):
        return
    fm = ttf_font.load_ttf(_R1_SIMHEI)
    adv = fm.advance_width("你", 16.0)
    assert adv <= 48.0, f"CJK advance should be clamped to 3em, got {adv}"


def test_r1_max_font_bytes_cap():
    import ttf_font
    assert ttf_font._MAX_FONT_BYTES == 12 * 1024 * 1024


def test_r1_glyph_bbox_valid():
    import ttf_font, rasterize
    if not _os.path.isfile(_R1_SIMHEI):
        return
    fm = ttf_font.load_ttf(_R1_SIMHEI)
    o = fm.glyph_outline("中")
    assert o is not None
    bbox = rasterize.glyph_bbox(o, 16.0 / fm.units_per_em)
    assert bbox is not None, "bbox should not be None for 中"
    x0, y0, x1, y1 = bbox
    assert x1 > x0, f"x range must be positive: {bbox}"
    assert y1 > y0, f"y range must be positive: {bbox}"


def test_r1_font_family_fallback_chain():
    import ttf_font
    m = ttf_font.find_font_for_family("nonexistentfamily123", 400, ["fonts"])
    assert m is not None, "fallback chain must land on a bundled TTF"


def test_r1_cjk_fallback_for_missing_glyph():
    import font, ttf_font
    font.init_real_fonts(["fonts"])
    primary = font.get_real_metrics("monospace")
    if primary is None:
        return
    assert primary.has_glyph("\u4e2d") == False, "ASCII font should not have CJK"
    resolved = font.resolve_metrics_for_char("\u4e2d", primary)
    assert resolved is not None, "CJK char must resolve to a fallback face"


def test_r1_advance_text_cjk():
    import font, text
    font.init_real_fonts([])
    w = text.advance_text("中文", 16.0, "monospace")
    assert w > 0, "CJK advance must be > 0"


def test_r1_glyph_mask_cache_hit():
    import text, font
    font.init_real_fonts(["fonts"])
    m = font.get_real_metrics("monospace")
    if m is None:
        return
    mask1 = text._get_glyph_mask("A", m, 16.0, 1.0)
    assert mask1 is not None
    mask2 = text._get_glyph_mask("A", m, 16.0, 1.0)
    assert mask2 is mask1, "same key must return the cached mask"


def test_r1_blit_coverage_render():
    import font, text, paint
    font.init_real_fonts(["fonts"])
    bm = paint.Bitmap(100, 40, paint.WHITE)
    ok = text._blit_coverage(bm, 0, 10, "A", 16.0, 1.0, paint.Color(0, 0, 0))
    assert ok is True, "blit_coverage must succeed for A with a real font"
    nonwhite = sum(1 for p in bm.pixels if p != paint.WHITE)
    assert nonwhite > 0, "blit_coverage must paint pixels"


def test_r1_parse_cmap_fmt4():
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    assert len(m.cmap) > 100, f"cmap should have >100 entries, got {len(m.cmap)}"


def test_r1_parse_cmap_cjk_absent():
    import ttf_font, font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    assert m.has_glyph("\u4e2d") == False, "arial should not have CJK"
    saved = font._cjk_fallback
    font._cjk_fallback = None
    try:
        assert m.has_glyph("\u4e2d") == False
    finally:
        font._cjk_fallback = saved


def test_r1_composite_glyph_cycle_guard():
    import ttf_font
    m = ttf_font.load_ttf("fonts/arial.ttf")
    o = m.glyph_outline("!")
    assert o is not None


def test_r1_glyf_read_clamped():
    import ttf_font, struct
    m = ttf_font.load_ttf("fonts/arial.ttf")
    gid = m.cmap.get(0x41, 0)
    raw = ttf_font._read_glyf(m, gid)
    assert len(raw) > 10, "glyf record should be > 10 bytes"
    raw2 = ttf_font._read_glyf(m, m.num_glyphs + 100)
    assert len(raw2) == 0, "out-of-range glyf must return empty"


def test_r1_num_contours_truncated_rejected():
    import ttf_font, struct
    m = ttf_font.load_ttf("fonts/arial.ttf")
    bad = struct.pack(">h", -18931) + b"\x00" * 100
    pts = ttf_font._parse_composite_glyph(bad, m, 0)
    assert len(pts) == 0, "truncated glyf must yield no points"


def test_r1_load_font_candidate_chain():
    import ttf_font
    cands = ttf_font._font_candidates("monospace", False, ["fonts", r"C:\Windows\Fonts"])
    assert len(cands) >= 1, "at least one candidate expected"
    m = ttf_font.load_font_for_family("monospace", 0.0, False, ["fonts"])
    assert m is not None, "monospace must resolve to a parseable font"


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_r1_is_ttc_detects_collection", test_r1_is_ttc_detects_collection))
TESTS.append(("test_r1_ttc_face_count", test_r1_ttc_face_count))
TESTS.append(("test_r1_load_ttc_face_zero", test_r1_load_ttc_face_zero))
TESTS.append(("test_r1_load_face_best_cjk", test_r1_load_face_best_cjk))
TESTS.append(("test_r1_simhei_cjk_glyphs", test_r1_simhei_cjk_glyphs))
TESTS.append(("test_r1_find_cjk_font", test_r1_find_cjk_font))
TESTS.append(("test_r1_advance_width_missing_glyph", test_r1_advance_width_missing_glyph))
TESTS.append(("test_r1_font_search_dirs", test_r1_font_search_dirs))
TESTS.append(("test_r1_get_cjk_fallback_is_fontmetrics", test_r1_get_cjk_fallback_is_fontmetrics))
TESTS.append(("test_r1_text_draw_cjk_returns_height", test_r1_text_draw_cjk_returns_height))
TESTS.append(("test_r1_glyph_mask_cache_cap", test_r1_glyph_mask_cache_cap))
TESTS.append(("test_r1_cjk_glyph_outline_extractable", test_r1_cjk_glyph_outline_extractable))
TESTS.append(("test_r1_ttc_cjk_face_score", test_r1_ttc_cjk_face_score))
TESTS.append(("test_r1_cjk_div_render", test_r1_cjk_div_render))
TESTS.append(("test_r1_advance_width_clamped", test_r1_advance_width_clamped))
TESTS.append(("test_r1_max_font_bytes_cap", test_r1_max_font_bytes_cap))
TESTS.append(("test_r1_glyph_bbox_valid", test_r1_glyph_bbox_valid))
TESTS.append(("test_r1_font_family_fallback_chain", test_r1_font_family_fallback_chain))
TESTS.append(("test_r1_cjk_fallback_for_missing_glyph", test_r1_cjk_fallback_for_missing_glyph))
TESTS.append(("test_r1_advance_text_cjk", test_r1_advance_text_cjk))
TESTS.append(("test_r1_glyph_mask_cache_hit", test_r1_glyph_mask_cache_hit))
TESTS.append(("test_r1_blit_coverage_render", test_r1_blit_coverage_render))
TESTS.append(("test_r1_parse_cmap_fmt4", test_r1_parse_cmap_fmt4))
TESTS.append(("test_r1_parse_cmap_cjk_absent", test_r1_parse_cmap_cjk_absent))
TESTS.append(("test_r1_composite_glyph_cycle_guard", test_r1_composite_glyph_cycle_guard))
TESTS.append(("test_r1_glyf_read_clamped", test_r1_glyf_read_clamped))
TESTS.append(("test_r1_num_contours_truncated_rejected", test_r1_num_contours_truncated_rejected))
TESTS.append(("test_r1_load_font_candidate_chain", test_r1_load_font_candidate_chain))
