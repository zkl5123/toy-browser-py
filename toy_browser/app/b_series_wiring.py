"""批次 41 — B 系列活显示 GUI 接线 + 网络 I/O 实装（纯逻辑核心）。

依据：``assessments/20261017-round6/README.md``"剩余限制（下一批次）"
行 "B 系列 B01–B10 纯逻辑核心全部落地（活显示 / GUI 接线 / 网络 I/O
留后续批次）"。批次 16–20 已落地 B01–B10 纯逻辑核心
（``navigation.py`` / ``activation.py`` / ``field_editing.py`` /
``document_context.py`` / ``load_phases.py`` / ``request_config.py`` /
``first_screen.py``），本批补**活显示 GUI 接线 + 网络 I/O 实装**：

* :class:`BGuiWiring` — 把 B01 ``NavigationController`` + B07
  ``activate_element`` + B08 ``DocumentContext`` 接成可驱动
  ``gui.py`` 的活显示接线核心（导航决策 → 页面加载 / 激活 → 表单提交
  → 资源生命周期登记的统一分派）。
* :func:`network_io_smoke` — **网络 I/O 实装**：用本地
  ``http.server`` 起一个真实 HTTP 端点，驱动
  ``net.HttpClient`` 的 ``get`` / ``post`` / 重定向 / 404 路径，
  验证 B02 ``RequestConfig`` + B08 ``DocumentContext`` 的实装闭环
  （活显示批次需真实网络 I/O；本核心只走本地 loopback，不依赖出站
  网络——环境限制见 README"在线对照复测仍阻塞"）。
* :func:`run_b_series_local_http` — 起本地 ``http.server``，跑
  ``net.fetch_html`` + ``browser.render_document_from_url`` 的
  本地实装冒烟（活显示批次"网络 I/O 实装"验收项）。

设计要点（与批次 16–20 **兼容不重叠**）：
- 批次 16–20 管 B01–B10 纯逻辑核心（导航 / 激活 / 字段编辑 / 资源
  生命周期 / 阶段状态机 / 请求配置 / 首屏可读），本批补**活显示 GUI
  接线 + 网络 I/O 实装**（本地 loopback HTTP 实装冒烟 + B 系列核心
  接入 gui.py 的活显示分派）。
- 纯逻辑核心：不强制 tkinter；网络实装只走本地 ``http.server``
  loopback（出站网络仍阻塞——"在线对照复测"留后续）。
- 回归用 ``tests/test_b_series_gui_wiring.py`` 覆盖。
"""

from __future__ import annotations

import os
import sys
import select
import socket
import threading
from typing import Optional, Callable
from dataclasses import dataclass, field

try:
    from toy_browser.app.navigation import (
        NavRequest, NavOutcome, NavigationController,
        SRC_ADDRESS, SRC_LINK, SRC_SCRIPT)
    from toy_browser.app.activation import (
        activate_element, parse_link_target, build_form_submission,
        FormField, TRIGGER_MOUSE, TRIGGER_ENTER, TRIGGER_SUBMIT)
    from toy_browser.app.document_context import DocumentContext
    from toy_browser.app.request_config import RequestConfig
except Exception:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.app.navigation import (
        NavRequest, NavOutcome, NavigationController,
        SRC_ADDRESS, SRC_LINK, SRC_SCRIPT)
    from toy_browser.app.activation import (
        activate_element, parse_link_target, build_form_submission,
        FormField, TRIGGER_MOUSE, TRIGGER_ENTER, TRIGGER_SUBMIT)
    from toy_browser.app.document_context import DocumentContext
    from toy_browser.app.request_config import RequestConfig


# ---------------------------------------------------------------------------
# BGuiWiring — B 系列活显示 GUI 接线核心（导航 + 激活 + 资源生命周期）
# ---------------------------------------------------------------------------

@dataclass
class BDispatchResult:
    """B 系列活显示分派结果（导航 / 激活 / 提交 / 资源登记）。"""
    ok: bool
    action: str = "none"       # "navigate" / "submit" / "focus" / "none"
    target: str = ""
    error: Optional[str] = None
    query: str = ""
    method: str = "GET"
    prevented: bool = False
    blocked: bool = False
    violations: list = field(default_factory=list)


@dataclass
class InitialRoute:
    """B01 批次 55 — 启动参数统一路由判定结果（纯逻辑）。

    ``route`` 取值：
    - ``"local_file"`` — 本地文件存在，走 :meth:`PageInstance.load_file`；
    - ``"url"`` — http(s) / about 等 URL 形态，经统一导航入口提交
      （目标在 ``target``）；
    - ``"blank"`` — 空参数或不可识别，落到 ``about:blank``。

    设计：把启动参数"该走哪条导航"的判定从 ``gui.py`` 的
    ``TabBrowser.__init__`` 抽成可测纯逻辑，使启动 / 地址栏 / 链接
    三类导航共享同一 :class:`NavigationController` 语义（B01 统一
    控制器），同时**不改动既有渲染路径**（本地文件仍 ``load_file``）。
    """
    route: str
    target: str = ""


