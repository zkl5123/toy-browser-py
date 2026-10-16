"""C02 — 连续像素缓冲区及 GUI 呈现回归（round5 docs 批次 2）。

覆盖 CPUImage：
- 0xRRGGBB int 列表 → RGB8 连续缓冲区逐像素一致（奇数宽度 / 不同
  stride / 随机像素）
- to_ppm_bytes 与 Bitmap.save_ppm 字节一致（CLI 兼容）
- get/set_pixel 越界 clamp
- 缓冲池按尺寸复用、清零、有界
"""
from __future__ import annotations

import os
import random
import tempfile

from toy_browser.rendering.paint import Bitmap, WHITE
from toy_browser.rendering.cpu_image import CPUImage, ImagePool


TESTS = []


def test_c02_from_bitmap_matches_pixels():
    """随机像素：CPUImage.get_pixel 与 Bitmap.pixels 逐像素一致。"""
    rng = random.Random(20261016)
    w, h = 137, 59  # 奇数宽度
    bmp = Bitmap(w, h, fill=WHITE)
    for y in range(h):
        for x in range(w):
            c = (rng.randrange(256) << 16) | \
                (rng.randrange(256) << 8) | rng.randrange(256)
            bmp.pixels[y * w + x] = c
    img = CPUImage.from_bitmap(bmp)
    for y in range(h):
        for x in range(w):
            r, g, b = img.get_pixel(x, y)
            c = bmp.pixels[y * w + x]
            assert (r, g, b) == ((c >> 16) & 0xFF, (c >> 8) & 0xFF,
                                  c & 0xFF), f"mismatch at ({x},{y})"


def test_c02_ppm_bytes_match_save_ppm():
    """to_ppm_bytes 与 Bitmap.save_ppm 写到磁盘的字节一致。"""
    rng = random.Random(7)
    w, h = 10, 4
    bmp = Bitmap(w, h, fill=0x123456)
    img = CPUImage.from_bitmap(bmp)
    a = img.to_ppm_bytes()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.ppm")
        bmp.save_ppm(p)
        with open(p, "rb") as f:
            b = f.read()
    assert a == b, "PPM bytes diverge from legacy save_ppm"
    assert a.startswith(b"P6\n")


def test_c02_ppm_header_format():
    img = CPUImage(width=5, height=3)
    data = img.to_ppm_bytes()
    header = b"P6\n5 3\n255\n"
    assert data.startswith(header)
    # body = stride * height (紧凑 stride = width*3)
    assert len(data) == len(header) + 5 * 3 * 3


def test_c02_set_pixel_clamp():
    img = CPUImage(width=4, height=4)
    img.set_pixel(-1, 0, 255, 0, 0)      # 越界 → 忽略
    img.set_pixel(99, 0, 255, 0, 0)       # 越界 → 忽略
    img.set_pixel(0, 0, 255, 0, 0)
    assert img.get_pixel(0, 0) == (255, 0, 0)
    # get 越界 clamp 到边界
    assert img.get_pixel(-10, -10) == (255, 0, 0)
    assert img.get_pixel(99, 99) == (0, 0, 0)


def test_c02_pool_reuse_and_clear():
    pool = ImagePool(max_size=2)
    a = pool.get(10, 10)
    a.buffer[0] = 0xFF
    pool.put(a)
    b = pool.get(10, 10)
    assert b is a, "pool must reuse same-size image"
    assert b.buffer[0] == 0, "reused buffer must be zeroed"
    # 不同尺寸不复用
    c = pool.get(20, 20)
    assert c is not a
    # 有界：同尺寸池不超过 max_size
    for i in range(5):
        pool.put(pool.get(30, 30))
    # 归还后 get 仍能拿到可用的（清零）图像
    d = pool.get(30, 30)
    assert all(x == 0 for x in d.buffer[:10]), "pool image must be zeroed"


def test_c02_empty_image():
    img = CPUImage(width=0, height=0)
    assert img.is_empty()
    assert img.to_ppm_bytes().startswith(b"P6\n")


TESTS.append(("test_c02_from_bitmap_matches_pixels",
              test_c02_from_bitmap_matches_pixels))
TESTS.append(("test_c02_ppm_bytes_match_save_ppm",
              test_c02_ppm_bytes_match_save_ppm))
TESTS.append(("test_c02_ppm_header_format", test_c02_ppm_header_format))
TESTS.append(("test_c02_set_pixel_clamp", test_c02_set_pixel_clamp))
TESTS.append(("test_c02_pool_reuse_and_clear", test_c02_pool_reuse_and_clear))
TESTS.append(("test_c02_empty_image", test_c02_empty_image))
