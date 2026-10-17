"""C02 — 连续像素缓冲区阶段 2–3（多格式/padding/帧版本所有权/批量转换）
回归（round5 docs 批次 15）。

覆盖 toy_browser/rendering/cpu_image_formats.py：
- bytes_per_pixel / padded_stride / row_offset：RGB/RGBA/BGRA 每像素
  字节数 + 行 padding（C02 步骤 3）
- FrameImage：帧版本 + 读写所有权（C02 步骤 4，UI 读取时工作线程
  不得仍在改图）
- FormatConverter：RGBA/BGRA→RGB8 批量转换（不逐像素临时 bytes），
  透明合成与 paint.Color.blend_over 同公式（C02 验收 "透明图片逐
  像素一致"）
"""
from __future__ import annotations

from toy_browser.rendering.cpu_image_formats import (
    FORMAT_RGB8, FORMAT_RGBA8, FORMAT_BGRA8,
    bytes_per_pixel, padded_stride, row_offset,
    FrameImage, FormatConverter, composite_alpha,
)
from toy_browser.rendering.paint import Color


TESTS = []


# ----------------------------------------------------------------------
# 格式字节数 + padding（C02 步骤 3）
# ----------------------------------------------------------------------
def test_bytes_per_pixel():
    assert bytes_per_pixel("RGB8") == 3
    assert bytes_per_pixel("RGBA8") == 4
    assert bytes_per_pixel("BGRA8") == 4


def test_padded_stride_compact():
    assert padded_stride(10, "RGB8", align=1) == 30
    assert padded_stride(10, "RGBA8", align=1) == 40


def test_padded_stride_aligned():
    """行 padding（GPU 常用 4 字节对齐）。"""
    # 10 像素 RGB8 = 30 字节，4 对齐 → 32
    assert padded_stride(10, "RGB8", align=4) == 32
    # 10 像素 RGBA8 = 40 字节，4 对齐 → 40
    assert padded_stride(10, "RGBA8", align=4) == 40
    # 3 像素 RGB8 = 9 字节，32 对齐 → 32
    assert padded_stride(3, "RGB8", align=32) == 32


def test_row_offset():
    assert row_offset(0, 32) == 0
    assert row_offset(3, 32) == 96


# ----------------------------------------------------------------------
# FrameImage — 帧版本 + 所有权（C02 步骤 4）
# ----------------------------------------------------------------------
def test_frame_version_publish():
    img = FrameImage(bytearray(30), frame_version=0)
    # 写侧改图 → 版本自增，未发布前 UI 不可读
    img.buffer[0] = 0xFF
    img.bump_version()
    assert img.frame_version == 1
    assert img.can_read() is False   # owner 仍是 worker，未发布
    # 发布 → UI 可读
    img.publish()
    assert img.can_read() is True
    assert img.read_version() == 1


def test_frame_stale_write_blocks_read():
    """UI 持有期间写侧又改图（bump_version）→ 版本不一致，UI 读到
    旧帧而非脏 buffer（"不能 UI 读取时工作线程仍在改图"）。"""
    img = FrameImage(bytearray(30))
    img.buffer[0] = 0x11
    img.bump_version()
    img.publish()                     # published=1
    snap = img.snapshot()            # UI 读到已发布快照
    assert snap[0] == 0x11
    # 写侧又改（模拟工作线程新一轮渲染）
    img.buffer[0] = 0x22
    img.bump_version()               # frame_version=2 > published=1
    assert img.can_read() is False   # 版本不一致 → 不可读当前 buffer
    assert img.read_version() == 1   # 仍是上一已发布版本
    assert snap[0] == 0x11          # UI 快照不受后续改动影响


# ----------------------------------------------------------------------
# FormatConverter — 批量转换（C02 步骤 3 + 验收 "透明图片逐像素一致"）
# ----------------------------------------------------------------------
def _rgba(w, h, fill_rgb, a):
    buf = bytearray(w * h * 4)
    for i in range(w * h):
        buf[i * 4] = fill_rgb[0]
        buf[i * 4 + 1] = fill_rgb[1]
        buf[i * 4 + 2] = fill_rgb[2]
        buf[i * 4 + 3] = a
    return buf


def test_rgba_opaque_to_rgb8():
    """不透明 RGBA → RGB8 逐像素一致。"""
    buf = _rgba(2, 1, (10, 20, 30), 255)
    out = FormatConverter.rgba_to_rgb8(buf, 2, 1, in_stride=8)
    assert len(out) == 6
    # 不透明直接取 RGB
    assert out[0] == 10 and out[1] == 20 and out[2] == 30
    assert out[3] == 10 and out[4] == 20 and out[5] == 30


