"""R4 stage 7 — regression guard for the PNG encode path.

The R4 stage 7 profiling round refactored the stdlib PNG encoder in
``browser.py`` (``write_png`` / the new ``write_png_bytes``) from a
growing-``bytearray`` per-pixel ``extend`` loop to a pre-sized
buffer + per-row writer.  The refactor is *byte-identical* to the
legacy algorithm (filter-0 rows, pixel-major R G B, the same zlib
call).  This guard freezes that contract: a fixed 800×600 random
bitmap's ``write_png`` / ``write_png_bytes`` output must equal the
reference PNG produced by the legacy algorithm.  Any future change to
the encode path that alters the output bytes fails here.

Kept intentionally offline (no network, no PIL, no disk beyond a
single in-memory encode) so it can ride in the 936-test suite.
"""
import os
import random
import struct
import sys
import zlib

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

from toy_browser.app import browser
from toy_browser.rendering import paint


def _legacy_png_bytes(bmp: "paint.Bitmap") -> bytes:
    """The pre-R4-stage-7 PNG encoder (filter-0 rows, pixel-major
    RGB, zlib), kept here as the byte-reference.  This is the exact
    loop the legacy ``browser.write_png`` used to run."""
    w, h = bmp.width, bmp.height
    raw_rows = bytearray()
    for py in range(h):
        raw_rows.append(0)  # filter type: none
        row = bmp.pixels[py * w:(py + 1) * w]
        for px in row:
            raw_rows.extend(((px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF))

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows))
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _fixed_bitmap() -> "paint.Bitmap":
    """800×600 random-24-bit bitmap, seeded so the reference bytes are
    stable across runs and machines (zlib is deterministic for a
    given payload + level)."""
    bmp = paint.Bitmap(800, 600)
    rnd = random.Random(0x5EED)
    for i in range(len(bmp.pixels)):
        bmp.pixels[i] = rnd.getrandbits(24)
    return bmp


def test_png_encode_byte_identity() -> None:
    """R4 stage 7 — the refactored PNG encoder must produce bytes
    identical to the legacy algorithm on a fixed random bitmap."""
    bmp = _fixed_bitmap()
    expected = _legacy_png_bytes(bmp)
    actual = browser.write_png_bytes(bmp)
    assert actual == expected, (
        "write_png_bytes diverged from the legacy encoder: "
        f"len {len(actual)} vs {len(expected)}")
    assert actual[:8] == b"\x89PNG\r\n\x1a\n", "PNG signature missing"


def test_png_file_write_matches_bytes() -> None:
    """R4 stage 7 — ``write_png`` (file) must emit the same bytes as
    ``write_png_bytes`` (memory) for the same bitmap."""
    import tempfile

    bmp = _fixed_bitmap()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        browser.write_png(bmp, path)
        with open(path, "rb") as fh:
            file_bytes = fh.read()
        assert file_bytes == browser.write_png_bytes(bmp), (
            "write_png file output differs from write_png_bytes")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_png_encode_handles_edge_sized_bitmaps() -> None:
    """R4 stage 7 — 1×1 and zero-height bitmaps must still encode to
    valid PNG bytes (the encoder's pre-sized buffer + per-row writer
    must not break on degenerate sizes)."""
    for w, h in ((1, 1), (4, 0), (0, 3)):
        bmp = paint.Bitmap(w, h)
        out = browser.write_png_bytes(bmp)
        assert out[:8] == b"\x89PNG\r\n\x1a\n", (
            f"{w}x{h} bitmap: PNG signature missing")
        # A 0-height / 0-width frame encodes to 0 raw rows: the
        # legacy loop would have appended no bytes, so the new
        # pre-sized buffer (length 0) must match it exactly.
        assert out == _legacy_png_bytes(bmp), (
            f"{w}x{h} bitmap: byte divergence from legacy encoder")


TESTS = [
    ("test_png_encode_byte_identity", test_png_encode_byte_identity),
    ("test_png_file_write_matches_bytes", test_png_file_write_matches_bytes),
    ("test_png_encode_handles_edge_sized_bitmaps", test_png_encode_handles_edge_sized_bitmaps),
]
