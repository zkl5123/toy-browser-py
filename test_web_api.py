"""P33.P1 — Web API tests: localStorage, sessionStorage, WebSocket,
navigator, location, history, URL, URLSearchParams.

Each global is exercised for its core observable surface.
"""
import dom
import js
import web_api


def _make_host(html="<html><body><div id='d'>hi</div></body></html>"):
    tree = dom.parse(html)
    host = js.Host(tree, lambda s: None)
    return host


# ------------------------------------------------------------------
# localStorage / sessionStorage
# ------------------------------------------------------------------

def test_p1_localstorage_set_get():
    host = _make_host()
    ls = host.globals["localStorage"]
    ls.setItem("k1", "v1")
    assert ls.getItem("k1") == "v1", f"got {ls.getItem('k1')!r}"
    assert ls.length == 1


def test_p1_localstorage_remove_clear():
    host = _make_host()
    ls = host.globals["localStorage"]
    ls.setItem("a", "1")
    ls.setItem("b", "2")
    assert ls.length == 2
    ls.removeItem("a")
    assert ls.getItem("a") is None
    assert ls.length == 1
    ls.clear()
    assert ls.length == 0


def test_p1_localstorage_key_index():
    host = _make_host()
    ls = host.globals["localStorage"]
    ls.setItem("first", "x")
    ls.setItem("second", "y")
    assert ls.key(0) == "first"
    assert ls.key(1) == "second"
    assert ls.key(5) is None


def test_p1_localstorage_quota():
    host = _make_host()
    ls = host.globals["localStorage"]
    big = "a" * (6 * 1024 * 1024)  # 6 MiB > 5 MiB quota
    try:
        ls.setItem("big", big)
        assert False, "expected QuotaExceededError"
    except web_api.QuotaExceededError:
        pass


def test_p1_sessionstorage_separate_from_local():
    host = _make_host()
    local = host.globals["localStorage"]
    sess = host.globals["sessionStorage"]
    local.setItem("k", "local_v")
    sess.setItem("k", "sess_v")
    assert local.getItem("k") == "local_v"
    assert sess.getItem("k") == "sess_v"
    assert local.length == 1 and sess.length == 1


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------

def test_p1_websocket_send_and_state():
    host = _make_host()
    WS = host.globals["WebSocket"]
    ws = WS("ws://example.com")
    assert ws.readyState == ws.OPEN
    ws.send("hello")
    assert ws.sent_messages == ["hello"]
    ws.close()
    assert ws.readyState == ws.CLOSED


def test_p1_websocket_simulate_message():
    host = _make_host()
    WS = host.globals["WebSocket"]
    received = []
    ws = WS("ws://example.com")
    ws.onmessage = lambda ev: received.append(ev["data"])
    ws._simulate_message("from server")
    assert received == ["from server"]


def test_p1_websocket_send_after_close_raises():
    host = _make_host()
    WS = host.globals["WebSocket"]
    ws = WS("ws://x")
    ws.close()
    try:
        ws.send("post-close")
        assert False, "expected exception"
    except Exception:
        pass


# ------------------------------------------------------------------
# navigator
# ------------------------------------------------------------------

def test_p1_navigator_readonly():
    host = _make_host()
    nav = host.globals["navigator"]
    assert "toy-browser-py" in nav["userAgent"]
    assert nav["onLine"] is True
    assert nav["language"] == "en-US"
    assert nav["cookieEnabled"] is True


def test_p1_navigator_hardware_concurrency():
    host = _make_host()
    nav = host.globals["navigator"]
    assert isinstance(nav["hardwareConcurrency"], int)
    assert nav["hardwareConcurrency"] >= 1


# ------------------------------------------------------------------
# location
# ------------------------------------------------------------------

def test_p1_location_default_about_blank():
    host = _make_host()
    loc = host.globals["location"]
    # Host was created without a url → about:blank
    assert loc["hostname"] in ("blank", "")


def test_p1_location_make_location_helper():
    loc = web_api.make_location("https://example.com:8080/path?query=1#frag")
    assert loc["protocol"] == "https:"
    assert loc["hostname"] == "example.com"
    assert loc["port"] == "8080"
    assert loc["pathname"] == "/path"
    assert loc["search"] == "?query=1"
    assert loc["hash"] == "#frag"
    assert loc["origin"] == "https://example.com"


def test_p1_location_origin_http():
    loc = web_api.make_location("http://foo.bar/baz")
    assert loc["origin"] == "http://foo.bar"


# ------------------------------------------------------------------
# history
# ------------------------------------------------------------------

def test_p1_history_initial():
    host = _make_host()
    hist = host.globals["history"]
    assert hist["length"] == 1
    assert hist["state"] is None
    assert hist["_back_stack"] == []
    assert hist["_fwd_stack"] == []


# ------------------------------------------------------------------
# URL
# ------------------------------------------------------------------