def test_rgba_alpha_composite_matches_color():
    """透明合成与 paint.Color.blend_over 同公式（验收 "透明图片逐
    像素一致"）。"""
    # 前景 (0,0,0) a=128 合成到背景 (255,255,255)
    buf = _rgba(1, 1, (0, 0, 0), 128)
    out = FormatConverter.rgba_to_rgb8(buf, 1, 1, in_stride=4,
                                       bg=(255, 255, 255))
    # 用 Color.blend_over 做基准
    ref = Color(0, 0, 0, 128).blend_over(0xFFFFFF)
    exp = ((ref >> 16) & 0xFF, (ref >> 8) & 0xFF, ref & 0xFF)
    assert (out[0], out[1], out[2]) == exp


def test_rgba_transparent_to_bg():
    """全透明（a=0）→ 取背景。"""
    buf = _rgba(1, 1, (1, 2, 3), 0)
    out = FormatConverter.rgba_to_rgb8(buf, 1, 1, in_stride=4,
                                       bg=(9, 9, 9))
    assert (out[0], out[1], out[2]) == (9, 9, 9)


def test_bgra_to_rgb8_channel_swap():
    """BGRA → RGB8 通道序翻转。"""
    # BGRA 内存：B,G,R,A = 30,20,10,255
    buf = bytearray([30, 20, 10, 255])
    out = FormatConverter.bgra_to_rgb8(buf, 1, 1, in_stride=4)
    assert out[0] == 10 and out[1] == 20 and out[2] == 30


def test_rgb8_to_rgba_appends_alpha():
    buf = bytearray([1, 2, 3])
    out = FormatConverter.rgb8_to_rgba(buf, 1, 1, in_stride=3, alpha=255)
    assert out == bytearray([1, 2, 3, 255])


def test_composite_alpha_endpoints():
    """alpha=0/255 端点（验收 "透明图片"）。"""
    assert composite_alpha(50, 99, 255) == 50   # 全不透明 → 前景
    assert composite_alpha(50, 99, 0) == 99     # 全透明 → 背景
    # 中间值与 Color.blend_over 一致
    c = Color(50, 50, 50, 128).blend_over(99 * 0x10000 + 99 * 0x100 + 99)
    assert composite_alpha(50, 99, 128) == ((c >> 16) & 0xFF)


# ----------------------------------------------------------------------
# 不同 stride + 奇数宽度批量转换（C02 验收）
# ----------------------------------------------------------------------
def test_stride_padding_conversion():
    """带 padding 的 stride 转换仍逐像素正确。"""
    w, h = 3, 2
    # 4 对齐 → stride = ceil(3*4/4)*4 = 12
    stride = padded_stride(w, "RGBA8", align=4)
    assert stride == 12
    buf = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            i = y * stride + x * 4
            buf[i] = x * 10
            buf[i + 1] = y * 10
            buf[i + 2] = 100
            buf[i + 3] = 255
    out = FormatConverter.rgba_to_rgb8(buf, w, h, in_stride=stride)
    assert len(out) == w * 3 * h
    # 像素 (1,0) → out[(0*3+1)*3 .. +2] = (10, 0, 100)
    i = (0 * w + 1) * 3
    assert (out[i], out[i + 1], out[i + 2]) == (10, 0, 100)
    # 像素 (2,1) → 行 1 x=2 → out[(1*3+2)*3]
    i = (1 * w + 2) * 3
    assert (out[i], out[i + 1], out[i + 2]) == (20, 10, 100)


TESTS.append(("test_bytes_per_pixel", test_bytes_per_pixel))
TESTS.append(("test_padded_stride_compact",
              test_padded_stride_compact))
TESTS.append(("test_padded_stride_aligned",
              test_padded_stride_aligned))
TESTS.append(("test_row_offset", test_row_offset))
TESTS.append(("test_frame_version_publish",
              test_frame_version_publish))
TESTS.append(("test_frame_stale_write_blocks_read",
              test_frame_stale_write_blocks_read))
TESTS.append(("test_rgba_opaque_to_rgb8",
              test_rgba_opaque_to_rgb8))
TESTS.append(("test_rgba_alpha_composite_matches_color",
              test_rgba_alpha_composite_matches_color))
TESTS.append(("test_rgba_transparent_to_bg",
              test_rgba_transparent_to_bg))
TESTS.append(("test_bgra_to_rgb8_channel_swap",
              test_bgra_to_rgb8_channel_swap))
TESTS.append(("test_rgb8_to_rgba_appends_alpha",
              test_rgb8_to_rgba_appends_alpha))
TESTS.append(("test_composite_alpha_endpoints",
              test_composite_alpha_endpoints))
TESTS.append(("test_stride_padding_conversion",
              test_stride_padding_conversion))