def resolve_initial_route(initial: Optional[str],
                          is_local_file: Callable[[str], bool]) -> InitialRoute:
    """B01 批次 55 — 启动参数统一路由（本地文件 / URL / about:blank）。

    判定规则（与 :meth:`NavigationController.classify_address_input`
    的"网址"分支同源，保证启动与地址栏入口语义一致）：

    - 空 / 仅空白 → ``blank``；
    - 本地文件存在（``is_local_file(path)`` 为真）→ ``local_file``；
    - 绝对 URL（含 scheme，如 ``http://``/``https://``/``about:`` 等）
      或带路径特征的裸 URL（如 ``localhost:8000``、``www.x.com``）
      → ``url``（目标原样保留）；
    - 其余（纯文本、相对片段）→ ``blank``（不当本地文件导航，避免
      启动参数被误当文件路径）。

    ``is_local_file`` 由调用方注入（GUI 传 ``os.path.isfile``），使
    本函数无文件系统副作用、可直接单测。
    """
    from toy_browser.app.navigation import _is_url_like as _is_url_form
    s = (initial or "").strip()
    if not s:
        return InitialRoute(route="blank")
    if is_local_file(s):
        return InitialRoute(route="local_file", target=s)
    if _is_url_form(s):
        return InitialRoute(route="url", target=s)
    return InitialRoute(route="blank")



class BGuiWiring:
    """B 系列活显示 GUI 接线核心。

    把 B01 ``NavigationController``（导航决策）+ B07
    ``activate_element``（激活动作）+ B08 ``DocumentContext``（资源
    生命周期）接成 ``gui.py`` 的活显示分派入口。

    * ``nav`` — 导航控制器（B01 批次 17 纯逻辑核心）。
    * ``ctx`` — 文档上下文（B08 批次 19 纯逻辑核心；资源三态 +
      任务版本）。
    """

    def __init__(self,
                 nav: Optional[NavigationController] = None,
                 ctx: Optional[DocumentContext] = None) -> None:
        self.nav = nav or NavigationController()
        self.ctx = ctx or DocumentContext()

    # -- B01 — 地址栏 / 链接导航分派 ---------------------------------------

    def dispatch_address(self, text: str,
                         search_url_for=None) -> BDispatchResult:
        """B01 步骤 3 — 地址栏输入三分类 + 分派（活显示入口）。"""
        req = self.nav.classify_address_input(text, search_url_for)
        outcome = self.nav.dispatch(req)
        if not outcome.ok:
            return BDispatchResult(ok=False, action="none",
                                    target=req.target, error=req.error)
        # B08 — 任务版本门控（导航提交 = bump）
        self.ctx.bump_task_version()
        self.ctx.final_url = outcome.final_url
        return BDispatchResult(ok=True, action="navigate",
                                target=outcome.final_url)

    def dispatch_link(self, href: str, target_attr: str = "",
                      cancelable: bool = True,
                      on_click=None) -> BDispatchResult:
        """B07 步骤 3 — 链接点击激活 + B01 分派（活显示入口）。"""
        base = self.nav.current_url or "about:blank"
        resolved, url_kind, scheme_ok = parse_link_target(
            href, base, target_attr)
        if not scheme_ok:
            return BDispatchResult(ok=True, action="none",
                                    target=resolved,
                                    error="scheme-not-supported")
        req = NavRequest(target=resolved, source=SRC_LINK,
                         replace=(url_kind == "current"))
        outcome = self.nav.dispatch(req)
        if not outcome.ok:
            return BDispatchResult(ok=False, action="none",
                                    target=resolved, error=req.error)
        self.ctx.bump_task_version()
        self.ctx.final_url = outcome.final_url
        return BDispatchResult(ok=True, action="navigate",
                                target=outcome.final_url)

    # -- B07 — 表单提交分派 --------------------------------------------------

    def dispatch_form(self, fields: list, form_id: str = "",
                      action: str = "", method: str = "GET",
                      trigger: str = TRIGGER_SUBMIT,
                      validate: bool = True) -> BDispatchResult:
        """B07 步骤 4 — 表单提交（活显示入口）：校验 + 序列化 + 分派。"""
        base = self.nav.current_url or "about:blank"
        res = build_form_submission(fields, form_id, action, base,
                                    method, trigger, validate)
        if res.get("blocked"):
            return BDispatchResult(ok=False, action="submit",
                                    blocked=True,
                                    violations=res.get("violations", []))
        url = res.get("url", "")
        req = NavRequest(target=url, source=SRC_SCRIPT,
                         replace=(trigger == TRIGGER_SUBMIT))
        outcome = self.nav.dispatch(req)
        return BDispatchResult(
            ok=outcome.ok,
            action="submit" if outcome.ok else "none",
            target=outcome.final_url if outcome.ok else url,
            method=res.get("method", "GET"),
            query=res.get("body", ""),
            error=req.error if not outcome.ok else None)

    # -- B08 — 资源生命周期登记 -------------------------------------------

    def mark_resource(self, url: str, state: str,
                      status_code: int = 0,
                      blocked_reason: str = "") -> None:
        """B08 步骤 2 — 资源三态登记（活显示批次资源生命周期接线）。"""
        if state == "downloaded":
            self.ctx.mark_downloaded(url, status_code or 200)
        elif state == "decoded":
            self.ctx.mark_decoded(url)
        elif state == "applied":
            self.ctx.mark_applied(url)
        elif state == "blocked":
            self.ctx.mark_blocked(url, reason=blocked_reason or "blocked")

    def describe(self) -> str:
        """诊断串（活显示批次诊断区消费）。"""
        rc = self.ctx.resource_completeness()
        return (f"BGuiWiring nav={self.nav.current_url} "
                f"ctx.final={self.ctx.final_url} "
                f"resources_total={rc.get('total', 0)} "
                f"downloaded={rc.get('downloaded', 0)} "
                f"applied={rc.get('applied', 0)}")


