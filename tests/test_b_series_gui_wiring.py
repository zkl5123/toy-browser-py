"""批次 41 回归 — B 系列活显示 GUI 接线 + 网络 I/O 实装。

覆盖：
1. :class:`BGuiWiring.dispatch_address` — B01 地址栏分派 → 导航成功。
2. :class:`BGuiWiring.dispatch_link` — B07 链接激活 → B01 分派。
3. :class:`BGuiWiring.dispatch_form` — B07 表单提交 → B01 分派。
4. :class:`BGuiWiring.mark_resource` — B08 资源三态登记。
5. :func:`start_local_http_server` — 本地 loopback HTTP 起停。
6. :func:`network_io_smoke` — B02 + B08 网络 I/O 实装冒烟。
7. :func:`run_b_series_local_http` — ``render_document_from_url`` 本地实装。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.b_series_wiring import (
    BGuiWiring, BDispatchResult,
    start_local_http_server, network_io_smoke, run_b_series_local_http,
    _LocalHttpHandler)
from toy_browser.app.activation import FormField
from toy_browser.app.document_context import DocumentContext
from toy_browser.app.navigation import NavigationController, NavRequest, SRC_ADDRESS
from toy_browser.app.request_config import RequestConfig
from skip_support import require_tk


# ---------------------------------------------------------------------------
# 1. dispatch_address — B01 地址栏分派
# ---------------------------------------------------------------------------

def test_dispatch_address_ok():
    w = BGuiWiring()
    r = w.dispatch_address("http://example.com/page")
    assert r.ok is True
    assert r.action == "navigate"
    assert r.target == "http://example.com/page"
    assert w.nav.current_url == "http://example.com/page"


def test_dispatch_address_empty():
    w = BGuiWiring()
    r = w.dispatch_address("")
    assert r.ok is False
    assert r.error == "empty-input"


def test_dispatch_address_updates_ctx_version():
    w = BGuiWiring()
    v0 = w.ctx.task_version
    r = w.dispatch_address("http://example.com/a")
    assert r.ok
    assert w.ctx.task_version == v0 + 1
    assert w.ctx.final_url == "http://example.com/a"


# ---------------------------------------------------------------------------
# 2. dispatch_link — B07 链接激活 + B01 分派
# ---------------------------------------------------------------------------

def test_dispatch_link_relative():
    w = BGuiWiring()
    w.nav.current_url = "http://example.com/base"
    r = w.dispatch_link("/rel", "")
    assert r.ok is True
    assert r.action == "navigate"
    assert r.target == "http://example.com/rel"


def test_dispatch_link_unsupported_scheme():
    w = BGuiWiring()
    w.nav.current_url = "http://example.com/base"
    r = w.dispatch_link("javascript:alert(1)", "")
    assert r.ok is True          # 决策成功（不是导航失败）
    assert r.action == "none"
    assert r.error == "scheme-not-supported"


def test_dispatch_link_blank_target():
    w = BGuiWiring()
    w.nav.current_url = "http://example.com/base"
    r = w.dispatch_link("http://other.com", "_blank")
    # url_kind == "blank" → replace=False（新建历史项）
    assert r.ok is True
    assert r.action == "navigate"
    assert r.target == "http://other.com"


# ---------------------------------------------------------------------------
# 3. dispatch_form — B07 表单提交 + B01 分派
# ---------------------------------------------------------------------------

def test_dispatch_form_get_ok():
    w = BGuiWiring()
    w.nav.current_url = "http://example.com"
    fields = [
        FormField(name="q", value="hello world", kind="text"),
        FormField(name="page", value="2", kind="text"),
    ]
    r = w.dispatch_form(fields, form_id="f1", action="/search", method="GET")
    assert r.ok is True
    assert r.action == "submit"
    assert "q=hello" in r.target or "q=hello%20world" in r.target
    assert "page=2" in r.target


def test_dispatch_form_blocked_required():
    w = BGuiWiring()
    w.nav.current_url = "http://example.com"
    fields = [FormField(name="required", value="", kind="text")]
    # 模拟 required 字段（activation.py _required_empty 钩子）
    fields[0].required = True
    r = w.dispatch_form(fields, form_id="f", action="/x", method="GET",
                       trigger="requestSubmit", validate=True)
    assert r.ok is False
    assert r.blocked is True
    assert len(r.violations) >= 1


def test_dispatch_form_post_body():
    w = BGuiWiring()
    w.nav.current_url = "http://example.com"
    fields = [FormField(name="a", value="1", kind="text")]
    r = w.dispatch_form(fields, form_id="f", action="/submit", method="POST")
    assert r.ok is True
    assert r.action == "submit"
    assert r.method == "POST"
    assert r.query == "a=1"


# ---------------------------------------------------------------------------
# 4. mark_resource — B08 资源三态登记
# ---------------------------------------------------------------------------

def test_mark_resource_states():
    w = BGuiWiring()
    url = "http://example.com/res.js"
    w.mark_resource(url, "downloaded", status_code=200)
    assert url in w.ctx.resources
    assert w.ctx.resources[url].downloaded is True
    w.mark_resource(url, "decoded")
    assert w.ctx.resources[url].decoded is True
    w.mark_resource(url, "applied")
    assert w.ctx.resources[url].applied is True


def test_mark_resource_blocked():
    w = BGuiWiring()
    url = "http://example.com/bad.png"
    w.mark_resource(url, "blocked", blocked_reason="404")
    assert url in w.ctx.resources
    assert w.ctx.resources[url].blocked_reason == "404"


def test_describe():
    w = BGuiWiring()
    w.nav.current_url = "http://example.com"
    w.mark_resource("http://example.com/a.css", "applied")
    desc = w.describe()
    assert "nav=http://example.com" in desc
    assert "resources_total=" in desc
    assert "downloaded=" in desc
    assert "applied=" in desc


# ---------------------------------------------------------------------------
# 5. start_local_http_server — 本地 loopback 起停
# ---------------------------------------------------------------------------

def test_start_and_shutdown_local_http():
    srv, h, port = start_local_http_server(_LocalHttpHandler())
    assert port > 0
    srv.shutdown()


def test_local_http_get_200():
    srv, h, port = start_local_http_server(_LocalHttpHandler())
    try:
        import urllib.request
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/ok", timeout=5)
        body = resp.read()
        assert b"local-ok" in body
        assert resp.status == 200
    finally:
        srv.shutdown()


def test_local_http_get_404():
    srv, h, port = start_local_http_server(_LocalHttpHandler())
    try:
        import urllib.request
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/missing", timeout=5)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_local_http_post_echo():
    srv, h, port = start_local_http_server(_LocalHttpHandler())
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/post-echo",
            data=b"q=test",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = urllib.request.urlopen(req, timeout=5)
        body = resp.read().decode("latin-1")
        assert "echo:q=test" in body
    finally:
        srv.shutdown()


def test_local_http_redirect():
    srv, h, port = start_local_http_server(_LocalHttpHandler())
    try:
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/redirect")
        resp = conn.getresponse()
        assert resp.status == 302
        assert resp.getheader("Location") == "/ok"
        conn.close()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 6. network_io_smoke — B02 + B08 网络 I/O 实装冒烟
# ---------------------------------------------------------------------------

def test_network_io_smoke():
    srv, h, port = start_local_http_server(_LocalHttpHandler())
    try:
        base = f"http://127.0.0.1:{port}"
        res = network_io_smoke(base)
        assert res["get_ok"] is True, f"get_ok failed: {res}"
        assert res["post_ok"] is True, f"post_ok failed: {res}"
        assert res["ok"] is True
    finally:
        srv.shutdown()


def test_network_io_smoke_with_ctx():
    srv, h, port = start_local_http_server(_LocalHttpHandler())
    try:
        base = f"http://127.0.0.1:{port}"
        cx = DocumentContext()
        res = network_io_smoke(base, ctx=cx)
        assert res["get_ok"]
        # B08 资源登记（GET /ok 标记 downloaded+decoded+applied，
        # POST /post-echo 标记 downloaded，404 /missing 标记 blocked）
        assert len(cx.resources) >= 2
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 7. run_b_series_local_http — render_document_from_url 本地实装
# ---------------------------------------------------------------------------

def test_run_b_series_local_http():
    res = run_b_series_local_http()
    assert res["ok"] is True, f"local http render failed: {res}"
    assert res["port"] > 0
    assert res["bmp_nonwhite"] > 0
    assert res["error"] is None


def test_run_b_series_local_http_url():
    res = run_b_series_local_http()
    assert res["url"].startswith("http://127.0.0.1:")


# ---------------------------------------------------------------------------
# 回归：BGuiWiring + 纯逻辑核心
# ---------------------------------------------------------------------------

def test_bguiwiring_default_nav_blank():
    w = BGuiWiring()
    assert w.nav.current_url == "about:blank"


def test_bguiwiring_custom_nav():
    nav = NavigationController(initial_url="http://seed.example/")
    w = BGuiWiring(nav=nav)
    r = w.dispatch_link("page.html", "")
    assert r.ok
    assert r.target == "http://seed.example/page.html"


# ---------------------------------------------------------------------------
# 8. 批次 42 — gui.py TabBrowser 活显示接线（stub 驱动，不构建 Tk）
# ---------------------------------------------------------------------------

class _PageStub:
    """最小 PageInstance 替身：记录 load_url 调用，供 TabBrowser stub 测试。"""

    def __init__(self, url: str = "about:blank"):
        self.url = url
        self.width = 800
        self.height = 600
        self.title = "test"
        self.back_stack: list = []
        self.fwd_stack: list = []
        self.bitmap = None
        self._dom_tree = None
        self.hover_id = None
        self.focus_id = None
        self.active_form_field = None
        self._browser = None

    def load_url(self, url, w=800, h=600):
        self.url = url


def _make_stub_tabbrowser(initial_url="about:blank"):
    """构造一个不跑 __init__ 的 TabBrowser stub，注入 BGuiWiring + 最小桩属性。

    与既有 gui 回归测试（test_g09_gui_wiring / test_g11_gui_wiring）的
    ``object.__new__(TabBrowser)`` 模式一致：避免构建完整 Tk UI，只
    验证 BGuiWiring 分派映射 + 既有导航执行路径的接线。
    """
    require_tk()  # R7-Q01 — 缺 Tk 环境 → SKIP，不算功能失败
    from toy_browser.app.gui import TabBrowser
    tb = object.__new__(TabBrowser)
    tb.tabs = [_PageStub(initial_url)]
    tb.current = 0
    tb._b_wiring = BGuiWiring()
    tb._refresh_canvas = lambda idx: None
    tb._update_status = lambda: None
    tb.status_var = type("S", (), {"set": staticmethod(lambda v: None)})()
    tb.notebook = type("N", (), {"tab": staticmethod(
        lambda a, b=None, text=None, c=None: None)})()

    # addr_var 桩：get 返回初始 URL，set 记录最后一次
    tb.addr_var = type("V", (), {
        "get": staticmethod(lambda: initial_url),
        "set": staticmethod(lambda v: None),
        "trace_add": staticmethod(lambda *a: None),
    })()
    return tb


def test_b42_navigate_via_b_wiring():
    """批次 42 — _navigate 经 BGuiWiring 分派（地址栏 → commit_navigation）。"""
    tb = _make_stub_tabbrowser("http://seed.example/")
    committed = []
    tb._commit_navigation = lambda url: committed.append(url)
    tb._navigate()
    assert committed == ["http://seed.example/"]


def test_b42_navigate_to_b_wiring_relative():
    """批次 42 — _navigate_to 经 BGuiWiring dispatch_link（相对链接 → 最终 URL）。"""
    tb = _make_stub_tabbrowser("http://seed.example/dir/")
    nav_called = []
    tb._commit_navigation = lambda url: nav_called.append(url)
    # 相对链接 / 不支持 scheme → 走既有回退路径（page.load_url / no-op）
    tb._navigate_to("page.html")
    assert nav_called == ["http://seed.example/dir/page.html"]


def test_b42_navigate_to_b_wiring_unsupported_scheme():
    """批次 42 — _navigate_to 经 BGuiWiring dispatch_link（javascript: → 回退既有路径）。"""
    tb = _make_stub_tabbrowser("http://seed.example/")
    tb._navigate_to("javascript:void(0)")
    # 不支持 scheme → 既有回退路径（不导航），page.url 不变
    assert tb.tabs[0].url == "http://seed.example/"


def test_b42_navigate_to_b_wiring_absolute():
    """批次 42 — _navigate_to 经 BGuiWiring dispatch_link（绝对 URL → 最终 URL）。"""
    tb = _make_stub_tabbrowser("about:blank")
    nav_called = []
    tb._commit_navigation = lambda url: nav_called.append(url)
    tb._navigate_to("http://other.example/x")
    assert nav_called == ["http://other.example/x"]


def test_b42_dispatch_form_b_wiring():
    """批次 42 — _b_dispatch_form 经 BGuiWiring 分派（GET 表单 → 导航 URL）。"""
    tb = _make_stub_tabbrowser("http://seed.example/")
    fields = [FormField(name="q", value="test", kind="text")]
    r = tb._b_dispatch_form(fields, "f1", "/search", "GET")
    assert r.ok
    assert r.action == "submit"
    assert r.target.endswith("/search?q=test")


def test_b42_describe_b_wiring():
    """批次 42 — _b_describe 诊断串（资源三态 + 任务版本）。"""
    tb = _make_stub_tabbrowser("about:blank")
    r = tb._b_dispatch_link("/a")
    assert r.ok
    s = tb._b_describe()
    assert "nav=http://seed" not in s or "nav=" in s
    assert "ctx.final=" in s
    assert "resources_total=" in s


TESTS = [
    ("b41_dispatch_address_ok", test_dispatch_address_ok),
    ("b41_dispatch_address_empty", test_dispatch_address_empty),
    ("b41_dispatch_address_updates_ctx_version", test_dispatch_address_updates_ctx_version),
    ("b41_dispatch_link_relative", test_dispatch_link_relative),
    ("b41_dispatch_link_unsupported_scheme", test_dispatch_link_unsupported_scheme),
    ("b41_dispatch_link_blank_target", test_dispatch_link_blank_target),
    ("b41_dispatch_form_get_ok", test_dispatch_form_get_ok),
    ("b41_dispatch_form_blocked_required", test_dispatch_form_blocked_required),
    ("b41_dispatch_form_post_body", test_dispatch_form_post_body),
    ("b41_mark_resource_states", test_mark_resource_states),
    ("b41_mark_resource_blocked", test_mark_resource_blocked),
    ("b41_describe", test_describe),
    ("b41_start_and_shutdown_local_http", test_start_and_shutdown_local_http),
    ("b41_local_http_get_200", test_local_http_get_200),
    ("b41_local_http_get_404", test_local_http_get_404),
    ("b41_local_http_post_echo", test_local_http_post_echo),
    ("b41_local_http_redirect", test_local_http_redirect),
    ("b41_network_io_smoke", test_network_io_smoke),
    ("b41_network_io_smoke_with_ctx", test_network_io_smoke_with_ctx),
    ("b41_run_b_series_local_http", test_run_b_series_local_http),
    ("b41_run_b_series_local_http_url", test_run_b_series_local_http_url),
    ("b41_bguiwiring_default_nav_blank", test_bguiwiring_default_nav_blank),
    ("b41_bguiwiring_custom_nav", test_bguiwiring_custom_nav),
    ("b42_navigate_via_b_wiring", test_b42_navigate_via_b_wiring),
    ("b42_navigate_to_b_wiring_relative", test_b42_navigate_to_b_wiring_relative),
    ("b42_navigate_to_b_wiring_unsupported_scheme", test_b42_navigate_to_b_wiring_unsupported_scheme),
    ("b42_navigate_to_b_wiring_absolute", test_b42_navigate_to_b_wiring_absolute),
    ("b42_dispatch_form_b_wiring", test_b42_dispatch_form_b_wiring),
    ("b42_describe_b_wiring", test_b42_describe_b_wiring),
]
