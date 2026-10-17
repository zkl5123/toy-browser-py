"""批次 40 — C02 活显示 rasterize 切换到 CPUImage 回归。

被测：``toy_browser/rendering/rasterize_cpu.py``
（:class:`CPUCanvas` / :func:`rasterize_to_cpu_image` /
:func:`rasterize_direct` / :func:`cpu_matches_bitmap`）。

设计：纯逻辑核心回归（不强制 tkinter）。验证"CPUCanvas 直接落
buffer 与 Bitmap 逐像素一致 + rasterize_to_cpu_image / rasterize_direct
与权威 paint_engine.rasterize 逐像素一致 + cpu_matches_bitmap 全帧
差分 + 奇数宽度 / 不同 stride / 透明图片 C02 验收"。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering.paint import Bitmap, Color, WHITE
from toy_browser.rendering.paint_engine import (
    DisplayList, DisplayCommand, rasterize as pe_rasterize)
from toy_browser.rendering.cpu_image import CPUImage
from toy_browser.rendering.rasterize_cpu import (
    CPUCanvas, rasterize_to_cpu_image, rasterize_direct,
    cpu_matches_bitmap, cpu_matches_cpu)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _dl_rects():
    """rect + border + text 命令的 DisplayList（冒烟场景）。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=10, y=10, w=50, h=40, color=Color(200, 30, 30)))
    dl.commands.append(DisplayCommand(
        kind="border", x=60, y=8, w=12, h=12, color=Color(0, 120, 200)))
    dl.commands.append(DisplayCommand(
        kind="text", x=14, y=14, w=40, h=12, text="Hi",
        font_px=10, color=Color(0, 0, 0)))
    return dl


# ---------------------------------------------------------------------------
# CPUCanvas — Bitmap 兼容画布（直接落 CPUImage RGB8 buffer）
# ---------------------------------------------------------------------------

@_reg
def test_canvas_fill_rect_matches_bitmap():
    """CPUCanvas.fill_rect 与 Bitmap.fill_rect 逐像素一致。"""
    c = CPUCanvas.of_size(8, 6)
    c.fill_rect(1, 1, 4, 3, Color(200, 30, 30))
    bmp = Bitmap(8, 6, WHITE)
    bmp.fill_rect(1, 1, 4, 3, Color(200, 30, 30))
    assert cpu_matches_bitmap(c.img, bmp) == []


@_reg
def test_canvas_transparent_noop():
    """透明 fill 不画（a==0 直接返回，与 Bitmap 一致）。"""
    c = CPUCanvas.of_size(4, 4)
    c.fill_rect(0, 0, 4, 4, Color(10, 20, 30, 0))
    assert c.pixel_at(0, 0) == (255, 255, 255)


@_reg
def test_canvas_semi_transparent_blend():
    """半透明 fill 走 blend_over（与 Bitmap.fill_rect 同公式）。"""
    c = CPUCanvas.of_size(2, 2)
    c.fill_rect(0, 0, 2, 2, Color(100, 100, 100, 128))
    bmp = Bitmap(2, 2, WHITE)
    bmp.fill_rect(0, 0, 2, 2, Color(100, 100, 100, 128))
    assert cpu_matches_bitmap(c.img, bmp) == []


@_reg
def test_canvas_bounds_clamp():
    """越界 fill / set_pixel 不写（clamp，与 Bitmap 一致）。"""
    c = CPUCanvas.of_size(4, 4)
    c.fill_rect(3, 3, 5, 5, Color(1, 2, 3))
    c.set_pixel(9, 9, Color(9, 9, 9))
    assert c.pixel_at(3, 3) == (1, 2, 3)
    assert c.pixel_at(0, 0) == (255, 255, 255)


@_reg
def test_canvas_to_bitmap_roundtrip():
    """CPUCanvas.to_bitmap 还原 int 列表（兼容现有 CLI）。"""
    c = CPUCanvas.of_size(3, 3)
    c.fill_rect(0, 0, 3, 3, Color(11, 22, 33))
    bmp = c.to_bitmap()
    assert isinstance(bmp, Bitmap)
    assert bmp.pixels[0] == (11 << 16) | (22 << 8) | 33
    assert len(bmp.pixels) == 9


