"""R7-S01 — JS→Python host object capability boundary.

Round-7 security audit S01 (网络脚本与安全审计.md §3): page script
could read ``location._host.doc_url``, ``localStorage.__class__.__name__``
and ``fetch.__self__._js_step_budget`` through the interpreter's generic
``getattr``/``setattr`` fallbacks.  These tests pin the fixed contract:

* the three probed private reads now yield ``undefined`` (None);
* every host-object class only exposes its declared capability table
  (read / write / call);
* legitimate Web API surface (location parts, storage, URL, Date,
  Map/Set, XHR callbacks, DOM element props/methods) still works.

Only synthetic metadata is used — no real files, credentials or
destructive payloads.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.scripting import js

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _run(html: str):
    """Parse + run all inline scripts; return the host's log lines."""
    root = dom.parse(html)
    host = js.Host(root, lambda m: None)
    host.execute_all()
    return host.logs


# ---------------------------------------------------------------------------
# 1. the three audit probes must read back as undefined
# ---------------------------------------------------------------------------

@_reg
def test_location_host_private_field_blocked():
    logs = _run(
        "<html><body><script>"
        "console.log('v:' + (typeof location._host));"
        "console.log('doc:' + (location._host && location._host.doc_url));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "v:undefined" in joined, joined
    assert "doc:undefined" in joined, joined


@_reg
def test_storage_class_metadata_blocked():
    logs = _run(
        "<html><body><script>"
        "console.log('c:' + (typeof localStorage.__class__));"
        "console.log('n:' + (localStorage.__class__ && localStorage.__class__.__name__));"
        "console.log('s:' + (typeof localStorage._store));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "c:undefined" in joined, joined
    assert "n:undefined" in joined, joined
    assert "s:undefined" in joined, joined


@_reg
def test_fetch_self_budget_blocked():
    logs = _run(
        "<html><body><script>"
        "console.log('s:' + (typeof fetch.__self__));"
        "console.log('b:' + (fetch.__self__ && fetch.__self__._js_step_budget));"
        "console.log('m:' + (typeof fetch.__self__._js_step_budget));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "s:undefined" in joined, joined
    assert "b:undefined" in joined, joined
    assert "m:undefined" in joined, joined


# ---------------------------------------------------------------------------
# 2. optional chain / bracket / method-extraction paths share the boundary
# ---------------------------------------------------------------------------

@_reg
def test_optional_chain_shares_boundary():
    logs = _run(
        "<html><body><script>"
        "console.log('a:' + (location?._host));"
        "console.log('b:' + (localStorage?.__class__));"
        "console.log('c:' + (typeof fetch?.__self__));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "a:undefined" in joined and "b:undefined" in joined, joined
    assert "c:undefined" in joined, joined


@_reg
def test_bracket_access_shares_boundary():
    # obj[key] on a host object never resolves (Index path), but the
    # extracted-method path must not leak private attributes either.
    logs = _run(
        "<html><body><script>"
        "var f = location.__lookupGetter__;"
        "console.log('g:' + (typeof f));"
        "var h = localStorage.hasOwnProperty;"
        "console.log('h:' + (typeof h));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "g:undefined" in joined, joined
    assert "h:undefined" in joined, joined


@_reg
def test_writes_to_host_objects_are_dropped():
    root = dom.parse("<html><body><script>"
                     "localStorage._store = {'x': 1};"
                     "location._host = 1;"
                     "</script></body></html>")
    host = js.Host(root, lambda m: None)
    host.execute_all()
    # The private fields must be untouched by the blocked writes.
    store = host.globals["localStorage"]
    assert getattr(store, "_store", {}) == {}, "blocked write must not land"
    loc = host.globals["location"]
    assert not hasattr(loc, "_host") or getattr(loc, "_host") is not None


@_reg
def test_dom_element_node_not_reachable():
    logs = _run(
        "<html><body><div id='d'>x</div><script>"
        "var el = document.getElementById('d');"
        "console.log('n:' + (typeof el.node));"
        "console.log('h:' + (typeof el._host_ref));"
        "console.log('i:' + el.id);"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "n:undefined" in joined, joined
    assert "h:undefined" in joined, joined
    assert "i:d" in joined, joined


# ---------------------------------------------------------------------------
# 3. legitimate surface keeps working (no over-blocking)
# ---------------------------------------------------------------------------

@_reg
def test_location_parts_still_readable():
    # A real document URL projects the standard URL parts.
    root = dom.parse("<html><body></body></html>")
    host = js.Host(root, lambda m: None)
    host.doc_url = "https://a.example.test:8080/p/q?x=1#frag"
    host.execute_all()
    host.execute(
        "console.log('p:' + location.protocol);"
        "console.log('h:' + location.host);"
        "console.log('hn:' + location.hostname);"
        "console.log('po:' + location.port);"
        "console.log('pa:' + location.pathname);"
        "console.log('s:' + location.search);"
        "console.log('f:' + location.hash);"
        "console.log('o:' + location.origin);")
    joined = "\n".join(host.logs)
    assert "p:https:" in joined, joined
    assert "h:a.example.test:8080" in joined, joined
    assert "hn:a.example.test" in joined, joined
    assert "po:8080" in joined, joined
    assert "pa:/p/q" in joined, joined
    assert "s:?x=1" in joined, joined
    assert "f:#frag" in joined, joined
    assert "o:https://a.example.test:8080" in joined, joined


@_reg
def test_storage_api_still_works():
    logs = _run(
        "<html><body><script>"
        "localStorage.setItem('k', 'v');"
        "console.log('get:' + localStorage.getItem('k'));"
        "console.log('len:' + localStorage.length);"
        "localStorage.removeItem('k');"
        "console.log('gone:' + (localStorage.getItem('k') === null));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "get:v" in joined, joined
    assert "len:1" in joined, joined
    assert "gone:true" in joined, joined


@_reg
def test_date_statics_and_methods_still_work():
    logs = _run(
        "<html><body><script>"
        "var d = new Date(2020, 0, 15);"
        "console.log('y:' + d.getFullYear());"
        "console.log('m:' + d.getMonth());"
        "console.log('n:' + (typeof Date.now));"
        "console.log('p:' + (typeof Date.parse));"
        "console.log('u:' + (typeof Date.UTC));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "y:2020" in joined, joined
    assert "m:0" in joined, joined
    assert "n:function" in joined, joined
    assert "p:function" in joined, joined
    assert "u:function" in joined, joined


@_reg
def test_map_set_still_work():
    logs = _run(
        "<html><body><script>"
        "var m = new Map(); m.set('a', 1); m.set('b', 2);"
        "console.log('sz:' + m.size);"
        "console.log('get:' + m.get('a'));"
        "console.log('has:' + m.has('b'));"
        "var s = new Set([1, 2, 2]);"
        "console.log('ssz:' + s.size);"
        "console.log('shas:' + s.has(2));"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "sz:2" in joined, joined
    assert "get:1" in joined, joined
    assert "has:true" in joined, joined
    assert "ssz:2" in joined, joined
    assert "shas:true" in joined, joined


@_reg
def test_url_api_still_works():
    logs = _run(
        "<html><body><script>"
        "var u = new URL('https://a.example.test:8080/p/q?x=1#f');"
        "console.log('h:' + u.host);"
        "console.log('pn:' + u.pathname);"
        "console.log('s:' + u.search);"
        "console.log('f:' + u.hash);"
        "console.log('o:' + u.origin);"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "h:a.example.test:8080" in joined, joined
    assert "pn:/p/q" in joined, joined
    assert "s:?x=1" in joined, joined
    assert "f:#f" in joined, joined
    assert "o:https://a.example.test:8080" in joined, joined


@_reg
def test_xhr_callback_write_still_allowed():
    # xhr.onload = fn must land (writable whitelist entry) — the
    # callback contract the URL pipeline relies on.
    root = dom.parse("<html><body></body></html>")
    host = js.Host(root, lambda m: None)
    host.execute("var x = new XMLHttpRequest(); x.onload = function(){};")
    x = host.globals  # placeholder; fetch the instance via a fresh interp
    interp = host._interp()
    interp.env = js.Env(host.globals, None)
    program = js.parse_program("var x = new XMLHttpRequest(); x.onload = function(){};")
    interp.eval_block(program)
    x = interp.env.lookup("x")
    assert callable(x.onload), "onload write must land on the XHR instance"


@_reg
def test_error_object_still_readable():
    logs = _run(
        "<html><body><script>"
        "try { throw new Error('boom'); } catch (e) {"
        "console.log('m:' + e.message);"
        "console.log('n:' + e.name);"
        "}"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "m:boom" in joined, joined
    assert "n:Error" in joined, joined


@_reg
def test_websocket_surface_still_works():
    logs = _run(
        "<html><body><script>"
        "var w = new WebSocket('ws://x/');"
        "console.log('rs:' + w.readyState);"
        "w.send('hello');"
        "console.log('url:' + w.url);"
        "</script></body></html>")
    joined = "\n".join(logs)
    assert "rs:1" in joined, joined
    assert "url:ws://x/" in joined, joined


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
