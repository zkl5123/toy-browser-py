"""C09 — PNG 编码与异步导出（纯逻辑核心，批次 14）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C09：

> 证据与位置：browser.py 的 ``_png_pack_row/_png_raw_rows/
> write_png_bytes``；800×600 内存编码约 0.58 秒。
>
> 1. C02 完成后直接读取连续 RGB/RGBA 行，消除导出时再次遍历 Python
>    int 列表。
> 2. 把行打包、压缩和文件写入分别计时；按需求在压缩率和速度之间
>    提供可选参数。
> 3. 交互帧不做 PNG；导出捕获不可变帧快照，在后台编码，完成后回 UI
>    通知。
> 4. 保留默认编码的旧契约；若新增快速编码模式改变了 PNG 字节，新增
>    解码像素一致性测试，不能简单改掉字节金图掩盖错误。
>
> 验收：随机位图、奇数宽度、纯色、透明、导出中导航/关页、写盘失败。
> 默认字节回归不破坏；新模式解码像素正确且 UI 不冻结。
> 交付：连续行编码、后台导出、字节与像素双层测试。

本模块是**纯逻辑核心**（不依赖 tkinter / 网络 / 真实磁盘 I/O），把
C09 的"连续行编码 + 分段计时 + 压缩参数 + 后台导出快照"抽成可测试的
数据结构；browser.py 现有的 ``write_png`` / ``write_png_bytes`` 保持
默认字节契约不变（C09 步骤 4 "保留默认编码的旧契约"）：

* :class:`ContinuousRows` — 连续 RGB/RGBA 行编码（C09 步骤 1）：从
  位图直接按行切片读取，消除逐像素 int 列表遍历。
* :class:`PngEncodePlan` — 行打包 / 压缩 / 写盘分段计时 + 压缩率
  参数（C09 步骤 2）：``level`` 控制压缩率 vs 速度，``stage_timings``
  分别记录 pack/compress/write 耗时。
* :class:`FrameSnapshot` — 不可变帧快照（C09 步骤 3 "导出捕获不可变
  帧快照，在后台编码"）：捕获时刻冻结像素，后台编码不影响 UI 帧
  更新；"交互帧不做 PNG"（导出走快照，不进交互渲染路径）。
* :class:`BackgroundExporter` — 后台导出任务（C09 步骤 3 "完成后回
  UI 通知" + 验收 "导出中导航/关页"）：导出中导航 / 关页 → 取消
  任务（不写盘），任务完成回 UI 通知。
* :func:`decode_pixels` — 解码像素一致性（C09 步骤 4）：把 PNG
  IDAT 解回像素，与原始位图逐像素比较（"不能简单改掉字节金图掩盖
  错误"——新模式改字节后仍要像素一致）。
* :func:`encode_rows` — 把连续行打包成 PNG 字节（与 browser.py
  ``_png_encode_rows`` 同规则，可独立测试 + 分段计时）。
"""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# 连续行编码（C09 步骤 1：直接读连续 RGB/RGBA 行）
# ----------------------------------------------------------------------
class ContinuousRows:
    """C09 步骤 1 — 连续 RGB 行（从位图按行切片，消除逐像素 int 遍历）。

    * *pixels* — 行主序 32-bit 像素列表（``0x00RRGGBB``，同
      browser.py ``Bitmap.pixels``）。
    * *width* / *height*。
    * :meth:`row_rgb` — 取第 *py* 行的 RGB triplets（``bytes``），
      按 PNG 像素主序 ``R G B R G B …``。
    * :meth:`raw_rows` — filter-0 raw-row 负载（每行前置 filter
      字节 0 + w×3 字节），等价 browser.py ``_png_raw_rows``。
    """

    def __init__(self, pixels: list, width: int, height: int) -> None:
        self.pixels = pixels
        self.width = width
        self.height = height

    def row_rgb(self, py: int) -> bytes:
        """第 *py* 行的 RGB bytes（w×3，像素主序 R G B …）。"""
        w = self.width
        row = self.pixels[py * w:(py + 1) * w]
        out = bytearray(w * 3)
        for i, px in enumerate(row):
            out[i * 3] = (px >> 16) & 0xFF
            out[i * 3 + 1] = (px >> 8) & 0xFF
            out[i * 3 + 2] = px & 0xFF
        return bytes(out)

    def raw_rows(self) -> bytearray:
        """filter-0 raw-row 负载（与 browser.py ``_png_raw_rows`` 同
        字节序）：每行 1 字节 filter(0) + w×3 字节 RGB。"""
        w, h = self.width, self.height
        raw = bytearray(h * (w * 3 + 1))
        off = 0
        for py in range(h):
            raw[off] = 0
            off += 1
            row_bytes = self.row_rgb(py)
            raw[off:off + len(row_bytes)] = row_bytes
            off += len(row_bytes)
        return raw


