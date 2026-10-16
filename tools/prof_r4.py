"""R4 stage 7 — performance profiling: per-segment timing + peak memory.

Profiles the toy-browser full pipeline (parse → css → layout → paint →
PNG) on fixed workloads (100 / 1000 / 5000 / 10000 element div lists)
and reports per-segment wall-clock + peak RSS.  Also validates the
`changed_region`-driven partial-repaint path on a scroll workload.

Run:  python tools/prof_r4.py  (prints the report; also writes
assessments/20261016/perf_r4_profile.json)

The 10k-node "sentinel" contract (the P34 perf gate's machine-dependent
thresholds) is verified by the existing `test_p34*` tests, not by this
tool; this tool's job is the *segmented* profile + the C-extension
decision point (the plan's "fixed-load machine, expanded samples, then
decide" step).
"""
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools" / "benchmarks"))

import dom as dom_mod
import css as css_mod
import layout as layout_mod
import paint_engine
import browser

import os
os.chdir(_ROOT)


def _peak_rss_mb() -> float:
    """Peak RSS in MB for the current process.

    Windows: ``psapi.GetProcessMemoryInfo`` with the 64-bit
    ``PROCESS_MEMORY_COUNTERS_EX`` struct (a 32-bit struct fails on
    64-bit Python — the ``cb`` field mismatch makes the call silently
    return 0, discovered in the R4 stage 7 profiling round).  POSIX:
    ``resource.getrusage`` (KB on Linux, bytes on macOS)."""
    if sys.platform == "win32":
        try:
            import ctypes

            # The 32-bit PROCESS_MEMORY_COUNTERS and its 64-bit EX
            # variant share the first 8 fields; EX appends a single
            # CommitLimit (SIZE_T) at the end.  On a 64-bit process
            # the API requires the EX variant.
            _C_SIZE_T = ctypes.c_size_t

            class _PCM32(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaults", ctypes.c_ulonglong),
                    ("PeakWorkingSetSize", _C_SIZE_T),
                    ("WorkingSetSize", _C_SIZE_T),
                    ("QuotaPeakPrivateBytes", _C_SIZE_T),
                    ("QuotaUsedPrivateBytes", _C_SIZE_T),
                    ("PrivateBytes", _C_SIZE_T),
                    ("PeakPrivateBytes", _C_SIZE_T),
                ]

            class PROCESS_MEMORY_COUNTERS_EX(_PCM32):
                _fields_ = _PCM32._fields_ + [("CommitLimit", _C_SIZE_T)]

            k32 = ctypes.WinDLL("kernel32")
            psapi = ctypes.WinDLL("psapi")
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            hproc = k32.GetCurrentProcess()
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
                ctypes.c_ulong,
            ]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            pc = PROCESS_MEMORY_COUNTERS_EX()
            pc.cb = ctypes.sizeof(pc)
            ok = psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pc),
                                            ctypes.sizeof(pc))
            if not ok:
                return -1.0
            return pc.PeakWorkingSetSize / (1024.0 * 1024.0)
        except Exception:
            return -1.0
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        # macOS reports bytes; Linux reports KB.
        if sys.platform == "darwin":
            return ru.ru_maxrss / (1024.0 * 1024.0)
        return ru.ru_maxrss / 1024.0
    except Exception:
        return -1.0


def _build_html(n: int) -> str:
    rows = ''.join(f'<div style="width:100px;height:20px;margin:2px;background:#ccc">C{i}</div>'
                   for i in range(n))
    return f"<html><head><style>*{{box-sizing:border-box}}</style></head><body>{rows}</body></html>"


def profile_segments(n: int, w: int = 800, h: int = 600,
                    samples: int = 3) -> dict:
    """Run the full pipeline *samples* times on an *n*-element document
    and return the per-segment (parse / css / layout / paint / png)
    wall-clock ms (min across samples) + peak RSS MB.

    The PNG segment uses :func:`browser.write_png_bytes` (in-memory
    encode, no file I/O) so it measures the pure encode cost — the
    4th report's perf sentinel tracks render+png, but the *decision
    point* the plan cares about ("short-page interaction bitmap
    <500ms") is the bitmap-generation part, which is
    parse+css+layout+paint."""
    html = _build_html(n)
    seg = {k: [] for k in ("parse", "css", "layout", "paint", "png")}
    rss0 = _peak_rss_mb()
    for _ in range(samples):
        t = time.perf_counter()
        tree = dom_mod.parse(html)
        t_parse = time.perf_counter() - t

        t = time.perf_counter()
        author_css, ua_css = browser._full_css(tree, "")
        media_rules = css_mod.parse_media_queries(author_css)
        supports_rules = css_mod.parse_supports_blocks(author_css)
        styled = css_mod.apply(tree, author_css, ua_css=ua_css,
                               media_rules=media_rules,
                               supports_rules=supports_rules,
                               viewport_w=float(w), viewport_h=float(h))
        t_css = time.perf_counter() - t

        t = time.perf_counter()
        root = layout_mod.layout(styled, float(w), float(h))
        t_layout = time.perf_counter() - t

        t = time.perf_counter()
        dl = paint_engine.emit_commands(root)
        bmp = paint_engine.rasterize(dl, w, h)
        t_paint = time.perf_counter() - t

        t = time.perf_counter()
        browser.write_png_bytes(bmp)
        t_png = time.perf_counter() - t

        seg["parse"].append(t_parse)
        seg["css"].append(t_css)
        seg["layout"].append(t_layout)
        seg["paint"].append(t_paint)
        seg["png"].append(t_png)
    rss1 = _peak_rss_mb()
    out = {}
    for k in seg:
        out[k + "_ms"] = min(seg[k]) * 1000.0
    out["render_ms"] = (min(seg["parse"]) + min(seg["css"])
                        + min(seg["layout"]) + min(seg["paint"])) * 1000.0
    out["render_plus_png_ms"] = out["render_ms"] + out["png_ms"]
    out["peak_rss_mb"] = max(rss0, rss1)
    return out


