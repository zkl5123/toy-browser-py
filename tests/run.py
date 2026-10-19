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

Result contract (R7-Q01, round-7 assessment):
  Every enqueued test lands in exactly one of five states — PASS, FAIL,
  ERROR, SKIP, TIMEOUT — and all five are counted in the summary
  denominator:

  * PASS    — the test function returned without raising.
  * FAIL    — the test function raised (a genuine assertion failure).
  * ERROR   — infrastructure failure (module import error, Tcl
              subprocess crash / missing summary / timeout bookkeeping).
  * SKIP    — the test raised ``skip_support.SkipTest(reason)``; the
              environment cannot support it.  A SKIP is *never* a PASS.
  * TIMEOUT — the Tcl-isolated subprocess exceeded its wall budget.

  Import failures are ERROR results (and a non-zero exit), never a
  silent shrink of the denominator.  An unknown or empty ``--only``
  selection is a hard error.  ``--json PATH`` writes a machine-readable
  report (per-test id / status / duration / detail + environment).

Run:
    python test.py            # fast tier (default; 78 modules, ~50 s)
    python test.py --full     # full suite (all 1889 tests, ~7 min)
    python test.py --only test_css test_js   # run a subset of modules
    python test.py --verbose  # print every passing test
    python test.py --report   # also print the per-module timing table
    python test.py --json report.json        # machine-readable report
    python test.py --junit report.xml         # JUnit XML report
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools" / "benchmarks"))
sys.path.insert(0, str(_ROOT / "tests"))
os.chdir(_ROOT)

from skip_support import SkipTest  # noqa: E402

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
#   test_gpu_backend              ~7 s
#   test_gpu_image_cache          ~4 s
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
_JSON: str | None = None
if "--json" in sys.argv:
    _j = sys.argv.index("--json")
    if _j + 1 < len(sys.argv) and not sys.argv[_j + 1].startswith("-"):
        _JSON = sys.argv[_j + 1]
_JUNIT: str | None = None
if "--junit" in sys.argv:
    _u = sys.argv.index("--junit")
    if _u + 1 < len(sys.argv) and not sys.argv[_u + 1].startswith("-"):
        _JUNIT = sys.argv[_u + 1]

# Wall budget for a Tcl-isolated subprocess (R7-Q01: the legacy runner
# had no timeout at all and swallowed the child's stderr).
_TCL_TIMEOUT_S = 600.0

# Result states (R7-Q01).  PASS/FAIL are per-test outcomes; ERROR is an
# infrastructure failure; SKIP is an environment-gated non-run; TIMEOUT
# is a subprocess wall-budget overrun.
_PASS, _FAIL, _ERROR, _SKIP, _TIMEOUT = "PASS", "FAIL", "ERROR", "SKIP", "TIMEOUT"


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


