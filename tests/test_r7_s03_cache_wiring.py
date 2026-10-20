"""R7-S03 第 4 点 — 缓存接线验收（真实磁盘缓存 + 回环服务）。

纯逻辑测试（``test_r7_s03_cache_validity.py``）证明判定**算得对**；
本文件证明它**真的接进了** ``net.CacheStore`` / ``HttpClient``：

1. ``Vary: Accept-Encoding``：换个 Accept-Encoding 的请求不得命中
   （服务端计数必须 +1）；
2. ``Vary: *``：永不入库、永不命中；
3. 带 ``Authorization`` 请求的响应不进共享缓存（RFC 7234 §3.2），
   显式 ``public`` 才可以；
4. **304 头合并**：304 之后 ``Content-Type`` / CSP 等安全头仍正确
   （审计验收项）——修复前 304 分支直接拿 304 的头当响应头；
5. 凭据隔离：以 ``include`` 存的条目，``omit`` 的请求读不到。
"""
import sys, os, threading, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect

from toy_browser.network import net
from toy_browser.network import cache_validity as cv

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _start_server(handler):
    """handler(method, path, headers) → (status, headers_list, body)。
    记录 ``counts[(method, path)]`` 与最后一次请求头 ``last[(path)]``。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    counts: dict = {}
    last: dict = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            path = self.path.split("?")[0]
            counts[(self.command, path)] = counts.get((self.command, path), 0) + 1
            last[path] = dict(self.headers.items())
            status, hdrs, body = handler(self.command, path,
                                         dict(self.headers.items()))
            self.send_response(status)
            for k, v in hdrs:
                self.send_header(k, v)
            if not any(k.lower() == "content-length" for k, _v in hdrs):
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
        do_POST = _serve

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, counts, last


def _store():
    return net.CacheStore(tempfile.mkdtemp(prefix="r7s03cache"), enabled=True)


# ---------------------------------------------------------------------------
# 静态层 — 接线确实存在
# ---------------------------------------------------------------------------

@_reg
def test_static_cache_key_delegates_to_validity():
    src = inspect.getsource(net._cache_key)
    assert "_cv.cache_key" in src, "缓存键必须走 cache_validity（含凭据）"


@_reg
def test_static_put_runs_storability_decision():
    src = inspect.getsource(net.build_cache_entry)
    assert "_cv.is_storable" in src
    assert "return None" in src, "不可存必须返回 None 而不是照存"


@_reg
def test_static_get_merges_304_headers():
    src = inspect.getsource(net.HttpClient.get)
    assert "merge_304_headers" in src, "304 到达后必须合并已存头"
    assert "credentials_mode=_cred" in src, "缓存查找必须带凭据模式"


@_reg
def test_static_lookup_checks_vary():
    src = inspect.getsource(net.CacheStore._load)
    assert "vary_matches" in src, "命中前必须做 Vary 匹配"


# ---------------------------------------------------------------------------
# 1. Vary
# ---------------------------------------------------------------------------

@_reg
def test_vary_mismatch_is_a_miss():
    store = _store()
    seen = []

    def h(method, path, headers):
        seen.append(headers.get("Accept-Encoding"))
        return (200,
                [("Content-Type", "text/plain"),
                 ("Cache-Control", "max-age=600"),
                 ("Vary", "Accept-Encoding")],
                b"body:" + (headers.get("Accept-Encoding") or "").encode())

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        url = f"http://127.0.0.1:{port}/v"
        r1 = c.get(url, headers={"Accept-Encoding": "gzip"})
        assert r1.body == b"body:gzip"
        # 同 Accept-Encoding → 命中缓存（服务端不再被问）。
        r2 = c.get(url, headers={"Accept-Encoding": "gzip"})
        assert r2.body == b"body:gzip"
        assert counts[("GET", "/v")] == 1, \
            f"相同 Vary 字段值应命中缓存，服务端只被问 1 次：{counts}"
        # 换 Accept-Encoding → 不得命中。
        r3 = c.get(url, headers={"Accept-Encoding": "br"})
        assert r3.body == b"body:br", "Vary 不匹配必须重新取真实响应"
        assert counts[("GET", "/v")] == 2
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_vary_star_never_cached():
    store = _store()

    def h(method, path, headers):
        return (200,
                [("Content-Type", "text/plain"),
                 ("Cache-Control", "max-age=600"),
                 ("Vary", "*")],
                b"x")

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        url = f"http://127.0.0.1:{port}/star"
        c.get(url)
        c.get(url)
        assert counts[("GET", "/star")] == 2, \
            "Vary:* 永不命中，两次都要回源"
        assert store.clear() == 0, "Vary:* 的响应不得入库"
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 2. 授权响应（RFC 7234 §3.2）
# ---------------------------------------------------------------------------

@_reg
def test_authenticated_response_not_shared():
    store = _store()

    def h(method, path, headers):
        if path == "/private":
            return (200, [("Content-Type", "text/plain"),
                          ("Cache-Control", "max-age=600")], b"secret")
        return (200, [("Content-Type", "text/plain"),
                      ("Cache-Control", "public, max-age=600")], b"public")

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        c.get(f"http://127.0.0.1:{port}/private",
              headers={"Authorization": "Bearer tok"})
        c.get(f"http://127.0.0.1:{port}/private",
              headers={"Authorization": "Bearer tok"})
        assert counts[("GET", "/private")] == 2, \
            "带 Authorization 的响应不得进共享缓存（RFC 7234 3.2）"
        # 显式 public 才允许缓存。
        c.get(f"http://127.0.0.1:{port}/pub",
              headers={"Authorization": "Bearer tok"})
        c.get(f"http://127.0.0.1:{port}/pub",
              headers={"Authorization": "Bearer tok"})
        assert counts[("GET", "/pub")] == 1, \
            "Cache-Control: public 的响应可以缓存"
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 3. 304 头合并（审计验收项）
# ---------------------------------------------------------------------------

@_reg
def test_304_keeps_security_headers():
    """304 之后 Content-Type / CSP 仍正确（修复前会丢）。"""
    store = _store()
    state = {"n": 0}

    def h(method, path, headers):
        state["n"] += 1
        if headers.get("If-None-Match") == '"v1"':
            # 304：只带变化的头，**不带** Content-Type / CSP。
            return (304,
                    [("ETag", '"v1"'),
                     ("Cache-Control", "max-age=600"),
                     ("Date", "Tue, 02 Jan 2024 00:00:00 GMT")],
                    b"")
        return (200,
                [("Content-Type", "text/html; charset=utf-8"),
                 ("Content-Security-Policy", "default-src 'self'"),
                 ("X-Frame-Options", "DENY"),
                 ("ETag", '"v1"'),
                 ("Cache-Control", "max-age=0")],
                b"<html>hi</html>")

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        url = f"http://127.0.0.1:{port}/doc"
        first = c.get(url)
        assert first.headers.get("content-type") == \
            "text/html; charset=utf-8"
        second = c.get(url)
        assert state["n"] == 2, "第二次必须带着 If-None-Match 去校验"
        assert last["/doc"].get("If-None-Match") == '"v1"', \
            "必须发出条件请求"
        # 关键断言：304 之后安全头仍在。
        assert second.headers.get("content-type") == \
            "text/html; charset=utf-8", \
            f"304 后 Content-Type 丢失：{second.headers}"
        assert second.headers.get("content-security-policy") == \
            "default-src 'self'", "304 后 CSP 丢失"
        assert second.headers.get("x-frame-options") == "DENY"
        assert second.body == b"<html>hi</html>", "304 必须复用缓存 body"
        assert second.headers.get("cache-control") == "max-age=600", \
            "304 带来的新头必须覆盖旧值"
        # 落盘的条目也要保持一致（不是只在内存里合并）。
        entry = store.get_stale(net.parse_url(url),
                                credentials_mode="include") or \
            store.get_fresh(net.parse_url(url),
                            credentials_mode="include")
        assert entry is not None
        assert entry.headers.get("content-security-policy") == \
            "default-src 'self'", "磁盘条目也必须保留 CSP"
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 4. 凭据隔离
# ---------------------------------------------------------------------------

@_reg
def test_cache_entries_do_not_mix_credentials():
    """以 include 存的条目，omit 的请求读不到（不串凭据策略）。"""
    store = _store()

    def h(method, path, headers):
        return (200, [("Content-Type", "text/plain"),
                      ("Cache-Control", "max-age=600")], b"v")

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        url = f"http://127.0.0.1:{port}/cred"
        u = net.parse_url(url)
        resp = c.get(url)
        assert store.put(u, resp, credentials_mode="include")
        assert store.get_fresh(u, credentials_mode="include") is not None
        assert store.get_fresh(u, credentials_mode="omit") is None, \
            "不同凭据模式不得共用同一条缓存记录"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_undeclared_probe_sees_client_entry():
    """未声明凭据的调用方（遗留用法：直接问 store）看得到 client
    刚存的条目——已声明的调用方之间仍然严格隔离（见上一条）。"""
    store = _store()

    def h(method, path, headers):
        return (200, [("Content-Type", "text/plain"),
                      ("Cache-Control", "max-age=600")], b"v")

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        u = net.parse_url(f"http://127.0.0.1:{port}/compat")
        c.get(u)
        # HttpClient 用导航凭据（include）落盘；直接 probe 用 "" 也能
        # 找到（否则 p23 那类"看看存进去没有"的用例会凭空失败）。
        assert store.get_fresh(u) is not None, \
            "未声明凭据的 probe 必须能读到 client 存的条目"
        # 但 omit 这种**已声明**的桶绝不能读到它。
        assert store.get_fresh(u, credentials_mode="omit") is None
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_no_store_never_written():
    store = _store()

    def h(method, path, headers):
        return (200, [("Content-Type", "text/plain"),
                      ("Cache-Control", "no-store")], b"v")

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        c.get(f"http://127.0.0.1:{port}/ns")
        assert store.clear() == 0, "no-store 的响应不得落盘"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_uncacheable_status_not_written():
    store = _store()

    def h(method, path, headers):
        return (500, [("Content-Type", "text/plain")], b"boom")

    srv, port, counts, last = _start_server(h)
    try:
        c = net.HttpClient(timeout=5.0, cache=store)
        c.get(f"http://127.0.0.1:{port}/err")
        assert store.clear() == 0, "5xx 不得入库"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_multi_value_set_cookie_survives_disk_roundtrip():
    """第 3 点联动：多值 Set-Cookie 落盘后仍是列表（不被 str() 压坏）。"""
    store = _store()
    resp = net.HttpResponse(status=200, status_text="OK",
                            headers={"content-type": "text/plain",
                                     "set-cookie": ["a=1", "b=2"],
                                     "cache-control": "max-age=600"},
                            body=b"v", final_url="http://x.test/")
    u = net.parse_url("http://x.test/")
    assert store.put(u, resp)
    got = store.get_fresh(u)
    assert got is not None
    assert got.headers.get("set-cookie") == ["a=1", "b=2"], \
        f"多值 Set-Cookie 落盘被破坏：{got.headers.get('set-cookie')!r}"


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e)))
            print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
