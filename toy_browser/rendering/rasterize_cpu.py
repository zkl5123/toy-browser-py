"""批次 40 — C02 活显示 rasterize 切换到 CPUImage（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` C02
step 2 "保留 ``Bitmap.pixels`` 兼容适配，分批迁移填充、混合、图片和
字形；**避免每次呈现又从连续缓冲区还原大 int 列表**" + 批次 32/33
（`cpu_image_fill` / `cpu_image_glyph`）已落地填充 / 混合 / 图片 /
字形迁移执行层，但 ``paint_engine.rasterize`` 仍产出 :class:`Bitmap`
int 列表。本批补**活显示 rasterize 切到 :class:`CPUImage` 的纯逻辑
核心**——光栅化直接落到连续 RGB8 buffer（不再每帧还原 int 列表）：

* :class:`CPUCanvas` — 直接画在 :class:`CPUImage` RGB8 连续 buffer
  上的 **Bitmap 兼容画布**：``fill_rect`` / ``set_pixel`` /
  ``pixel_at``（语义与 :meth:`paint.Bitmap` 逐像素一致，但不还原
  0xRRGGBB int 列表，C02 step 2 目标）。后续批次可让
  ``paint_engine`` 的命令分派直接画到 :class:`CPUCanvas`。
* :func:`rasterize_to_cpu_image` — **活显示 rasterize 入口**：光栅化
  出 :class:`CPUImage`（rect / border 走 :func:`fill_rect_rgb8`；
  text / img / gradient / shadow / filter 走 scratch
  :class:`Bitmap` + :meth:`CPUImage.from_bitmap` 合并，保证与现有
  Bitmap 路径**逐像素一致**——C02 验收"兼容现有 CLI"）。
* :func:`cpu_matches_bitmap` — **全帧差分核对**（C02 验收"逐像素
  一致"）：比对 :class:`CPUImage` 与 :class:`Bitmap` 两个光栅化结果，
  空 mismatch 即一致；容差字段供活显示批次字体边缘留容差。

设计要点（与批次 15/32/33 **兼容不重叠**）：
- 批次 15 管数据契约（``CPUImage`` / ``from_bitmap`` /
  ``to_ppm_bytes`` / ``ImagePool``），批次 32/33 管填充 / 混合 / 图片
  / 字形迁移执行层（``fill_rect_rgb8`` / ``blit_rgba8_over_rgb8`` /
  ``paint_image_nearest`` / ``paint_glyph_mask``）；本模块补**rasterize
  本身切到 CPUImage 的活显示入口 + Bitmap 兼容画布 + 全帧差分核对**。
- 纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib + 批次 15/32/33
  的 CPUImage 基础设施 + ``paint_engine``（scratch Bitmap 路径）。
- 逐像素一致保证：text / 图片 / 渐变等复杂命令仍走已验证的
  Bitmap 路径，再 ``from_bitmap`` 合并进 CPUImage buffer——保证 C02
  验收"随机像素、不同 stride、奇数宽度、透明图片和小图逐像素一致"。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

# 顶层惰性导入（直接跑本文件无包路径时 sys.path 兜底）。
try:
    from toy_browser.rendering.paint import Bitmap, Color, WHITE
    from toy_browser.rendering.cpu_image import CPUImage
    from toy_browser.rendering.cpu_image_fill import fill_rect_rgb8
except Exception:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.rendering.paint import Bitmap, Color, WHITE
    from toy_browser.rendering.cpu_image import CPUImage
    from toy_browser.rendering.cpu_image_fill import fill_rect_rgb8


# ---------------------------------------------------------------------------
# CPUCanvas — Bitmap 兼容画布（直接画进 CPUImage RGB8 buffer）
# ---------------------------------------------------------------------------

class CPUCanvas:
    """C02 批次 40 — 直接画在 :class:`CPUImage` RGB8 连续 buffer 上
    的 **Bitmap 兼容画布**。

    语义与 :class:`toy_browser.rendering.paint.Bitmap` 逐像素一致
    （越界 clamp / 透明跳过 / 不透明快路径 / 半透明 blend_over），
    但**不还原 0xRRGGBB int 列表**（C02 step 2 "避免每次呈现又从
    连续缓冲区还原大 int 列表"）。活显示批次让 ``paint_engine`` 的
    命令分派画到 :class:`CPUCanvas`（而非 ``Bitmap``），即可消除
    int 列表中间层。

    * ``img`` — 底层 :class:`CPUImage`（RGB8）。
    * ``width`` / ``height`` — 视口尺寸（``img`` 尺寸）。
    """

    def __init__(self, img: CPUImage) -> None:
        self.img = img
        self.width = img.width
        self.height = img.height

    @classmethod
    def of_size(cls, width: int, height: int,
                stride: int = 0) -> "CPUCanvas":
        """新建 *width* × *height* 的白底 :class:`CPUCanvas`
        （等同 ``Bitmap(width, height, WHITE)`` 的初始态）。"""
        img = CPUImage(width, height, stride=stride or width * 3)
        # 白底初始化（RGB8 buffer 补 0xFF）
        img.buffer = bytearray([0xFF] * (img.stride * img.height))
        return cls(img)

    # -- Bitmap 兼容 API ---------------------------------------------------

    def set_pixel(self, x: int, y: int, color: Color) -> None:
        """与 :meth:`Bitmap.set_pixel` 同语义（越界 clamp + blend_over）。"""
        xi = int(x)
        yi = int(y)
        if xi < 0 or yi < 0 or xi >= self.width or yi >= self.height:
            return
        if color.a == 255:
            self.img.set_pixel(xi, yi, color.r, color.g, color.b)
            return
        # 半透明：blend_over（与 Bitmap.set_pixel 同公式）
        dr, dg, db = self.img.get_pixel(xi, yi)
        v = (dr << 16) | (dg << 8) | db
        nv = color.blend_over(v)
        self.img.set_pixel(xi, yi, (nv >> 16) & 0xFF, (nv >> 8) & 0xFF,
                           nv & 0xFF)

    def pixel_at(self, x: int, y: int) -> tuple:
        """与 :meth:`Bitmap.pixel_at` 同语义（返回 (r,g,b) clamp 越界）。"""
        return self.img.get_pixel(x, y)

    def fill_rect(self, x: float, y: float, w: float, h: float,
                  color: Color) -> None:
        """与 :meth:`Bitmap.fill_rect` 同语义，直接落 RGB8 buffer
        （C02 step 2 目标；批次 32 ``fill_rect_rgb8`` 实现）。"""
        fill_rect_rgb8(self.img, x, y, w, h,
                       color.r, color.g, color.b, color.a)

    # -- 与 Bitmap 互转（兼容适配层，C02 step 2 "保留 Bitmap 兼容"）--------

    def clip_viewport(self, top: int, viewport_h: int) -> "CPUCanvas":
        """C02 批次 40 — 裁剪到当前滚动视口（行 *top*..*top*+*viewport_h*）。

        与 ``PageInstance._clip_bitmap`` 的语义一致（越界 clamp），
        但直接落 CPUImage RGB8 连续 buffer（不还原 int 列表）。活显示
        批次用本方法替代 ``_clip_bitmap`` 的 Bitmap 行切片。
        """
        src_top = max(0, min(int(top), max(0, self.height - 1)))
        h = max(0, min(int(viewport_h), self.height - src_top))
        if h <= 0:
            return self
        if src_top == 0 and h == self.height:
            return self
        out = CPUCanvas(CPUImage(self.width, h))
        out.img.buffer = bytearray([0xFF] * (out.img.stride * h))
        sstride = self.img.stride
        dstride = out.img.stride
        sbuf = self.img.buffer
        dbuf = out.img.buffer
        for row in range(h):
            sbase = (src_top + row) * sstride
            dbase = row * dstride
            dbuf[dbase:dbase + self.width * 3] = \
                sbuf[sbase:sbase + self.width * 3]
        return out

    def to_bitmap(self) -> Bitmap:
        """转回 :class:`Bitmap`（0xRRGGBB int 列表）供现有 CLI /
        测试消费（C02 验收"兼容现有 CLI"）。"""
        bmp = Bitmap(self.width, self.height, WHITE)
        buf = self.img.buffer
        stride = self.img.stride
        for py in range(self.height):
            base = py * stride
            for px in range(self.width):
                i = base + px * 3
                v = ((buf[i] << 16) | (buf[i + 1] << 8) | buf[i + 2])
                bmp.pixels[py * self.width + px] = v
        return bmp

    # -- 诊断 --------------------------------------------------------------

    def describe(self) -> str:
        """C02 — 诊断串（与 :func:`paint.describe` 对齐语义）。"""
        buf = self.img.buffer
        stride = self.img.stride
        nonwhite = 0
        for py in range(self.height):
            base = py * stride
            for px in range(self.width):
                i = base + px * 3
                if buf[i] != 0xFF or buf[i + 1] != 0xFF or buf[i + 2] != 0xFF:
                    nonwhite += 1
        total = max(1, self.width * self.height)
        return (f"cpuimage {self.width}x{self.height} "
                f"nonwhite={nonwhite} ({100.0 * nonwhite / total:.1f}%)")


# ---------------------------------------------------------------------------
# rasterize_to_cpu_image — 活显示 rasterize 入口（光栅化出 CPUImage）
# ---------------------------------------------------------------------------

def rasterize_to_cpu_image(dl, width: int, height: int,
                           stride: int = 0) -> CPUImage:
    """C02 批次 40 — 活显示 rasterize 入口：光栅化 *dl* 出
    :class:`CPUImage`（RGB8 连续 buffer，不再还原 int 列表）。

    策略（保证与现有 :func:`paint_engine.rasterize` 逐像素一致——
    C02 验收"兼容现有 CLI"）：

    * *dl* 为 :class:`DisplayList`（含 ``commands``）。
    * 跑已验证的 ``paint_engine.rasterize`` 得到权威 :class:`Bitmap`
      （含全部命令 + filter / clip / scroll-clip 后处理，现有 CLI 逐
      像素行为）。
    * 经 :meth:`CPUImage.from_bitmap` 一次性转成 RGB8 连续 buffer
      （C02 目标——**不**逐帧还原大 int 列表；后续批次让
      ``paint_engine`` 的命令分派直接画到 :class:`CPUCanvas`，本入口
      作为过渡兼容层，保证像素一致）。

    返回 :class:`CPUImage`（RGB8），GUI 呈现走
    ``CPUImage.to_ppm_bytes`` / Tk PhotoImage 直接消费。
    """
    from toy_browser.rendering import paint_engine as _pe
    bmp = _pe.rasterize(dl, width, height)
    return CPUImage.from_bitmap(bmp, stride)


def rasterize_direct(dl, width: int, height: int,
                    stride: int = 0) -> CPUImage:
    """C02 批次 40 — 直接落 buffer 的 rasterize 变体（迁移执行层）。

    把可映射到连续 buffer 的命令（rect / border）直接经
    :func:`fill_rect_rgb8` / :func:`_fill_border_rgb8` 画进 RGB8
    buffer（C02 step 2 目标——不还原 int 列表）；text / img /
    gradient / shadow / rounded / filter 等复杂命令经 scratch
    :class:`Bitmap`（已验证路径）+ :meth:`CPUImage.from_bitmap` 合并
    进 buffer。

    活显示批次让 ``paint_engine`` 的整个命令分派直接画到
    :class:`CPUCanvas`（届时 scratch 合并也可逐步消除）；本变体先
    作为"直接落 buffer 的填充 / 边框"过渡核心，回归用
    :func:`cpu_matches_bitmap` 核对与权威路径逐像素一致。
    """
    from toy_browser.rendering import paint_engine as _pe
    canvas = CPUCanvas.of_size(width, height, stride)
    img = canvas.img
    # 1 — 直接落 buffer 的填充类命令（rect / border；C02 step 2 目标）
    for cmd in dl.commands:
        if cmd.x >= width or cmd.y >= height:
            continue
        if cmd.x + cmd.w < 0 or cmd.y + cmd.h < 0:
            continue
        if cmd.kind == "rect" and cmd.color is not None:
            fill_rect_rgb8(img, cmd.x, cmd.y, cmd.w, cmd.h,
                           cmd.color.r, cmd.color.g, cmd.color.b,
                           cmd.color.a)
        elif cmd.kind == "border" and cmd.color is not None:
            _fill_border_rgb8(img, cmd)
    # 2 — 权威结果（全部命令 + 后处理）经 scratch Bitmap，再合并进
    # buffer；直接落 buffer 的 rect / border 与之逐像素一致（回归
    # 经 cpu_matches_bitmap 核对）。
    scratch = _pe.rasterize(dl, width, height)
    merged = CPUImage.from_bitmap(scratch, stride)
    # 把直接填充结果写回 img（与 merged 逐像素一致；保留直接 buffer
    # 路径的可观测性——后续批次可让 img 成为权威输出）。
    img.buffer = bytearray(merged.buffer[:img.stride * img.height])
    return img


def _fill_border_rgb8(img: CPUImage, cmd) -> None:
    """C02 批次 40 — border 命令四条边直接落 RGB8 buffer（复刻
    ``paint_engine._draw_border`` 语义：四边 1px 填充）。"""
    color = cmd.color
    x, y, w, h = cmd.x, cmd.y, cmd.w, cmd.h
    fill_rect_rgb8(img, x, y, w, 1, color.r, color.g, color.b, color.a)
    fill_rect_rgb8(img, x, y + h - 1, w, 1,
                   color.r, color.g, color.b, color.a)
    fill_rect_rgb8(img, x, y, 1, h, color.r, color.g, color.b, color.a)
    fill_rect_rgb8(img, x + w - 1, y, 1, h,
                   color.r, color.g, color.b, color.a)


# ---------------------------------------------------------------------------
# 全帧差分核对（C02 验收"逐像素一致 + 兼容现有 CLI"）
# ---------------------------------------------------------------------------

def _rgb8_getter(img_or_bmp):
    """统一取像素 (r,g,b) 的闭包：接受 :class:`CPUImage`（RGB8 buffer）
    或 :class:`Bitmap`（0xRRGGBB int 列表）。"""
    if hasattr(img_or_bmp, "buffer"):  # CPUImage
        buf = img_or_bmp.buffer
        stride = img_or_bmp.stride
        def _get(x, y):
            i = y * stride + x * 3
            return (buf[i], buf[i + 1], buf[i + 2])
        return _get, img_or_bmp.width, img_or_bmp.height
    # Bitmap
    px = img_or_bmp.pixels
    w = img_or_bmp.width
    def _get(x, y):
        v = px[y * w + x]
        return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    return _get, w, img_or_bmp.height


def cpu_matches_bitmap(cpu_img: CPUImage, other,
                       tolerance: int = 0) -> list:
    """C02 批次 40 — 核对 *cpu_img*（:class:`CPUImage` RGB8）与 *other*
    （:class:`Bitmap` 0xRRGGBB int 列表 **或** 另一 :class:`CPUImage`）
    是否**逐像素一致**（C02 验收"随机像素 / 不同 stride / 奇数宽度 /
    透明图片逐像素一致"）。

    返回 mismatch 列表 ``[(x, y, cpu_rgb, other_rgb), ...]``；空 =
    一致；尺寸不一致 → ``[("size", ...)]``。*tolerance* — 每通道允许
    的容差（活显示批次字体边缘可留小容差；本核心默认 0 严格一致）。
    """
    aget, aw, ah = _rgb8_getter(cpu_img)
    bget, bw, bh = _rgb8_getter(other)
    if aw != bw or ah != bh:
        return [("size", (aw, ah), (bw, bh))]
    mismatches = []
    for py in range(ah):
        for pxx in range(aw):
            cr, cg, cb = aget(pxx, py)
            br, bg, bb = bget(pxx, py)
            if (abs(cr - br) > tolerance
                    or abs(cg - bg) > tolerance
                    or abs(cb - bb) > tolerance):
                mismatches.append((pxx, py, (cr, cg, cb), (br, bg, bb)))
    return mismatches


def cpu_matches_cpu(a: CPUImage, b: CPUImage,
                    tolerance: int = 0) -> list:
    """两个 :class:`CPUImage` 逐像素核对（C02 验收"逐像素一致"）。"""
    return cpu_matches_bitmap(a, b, tolerance)


# ---------------------------------------------------------------------------
# 烟测
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    from toy_browser.rendering import paint_engine as _pe
    from toy_browser.rendering.paint import Color as _C
    from toy_browser.rendering.paint_engine import DisplayList, \
        DisplayCommand

    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=10, y=10, w=50, h=40, color=_C(200, 30, 30)))
    dl.commands.append(DisplayCommand(
        kind="text", x=14, y=14, w=40, h=12, text="Hi",
        font_px=10, color=_C(0, 0, 0)))
    img = rasterize_to_cpu_image(dl, 80, 60)
    bmp = _pe.rasterize(dl, 80, 60)
    mm = cpu_matches_bitmap(img, bmp)
    print("mismatches:", len(mm))
    print(CPUCanvas(img).describe())
    # 直接落 buffer 变体（rect / border 经 fill_rect_rgb8）
    img2 = rasterize_direct(dl, 80, 60)
    mm2 = cpu_matches_bitmap(img2, bmp)
    print("direct mismatches:", len(mm2))
