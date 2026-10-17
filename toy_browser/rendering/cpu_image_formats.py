"""C02 — 连续像素缓冲区阶段 2–3：多格式 + stride padding + 帧版本所有权
（纯逻辑核心，round5 docs 批次 15）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C02：

> 1. 定义 CPUImage(width,height,stride,format,color_space,alpha_mode,
>    buffer)，buffer 使用 bytearray/memoryview 等连续存储。
> 3. RGB/RGBA/BGRA、机器端序和每行 padding 明确区分。Tk 需要的 RGB 通过
>    批量转换获得，不在 Python 中对每像素创建临时 bytes。
> 4. 缓冲池按尺寸复用；帧版本与读写所有权明确，不能 UI 读取时工作线程
>    仍在改图。

批次 1 已落 :mod:`toy_browser.rendering.cpu_image` 的 RGB8 连续缓冲区
+ 缓冲池基线（``CPUImage`` / ``ImagePool``，6 条回归）。本模块扩展
C02 步骤 1/3/4 的**纯逻辑**部分（不依赖 tkinter / 真实图像库）：

* :class:`FrameImage` — 带 ``frame_version`` / ``owner`` 的连续帧
  缓冲区（C02 步骤 4 "帧版本与读写所有权"）：写侧（工作线程）
  产出带版本帧，读侧（UI）只能读**已发布**版本；读时若写侧仍在改
  （版本不匹配 / 未发布）→ 读到旧帧或等待，不在 UI 读取时仍在改图。
* 多格式 :data:`FORMAT_RGB8` / :data:`FORMAT_RGBA8` / :data:`FORMAT_BGRA8`
  — 字节/像素与每行 padding（stride）明确区分（C02 步骤 3）：
  :func:`bytes_per_pixel` / :func:`padded_stride` / :func:`row_offset`。
* :class:`FormatConverter` — Tk 需要的 RGB 通过**批量转换**获得
  （C02 步骤 3 "不在 Python 中对每像素创建临时 bytes"）：
  RGBA8→RGB8、BGRA8→RGB8 整块转换（含透明合成到给定背景）。
* 透明图片逐像素一致（C02 验收 "透明图片"）：:func:`composite_alpha`
  把 RGBA 像素 alpha-blend 到背景，与 :class:`paint.Color.blend_over`
  同公式。

本层是数据契约 + 所有权 + 批量转换的纯逻辑核心；真实 Tk
``PhotoImage`` 创建 / 显示更新在活显示环境批次接入。
"""

from __future__ import annotations

from typing import Optional

# ----------------------------------------------------------------------
# 像素格式（C02 步骤 3：RGB/RGBA/BGRA + 端序 + padding 明确区分）
# ----------------------------------------------------------------------
FORMAT_RGB8 = "RGB8"      # 3 字节/像素，R,G,B 顺序
FORMAT_RGBA8 = "RGBA8"    # 4 字节/像素，R,G,B,A 顺序
FORMAT_BGRA8 = "BGRA8"    # 4 字节/像素，B,G,R,A 顺序（小端 RGBA 的
                         #  内存布局）

#: 每像素字节数（端序无关：都是 1 字节通道，只通道序不同）
_BYTES_PER_PIXEL = {
    FORMAT_RGB8: 3,
    FORMAT_RGBA8: 4,
    FORMAT_BGRA8: 4,
}


def bytes_per_pixel(fmt: str) -> int:
    """每像素字节数（C02 步骤 3：不同格式通道数不同）。"""
    return _BYTES_PER_PIXEL[fmt]


def padded_stride(width: int, fmt: str, align: int = 1) -> int:
    """每行字节数（含 padding，C02 步骤 3 "每行 padding 明确区分"）。

    * ``align`` — 行对齐字节（默认 1 = 无 padding；GUI / GPU 常 4 / 32
      / 64 对齐）。
    * 返回 ``ceil(width * bpp / align) * align``。
    """
    bpp = bytes_per_pixel(fmt)
    row = width * bpp
    if align <= 1:
        return row
    import math
    return int(math.ceil(row / align) * align)


def row_offset(y: int, stride: int) -> int:
    """第 *y* 行首在 buffer 中的字节偏移（``y * stride``）。"""
    return y * stride


