"""R7-S04 — pre-allocation network resource caps (bounded reads).

Round-7 security audit S04 (网络脚本与安全审计.md §3): the response
cap was enforced *after* the body was fully allocated —
``resp.read()`` (no argument) buffered everything before the size
check fired (probe ``read_before_cap``), ``gzip.decompress`` expanded
the whole bomb before comparing (probe ``response_limits_and_cookie_ingest``
first half), and the POST path had no cap at all (second half).

These tests pin the fixed contract:

* the shared chunked reader asks for a bounded amount on every
  ``read(amt)`` call and never pulls more than ``cap + 1`` bytes from
  the wire before raising :class:`ResponseTooLarge`;
* gzip decoding is incremental and bounded (cap fires mid-decode);
* a 128-byte instance cap rejects a 129-byte raw body, a gzip body
  that decodes past the cap, and a 129-byte POST body — on a real
  loopback server;
* an over-limit connection is closed and never returned to the
  keep-alive pool, and the client keeps working afterwards.

Only a few-KiB loopback payload is used — no memory-exhaustion
attempt (per the audit's acceptance note).
"""
import gzip
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.network import net
from toy_browser.network.net import (
    HttpClient,
    ResponseTooLarge,
    _read_response_bounded,
    fetch_resource,
    gunzip_bounded,
    maybe_gunzip,
    parse_url,
)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# fake reader — proves every read() argument is bounded
# ---------------------------------------------------------------------------

class _FakeResp:
    """Duck-type for http.client.HTTPResponse.read() that serves the
    body in fixed-size pieces (a slow server) and records every
    ``read(amt)`` argument and the bytes handed back."""

    def __init__(self, data: bytes, piece: int = 64):
        self._data = data
        self._pos = 0
        self._piece = piece
        self.amts: list = []       # every amt argument seen
        self.handed: list = []     # bytes returned per call

    def read(self, amt=None):
        self.amts.append(amt)
        if amt is None:
            # The S04 bug: an unbounded read must never happen.
            chunk = self._data[self._pos:]
        else:
            end = min(self._pos + min(amt, self._piece), len(self._data))
            chunk = self._data[self._pos:end]
        self._pos += len(chunk)
        self.handed.append(len(chunk))
        return chunk


@_reg
def test_fake_reader_every_read_argument_is_bounded():
    """A 4096-byte body under a 128-byte cap: no read() may ask for an
    unbounded amount, and the reader must stop after pulling at most
    cap+1 bytes from the wire."""
    fake = _FakeResp(b"A" * 4096, piece=64)
    try:
        _read_response_bounded(fake, 128)
        raise AssertionError("4096-byte body must be rejected under a "
                             "128-byte cap")
    except ResponseTooLarge:
        pass
    assert all(isinstance(a, int) and a >= 1 for a in fake.amts), \
        f"read() must always pass a bounded positive amt: {fake.amts}"
    assert all(a <= 129 for a in fake.amts), \
        f"no read() may ask for more than cap+1 bytes: {fake.amts}"
    assert sum(fake.handed) == 129, \
        f"reader must stop at cap+1 bytes, handed {sum(fake.handed)}"
    assert len(fake.amts) >= 2, \
        f"reading must be chunked, got {len(fake.amts)} call(s)"


@_reg
def test_fake_reader_within_cap_returns_full_body():
    fake = _FakeResp(b"B" * 128, piece=64)
    out = _read_response_bounded(fake, 128)
    assert out == b"B" * 128, f"body at exactly the cap must pass: {len(out)}"
    assert all(a <= 129 for a in fake.amts), fake.amts


@_reg
def test_fake_reader_empty_body_ok():
    fake = _FakeResp(b"", piece=64)
    assert _read_response_bounded(fake, 128) == b""


@_reg
def test_fake_reader_zero_cap_rejects_any_byte():
    fake = _FakeResp(b"x", piece=64)
    try:
        _read_response_bounded(fake, 0)
        raise AssertionError("a 1-byte body must be rejected under cap 0")
    except ResponseTooLarge:
        pass
    assert sum(fake.handed) <= 1, fake.handed


# ---------------------------------------------------------------------------
# bounded incremental gzip
# ---------------------------------------------------------------------------

@_reg
def test_gunzip_over_cap_rejected():
    """The S04 decompression-bomb probe: a small gzip that expands
    past the cap is rejected *during* decode."""
    bomb = gzip.compress(b"C" * 4096)
    assert len(bomb) < 128, "the compressed bomb must fit under the cap"
    try:
        gunzip_bounded(bomb, 128)
        raise AssertionError("gzip expanding to 4096 bytes must be "
                             "rejected under a 128-byte cap")
    except ResponseTooLarge:
        pass


@_reg
def test_gunzip_exact_cap_ok():
    body = b"D" * 128
    out = gunzip_bounded(gzip.compress(body), 128)
    assert out == body, f"decoded output at exactly the cap must pass"