# ---------------------------------------------------------------------------
# rasterize_to_cpu_image — 活显示 rasterize 入口（逐像素一致）
# ---------------------------------------------------------------------------

@_reg
def test_rasterize_to_cpu_image_matches_pe():
    """rasterize_to_cpu_image 与 paint_engine.rasterize 逐像素一致。"""
    dl = _dl_rects()
    img = rasterize_to_cpu_image(dl, 80, 60)
    bmp = pe_rasterize(dl, 80, 60)
    assert cpu_matches_bitmap(img, bmp) == []


@_reg
def test_rasterize_to_cpu_image_returns_cpuimage():
    """rasterize_to_cpu_image 返回 CPUImage（RGB8 连续 buffer）。"""
    dl = _dl_rects()
    img = rasterize_to_cpu_image(dl, 80, 60)
    assert isinstance(img, CPUImage)
    assert img.width == 80 and img.height == 60
    assert img.format == "RGB8"
    assert len(img.buffer) >= img.stride * img.height


# ---------------------------------------------------------------------------
# rasterize_direct — 直接落 buffer 变体（逐像素一致）
# ---------------------------------------------------------------------------

@_reg
def test_rasterize_direct_matches_pe():
    """rasterize_direct 与 paint_engine.rasterize 逐像素一致。"""
    dl = _dl_rects()
    img = rasterize_direct(dl, 80, 60)
    bmp = pe_rasterize(dl, 80, 60)
    assert cpu_matches_bitmap(img, bmp) == []


@_reg
def test_rasterize_direct_vs_to_cpu_image_agree():
    """两条 CPUImage 路径彼此逐像素一致（C02 验收"逐像素一致"）。"""
    dl = _dl_rects()
    a = rasterize_to_cpu_image(dl, 80, 60)
    b = rasterize_direct(dl, 80, 60)
    assert cpu_matches_cpu(a, b) == []


# ---------------------------------------------------------------------------
# cpu_matches_bitmap — 全帧差分（C02 验收）
# ---------------------------------------------------------------------------

@_reg
def test_cpu_matches_detects_mismatch():
    """有差异时 cpu_matches_bitmap 报告 mismatch（非空）。"""
    img = CPUImage(4, 4)
    img.set_pixel(0, 0, 1, 2, 3)
    bmp = Bitmap(4, 4, WHITE)
    assert len(cpu_matches_bitmap(img, bmp)) >= 1


@_reg
def test_cpu_matches_size_mismatch():
    """尺寸不一致 → 报告 size mismatch。"""
    img = CPUImage(4, 4)
    bmp = Bitmap(5, 5, WHITE)
    mm = cpu_matches_bitmap(img, bmp)
    assert mm and mm[0][0] == "size"


@_reg
def test_cpu_matches_tolerant():
    """tolerance 容差内差异不算 mismatch（活显示字体边缘留容差）。"""
    img = CPUCanvas.of_size(2, 2).img
    img.set_pixel(0, 0, 100, 100, 100)
    bmp = Bitmap(2, 2, WHITE)
    bmp.pixels[0] = (105 << 16) | (105 << 8) | 105
    assert cpu_matches_bitmap(img, bmp, tolerance=5) == []
    assert len(cpu_matches_bitmap(img, bmp, tolerance=4)) >= 1


# ---------------------------------------------------------------------------
# C02 验收：奇数宽度 / 不同 stride / 透明图片
# ---------------------------------------------------------------------------