def profile_changed_region_scroll() -> dict:
    """Validate the `changed_region`-driven partial-repaint path on a
    real pipeline document.

    The 100-element div list lays out to ~2200 px of content; the
    rasterizer paints only the top 600 px (the viewport).  Two
    frames are produced: the unshifted display list and the same
    display list with every command's y translated down by 300 px
    (the toy's scroll model — the full document is rasterized once and
    the viewport clip scrolls, so a "scroll" re-paints the moved
    content against the white background).  :func:`browser.changed_region`
    must return a localized band, not the full frame — the precondition
    for re-rasterizing only the changed region (the plan's "verify
    changed_region-driven partial repaint" step).
    """
    import copy

    n = 100
    html = ("<html><head><style>*{{box-sizing:border-box}}</style></head><body>"
            + "".join(f'<div style="width:100px;height:20px;margin:2px;background:#ccc">C{i}</div>'
                      for i in range(n))
            + "</body></html>")
    tree = dom_mod.parse(html)
    author_css, ua_css = browser._full_css(tree, "")
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(tree, author_css, ua_css=ua_css,
                           media_rules=media_rules,
                           supports_rules=supports_rules,
                           viewport_w=800.0, viewport_h=600.0)
    root = layout_mod.layout(styled, 800.0, 600.0)
    dl = paint_engine.emit_commands(root)
    b0 = paint_engine.rasterize(dl, 800, 600)

    # Scroll emulation: shift every command 300px down and re-rasterize.
    dl_scrolled = copy.deepcopy(dl)
    for c in dl_scrolled.commands:
        c.y += 300.0
    b1 = paint_engine.rasterize(dl_scrolled, 800, 600)

    region = browser.changed_region(b0, b1)
    diff_px = 0
    total_px = b0.width * b0.height
    if region:
        x, y, ww, hh = region
        for py in range(y, min(b0.height, y + hh)):
            base = py * b0.width
            for px in range(x, min(b0.width, x + ww)):
                if b0.pixels[base + px] != b1.pixels[base + px]:
                    diff_px += 1
    return {
        "diff_bbox": region,
        "diff_px": diff_px,
        "total_px": total_px,
        "diff_fraction": (diff_px / total_px) if total_px else 0.0,
        # "localized": the diff must be a strip (less than half the
        # frame), not the whole frame.
        "localized": (region is not None
                      and (diff_px / total_px) < 0.5),
    }


def main() -> int:
    report = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "stage": "R4 stage 7 — performance + memory profiling",
        "note": ("Segmented wall-clock (min of 3 samples) + peak RSS. "
                 "The plan's 1000-element <600ms (aggressive <500ms) "
                 "target is the render+png figure below; the C/raster "
                 "decision point is deferred to a fixed-load machine "
                 "with expanded samples (out of scope for stdlib)."),
        "workloads": {},
        "changed_region_scroll": None,
    }
    for n in (100, 1000, 5000, 10000):
        prof = profile_segments(n)
        report["workloads"][str(n)] = prof
        print(f"\n=== {n} elements ===")
        for k in ("parse", "css", "layout", "paint", "png"):
            print(f"  {k:6s} {prof[k + '_ms']:9.1f} ms")
        print(f"  {'render (parse+css+layout+paint)':35s} {prof['render_ms']:9.1f} ms")
        print(f"  {'render+png':35s} {prof['render_plus_png_ms']:9.1f} ms")
        print(f"  peak RSS {prof['peak_rss_mb']:.1f} MB")

    scroll = profile_changed_region_scroll()
    report["changed_region_scroll"] = scroll
    print("\n=== changed_region scroll probe (100-element doc, yshift 0 vs 300, 800x600 frame) ===")
    print(f"  diff bbox {scroll['diff_bbox']}  diff {scroll['diff_px']}px "
          f"of {scroll['total_px']}px ({100 * scroll['diff_fraction']:.1f}%)  "
          f"localized={scroll['localized']}")
    assert scroll["localized"], \
        "changed_region scroll diff must be a localized strip, not the full frame"

    out = _ROOT / "assessments" / "20261016" / "perf_r4_profile.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nreport written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
