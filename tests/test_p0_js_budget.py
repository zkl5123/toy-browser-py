"""P0 JS budget tests: infinite loop + recursion cap + cancellation."""

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

import js, dom

_failures = []
def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        _failures.append(name)

# 1. Host exists with the P0 knobs
def t1_host_knobs():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    h = js.Host(root, log=lambda m: None)
    check("_js_step_budget exists", hasattr(h, "_js_step_budget"))
    check("_js_max_recursion exists", hasattr(h, "_js_max_recursion"))
    check("_js_step_budget default 2M", h._js_step_budget == 2_000_000)
    check("_js_max_recursion default 256", h._js_max_recursion == 256)

# 2. _BudgetExceeded is raised on non-terminating recursion
def t2_recursion_cap():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    h = js.Host(root, log=lambda m: None)
    # A function that recurses until the depth cap.  With the legacy
    # engine this would either hit Python's RecursionError (far
    # outside the host's catch list) or hang forever.  P0 raises
    # _BudgetExceeded at the cap, which eval() propagates.
    code = "function g(n){ return g(n); } g(1)"
    try:
        h.eval(code)
        check("infinite recursion raises _BudgetExceeded", False,
              "eval returned normally (should have raised)")
    except js._BudgetExceeded:
        check("infinite recursion raises _BudgetExceeded", True)
    except RecursionError:
        check("infinite recursion raises _BudgetExceeded", False,
              "Python RecursionError leaked (P0 guard not in place)")
    except Exception as e:
        check("infinite recursion raises _BudgetExceeded", False,
              f"unexpected exception: {type(e).__name__}: {e}")

# 3. A bounded loop (10 iterations) still works
def t3_bounded_loop():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    h = js.Host(root, log=lambda m: None)
    # `execute` runs statements; `eval` returns the wrapped IIFE's
    # value.  The P9.3 IIFE wrapper does NOT return the last
    # expression (JS functions need an explicit `return`), so use
    # execute for statement-based code and assert on side effects
    # instead of return values.
    h.execute("var s = 0; for (var i = 0; i < 10; i++) { s = s + i; }")
    # The loop must have run to completion without a budget log.
    check("bounded for-loop completes without budget log",
          not any("budget" in m for m in h.logs), f"logs={h.logs}")

# 4. A 1000-iteration while loop still works (well under the 2M budget)
def t4_while_loop():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    h = js.Host(root, log=lambda m: None)
    h.execute("var i = 0; var s = 0; while (i < 1000) { s = s + 1; i = i + 1; }")
    check("1000-iteration while completes without budget log",
          not any("budget" in m for m in h.logs), f"logs={h.logs}")

# 5. execute() with an infinite loop logs a budget message and does
#    NOT raise (the host's catch-all converts the exception to a log).
def t5_execute_infinite_loop():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    logs = []
    h = js.Host(root, log=lambda m: logs.append(m))
    # while(true){} would have hung the process forever before P0.
    h.execute("while (true) {}")
    check("execute(infinite loop) does not raise", True)
    check("execute logs a budget-exceeded message",
          any("budget" in m for m in logs),
          f"logs={logs}")

# 6. Recursion depth can be tuned via the host knob.
def t6_reduced_recursion_cap():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    h = js.Host(root, log=lambda m: None)
    h._js_max_recursion = 50
    try:
        h.eval("function g(n){ return g(n); } g(1)")
        check("reduced cap still catches recursion", False)
    except js._BudgetExceeded:
        check("reduced cap still catches recursion", True)
    except RecursionError:
        check("reduced cap still catches recursion", False,
              "Python RecursionError leaked")

# 7. Arrow functions inherit the recursion guard too.
def t7_arrow_recursion_guard():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    h = js.Host(root, log=lambda m: None)
    code = "const g = (n) => g(n); g(1)"
    try:
        h.eval(code)
        check("arrow recursion caught by guard", False,
              "eval returned normally")
    except js._BudgetExceeded:
        check("arrow recursion caught by guard", True)
    except RecursionError:
        check("arrow recursion caught by guard", False,
              "Python RecursionError leaked")

# 8. Normal functions still work (no budget regression)
def t8_normal_function():
    root = dom.parse("<!DOCTYPE html><html><body></body></html>")
    h = js.Host(root, log=lambda m: None)
    # Use execute for statement-based code; the P9.3 IIFE wrapper in
    # eval() does not implicitly return the last expression (JS
    # functions require an explicit return).
    h.execute("function add(a, b) { return a + b; }")
    result = h.eval("add(3, 4)")
    check("normal function call returns 7", result == 7, f"got {result}")

def main():
    t1_host_knobs()
    t2_recursion_cap()
    t3_bounded_loop()
    t4_while_loop()
    t5_execute_infinite_loop()
    t6_reduced_recursion_cap()
    t7_arrow_recursion_guard()
    t8_normal_function()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All P0 JS budget tests passed.")

if __name__ == "__main__":
    main()


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("t1_host_knobs", t1_host_knobs))
TESTS.append(("t2_recursion_cap", t2_recursion_cap))
TESTS.append(("t3_bounded_loop", t3_bounded_loop))
TESTS.append(("t4_while_loop", t4_while_loop))
TESTS.append(("t5_execute_infinite_loop", t5_execute_infinite_loop))
TESTS.append(("t6_reduced_recursion_cap", t6_reduced_recursion_cap))
TESTS.append(("t7_arrow_recursion_guard", t7_arrow_recursion_guard))
TESTS.append(("t8_normal_function", t8_normal_function))
