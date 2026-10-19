"""B03 — script-boundary isolation regression tests.

Round-5 assessment (百度真实网页访问评估与改进步骤 §B03) identified the
exact break in the "open a real site" task: a page whose Nth inline
script hits a parse-time / runtime SyntaxError (baidu's "pos 210"
shape) crashed the WHOLE page, and the GUI misreported that script
fault as a *Connection error*.  Root cause: ``Host.execute`` re-raises
interpreter ``SyntaxError`` / ``ReferenceError`` … as a bare
``_Throw`` (not a ``JSUncaughtError``), and the legacy
``execute_scripts_in_order`` loop only caught ``JSUncaughtError`` — so
the bare ``_Throw`` escaped the script boundary and took the page down.

This batch fixes the boundary: the loop now catches ``_Throw`` and
``RecursionError`` too, and records every isolated fault on
``Host.script_errors`` as a structured entry ``{source, kind, error}``.
That lets the GUI / CLI surface "page rendered, N script(s) failed"
without conflating a script failure with a network / connection error.

Tests (no test content is shared with the existing modules — this is a
new B03 module):
  t1  a parse-error script no longer escapes the boundary
  t2  the script AFTER a failing one still runs (isolation)
  t3  each failing inline script records one structured entry
  t4  a clean page records zero script errors
  t5  an external script's failure is recorded with its URL
  t6  ``uncaught throw`` (JSUncaughtError path) keeps its legacy log
      and is recorded exactly once into script_errors (no double-record)
  t7  a runaway-recursion script is logged and the rest runs
"""

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

from toy_browser.document import dom
from toy_browser.scripting.js import Host

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        _failures.append(name)


def _run(scripts, external_url=None):
    """Build a page with the given inline scripts and run it in document
    order; return ``(host, logs)``.  If *external_url* is set, a single
    external <script src> with that URL is prepended (its fetched body
    is passed as the failing source)."""
    parts = [f'<script>{s}</script>' for s in scripts]
    if external_url is not None:
        parts.insert(0, f'<script src="{external_url}"></script>')
    html = ('<!DOCTYPE html><html><head><title>t</title></head>'
            f'<body>{"".join(parts)}</body></html>')
    root = dom.parse(html)
    logs = []

    def _combined_log(msg):
        """Forward both to the user log sink and to ``host.logs`` so the
        legacy console stream (``logs``) and the structured diagnostics
        panel (``host.logs``) see the same uncaught-throw line."""
        logs.append(msg)
        host.logs.append(msg)

    host = Host(root, log=_combined_log)
    return host, logs


# --- 1. parse-error script no longer escapes the boundary ---
def t1_parse_error_no_escape():
    # "var x = {;" is a genuine parse-time SyntaxError (unexpected ';').
    # Before the B03 fix, execute_scripts_in_order raised a bare
    # _Throw that escaped the loop; now it is contained.
    html = ('<!DOCTYPE html><html><head><title>t</title></head>'
            '<body><script>var x = {;</script></body></html>')
    root = dom.parse(html)
    logs = []
    host = Host(root, log=logs.append)
    escaped = False
    try:
        host.execute_scripts_in_order({})
    except Exception as e:  # noqa: BLENT0
        escaped = True
        print(f"  escaped: {type(e).__name__}: {e}")
    check("parse-error script contained (no escape)", not escaped)


# --- 2. the script AFTER a failing one still runs ---
def t2_later_scripts_still_run():
    scripts = [
        'console.log("BEFORE");',
        'var x = {;',            # parse error — fails
        'console.log("AFTER");',
    ]
    host, logs = _run(scripts)
    host.execute_scripts_in_order({})
    check("script after a failing one runs", any("AFTER" in l for l in logs),
          f"logs={logs}")