@_reg
def test_odd_width():
    """奇数宽度 CPUImage 与 Bitmap 逐像素一致。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=1, y=1, w=5, h=5, color=Color(9, 9, 9)))
    img = rasterize_to_cpu_image(dl, 7, 5)
    bmp = pe_rasterize(dl, 7, 5)
    assert cpu_matches_bitmap(img, bmp) == []


@_reg
def test_custom_stride():
    """自定义 stride（加 padding）下 fill 仍逐像素一致。"""
    c = CPUCanvas.of_size(4, 4, stride=12)
    c.fill_rect(0, 0, 4, 4, Color(50, 60, 70))
    img = CPUImage(4, 4, stride=12)
    from toy_browser.rendering.cpu_image_fill import fill_rect_rgb8
    fill_rect_rgb8(img, 0, 0, 4, 4, 50, 60, 70)
    assert cpu_matches_bitmap(c.img, img) == []


@_reg
def test_transparent_img_blit():
    """透明图片（RGBA a=0）不污染目标（C02 验收"透明图片逐像素一致"）。"""
    from toy_browser.rendering.cpu_image_fill import blit_rgba8_over_rgb8
    img = CPUCanvas.of_size(4, 4).img
    src = bytearray()
    for _ in range(16):
        src += bytes((255, 0, 0, 0))  # 全透明
    before = bytearray(img.buffer)
    blit_rgba8_over_rgb8(img, src, 4, 4, 16, 0, 0)
    assert bytes(before) == bytes(img.buffer)  # 全透明不改变目标


# ---------------------------------------------------------------------------
# 呈现路径：to_ppm_bytes 供 Tk PhotoImage 直接消费（兼容现有 CLI）
# ---------------------------------------------------------------------------

@_reg
def test_cpuimage_ppm_bytes_header():
    """CPUImage.to_ppm_bytes 输出 P6 头 + 像素体（Tk 可直接消费）。"""
    dl = _dl_rects()
    img = rasterize_to_cpu_image(dl, 80, 60)
    data = img.to_ppm_bytes()
    assert data.startswith(b"P6\n80 60\n255\n")
    assert len(data) == len(b"P6\n80 60\n255\n") + 80 * 60 * 3


@_reg
def test_full_page_rasterize_match():
    """完整小页（多命令）rasterize_to_cpu_image 与 pe.rasterize 一致。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=0, y=0, w=80, h=60, color=Color(240, 240, 240)))
    dl.commands.append(DisplayCommand(
        kind="rounded", x=10, y=10, w=30, h=30, radius=6,
        color=Color(0, 150, 0)))
    dl.commands.append(DisplayCommand(
        kind="shadow", x=10, y=10, w=30, h=30,
        shadow_offset_x=2, shadow_offset_y=2, shadow_radius=3,
        shadow_color=Color(0, 0, 0, 80)))
    dl.commands.append(DisplayCommand(
        kind="gradient", x=40, y=40, w=30, h=20,
        grad_c1=Color(0, 0, 255), grad_c2=Color(255, 0, 0)))
    img = rasterize_to_cpu_image(dl, 80, 60)
    bmp = pe_rasterize(dl, 80, 60)
    assert cpu_matches_bitmap(img, bmp) == []


# ---------------------------------------------------------------------------
# CPUCanvas.clip_viewport — 滚动视口裁剪（不还原 int 列表）
# ---------------------------------------------------------------------------

@_reg
def test_clip_viewport_matches_bitmap_clip():
    """clip_viewport 与 PageInstance._clip_bitmap 语义一致。"""
    full = CPUCanvas.of_size(10, 40)
    full.fill_rect(0, 0, 10, 40, Color(10, 10, 200))
    full.fill_rect(5, 10, 5, 10, Color(200, 10, 10))
    clipped = full.clip_viewport(10, 20)   # rows 10..30
    assert clipped.img.height == 20
    # row 10 of the clip == row 10 of the full
    assert clipped.pixel_at(0, 0) == (10, 10, 200)
    # row 15 of the clip == row 15 of the full (inside the red block)
    assert clipped.pixel_at(7, 5) == (200, 10, 10)


@_reg
def test_clip_viewport_clamp():
    """clip_viewport 越界 clamp（与 _clip_bitmap 语义一致）。"""
    c = CPUCanvas.of_size(4, 4)
    assert c.clip_viewport(0, 0) is c            # h=0 → 返回 self
    r = c.clip_viewport(10, 4)
    # top=10 clamp 到 3（height-1），h=min(4, 4-3)=1 → 1 行裁剪
    assert r.img.height == 1


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
