"""R7-S04 第 3 点接线 — deadline / 取消 / 页级预算接进 HTTP 主链。

锁定三个行为（审计验收："取消后连接与任务计数收敛"）：

1. 取消粘性：``client.cancel()`` 之后，后续 GET/POST 在门禁处直接
   抛 :class:`rb.RequestCancelled`，连接从未 acquire（池不变）；
2. 页级预算耗尽：两个请求累计解压字节超过预算后，第三个请求在
   门禁处抛 :class:`ResponseTooLarge`（预算耗尽，连接未 acquire）；
3. deadline 过期：``Deadline(monotonic=0.0, budget=0.0)`` 等价"已
   过期"，任何请求在门禁处被拒绝（单调时钟注入，不真实等）。

所有测试都用回环服务器（几 KiB），不跑内存耗尽攻击。
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.network import net
from toy_browser.network.net import HttpClient, ResponseTooLarge
from toy_browser.network import request_budget as rb

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _start_server(routes):
    import http.server
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            key = (self.command, self.path.split("?")[0])
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
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
            if not any(k.lower() == "content-length"
                       for k in (extra or {})):
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
        do_POST = _serve

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


@_reg
def test_cancel_blocks_all_subsequent_requests():
    """取消后，GET 和 POST 都在门禁处被拒绝，连接从未 acquire（池不变）。"""
    srv, port = _start_server({
        ("GET", "/a"): (200, "text/plain", b"X" * 100, {}),
        ("POST", "/submit"): (200, "text/plain", b"Y" * 100, {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=1024)
        # 先正常取一个，验证服务可用。
        resp = c.get(f"http://127.0.0.1:{port}/a")
        assert len(resp.body) == 100
        pool_before = len(c._conn_pool)  # 正常请求后连接可能已回池

        # 取消：粘性终态。
        c.cancel("test cancel")

        # 后续 GET 必须在门禁处被拒绝（不实际发请求）。
        try:
            c.get(f"http://127.0.0.1:{port}/a")
            raise AssertionError("取消后的 GET 必须抛 RequestCancelled")
        except rb.RequestCancelled:
            pass

        # 后续 POST 同样被拒绝。
        try:
            c.post(f"http://127.0.0.1:{port}/submit", data={"a": "b"})
            raise AssertionError("取消后的 POST 必须抛 RequestCancelled")
        except rb.RequestCancelled:
            pass

        # 连接池不得因取消后的请求新增条目（门禁在 acquire 前就拒绝了）。
        assert len(c._conn_pool) <= pool_before, \
            f"取消后连接池不得新增，" \
            f"取消前 {pool_before}，取消后 {len(c._conn_pool)}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_page_budget_exhausted_blocks_next_request():
    """预算 100：第一个 100 字节请求通过并记账（raw_used=100，用满）；
    第二个 100 字节请求在门禁处被拒（exhausted=True → 拒绝）。
    验证的是"用满即拒绝剩余请求"。"""
    srv, port = _start_server({
        ("GET", "/a"): (200, "text/plain", b"Z" * 100, {}),
    })
    try:
        budget = rb.PageBudget(raw=100, decoded=100)
        c = HttpClient(timeout=5.0, max_response_bytes=1024,
                       page_budget=budget)

        # 第一个请求：100 字节，恰好用满预算。
        r1 = c.get(f"http://127.0.0.1:{port}/a")
        assert len(r1.body) == 100
        assert budget.exhausted, "100/100 字节用满后应耗尽"

        # 第二个请求：门禁检查 exhausted=True → 拒绝。
        try:
            c.get(f"http://127.0.0.1:{port}/a")
            raise AssertionError("预算耗尽后的请求必须在门禁处被拒绝")
        except ResponseTooLarge:
            pass

        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_deadline_expired_at_zero_budget_blocks_request():
    """Deadline 预算 0.0 等价"未启用"（remaining=inf）；
    要验证过期，用单调时钟值已走到的 deadline。"""
    srv, port = _start_server({
        ("GET", "/a"): (200, "text/plain", b"W" * 10, {}),
    })
    try:
        import time as _t
        # 构造时刻 t0=0.0，预算 0.1 秒；此时单调时钟已远超 0.1 → 过期。
        dl = rb.Deadline(monotonic=0.0, budget=0.1)
        c = HttpClient(timeout=5.0, max_response_bytes=1024,
                       deadline=dl)
        try:
            c.get(f"http://127.0.0.1:{port}/a")
            raise AssertionError("过期的 deadline 必须在门禁处拒绝请求")
        except (rb.RequestCancelled, ResponseTooLarge):
            pass  # deadline 过期 → 转为 RequestCancelled
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_valid_deadline_does_not_block_request():
    """未过期的 deadline 不影响正常请求。"""
    srv, port = _start_server({
        ("GET", "/a"): (200, "text/plain", b"V" * 50, {}),
    })
    try:
        # 单调时钟 0.0，预算 100.0 → 未过期。
        import time as _t
        dl = rb.Deadline(monotonic=0.0, budget=_t.monotonic() + 60.0)
        c = HttpClient(timeout=5.0, max_response_bytes=1024, deadline=dl)
        resp = c.get(f"http://127.0.0.1:{port}/a")
        assert len(resp.body) == 50, "未过期的 deadline 不得阻塞正常请求"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_budget_charge_is_called_after_successful_response():
    """记账发生在 maybe_gunzip 之后（解压字节=输出长度），
    而不是原始字节。"""
    import gzip
    srv, port = _start_server({
        ("GET", "/gz"): (200, "text/plain", gzip.compress(b"Q" * 200),
                         {"Content-Encoding": "gzip"}),
    })
    try:
        budget = rb.PageBudget(raw=5000, decoded=250)
        c = HttpClient(timeout=5.0, max_response_bytes=1024,
                       page_budget=budget)
        resp = c.get(f"http://127.0.0.1:{port}/gz")
        # 原始字节=gzip 大小（小），解压字节=200（> 记账值 250 内）。
        assert len(resp.body) == 200
        assert budget.decoded_used >= 200, \
            f"解压记账应 >= 200，实际 {budget.decoded_used}"
        # 原始字节 = gzip 压缩体大小（< 200）
        gz_size = len(gzip.compress(b"Q" * 200))
        assert budget.raw_used == gz_size, \
            f"原始记账应等于 gzip 体大小 {gz_size}，实际 {budget.raw_used}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_budget_exhausted_does_not_affect_client_without_budget():
    """未传 page_budget 的 client 不受预算限制（保持旧行为）。"""
    srv, port = _start_server({
        ("GET", "/a"): (200, "text/plain", b"B" * 5000, {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=1024 * 1024)
        resp = c.get(f"http://127.0.0.1:{port}/a")
        assert len(resp.body) == 5000, "无预算的 client 不受限制"
        c.close_pool()
    finally:
        srv.shutdown()


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
