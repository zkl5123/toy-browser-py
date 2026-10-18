"""C02 — 连续像素缓冲区及 GUI 呈现（第一阶段：统一数据契约）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C02：

1. 定义 ``CPUImage(width, height, stride, format, buffer)``，buffer
   使用 ``bytearray`` 连续存储（不再每帧从 int 列表还原）。
2. 保留 ``Bitmap.pixels`` 兼容：``CPUImage.from_bitmap`` /
   ``to_ppm_bytes`` 做一次性转换；后续批次才逐阶段迁移填充、
   混合、图片与字形到连续缓冲区。
3. ``to_ppm_bytes`` 用 ``struct`` 批量打包（避免逐像素 Python
   临时 bytes —— 第五轮实测 800×600 转换链约 1209ms 的根因）；
   输出 P6 二进制 PPM，Tk ``PhotoImage`` 可直接消费。
4. 格式：``"RGB8"``（3 字节/像素，little-endian）为 GUI 呈现
   契约；stride = width * 3。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


# ----------------------------------------------------------------------
# CPUImage
# ----------------------------------------------------------------------

@dataclass
class CPUImage:
    """C02 — 连续像素缓冲区。

    * ``format`` — ``"RGB8"``（3 字节/像素，小端：B,G,R 顺序为
      平台相关；这里固定 **RGB 小端**）。
    * ``buffer`` — ``bytearray``（或任何 buffer-protocol 对象），
      长度至少 ``stride * height``。
    * ``stride`` — 每行字节数（RGB8 下 = ``width * 3``；可加
      padding）。
    """

    width: int
    height: int
    format: str = "RGB8"
    buffer: bytearray = field(default_factory=bytearray)
    stride: int = 0

    _BYTES_PER_PIXEL = 3  # RGB8

    def __post_init__(self) -> None:
        if self.stride == 0:
            self.stride = self.width * self._BYTES_PER_PIXEL
        if len(self.buffer) < self.stride * self.height:
            # 扩容（保留已有前缀内容，其余补 0）
            need = self.stride * self.height
            self.buffer = bytearray(self.buffer)[:need] + \
                bytearray(need - min(len(self.buffer), need))

    # ------------------------------------------------------------------
    # 像素访问（CPU 测试 / 验证用；不是热路径）
    # ------------------------------------------------------------------
    def get_pixel(self, x: int, y: int) -> tuple[int, int, int]:
        """RGB8 (r, g, b) — PPM byte order R,G,B (clamp 越界)."""
        x = max(0, min(self.width - 1, int(x)))
        y = max(0, min(self.height - 1, int(y)))
        i = y * self.stride + x * self._BYTES_PER_PIXEL
        return (self.buffer[i], self.buffer[i + 1], self.buffer[i + 2])

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int,
                  a: int = 255) -> None:
        """按 (r, g, b, a) 设置像素（越界 clamp 到 buffer 边界；
        a=255 不透明直写，半透明 src-over 混合，与 ``Bitmap.set_pixel``
        的 blend_over 同公式（255 归一：``r*a + dr*(255-a)``）。"""
        x = int(x)
        y = int(y)
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        i = y * self.stride + x * 3
        dr, dg, db = self.buffer[i], self.buffer[i + 1], self.buffer[i + 2]
        if a == 255:
            self.buffer[i] = r & 0xFF
            self.buffer[i + 1] = g & 0xFF
            self.buffer[i + 2] = b & 0xFF
            return
        # 半透明 src-over（与 Color.blend_over 同公式：
        # min(255, int(r*(a/255) + dr*(1-a/255)))）。
        sa = a / 255.0
        self.buffer[i] = min(255, int(r * sa + dr * (1.0 - sa))) & 0xFF
        self.buffer[i + 1] = min(255, int(g * sa + dg * (1.0 - sa))) & 0xFF
        self.buffer[i + 2] = min(255, int(b * sa + db * (1.0 - sa))) & 0xFF

    # ------------------------------------------------------------------
    # 从 Bitmap 转换（0xRRGGBB int 列表 → 连续 RGB8）
    # ------------------------------------------------------------------
    @classmethod
    def from_bitmap(cls, bmp, stride: int = 0) -> "CPUImage":
        """C02 — 从 :class:`paint.Bitmap`（``pixels`` 为 0xRRGGBB
        int 列表）构建 RGB8 连续缓冲区。

        用 ``struct.pack`` 批量打包（每行一次），避免逐像素
        Python 临时 bytes。
        """
        w, h = bmp.width, bmp.height
        pixels = bmp.pixels
        img = cls(width=w, height=h,
                  stride=stride or w * cls._BYTES_PER_PIXEL)
        flat = bytearray()
        # 逐行打包（struct 一次最多 4096 个字节；按行切分安全）
        pack = struct.pack
        fmt = "<" + "B" * (3 * w)
        for y in range(h):
            row = pixels[y * w:(y + 1) * w]
            # 0xRRGGBB → P6 PPM 的 RGB 字节序（R 先，与 Bitmap 兼容）
            flat += pack(fmt,
                         *[v for px in row
                           for v in ((px >> 16) & 0xFF, (px >> 8) & 0xFF,
                                     px & 0xFF)])
        img.buffer = flat
        return img

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def to_ppm_bytes(self) -> bytes:
        """P6 二进制 PPM 字节（Tk ``PhotoImage -data`` 可直接消费）。

        C02 — 批量构造，无逐像素 Python 临时 bytes。
        """
        header = f"P6\n{self.width} {self.height}\n255\n".encode("ascii")
        # RGB8 stride 紧凑时 body 就是 buffer 前 stride*height 字节
        body = bytes(self.buffer[:self.stride * self.height])
        return header + body

    def save_ppm(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.to_ppm_bytes())

    # ------------------------------------------------------------------
    # C09 批次 43 — 连续行 PNG 编码（直接读 RGB8 buffer，消除 int 列表）
    # ------------------------------------------------------------------
    def raw_rows(self) -> bytearray:
        """C09 步骤 1 — filter-0 raw-row 负载（每行 1 字节 filter(0) +
        stride*? 实际 w*3 字节 RGB），**直接切片 RGB8 buffer**（不还原
        0xRRGGBB int 列表，与 :class:`browser.ContinuousRows` 的 int
        列表路径逐字节一致）。"""
        w, h = self.width, self.height
        stride = self.stride
        buf = self.buffer
        bpr = w * 3  # bytes per row（RGB8）
        raw = bytearray(h * (bpr + 1))
        off = 0
        for py in range(h):
            raw[off] = 0  # filter type: none
            off += 1
            sbase = py * stride
            raw[off:off + bpr] = buf[sbase:sbase + bpr]
            off += bpr
        return raw

    def to_png_bytes(self, level: int = 6) -> bytes:
        """C09 批次 43 — 把 RGB8 buffer 编码成 8-bit RGB PNG 字节
        （zlib 压缩级别 *level*，默认 6 与 :func:`browser.write_png`
        逐字节一致；C09 步骤 1 "直接读连续 RGB 行，消除导出时再次
        遍历 Python int 列表"）。"""
        from toy_browser.app.png_export import encode_rows
        raw = self.raw_rows()
        return encode_rows(self.width, self.height, bytes(raw),
                           level=level)

    def save_png(self, path: str, level: int = 6) -> None:
        with open(path, "wb") as f:
            f.write(self.to_png_bytes(level=level))

    def nonwhite_count(self) -> int:
        """C02 — 非白像素计数（与 :meth:`paint.Bitmap.nonwhite` 同
        语义；供 CLI 输出 ``nonwhite px`` 统计，不还原 int 列表）。"""
        buf = self.buffer
        stride = self.stride
        n = 0
        for py in range(self.height):
            base = py * stride
            for px in range(self.width):
                i = base + px * 3
                if buf[i] != 0xFF or buf[i + 1] != 0xFF \
                        or buf[i + 2] != 0xFF:
                    n += 1
        return n

    def to_view(self) -> "CpuImageBitmapView":
        """批次 45 — 返回 :class:`CpuImageBitmapView`（``paint.Bitmap``
        的 0xRRGGBB int 列表**视图**，直接包裹本 buffer，不复制）。

        供 ``paint_engine`` / ``typography`` 既有命令分派直接画到
        :class:`CPUImage`（消除 scratch Bitmap 合并层——C02 目标
        "避免每次呈现又从连续缓冲区还原大 int 列表" 的**写侧**）：
        所有经 :meth:`pixels` 属性的读写都直接落到 RGB8 buffer。
        注意：视图的 ``width`` / ``height`` 与 buffer 一致，但
        :meth:`save_ppm` / :meth:`to_ppm_bytes` 等仍走 :class:`Bitmap`
        的 int 列表语义（视图按需还原）；优先用 :meth:`CPUImage` 自身的
        ``to_ppm_bytes`` / ``to_png_bytes`` 等 buffer 直接路径。"""
        return CpuImageBitmapView(self)

    # ------------------------------------------------------------------
    # 尺寸 / 校验
    # ------------------------------------------------------------------
    @property
    def size(self) -> int:
        return self.stride * self.height

    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0