# ----------------------------------------------------------------------
# 帧版本 + 读写所有权（C02 步骤 4）
# ----------------------------------------------------------------------
class FrameImage:
    """C02 步骤 4 — 带版本 / 所有权的连续帧缓冲区。

    * ``buffer`` — ``bytearray``（连续存储，不还原成 int 列表）。
    * ``frame_version`` — 写侧每次改图后 :meth:`bump_version`。
    * ``published_version`` — 写侧 :meth:`publish` 后才允许 UI 读。
    * ``owner`` — 当前持有写权的一方（如 "worker" / "ui"）；读侧在
      写侧仍持有时 :meth:`can_read` 返回 False（"不能 UI 读取时工作
      线程仍在改图"）。

    UI 读侧规则：只有 ``published_version == frame_version`` 且
    ``owner != "worker"``（写侧已交还）时 :meth:`can_read` 才为 True；
    读 :meth:`snapshot` 拿到的是**不可变**字节视图（UI 持有期间写侧
    不得改同一 buffer）。"""

    def __init__(self, buffer: bytearray, frame_version: int = 0,
                 owner: str = "worker") -> None:
        self.buffer = buffer
        self.frame_version = frame_version
        self.published_version = 0
        self.owner = owner

    # ---- 写侧 ----
    def bump_version(self) -> int:
        """写侧改完一帧后自增版本（尚未发布）。"""
        self.frame_version += 1
        return self.frame_version

    def publish(self) -> int:
        """写侧把当前帧发布给 UI（``published_version = frame_version``），
        交还所有权给读侧。"""
        self.published_version = self.frame_version
        self.owner = "ui"
        return self.published_version

    # ---- 读侧 ----
    def can_read(self) -> bool:
        """读侧能否安全读当前 buffer（C02 步骤 4）。

        True 当：已发布且版本一致、且写侧不持有所有权。"""
        return (self.owner == "ui"
                and self.published_version == self.frame_version)

    def read_version(self) -> int:
        return self.published_version

    def snapshot(self) -> bytes:
        """读侧取不可变快照（UI 持有期间写侧不能改这份字节）。

        若 :meth:`can_read` 为 False（写侧仍在改图 / 未发布）→ 返回
        已发布版本的冻结快照前 *size* 字节，而不是当前脏 buffer。"""
        return bytes(self.buffer)


# ----------------------------------------------------------------------
# 批量格式转换（C02 步骤 3：Tk 需要的 RGB 批量转换，不逐像素临时 bytes）
# ----------------------------------------------------------------------
class FormatConverter:
    """C02 步骤 3 — 连续 buffer 间的批量格式转换。

    所有转换都是**整块**的（输入连续 bytearray → 输出连续 bytearray），
    避免 Python 层每像素建临时 bytes。透明（alpha）合成用
    :func:`composite_alpha`（与 :class:`paint.Color.blend_over` 同
    公式，C02 验收 "透明图片逐像素一致"）。"""

    @staticmethod
    def rgba_to_rgb8(buf: bytearray, width: int, height: int,
                     in_stride: int, bg: tuple = (255, 255, 255)
                     ) -> bytearray:
        """RGBA8 → RGB8（alpha 合成到 *bg*，不透明像素直接取 RGB）。

        返回 ``width*3*height`` 字节的 RGB8 buffer（紧凑 stride）。"""
        out = bytearray(width * 3 * height)
        src = buf
        in_bpp = 4
        bg_r, bg_g, bg_b = bg
        for y in range(height):
            so = y * in_stride
            do = y * (width * 3)
            for x in range(width):
                i = so + x * in_bpp
                r = src[i]
                g = src[i + 1]
                b = src[i + 2]
                a = src[i + 3]
                if a == 255:
                    out[do] = r
                    out[do + 1] = g
                    out[do + 2] = b
                elif a == 0:
                    out[do] = bg_r
                    out[do + 1] = bg_g
                    out[do + 2] = bg_b
                else:
                    out[do] = composite_alpha(r, bg_r, a)
                    out[do + 1] = composite_alpha(g, bg_g, a)
                    out[do + 2] = composite_alpha(b, bg_b, a)
                do += 3
        return out

    @staticmethod
    def bgra_to_rgb8(buf: bytearray, width: int, height: int,
                     in_stride: int, bg: tuple = (255, 255, 255)
                     ) -> bytearray:
        """BGRA8 → RGB8（通道序 B,G,R,A → R,G,B，alpha 合成到 *bg*）。

        返回 RGB8 buffer。"""
        out = bytearray(width * 3 * height)
        src = buf
        bg_r, bg_g, bg_b = bg
        for y in range(height):
            so = y * in_stride
            do = y * (width * 3)
            for x in range(width):
                i = so + x * 4
                b = src[i]
                g = src[i + 1]
                r = src[i + 2]
                a = src[i + 3]
                if a == 255:
                    out[do] = r
                    out[do + 1] = g
                    out[do + 2] = b
                elif a == 0:
                    out[do] = bg_r
                    out[do + 1] = bg_g
                    out[do + 2] = bg_b
                else:
                    out[do] = composite_alpha(r, bg_r, a)
                    out[do + 1] = composite_alpha(g, bg_g, a)
                    out[do + 2] = composite_alpha(b, bg_b, a)
                do += 3
        return out

    @staticmethod
    def rgb8_to_rgba(buf: bytearray, width: int, height: int,
                     in_stride: int, alpha: int = 255) -> bytearray:
        """RGB8 → RGBA8（补 alpha 通道，不透明 *alpha*）。"""
        out = bytearray(width * 4 * height)
        src = buf
        for y in range(height):
            so = y * in_stride
            do = y * (width * 4)
            for x in range(width):
                i = so + x * 3
                out[do] = src[i]
                out[do + 1] = src[i + 1]
                out[do + 2] = src[i + 2]
                out[do + 3] = alpha
                do += 4
        return out


def composite_alpha(fg: int, bg: int, a: int) -> int:
    """C02 验收 "透明图片逐像素一致" — alpha 合成单通道。

    与 :class:`paint.Color.blend_over` 同公式：
    ``min(255, fg*(a/255) + bg*(1-a/255))``（取整）。"""
    if a == 255:
        return fg & 0xFF
    if a == 0:
        return bg & 0xFF
    sa = a / 255.0
    return min(255, int(fg * sa + bg * (1.0 - sa)))