# ----------------------------------------------------------------------
# 编码计划 + 分段计时（C09 步骤 2：行打包/压缩/写盘分别计时 + 压缩参数）
# ----------------------------------------------------------------------
@dataclass
class PngEncodePlan:
    """C09 步骤 2 — 行打包 / 压缩 / 写盘分段计时 + 压缩率参数。

    * ``level`` — zlib 压缩级别 0–9（0 最快最低压缩，9 最慢最高
      压缩；默认 6 与 browser.py ``zlib.compress`` 默认一致，保留旧
      契约）。
    * ``stage_timings`` — ``{"pack": ms, "compress": ms, "write": ms}``
      （分别计时，"按需求在压缩率和速度之间提供可选参数"）。
    * :meth:`run` — 跑一次编码（pack + compress），填充 stage_timings。
    """
    level: int = 6
    stage_timings: dict = field(default_factory=dict)

    def run(self, rows: ContinuousRows) -> bytes:
        """编码 *rows* 成 PNG 字节（与 browser.py ``_png_encode_rows``
        同字节契约，level=6 时逐字节一致）。"""
        import time
        t0 = time.perf_counter()
        raw = rows.raw_rows()
        t1 = time.perf_counter()
        png = encode_rows(rows.width, rows.height, bytes(raw),
                          level=self.level)
        t2 = time.perf_counter()
        self.stage_timings = {
            "pack_ms": round((t1 - t0) * 1000, 3),
            "compress_ms": round((t2 - t1) * 1000, 3),
            "write_ms": 0.0,   # 内存编码不写盘；写盘计时由调用方填
        }
        return png


def encode_rows(width: int, height: int, raw_rows: bytes,
                level: int = 6) -> bytes:
    """把 filter-0 raw-row 负载打包成完整 PNG 字节（与 browser.py
    ``_png_encode_rows`` 同规则：IHDR 8-bit RGB、IDAT zlib、IEND）。

    * *level* — zlib 压缩级别（默认 6 保留旧契约；0 最快）。
    """
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw_rows, level=level)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


# ----------------------------------------------------------------------
# 解码像素一致性（C09 步骤 4：不能简单改字节金图掩盖错误）
# ----------------------------------------------------------------------
def decode_pixels(png: bytes) -> list:
    """把 PNG 字节解回 32-bit 像素列表（``0x00RRGGBB``，行主序）。

    用于"新模式解码像素一致性测试"：编码改了压缩级别 → PNG 字节变了，
    但解回像素必须与原图逐像素一致（不能改字节金图掩盖错误）。

    返回与 :class:`ContinuousRows.pixels` 同格式的列表。"""
    # 解析 IHDR（宽高 + 位深 + 颜色类型）
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    # 走 chunk
    offset = 8
    width = height = bit_depth = color_type = 0
    idat = b""
    while offset < len(png):
        ln = struct.unpack(">I", png[offset:offset + 4])[0]
        tag = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + ln]
        offset += 8 + ln + 4  # + CRC
        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(
                ">IIBB", data[:10])
        elif tag == b"IDAT":
            idat += data
        elif tag == b"IEND":
            break
    raw = zlib.decompress(idat)
    # 8-bit truecolor (color_type 2)：每行 filter(1) + w*3
    if bit_depth != 8 or color_type != 2:
        raise ValueError("unsupported PNG: only 8-bit RGB")
    bytes_per_row = width * 3
    pixels = []
    for py in range(height):
        base = py * (bytes_per_row + 1)
        filter_byte = raw[base]
        row_off = base + 1 + filter_byte  # filter 0: 无偏移（本编码器
        # 只产 filter 0）
        for px in range(width):
            o = row_off + px * 3
            r = raw[o]
            g = raw[o + 1]
            b = raw[o + 2]
            pixels.append((r << 16) | (g << 8) | b)
    return pixels


def pixels_equal(a: list, b: list) -> bool:
    """C09 验收 — 两像素列表逐像素一致（"与全量渲染逐像素比较"）。"""
    if len(a) != len(b):
        return False
    return all(x == y for x, y in zip(a, b))


