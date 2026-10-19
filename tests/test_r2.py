
# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

# P33.R2 — network subresource concurrency + GUI full-content scroll tests.
# ---------------------------------------------------------------------------
# R2 layer: keep-alive connection pool, parallel subresource loader
# (bounded thread pool), and the GUI's full-document-height rendering
# with scroll-offset clipping.  All tests are self-contained: they use
# a local ``http.server`` on an ephemeral port, or pure DOM/CSS fixtures,
# so they run without external network access.

import os as _os
from skip_support import require_tk


def test_r2_http_client_pool_exists():
    """HttpClient must expose the keep-alive pool internals."""
    from toy_browser.network import net
    c = net.HttpClient(timeout=2.0)
    assert hasattr(c, "_conn_pool"), "pool dict missing"
    assert c._conn_pool == {}, "fresh client must have an empty pool"
    assert hasattr(c, "close_pool")
    c.close_pool()


def test_r2_pool_key_shape():
    """The pool key is (scheme, host, port, verify_tls) so two
    clients with different verify modes do not share sockets."""
    from toy_browser.network import net
    c = net.HttpClient(timeout=2.0)
    key = c._pool_key(net.parse_url("http://example.com:8080/x"))
    assert key == ("http", "example.com", 8080, True), key
    key2 = c._pool_key(net.parse_url("https://example.com/x"))
    assert key2 == ("https", "example.com", 443, True), key2


def test_r2_keepalive_reuses_connection():
    """Two GETs to the same origin must share one pooled socket."""
    from toy_browser.network import net
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            b = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = net.HttpClient(timeout=2.0)
        c.get(net.parse_url(f"http://127.0.0.1:{port}/a"))
        after_first = len(c._conn_pool)
        c.get(net.parse_url(f"http://127.0.0.1:{port}/b"))
        after_second = len(c._conn_pool)
        assert after_first >= 1, "the first GET must pool its connection"
        assert after_second == after_first, \
            "a second GET to the same origin must not add a pool entry"
        c.close_pool()
        assert c._conn_pool == {}
    finally:
        srv.shutdown()


def test_r2_release_dead_conn_not_pooled():
    """A connection the server marked ``Connection: close`` must not
    be returned to the pool."""
    from toy_browser.network import net
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            b = b"bye"
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = net.HttpClient(timeout=2.0)
        r = c.get(net.parse_url(f"http://127.0.0.1:{port}/"))
        assert r.status == 200
        assert len(c._conn_pool) == 0, \
            "a Connection: close response must not be pooled"
        c.close_pool()
    finally:
        srv.shutdown()


def test_r2_pool_cap_8():
    """_release_conn drops a new connection when the pool already
    holds 8 (the cap prevents unbounded growth across origins)."""
    from toy_browser.network import net
    c = net.HttpClient(timeout=2.0)

    class FC:
        closed = False

        def close(self):
            self.closed = True

    # Fill the pool to capacity.
    for i in range(8):
        c._conn_pool[("http", f"h{i}", 80, True)] = FC()
    extra = FC()
    c._release_conn(net.parse_url("http://extra.example/"), extra, dead=False)
    assert len(c._conn_pool) == 8, \
        f"pool must stay at 8, got {len(c._conn_pool)}"
    assert extra.closed is True, "the overflow connection must be closed"


def test_r2_load_subresources_parallel_data_uri():
    """data: URIs are recorded inline without touching the network;
    the parallel path produces the same kind→url mapping as the
    sequential path."""
    from toy_browser.network import net
    from toy_browser.document import dom
    html = ('<html><head>'
            '<link rel="stylesheet" href="data:text/css,body{}"/>'
            '<img src="data:image/png;base64,xx"/>'
            '</head><body>x</body></html>')
    tree = dom.parse(html)
    seq = net.load_subresources(tree, "http://x/")
    par = net.load_subresources_parallel(tree, "http://x/", max_workers=4)
    assert set(seq.keys()) == set(par.keys())
    assert set(seq.get("stylesheet", {}).keys()) == \
        set(par.get("stylesheet", {}).keys())
    # data: URIs map to None (no fetch performed).
    for m in (seq, par):
        for kind, urlmap in m.items():
            for u, r in urlmap.items():
                if u.lower().startswith("data:"):
                    assert r is None


def test_r2_parallel_loader_network():
    """A page with 3 real subresources (distinct paths on one
    origin) must load through the thread pool; all three resolve to
    200 responses over the shared keep-alive client."""
    from toy_browser.network import net
    from toy_browser.document import dom
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            b = self.path.encode("ascii")
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        html = ('<html><head>'
                f'<link rel="stylesheet" href="{base}/s.css"/>'
                f'<img src="{base}/a.png"/>'
                f'<img src="{base}/b.png"/>'
                f'<img src="{base}/c.png"/>'
                '</head><body>x</body></html>')
        tree = dom.parse(html)
        c = net.HttpClient(timeout=2.0)
        r = net.load_subresources_parallel(tree, base, client=c,
                                           max_workers=4)
        # 3 images + 1 stylesheet = 4 network fetches, all 200.
        assert len(r.get("image", {})) == 3
        assert len(r.get("stylesheet", {})) == 1
        for kind, urlmap in r.items():
            for u, resp in urlmap.items():
                assert resp is not None, f"{kind} {u} failed"
                assert resp.status == 200
        assert len(c._conn_pool) == 1, \
            "all four resources share one origin → one pooled socket"
        c.close_pool()
    finally:
        srv.shutdown()


