"""R7-J01 probe runner — replays the round-7 semantic-probes.json
contract against the toy engine (same shape as the assessment's
toy_js_runner: logs + error per case)."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import os
os.chdir(PROJECT_ROOT)

from toy_browser.document import dom
from toy_browser.scripting import js


def run_case(code: str):
    logs = []
    err = None
    try:
        host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
        host.execute(code)
        # The assessment's toy_js_runner drains the event loop after the
        # script (promise callbacks / timers), so microtask-dependent
        # probes (promise_order / async_function) are observable.
        host.flush_timers()
        host.flush_microtasks()
        host.flush_raf()
    except js.JSUncaughtError as e:
        err = f"JSUncaughtError: {e}"
    except Exception as e:  # parse error etc.
        err = f"{type(e).__name__}: {e}"
    return logs, err


def main():
    probes = json.load(open(
        PROJECT_ROOT / "assessments" / "20261018-round7" / "semantic-probes.json",
        encoding="utf-8"))
    n_ok = 0
    fails = []
    for case in probes["cases"]:
        logs, err = run_case(case["code"])
        ref = case["reference"]
        ok = (logs == ref["logs"]) and ((err is None) == (ref["error"] is None))
        # Compare error *presence* (message wording differs by design).
        if ok:
            n_ok += 1
        else:
            fails.append((case["id"], case["code"], ref, logs, err))
    print(f"matched {n_ok}/{len(probes['cases'])}")
    for cid, code, ref, logs, err in fails:
        print(f"--- {cid}")
        print(f"    code:     {code}")
        print(f"    expected: logs={ref['logs']!r} error={ref['error']!r}")
        print(f"    actual:   logs={logs!r} error={err!r}")
    return 0 if n_ok == len(probes["cases"]) else 1


if __name__ == "__main__":
    sys.exit(main())