# ----------------------------------------------------------------------
# 批次 45 — Bitmap 兼容视图（直接画到 CPUImage RGB8 buffer）
# ----------------------------------------------------------------------

class CpuImageBitmapView:
    """批次 45 — :class:`paint.Bitmap` 的 **0xRRGGBB int 列表视图**，
    直接包裹 :class:`CPUImage` 的 RGB8 buffer（不复制、不还原大 int
    列表）。

    * ``img`` — 底层 :class:`CPUImage`。
    * ``width`` / ``height`` — 与 ``img`` 一致。
    * ``pixels`` — 返回**可写**的 :class:`_Rgb8U32List`（0xRRGGBB 语义，
      读写直接落到 ``img.buffer``）。

    供 ``paint_engine`` / ``typography`` 既有命令分派（按 ``bmp.pixels``
    int 列表读写的 ``draw_text`` / ``_fill_polygon`` / ``_paint_img_pixels``
    等）直接画到 :class:`CPUImage`——消除 scratch Bitmap 合并层。
    兼容 :meth:`Bitmap.set_pixel` / :meth:`Bitmap.pixel_at` 同语义（越界
    clamp / 不透明直写 / 半透明 blend_over）。
    """

    def __init__(self, img: CPUImage) -> None:
        self.img = img
        self.width = img.width
        self.height = img.height

    @property
    def pixels(self):
        """返回 :class:`_Rgb8U32List`（0xRRGGBB int 列表语义，可写，
        直接落到 RGB8 buffer）。"""
        if not hasattr(self, "_view"):
            self._view = _Rgb8U32List(self.img)
        return self._view

    def set_pixel(self, x: int, y: int, color: "Color") -> None:
        """与 :meth:`Bitmap.set_pixel` 同语义（越界 clamp + blend_over）。

        直接经 :meth:`CPUImage.set_pixel` 落到 RGB8 buffer（半透明走
        与 :class:`Color.blend_over` 同公式的 src-over 混合，保证与
        Bitmap 路径逐像素一致）。"""
        xi = int(x)
        yi = int(y)
        if xi < 0 or yi < 0 or xi >= self.width or yi >= self.height:
            return
        if color.a == 255:
            self.img.set_pixel(xi, yi, color.r, color.g, color.b, 255)
            return
        dr, dg, db = self.img.get_pixel(xi, yi)
        v = (dr << 16) | (dg << 8) | db
        nv = color.blend_over(v)
        self.img.set_pixel(xi, yi, (nv >> 16) & 0xFF, (nv >> 8) & 0xFF,
                           nv & 0xFF, 255)

    def pixel_at(self, x: int, y: int) -> tuple:
        """与 :meth:`Bitmap.pixel_at` 同语义（返回 (r,g,b) clamp 越界）。"""
        return self.img.get_pixel(x, y)

    def fill_rect(self, x: float, y: float, w: float, h: float,
                  color: "Color") -> None:
        """与 :meth:`Bitmap.fill_rect` 同语义（越界 clamp + 不透明快
        路径 / 半透明 blend_over），直接落 RGB8 buffer。"""
        from toy_browser.rendering.cpu_image_fill import fill_rect_rgb8
        fill_rect_rgb8(self.img, x, y, w, h,
                       color.r, color.g, color.b, color.a)

    def to_bitmap(self):
        """转 :class:`paint.Bitmap`（0xRRGGBB int 列表）——按需一次性
        还原（供需要独立 int 列表的消费端；视图本身零复制）。"""
        from toy_browser.rendering.paint import Bitmap, WHITE
        bmp = Bitmap(self.width, self.height, WHITE)
        view = self.pixels
        for i in range(self.width * self.height):
            bmp.pixels[i] = view[i]
        return bmp


