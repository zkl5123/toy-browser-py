"""批次 33 — C02 step 2 图片/字形逐阶段迁移 + 缓冲池复用治理
（纯逻辑核心，round5 docs C02）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` C02
step 2 + step 4：

> 保留 ``Bitmap.pixels`` 兼容适配，分批迁移填充、混合、图片和字形；
> 避免每次呈现又从连续缓冲区还原大 int 列表。
> 4. 缓冲池按尺寸复用；帧版本与读写所有权明确…

批次 32（:mod:`toy_browser.rendering.cpu_image_fill`）已落地**填充 /
混合 / 图片 / 字形的连续 buffer 迁移执行层**（``fill_rect_rgb8`` /
``blit_rgba8_over_rgb8`` / ``blit_rgba8_over_rgba8`` /
``blend_pixel_rgba8`` + ``fill_matches_bitmap`` 差分核对）。本批补
C02 step 2"图片和字形"剩余两块 + step 4 缓冲池**复用治理**的纯逻辑
部分：

* :func:`paint_image_nearest` — 复刻
  :func:`toy_browser.rendering.paint_engine._paint_img_pixels` 的
  **最近邻缩放 + 越界 clamp + 截断 data URI 防御**（P3.1/P19 语义），
  但**直接写进** :class:`CPUImage` 的 RGB8 连续 buffer（不还原
  0xRRGGBB int 列表）——把"图片"迁移到连续缓冲区（C02 step 2
  "图片"）。
* :func:`paint_glyph_mask` — 把单字符/字形 **CPU mask**
  （``mask[y][x]`` 0–255 不透明度，与批次 12/15
  ``toy_browser/typography/rasterize`` 字形栅格语义一致）alpha 混合
  进 :class:`CPUImage` 的 RGB8 buffer（C02 step 2 "字形"，首版 CPU
  字形 mask + 后续 P06 GPU 混合不另行换行）。
* :class:`ImagePoolPolicy` — 批次 15 :class:`ImagePool`（有界池：
  ``get`` / ``put`` / 超上限丢弃）之上的**复用治理层**（C02 step 4
  "缓冲池按尺寸复用"）：``hit`` / ``miss`` / ``evicted`` /
  ``reused`` / ``created`` 计数 + ``hit_rate`` + ``total_created`` +
  按 (width,height,stride) 的尺寸分布 ``size_distribution`` +
  ``to_report``（活显示批次 C02 验收"分测打包/转换/显示更新"与
  诊断区可展示池命中率，判断池是否真的在复用而不是每帧新建）。
* :func:`paint_glyph_mask` / :func:`paint_image_nearest` 均与
  :class:`Bitmap` 参考路径（``paint_engine._paint_img_pixels`` /
  逐像素 mask 混合）逐像素一致（C02 验收"随机像素、不同 stride、
  奇数宽度、透明图片和小图逐像素一致"），用
  :func:`toy_browser.rendering.cpu_image_fill.fill_matches_bitmap`
  直接回归。

设计要点（与批次 15/32 **兼容不重叠**）：
- 批次 15 管数据契约（``CPUImage`` / ``from_bitmap`` / ``to_ppm_bytes``
  + ``ImagePool`` 有界池）；批次 32 管迁移执行层（填充 / 混合 / 图片 /
  字形 直接写连续 buffer）；本批补"图片最近邻缩放 / 字形 mask 混合"
  两个迁移入口 + 池复用**可观测治理**（计数 / 命中率 / 尺寸分布），
  让活显示批次切 ``rasterize`` 到 :class:`CPUImage` 时能证明池在复用。
- 语义与 :mod:`toy_browser.rendering.paint` / :mod:`paint_engine`
  **完全一致**（最近邻 clamp、截断防御、mask alpha 混合），
  ``fill_matches_bitmap`` 直接回归"与现有 Bitmap 逐像素一致"（C02
  验收"兼容现有 CLI"）。
- 纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib + 批次 15
  ``CPUImage`` / ``ImagePool`` + 批次 32 ``fill_matches_bitmap``。
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from toy_browser.rendering.cpu_image import CPUImage, ImagePool
    from toy_browser.rendering.cpu_image_fill import fill_matches_bitmap
except Exception:  # pragma: no cover - 直接跑本文件（无包路径）时降级
    CPUImage = None  # type: ignore
    ImagePool = None  # type: ignore
    fill_matches_bitmap = None  # type: ignore


# ----------------------------------------------------------------------
# 图片最近邻缩放（C02 step 2 "图片" + P3.1/P19 语义）
# ----------------------------------------------------------------------

def paint_image_nearest(img: CPUImage, pixels, src_w: int, src_h: int,
                        dst_x0: int, dst_y0: int,
                        dst_w: int, dst_h: int) -> None:
    """把已解码图片像素 *pixels*（0xRRGGBB int 列表，``src_w*src_h``）
    以**最近邻缩放** + 越界 clamp + 截断 data URI 防御（P3.1/P19）
    直接画进 *img*（:class:`CPUImage`，RGB8 连续 buffer）的
    (dst_x0,dst_y0,dst_w,dst_h) 区域。

    完全复刻 :func:`toy_browser.rendering.paint_engine.
    _paint_img_pixels` 语义（逐像素 0xRRGGBB 解出 RGB、最近邻映射
    ``sy=min(src_h-1, int(dy*src_h/dst_h))``、越界 ``idx>=len(pixels)``
    防御），但**不还原成 int 列表**（直接写连续 buffer，C02 step 2
    "图片"迁移）。活显示批次 ``rasterize`` 切到 :class:`CPUImage` 后
    本函数替代 ``_paint_img_pixels``。
    """
    if not pixels or src_w <= 0 or src_h <= 0:
        return
    if dst_w <= 0 or dst_h <= 0:
        return
    W, H = img.width, img.height
    buf = img.buffer
    stride = img.stride
    for dy in range(dst_h):
        sy = min(src_h - 1, int(dy * src_h / dst_h))
        row_start = sy * src_w
        for dx in range(dst_w):
            sx = min(src_w - 1, int(dx * src_w / dst_w))
            idx = row_start + sx
            if idx >= len(pixels):
                break
            color_u32 = pixels[idx]
            r = (color_u32 >> 16) & 0xFF
            g = (color_u32 >> 8) & 0xFF
            b = color_u32 & 0xFF
            px = dst_x0 + dx
            py = dst_y0 + dy
            if px < 0 or py < 0 or px >= W or py >= H:
                continue
            i = py * stride + px * 3
            buf[i] = r
            buf[i + 1] = g
            buf[i + 2] = b


# ----------------------------------------------------------------------
# 字形 mask 混合（C02 step 2 "字形" + 批次 12/15 字形栅格语义）
# ----------------------------------------------------------------------

def paint_glyph_mask(img: CPUImage, mask, gw: int, gh: int,
                     x0: int, y0: int,
                     r: int = 0, g: int = 0, b: int = 0,
                     base_alpha: int = 255) -> None:
    """把 CPU 字形 **mask**（``mask[y][x]`` 为 0–255 不透明度）alpha
    混合进 *img*（:class:`CPUImage`，RGB8 连续 buffer）在 (x0,y0) 处。

    与批次 12/15 ``toy_browser/typography/rasterize`` 字形栅格语义
    一致（mask = 字形覆盖率，0 = 完全透明，255 = 完全不透明）；颜色
    取 (r,g,b)，覆盖率 ``m`` 决定合成量：

    * ``m == 0`` → 跳过（透明）；
    * 否则 ``out_c = m*(base_alpha/255) 与 目标 c 按"源在上、目标在
      下"alpha 混合``，其中源不透明度 = ``m*base_alpha/255/255``
      （mask × base_alpha 两段乘法，与"字形 mask 不透明度 × 文本
      颜色 alpha"一致，避免重复相乘——P11 原则）。

    越界 clamp（负/超界不写）。活显示批次 ``rasterize`` 的字形阶段
    用本函数替代逐像素 ``Bitmap.set_pixel(Color(...))``。
    """
    if not mask or gw <= 0 or gh <= 0:
        return
    W, H = img.width, img.height
    buf = img.buffer
    stride = img.stride
    for gy in range(gh):
        py = y0 + gy
        if py < 0 or py >= H:
            continue
        row = mask[gy]
        for gx in range(gw):
            px = x0 + gx
            if px < 0 or px >= W:
                continue
            m = row[gx] & 0xFF
            if m == 0:
                continue
            # 源不透明度 = m * base_alpha / 255 / 255
            sa = (m * base_alpha) / (255 * 255)
            i = py * stride + px * 3
            dr = buf[i]
            dg = buf[i + 1]
            db = buf[i + 2]
            nr = min(255, int(r * sa + dr * (1.0 - sa)))
            ng = min(255, int(g * sa + dg * (1.0 - sa)))
            nb = min(255, int(b * sa + db * (1.0 - sa)))
            buf[i] = nr & 0xFF
            buf[i + 1] = ng & 0xFF
            buf[i + 2] = nb & 0xFF


# ----------------------------------------------------------------------
# 缓冲池复用治理（C02 step 4 "缓冲池按尺寸复用" + 可观测命中率）
# ----------------------------------------------------------------------

class ImagePoolPolicy:
    """批次 15 :class:`ImagePool` 之上的**复用治理层**（C02 step 4
    "缓冲池按尺寸复用" + 验收"分测打包/转换/显示更新"）。

    包装 *pool*（:class:`ImagePool`）并记录：

    * ``hit`` — 池内命中（``get`` 返回复用图像）次数；
    * ``miss`` — 池未命中（新建图像）次数；
    * ``evicted`` — ``put`` 超上限被丢弃次数；
    * ``reused`` / ``created`` — 与 hit/miss 同义（便于诊断区展示）；
    * ``hit_rate`` — ``hit/(hit+miss)``（无请求 → 0.0）；
    * ``total_created`` — 累计新建图像数（= miss）；
    * :meth:`size_distribution` — 按 (width,height,stride) 的
      池内当前数量分布（活显示批次诊断区展示"哪些尺寸在池里"）；
    * :meth:`to_report` — 一行人类可读报告（诊断区 / 测试打印用）。

    活显示批次在每次 ``get`` / ``put`` 时调 :meth:`on_get` /
    :meth:`on_put` 记账，从而能**证明池真的在复用**（命中率 > 0）
    而不是每帧新建（C02 验收"缓冲池按尺寸复用"）。
    """

    def __init__(self, pool: Optional[ImagePool] = None) -> None:
        self.pool = pool
        self.hit = 0
        self.miss = 0
        self.evicted = 0

    # ---- 记账入口 ----
    def on_get(self, reused: bool) -> None:
        """一次 ``pool.get``：*reused* True = 命中池内（``hit``+1），
        False = 新建（``miss``+1）。"""
        if reused:
            self.hit += 1
        else:
            self.miss += 1

    def on_put(self, accepted: bool) -> None:
        """一次 ``pool.put``：*accepted* False = 超上限被丢弃
        （``evicted``+1）。"""
        if not accepted:
            self.evicted += 1

    # ---- 派生指标 ----
    @property
    def reused(self) -> int:
        return self.hit

    @property
    def created(self) -> int:
        return self.miss

    @property
    def total_created(self) -> int:
        return self.miss

    @property
    def hit_rate(self) -> float:
        total = self.hit + self.miss
        if total == 0:
            return 0.0
        return self.hit / total

    def size_distribution(self) -> dict:
        """按 (width,height,stride) 的池内当前数量分布（若 *pool* 有
        ``_pool`` 私有属性则读它；否则返回空 dict）。"""
        out: dict = {}
        if self.pool is None:
            return out
        raw = getattr(self.pool, "_pool", None)
        if raw is None:
            return out
        for key, lst in raw.items():
            out[key] = len(lst)
        return out

    def to_report(self) -> str:
        """一行人类可读报告（诊断区 / 测试打印）。"""
        return (f"pool hit={self.hit} miss={self.miss} "
                f"evicted={self.evicted} hit_rate={self.hit_rate:.2f}")


# ----------------------------------------------------------------------
# 烟测
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover - 手动烟测
    sys = __import__("sys")
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.rendering.cpu_image import CPUImage as _CI, ImagePool as _IP
    from toy_browser.rendering.cpu_image_fill import fill_matches_bitmap as _fmb
    from toy_browser.rendering.paint import Bitmap, Color, WHITE

    CPUImage = _CI
    ImagePool = _IP
    fill_matches_bitmap = _fmb

    # 2x2 图片放大到 4x4 最近邻（每像素复制 4 次）
    px = [0xFF0000, 0x00FF00, 0x0000FF, 0x112233]  # R G B 灰
    bmp = Bitmap(4, 4, WHITE)
    from toy_browser.rendering.paint_engine import _paint_img_pixels, DisplayCommand
    # 用 paint_engine 参考路径画 Bitmap
    cmd = DisplayCommand(kind="img")
    cmd.x, cmd.y, cmd.w, cmd.h = 0, 0, 4, 4
    cmd.img_src_w, cmd.img_src_h = 2, 2
    cmd.img_pixels = px
    _paint_img_pixels(bmp, cmd)

    img = CPUImage(width=4, height=4)
    paint_image_nearest(img, px, 2, 2, 0, 0, 4, 4)
    print("image mismatch:", fill_matches_bitmap(bmp, img))

    # 字形 mask（2x2 全 255 = 不透明黑色字形）画在白底上
    bmp2 = Bitmap(3, 3, WHITE)
    for _y in range(2):
        for _x in range(2):
            bmp2.pixels[_y * 3 + _x] = 0  # 参考：2x2 变黑
    img2 = CPUImage(width=3, height=3)
    from toy_browser.rendering.cpu_image_fill import fill_rect_rgb8
    fill_rect_rgb8(img2, 0, 0, 3, 3, 255, 255, 255, 255)  # 白底
    paint_glyph_mask(img2, [[255, 255], [255, 255]], 2, 2, 0, 0,
                     r=0, g=0, b=0, base_alpha=255)
    print("glyph px(0,0):", img2.get_pixel(0, 0),
          "px(2,2):", img2.get_pixel(2, 2),
          "mismatch:", fill_matches_bitmap(bmp2, img2))

    # 池治理
    pool = ImagePool(max_size=2)
    pol = ImagePoolPolicy(pool)
    a = pool.get(8, 8)
    pol.on_get(False)  # 首次 miss
    pool.put(a)
    pol.on_put(True)
    b = pool.get(8, 8)
    pol.on_get(True)   # 命中
    print(pol.to_report(), "| dist:", pol.size_distribution())
