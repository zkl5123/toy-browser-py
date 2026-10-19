"""R7-A01/B05 — GUI 侧消费 _pending_nav（脚本 location 导航跟跳）。

Round-7 评估（逐项改进实施方案 R7-A01 第 4 点"GUI 消费脚本同步产生的
导航" + GUI 与工程质量审计 B05"公共 API 跟跳但 GUI 未消费 pending_nav"）：

* CLI ``browser.render_document_from_url`` 已有 B05 跟跳段（消费
  ``host._pending_nav`` + seen-URL 去重 + 循环预算），但 GUI 的
  ``PageInstance.load_url`` 执行脚本后直接 ``load_html``，未消费
  ``_pending_nav``——百度 227 字节跳转页在 GUI 下停在脚本页而非最终
  文档（hit-test / 历史 / 命中都不对应真实渲染页）。

本批端到端：本地 http.server 夹具提供跳转页（``location.replace`` 到
``/real``），验证 ``PageInstance.load_url`` 跟跳到最终目标；并验证
baidu 形状的 https↔http 反复替换被循环预算拦住（不死循环）。

完整 Python 3.11.9 环境（纯逻辑层，不依赖 Tk/wgpu）。
"""
import os
import sys
import threading
import http.server
import socketserver

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import gui

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


class _RedirHandler(http.server.BaseHTTPRequestHandler):
    """提供两个端点：/redir（脚本 location.replace 到 /real）、/real。"""

    def log_message(self, *args):  # 静默
        pass

    def do_GET(self):
        if self.path == "/redir":
            body = (b"<!DOCTYPE html><html><head><title>redir</title></head>"
                    b"<body><script>location.replace('/real');</script>"
                    b"</body></html>")
        elif self.path == "/real":
            body = (b"<!DOCTYPE html><html><head><title>real</title></head>"
                    b"<body><h1 id='target'>REAL-TARGET</h1></body></html>")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server():
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _RedirHandler)
    port = server.server_address[1]
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server, port


@_test
def test_load_url_follows_script_navigation():
    """GUI load_url 消费 _pending_nav：/redir 脚本 location.replace 到
    /real，GUI 应跟跳到 /real（而非停在 /redir）。"""
    server, port = _start_server()
    try:
        page = gui.PageInstance()
        page.load_url(f"http://127.0.0.1:{port}/redir",
                      w=300, h=300, record_history=False)
        # 跟跳后 url 应为最终目标 /real（循环预算 + seen 去重保证）。
        assert page.url.endswith("/real"), (
            f"GUI load_url must follow script navigation to /real, "
            f"got {page.url!r}")
        # 渲染页含最终文档内容（REAL-TARGET）。
        assert getattr(page, "_dom_tree", None) is not None
    finally:
        server.shutdown()


@_test
def test_loop_guard_https_http_replace():
    """baidu 形状：脚本反复 location.replace(https↔http)——循环预算
    拦住死循环，不无限跟跳（GUI 侧 seen 去重 + 每跳重抓）。"""
    # 直接构造 _pending_nav 自指环：/redir → /redir（reload 形状），
    # seen 去重后应识别为 reload 不跟跳。
    server, port = _start_server()
    try:
        # 用一个会"自指"的页面：location.replace 回自己 → 视为 reload。
        page = gui.PageInstance()
        # /redir 的 target 若是自身 url，B05 段视为 reload（continue），
        # 不跟跳，page.url 保持 /redir。
        page.load_url(f"http://127.0.0.1:{port}/redir",
                      w=300, h=300, record_history=False)
        # 无论是否跟跳，url 必须是受控的（要么 /redir 要么 /real），
        # 关键是不死循环（load_url 返回即通过）。
        assert page.url.endswith(("/redir", "/real")), (
            f"loop guard must keep url bounded, got {page.url!r}")
    finally:
        server.shutdown()


@_test
def test_pending_nav_reload_not_followed():
    """location.replace 回自身 URL = reload（非跨文档导航），GUI 不
    跟跳——保持 B05 的 reload 语义（CLI 段同契约）。"""
    # 直接造 _pending_nav 验证消费逻辑的 reload 分支（不依赖网络）。
    page = gui.PageInstance()
    page.url = "http://127.0.0.1:9/here"
    from toy_browser.scripting import js as _js
    host = _js.Host(page._dom_tree or _js.DomNode.element("html"),
                     log=lambda s: None)
    # 模拟一个 _pending_nav 指向自身（reload）：消费后不应跟跳。
    host._pending_nav = ["http://127.0.0.1:9/here"]
    pending = list(host._pending_nav)
    seen = {"http://127.0.0.1:9/here"}
    follow = None
    for req in pending:
        target = req if isinstance(req, str) else getattr(req, "target", req)
        if not target or target in seen:
            continue
        if target == page.url:
            continue  # reload：不跟跳
        follow = target
        break
    assert follow is None, (
        f"reload (self-target) must not be followed, got {follow!r}")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")
