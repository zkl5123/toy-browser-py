# P33.R1 — memory governor + resource-governance tests.
# ---------------------------------------------------------------------------
# These tests exercise mem_guard (the 6 GB process-heap cap and the
# 12 MiB per-face file gate) and verify that the TTF loader respects
# both limits so a low-spec host never OOMs on a large CJK collection.

import os as _os


def test_mem_guard_cap_constants():
    import mem_guard
    assert mem_guard.MEMORY_CAP_MB == 6000, \
        f"6 GB cap expected, got {mem_guard.MEMORY_CAP_MB}"
    assert mem_guard.MAX_FACE_MB == 12, \
        f"12 MiB face gate expected, got {mem_guard.MAX_FACE_MB}"


def test_mem_guard_can_load_face_size_gate():
    import mem_guard
    assert mem_guard.can_load_face(12 * 1024 * 1024) is True
    assert mem_guard.can_load_face(13 * 1024 * 1024) is False
    assert mem_guard.can_load_face(20 * 1024 * 1024) is False
    assert mem_guard.can_load_face(1024) is True


def test_mem_guard_heap_mb_nonnegative():
    import mem_guard
    h = mem_guard.heap_mb()
    assert isinstance(h, float) and h >= 0.0


def test_mem_guard_check_budget_true_at_start():
    import mem_guard
    # At process start the heap is well under 6 GB.
    assert mem_guard.check_budget() is True


def test_mem_guard_enforce_cap_true_at_start():
    import mem_guard
    assert mem_guard.enforce_cap() is True


def test_mem_guard_peak_ge_heap():
    import mem_guard
    # Peak is always >= current heap (tracemalloc invariant).
    assert mem_guard.peak_mb() >= mem_guard.heap_mb() - 1.0


def test_ttf_load_respects_mem_guard():
    """A face whose file is > 12 MiB is rejected by _load_font_path
    even when the path exists on disk."""
    import ttf_font
    import tempfile
    # Create a 13 MB dummy file (the content is irrelevant; the size
    # gate fires before the parse).
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as f:
        f.write(b"\x00" * (13 * 1024 * 1024))
        tmp = f.name
    try:
        fm = ttf_font._load_font_path(tmp, ttf_font._MAX_FONT_BYTES)
        assert fm is None, "13 MB face must be rejected by the size gate"
    finally:
        _os.unlink(tmp)


def test_ttf_outline_cache_bounded():
    """The per-glyph outline cache on FontMetrics is capped at 4096
    entries so a CJK face with 28k glyphs does not hold every outline."""
    import ttf_font
    # The cap constant lives on FontMetrics (default 4096).
    assert ttf_font.FontMetrics._outline_cache_max == 4096
    # A fresh face has an empty outline cache.
    fm = ttf_font.FontMetrics()
    assert len(fm._outline_cache) == 0


def test_ttf_advance_array_capped():
    """The materialized hmtx advance array is capped at 4096 entries
    regardless of numGlyphs (a CJK face with 28k glyphs still gets a
    bounded array)."""
    import ttf_font
    simhei = r"C:\Windows\Fonts\simhei.ttf"
    if not _os.path.isfile(simhei):
        return
    fm = ttf_font.load_ttf(simhei)
    assert fm is not None
    assert len(fm.advances) <= 4096, \
        f"advance array must be <= 4096, got {len(fm.advances)}"


def test_mem_guard_process_rss_proxy():
    import mem_guard
    rss = mem_guard.process_rss_mb()
    assert rss >= 0.0


def test_font_registry_clear_and_reinit():
    """After a test clears the font registry, the next
    init_real_fonts call re-loads the families and the CJK face."""
    import font
    import ttf_font
    # Remember the current state.
    had_monospace = font.get_real_metrics("monospace") is not None
    had_cjk = font.get_cjk_fallback() is not None
    # Clear everything.
    font._real_font_metrics.clear()
    font._cjk_fallback = None
    ttf_font._font_cache.clear()
    # P33.R5 — also clear the text metrics memo so the re-init's
    # fresh faces are picked up by the next layout instead of
    # serving stale memoized (family, weight) entries.
    import text
    text.clear_metrics_memo()
    # Re-init.
    font.init_real_fonts(["fonts"])
    if had_monospace:
        assert font.get_real_metrics("monospace") is not None, \
            "monospace must re-load after clear + re-init"
    if had_cjk:
        assert font.get_cjk_fallback() is not None, \
            "CJK face must re-load after clear + re-init"


def test_r1_glyph_mask_cache_cap_constant():
    """The 8-bit glyph mask cache in text.py is capped at 4096
    entries (the constant is the governance knob; the eviction policy
    is 'stop storing new keys' once the cap is reached)."""
    import text
    assert text._glyph_cache_max == 4096


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_mem_guard_cap_constants", test_mem_guard_cap_constants))
TESTS.append(("test_mem_guard_can_load_face_size_gate", test_mem_guard_can_load_face_size_gate))
TESTS.append(("test_mem_guard_heap_mb_nonnegative", test_mem_guard_heap_mb_nonnegative))
TESTS.append(("test_mem_guard_check_budget_true_at_start", test_mem_guard_check_budget_true_at_start))
TESTS.append(("test_mem_guard_enforce_cap_true_at_start", test_mem_guard_enforce_cap_true_at_start))
TESTS.append(("test_mem_guard_peak_ge_heap", test_mem_guard_peak_ge_heap))
TESTS.append(("test_ttf_load_respects_mem_guard", test_ttf_load_respects_mem_guard))
TESTS.append(("test_ttf_outline_cache_bounded", test_ttf_outline_cache_bounded))
TESTS.append(("test_ttf_advance_array_capped", test_ttf_advance_array_capped))
TESTS.append(("test_mem_guard_process_rss_proxy", test_mem_guard_process_rss_proxy))
TESTS.append(("test_font_registry_clear_and_reinit", test_font_registry_clear_and_reinit))
TESTS.append(("test_r1_glyph_mask_cache_cap_constant", test_r1_glyph_mask_cache_cap_constant))