class _Rgb8U32List:
    """批次 45 — 0xRRGGBB int 列表的**可写视图**，直接包裹
    :class:`CPUImage` 的 RGB8 buffer（索引语义与 ``paint.Bitmap.pixels``
    一致：``idx = y * width + x``，元素为 0x00RRGGBB u32）。

    * ``__getitem__`` / ``__setitem__`` / ``__len__`` / ``__iter__`` /
      切片（整行读，写回 buffer；整行写，逐元素解包写 buffer）。

    供 ``paint_engine`` / ``typography`` 既有命令分派（按
    ``bmp.pixels[idx]`` 读写）直接画到 :class:`CPUImage`。
    """

    def __init__(self, img: CPUImage) -> None:
        self._img = img
        self._w = img.width
        self._h = img.height
        self._stride = img.stride
        self._buf = img.buffer

    def __len__(self) -> int:
        return self._w * self._h

    def __getitem__(self, i):
        if isinstance(i, slice):
            start, stop, step = i.indices(self._w * self._h)
            if step != 1:
                return [self[k] for k in range(start, stop, step)]
            out = []
            buf = self._buf
            stride = self._stride
            for k in range(start, stop):
                py, px = divmod(k, self._w)
                off = py * stride + px * 3
                out.append((buf[off] << 16) | (buf[off + 1] << 8)
                           | buf[off + 2])
            return out
        py, px = divmod(int(i), self._w)
        off = py * self._stride + px * 3
        buf = self._buf
        return (buf[off] << 16) | (buf[off + 1] << 8) | buf[off + 2]

    def __setitem__(self, i, value) -> None:
        buf = self._buf
        stride = self._stride
        if isinstance(i, slice):
            start, stop, step = i.indices(self._w * self._h)
            if step != 1 or not isinstance(value, (list, tuple)):
                raise TypeError("_Rgb8U32List slice 赋值需连续 step=1 序列")
            if len(value) != stop - start:
                raise ValueError("切片长度不匹配")
            for k in range(start, stop):
                py, px = divmod(k, self._w)
                off = py * stride + px * 3
                v = value[k - start] & 0xFFFFFF
                buf[off] = (v >> 16) & 0xFF
                buf[off + 1] = (v >> 8) & 0xFF
                buf[off + 2] = v & 0xFF
            return
        py, px = divmod(int(i), self._w)
        off = py * stride + px * 3
        v = int(value) & 0xFFFFFF
        buf[off] = (v >> 16) & 0xFF
        buf[off + 1] = (v >> 8) & 0xFF
        buf[off + 2] = v & 0xFF

    def __iter__(self):
        for k in range(self._w * self._h):
            yield self[k]

    def __repr__(self) -> str:
        return f"_Rgb8U32List({self._w}x{self._h})"


