"""批次 32 — C02 step 2 填充/混合/图片迁移到连续缓冲区 回归。

被测：``toy_browser/rendering/cpu_image_fill.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering.cpu_image import CPUImage
from toy_browser.rendering.paint import Bitmap, Color, WHITE
from toy_browser.rendering.cpu_image_fill import (
    fill_rect_rgb8, blit_rgba8_over_rgb8, blit_rgba8_over_rgba8,
    blend_pixel_rgba8, fill_matches_bitmap,
    _read_rgb8, _write_rgb8,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _white_img(w, h):
    """白底 RGB8 CPUImage（等同 Bitmap(w, h, WHITE) 初始态）。"""
    img = CPUImage(width=w, height=h)
    fill_rect_rgb8(img, 0, 0, w, h, 255, 255, 255, 255)
    return img


def _bmp_rgb(v):
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


# ---------------------------------------------------------------------------
# fill_rect_rgb8 与 Bitmap.fill_rect 逐像素一致（C02 验收"兼容现有 CLI"）
# ---------------------------------------------------------------------------

@_reg
def test_fill_opaque_matches_bitmap():
    """不透明填充：CPUImage 与 Bitmap 逐像素一致。"""
    bmp = Bitmap(4, 4, WHITE)
    bmp.fill_rect(1, 1, 2, 2, Color(0, 128, 255, 255))
    img = _white_img(4, 4)
    fill_rect_rgb8(img, 1, 1, 2, 2, 0, 128, 255, 255)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_fill_semi_transparent_matches_bitmap():
    """半透明填充：alpha 混合与 Bitmap.blend_over 逐像素一致。"""
    bmp = Bitmap(4, 4, WHITE)
    bmp.fill_rect(0, 0, 3, 3, Color(255, 0, 0, 128))
    img = _white_img(4, 4)
    fill_rect_rgb8(img, 0, 0, 3, 3, 255, 0, 0, 128)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_fill_transparent_no_op():
    """透明（a=0）填充：不写任何像素（与 Bitmap 一致）。"""
    bmp = Bitmap(4, 4, WHITE)
    bmp.fill_rect(0, 0, 4, 4, Color(255, 0, 0, 0))
    img = _white_img(4, 4)
    fill_rect_rgb8(img, 0, 0, 4, 4, 255, 0, 0, 0)
    assert fill_matches_bitmap(bmp, img) == []
    assert img.get_pixel(0, 0) == (255, 255, 255)  # 仍白


@_reg
def test_fill_clamp_negative_and_zero():
    """负尺寸 / 越界 clamp：只画可见部分（与 Bitmap 一致）。"""
    bmp = Bitmap(4, 4, WHITE)
    bmp.fill_rect(-2, -2, 4, 4, Color(0, 0, 255, 255))  # 越界裁剪到 0..2
    img = _white_img(4, 4)
    fill_rect_rgb8(img, -2, -2, 4, 4, 0, 0, 255, 255)
    assert fill_matches_bitmap(bmp, img) == []
    # 负尺寸 → 不画
    fill_rect_rgb8(img, 0, 0, 0, 0, 255, 255, 0, 255)
    assert img.get_pixel(0, 0) == (0, 0, 255)


@_reg
def test_fill_partial_outside():
    """部分越界（右/下超出）：clamp 到边界内（与 Bitmap 一致）。"""
    bmp = Bitmap(4, 4, WHITE)
    bmp.fill_rect(2, 2, 10, 10, Color(0, 255, 0, 255))  # 越界裁剪到 2..4
    img = _white_img(4, 4)
    fill_rect_rgb8(img, 2, 2, 10, 10, 0, 255, 0, 255)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_fill_stacked_semi():
    """半透明叠加：多次 blend 累积（与 Bitmap 多次 set_pixel 一致）。"""
    bmp = Bitmap(3, 3, WHITE)
    for _ in range(3):
        bmp.fill_rect(0, 0, 3, 3, Color(0, 0, 255, 64))
    img = _white_img(3, 3)
    for _ in range(3):
        fill_rect_rgb8(img, 0, 0, 3, 3, 0, 0, 255, 64)
    assert fill_matches_bitmap(bmp, img) == []


# ---------------------------------------------------------------------------
# blit_rgba8_over_rgb8（图片 alpha 混合）
# ---------------------------------------------------------------------------

@_reg
def test_blit_rgba_opaque_covers():
    """RGBA 不透明像素覆盖 RGB8 目标。"""
    img = _white_img(4, 4)
    src = bytearray()
    for y in range(2):
        for x in range(2):
            src += bytes((255, 0, 0, 255))
    blit_rgba8_over_rgb8(img, src, 2, 2, 2 * 4, ox=0, oy=0)
    assert img.get_pixel(0, 0) == (255, 0, 0)
    assert img.get_pixel(1, 1) == (255, 0, 0)
    assert img.get_pixel(2, 2) == (255, 255, 255)  # 未覆盖仍白


@_reg
def test_blit_rgba_transparent_no_write():
    """RGBA 全透明像素不写目标。"""
    img = _white_img(4, 4)
    src = bytes((255, 0, 0, 0)) * 4  # 2x2 全透明
    blit_rgba8_over_rgb8(img, src, 2, 2, 2 * 4, ox=0, oy=0)
    assert img.get_pixel(0, 0) == (255, 255, 255)  # 仍白


@_reg
def test_blit_rgba_semi_composite():
    """RGBA 半透明合成到 RGB8（composite_alpha 公式）。"""
    img = _white_img(4, 4)
    src = bytes((255, 0, 0, 128)) * 4  # 2x2 半透明红
    blit_rgba8_over_rgb8(img, src, 2, 2, 2 * 4, ox=0, oy=0)
    # 半透明红(R=255,G=0,B=0,a=128) 合到白底：
    # R: composite_alpha(255,255,128)=int(255*128/255+255*127/255)=255
    # G: composite_alpha(0,255,128)=int(0+255*127/255)=127
    # B: 同 G = 127
    assert img.get_pixel(0, 0) == (255, 127, 127)


@_reg
def test_blit_rgba_offscreen_clamp():
    """RGBA 源部分越界（ox/oy 负或超界）：clamp 不写越界像素。"""
    img = _white_img(4, 4)
    src = bytes((0, 255, 0, 255)) * 4  # 2x2 不透明绿
    blit_rgba8_over_rgb8(img, src, 2, 2, 2 * 4, ox=2, oy=2)
    assert img.get_pixel(2, 2) == (0, 255, 0)
    assert img.get_pixel(0, 0) == (255, 255, 255)  # 未覆盖


# ---------------------------------------------------------------------------
# blit_rgba8_over_rgba8（图片层叠）
# ---------------------------------------------------------------------------

@_reg
def test_blit_rgba8_to_rgba8_layer():
    """RGBA→RGBA 层叠：源 alpha 混到目标、目标 alpha 保留。"""
    dst = bytearray(4 * 2 * 4)  # 2x2 RGBA 全透明
    src = bytes((255, 0, 0, 128)) * 4  # 2x2 半透明红
    blit_rgba8_over_rgba8(dst, 2 * 4, 2, 2, src, 2, 2, 2 * 4, 0, 0)
    # dst[0]=255, dst[1]=0, dst[2]=0, dst[3]=alpha
    # blend: src_a=128, dst_a=0 → out_a=int(128 + 0)=128
    # R=255*0.502+0*0*0.498=128? 用 blend_pixel_rgba8 公式：
    # or_ = 255*sa + 0*(da*(1-sa)) = 255*0.502 + 0 = 128
    assert dst[0] == 128  # R
    assert dst[3] == 128  # 输出 alpha
    assert dst[1] == 0 and dst[2] == 0  # G/B=0


@_reg
def test_blit_rgba8_transparent_source_noop():
    """RGBA 源全透明 → 目标不变。"""
    dst = bytearray(4 * 2 * 4)
    dst[3] = 200  # 目标 alpha
    src = bytes((0, 0, 255, 0)) * 4  # 全透明
    blit_rgba8_over_rgba8(dst, 2 * 4, 2, 2, src, 2, 2, 2 * 4, 0, 0)
    assert dst[3] == 200  # 目标 alpha 保留（源 a=0 跳过）


# ---------------------------------------------------------------------------
# blend_pixel_rgba8
# ---------------------------------------------------------------------------

@_reg
def test_blend_pixel_rgba8_opaque_source():
    """src a=255 → 颜色被源覆盖、alpha 保留目标。"""
    out = blend_pixel_rgba8(10, 20, 30, 200, 255, 0, 0, 255)
    assert out == (255, 0, 0, 200)


@_reg
def test_blend_pixel_rgba8_transparent_source():
    """src a=0 → 目标不变。"""
    out = blend_pixel_rgba8(10, 20, 30, 200, 255, 0, 0, 0)
    assert out == (10, 20, 30, 200)


@_reg
def test_blend_pixel_rgba8_semi():
    """src 半透明 → 颜色混合、alpha 累加（clamp 255）。"""
    out = blend_pixel_rgba8(0, 0, 0, 0, 255, 0, 0, 128)
    r, g, b, a = out
    # sa=128/255≈0.502, da=0 → out_a=int(128+0)=128
    # R=255*0.502+0=128
    assert a == 128
    assert r == 128
    assert g == 0 and b == 0


@_reg
def test_blend_pixel_rgba8_alpha_accumulate_clamp():
    """两个高 alpha 叠加 → 输出 alpha 累加（不超 255 clamp）。"""
    out = blend_pixel_rgba8(255, 255, 255, 200, 255, 255, 255, 200)
    # out_a = min(255, src_a + dst_a*(1-sa)) = 200 + 200*(1-200/255) ≈ 243
    assert out[3] == int(min(255, 200 + 200 * (1 - 200 / 255)))


@_reg
def test_blend_pixel_rgba8_alpha_clamp_255():
    """两个 255 alpha 叠加 → 输出 alpha 恰好 clamp 到 255。"""
    out = blend_pixel_rgba8(255, 255, 255, 255, 255, 255, 255, 255)
    assert out[3] == 255


# ---------------------------------------------------------------------------
# fill_matches_bitmap（C02 验收"逐像素一致"核对工具）
# ---------------------------------------------------------------------------

@_reg
def test_fill_matches_bitmap_uses_from_bitmap():
    """from_bitmap 转换的 CPUImage 与 Bitmap 逐像素一致（空 mismatch）。"""
    bmp = Bitmap(5, 3, WHITE)
    for y in range(3):
        for x in range(5):
            bmp.pixels[y * 5 + x] = ((x * 40) & 0xFF) << 16 | ((y * 50) & 0xFF) << 8 | ((x + y) * 10) & 0xFF
    img = CPUImage.from_bitmap(bmp)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_fill_matches_bitmap_detects_diff():
    """人为改一个像素 → mismatch 非空。"""
    bmp = Bitmap(3, 3, WHITE)
    img = CPUImage.from_bitmap(bmp)
    img.set_pixel(1, 1, 0, 0, 0)  # 改中点为黑
    mism = fill_matches_bitmap(bmp, img)
    assert len(mism) == 1
    assert mism[0][0] == 1 and mism[0][1] == 1
    assert mism[0][3] == (0, 0, 0)  # img 侧
    assert mism[0][2] == (255, 255, 255)  # bmp 侧


@_reg
def test_fill_matches_bitmap_tolerance():
    """tolerance 放宽后小差异不算 mismatch。"""
    bmp = Bitmap(3, 3, WHITE)
    img = CPUImage.from_bitmap(bmp)
    img.set_pixel(1, 1, 250, 250, 250)  # 差 5
    assert fill_matches_bitmap(bmp, img, tolerance=5) == []
    assert len(fill_matches_bitmap(bmp, img, tolerance=4)) == 1


@_reg
def test_fill_matches_bitmap_size_mismatch():
    """尺寸不一致 → 返回空（不做比对，安全）。"""
    bmp = Bitmap(3, 3, WHITE)
    img = CPUImage(width=4, height=4)
    assert fill_matches_bitmap(bmp, img) == []


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
