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

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        i = y * self.stride + x * self._BYTES_PER_PIXEL
        self.buffer[i] = r & 0xFF
        self.buffer[i + 1] = g & 0xFF
        self.buffer[i + 2] = b & 0xFF

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
    # 尺寸 / 校验
    # ------------------------------------------------------------------
    @property
    def size(self) -> int:
        return self.stride * self.height

    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0


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
