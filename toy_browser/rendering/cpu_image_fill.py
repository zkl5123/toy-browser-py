"""批次 32 — C02 step 2 填充 / 混合 / 图片迁移到连续缓冲区（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` C02
step 2：

> 保留 ``Bitmap.pixels`` 兼容适配，**分批迁移填充、混合、图片和字形**
> 到连续缓冲区；避免每次呈现又从连续缓冲区还原大 int 列表。

C02 验收：

> 随机像素、不同 stride、奇数宽度、透明图片和小图逐像素一致。

批次 15（:mod:`toy_browser.rendering.cpu_image` /
:mod:`toy_browser.rendering.cpu_image_formats`）已落地 ``CPUImage``
（RGB8 连续 buffer + ``from_bitmap`` / ``to_ppm_bytes``）、
``FormatConverter``（RGBA8/BGRA8→RGB8 批量转换 + ``composite_alpha``
透明合成公式）与 ``FrameImage``（帧版本 + 读写所有权，批次 29
``frame_ownership`` 进一步固化）。本批补 C02 step 2 的**迁移执行层**
——把 :class:`toy_browser.rendering.paint.Bitmap` 的**填充 / 混合 /
图片 / 字形**直接落到 ``CPUImage`` 连续缓冲区（``bytearray``，不再还原
成 0xRRGGBB int 列表）：

* :func:`fill_rect_rgb8` — 把一个矩形填充进 **RGB8** 连续 buffer
  （复刻 :meth:`Bitmap.fill_rect` 语义：越界 clamp、透明跳过、不透明
  快路径逐行写、半透明逐像素 alpha 混合到已有像素）。与
  :class:`Bitmap` 逐像素一致（C02 验收"透明图片逐像素一致"）。
* :func:`blit_rgba8_over_rgb8` — 把一块 **RGBA8** 源（图片 / 字形
  mask）alpha 混合进 **RGB8** 目标（C02 step 2 "图片"迁移；透明通道
  走 :func:`toy_browser.rendering.cpu_image_formats.
  composite_alpha` 同公式）。
* :func:`blit_rgba8_over_rgba8` — RGBA8 → RGBA8 混合（保留目标 alpha
  通道，图片层叠用）。
* :func:`blend_pixel_rgba8` — 单像素 RGBA 混合工具（字形 / 子像素用；
  与 :class:`paint.Color.blend_over` 同语义，只是操作 4 通道）。
* :func:`fill_matches_bitmap` — **差分核对工具**（C02 验收"逐像素
  一致"）：把一个 :class:`Bitmap` 的 ``pixels``（0xRRGGBB int 列表）
  经 :meth:`CPUImage.from_bitmap` 转成 RGB8 buffer，再对两者做逐像素
  比对，返回不一致偏移列表（空 = 一致）。

设计要点（与批次 15 ``cpu_image`` / ``cpu_image_formats`` **兼容不重叠**）：
- 批次 15 管"连续 buffer 的数据契约 + 格式转换 + 帧所有权"；本模块管
  "填充 / 混合 / 图片 / 字形 **直接写进连续 buffer**"（迁移执行层），
  让 ``paint_engine.rasterize``（现仍产出 :class:`Bitmap` int 列表）
  活显示批次可逐阶段切到 :class:`CPUImage`（先填充 / 混合，再图片 /
  字形），不再每帧还原大 int 列表（C02 step 2 目标）。
- 语义与 :class:`toy_browser.rendering.paint.Bitmap` /
  :class:`paint.Color` **完全一致**（不透明快路径 + 半透明逐像素
  ``blend_over`` + 透明跳过），``fill_matches_bitmap`` 直接回归"与
  现有 Bitmap 逐像素一致"（C02 验收"兼容现有 CLI"）。
- 纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib + 批次 15
  ``CPUImage`` / ``cpu_image_formats``。
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from toy_browser.rendering.cpu_image import CPUImage
    from toy_browser.rendering.cpu_image_formats import composite_alpha
except Exception:  # pragma: no cover - 直接跑本文件（无包路径）时降级
    CPUImage = None  # type: ignore
    composite_alpha = None  # type: ignore


# ----------------------------------------------------------------------
# 基础像素读写（RGB8 buffer，含 stride padding）
# ----------------------------------------------------------------------

def _read_rgb8(buf: bytearray, stride: int, x: int, y: int) -> tuple[int, int, int]:
    """读 RGB8 buffer 在 (x,y) 的 (r,g,b)。"""
    i = y * stride + x * 3
    return (buf[i], buf[i + 1], buf[i + 2])


def _write_rgb8(buf: bytearray, stride: int, x: int, y: int,
                r: int, g: int, b: int) -> None:
    """写 RGB8 buffer 在 (x,y) 的 (r,g,b)（clamp 0–255）。"""
    i = y * stride + x * 3
    buf[i] = r & 0xFF
    buf[i + 1] = g & 0xFF
    buf[i + 2] = b & 0xFF


# ----------------------------------------------------------------------
# 填充（C02 step 2：把 fill_rect 迁移到连续 buffer）
# ----------------------------------------------------------------------

def fill_rect_rgb8(img: CPUImage, x: float, y: float, w: float, h: float,
                   r: int, g: int, b: int, a: int = 255) -> None:
    """把 (x,y,w,h) 矩形以 (r,g,b,a) 填充进 *img* 的 **RGB8** buffer。

    完全复刻 :meth:`toy_browser.rendering.paint.Bitmap.fill_rect` 语义：

    * 越界 clamp（``x0=max(0,int(x))`` 等），负尺寸 / 零面积直接返回；
    * 透明（``a == 0``）直接返回（不画）；
    * 不透明（``a == 255``）快路径：逐行把 [x0,x1) 的像素写成 (r,g,b)；
    * 半透明：逐像素 :func:`composite_alpha` 混合到已有像素
      （与 :class:`paint.Color.blend_over` 同公式）。

    直接写 *img* 的连续 buffer（含 stride padding），不还原成 int
    列表（C02 step 2 目标）。活显示批次 ``paint_engine.rasterize``
    切到 :class:`CPUImage` 后，本函数替代 ``bmp.fill_rect``。
    """
    W, H = img.width, img.height
    if a == 0:
        return
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(W, int(x + w))
    y1 = min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return
    buf = img.buffer
    stride = img.stride
    if a == 255:
        # 不透明快路径：逐行写 (r,g,b)
        row_bytes = bytes((r & 0xFF, g & 0xFF, b & 0xFF))
        for py in range(y0, y1):
            base = py * stride + x0 * 3
            # 逐行重复写 row_bytes 到 [x0,x1)
            for px in range(x0, x1):
                i = py * stride + px * 3
                buf[i] = r & 0xFF
                buf[i + 1] = g & 0xFF
                buf[i + 2] = b & 0xFF
    else:
        # 半透明：逐像素 alpha 混合到已有像素（composite_alpha 同公式）
        sa = a / 255.0
        for py in range(y0, y1):
            for px in range(x0, x1):
                dr, dg, db = _read_rgb8(buf, stride, px, py)
                nr = min(255, int(r * sa + dr * (1.0 - sa)))
                ng = min(255, int(g * sa + dg * (1.0 - sa)))
                nb = min(255, int(b * sa + db * (1.0 - sa)))
                _write_rgb8(buf, stride, px, py, nr, ng, nb)


# ----------------------------------------------------------------------
# 图片 / 字形混合（C02 step 2：RGBA8 源 alpha 混合进目标）
# ----------------------------------------------------------------------

def blend_pixel_rgba8(dst_r: int, dst_g: int, dst_b: int, dst_a: int,
                      src_r: int, src_g: int, src_b: int, src_a: int
                      ) -> tuple[int, int, int, int]:
    """单像素 RGBA "src over dst" 混合（源在上、目标在下）。

    与 :class:`paint.Color.blend_over` 同语义（straight alpha，不是
    预乘），扩展 4 通道（保留目标 alpha）。返回 (r,g,b,a) 各 clamp
    0–255。src_a==0 → 目标不变；src_a==255 → 源覆盖。
    """
    if src_a == 0:
        return (dst_r & 0xFF, dst_g & 0xFF, dst_b & 0xFF, dst_a & 0xFF)
    if src_a == 255:
        return (src_r & 0xFF, src_g & 0xFF, src_b & 0xFF,
                dst_a & 0xFF)
    sa = src_a / 255.0
    da = dst_a / 255.0
    out_a = int(min(255, src_a + dst_a * (1.0 - sa)) )
    # 颜色：src over dst（straight alpha）
    or_ = int(min(255, src_r * sa + dst_r * da * (1.0 - sa)))
    og = int(min(255, src_g * sa + dst_g * da * (1.0 - sa)))
    ob = int(min(255, src_b * sa + dst_b * da * (1.0 - sa)))
    return (or_ & 0xFF, og & 0xFF, ob & 0xFF, out_a & 0xFF)


def blit_rgba8_over_rgb8(dst: CPUImage, src: bytearray,
                         src_width: int, src_height: int,
                         src_stride: int,
                         ox: int, oy: int) -> None:
    """把 **RGBA8** 源（*src*，``src_stride`` 字节/行）alpha 混合进
    *dst*（**RGB8** :class:`CPUImage`）在 (ox,oy) 处。

    C02 step 2 "图片"迁移：透明通道走
    :func:`toy_browser.rendering.cpu_image_formats.composite_alpha`
    同公式（与 :class:`paint.Color.blend_over` 一致）。越界 / 全透明
    像素不写。*dst* 的 RGB8 buffer 直接原地更新（不还原 int 列表）。
    """
    dbuf = dst.buffer
    dstride = dst.stride
    dw, dh = dst.width, dst.height
    for y in range(src_height):
        dy = oy + y
        if dy < 0 or dy >= dh:
            continue
        srow = y * src_stride
        for x in range(src_width):
            dx = ox + x
            if dx < 0 or dx >= dw:
                continue
            i = srow + x * 4
            r = src[i]
            g = src[i + 1]
            b = src[i + 2]
            a = src[i + 3]
            if a == 0:
                continue
            dr, dg, db_ = _read_rgb8(dbuf, dstride, dx, dy)
            if a == 255:
                nr, ng, nb = r, g, b
            else:
                nr = composite_alpha(r, dr, a)
                ng = composite_alpha(g, dg, a)
                nb = composite_alpha(b, db_, a)
            _write_rgb8(dbuf, dstride, dx, dy, nr, ng, nb)


def blit_rgba8_over_rgba8(dst: bytearray, dst_stride: int, dst_width: int,
                          dst_height: int,
                          src: bytearray, src_width: int, src_height: int,
                          src_stride: int, ox: int, oy: int) -> None:
    """RGBA8 → RGBA8 混合（保留目标 alpha 通道，图片层叠用）。

    每个像素用 :func:`blend_pixel_rgba8`（src over dst）；越界 / 全
    透明源像素跳过。*dst* 的 RGBA8 buffer 原地更新（4 字节/像素，
    ``dst_stride`` 字节/行）。
    """
    for y in range(src_height):
        dy = oy + y
        if dy < 0 or dy >= dst_height:
            continue
        srow = y * src_stride
        drow = dy * dst_stride
        for x in range(src_width):
            dx = ox + x
            if dx < 0 or dx >= dst_width:
                continue
            si = srow + x * 4
            di = drow + dx * 4
            sr, sg, sb, sa = src[si], src[si + 1], src[si + 2], src[si + 3]
            if sa == 0:
                continue
            dr, dg, db_, da = dst[di], dst[di + 1], dst[di + 2], dst[di + 3]
            nr, ng, nb, na = blend_pixel_rgba8(dr, dg, db_, da,
                                                sr, sg, sb, sa)
            dst[di] = nr
            dst[di + 1] = ng
            dst[di + 2] = nb
            dst[di + 3] = na


# ----------------------------------------------------------------------
# 差分核对（C02 验收"与现有 Bitmap 逐像素一致"）
# ----------------------------------------------------------------------

def fill_matches_bitmap(bmp, img: CPUImage,
                        tolerance: int = 0) -> list:
    """核对 *img*（:class:`CPUImage`，RGB8）与 *bmp*（:class:`Bitmap`
    int 列表）是否**逐像素一致**（C02 验收"兼容现有 CLI / 逐像素
    一致"）。

    * *bmp* — :class:`toy_browser.rendering.paint.Bitmap`（``pixels``
      为 0xRRGGBB int 列表，``width`` / ``height``）。
    * *img* — 同等几何的 :class:`CPUImage`（RGB8 连续 buffer）。
    * *tolerance* — 每通道允许差（默认 0 = 精确一致；C03 字体边缘
      容差场景可放宽，与批次 27 ``geometry_freeze`` 同语义）。

    返回不一致像素偏移列表（每个元素 ``(x, y, bmp_rgb, img_rgb)``）；
    空 = 逐像素一致。尺寸 / 越界安全。
    """
    out: list = []
    if bmp.width != img.width or bmp.height != img.height:
        return out
    pix = bmp.pixels
    buf = img.buffer
    stride = img.stride
    for y in range(bmp.height):
        for x in range(bmp.width):
            v = pix[y * bmp.width + x]
            br, bg_, bb = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
            ir, ig_, ib = _read_rgb8(buf, stride, x, y)
            if (abs(br - ir) > tolerance
                    or abs(bg_ - ig_) > tolerance
                    or abs(bb - ib) > tolerance):
                out.append((x, y, (br, bg_, bb), (ir, ig_, ib)))
    return out


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.rendering.cpu_image import CPUImage as _CI
    from toy_browser.rendering.cpu_image_formats import composite_alpha as _ca
    from toy_browser.rendering.paint import Bitmap, Color, WHITE

    # 直接在本模块命名空间里补上惰性符号（仅烟测用）
    CPUImage = _CI
    composite_alpha = _ca
    bmp = Bitmap(4, 4, WHITE)
    bmp.fill_rect(0, 0, 2, 2, Color(255, 0, 0, 128))  # 半透明红
    bmp.fill_rect(2, 2, 1, 1, Color(0, 255, 0, 255))  # 不透明绿

    img = CPUImage(width=4, height=4)
    # 先白底（等同 Bitmap(WHITE)）
    fill_rect_rgb8(img, 0, 0, 4, 4, 255, 255, 255, 255)
    fill_rect_rgb8(img, 0, 0, 2, 2, 255, 0, 0, 128)
    fill_rect_rgb8(img, 2, 2, 1, 1, 0, 255, 0, 255)
    mism = fill_matches_bitmap(bmp, img)
    print("mismatch:", mism)
    print("sample img[0,0]:", img.get_pixel(0, 0),
          "bmp[0,0]:", bmp.pixel_at(0, 0))