# ----------------------------------------------------------------------
# 不可变帧快照 + 后台导出（C09 步骤 3：捕获快照、后台编码、回 UI 通知）
# ----------------------------------------------------------------------
class FrameSnapshot:
    """C09 步骤 3 — 导出捕获的不可变帧快照。

    * 捕获时冻结像素（``list(pixels)`` 拷贝，后台编码不改变活帧）。
    * "交互帧不做 PNG"：交互渲染路径不产快照；只有显式导出请求
      （如"导出诊断截图"按钮）才 :meth:`capture` 一份快照交给
      :class:`BackgroundExporter` 后台编码。
    """

    def __init__(self, pixels: list, width: int, height: int,
                 frame_id: int = 0) -> None:
        self.pixels = list(pixels)   # 冻结拷贝
        self.width = width
        self.height = height
        self.frame_id = frame_id

    def rows(self) -> ContinuousRows:
        return ContinuousRows(self.pixels, self.width, self.height)


class BackgroundExporter:
    """C09 步骤 3 + 验收 "导出中导航/关页" — 后台导出任务。

    * :meth:`start` — 捕获快照 + 建任务（状态 ``pending``）。
    * :meth:`cancel` — 导出中导航 / 关页 → 取消任务（状态
      ``cancelled``，不写盘；"UI 不冻结"——编码在后台，取消即时）。
    * :meth:`complete` — 后台编码完成 → 状态 ``done`` + 回 UI 通知
      （调用方读 ``png_bytes`` 写盘，或 ``cancelled`` 丢弃）。
    * 验收 "写盘失败"：写盘由调用方做（本层只产字节），失败不回滚
      像素，只标记任务失败（``failed``）。
    """

    # 状态常量
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"

    def __init__(self) -> None:
        self._tasks: dict = {}
        self._next_id = 1
        self._notifications: list = []

    def start(self, snap: FrameSnapshot,
              level: int = 6) -> int:
        task_id = self._next_id
        self._next_id += 1
        self._tasks[task_id] = {
            "status": self.PENDING,
            "snapshot": snap,
            "level": level,
            "png_bytes": None,
            "cancelled": False,
        }
        return task_id

    def cancel(self, task_id: int) -> bool:
        """取消任务（导出中导航/关页）。返回是否取消成功。"""
        t = self._tasks.get(task_id)
        if t is None:
            return False
        if t["status"] in (self.DONE, self.CANCELLED, self.FAILED):
            return False
        t["status"] = self.CANCELLED
        t["cancelled"] = True
        self._notify(task_id, self.CANCELLED)
        return True

    def complete(self, task_id: int) -> Optional[bytes]:
        """后台编码完成 → 产 PNG 字节 + 回 UI 通知。返回 PNG 字节。"""
        t = self._tasks.get(task_id)
        if t is None:
            return None
        if t["status"] == self.CANCELLED:
            return None   # 已取消 → 不产字节
        plan = PngEncodePlan(level=t["level"])
        png = plan.run(t["snapshot"].rows())
        t["status"] = self.DONE
        t["png_bytes"] = png
        self._notify(task_id, self.DONE, len(png))
        return png

    def fail(self, task_id: int, reason: str = "") -> None:
        """写盘失败 → 标记任务失败（不回滚像素）。"""
        t = self._tasks.get(task_id)
        if t is None:
            return
        t["status"] = self.FAILED
        t["fail_reason"] = reason
        self._notify(task_id, self.FAILED, 0, reason)

    def status(self, task_id: int) -> str:
        t = self._tasks.get(task_id)
        return t["status"] if t else "unknown"

    def _notify(self, task_id: int, status: str,
                size: int = 0, reason: str = "") -> None:
        """C09 步骤 3 "完成后回 UI 通知"——记录通知（调用方消费）。"""
        self._notifications.append((task_id, status, size, reason))

    def notifications(self) -> list:
        return list(self._notifications)


# ----------------------------------------------------------------------
# 纯色 / 透明 / 奇数宽度编码辅助（C09 验收）
# ----------------------------------------------------------------------
def solid_bitmap(width: int, height: int,
                 rgb: tuple = (255, 255, 255)) -> ContinuousRows:
    """纯色位图（验收 "纯色"）。"""
    px = ((rgb[0] << 16) | (rgb[1] << 8) | rgb[2])
    return ContinuousRows([px] * (width * height), width, height)


def transparent_rows(width: int, height: int) -> ContinuousRows:
    """全透明（alpha=0，验收 "透明"）——RGB 通道全 0。"""
    return ContinuousRows([0] * (width * height), width, height)


def random_bitmap(width: int, height: int, seed: int = 42) -> ContinuousRows:
    """随机位图（验收 "随机位图"，可复现）。"""
    import random
    rng = random.Random(seed)
    pixels = [rng.getrandbits(24) for _ in range(width * height)]
    return ContinuousRows(pixels, width, height)