@_reg
def test_gunzip_within_cap_ok():
    body = b"hello bounded world\n" * 3
    out = gunzip_bounded(gzip.compress(body), 128)
    assert out == body


@_reg
def test_gunzip_boundary_one_over_cap_rejected():
    body = b"E" * 129
    try:
        gunzip_bounded(gzip.compress(body), 128)
        raise AssertionError("129 decoded bytes must be rejected")
    except ResponseTooLarge:
        pass


@_reg
def test_gunzip_bad_data_lenient():
    """P22.2 contract: undecodable input returns the raw body."""
    raw = b"this is not gzip at all"
    assert gunzip_bounded(raw, 128) is raw
    assert gunzip_bounded(b"", 128) == b""


@_reg
def test_gunzip_truncated_stream_lenient():
    gz = gzip.compress(b"F" * 512)
    truncated = gz[:-4]
    assert gunzip_bounded(truncated, 4096) == truncated, \
        "a truncated member must return the raw body (lenient P22.2)"


@_reg
def test_gunzip_concatenated_members():
    """gzip.decompress accepts concatenated members; the bounded
    decoder must match it."""
    part1, part2 = b"G" * 300, b"H" * 300
    both = gzip.compress(part1) + gzip.compress(part2)
    out = gunzip_bounded(both, 4096)
    assert out == gzip.decompress(both) == part1 + part2


@_reg
def test_gunzip_matches_stdlib_large_payload():
    payload = bytes((i * 7 + 13) % 251 for i in range(300 * 1024))
    gz = gzip.compress(payload)
    out = gunzip_bounded(gz, 1024 * 1024)
    assert out == payload == gzip.decompress(gz)


@_reg
def test_maybe_gunzip_header_gate_and_instance_cap():
    resp = net._TransientResp(
        status=200, status_text="OK",
        headers={"content-encoding": "gzip"},
        body=gzip.compress(b"I" * 4096), final_url="http://x/")
    # No cap argument → module default (large) → decodes fine.
    assert len(maybe_gunzip(resp)) == 4096
    # Instance cap threaded in → rejected.
    try:
        maybe_gunzip(resp, 128)
        raise AssertionError("maybe_gunzip must honor the instance cap")
    except ResponseTooLarge:
        pass
    # No content-encoding → body untouched.
    plain = net._TransientResp(
        status=200, status_text="OK",
        headers={"content-type": "text/plain"},
        body=b"raw", final_url="http://x/")
    assert maybe_gunzip(plain, 8) == b"raw"


# ---------------------------------------------------------------------------
# loopback server — the audit's real-client probes
# ---------------------------------------------------------------------------