# ----------------------------------------------------------------------
# 缓冲池（C02 step 4：按尺寸复用；帧所有权由调用方明确）
# ----------------------------------------------------------------------

class ImagePool:
    """C02 — 按 (width, height, stride) 复用 CPUImage 的有界池。

    ``get`` 返回池内空闲图像（清零）或新建；``put`` 归还。
    池有上限（``max_size``），超出的直接丢弃（避免无界内存增长）。
    所有权规则：``get`` 之后到 ``put`` 之前，调用方独占 buffer；
    UI 读取时工作线程不得仍在改图（由 C01 帧版本保证）。
    """

    def __init__(self, max_size: int = 8) -> None:
        self.max_size = max_size
        self._pool: dict[tuple[int, int, int], list[CPUImage]] = {}
        self._total = 0

    def get(self, width: int, height: int,
            stride: int = 0) -> CPUImage:
        key = (width, height, stride or width * CPUImage._BYTES_PER_PIXEL)
        lst = self._pool.get(key)
        if lst:
            img = lst.pop()
            img.buffer[:] = b"\x00" * len(img.buffer)  # 清零
            return img
        img = CPUImage(width, height, stride=stride)
        self._total += 1
        return img

    def put(self, img: CPUImage) -> None:
        key = (img.width, img.height, img.stride)
        lst = self._pool.setdefault(key, [])
        if len(lst) < self.max_size:
            lst.append(img)
        # 超上限的图像直接丢弃（GC 回收 buffer）

    def __len__(self) -> int:
        return sum(len(v) for v in self._pool.values())