def test_p1_url_basic_properties():
    u = web_api.ToyURL("https://example.com:3000/path/x?q=1&r=2#f")
    assert u.protocol == "https:"
    assert u.hostname == "example.com"
    assert u.port == "3000"
    assert u.pathname == "/path/x"
    assert u.search == "?q=1&r=2"
    assert u.hash == "#f"
    assert u.origin == "https://example.com:3000"


def test_p1_url_relative_with_base():
    u = web_api.ToyURL("page.html", "https://example.com/base/")
    assert "example.com" in u.hostname
    assert u.pathname == "/base/page.html"


def test_p1_url_searchparams():
    u = web_api.ToyURL("https://example.com/?a=1&b=2&b=3")
    sp = u.searchParams
    assert sp.get("a") == "1"
    assert sp.getAll("b") == ["2", "3"]
    assert sp.has("a") and sp.has("b")
    assert not sp.has("z")
    sp.append("c", "4")
    assert sp.get("c") == "4"
    sp.delete("a")
    assert not sp.has("a")
    sp.set("a", "9")
    assert sp.get("a") == "9"


def test_p1_url_searchparams_constructor_from_string():
    sp = web_api.ToyURLSearchParams("?x=1&y=2")
    assert sp.get("x") == "1"
    assert sp.get("y") == "2"


# ------------------------------------------------------------------
# URLSearchParams (standalone)
# ------------------------------------------------------------------

def test_p1_urlsearchparams_standalone():
    sp = web_api.ToyURLSearchParams("a=1&b=2")
    assert sp.get("a") == "1"
    assert sp.getAll("b") == ["2"]
    sp.append("b", "3")
    assert sp.getAll("b") == ["2", "3"]
    sp.delete("a")
    assert not sp.has("a")
    s = sp.toString()
    assert "b=2" in s and "b=3" in s


# ------------------------------------------------------------------
# JS-level integration
# ------------------------------------------------------------------

def test_p1_js_localstorage_script():
    html = """<html><body>
    <script>
        localStorage.setItem('greeting', 'hello_world');
    </script>
    </body></html>"""
    host = _make_host(html)
    host.execute_all()
    ls = host.globals["localStorage"]
    assert ls.getItem("greeting") == "hello_world"


def test_p1_js_websocket_script():
    html = """<html><body>
    <script>
        var ws = new WebSocket("ws://example.com");
        ws.send("ping");
        ws.close();
    </script>
    </body></html>"""
    host = _make_host(html)
    host.execute_all()
    assert "WebSocket" in host.globals


# ------------------------------------------------------------------
# register_web_api idempotency
# ------------------------------------------------------------------

def test_p1_register_web_api_idempotent():
    host = _make_host()
    web_api.register_web_api(host)
    assert "localStorage" in host.globals
    assert "WebSocket" in host.globals


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_p1_localstorage_set_get", test_p1_localstorage_set_get))
TESTS.append(("test_p1_localstorage_remove_clear", test_p1_localstorage_remove_clear))
TESTS.append(("test_p1_localstorage_key_index", test_p1_localstorage_key_index))
TESTS.append(("test_p1_localstorage_quota", test_p1_localstorage_quota))
TESTS.append(("test_p1_sessionstorage_separate_from_local", test_p1_sessionstorage_separate_from_local))
TESTS.append(("test_p1_websocket_send_and_state", test_p1_websocket_send_and_state))
TESTS.append(("test_p1_websocket_simulate_message", test_p1_websocket_simulate_message))
TESTS.append(("test_p1_websocket_send_after_close_raises", test_p1_websocket_send_after_close_raises))
TESTS.append(("test_p1_navigator_readonly", test_p1_navigator_readonly))
TESTS.append(("test_p1_navigator_hardware_concurrency", test_p1_navigator_hardware_concurrency))
TESTS.append(("test_p1_location_default_about_blank", test_p1_location_default_about_blank))
TESTS.append(("test_p1_location_make_location_helper", test_p1_location_make_location_helper))
TESTS.append(("test_p1_location_origin_http", test_p1_location_origin_http))
TESTS.append(("test_p1_history_initial", test_p1_history_initial))
TESTS.append(("test_p1_url_basic_properties", test_p1_url_basic_properties))
TESTS.append(("test_p1_url_relative_with_base", test_p1_url_relative_with_base))
TESTS.append(("test_p1_url_searchparams", test_p1_url_searchparams))
TESTS.append(("test_p1_url_searchparams_constructor_from_string", test_p1_url_searchparams_constructor_from_string))
TESTS.append(("test_p1_urlsearchparams_standalone", test_p1_urlsearchparams_standalone))
TESTS.append(("test_p1_js_localstorage_script", test_p1_js_localstorage_script))
TESTS.append(("test_p1_js_websocket_script", test_p1_js_websocket_script))
TESTS.append(("test_p1_register_web_api_idempotent", test_p1_register_web_api_idempotent))