# --- 3. each failing inline script records one structured entry ---
def t3_structured_entry_recorded():
    host, _ = _run(['console.log("b");', 'var x = {;', 'console.log("a");'])
    host.execute_scripts_in_order({})
    check("one script error recorded for one failing script",
          len(host.script_errors) == 1,
          f"got {len(host.script_errors)}")
    if host.script_errors:
        e = host.script_errors[0]
        check("entry has source/kind/error keys",
              set(e) >= {"source", "kind", "error"},
              f"keys={list(e)}")
        check("entry kind is inline", e.get("kind") == "inline",
              f"kind={e.get('kind')}")
        check("entry error names the fault", "SyntaxError" in e.get("error", "")
              or "property key" in e.get("error", ""),
              f"error={e.get('error')}")


# --- 4. a clean page records zero script errors ---
def t4_clean_page_zero_errors():
    host, _ = _run(['console.log("ok");', 'var y = 2;'])
    host.execute_scripts_in_order({})
    check("clean page records zero script errors",
          len(host.script_errors) == 0,
          f"got {len(host.script_errors)}")


# --- 5. an external script's failure is recorded with its URL ---
def t5_external_script_error_recorded():
    # The external body (keyed by absolute URL) is a parse error; the
    # record must carry the URL as its source.
    url = "https://example.com/bad.js"
    body = "var x = {;"
    host, _ = _run(['console.log("after-ext");'], external_url=url)
    host.execute_scripts_in_order({url: body})
    check("external parse failure does not escape",
          host.script_errors is not None)
    check("external failure recorded with its URL",
          any(e.get("source") == url for e in host.script_errors),
          f"errors={host.script_errors}")
    check("external entry kind is external",
          any(e.get("kind") == "external" for e in host.script_errors))


# --- 6. uncaught-throw keeps its legacy log, recorded once ---
def t6_uncaught_throw_recorded_once():
    # "throw new Error()" / "undefinedFn()" surface as JSUncaughtError
    # (execute already wraps them).  The uncaught throw is recorded
    # structurally exactly once so the diagnostics panel sees it
    # (R7-J03), while the legacy "uncaught throw" console line is
    # emitted exactly once too — the contract is "recorded once",
    # not "absent".
    host, logs = _run(['undefinedFn();'])
    host.execute_scripts_in_order({})
    check("uncaught throw logged (legacy path)",
          any("uncaught throw" in l.lower() or "ReferenceError" in l
              for l in logs),
          f"logs={logs}")
    check("uncaught throw recorded exactly once",
          len(host.script_errors) == 1,
          f"errors={host.script_errors}")
    if host.script_errors:
        e = host.script_errors[0]
        check("uncaught throw entry kind is inline",
              e.get("kind") == "inline", f"entry={e}")
        check("uncaught throw entry names the fault",
              "ReferenceError" in e.get("error", "") or "undefined" in e.get("error", ""),
              f"entry={e}")


# --- 7. runaway recursion is contained and the rest runs ---
def t7_recursion_contained():
    # A function that recurses past the interpreter's depth guard raises
    # RecursionError; the boundary logs it and later scripts still run.
    src = ('function loop(n){ return loop(n+1); } loop(0);')
    host, logs = _run([src, 'console.log("AFTER-REC");'])
    host.execute_scripts_in_order({})
    check("runaway recursion contained (no escape)",
          any("AFTER-REC" in l for l in logs),
          f"logs={logs}")


def main():
    t1_parse_error_no_escape()
    t2_later_scripts_still_run()
    t3_structured_entry_recorded()
    t4_clean_page_zero_errors()
    t5_external_script_error_recorded()
    t6_uncaught_throw_recorded_once()
    t7_recursion_contained()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All P34.7 (B03 script-boundary) tests passed.")


if __name__ == "__main__":
    main()


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("t1_parse_error_no_escape", t1_parse_error_no_escape))
TESTS.append(("t2_later_scripts_still_run", t2_later_scripts_still_run))
TESTS.append(("t3_structured_entry_recorded", t3_structured_entry_recorded))
TESTS.append(("t4_clean_page_zero_errors", t4_clean_page_zero_errors))
TESTS.append(("t5_external_script_error_recorded", t5_external_script_error_recorded))
TESTS.append(("t6_uncaught_throw_recorded_once", t6_uncaught_throw_recorded_once))
TESTS.append(("t7_recursion_contained", t7_recursion_contained))