# ---------------------------------------------------------------------------
# 网络 I/O 实装（本地 loopback HTTP；出站网络仍阻塞）
# ---------------------------------------------------------------------------

class _LocalHttpHandler:
    """本地 ``http.server`` 处理器：GET / POST 回显 + 重定向 + 404。

    活显示批次"网络 I/O 实装"用——只走本地 loopback，不依赖出站
    网络（环境限制：TCP 443 出站超时；本地 loopback + 自托管
    http.server 可用）。
    """

    def __init__(self) -> None:
        self.requests: list = []

    def handle_get(self, path: str) -> tuple[int, bytes, dict]:
        self.requests.append(("GET", path))
        if path.startswith("/redirect"):
            return 302, b"", {"Location": "/ok"}
        if path == "/ok":
            return 200, b"<html><body>local-ok</body></html>", \
                {"Content-Type": "text/html"}
        if path == "/post-echo":
            return 405, b"", {"Allow": "POST"}
        return 404, b"not found", {"Content-Type": "text/plain"}

    def handle_post(self, path: str, body: bytes) -> tuple[int, bytes, dict]:
        self.requests.append(("POST", path, body))
        if path == "/post-echo":
            return 200, b"echo:" + body, \
                {"Content-Type": "text/plain"}
        return 404, b"not found", {"Content-Type": "text/plain"}


def start_local_http_server(handler: Optional[_LocalHttpHandler] = None,
                             host: str = "127.0.0.1",
                             port: int = 0) -> tuple:
    """起本地 loopback HTTP 服务器（活显示批次"网络 I/O 实装"用）。

    返回 ``(server, handler, actual_port)``；调用方负责
    ``server.shutdown()``。*port=0* 让系统分配空闲端口（避免冲突）。
    用 ``http.server`` 的 ``BaseHTTPRequestHandler`` 直接喂连接
    （不依赖 ``socketserver`` 的自动绑定，避免 double-bind）。
    """
    import http.server
    import socket
    import select

    handler = handler or _LocalHttpHandler()

    # 手动绑定 socket
    import socket as _sock_mod
    import socketserver as _ss
    _port = int(port) if port is not None else 0
    sock = _sock_mod.socket(_sock_mod.AF_INET, _sock_mod.SOCK_STREAM)
    sock.setsockopt(_sock_mod.SOL_SOCKET, _sock_mod.SO_REUSEADDR, 1)
    sock.bind((host, _port))
    actual_port = sock.getsockname()[1]
    sock.close()  # socketserver 重新 bind

    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        def do_GET(self):
            code, body, hdrs = handler.handle_get(self.path)
            self.send_response(code)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else b""
            code, resp, hdrs = handler.handle_post(self.path, body)
            self.send_response(code)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    class _SS(_ss.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        server = _SS((host, actual_port), _H)
    except OSError:
        # 重新 bind
        import socket as _s2
        s2 = _s2.socket(_s2.AF_INET, _s2.SOCK_STREAM)
        s2.setsockopt(_s2.SOL_SOCKET, _s2.SO_REUSEADDR, 1)
        s2.bind((host, actual_port))
        s2.close()
        server = _SS((host, 0), _H)
        actual_port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.05},
                         daemon=True)
    t.start()

    class _MiniServer:
        def __init__(self, server, thread, port):
            self._server = server
            self._thread = thread
            self._port = port

        def shutdown(self):
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass

    return _MiniServer(server, t, actual_port), handler, actual_port


