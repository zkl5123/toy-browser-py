"""Network: HTTP, cache, TLS, cookies, CORS/CSP — test module (GPT P1-2 split from test.py)."""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


from toy_browser.app import browser
from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout
from toy_browser.scripting import js
from toy_browser.typography import text
from toy_browser.rendering import paint
from toy_browser.rendering.paint import Bitmap, Color
import sys

# Shared helpers (defined in test.py, needed by multiple modules)
from test_helpers import (
    _a11y_roles,
    _find_a11y_role,
    _find_box,
    _find_box_by_id,
    _find_box_by_id_or_tag,
    _find_box_by_tag,
    _greens,
    _p20_bitmap,
    _p20_first_valid_outline,
    _p20_ink,
    _p20_metrics,
    _p21_start_server,
    _p22_start_server,
    _p29_js_logs,
    _p30_doc_html,
    _p30_host,
    _p30_qsel_all,
    _p32_cleanup,
    _p32_tmp,
    _pixel_diff,
    _reds,
    _rmtree_quiet,
    find2_children,
)

TESTS: list = []

def test_fetch_sandbox():
    root = dom.parse("<html><body></body></html>")
    logs: list[str] = []
    host = js.Host(root, logs.append)
    host.execute("fetch('file:///etc/passwd');")
    assert any("blocked" in l for l in logs), f"file:// fetch must be blocked, logs={logs}"
    host.execute("fetch('http://example.com');")
    blocked_http = any("blocked" in l and "http://example.com" in l for l in logs)
    assert not blocked_http, "http:// fetch must not be blocked"



TESTS.append(("test_fetch_sandbox", test_fetch_sandbox))
def test_fetch_sandbox():
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute("fetch('file:///etc/passwd');")
    assert any("blocked" in l for l in logs), "file:// fetch must be logged as blocked"

    host.execute("fetch('http://example.com');")
    blocked_http = any("blocked" in l and "http://example.com" in l for l in logs)
    assert not blocked_http, "http:// fetch must not be blocked"



TESTS.append(("test_fetch_sandbox", test_fetch_sandbox))
def test_embedded_img_base64():
    """P3.1 — base64 data URI in <img src> renders decoded pixels.

    P33.R1 — the layout box reports x=0 y=0, but the img is rendered
    at x=8 due to body margin (P19).  Check a pixel in the center of
    the rendered region (x=20, y=20) which is well inside the 40x40
    image and not affected by AA edge effects.
    """
    import base64

    raw = bytes([255, 0, 0] * 16)  # 4x4 red square
    b64 = base64.b64encode(raw).decode()
    data_uri = f"data:image/octet-stream;base64,{b64}"
    html = f'<html><body><img id="pic" src="{data_uri}" width="40" height="40"></body></html>'
    bmp = browser.render_document(html, "", 200, 200)
    # P19 — default CSS adds body { margin: 8px }, shifting the img to
    # x=8..48, y=8..48.  Check a pixel well inside the image region.
    px = bmp.pixels[20 * 200 + 20]
    r, g, b = (px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF
    assert r > 200 and g < 55 and b < 55, \
        f"embedded img must render red, got ({r},{g},{b})"



TESTS.append(("test_embedded_img_base64", test_embedded_img_base64))
def test_embedded_img_intrinsic_size():
    """P3.1 — img with data URI but no width/height uses intrinsic pixel size."""
    import base64

    raw = bytes([0, 0, 255] * 9)  # 3x3 blue
    b64 = base64.b64encode(raw).decode()
    data_uri = f"data:image/octet-stream;base64,{b64}"
    html = f'<html><body><img src="{data_uri}"></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "")
    lroot = layout.layout(styled, 400.0, 300.0)
    img_box = _find_box_by_tag(lroot, "img")
    assert img_box is not None, "img box must exist"
    assert img_box.img_pixels is not None, "img_pixels must be decoded"
    assert img_box.img_pixel_w == 3 and img_box.img_pixel_h == 3, "intrinsic size must be 3x3"
    assert abs(img_box.rect.w - 3.0) < 1.0, f"img width must be ~3, got {img_box.rect.w}"



TESTS.append(("test_embedded_img_intrinsic_size", test_embedded_img_intrinsic_size))
def test_embedded_img_png_data_uri():
    """P3.1 — PNG data URI is decoded via the minimal PNG reader."""
    import base64, struct, zlib

    # Build a 1x1 red PNG in memory.
    w, h = 1, 1
    raw_row = b"\x00" + bytes([255, 0, 0])  # filter 0 + RGB
    raw_rows = raw_row * h
    idat = zlib.compress(raw_rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    b64 = base64.b64encode(png).decode()
    data_uri = f"data:image/png;base64,{b64}"

    html = f'<html><body><img id="p" src="{data_uri}" width="20" height="20"></body></html>'
    bmp = browser.render_document(html, "", 100, 100)
    px = bmp.pixels[10 * 100 + 10]
    r, g, b = (px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF
    assert r > 200 and g < 55 and b < 55, f"PNG data URI must render red, got ({r},{g},{b})"



TESTS.append(("test_embedded_img_png_data_uri", test_embedded_img_png_data_uri))
