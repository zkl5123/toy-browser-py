"""R7-S03 第 2 点 — 接线验收：真实发送/重定向使用真实上下文。

纯逻辑测试（``test_r7_s03_cookie_context.py``）证明上下文**算得对**；
本文件证明它**真的被用上**——两个回环端口（不同端口 = 不同源）上跑
真实 HTTP，服务端记录每一跳收到的 ``Cookie`` 头：

1. 同源于资源带 cookie，跨源（换端口）默认不带（凭据门禁真的接进
   了 ``HttpClient`` / ``FetchTransport``）；
2. **重定向逐跳重算**：第 1 跳同源带 cookie，302 到另一个端口后第
   2 跳**不再带**（修复前：第 0 跳算出的 dict 原样带到底，Lax
   cookie 泄漏到跨站目标）；
3. 顶层导航跨源跳转**不**降级（SameSite=Lax 顶层 GET 语义）；
4. in-page ``fetch()`` 走文档归属（同一 client + FetchTransport）；
5. 静态锁：``_follow_redirects`` 必须调用 ``for_redirect``，
   ``_single_get`` 不得再吃 dict 缺省值。
"""
import sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect

from toy_browser.network import net
from toy_browser.network import cookie_store as cs
from toy_browser.network import cookie_context as cc

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _start_server(routes):
    """(method, path) → (status, ctype, body, extra_hdrs)；记录
    ``counts[("hdr", path)]`` = 该路径最后一次收到的 Cookie 头。"""
    import http.server
    from http.server import BaseHTTPRequestHandler, HTTPServer

    counts: dict = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            key = (self.command, self.path.split("?")[0])
            counts[key] = counts.get(key, 0) + 1
            counts[("hdr", key[1])] = self.headers.get("Cookie") or ""
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            r = routes.get(key)
            if r is None:
                self.send_response(404)
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"nf")
                return
            status, ctype, body, extra = r
            self.send_response(status)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in (extra or []):
                self.send_header(k, v)
            if not any(k.lower() == "content-length"
                       for k, _v in (extra or [])):
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
        do_POST = _serve

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, counts


def _hdr(counts, path):
    return counts.get(("hdr", path), "")


def _jar_with(cookie="lax=1"):
    jar = cs.CookieJar()
    jar.ingest_response("http://127.0.0.1/", {"set-cookie": cookie})
    return jar


# ---------------------------------------------------------------------------
# 静态层 — 接线确实存在（不是只在模块里写好却没人调用）
# ---------------------------------------------------------------------------

@_reg
def test_static_follow_redirects_recomputes_per_hop():
    src = inspect.getsource(net._follow_redirects)
    assert "for_redirect(" in src, \
        "重定向必须逐跳重新求值（旧代码直接透传同一个 dict）"


@_reg
def test_static_single_get_has_no_dict_defaults():
    src = inspect.getsource(net.HttpClient._single_get)
    assert "is_top_level\", True" not in src, \
        "不得保留『缺省顶层导航』"
    assert "ctx.get(" not in src, "不得再从 dict 里取默认值"
    assert "cookie_header(" in src, "必须走类型化上下文的凭据门禁"


@_reg
def test_static_client_has_top_level_url():
    src = inspect.getsource(net.HttpClient.__init__)
    assert "top_level_url" in src


@_reg
def test_static_fetch_transport_carries_document():
    src = inspect.getsource(net.FetchTransport.__init__)
    assert "document_url" in src and "credentials_mode" in src
    fsrc = inspect.getsource(net.FetchTransport.fetch)
    assert "cookie_context=ctx" in fsrc, "fetch/XHR 必须带上下文"


@_reg
def test_static_post_uses_typed_context():
    src = inspect.getsource(net.HttpClient.post)
    assert "_cookie_ctx_for(" in src, "POST 也要走真实上下文"
    assert "ctx.ingest(" in src, "POST 响应 Set-Cookie 入库（第 3 点）"


# ---------------------------------------------------------------------------
# 动态层 — 回环双端口（不同端口 = 不同源）
# ---------------------------------------------------------------------------

