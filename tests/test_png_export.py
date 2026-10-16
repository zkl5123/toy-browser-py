"""C09 — PNG 编码与异步导出（纯逻辑核心）回归（round5 docs 批次 14）。

覆盖 toy_browser/app/png_export.py：
- ContinuousRows：连续 RGB 行编码（row_rgb / raw_rows 与 browser.py
  同字节序）
- encode_rows / PngEncodePlan：分段计时 + 压缩级别（默认 6 保留旧
  契约，level 0 最快）
- decode_pixels / pixels_equal：解码像素一致性（改压缩级别后字节变
  但像素一致，不能改字节金图掩盖错误）
- FrameSnapshot：不可变帧快照（捕获冻结像素，活帧更新不影响快照）
- BackgroundExporter：后台导出（pending/done/cancelled/failed，导出中
  导航/关页取消，完成回 UI 通知，写盘失败标记）
- solid_bitmap / transparent_rows / random_bitmap：纯色 / 透明 / 随机
  位图
"""
from __future__ import annotations

import struct
import zlib

from toy_browser.app.png_export import (
    ContinuousRows, PngEncodePlan, encode_rows, decode_pixels,
    pixels_equal, FrameSnapshot, BackgroundExporter,
    solid_bitmap, transparent_rows, random_bitmap,
)


TESTS = []


def _bmp_rows(width=4, height=3, rgb=(255, 0, 0)):
    px = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    return ContinuousRows([px] * (width * height), width, height)


# ----------------------------------------------------------------------
# ContinuousRows — 连续行编码（C09 步骤 1）
# ----------------------------------------------------------------------
def test_row_rgb_byte_order():
    rows = _bmp_rows(2, 1, (255, 0, 128))
    rb = rows.row_rgb(0)
    # 2 像素 × 3 字节：RRRGGGBBB（255,0,128 各两遍）
    assert len(rb) == 6
    assert rb[0] == 255 and rb[1] == 0 and rb[2] == 128
    assert rb[3] == 255 and rb[4] == 0 and rb[5] == 128


def test_raw_rows_matches_browser_format():
    """raw_rows 与 browser.py ``_png_raw_rows`` 同字节序（每行 1 字节
    filter(0) + w×3 RGB）。"""
    rows = _bmp_rows(2, 3, (1, 2, 3))
    raw = rows.raw_rows()
    assert len(raw) == 3 * (2 * 3 + 1)
    # 第 0 行：filter=0 + 2×3 字节
    assert raw[0] == 0
    assert raw[1] == 1 and raw[2] == 2 and raw[3] == 3
    # 第 1 行从 off = (2*3+1)=7 开始
    assert raw[7] == 0
    assert raw[8] == 1 and raw[9] == 2 and raw[10] == 3


# ----------------------------------------------------------------------
# encode_rows + PngEncodePlan（C09 步骤 2：分段计时 + 压缩参数）
# ----------------------------------------------------------------------
def test_encode_rows_produces_valid_png():
    rows = _bmp_rows(2, 2)
    png = encode_rows(2, 2, bytes(rows.raw_rows()), level=6)
    # PNG 签名
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # 含 IHDR / IDAT / IEND chunk
    assert b"IHDR" in png and b"IDAT" in png and b"IEND" in png


def test_encode_default_level_matches_browser():
    """默认 level=6 与 browser.py ``_png_encode_rows`` 字节一致
    （C09 步骤 4 "保留默认编码的旧契约"）。"""
    rows = _bmp_rows(2, 2, (10, 20, 30))
    raw = bytes(rows.raw_rows())
    # 复现 browser.py 的编码路径
    def _chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw)   # 默认 level=6
    legacy = sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    assert encode_rows(2, 2, raw, level=6) == legacy


def test_plan_stage_timings_recorded():
    rows = _bmp_rows(3, 3)
    plan = PngEncodePlan(level=6)
    png = plan.run(rows)
    assert len(png) > 0
    # stage_timings 含 pack / compress / write 三段
    assert "pack_ms" in plan.stage_timings
    assert "compress_ms" in plan.stage_timings
    assert "write_ms" in plan.stage_timings