def _start_server(routes):
    """Minimal http.server (GET + POST) on a random port.

    ``routes`` maps (method, path) → (status, ctype, body, extra_hdrs).
    Returns (server, port); the caller must shut it down.
    """
    import http.server
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            key = (self.command, self.path.split("?")[0])
            # A real server drains the request body before responding;
            # leaving it unread makes the server's close() emit an RST
            # that races with the client's abort on Windows loopback.
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
def test_loopback_raw_129_rejected():
    """S04 probe read_before_cap: 129 raw bytes under a 128-byte cap."""
    srv, port = _start_server({
        ("GET", "/big"): (200, "text/plain", b"J" * 129, {}),
        ("GET", "/ok"): (200, "text/plain", b"J" * 128, {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=128)
        try:
            c.get(f"http://127.0.0.1:{port}/big")
            raise AssertionError("129-byte body must be rejected")
        except ResponseTooLarge:
            pass
        # The cap is exactly 128 → the boundary body still works.
        resp = c.get(f"http://127.0.0.1:{port}/ok")
        assert resp.body == b"J" * 128, len(resp.body)
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_loopback_gzip_decoded_over_cap_rejected():
    """S04 probe response_limits_and_cookie_ingest (first half): a
    small gzip that decodes to 4096 bytes must be rejected under a
    128-byte instance cap (the legacy path returned it happily)."""
    gz = gzip.compress(b"K" * 4096)
    srv, port = _start_server({
        ("GET", "/bomb"): (200, "text/plain", gz,
                           {"Content-Encoding": "gzip"}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=128)
        try:
            c.get(f"http://127.0.0.1:{port}/bomb")
            raise AssertionError("gzip bomb must be rejected under the "
                                 "instance cap")
        except ResponseTooLarge:
            pass
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_loopback_post_over_cap_rejected():
    """S04 probe response_limits_and_cookie_ingest (second half):
    the POST path had no cap at all — a 4096-byte POST response came
    back under a 128-byte cap."""
    srv, port = _start_server({
        ("POST", "/submit"): (200, "text/plain", b"L" * 4096, {}),
        ("POST", "/small"): (200, "text/plain", b"L" * 128, {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=128)
        try:
            c.post(f"http://127.0.0.1:{port}/submit", data={"a": "b"})
            raise AssertionError("4096-byte POST body must be rejected")
        except ResponseTooLarge:
            pass
        resp = c.post(f"http://127.0.0.1:{port}/small", data={"a": "b"})
        assert resp.body == b"L" * 128, len(resp.body)
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_over_limit_connection_not_pooled_and_client_recovers():
    """After an over-limit response the connection must be closed and
    NOT returned to the keep-alive pool; the client must keep
    serving later requests (state converges)."""
    srv, port = _start_server({
        ("GET", "/big"): (200, "text/plain", b"M" * 129, {}),
        ("GET", "/after"): (200, "text/plain", b"ok", {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=128)
        try:
            c.get(f"http://127.0.0.1:{port}/big")
        except ResponseTooLarge:
            pass
        assert len(c._conn_pool) == 0, \
            f"over-limit connection must not be pooled: {c._conn_pool}"
        resp = c.get(f"http://127.0.0.1:{port}/after")
        assert resp.status == 200 and resp.body == b"ok", resp
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_loopback_post_129_rejected_128_ok():
    """S04 验收原文 — "128 字节限额拒绝 129 字节 POST"：129-byte
    POST 响应在 128-byte 实例上限下被拒，128-byte 边界放行。

    （既有 ``test_loopback_post_over_cap_rejected`` 用的是 4096 字节；
    本测试锁定验收条款点名的 129/128 边界。）"""
    srv, port = _start_server({
        ("POST", "/big"): (200, "text/plain", b"P" * 129, {}),
        ("POST", "/ok"): (200, "text/plain", b"P" * 128, {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=128)
        try:
            c.post(f"http://127.0.0.1:{port}/big", data={"a": "b"})
            raise AssertionError("129-byte POST body must be rejected")
        except ResponseTooLarge:
            pass
        resp = c.post(f"http://127.0.0.1:{port}/ok", data={"a": "b"})
        assert resp.body == b"P" * 128, len(resp.body)
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_post_released_back_to_pool_after_success():
    """R7-S04 第 3 点 — "取消后连接与任务计数收敛"：成功 POST 的
    keep-alive 连接必须**归回**可复用池（与 GET 同口径），而
    超限 / 取消路径的连接不进池。"""
    srv, port = _start_server({
        ("POST", "/ok"): (200, "text/plain", b"ok", {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=1024)
        key = c._pool_key(parse_url(f"http://127.0.0.1:{port}/ok"))
        resp = c.post(f"http://127.0.0.1:{port}/ok", data={"a": "b"})
        assert resp.status == 200, resp.status
        assert key in c._conn_pool, \
            ("successful keep-alive POST must be returned to the pool "
             f"(pool keys: {list(c._conn_pool)})")
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_post_cancelled_connection_removed_from_pool():
    """R7-S04 第 3 点 — "取消后连接与任务计数收敛"：取消的 POST
    连接被**剔除**出池（``except RequestCancelled`` 分支），且
    取消是**粘性终态**——后续请求在门禁处直接拒绝，不发包。"""
    from toy_browser.network.request_budget import RequestCancelled
    srv, port = _start_server({
        ("POST", "/ok"): (200, "text/plain", b"ok", {}),
        ("GET", "/after"): (200, "text/plain", b"ok", {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=1024)
        key = c._pool_key(parse_url(f"http://127.0.0.1:{port}/ok"))
        # 1) 未取消：POST 成功，连接归池。
        resp = c.post(f"http://127.0.0.1:{port}/ok", data={"a": "b"})
        assert resp.status == 200 and key in c._conn_pool
        # 2) 取消：后续 GET 在门禁处被拒（不发包）；连接池收敛。
        c.cancel("user aborted")
        try:
            c.get(f"http://127.0.0.1:{port}/after")
            raise AssertionError("cancelled client must not send")
        except RequestCancelled:
            pass
        # 3) 粘性终态：再次 get 仍然直接拒绝（不发包、不触碰连接）。
        try:
            c.get(f"http://127.0.0.1:{port}/after")
            raise AssertionError("cancelled client is sticky")
        except RequestCancelled:
            pass
        assert key not in c._conn_pool, \
            ("cancelled client must not leave reusable connections "
             f"(pool keys: {list(c._conn_pool)})")
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_fetch_resource_isolates_over_limit_failure():
    """A subresource whose body exceeds the cap must fail as None
    (like any other fetch failure) — the page load continues."""
    srv, port = _start_server({
        ("GET", "/big"): (200, "text/plain", b"N" * 129, {}),
    })
    try:
        c = HttpClient(timeout=5.0, max_response_bytes=128)
        resp = fetch_resource(f"http://127.0.0.1:{port}/big", client=c)
        assert resp is None, "over-limit subresource must map to None"
        c.close_pool()
    finally:
        srv.shutdown()


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