@_reg
def test_same_origin_sends_cookie_cross_origin_does_not():
    """凭据门禁真的生效：跨源（换端口）默认 same-origin ⇒ 不带。"""
    routes = {
        ("GET", "/a"): (200, "text/plain", b"A", []),
    }
    srv, port, counts = _start_server(routes)
    srv2, port2, counts2 = _start_server(routes)
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        # 顶层导航到 A：带 cookie。
        c.get(f"http://127.0.0.1:{port}/a")
        assert _hdr(counts, "/a") == "lax=1", \
            f"同站顶层导航应带 cookie：{_hdr(counts, '/a')!r}"
        # 顶层导航到 B（另一个端口 = 另一个源）：cookie 是 host-only
        # 于 127.0.0.1，host 匹配 ⇒ 顶层导航仍带（站点 = 自己）。
        c.get(f"http://127.0.0.1:{port2}/a")
        assert _hdr(counts2, "/a") == "lax=1"
        c.close_pool()
    finally:
        srv.shutdown()
        srv2.shutdown()


@_reg
def test_subresource_cross_origin_drops_cookie():
    """子资源上下文：跨源（换端口）子资源不带 cookie。"""
    routes = {("GET", "/s.css"): (200, "text/css", b"body{}", [])}
    srv, port, counts = _start_server(routes)
    srv2, port2, counts2 = _start_server(routes)
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        doc = f"http://127.0.0.1:{port}/"
        # 同源子资源 → 带。
        net.fetch_resource(net.parse_url(f"http://127.0.0.1:{port}/s.css"),
                           client=c,
                           cookie_context=cc.for_subresource(
                               f"http://127.0.0.1:{port}/s.css", doc,
                               destination="style"))
        assert _hdr(counts, "/s.css") == "lax=1", \
            f"同源子资源应带 cookie：{_hdr(counts, '/s.css')!r}"
        # 跨源子资源（同站不同端口）→ 凭据 same-origin ⇒ 不带。
        net.fetch_resource(net.parse_url(f"http://127.0.0.1:{port2}/s.css"),
                           client=c,
                           cookie_context=cc.for_subresource(
                               f"http://127.0.0.1:{port2}/s.css", doc,
                               destination="style"))
        assert _hdr(counts2, "/s.css") == "", \
            f"跨源子资源不得带 cookie：{_hdr(counts2, '/s.css')!r}"
        c.close_pool()
    finally:
        srv.shutdown()
        srv2.shutdown()


@_reg
def test_redirect_second_hop_cross_origin_drops_cookie():
    """**审计点名的泄漏**：第 1 跳同源带 cookie，302 到另一个源后
    第 2 跳不得继续带。

    修复前 ``_follow_redirects`` 把第 0 跳算出的 dict 原样透传，且
    credentials 从未参与判定 ⇒ 两跳都带。"""
    srv2, port2, counts2 = _start_server({
        ("GET", "/b"): (200, "text/plain", b"B", []),
    })
    srv, port, counts = _start_server({
        ("GET", "/a"): (302, "text/plain", b"",
                        [("Location", f"http://127.0.0.1:{port2}/b")]),
    })
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        doc = f"http://127.0.0.1:{port}/"
        ctx = cc.for_subresource(f"http://127.0.0.1:{port}/a", doc,
                                 destination="fetch")
        resp = c.get(f"http://127.0.0.1:{port}/a", cookie_context=ctx)
        assert resp.status == 200 and resp.body == b"B", \
            "重定向链必须真的走到第二跳"
        assert _hdr(counts, "/a") == "lax=1", \
            f"第一跳同源应带 cookie：{_hdr(counts, '/a')!r}"
        assert _hdr(counts2, "/b") == "", \
            f"跨源第二跳不得继续带 cookie：{_hdr(counts2, '/b')!r}"
        c.close_pool()
    finally:
        srv.shutdown()
        srv2.shutdown()


@_reg
def test_redirect_same_origin_keeps_cookie():
    """同源跳转（同端口不同路径）两跳都带——不要误伤正常行为。"""
    srv, port, counts = _start_server({
        ("GET", "/a"): (302, "text/plain", b"", [("Location", "/b")]),
        ("GET", "/b"): (200, "text/plain", b"B", []),
    })
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        doc = f"http://127.0.0.1:{port}/"
        ctx = cc.for_subresource(f"http://127.0.0.1:{port}/a", doc,
                                 destination="fetch")
        c.get(f"http://127.0.0.1:{port}/a", cookie_context=ctx)
        assert _hdr(counts, "/a") == "lax=1"
        assert _hdr(counts, "/b") == "lax=1", \
            f"同源跳转第二跳仍应带 cookie：{_hdr(counts, '/b')!r}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_top_level_redirect_keeps_cookie():
    """顶层导航跨源跳转**不**降级（SameSite=Lax 跨站顶层 GET）。"""
    srv2, port2, counts2 = _start_server({
        ("GET", "/b"): (200, "text/plain", b"B", []),
    })
    srv, port, counts = _start_server({
        ("GET", "/a"): (302, "text/plain", b"",
                        [("Location", f"http://127.0.0.1:{port2}/b")]),
    })
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        # 不给 cookie_context ⇒ 顶层导航语义（HttpClient 的缺省路径）。
        c.get(f"http://127.0.0.1:{port}/a")
        assert _hdr(counts2, "/b") == "lax=1", \
            f"顶层导航跨源跳转仍应带 cookie：{_hdr(counts2, '/b')!r}"
        c.close_pool()
    finally:
        srv.shutdown()
        srv2.shutdown()


