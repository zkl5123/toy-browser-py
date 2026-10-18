"""End-to-end regression runner for toy-browser-py (pure stdlib, no test framework).

Architecture (refactored):
  * **Auto-discovery** — every ``tests/test_*.py`` module that exposes a
    module-level ``TESTS`` list is collected by glob + source scan; no
    hard-coded import list.  Modules are imported *lazily*, only when
    enqueued, so the fast tier never pays the expensive modules'
    import/global-init cost.  New test modules are picked up automatically.
  * **Tiered runs** — the default is the *fast tier*: it skips the 10
    expensive modules (test_browser + 4 wgpu GPU modules + the 4
    full-pipeline benchmark modules test_p2 / test_p34_r4 / r5 / r6 +
    1 real-multiprocessing spawn module, ~350 s of serial wall-clock)
    and runs the remaining ~78 light modules in ~50 s, covering the bulk
    of the logic regression.  The full suite (all 1889 tests, ~7 min) is
    available with ``--full``.
  * **Quiet by default** — the runner prints only failures + a final
    summary and, with ``--report``, a per-module timing table.
    ``--verbose`` restores the per-test "ok <name>" stream.

Run:
    python test.py            # fast tier (default; 78 modules, ~50 s)
    python test.py --full     # full suite (all 1889 tests, ~7 min)
    python test.py --only test_css test_js   # run a subset of modules
    python test.py --verbose  # print every passing test
    python test.py --report   # also print the per-module timing table

Every test function and its assertions are unchanged — only the
*orchestration* (discovery, tiering, output) is reworked.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools" / "benchmarks"))
sys.path.insert(0, str(_ROOT / "tests"))
os.chdir(_ROOT)

# ---------------------------------------------------------------------------
# Expensive modules — the ones that dominate the wall clock.  Measured
# (serial, --full --report), these render the *entire* HTML→paint→rasterize
# pipeline many times over and/or drive wgpu / real multiprocessing:
#   test_p34_r6                  ~125 s (10 000-node full pipeline benchmark)
#   test_browser                  ~50 s (357 full re-renders of the pipeline)
#   test_p2                       ~48 s (full-render benchmark)
#   test_p34_r5                   ~42 s (3000-node pipeline benchmark)
#   test_p34_r4                   ~39 s (3000-node pipeline benchmark)
#   test_gpu_glyph_atlas          ~27 s (wgpu glyph-atlas rasterisation)
#   test_gpu_rasterize            ~12 s (wgpu texture upload / read-back)
#   test_gpu_backend               ~7 s
#   test_gpu_image_cache           ~4 s
#   test_document_process_spawn    ~4 s (real multiprocessing spawn)
# Together ~350 s when serial.  The *default* fast tier skips these so a
# day-to-day logic regression finishes in ~50 s; ``--full`` includes them.
# ---------------------------------------------------------------------------
_EXPENSIVE = {
    "test_p34_r6",
    "test_browser",
    "test_p2",
    "test_p34_r5",
    "test_p34_r4",
    "test_gpu_glyph_atlas",
    "test_gpu_rasterize",
    "test_gpu_backend",
    "test_gpu_image_cache",
    "test_document_process_spawn",
}

# Default is the fast tier; --full opts into the complete suite.
_FULL = "--full" in sys.argv
# --only <module> [module …] filters the runner to a subset of modules.
_ONLY: set[str] | None = None
if "--only" in sys.argv:
    _i = sys.argv.index("--only")
    _ONLY = set()
    for _arg in sys.argv[_i + 1:]:
        if _arg.startswith("-"):
            break
        _ONLY.add(_arg)

_REPORT = "--report" in sys.argv
_VERBOSE = "--verbose" in sys.argv


def _p_has_tests(path: Path) -> bool:
    """Cheap source scan: does a ``test_*.py`` file declare a ``TESTS`` list?"""
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^\s*TESTS\s*[:=]", src, re.MULTILINE) is not None


def _discover_paths() -> list[Path]:
    """Every ``tests/test_*.py`` that declares a module-level ``TESTS``.

    Pure source scan — no import, so the fast tier never pays the
    expensive modules' import/global-init cost unless they are enqueued.
    """
    paths: list[Path] = []
    for path in sorted(Path(_ROOT / "tests").glob("test_*.py")):
        if _p_has_tests(path):
            paths.append(path)
    return paths


def _import_group(path: Path) -> tuple[str, list]:
    """Import one test module and return ``(module_name, TESTS)``."""
    mod = importlib.import_module(path.stem)
    return path.stem, list(getattr(mod, "TESTS", []))


def main() -> int:
    # ------------------------------------------------------------------
    # 1. Discover module names (source scan only, no import yet) and
    #    tier them so the fast tier never imports the expensive ones.
    # ------------------------------------------------------------------
    paths = _discover_paths()
    names = [p.stem for p in paths]

    if _ONLY is not None:
        run_names = [n for n in names if n in _ONLY]
        skipped: list[str] = []
        unknown = sorted(_ONLY - set(names))
        if unknown:
            print(f"  WARN: --only module(s) not found: {unknown}")
    elif _FULL:
        run_names = list(names)
        skipped = []
    else:
        # fast tier: run everything except the expensive modules.
        run_names = [n for n in names if n not in _EXPENSIVE]
        skipped = [n for n in names if n in _EXPENSIVE]

    # ------------------------------------------------------------------
    # 2. Import only the modules we will run (lazy).
    # ------------------------------------------------------------------
    path_by_stem = {p.stem: p for p in paths}
    groups: list[tuple[str, list]] = []
    for name in run_names:
        try:
            groups.append(_import_group(path_by_stem[name]))
        except Exception as e:  # noqa: BLENT0
            print(f"  WARN: failed to import {name}: {e}")

    total_enqueued = sum(len(t) for _, t in groups)
    mode_label = "full" if _FULL else "fast"
    if _ONLY is not None:
        mode_label = f"only({','.join(sorted(_ONLY))})"
    if not _FULL and _ONLY is None:
        print(
            f"[mode={mode_label}] "
            f"{len(groups)} modules, {total_enqueued} tests, "
            f"{len(skipped)} expensive module(s) skipped"
        )
    else:
        print(f"[mode={mode_label}] {len(groups)} modules, {total_enqueued} tests")

    passed = 0
    failed: list[tuple[str, str]] = []
    module_times: list[tuple[float, str, int, int]] = []
    t_wall = time.time()

    # ------------------------------------------------------------------
    # 3. Serial execution of every enqueued module.
    # ------------------------------------------------------------------
    for mod_name, tests in groups:
        t0 = time.time()
        mod_passed = 0
        for name, fn in tests:
            try:
                fn()
                mod_passed += 1
                passed += 1
                if _VERBOSE:
                    print(f"  ok  [{mod_name}] {name}")
            except Exception as e:  # noqa: BLENT0
                failed.append((f"{mod_name}::{name}", repr(e)))
                if _VERBOSE:
                    import traceback
                    print(f"  FAIL [{mod_name}] {name}: {e}")
                    traceback.print_exc()
        module_times.append((time.time() - t0, mod_name, mod_passed,
                             len(tests)))

    # ------------------------------------------------------------------
    # 4. Summary.
    # ------------------------------------------------------------------
    wall = time.time() - t_wall
    print(f"\n{'=' * 60}")
    print(
        f"RESULT: {passed}/{total_enqueued} passed in {wall:.1f}s "
        f"({len(groups)} modules, mode={mode_label})"
    )
    if skipped:
        print("skipped (expensive, fast tier): " + ", ".join(skipped))
    if _REPORT:
        print(f"\n{'=' * 60}\nPER-MODULE TIMING (slowest first):")
        for d, m, p, t in sorted(module_times, reverse=True):
            bar = "#" * int(d * 2)
            print(f"  {d:6.1f}s  {m:32s} {p:4d}/{t:<4d} {bar}")
    if failed:
        print(f"FAILED ({len(failed)}):")
        for n, e in failed:
            print(f"  - {n}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