def test_r2_parallel_loader_failure_is_none():
    """A subresource that 404s maps to None (the loader never raises
    on a failing resource — P22.4 contract)."""
    from toy_browser.network import net
    from toy_browser.document import dom
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith(".css"):
                b = b"body{}"
                self.send_response(200)
            else:
                b = b""
                self.send_response(404)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        html = ('<html><head>'
                f'<link rel="stylesheet" href="{base}/ok.css"/>'
                f'<img src="{base}/missing.png"/>'
                '</head><body>x</body></html>')
        tree = dom.parse(html)
        r = net.load_subresources_parallel(tree, base, max_workers=4)
        assert r.get("stylesheet", {}).get(f"{base}/ok.css") is not None
        assert r.get("image", {}).get(f"{base}/missing.png") is None
    finally:
        srv.shutdown()


def test_r2_sequential_fast_path_short_list():
    """Two or fewer network specs use the sequential path (thread
    startup would cost more than it saves) — the result is identical
    to the parallel path's mapping shape."""
    from toy_browser.network import net
    from toy_browser.document import dom
    html = ('<html><head>'
            '<img src="data:image/png;base64,xx"/>'
            '</head><body>x</body></html>')
    tree = dom.parse(html)
    r = net.load_subresources_parallel(tree, "http://x/", max_workers=4)
    assert "image" in r


def test_r2_gui_page_scroll_max():
    """PageInstance.scroll_max / scroll_ratio reflect the content
    height vs. the viewport (pure arithmetic, no Tk needed)."""
    require_tk()  # R7-Q01 — gui 模块级 import tkinter；缺 Tk → SKIP
    from toy_browser.app import gui
    p = gui.PageInstance()
    p.width, p.height = 800, 600
    p.content_height = 1000
    assert p.scroll_max() == 400
    p.scroll_to(9999)
    assert p.yoffset == 400, "offset must clamp to scroll_max"
    assert abs(p.scroll_ratio() - 1.0) < 1e-9
    p.scroll_to(0)
    assert p.yoffset == 0 and p.scroll_ratio() == 0.0
    # Non-scrollable page.
    p.content_height = 500
    assert p.scroll_max() == 0 and p.scroll_ratio() == 0.0


def test_r2_gui_clip_bitmap_rows():
    """_clip_bitmap crops the scroll window out of the full-document
    bitmap (row offsets, not a copy of the whole document)."""
    require_tk()  # R7-Q01 — gui 模块级 import tkinter；缺 Tk → SKIP
    from toy_browser.app import gui
    from toy_browser.rendering import paint
    p = gui.PageInstance()
    p.width, p.height = 4, 3
    p.content_height = 6
    full = paint.Bitmap(4, 6)
    # Distinct row colors: row i → all pixels (i << 16).
    for i in range(6):
        for x in range(4):
            full.pixels[i * 4 + x] = (i << 16) | 0xFFFF
    p._full_bitmap = full
    p.scroll_to(2)
    assert p.bitmap.height == 3
    assert p.bitmap.pixels[0] == (2 << 16) | 0xFFFF
    assert p.bitmap.pixels[4] == (3 << 16) | 0xFFFF
    assert p.bitmap.pixels[8] == (4 << 16) | 0xFFFF
    # Clamp to the bottom.
    p.scroll_to(999)
    assert p.yoffset == 3
    assert p.bitmap.pixels[0] == (3 << 16) | 0xFFFF
    assert p.bitmap.pixels[4] == (4 << 16) | 0xFFFF


def test_r2_gui_content_height_probe():
    """_content_height_probe measures the document's full content
    height from the layout tree (independent of the viewport)."""
    require_tk()  # R7-Q01 — gui 模块级 import tkinter；缺 Tk → SKIP
    from toy_browser.app import gui
    from toy_browser.document import dom
    from toy_browser.style import css
    from toy_browser.layout import layout
    html = "<html><body>" + "".join(
        f'<div style="height:50px">{i}</div>' for i in range(20)
    ) + "</body></html>"
    tree = dom.parse(html)
    styled = css.apply(tree, "", viewport_w=800.0, viewport_h=600.0)
    probe = layout.layout(styled, 800.0, 10_000_000.0)
    p = gui.PageInstance()
    ch = p._content_height_probe(probe)
    assert ch >= 20 * 50, f"probe height {ch} < 1000 px expected"
    # A shorter viewport must not shrink the content height.
    probe2 = layout.layout(styled, 800.0, 300.0)
    ch2 = p._content_height_probe(probe2)
    assert ch2 == ch, "content height is viewport-independent"


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_r2_http_client_pool_exists", test_r2_http_client_pool_exists))
TESTS.append(("test_r2_pool_key_shape", test_r2_pool_key_shape))
TESTS.append(("test_r2_keepalive_reuses_connection", test_r2_keepalive_reuses_connection))
TESTS.append(("test_r2_release_dead_conn_not_pooled", test_r2_release_dead_conn_not_pooled))
TESTS.append(("test_r2_pool_cap_8", test_r2_pool_cap_8))
TESTS.append(("test_r2_load_subresources_parallel_data_uri", test_r2_load_subresources_parallel_data_uri))
TESTS.append(("test_r2_parallel_loader_network", test_r2_parallel_loader_network))
TESTS.append(("test_r2_parallel_loader_failure_is_none", test_r2_parallel_loader_failure_is_none))
TESTS.append(("test_r2_sequential_fast_path_short_list", test_r2_sequential_fast_path_short_list))
TESTS.append(("test_r2_gui_page_scroll_max", test_r2_gui_page_scroll_max))
TESTS.append(("test_r2_gui_clip_bitmap_rows", test_r2_gui_clip_bitmap_rows))
TESTS.append(("test_r2_gui_content_height_probe", test_r2_gui_content_height_probe))