@_reg
def test_fetch_transport_uses_document_origin():
    """in-page fetch()：同源带 cookie，跨源不带（凭据默认 same-origin）。"""
    routes = {("GET", "/api"): (200, "application/json", b"{}", [])}
    srv, port, counts = _start_server(routes)
    srv2, port2, counts2 = _start_server(routes)
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        doc = f"http://127.0.0.1:{port}/"
        t = net.FetchTransport(c, base_url=doc, document_url=doc)
        r1 = t.fetch(f"http://127.0.0.1:{port}/api")
        assert r1 is not None and r1.ok
        assert _hdr(counts, "/api") == "lax=1", \
            f"同源 fetch 应带 cookie：{_hdr(counts, '/api')!r}"
        r2 = t.fetch(f"http://127.0.0.1:{port2}/api")
        assert r2 is not None and r2.ok
        assert _hdr(counts2, "/api") == "", \
            f"跨源 fetch 默认不得带 cookie：{_hdr(counts2, '/api')!r}"
        c.close_pool()
    finally:
        srv.shutdown()
        srv2.shutdown()


@_reg
def test_fetch_transport_include_credentials_opt_in():
    """显式 include 才跨源带 cookie（不静默降级，也不静默放行）。"""
    srv2, port2, counts2 = _start_server({
        ("GET", "/api"): (200, "application/json", b"{}", []),
    })
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        t = net.FetchTransport(c, base_url="http://127.0.0.1:1/",
                               document_url="http://127.0.0.1:1/",
                               credentials_mode=cc.CRED_INCLUDE)
        t.fetch(f"http://127.0.0.1:{port2}/api")
        assert _hdr(counts2, "/api") == "lax=1", \
            "include 模式应跨源带 cookie"
        c.close_pool()
    finally:
        srv2.shutdown()


@_reg
def test_post_set_cookie_ingested_and_context_used():
    """第 3 点：POST 响应 Set-Cookie 入库；POST 也走凭据门禁。"""
    srv, port, counts = _start_server({
        ("POST", "/p"): (200, "text/plain", b"ok",
                         [("Set-Cookie", "posted=1")]),
    })
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        c.post(f"http://127.0.0.1:{port}/p", data={"a": "b"},
               cookie_context=cc.for_subresource(
                   f"http://127.0.0.1:{port}/p",
                   f"http://127.0.0.1:{port}/", destination="fetch"))
        assert "posted" in {x.name for x in jar.all()}, \
            "POST 响应 Set-Cookie 必须入库"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_unknown_context_client_never_sends():
    """顶层归属未知 ⇒ 不发（与旧的『缺省同站顶层』相对）。"""
    srv, port, counts = _start_server({
        ("GET", "/x"): (200, "text/plain", b"X", []),
    })
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        ctx = cc.unknown_context(f"http://127.0.0.1:{port}/x")
        c.get(f"http://127.0.0.1:{port}/x", cookie_context=ctx)
        assert _hdr(counts, "/x") == "", \
            f"未知归属不得带 cookie：{_hdr(counts, '/x')!r}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_client_top_level_url_drives_subresource_default():
    """绑定了 top_level_url 的 client：未显式给上下文时按子资源判定。"""
    srv, port, counts = _start_server({
        ("GET", "/x"): (200, "text/plain", b"X", []),
    })
    srv2, port2, counts2 = _start_server({
        ("GET", "/x"): (200, "text/plain", b"X", []),
    })
    try:
        jar = _jar_with()
        c = net.HttpClient(timeout=5.0, cookies=jar)
        c.top_level_url = f"http://127.0.0.1:{port}/"
        c.get(f"http://127.0.0.1:{port}/x")  # == 顶层文档本身 → 带
        assert _hdr(counts, "/x") == "lax=1"
        c.get(f"http://127.0.0.1:{port2}/x")  # 跨源子资源 → 不带
        assert _hdr(counts2, "/x") == "", \
            f"跨源不得带 cookie：{_hdr(counts2, '/x')!r}"
        c.close_pool()
    finally:
        srv.shutdown()
        srv2.shutdown()


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
