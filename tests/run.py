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
import json
import os
import re
import subprocess
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


# Tcl-sensitive modules that drive Tk's ``event_generate``.  They must run
# in a *fresh* Tcl interpreter, isolated from the dozens of earlier
# Tk-rooting modules in the same process whose residual event bindings /
# focus state pollute the shared interpreter and silently break
# ``event_generate`` dispatch.  They are therefore executed in a clean
# subprocess (a brand-new Tcl interpreter) and their pass/fail counts are
# merged back into the total.  Test functions are NOT modified — only the
# isolation of *where* they run is changed.
_TCL_ISOLATED: set[str] = {"test_tk_keyboard_binding"}


def _run_tcl_isolated(mod_names: list[str]) -> tuple[int, int, list[tuple[str, str]]]:
    """Run Tcl-sensitive modules in a clean subprocess; return (passed, enqueued, failures).

    The subprocess boots a *fresh* Tcl interpreter, imports only the
    requested modules, runs their TESTS, and reports machine-parseable
    lines on stdout.  Any stderr from the child (including harmless Tcl
    warnings) is swallowed.  If the child crashes the module is marked
    failed so the parent never reports a phantom pass.
    """
    py = sys.executable
    boot = (
        "import sys, importlib, json\n"
        f"for p in {json.dumps([str(_ROOT), str(_ROOT / 'tools' / 'benchmarks'), str(_ROOT / 'tests')])}:\n"
        "    sys.path.insert(0, p)\n"
        f"mods = {json.dumps(mod_names)}\n"
        "results = []\n"
        "enq = 0\n"
        "for m in mods:\n"
        "    mod = importlib.import_module(m)\n"
        "    for name, fn in getattr(mod, 'TESTS', []):\n"
        "        enq += 1\n"
        "        try:\n"
        "            fn()\n"
        "            results.append('P')\n"
        "        except Exception as e:\n"
        "            results.append('F')\n"
        "            print(json.dumps({'m': m, 'n': name, 'e': repr(e)}))\n"
        "print(json.dumps({'P': results.count('P'), 'enq': enq}))\n"
    )
    proc = subprocess.run(
        [py, "-c", boot],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    passed = 0
    enqueued = 0
    failures: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "e" in rec:  # a single failure line
            failures.append((f"{rec['m']}::{rec['n']}", rec["e"]))
        elif "P" in rec:  # the summary line
            passed = int(rec["P"])
            enqueued = int(rec["enq"])
    if proc.returncode != 0:
        # Child crashed / non-zero exit: don't credit phantom passes.
        failures.append((
            "tcl_isolated",
            f"subprocess exit {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:200]}",
        ))
        return 0, 0, failures
    return passed, enqueued, failures


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
    # 2. Import only the modules we will run in-process (lazy).
    #    Tcl-sensitive modules are pulled out of the in-process flow and
    #    run in a clean subprocess instead.
    # ------------------------------------------------------------------
    path_by_stem = {p.stem: p for p in paths}
    inline_names = [n for n in run_names if n not in _TCL_ISOLATED]
    tcl_names = [n for n in run_names if n in _TCL_ISOLATED]

    groups: list[tuple[str, list]] = []
    for name in inline_names:
        try:
            groups.append(_import_group(path_by_stem[name]))
        except Exception as e:  # noqa: BLENT0
            print(f"  WARN: failed to import {name}: {e}")

    inline_enqueued = sum(len(t) for _, t in groups)
    total_enqueued = inline_enqueued
    mode_label = "full" if _FULL else "fast"
    if _ONLY is not None:
        mode_label = f"only({','.join(sorted(_ONLY))})"
    extra = f", {len(tcl_names)} tcl-isolated module(s)" if tcl_names else ""
    if not _FULL and _ONLY is None:
        print(
            f"[mode={mode_label}] "
            f"{len(groups)} modules, {inline_enqueued} in-process tests, "
            f"{len(skipped)} expensive module(s) skipped{extra}"
        )
    else:
        print(
            f"[mode={mode_label}] {len(groups)} in-process modules, "
            f"{inline_enqueued} in-process tests{extra}"
        )

    passed = 0
    failed: list[tuple[str, str]] = []
    module_times: list[tuple[float, str, int, int]] = []
    t_wall = time.time()

    # ------------------------------------------------------------------
    # 3. Serial execution of every in-process enqueued module.
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
    # 3b. Tcl-sensitive modules in a clean subprocess (fresh Tcl).
    # ------------------------------------------------------------------
    if tcl_names:
        iso_passed, iso_enqueued, iso_failures = _run_tcl_isolated(tcl_names)
        passed += iso_passed
        total_enqueued += iso_enqueued
        failed.extend(iso_failures)
        module_times.append((time.time() - t_wall, "(tcl-isolated)",
                             iso_passed, iso_enqueued))

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
