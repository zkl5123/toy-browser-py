"""Shared test helpers (GPT P1-2 split from test.py)."""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


import os
import sys
import io
import shutil as _shutil
import tempfile as _tempfile
import threading
import socket
import ssl
import gzip
import http.server
import json
import struct
import zlib
import time
import urllib.parse

from toy_browser.app import browser
from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout
from toy_browser.scripting import js
from toy_browser.typography import text
from toy_browser.rendering import paint
from toy_browser.network import net
from toy_browser.rendering import paint_engine
from toy_browser.typography import rasterize
from toy_browser.typography import ttf_font
from toy_browser.document import html_pkg
from toy_browser.app import session
from toy_browser.network import cookie_store
from toy_browser.scripting import web_api
from toy_browser.rendering.paint import Bitmap, Color

def _reds(bmp: Bitmap) -> int:
    """Count red-dominant pixels: R channel high, G and B low."""
    return sum(
        1
        for p in bmp.pixels
        if ((p >> 16) & 0xFF) > 200
        and ((p >> 8) & 0xFF) < 55
        and (p & 0xFF) < 55
    )



def _greens(bmp: Bitmap) -> int:
    """P12.4 — count green-dominant pixels: G channel high, R and B low."""
    return sum(
        1
        for p in bmp.pixels
        if ((p >> 8) & 0xFF) > 200
        and ((p >> 16) & 0xFF) < 55
        and (p & 0xFF) < 55
    )



def _pixel_diff(bmp_a: Bitmap, bmp_b: Bitmap) -> int:
    """P12.1 — count pixel positions where the two bitmaps differ."""
    n = min(len(bmp_a.pixels), len(bmp_b.pixels))
    return sum(1 for i in range(n) if bmp_a.pixels[i] != bmp_b.pixels[i])



def _find_box_by_tag(root, tag: str):
    """BFS a layout tree for the first box whose dom tag is `tag`."""
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.is_element() and bx.dom.tag == tag:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None



def _find_box_by_id(root, eid: str):
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.is_element() and bx.dom.attr("id") == eid:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None



def _find_box_by_id_or_tag(root, eid: str, tag: str):
    """Find the first box that matches id OR tag (search depth-first)."""
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.is_element():
            if bx.dom.attr("id") == eid or bx.dom.tag == tag:
                return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None



def _a11y_roles(nodes: list) -> set:
    out: set = set()
    for n in nodes:
        out.add(n["role"])
        if "children" in n:
            out |= _a11y_roles(n["children"])
    return out



def _find_a11y_role(nodes: list, role: str) -> list:
    out: list = []
    for n in nodes:
        if n.get("role") == role:
            out.append(n)
        if "children" in n:
            out.extend(_find_a11y_role(n["children"], role))
    return out



def find2_children(n, eid):
    stack = [n]
    while stack:
        bx = stack.pop()
        if bx.dom and bx.dom.attr("id") == eid:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None


# ---------------------------------------------------------------------------
# P6.1 — position: absolute
# ---------------------------------------------------------------------------


def _p20_bitmap(w=200, h=80):
    from toy_browser.rendering import paint
    return paint.Bitmap(w, h)



def _p20_metrics():
    from toy_browser.typography import font
    # P33.R1 — a prior P17/P18 test may have cleared _real_font_metrics;
    # re-init from the "fonts/" dir so the helper is self-sufficient.
    m = font.get_real_metrics("monospace")
    if m is None:
        font.init_real_fonts(["fonts"])
        m = font.get_real_metrics("monospace")
    return m



def _p20_ink(bmp):
    return sum(1 for p in bmp.pixels if p != 0xFFFFFF)



def _p20_first_valid_outline(m):
    from toy_browser.typography import ttf_font
    for ch in "AEGHLMNORSTUVWX01357":
        o = m.glyph_outline(ch)
        if o is not None and ttf_font.outline_valid(o):
            return o
    return None



def _p21_start_server(routes):
    """Spin up a local http.server on a random port; return
    (server, port) that the caller must shutdown."""
    import http.server, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            key = self.path.split("?")[0]
            r = routes.get(key)
            if r is None:
                body = b"404x"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            status, ctype, body = r
            self.send_response(status)
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port



def _p22_start_server(routes):
    """P22 — http.server with optional extra-headers and gzip/chunked
    support.  ``routes`` maps path → (status, ctype, body, extra_hdrs).
    ``extra_hdrs`` may include ``Content-Encoding: gzip`` (body is
    sent verbatim, caller gzips it) or ``Transfer-Encoding: chunked``
    is NOT honored by BaseHTTPRequestHandler, so chunked is tested via
    ``net.decode_chunked`` on a raw stream instead."""
    import http.server, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            key = self.path.split("?")[0]
            r = routes.get(key)
            if r is None:
                body = b"404x"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if len(r) == 4:
                status, ctype, body, extra = r
            else:
                status, ctype, body = r
                extra = {}
            self.send_response(status)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in extra.items():
                self.send_header(k, v)
            if "Content-Length" not in {k.upper() for k in extra}:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port



def _rmtree_quiet(path):
    import shutil
    try:
        _shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass



def _find_box(bx, tag=None, id_=None):
    """Find the first box matching tag and/or id in a layout tree."""
    stack = [bx]
    while stack:
        n = stack.pop()
        if id_ and n.dom is not None and n.dom.attr("id") == id_:
            return n
        if tag and n.dom is not None and (n.dom.tag or "").lower() == tag:
            if id_ is None:
                return n
        for c in reversed(n.children):
            stack.append(c)
    return None



def _p29_js_logs(code: str) -> list:
    """P29 — run a JS snippet in a minimal DOM and return log lines."""
    from toy_browser.document import dom; from toy_browser.scripting import js
    logs: list = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(code)
    return logs



def _p30_host(html: str):
    """P30 — build a Host bound to *html* with a log sink."""
    from toy_browser.scripting import js
    logs: list = []
    host = js.Host(dom.parse(html), logs.append)
    host.execute_all()
    return host, logs



def _p30_qsel_all(html: str, selector: str, state: dict | None = None):
    """P30.1 — run a full-selector query against *html* and return tags."""
    root = dom.parse(html)
    return [n.tag for n in root.query_selector_all(selector, state)]



def _p30_doc_html():
    return (
        '<html><body>'
        '<div id="d1" class="a b"><p class="c">one</p><p class="c">two</p></div>'
        '<section class="a"><h2 id="h2a">x</h2><h2 id="h2b">y</h2></section>'
        '</body></html>'
    )



def _p32_tmp():
    return _tempfile.mkdtemp(prefix="p32_")



def _p32_cleanup(d):
    _shutil.rmtree(d, ignore_errors=True)



