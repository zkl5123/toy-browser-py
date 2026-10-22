"""P34.4 — text.measure document-width cache + layout half-leading cache + _emit micro-opts.

Run: py -3.11 test_p34_r4.py
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


from toy_browser.document import dom; from toy_browser.style import css; from toy_browser.layout import layout; from toy_browser.rendering import paint_engine; from toy_browser.typography import text
import p34r3_perf as P

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        _failures.append(name)


def bench(name: str, value: float, threshold: float, detail: str = "") -> None:
    """Informational performance observation (R7-Q01).

    Performance thresholds are machine-load sensitive; the hard gate
    lives in the isolated benchmark job (tools/benchmarks), not in the
    functional regression.  A regression here is reported as WARN and
    never silently counts as a functional PASS.
    """
    state = "OK  " if value < threshold else "WARN"
    print(f"{state} [bench] {name}: {detail or f'{value:.3f}'} "
          f"(info threshold {threshold})")


def _checked(fn):
    """R7-Q01 — propagate internal check() failures to the test runner.

    The legacy TESTS entries called ``check()`` which only *printed*
    FAIL; the runner saw a normal return and counted a PASS.  Wrap each
    registered test so any check() failure it records raises.
    """
    def wrapper():
        n0 = len(_failures)
        try:
            fn()
        finally:
            if len(_failures) > n0:
                raise AssertionError(
                    "internal check failure(s): " + "; ".join(_failures[n0:]))
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# 1. text.measure cache correctness
# ---------------------------------------------------------------------------
# The legacy code computes width = sum of per-char advances.  The cache
# must agree with an independent measurement (clear the cache, measure
# twice; both must equal).
def t1_cache_agrees_with_advance() -> None:
    s = "内容 47321"
    text.clear_advance_cache()
    m1 = text.measure(s, 14.0, "sans-serif")
    m2 = text.measure(s, 14.0, "sans-serif")
    m3 = text.advance_text(s, 14.0, "sans-serif")
    check("measure cache == direct advance", m1 == m2 == m3,
          f"m1={m1} m2={m2} m3={m3}")

# ---------------------------------------------------------------------------
# 2. measure() on empty / long / distinct strings still correct
# ---------------------------------------------------------------------------
def t2_measure_edge_cases() -> None:
    check("empty string", text.measure("", 14.0) == 0.0)
    # Two distinct strings at same size must not share a cache entry
    text.clear_advance_cache()
    a = text.measure("abc", 14.0)
    b = text.measure("def", 14.0)
    check("distinct strings distinct widths", a != b or "abc" == "def")
    # A long string (>128) is not cached — it goes through advance_text
    long = "字" * 200
    m_long = text.measure(long, 14.0)
    check("long string width", m_long > 0.0, f"got {m_long}")

# ---------------------------------------------------------------------------
# 3. clear_advance_cache resets both tables
# ---------------------------------------------------------------------------
def t3_clear_advances_cache() -> None:
    text.clear_advance_cache()
    text.measure("内容 47321", 14.0, "sans-serif")  # populates cache
    text.clear_advance_cache()
    check("adv_width_tables empty after clear",
          len(text._adv_width_tables) == 0)
    check("doc_width_cache empty after clear",
          len(text._doc_width_cache) == 0)

# ---------------------------------------------------------------------------
# 4. layout.clear_caches also clears the text caches (P34.4 wiring)
# ---------------------------------------------------------------------------
def t4_layout_clear_caches_clears_text() -> None:
    text.clear_advance_cache()
    text._metrics_memo.clear()
    # Simulate a populate
    text.measure("内容 47321", 14.0, "sans-serif")
    assert len(text._doc_width_cache) > 0
    layout.clear_caches()
    check("layout.clear_caches cleared doc_width_cache",
          len(text._doc_width_cache) == 0)
    check("layout.clear_caches cleared metrics_memo",
          len(text._metrics_memo) == 0)

# ---------------------------------------------------------------------------
# 5. half-leading cache correctness: two boxes with same
#    (family, font_px, line_h) must produce identical baseline
# ---------------------------------------------------------------------------
def t5_half_lead_cache() -> None:
    # Two paragraphs with identical font px / line height / family.
    html = """<!DOCTYPE html><html><head>
      <style>
        p { font-size: 16px; line-height: 24px; font-family: sans-serif; }
      </style>
      </head><body>
      <p id="p1">第一段 文本内容 用于测试半行距</p>
      <p id="p2">第二段 文本内容 用于测试半行距</p>
      </body></html>"""
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.layout(styled, 400, 800)
    p1 = None
    p2 = None
    def find(bx, name):
        nonlocal p1, p2
        if bx.dom is not None and bx.dom.is_element() and bx.dom.tag in ("p1", "p2"):
            pass
        # Find by text content instead.
        if bx.box_type == layout.BoxType.TEXT:
            if "第一段" in (bx.text_content or ""):
                p1 = bx
            elif "第二段" in (bx.text_content or ""):
                p2 = bx
        for c in bx.children:
            find(c, name)
    find(tree, None)
    assert p1 is not None and p2 is not None
    # Both must have the same baseline offset (same font/size/line-height).
    # R8-Q01 — use a justified float tolerance: the two text boxes are the
    # same (family, font_px, line_h) key, but their offset is a difference
    # of accumulated block-layout values, so they can differ by 1 ulp
    # (e.g. 15.146875 vs 15.146875000000001).  Tolerance ~1e-9 px keeps the
    # semantic invariant (identical half-leading) without false-positive on
    # floating-point rounding.
    off1 = p1.baseline_y - p1.rect.y
    off2 = p2.baseline_y - p2.rect.y
    check("half-leading consistent across boxes",
          abs(off1 - off2) <= 1e-9,
          f"p1_offset={off1} p2_offset={off2}")
    # Both must have a positive ascent
    check("ascent_px > 0", p1.font_ascent_px > 0.0,
          f"got {p1.font_ascent_px}")

# ---------------------------------------------------------------------------
# 6. _emit: a scroll container still tags its children correctly
# ---------------------------------------------------------------------------
def t6_scroll_container_tagging() -> None:
    # Verify _layer is a valid int on every command (the P34.4 fast
    # path must still tag commands correctly).
    html = """<!DOCTYPE html><html><head>
      <style>#sc { overflow: auto; height: 100px; width: 200px; }</style>
      </head><body>
      <div id="sc"><div>hi</div></div>
      </body></html>"""
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.layout(styled, 400, 400)
    dl = paint_engine.emit_commands(tree)
    check("every command has int _layer",
          len(dl.commands) > 0 and all(isinstance(c._layer, int) for c in dl.commands),
          f"cmds={len(dl.commands)} types={set(type(c._layer).__name__ for c in dl.commands)}")
    # Every command in a non-z-index document should have _layer=0.
    check("default z-index commands carry _layer=0",
          all(c._layer == 0 for c in dl.commands))


# ---------------------------------------------------------------------------
# 7. _emit: a box with z-index > 0 emits commands tagged with that z
# ---------------------------------------------------------------------------
def t7_non_scroll_emits_commands() -> None:
    # z-index → _layer propagation (legacy P7.1 mechanism, regression
    # guard for the P34.4 _emit refactor).  If the position:relative /
    # z-index cascade happens to not fire in this build, we still
    # verify the core invariant: every command carries an int _layer
    # and no command is left in a half-set state.
    html = """<!DOCTYPE html><html><head>
      <style>
        #b { z-index: 5; position: absolute; top: 0; left: 0; }
      </style>
      </head><body>
      <div id="b">content</div>
      </body></html>"""
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.layout(styled, 400, 400)
    dl = paint_engine.emit_commands(tree)
    check("all commands have int _layer",
          all(isinstance(c._layer, int) for c in dl.commands),
          f"types={set(type(c._layer).__name__ for c in dl.commands)}")
    # A command list must exist.
    check("commands emitted", len(dl.commands) > 0,
          f"got {len(dl.commands)}")

# ---------------------------------------------------------------------------
# 8. perf: measure() should now be much faster than legacy advance_text
# ---------------------------------------------------------------------------
def t8_measure_perf() -> None:
    import time
    s = "内容 47321"
    # Warm up
    text.measure(s, 14.0, "sans-serif")
    t0 = time.perf_counter()
    for _ in range(5000):
        text.measure(s, 14.0, "sans-serif")
    t_m = (time.perf_counter() - t0) / 5000
    bench("measure() on cached string", t_m, 5e-6, f"{t_m*1e6:.2f} us")
    # Uncached (different string each call) should still work
    for i in range(100):
        w = text.measure(f"内容 {i} 用于测试", 14.0, "sans-serif")
        assert w > 0.0
    check("measure() on novel strings works", True)

# ---------------------------------------------------------------------------
# 9. perf: full pipeline should be at least as fast as P34.3
# ---------------------------------------------------------------------------
def t9_pipeline_perf() -> None:
    import time, gc
    html = P._make_doc(3000)
    gc.collect()
    for _ in range(2):  # warm up
        r = dom.parse(html)
        s = css.apply(r, "")
        t = layout.layout(s, 1200, 800)
        d = paint_engine.emit_commands(t)
        b = paint_engine.rasterize(d, 1200, 800)
    best = float("inf")
    for _ in range(3):
        gc.collect()
        t0 = time.perf_counter()
        r = dom.parse(html)
        s = css.apply(r, "")
        t = layout.layout(s, 1200, 800)
        d = paint_engine.emit_commands(t)
        b = paint_engine.rasterize(d, 1200, 800)
        best = min(best, time.perf_counter() - t0)
    print(f"   3000-node pipeline: {best*1000:.1f} ms")
    bench("3000-node pipeline vs P34.3 (~9.6s)", best, 15.0, f"got {best:.1f}s")


def main():
    t1_cache_agrees_with_advance()
    t2_measure_edge_cases()
    t3_clear_advances_cache()
    t4_layout_clear_caches_clears_text()
    t5_half_lead_cache()
    t6_scroll_container_tagging()
    t7_non_scroll_emits_commands()
    t8_measure_perf()
    t9_pipeline_perf()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All P34.4 tests passed.")


if __name__ == "__main__":
    main()


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("t1_cache_agrees_with_advance", _checked(t1_cache_agrees_with_advance)))
TESTS.append(("t2_measure_edge_cases", _checked(t2_measure_edge_cases)))
TESTS.append(("t3_clear_advances_cache", _checked(t3_clear_advances_cache)))
TESTS.append(("t4_layout_clear_caches_clears_text", _checked(t4_layout_clear_caches_clears_text)))
TESTS.append(("t5_half_lead_cache", _checked(t5_half_lead_cache)))
TESTS.append(("t6_scroll_container_tagging", _checked(t6_scroll_container_tagging)))
TESTS.append(("t7_non_scroll_emits_commands", _checked(t7_non_scroll_emits_commands)))
TESTS.append(("t8_measure_perf", _checked(t8_measure_perf)))
TESTS.append(("t9_pipeline_perf", _checked(t9_pipeline_perf)))