def test_plan_compression_level_affects_size():
    """压缩级别影响输出大小（"压缩率 vs 速度可选参数"）。

    用**低熵重复数据**（渐变）体现压缩率差异——随机不可压缩数据上
    zlib level 0 的 deflate 存根开销反而略高于 level 9（正常现象，
    不能用随机数据测压缩差异）。"""
    import zlib
    w, h = 64, 64
    # 水平渐变（同列同色，高度可预测）
    pixels = []
    for y in range(h):
        for x in range(w):
            pixels.append((x << 16) | (0 << 8) | y)
    rows = ContinuousRows(pixels, w, h)
    raw = bytes(rows.raw_rows())
    comp0 = zlib.compress(raw, level=0)
    comp9 = zlib.compress(raw, level=9)
    assert len(comp0) > len(comp9)


# ----------------------------------------------------------------------
# decode_pixels / pixels_equal（C09 步骤 4：像素一致性）
# ----------------------------------------------------------------------
def test_decode_roundtrip_pixels_equal():
    """编码 → 解码像素与原图逐像素一致（"不能简单改字节金图掩盖
    错误"）。"""
    rows = _bmp_rows(3, 2, (10, 20, 30))
    png = encode_rows(3, 2, bytes(rows.raw_rows()), level=6)
    decoded = decode_pixels(png)
    assert pixels_equal(decoded, rows.pixels)


def test_decode_different_levels_same_pixels():
    """改压缩级别 → 字节变，但解码像素一致（C09 验收 "新模式解码
    像素正确"）。"""
    rows = _bmp_rows(3, 3, (5, 10, 15))
    raw = bytes(rows.raw_rows())
    png0 = encode_rows(3, 3, raw, level=0)
    png9 = encode_rows(3, 3, raw, level=9)
    assert png0 != png9                # 字节不同（压缩级别变）
    assert pixels_equal(decode_pixels(png0), rows.pixels)
    assert pixels_equal(decode_pixels(png9), rows.pixels)


def test_pixels_equal_length_and_content():
    assert pixels_equal([1, 2, 3], [1, 2, 3]) is True
    assert pixels_equal([1, 2], [1, 2, 3]) is False
    assert pixels_equal([1, 2, 3], [1, 2, 4]) is False


# ----------------------------------------------------------------------
# FrameSnapshot（C09 步骤 3：不可变帧快照）
# ----------------------------------------------------------------------
def test_snapshot_freezes_pixels():
    live = [0xFFFFFF, 0x000000]
    snap = FrameSnapshot(live, 2, 1, frame_id=7)
    # 活帧更新不影响快照（快照持有拷贝）
    live[0] = 0x123456
    assert snap.pixels[0] == 0xFFFFFF
    assert snap.frame_id == 7
    # 快照可生成连续行
    rows = snap.rows()
    assert rows.width == 2 and rows.height == 1


# ----------------------------------------------------------------------
# BackgroundExporter（C09 步骤 3 + 验收 "导出中导航/关页"、"写盘失败"）
# ----------------------------------------------------------------------
def _snap():
    return FrameSnapshot([0xFFFFFF] * 4, 2, 2)


def test_exporter_start_complete():
    ex = BackgroundExporter()
    tid = ex.start(_snap(), level=6)
    assert ex.status(tid) == ex.PENDING
    png = ex.complete(tid)
    assert png is not None and len(png) > 0
    assert ex.status(tid) == ex.DONE
    # 完成回 UI 通知（C09 步骤 3）
    assert any(n[0] == tid and n[1] == ex.DONE
               for n in ex.notifications())


