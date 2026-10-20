"""R7-J03 — Fetch / XHR / Response / event-path / diagnostics contract.

Round-7 assessment (逐项改进实施方案.md §4, R7-J03):

1. ``Response.text/json/arrayBuffer`` executor argument order and
   invalid-JSON rejection (a ``SyntaxError`` reject, not a silent
   ``null`` resolve);
2. XHR ``method`` / ``body`` / ``headers`` really go out on the wire;
   a 4xx/5xx is a *transport success* → ``onload``; only a network
   failure fires ``onerror``; ``fetch`` 404 fulfils with ``ok=false``;
3. ``bodyUsed`` one-shot reads, filtered response headers, final URL,
   ``AbortSignal`` cancellation;
4. capture / bubble / ``once`` / ``passive`` /
   ``stopPropagation`` / ``preventDefault`` layering over a real DOM
   event path (not a flat listener list);
5. B03 runtime throws and parse errors are recorded *structurally*
   (``script_errors``) so the diagnostics panel never shows zero
   errors, and a bad script never blocks the next good one.

The wire tests use a loopback echo server (it records the method,
path, body and headers it received); the offline tests use the
in-process engine only.
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.network import net
from toy_browser.network.net import FetchTransport, HttpClient
from toy_browser.scripting import js

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# loopback echo server — records what actually arrived
# ---------------------------------------------------------------------------

def _start_echo_server(routes):
    """Minimal http.server on a random port that records every request
    (method / path / body / Content-Type) in a shared dict.

    ``routes`` maps (method, path) → (status, ctype, body, extra_hdrs).
    Returns (server, port, seen); the caller must shut the server down.
    """
    import http.server
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: dict = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            key = (self.command, self.path.split("?")[0])
            seen.setdefault(key, []).append({
                "method": self.command,
                "path": self.path,
                "body": raw.decode("utf-8", "replace"),
                "content_type": self.headers.get("Content-Type") or "",
                "x_test": self.headers.get("X-Test") or "",
                "count": len(seen.get(key, [])) + 1,
            })
            r = routes.get(key)
            if r is None:
                self.send_response(404)
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"404x")
                return
            status, ctype, body, extra = r
            self.send_response(status)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
        do_POST = _serve
        do_PUT = _serve

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, seen


def _make_host(base, html=None, log=None):
    root = dom.parse(html or "<html><body></body></html>")
    logs = [] if log is None else log
    host = js.Host(root, logs.append)
    host.doc_url = base
    host.doc_origin = net._origin_str(base)
    return host, logs


# ---------------------------------------------------------------------------
# 1. XHR method / body / headers really go out
# ---------------------------------------------------------------------------

@_reg
def test_xhr_post_method_body_headers_reach_server():
    srv, port, seen = _start_echo_server({
        ("POST", "/submit"): (200, "text/plain", b"POSTED", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "var x = new XMLHttpRequest();"
            "x.onload = function(){ console.log('LOAD:' + x.status + ':' + x.responseText); };"
            "x.onerror = function(){ console.log('ERR'); };"
            "x.open('POST', '" + base + "submit');"
            "x.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');"
            "x.setRequestHeader('X-Test', 'j03');"
            "x.send('a=1&b=2');"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "LOAD:200:POSTED" in joined, f"XHR POST onload: {joined}"
        recs = seen.get(("POST", "/submit"), [])
        assert len(recs) == 1, f"server must see one POST: {recs}"
        rec = recs[0]
        assert rec["method"] == "POST", rec
        assert rec["body"] == "a=1&b=2", f"body must go out: {rec}"
        assert rec["content_type"] == "application/x-www-form-urlencoded", rec
        assert rec["x_test"] == "j03", f"setRequestHeader must go out: {rec}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_xhr_put_method_reaches_server():
    srv, port, seen = _start_echo_server({
        ("PUT", "/put-me"): (200, "text/plain", b"PUT-OK", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "var x = new XMLHttpRequest();"
            "x.onload = function(){ console.log('LOAD:' + x.responseText); };"
            "x.open('PUT', '" + base + "put-me');"
            "x.send('payload');"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        assert "LOAD:PUT-OK" in "\n".join(logs), logs
        recs = seen.get(("PUT", "/put-me"), [])
        assert len(recs) == 1 and recs[0]["body"] == "payload", recs
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_xhr_404_fires_onload_not_onerror():
    """R7-J03 — a completed HTTP response (404 included) is a transport
    success: onload fires with the status; onerror is reserved for
    network failures."""
    srv, port, seen = _start_echo_server({})  # no routes → 404
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "var x = new XMLHttpRequest();"
            "x.onload = function(){ console.log('LOAD:' + x.status); };"
            "x.onerror = function(){ console.log('ERR'); };"
            "x.open('GET', '" + base + "missing');"
            "x.send();"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "LOAD:404" in joined, f"404 must fire onload: {joined}"
        assert "ERR" not in joined, f"404 must NOT fire onerror: {joined}"
        assert len(seen.get(("GET", "/missing"), [])) == 1, seen
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_xhr_network_failure_fires_onerror():
    """A closed port is a network failure → onerror (status 0)."""
    # Bind a server, grab the port, close it: nothing listens now.
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    c = HttpClient(timeout=2.0)
    base = f"http://127.0.0.1:{dead_port}/"
    host, logs = _make_host(base, (
        "<html><body><script>"
        "var x = new XMLHttpRequest();"
        "x.onload = function(){ console.log('LOAD:' + x.status); };"
        "x.onerror = function(){ console.log('ERR:' + x.status); };"
        "x.open('GET', '" + base + "x');"
        "x.send();"
        "</script></body></html>"))
    host._fetch_transport = FetchTransport(c, base_url=base)
    host.execute_all()
    host.flush_timers()
    host.flush_microtasks()
    joined = "\n".join(logs)
    assert "ERR:0" in joined, f"network failure must fire onerror: {joined}"
    assert "LOAD:" not in joined, joined
    c.close_pool()


# ---------------------------------------------------------------------------
# 2. fetch contract: 404 fulfilled / ok=false / text / json
# ---------------------------------------------------------------------------

@_reg
def test_fetch_404_fulfilled_with_ok_false():
    srv, port, seen = _start_echo_server({})
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "missing')"
            ".then(function(r){ console.log('FULFILLED:' + r.status + ':' + r.ok); })"
            ".catch(function(e){ console.log('REJECTED:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "FULFILLED:404:false" in joined, \
            f"a 404 must fulfil with ok=false: {joined}"
        assert "REJECTED" not in joined, joined
        assert len(seen.get(("GET", "/missing"), [])) == 1, seen
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_fetch_text_and_json_reads():
    srv, port, seen = _start_echo_server({
        ("GET", "/data.json"): (200, "application/json",
                                b'{"n": 7, "s": "x"}', []),
        ("GET", "/plain.txt"): (200, "text/plain", b"hello world", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "plain.txt')"
            ".then(function(r){ return r.text(); })"
            ".then(function(t){ console.log('TEXT:' + t); });"
            "fetch('" + base + "data.json')"
            ".then(function(r){ return r.json(); })"
            ".then(function(j){ console.log('JSON:' + j.n + ':' + j.s); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "TEXT:hello world" in joined, f"text() read: {joined}"
        assert "JSON:7:x" in joined, f"json() read: {joined}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_fetch_json_invalid_rejects_syntaxerror():
    srv, port, seen = _start_echo_server({
        ("GET", "/bad.json"): (200, "application/json", b"{not json", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "bad.json')"
            ".then(function(r){ return r.json(); })"
            ".then(function(j){ console.log('FULFILLED:' + j); })"
            ".catch(function(e){ console.log('REJECTED:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "REJECTED:SyntaxError" in joined, \
            f"invalid JSON must reject with SyntaxError: {joined}"
        assert "FULFILLED" not in joined, joined
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_response_body_used_one_shot():
    srv, port, seen = _start_echo_server({
        ("GET", "/one.txt"): (200, "text/plain", b"once", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "one.txt')"
            ".then(function(r){"
            "  console.log('USED0:' + r.bodyUsed);"
            "  return r.text();"
            "})"
            ".then(function(t){"
            "  console.log('FIRST:' + t);"
            "  return r2();"
            "})"
            ".catch(function(e){ console.log('ERR:' + e.name); });"
            "function r2(){"
            "  return fetch('" + base + "one.txt').then(function(r){"
            "    return r.text().then(function(){ return r.text(); });"
            "  });"
            "}"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "USED0:false" in joined, f"bodyUsed starts false: {joined}"
        assert "FIRST:once" in joined, f"first read: {joined}"
        assert "ERR:TypeError" in joined, \
            f"second read must reject TypeError: {joined}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_response_headers_get_and_filtering():
    srv, port, seen = _start_echo_server({
        ("GET", "/hdr.txt"): (200, "text/plain", b"x",
                              {"X-Custom": "abc", "Set-Cookie": "sid=1"}),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "hdr.txt')"
            ".then(function(r){"
            "  console.log('CT:' + r.headers.get('content-type'));"
            "  console.log('CUSTOM:' + r.headers.get('X-Custom'));"
            "  console.log('COOKIE:' + (r.headers.get('set-cookie') === null));"
            "  console.log('HAS:' + r.headers.has('x-custom'));"
            "})"
            ".catch(function(e){ console.log('ERR:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "CT:text/plain" in joined, f"headers.get content-type: {joined}"
        assert "CUSTOM:abc" in joined, f"headers.get custom: {joined}"
        assert "COOKIE:true" in joined, \
            f"set-cookie must be filtered from headers: {joined}"
        assert "HAS:true" in joined, joined
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 3. AbortSignal cancellation
# ---------------------------------------------------------------------------

@_reg
def test_aborted_fetch_makes_no_request_and_rejects():
    srv, port, seen = _start_echo_server({
        ("GET", "/never.txt"): (200, "text/plain", b"nope", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "var ac = new AbortController();"
            "ac.abort();"
            "console.log('ABORTED:' + ac.signal.aborted);"
            "fetch('" + base + "never.txt', {signal: ac.signal})"
            ".then(function(){ console.log('FULFILLED'); })"
            ".catch(function(e){ console.log('REJECTED:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "ABORTED:true" in joined, f"signal must flip: {joined}"
        assert "REJECTED:AbortError" in joined, \
            f"aborted fetch must reject AbortError: {joined}"
        assert "FULFILLED" not in joined, joined
        assert not seen, f"an aborted fetch must not reach the wire: {seen}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_fetch_post_init_goes_out():
    srv, port, seen = _start_echo_server({
        ("POST", "/api"): (200, "application/json", b'{"ok":1}', []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "api', {method: 'POST', body: 'k=v',"
            " headers: {'X-Test': 'fetch-post'}})"
            ".then(function(r){ return r.json(); })"
            ".then(function(j){ console.log('OK:' + j.ok); })"
            ".catch(function(e){ console.log('ERR:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "OK:1" in joined, f"fetch POST init: {joined}"
        recs = seen.get(("POST", "/api"), [])
        assert len(recs) == 1, f"server must see the POST: {recs}"
        assert recs[0]["body"] == "k=v", recs
        assert recs[0]["x_test"] == "fetch-post", recs
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 4. DOM event path layering
# ---------------------------------------------------------------------------

def _event_host():
    html = (
        "<html><body><div id='outer'><section id='mid'>"
        "<button id='inner'>go</button></section></div></body></html>")
    host, logs = _make_host("http://x.invalid/", html)
    return host, logs


@_reg
def test_event_capture_then_bubble_order():
    host, logs = _event_host()
    host.execute(
        "var outer = document.getElementById('outer');"
        "var inner = document.getElementById('inner');"
        "var order = [];"
        "outer.addEventListener('ping', function(e){ order.push('outer-capture:' + e.eventPhase); }, true);"
        "outer.addEventListener('ping', function(e){ order.push('outer-bubble:' + e.eventPhase); });"
        "inner.addEventListener('ping', function(e){ order.push('inner:' + e.eventPhase); });"
        "inner.dispatchEvent('ping');"
        "console.log(order.join(','));")
    joined = "\n".join(logs)
    # capture (phase 1) on the ancestor fires BEFORE the target's own
    # listeners (phase 2), which fire before the ancestor's bubble
    # listeners (phase 3).
    assert "outer-capture:1" in joined, joined
    assert "outer-bubble:3" in joined, joined
    assert "inner:2" in joined, joined
    line = [l for l in logs if "outer-capture" in l][0]
    parts = line.split(",")
    assert parts == ["outer-capture:1", "inner:2", "outer-bubble:3"], parts


@_reg
def test_event_stop_propagation_halts_ancestors():
    host, logs = _event_host()
    host.execute(
        "var outer = document.getElementById('outer');"
        "var inner = document.getElementById('inner');"
        "var order = [];"
        "outer.addEventListener('ping', function(e){ order.push('outer'); });"
        "inner.addEventListener('ping', function(e){"
        "  order.push('inner');"
        "  e.stopPropagation();"
        "});"
        "inner.dispatchEvent('ping');"
        "console.log(order.join(','));")
    line = [l for l in logs if "inner" in l][0]
    assert line == "inner", f"stopPropagation must halt the walk: {line}"


@_reg
def test_event_stop_immediate_propagation_halts_same_element():
    host, logs = _event_host()
    host.execute(
        "var inner = document.getElementById('inner');"
        "var order = [];"
        "inner.addEventListener('ping', function(e){"
        "  order.push('first');"
        "  e.stopImmediatePropagation();"
        "});"
        "inner.addEventListener('ping', function(e){ order.push('second'); });"
        "inner.dispatchEvent('ping');"
        "console.log(order.join(','));")
    line = [l for l in logs if "first" in l][0]
    assert line == "first", \
        f"stopImmediatePropagation must skip later listeners: {line}"


@_reg
def test_event_once_removes_listener():
    host, logs = _event_host()
    host.execute(
        "var inner = document.getElementById('inner');"
        "var n = 0;"
        "inner.addEventListener('ping', function(e){ n = n + 1; }, {once: true});"
        "inner.dispatchEvent('ping');"
        "inner.dispatchEvent('ping');"
        "console.log('n=' + n);")
    assert any(l == "n=1" for l in logs), f"once must fire once: {logs}"


@_reg
def test_event_prevent_default_returns_false():
    host, logs = _event_host()
    host.execute(
        "var inner = document.getElementById('inner');"
        "var r1 = inner.dispatchEvent('ping', null, true, true);"
        "inner.addEventListener('ping', function(e){ e.preventDefault(); });"
        "var r2 = inner.dispatchEvent('ping', null, true, true);"
        "console.log('r1=' + r1 + ' r2=' + r2);")
    assert any(l == "r1=true r2=false" for l in logs), \
        f"preventDefault must flip dispatchEvent's return: {logs}"


@_reg
def test_event_remove_event_listener():
    host, logs = _event_host()
    host.execute(
        "var inner = document.getElementById('inner');"
        "var hits = 0;"
        "var fn = function(e){ hits = hits + 1; };"
        "inner.addEventListener('ping', fn);"
        "inner.dispatchEvent('ping');"
        "inner.removeEventListener('ping', fn);"
        "inner.dispatchEvent('ping');"
        "console.log('hits=' + hits);")
    assert any(l == "hits=1" for l in logs), \
        f"removeEventListener must unregister: {logs}"


@_reg
def test_event_target_and_current_target():
    host, logs = _event_host()
    host.execute(
        "var outer = document.getElementById('outer');"
        "var inner = document.getElementById('inner');"
        "outer.addEventListener('ping', function(e){"
        "  console.log('same=' + (e.target === e.currentTarget));"
        "});"
        "inner.dispatchEvent('ping', 7);"
        "var seen = '';"
        "inner.addEventListener('pong', function(e){ seen = e.detail; });"
        "inner.dispatchEvent('pong', 42);"
        "console.log('detail=' + seen);")
    joined = "\n".join(logs)
    # On the ancestor, target (inner) differs from currentTarget (outer).
    assert "same=false" in joined, joined
    assert "detail=42" in joined, f"detail must reach the listener: {joined}"


# ---------------------------------------------------------------------------
# 5. B03 structured diagnostics
# ---------------------------------------------------------------------------

@_reg
def test_runtime_throw_recorded_in_script_errors():
    """R7-J03 — a runtime throw is a structured script_errors entry,
    not a log-only event; the following good script still runs."""
    html = ("<html><body>"
            "<script>throw new Error('boom');</script>"
            "<script>console.log('AFTER');</script>"
            "</body></html>")
    host, logs = _make_host("http://x.invalid/", html)
    host.execute_all()
    assert len(host.script_errors) == 1, \
        f"runtime throw must be recorded: {host.script_errors}"
    entry = host.script_errors[0]
    assert entry["kind"] == "inline", entry
    assert "boom" in entry["error"], entry
    assert any(l == "AFTER" for l in logs), \
        f"a bad script must not block the next one: {logs}"


@_reg
def test_parse_error_recorded_and_next_script_runs():
    html = ("<html><body>"
            "<script>var = ;</script>"
            "<script>console.log('GOOD');</script>"
            "</body></html>")
    host, logs = _make_host("http://x.invalid/", html)
    host.execute_scripts_in_order({})
    assert len(host.script_errors) >= 1, \
        f"parse error must be recorded: {host.script_errors}"
    assert any("GOOD" in l for l in logs), \
        f"the good script after a parse error must run: {logs}"


@_reg
def test_budget_cancellation_recorded_structurally():
    html = ("<html><body>"
            "<script>var i = 0; while (true) { i = i + 1; }</script>"
            "<script>console.log('STILL_HERE');</script>"
            "</body></html>")
    host, logs = _make_host("http://x.invalid/", html)
    host._js_step_budget = 10_000
    host.execute_all()
    assert any(l == "STILL_HERE" for l in logs), \
        f"a budget-cancelled script must not block the next: {logs}"
    assert any("budget" in e["error"].lower() for e in host.script_errors), \
        f"the cancellation must be a structured script error: {host.script_errors}"


# ---------------------------------------------------------------------------
# J03 第 2 点 — 响应头过滤统一消费 request_context.filter_response_headers
# （S02 第 5 点"禁止响应头统一实现"）
# ---------------------------------------------------------------------------

@_reg
def test_response_headers_use_unified_filter():
    """J03 第 2 点 — JS 侧响应头暴露不再各写一套硬编码小集合，统一
    走 request_context.filter_response_headers（同源按
    FORBIDDEN_RESPONSE_HEADERS 集合过滤；跨源 opaque 无可见头）。
    服务端一次响应里同时挂多个 forbidden 头（set-cookie /
    access-control-allow-origin / connection / keep-alive …），
    脚本通过 r.headers.get / has 只能读到非 forbidden 头。"""
    srv, port, seen = _start_echo_server({
        ("GET", "/full.txt"): (
            200, "text/plain", b"body",
            {
                "X-Custom": "visible",
                "Set-Cookie": "sid=1",
                "Access-Control-Allow-Origin": "*",
                "Connection": "keep-alive",
                "Keep-Alive": "timeout=5",
            }),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "full.txt')"
            ".then(function(r){"
            "  console.log('CT:' + r.headers.get('content-type'));"
            "  console.log('VIS:' + r.headers.get('x-custom'));"
            "  console.log('COOKIE:' + (r.headers.get('set-cookie') === null));"
            "  console.log('ACAO:' + (r.headers.get('access-control-allow-origin') === null));"
            "  console.log('CONN:' + (r.headers.get('connection') === null));"
            "  console.log('KEEPALIVE:' + (r.headers.get('keep-alive') === null));"
            "})"
            ".catch(function(e){ console.log('ERR:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        # 普通头保留
        assert "CT:text/plain" in joined, joined
        assert "VIS:visible" in joined, joined
        # forbidden 响应头（set-cookie / access-control-* / connection /
        # keep-alive）全部被剥，脚本读 null
        assert "COOKIE:true" in joined, \
            f"set-cookie must be filtered: {joined}"
        assert "ACAO:true" in joined, \
            f"access-control-allow-origin must be filtered: {joined}"
        assert "CONN:true" in joined, \
            f"connection must be filtered: {joined}"
        assert "KEEPALIVE:true" in joined, \
            f"keep-alive must be filtered: {joined}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_xhr_response_headers_use_unified_filter():
    """J03 第 2 点 — XHR.getResponseHeader / getAllResponseHeaders
    消费同一个 helper：同源按 forbidden 集合过滤，跨源 opaque 全无。"""
    srv, port, seen = _start_echo_server({
        ("GET", "/x.txt"): (
            200, "text/plain", b"x",
            {
                "X-Custom": "ok",
                "Set-Cookie": "a=1",
                "Access-Control-Allow-Credentials": "true",
            }),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "var x = new XMLHttpRequest();"
            "x.open('GET', '" + base + "x.txt', false);"
            "x.send();"
            "console.log('V:' + x.getResponseHeader('x-custom'));"
            "console.log('C:' + (x.getResponseHeader('set-cookie') === null));"
            "console.log('ACAC:' + (x.getResponseHeader('access-control-allow-credentials') === null));"
            "var all = x.getAllResponseHeaders();"
            "console.log('ALL_HAS_COOKIE:' + all.indexOf('set-cookie'));"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "V:ok" in joined, f"x-custom must be visible: {joined}"
        assert "C:true" in joined, \
            f"set-cookie must be filtered in XHR: {joined}"
        assert "ACAC:true" in joined, \
            f"access-control-allow-credentials must be filtered: {joined}"
        # getAllResponseHeaders 也只列非 forbidden 头
        assert "ALL_HAS_COOKIE:-1" in joined, \
            f"getAllResponseHeaders must exclude forbidden: {joined}"
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# J03 第 2 点 / S02 第 5 点 — Response.type 按 S02 决策层判定
# ---------------------------------------------------------------------------

@_reg
def test_response_type_pure_logic_matrix():
    """S02 第 5 点 — request_context.response_type 纯逻辑判定矩阵：
    同源 basic / 跨源 no-cors opaque / 跨源 fetch cors / 不支持 error。
    与 evaluate_request 的 CORS 标记（opaque_response / requires_acao）
    同源，不各写一套。"""
    from toy_browser.network import request_context as rc

    # 同源 fetch → basic
    ctx = rc.context_for_destination(
        "https://a.test", "https://a.test/api", rc.DEST_FETCH)
    assert rc.response_type(ctx) == "basic"

    # 跨源 fetch（cors 模式）→ cors
    ctx = rc.context_for_destination(
        "https://a.test", "https://b.test/api", rc.DEST_FETCH)
    assert rc.response_type(ctx) == "cors"

    # 跨源 classic script（no-cors，opaque）→ opaque
    ctx = rc.context_for_destination(
        "https://a.test", "https://b.test/lib.js", rc.DEST_CLASSIC_SCRIPT)
    assert rc.response_type(ctx) == "opaque"

    # 跨源 image（no-cors，opaque）→ opaque
    ctx = rc.context_for_destination(
        "https://a.test", "https://b.test/x.png", rc.DEST_IMAGE)
    assert rc.response_type(ctx) == "opaque"

    # 同源 image → basic
    ctx = rc.context_for_destination(
        "https://a.test", "https://a.test/x.png", rc.DEST_IMAGE)
    assert rc.response_type(ctx) == "basic"

    # 不支持的 destination（空串）→ error，不偷换 basic 放行
    ctx = rc.RequestContext(
        url="https://a.test", document_origin="https://a.test",
        destination="", mode="")
    assert rc.response_type(ctx) == "error"


@_reg
def test_fetch_response_type_same_origin_basic():
    """J03 第 2 点 / S02 第 5 点 — 同源 fetch 的 Response.type 为
    "basic"（不再写死，按 S02 决策层判定）。"""
    srv, port, seen = _start_echo_server({
        ("GET", "/full.txt"): (200, "text/plain", b"body", {}),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        host, logs = _make_host(base, (
            "<html><body><script>"
            "fetch('" + base + "full.txt')"
            ".then(function(r){"
            "  console.log('TYPE:' + r.type);"
            "})"
            ".catch(function(e){ console.log('ERR:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "TYPE:basic" in joined, \
            f"same-origin fetch must be basic: {joined}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_fetch_cross_origin_js_response_type_cors():
    """J03 第 2 点 / S02 第 5 点 — 跨源 fetch 的 JS 侧
    ``_js_response_type`` 判定为 ``cors``（不再写死 basic）。
    用两个本地端口的 FetchTransport：文档源是 server A，fetch
    目标是 server B（跨源）。server B 响应须携带
    ``Access-Control-Allow-Origin``（匹配 A 的 origin），经
    ``validate_preflight_response`` 校验通过 → ``cors``；
    不携带则降级 ``opaque``（不冒充通过）。"""
    srv_a, port_a, _ = _start_echo_server({
        ("GET", "/doc"): (200, "text/plain", b"doc", {}),
    })
    # 让 server B 回 ACAO: *（非凭据模式跨源 fetch 的最简通过
    # 情形），使校验通过 → cors。
    srv_b, port_b, _ = _start_echo_server({
        ("GET", "/api"): (200, "text/plain", b"cross", {
            "Access-Control-Allow-Origin": "*"
        }),
    })
    try:
        c = HttpClient(timeout=5.0)
        base_a = f"http://127.0.0.1:{port_a}/"
        base_b = f"http://127.0.0.1:{port_b}/"
        host, logs = _make_host(base_a, (
            "<html><body><script>"
            "fetch('" + base_b + "api')"
            ".then(function(r){"
            "  console.log('TYPE:' + r.type);"
            "})"
            ".catch(function(e){ console.log('ERR:' + e.name); });"
            "</script></body></html>"))
        host._fetch_transport = FetchTransport(c, base_url=base_a)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        # 跨源 fetch（127.0.0.1:A → 127.0.0.1:B）且 B 回了
        # Access-Control-Allow-Origin: * → 校验通过 → cors。
        assert "TYPE:cors" in joined, \
            f"cross-origin fetch with ACAO:* must be cors: {joined}"
        c.close_pool()
    finally:
        srv_a.shutdown()
        srv_b.shutdown()