def network_io_smoke(base_url: str,
                     config: Optional[RequestConfig] = None,
                     ctx: Optional[DocumentContext] = None) -> dict:
    """B02 + B08 网络 I/O 实装冒烟（本地 loopback；活显示验收项）。

    驱动 ``net.HttpClient`` 的 GET / POST / 重定向 / 404 路径，
    验证 B02 ``RequestConfig``（UA / Accept / 超时）+ B08
    ``DocumentContext``（资源三态登记）的实装闭环。

    返回 ``{"ok": bool, "get_ok": bool, "post_ok": bool,
    "redirect_ok": bool, "not_found_ok": bool, "config": str,
    "ctx": str}``。
    """
    from toy_browser.network import net
    result = {"ok": False, "get_ok": False, "post_ok": False,
              "redirect_ok": False, "not_found_ok": False,
              "config": "", "ctx": ""}
    cfg = config or RequestConfig()
    cx = ctx if ctx is not None else DocumentContext()
    client = net.HttpClient(timeout=cfg.timeout or 5.0)
    try:
        # GET /ok
        resp = client.get(base_url + "/ok")
        result["get_ok"] = (resp.status == 200)
        cx.mark_downloaded(base_url + "/ok", resp.status)
        cx.mark_decoded(base_url + "/ok")
        cx.mark_applied(base_url + "/ok")
        # POST /post-echo
        resp2 = client.post(base_url + "/post-echo",
                             data=b"q=1",
                             content_type="application/x-www-form-urlencoded")
        result["post_ok"] = (resp2.status == 200)
        cx.mark_downloaded(base_url + "/post-echo", resp2.status)
        # 重定向 /redirect → /ok
        resp3 = client.get(base_url + "/redirect")
        result["redirect_ok"] = (resp3.status in (200, 302))
        # 404
        resp4 = client.get(base_url + "/missing")
        result["not_found_ok"] = (resp4.status == 404)
        cx.mark_blocked(base_url + "/missing", "404")
    except Exception as e:
        result["error"] = str(e)
    result["config"] = cfg.describe() if hasattr(cfg, "describe") else \
        f"ua={cfg.ua} timeout={cfg.timeout}"
    result["ctx"] = f"final={cx.final_url} " \
        f"completeness={cx.resource_completeness()}"
    result["ok"] = result["get_ok"] and result["post_ok"]
    return result


def run_b_series_local_http(
        port: int = 0,
        handler: Optional[_LocalHttpHandler] = None,
        width: int = 160,
        height: int = 120) -> dict:
    """活显示批次"网络 I/O 实装"验收：起本地 ``http.server``，跑
    ``net.fetch_html`` + ``browser.render_document_from_url`` 的本地
    实装冒烟（不依赖出站网络）。

    返回 ``{"ok": bool, "url": str, "bmp_nonwhite": int,
    "port": int, "error": Optional[str]}``。
    """
    from toy_browser.network import net
    from toy_browser.app import browser as _browser_mod
    server, handler, actual_port = start_local_http_server(
        handler, host="127.0.0.1", port=port)
    url = f"http://127.0.0.1:{actual_port}/ok"
    result = {"ok": False, "url": url, "bmp_nonwhite": 0,
              "port": actual_port, "error": None}
    try:
        try:
            bmp = _browser_mod.render_document_from_url(
                url, width=width, height=height,
                load_subresources=False,
                client=net.HttpClient(timeout=5.0))
            result["bmp_nonwhite"] = sum(
                1 for p in bmp.pixels if p != 0xFFFFFF)
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)
    finally:
        server.shutdown()
    return result


# ---------------------------------------------------------------------------
# 烟测
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    w = BGuiWiring()
    r1 = w.dispatch_address("http://127.0.0.1:12345/ok")
    print("address:", r1.action, r1.target)
    r2 = w.dispatch_link("/relative", "")
    print("link:", r2.action, r2.target, r2.error)
    r3 = w.dispatch_form([FormField(name="q", value="hello",
                                     kind="text")],
                        form_id="f1", action="/search",
                        method="GET")
    print("form:", r3.action, r3.target, r3.query)
    # 网络 I/O 实装（本地 loopback）
    import json
    out = json.dumps(run_b_series_local_http(), ensure_ascii=False)
    print("local http:", out)