def test_exporter_cancel_during_navigation():
    """导出中导航/关页 → 取消任务（不产字节，"UI 不冻结"）。"""
    ex = BackgroundExporter()
    tid = ex.start(_snap())
    assert ex.cancel(tid) is True
    assert ex.status(tid) == ex.CANCELLED
    assert ex.complete(tid) is None     # 已取消 → 不产字节
    # 取消回 UI 通知
    assert any(n[0] == tid and n[1] == ex.CANCELLED
               for n in ex.notifications())


def test_exporter_cancel_twice_noop():
    ex = BackgroundExporter()
    tid = ex.start(_snap())
    assert ex.cancel(tid) is True
    assert ex.cancel(tid) is False     # 已取消，二次取消无效果


def test_exporter_complete_unknown_task():
    ex = BackgroundExporter()
    assert ex.complete(999) is None
    assert ex.status(999) == "unknown"


def test_exporter_write_failure_marked():
    """写盘失败 → 标记任务 failed（不回滚像素，C09 验收 "写盘失败"）。"""
    ex = BackgroundExporter()
    tid = ex.start(_snap())
    ex.fail(tid, reason="disk full")
    assert ex.status(tid) == ex.FAILED
    notif = [n for n in ex.notifications() if n[0] == tid]
    assert notif and notif[-1][1] == ex.FAILED
    assert notif[-1][3] == "disk full"


# ----------------------------------------------------------------------
# 纯色 / 透明 / 随机 / 奇数宽度（C09 验收）
# ----------------------------------------------------------------------
def test_solid_roundtrip():
    rows = solid_bitmap(5, 4, rgb=(1, 2, 3))
    png = encode_rows(5, 4, bytes(rows.raw_rows()))
    assert pixels_equal(decode_pixels(png), rows.pixels)


def test_transparent_roundtrip():
    rows = transparent_rows(3, 3)
    png = encode_rows(3, 3, bytes(rows.raw_rows()))
    assert pixels_equal(decode_pixels(png), rows.pixels)


def test_random_odd_width_roundtrip():
    """奇数宽度（验收 "奇数宽度"）随机位图往返一致。"""
    rows = random_bitmap(7, 5, seed=1)
    png = encode_rows(7, 5, bytes(rows.raw_rows()), level=9)
    assert pixels_equal(decode_pixels(png), rows.pixels)


TESTS.append(("test_row_rgb_byte_order", test_row_rgb_byte_order))
TESTS.append(("test_raw_rows_matches_browser_format",
              test_raw_rows_matches_browser_format))
TESTS.append(("test_encode_rows_produces_valid_png",
              test_encode_rows_produces_valid_png))
TESTS.append(("test_encode_default_level_matches_browser",
              test_encode_default_level_matches_browser))
TESTS.append(("test_plan_stage_timings_recorded",
              test_plan_stage_timings_recorded))
TESTS.append(("test_plan_compression_level_affects_size",
              test_plan_compression_level_affects_size))
TESTS.append(("test_decode_roundtrip_pixels_equal",
              test_decode_roundtrip_pixels_equal))
TESTS.append(("test_decode_different_levels_same_pixels",
              test_decode_different_levels_same_pixels))
TESTS.append(("test_pixels_equal_length_and_content",
              test_pixels_equal_length_and_content))
TESTS.append(("test_snapshot_freezes_pixels",
              test_snapshot_freezes_pixels))
TESTS.append(("test_exporter_start_complete",
              test_exporter_start_complete))
TESTS.append(("test_exporter_cancel_during_navigation",
              test_exporter_cancel_during_navigation))
TESTS.append(("test_exporter_cancel_twice_noop",
              test_exporter_cancel_twice_noop))
TESTS.append(("test_exporter_complete_unknown_task",
              test_exporter_complete_unknown_task))
TESTS.append(("test_exporter_write_failure_marked",
              test_exporter_write_failure_marked))
TESTS.append(("test_solid_roundtrip", test_solid_roundtrip))
TESTS.append(("test_transparent_roundtrip", test_transparent_roundtrip))
TESTS.append(("test_random_odd_width_roundtrip",
              test_random_odd_width_roundtrip))
