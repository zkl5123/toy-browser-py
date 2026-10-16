"""Run each JS case through toy-browser's own js engine + DOM, mirroring edge_js.js."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import dom, js

CASES = json.load(open(os.path.join(os.path.dirname(__file__), 'js_cases.json'), encoding='utf-8'))
DOM_HTML = '<!DOCTYPE html><html><body><div id="t1">orig</div><button id="btn">B</button></body></html>'

out = {}
for c in CASES:
    tree = dom.parse(DOM_HTML)
    logs = []
    host = js.Host(tree, log=logs.append)
    try:
        host.execute(c['code'])
        status = 'OK'
    except js.JSUncaughtError as e:
        # P0 (agent report) — mirror real-browser semantics: an uncaught
        # JS exception halts the script and surfaces as an error status
        # (Edge reference records `ERROR: <name>: <msg>`).  The thrown
        # value is a JS "SyntaxError: …" string — split it into
        # name + message for the status line.  The toy engine no longer
        # writes this into the console log stream (JSUncaughtError
        # propagation keeps logs clean), so no log filtering needed.
        m = str(e.value)
        parts = m.split(": ", 1)
        name = parts[0] if parts[0] in ("SyntaxError", "TypeError", "RangeError",
                                        "ReferenceError", "EvalError", "URIError") else "Error"
        msg = parts[1] if len(parts) > 1 else m
        status = f"ERROR: {name}: {msg}"
    except Exception as e:
        status = f'ERROR: {type(e).__name__}: {e}'
    # drain timers + microtasks like the edge runner.
    # P1 (assessment report) — the toy engine now defers Promise
    # ``.then`` handlers onto a microtask queue that drains after the
    # synchronous script body + timers (V8 ordering: sync → timers →
    # microtasks).  probe_microtasks requires "start, end, promise",
    # so the drain must include the microtask pass.
    drain_error = None
    try:
        host.flush_raf()
        if hasattr(host, 'flush_timers'):
            host.flush_timers()
        elif hasattr(host, 'drain_timers'):
            host.drain_timers()
        elif hasattr(host, 'timers'):
            host.drain()
        # P1 — flush_microtasks drains the queue; the queue is re-armed
        # by each .then attach on a settled promise, so a single pass
        # may not settle chained promises (probe_async's f().then).
        # flush_timers already calls flush_microtasks internally, but
        # re-run it here to be explicit and idempotent.
        if hasattr(host, 'flush_microtasks'):
            host.flush_microtasks()
    except Exception as error:
        drain_error = repr(error)
    out[c['id']] = {'status': status, 'logs': list(logs), 'drain_error': drain_error}

with open(os.path.join(os.path.dirname(__file__), 'toy_js_results.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=1)
print('toy_js_results.json written,', len(out), 'cases')