def _run_tcl_isolated(mod_names: list[str]) -> dict:
    """Run Tcl-sensitive modules in a clean subprocess.

    Returns a result dict::

        {"statuses": [(test_id, status, detail), …],
         "stderr_path": str | None, "timed_out": bool}

    The subprocess boots a *fresh* Tcl interpreter, imports only the
    requested modules, runs their TESTS, and reports machine-parseable
    JSON lines on stdout (one per test: PASS/FAIL/SKIP + reason) plus a
    final summary line ``{"P": n, "F": n, "S": n, "enq": n}``.  R7-Q01:
    the child has a wall-clock timeout, its stderr is preserved to a
    file (never silently swallowed), and the summary must be present and
    internally consistent (P + F + S == enq) or the whole group is an
    ERROR — the parent never credits phantom passes.
    """
    py = sys.executable
    boot = (
        "import sys, importlib, json\n"
        f"for p in {json.dumps([str(_ROOT), str(_ROOT / 'tools' / 'benchmarks'), str(_ROOT / 'tests')])}:\n"
        "    sys.path.insert(0, p)\n"
        "from skip_support import SkipTest\n"
        f"mods = {json.dumps(mod_names)}\n"
        "results = []\n"
        "enq = 0\n"
        "for m in mods:\n"
        "    mod = importlib.import_module(m)\n"
        "    for name, fn in getattr(mod, 'TESTS', []):\n"
        "        enq += 1\n"
        "        try:\n"
        "            fn()\n"
        "            results.append({'m': m, 'n': name, 'st': 'P'})\n"
        "        except SkipTest as e:\n"
        "            results.append({'m': m, 'n': name, 'st': 'S', 'd': str(e)})\n"
        "        except Exception as e:\n"
        "            results.append({'m': m, 'n': name, 'st': 'F', 'd': repr(e)})\n"
        "for r in results:\n"
        "    print(json.dumps(r))\n"
        "print(json.dumps({'summary': True, 'P': sum(1 for r in results if r['st'] == 'P'), "
        "'F': sum(1 for r in results if r['st'] == 'F'), "
        "'S': sum(1 for r in results if r['st'] == 'S'), 'enq': enq}))\n"
    )
    stderr_path = os.path.join(
        tempfile.gettempdir(),
        f"toy_browser_tcl_isolated_{int(time.time())}.stderr.log")
    timed_out = False
    proc = None
    try:
        proc = subprocess.run(
            [py, "-c", boot],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TCL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        with open(stderr_path, "w", encoding="utf-8") as fh:
            fh.write("TCL-ISOLATED SUBPROCESS TIMEOUT "
                     f"({_TCL_TIMEOUT_S}s)\n--- stdout ---\n{out}\n"
                     f"--- stderr ---\n{err}\n")
        return {
            "statuses": [(f"tcl_isolated::{m}", _TIMEOUT,
                          f"subprocess exceeded {_TCL_TIMEOUT_S}s wall budget")
                         for m in mod_names],
            "stderr_path": stderr_path,
            "timed_out": True,
        }

    # Preserve the child's stderr for diagnostics (never swallow it).
    with open(stderr_path, "w", encoding="utf-8") as fh:
        fh.write(proc.stderr or "")
    stderr_path = stderr_path if (proc.stderr or "").strip() else None

    statuses: list[tuple[str, str, str]] = []
    summary = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("summary"):
            summary = rec
        elif "st" in rec:
            statuses.append((f"{rec['m']}::{rec['n']}", rec["st"], rec.get("d", "")))

    group_error = None
    if proc.returncode != 0:
        group_error = (f"subprocess exit {proc.returncode}"
                       + (f"; stderr: {(proc.stderr or '').strip()[:200]}" if proc.stderr else ""))
    elif summary is None:
        group_error = "subprocess produced no valid summary line"
    elif summary["P"] + summary["F"] + summary["S"] != summary["enq"]:
        group_error = (f"inconsistent summary P+F+S={summary['P'] + summary['F'] + summary['S']} "
                       f"!= enq={summary['enq']}")
    elif len(statuses) != summary["enq"]:
        group_error = (f"summary enq={summary['enq']} but {len(statuses)} "
                       "per-test records reported")

    if group_error is not None:
        # Do not credit whatever partial records we saw: the group is an
        # infrastructure ERROR (one entry per module, no phantom passes).
        return {
            "statuses": [(f"tcl_isolated::{m}", _ERROR, group_error) for m in mod_names],
            "stderr_path": stderr_path,
            "timed_out": False,
        }
    return {"statuses": statuses, "stderr_path": stderr_path, "timed_out": False}


def main() -> int:
    # ------------------------------------------------------------------
    # 1. Discover module names (source scan only, no import yet) and
    #    tier them so the fast tier never imports the expensive ones.
    # ------------------------------------------------------------------
    paths = _discover_paths()
    names = [p.stem for p in paths]

    if _ONLY is not None:
        unknown = sorted(_ONLY - set(names))
        if unknown:
            print(f"ERROR: --only module(s) not found: {unknown}")
            print(f"       discovered modules: {len(names)}")
            return 2
        run_names = [n for n in names if n in _ONLY]
        skipped: list[str] = []
        if not run_names:
            print("ERROR: --only selected no modules (empty selection set)")
            return 2
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
    results: list[tuple[str, str, str, float]] = []  # (id, status, detail, ms)
    import_errors: list[str] = []
    for name in inline_names:
        try:
            groups.append(_import_group(path_by_stem[name]))
        except Exception as e:  # noqa: BLENT0
            # R7-Q01: an import failure is an ERROR result — never a
            # silent shrink of the denominator followed by a "success".
            import_errors.append(name)
            results.append((f"module_import::{name}", _ERROR, repr(e), 0.0))
            print(f"  ERROR: failed to import {name}: {e}")

    inline_enqueued = sum(len(t) for _, t in groups)
    mode_label = "full" if _FULL else "fast"
    if _ONLY is not None:
        mode_label = f"only({','.join(sorted(_ONLY))})"
    extra = f", {len(tcl_names)} tcl-isolated module(s)" if tcl_names else ""
    if import_errors:
        extra += f", {len(import_errors)} module(s) failed to import"
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

    module_times: list[tuple[float, str, int, int]] = []
    t_wall = time.time()

    # ------------------------------------------------------------------
    # 3. Serial execution of every in-process enqueued module.
    # ------------------------------------------------------------------
    for mod_name, tests in groups:
        t0 = time.time()
        mod_passed = 0
        for name, fn in tests:
            tt = time.time()
            try:
                fn()
                mod_passed += 1
                results.append((f"{mod_name}::{name}", _PASS, "", time.time() - tt))
                if _VERBOSE:
                    print(f"  ok  [{mod_name}] {name}")
            except SkipTest as e:
                # R7-Q01: environment-gated non-run — an explicit SKIP,
                # never a PASS.
                results.append((f"{mod_name}::{name}", _SKIP,
                                str(e) or "skipped", time.time() - tt))
                if _VERBOSE:
                    print(f"  skip[{mod_name}] {name}: {e}")
            except Exception as e:  # noqa: BLENT0
                results.append((f"{mod_name}::{name}", _FAIL, repr(e),
                                time.time() - tt))
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
        t0 = time.time()
        iso = _run_tcl_isolated(tcl_names)
        results.extend((tid, st, detail, 0.0)
                       for tid, st, detail in iso["statuses"])
        iso_pass = sum(1 for _, st, _ in iso["statuses"] if st == _PASS)
        iso_enq = len(iso["statuses"])
        module_times.append((time.time() - t0, "(tcl-isolated)",
                             iso_pass, iso_enq))
        if iso["stderr_path"]:
            print(f"  tcl-isolated child stderr saved to: {iso['stderr_path']}")
        for tid, st, detail in iso["statuses"]:
            if st != _PASS:
                print(f"  {st} [{tid}]: {detail}")

    # ------------------------------------------------------------------
    # 4. Summary — every state is counted; nothing is silently dropped.
    # ------------------------------------------------------------------
    wall = time.time() - t_wall
    counts = {_PASS: 0, _FAIL: 0, _ERROR: 0, _SKIP: 0, _TIMEOUT: 0}
    for _tid, st, _d, _ms in results:
        counts[st] = counts.get(st, 0) + 1
    total = len(results)
    print(f"\n{'=' * 60}")
    print(
        f"RESULT: {counts[_PASS]} passed, {counts[_FAIL]} failed, "
        f"{counts[_ERROR]} errors, {counts[_SKIP]} skipped, "
        f"{counts[_TIMEOUT]} timeouts (total {total}) in {wall:.1f}s "
        f"({len(groups)} modules, mode={mode_label})"
    )
    if skipped:
        print("skipped (expensive, fast tier): " + ", ".join(skipped))
    if _REPORT:
        print(f"\n{'=' * 60}\nPER-MODULE TIMING (slowest first):")
        for d, m, p, t in sorted(module_times, reverse=True):
            bar = "#" * int(d * 2)
            print(f"  {d:6.1f}s  {m:32s} {p:4d}/{t:<4d} {bar}")

    problems = [r for r in results if r[1] != _PASS and r[1] != _SKIP]
    if problems:
        print(f"FAILED ({len(problems)}):")
        for tid, st, detail, _ms in problems:
            print(f"  - {st} [{tid}]: {detail[:300]}")
    elif counts[_SKIP]:
        print(f"NOTE: {counts[_SKIP]} test(s) SKIPped (environment); "
              "skips are not passes.")

    if _JSON:
        report = {
            "mode": mode_label,
            "environment": {
                "python": sys.version,
                "platform": platform.platform(),
                "executable": sys.executable,
                "tkinter": _env_has_tk(),
            },
            "counts": counts,
            "total": total,
            "duration_s": round(wall, 3),
            "tests": [
                {"id": tid, "status": st, "detail": detail,
                 "duration_ms": round(ms * 1000, 3)}
                for tid, st, detail, ms in results
            ],
        }
        try:
            Path(_JSON).write_text(json.dumps(report, indent=2,
                                              ensure_ascii=False),
                                   encoding="utf-8")
            print(f"machine-readable report written to {_JSON}")
        except OSError as e:
            print(f"WARN: could not write JSON report: {e}")

    if _JUNIT:
        _write_junit(_JUNIT, mode_label, results, counts, wall)

    # SKIP alone does not fail the run (the environment simply lacks a
    # capability); FAIL / ERROR / TIMEOUT do.
    return 1 if problems else 0


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _write_junit(path: str, mode: str,
                 results: list[tuple[str, str, str, float]],
                 counts: dict, wall: float) -> None:
    """R7-Q01 — JUnit XML report (CI-friendly machine-readable output).

    Status mapping: PASS→passed; FAIL/ERROR/TIMEOUT→failure (ERROR and
    TIMEOUT additionally carry a distinguishing ``type``); SKIP→skipped
    with the reason as the message.
    """
    suite_name = f"toy-browser-py-{mode}"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="{_xml_escape(suite_name)}" '
        f'tests="{len(results)}" failures="{counts[_FAIL]}" '
        f'errors="{counts[_ERROR] + counts[_TIMEOUT]}" '
        f'skipped="{counts[_SKIP]}" time="{wall:.3f}">',
    ]
    for tid, st, detail, ms in results:
        name = tid.split("::", 1)[-1] if "::" in tid else tid
        classname = tid.split("::", 1)[0] if "::" in tid else tid
        t = f"{ms:.3f}"
        if st == _PASS:
            lines.append(f'  <testcase name="{_xml_escape(name)}" '
                         f'classname="{_xml_escape(classname)}" time="{t}"/>')
        elif st == _SKIP:
            lines.append(
                f'  <testcase name="{_xml_escape(name)}" '
                f'classname="{_xml_escape(classname)}" time="{t}">'
                f'<skipped message="{_xml_escape(detail[:300])}"/>'
                f'</testcase>')
        else:
            kind = "error" if st in (_ERROR, _TIMEOUT) else "failure"
            lines.append(
                f'  <testcase name="{_xml_escape(name)}" '
                f'classname="{_xml_escape(classname)}" time="{t}">'
                f'<{kind} message="{_xml_escape(detail[:300])}" '
                f'type="{st}">{_xml_escape(detail)}</{kind}>'
                f'</testcase>')
    lines.append("</testsuite>")
    try:
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"JUnit XML report written to {path}")
    except OSError as e:
        print(f"WARN: could not write JUnit report: {e}")


def _env_has_tk() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
