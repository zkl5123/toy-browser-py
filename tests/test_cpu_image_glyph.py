"""批次 33 — C02 step 2 图片/字形逐阶段迁移 + 缓冲池复用治理 回归。

被测：``toy_browser/rendering/cpu_image_glyph.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering.cpu_image import CPUImage, ImagePool
from toy_browser.rendering.paint import Bitmap, Color, WHITE
from toy_browser.rendering.cpu_image_fill import (
    fill_rect_rgb8, fill_matches_bitmap,
)
from toy_browser.rendering.cpu_image_glyph import (
    paint_image_nearest, paint_glyph_mask, ImagePoolPolicy,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _white_img(w, h):
    img = CPUImage(width=w, height=h)
    fill_rect_rgb8(img, 0, 0, w, h, 255, 255, 255, 255)
    return img


# ---------------------------------------------------------------------------
# paint_image_nearest（C02 step 2 "图片" + P3.1/P19 语义）
# ---------------------------------------------------------------------------

@_reg
def test_image_nearest_upscale():
    """2x2 图片放大到 4x4 最近邻：每源像素复制 4 次（与 paint_engine 一致）。"""
    px = [0xFF0000, 0x00FF00, 0x0000FF, 0x112233]
    bmp = Bitmap(4, 4, WHITE)
    from toy_browser.rendering.paint_engine import _paint_img_pixels, DisplayCommand
    cmd = DisplayCommand(kind="img")
    cmd.x, cmd.y, cmd.w, cmd.h = 0, 0, 4, 4
    cmd.img_src_w, cmd.img_src_h = 2, 2
    cmd.img_pixels = px
    _paint_img_pixels(bmp, cmd)
    img = _white_img(4, 4)
    paint_image_nearest(img, px, 2, 2, 0, 0, 4, 4)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_image_nearest_downscale():
    """4x4 图片缩小到 2x2 最近邻（与 paint_engine 一致）。"""
    px = [0x111111, 0x222222, 0x333333, 0x444444] * 4
    bmp = Bitmap(2, 2, WHITE)
    from toy_browser.rendering.paint_engine import _paint_img_pixels, DisplayCommand
    cmd = DisplayCommand(kind="img")
    cmd.x, cmd.y, cmd.w, cmd.h = 0, 0, 2, 2
    cmd.img_src_w, cmd.img_src_h = 4, 4
    cmd.img_pixels = px
    _paint_img_pixels(bmp, cmd)
    img = _white_img(2, 2)
    paint_image_nearest(img, px, 4, 4, 0, 0, 2, 2)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_image_nearest_offset():
    """图片画在 (x0,y0) 偏移处（越界外不写）。"""
    px = [0x00FF00, 0xFF0000, 0x0000FF, 0x556677]
    bmp = Bitmap(6, 6, WHITE)
    from toy_browser.rendering.paint_engine import _paint_img_pixels, DisplayCommand
    cmd = DisplayCommand(kind="img")
    cmd.x, cmd.y, cmd.w, cmd.h = 1, 2, 2, 2
    cmd.img_src_w, cmd.img_src_h = 2, 2
    cmd.img_pixels = px
    _paint_img_pixels(bmp, cmd)
    img = _white_img(6, 6)
    paint_image_nearest(img, px, 2, 2, 1, 2, 2, 2)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_image_nearest_truncated_defense():
    """P19 截断 data URI 防御：pixels 长度 < src_w*src_h → 越界 break。"""
    px = [0xFF0000]  # 只有 1 像素（2x2 需要 4）
    img = _white_img(2, 2)
    paint_image_nearest(img, px, 2, 2, 0, 0, 2, 2)
    # 参考 Bitmap 路径：同样 break 在 idx>=len(pixels)
    bmp = Bitmap(2, 2, WHITE)
    from toy_browser.rendering.paint_engine import _paint_img_pixels, DisplayCommand
    cmd = DisplayCommand(kind="img")
    cmd.x, cmd.y, cmd.w, cmd.h = 0, 0, 2, 2
    cmd.img_src_w, cmd.img_src_h = 2, 2
    cmd.img_pixels = px
    _paint_img_pixels(bmp, cmd)
    assert fill_matches_bitmap(bmp, img) == []


@_reg
def test_image_nearest_empty_or_zero():
    """空 pixels / 负尺寸 / 零尺寸 → 不写。"""
    img = _white_img(3, 3)
    paint_image_nearest(img, [], 2, 2, 0, 0, 2, 2)
    paint_image_nearest(img, [0xFF0000]*4, 0, 2, 0, 0, 2, 2)
    paint_image_nearest(img, [0xFF0000]*4, 2, 0, 0, 0, 2, 2)
    paint_image_nearest(img, [0xFF0000]*4, 2, 2, 0, 0, 0, 0)
    assert fill_matches_bitmap(Bitmap(3, 3, WHITE), img) == []


@_reg
def test_image_nearest_odd_stride_width():
    """奇数宽度 + 非紧凑 stride（C02 验收"不同 stride、奇数宽度"）。"""
    px = [0xAA0000, 0x00BB00, 0x0000CC, 0x123456,
          0x654321, 0xFF00FF, 0x00FFFF, 0x111111, 0x222222]
    bmp = Bitmap(3, 3, WHITE)
    from toy_browser.rendering.paint_engine import _paint_img_pixels, DisplayCommand
    cmd = DisplayCommand(kind="img")
    cmd.x, cmd.y, cmd.w, cmd.h = 0, 0, 3, 3
    cmd.img_src_w, cmd.img_src_h = 3, 3
    cmd.img_pixels = px
    _paint_img_pixels(bmp, cmd)
    # CPUImage 带 stride padding（每行加 2 字节 padding → stride=3*3+? 用 12）
    img = CPUImage(width=3, height=3, stride=12)
    fill_rect_rgb8(img, 0, 0, 3, 3, 255, 255, 255, 255)
    paint_image_nearest(img, px, 3, 3, 0, 0, 3, 3)
    # fill_matches_bitmap 读 img 的 stride（12）正确
    assert fill_matches_bitmap(bmp, img) == []


# ---------------------------------------------------------------------------
# paint_glyph_mask（C02 step 2 "字形" + 批次 12/15 字形栅格语义）
# ---------------------------------------------------------------------------

@_reg
def test_glyph_full_opaque_black():
    """2x2 全 255 mask + 黑色 → 4 像素全黑（白底上）。"""
    img = _white_img(3, 3)
    paint_glyph_mask(img, [[255, 255], [255, 255]], 2, 2, 0, 0,
                     r=0, g=0, b=0, base_alpha=255)
    assert img.get_pixel(0, 0) == (0, 0, 0)
    assert img.get_pixel(1, 1) == (0, 0, 0)
    assert img.get_pixel(2, 2) == (255, 255, 255)  # 未盖仍白


@_reg
def test_glyph_semi_coverage():
    """mask=128 半覆盖：合成 128/255 比例（与 Bitmap 参考对照）。"""
    img = _white_img(2, 2)
    paint_glyph_mask(img, [[128, 128], [128, 128]], 2, 2, 0, 0,
                     r=0, g=0, b=0, base_alpha=255)
    # 白底合 128 覆盖率黑 → int(0*0.502 + 255*0.498) = 127
    assert img.get_pixel(0, 0) == (127, 127, 127)


@_reg
def test_glyph_zero_mask_noop():
    """mask=0 全透明 → 不写（仍白）。"""
    img = _white_img(2, 2)
    paint_glyph_mask(img, [[0, 0], [0, 0]], 2, 2, 0, 0,
                     r=0, g=0, b=0, base_alpha=255)
    assert img.get_pixel(0, 0) == (255, 255, 255)


@_reg
def test_glyph_base_alpha_scales():
    """base_alpha=128 → 覆盖率 m=255 实际源 alpha = 255*128/65025≈0.5。"""
    img = _white_img(1, 1)
    paint_glyph_mask(img, [[255]], 1, 1, 0, 0,
                     r=255, g=0, b=0, base_alpha=128)
    # sa = 255*128/65025 ≈ 0.498
    # R = int(255*0.498 + 255*0.502) = 255（全红）
    # G = int(0*0.498 + 255*0.502) = 128
    got = img.get_pixel(0, 0)
    assert got[0] == 255
    assert got[1] == int(255 * (1 - 255 * 128 / (255 * 255)))


@_reg
def test_glyph_outside_clamp():
    """mask 部分越界（x0,y0 负）→ 越界像素不写（mask 需满 gw*gh）。"""
    img = _white_img(2, 2)
    paint_glyph_mask(img, [[255, 255]] * 2, 2, 2, -1, -1,
                     r=0, g=0, b=0, base_alpha=255)
    # x0=-1,y0=-1：源 (sx,sy) → 目标 (sx-1, sy-1)；
    # 目标在 0..1 内的是 (0,0)（源 (1,1)）；其余 (源(0,0)→(-1,-1)、
    # (0,1)→(-1,0)、(1,0)→(0,-1)) 越界跳过 → 只有 (0,0) 变黑。
    assert img.get_pixel(0, 0) == (0, 0, 0)
    assert img.get_pixel(1, 1) == (255, 255, 255)


@_reg
def test_glyph_empty_mask_safe():
    """空 mask / 负尺寸 → 不写不抛。"""
    img = _white_img(2, 2)
    paint_glyph_mask(img, [], 0, 0, 0, 0, 0, 0, 0, 255)
    paint_glyph_mask(img, [[255]], 0, 2, 0, 0, 0, 0, 0, 255)
    assert fill_matches_bitmap(Bitmap(2, 2, WHITE), img) == []


# ---------------------------------------------------------------------------
# ImagePoolPolicy（C02 step 4 缓冲池复用治理）
# ---------------------------------------------------------------------------

@_reg
def test_pool_policy_hit_miss():
    """命中 / 未命中记账。"""
    pool = ImagePool(max_size=2)
    pol = ImagePoolPolicy(pool)
    assert pol.hit_rate == 0.0  # 无请求
    a = pool.get(8, 8)
    pol.on_get(False)  # 新建
    pool.put(a)
    pol.on_put(True)
    b = pool.get(8, 8)
    pol.on_get(True)   # 命中
    assert pol.hit == 1 and pol.miss == 1
    assert pol.reused == 1 and pol.created == 1
    assert pol.total_created == 1
    assert abs(pol.hit_rate - 0.5) < 1e-9


@_reg
def test_pool_policy_eviction():
    """put 超上限被丢弃 → evicted 记账。"""
    pool = ImagePool(max_size=1)
    pol = ImagePoolPolicy(pool)
    a1 = pool.get(4, 4); pool.put(a1)
    a2 = pool.get(4, 4); pool.put(a2)  # a2 入池，a1 被挤（len>=max）
    # ImagePool.put：len(lst)<max 才 append；a1 已在池，a2 来时 len=1>=max=1 → 丢
    pol.on_get(False)  # a1
    pol.on_get(False)  # a2（新建，因为池空）
    pol.on_put(True)   # a1 入
    pol.on_put(False)  # a2 被丢（超上限）
    assert pol.evicted == 1


@_reg
def test_pool_policy_size_distribution():
    """size_distribution 读池内当前数量。"""
    pool = ImagePool(max_size=4)
    pol = ImagePoolPolicy(pool)
    a = pool.get(10, 10)
    pool.put(a)
    b = pool.get(20, 20)
    pool.put(b)
    dist = pol.size_distribution()
    assert dist.get((10, 10, 30)) == 1
    assert dist.get((20, 20, 60)) == 1


@_reg
def test_pool_policy_no_pool():
    """pool=None 时 size_distribution 空、to_report 仍可用。"""
    pol = ImagePoolPolicy(None)
    assert pol.size_distribution() == {}
    assert "hit=0" in pol.to_report()


@_reg
def test_pool_policy_report_string():
    """to_report 人类可读。"""
    pol = ImagePoolPolicy(ImagePool(max_size=2))
    pol.on_get(True); pol.on_get(False)
    rep = pol.to_report()
    assert "hit=1" in rep and "miss=1" in rep and "hit_rate=0.50" in rep


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
