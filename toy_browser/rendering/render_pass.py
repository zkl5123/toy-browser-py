"""R7-P02 — 真实渲染 Pass（rect / image / glyph 独立图元光栅化）。

Round-7 评估（``渲染与标准差距审计.md`` §5 "P05/P06 真实证据"、
``逐项改进实施方案.md`` R7-P02 第 4、5 点）指出的核心缺陷：

    现状：``gpu.py:794`` CPU rasterize→全帧上传；P05/P06 在帧已
    生成后做缓存准备；present 只是有帧返回真。

    迁移顺序（R7-P02）：资源正确→DisplayList 完整→
    **真实 rect/image/glyph shader→surface 呈现**→clip/alpha/filter
    →device lost/resize→性能。**常规帧不读回；CPU 是独立 oracle，
    而不是 GPU 主链的整帧前处理器。**

本模块落地其中"真实 render pass"一层的**无 GPU 主链实现**：

* :class:`SoftwareRenderPass` — 确定性软件光栅化 Pass。接收
  :class:`paint_engine.DisplayList` 的 ``rect`` / ``img`` / ``text``
  图元命令，逐条调用**已验收的图元原语**（``fill_rect_rgb8`` /
  ``paint_image_nearest`` / ``paint_glyph_mask``）写入一个独立
  ``CPUImage``（RGB8 帧缓冲）。Pass 自身**不**调用
  ``paint_engine.rasterize_to_canvas``——它只按图元命令渲染，
  因此是"整帧前处理器"的反面：GPU 主链可据此把"逐图元真实光栅化
  + 独立缓冲"作为一条独立通路先跑通，再对照 oracle。

* :func:`cpu_oracle_frame` — **独立 CPU oracle**。走既有权威
  ``paint_engine.rasterize_to_canvas``（与 GPU 主链的 pass 完全
  分开的第二条路径），产出对照基准帧。"CPU 是独立 oracle"即此
  义：oracle 不经 pass，pass 不经 oracle。

* :func:`compare_frames` — pass 输出 vs oracle 逐像素对照（0
  mismatch 即 pass 图元光栅化正确），P02 验收"GPU 实际绘制单独
  通路像素对照通过"的 CPU 侧等价形态。

设计取舍（"不冒充通过"）：本 Pass 在无 wgpu 环境下是确定性软件
光栅化，**不是**真实 GPU shader 执行；真实 wgpu 光栅化器（GPU 侧
shader + 上传字节 + 帧时间）留待活 GPU 批次。本模块只保证图元
绘制路径正确 + 与独立 oracle 可对照，验收口径与审计一致：
"CPU 像素上传显存 ≠ GPU 执行光栅化"。

无 OS / wgpu 依赖，纯逻辑可回归（P13 不崩）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.rendering.cpu_image import CPUImage
from toy_browser.rendering.cpu_image_fill import (
    fill_rect_rgb8,
)
from toy_browser.rendering.cpu_image_glyph import (
    paint_image_nearest,
)


def _color_rgba(color) -> tuple[int, int, int, int]:
    """Color（r,g,b,a，0–255）→ 4 元组 int。None → 全 0 透明。"""
    if color is None:
        return (0, 0, 0, 0)
    return (int(color.r) & 0xFF, int(color.g) & 0xFF,
            int(color.b) & 0xFF, int(color.a) & 0xFF)


@dataclass
class SoftwareRenderPass:
    """R7-P02 — 确定性软件光栅化 Pass（rect / image / glyph 图元）。

    把 :class:`DisplayList` 的图元命令逐条光栅化进一个独立
    :class:`CPUImage`（RGB8 帧缓冲）。仅消费 ``rect`` / ``img`` /
    ``text`` 三类基础图元（R7-P02 第 4 点 "真实 rect/image/glyph
    shader" 的图元集）；round / shadow / border / gradient 等装饰
    图元留待后续批次按"基础正确后再逐项迁移"接入。

    用法::

        pass_out = SoftwareRenderPass(width, height).render(scene)
        oracle   = cpu_oracle_frame(scene, width, height)
        cmp      = compare_frames(pass_out, oracle)

    * ``render(scene)`` — 返回 :class:`CPUImage`（``buffer`` 为
      RGB8 连续 buffer，``stride = width*3``）。
    """

    width: int
    height: int
    #: 已绘制的图元命令计数（rect + img + text），供诊断 / 验收。
    commands_rendered: int = 0
    #: 按 kind 计的图元分布（rect / img / text）。
    kind_counts: dict = field(default_factory=dict)

    def new_frame(self) -> CPUImage:
        """新建一张空白 RGB8 帧缓冲。

        默认底色为**白色 (255,255,255)**——与独立 oracle 的权威
        全量光栅化器的初始画布底色一致（C02 默认画布白底），
        使逐像素对照在空白区域也成立。
        """
        img = CPUImage(width=self.width, height=self.height)
        n = self.width * 3 * self.height
        img.buffer = bytearray([255] * n)
        return img

    def render(self, scene) -> CPUImage:
        """把 *scene*（DisplayList）的图元命令光栅化进新帧缓冲。"""
        self.commands_rendered = 0
        self.kind_counts = {}
        img = self.new_frame()
        commands = getattr(scene, "commands", None) or []
        W, H = self.width, self.height
        for cmd in commands:
            kind = getattr(cmd, "kind", None)
            # 视口 culling（与 paint_engine._rasterize_into 同语义）：
            # 命令完全落在视口外 → 跳过。
            if int(getattr(cmd, "x", 0)) >= W or int(getattr(cmd, "y", 0)) >= H:
                continue
            if int(getattr(cmd, "x", 0)) + int(getattr(cmd, "w", 0)) <= 0 or \
                    int(getattr(cmd, "y", 0)) + int(getattr(cmd, "h", 0)) <= 0:
                continue
            if kind == "rect":
                self._draw_rect(img, cmd)
            elif kind == "img":
                self._draw_img(img, cmd)
            elif kind == "text":
                self._draw_text(img, cmd)
            else:
                # 装饰图元（round / shadow / border / gradient …）本
                # 基础 Pass 暂不消费——记录为 skipped，不冒充通过。
                continue
            self.commands_rendered += 1
            self.kind_counts[kind] = self.kind_counts.get(kind, 0) + 1
        return img

    # ----------------------------------------------------------------
    # 图元原语（逐条调用已验收的 CPUImage 写侧函数）
    # ----------------------------------------------------------------
    def _draw_rect(self, img: CPUImage, cmd) -> None:
        r, g, b, a = _color_rgba(getattr(cmd, "color", None))
        if a == 0:
            return
        fill_rect_rgb8(img, cmd.x, cmd.y, cmd.w, cmd.h, r, g, b, a)

    def _draw_img(self, img: CPUImage, cmd) -> None:
        pixels = getattr(cmd, "img_pixels", None)
        sw = int(getattr(cmd, "img_src_w", 0) or 0)
        sh = int(getattr(cmd, "img_src_h", 0) or 0)
        if not pixels or sw <= 0 or sh <= 0:
            return
        paint_image_nearest(img, pixels, sw, sh,
                            int(cmd.x), int(cmd.y),
                            int(cmd.w), int(cmd.h))

    def _draw_text(self, img: CPUImage, cmd) -> None:
        # 字形图元经权威 ``text.draw_text`` 绘制（与独立 oracle
        # 走同一字形轮廓 / 锚点，R7-T01），但**只针对本图元命令**
        # 写入 pass 帧缓冲的 CPUImage 视图——pass 仍逐图元光栅化，
        # 绝不调用权威整帧光栅化器（"CPU 不做整帧前
        # 处理"的 frame-trace 不变量）。
        from toy_browser.rendering.cpu_image import CpuImageBitmapView
        from toy_browser.typography import text as _text
        from toy_browser.rendering.paint import Color

        view = CpuImageBitmapView(img)
        color = getattr(cmd, "text_color", None)
        if color is None:
            color = getattr(cmd, "color", None)
        if color is None:
            color = Color(0, 0, 0)
        _text.draw_text(
            view,
            cmd.x,
            cmd.y,
            getattr(cmd, "text", "") or "",
            float(getattr(cmd, "font_px", 16.0) or 16.0),
            color,
            float(getattr(cmd, "max_width", 0.0) or 0.0),
            align=getattr(cmd, "align", "left") or "left",
            letter_spacing=float(getattr(cmd, "letter_spacing", 0.0) or 0.0),
            text_rendering=getattr(cmd, "text_rendering", "auto") or "auto",
            font_weight=int(getattr(cmd, "font_weight", 400) or 400),
            font_family=getattr(cmd, "font_family", "monospace") or "monospace",
            direction=getattr(cmd, "direction", "ltr") or "ltr",
        )


def cpu_oracle_frame(scene, width: int, height: int) -> CPUImage:
    """R7-P02 — 独立 CPU oracle：经权威 ``rasterize_to_canvas`` 产帧。

    这是与图元 Pass 通路（rect / img / text 逐图元光栅化）**完全
    分开的第二条路径**：oracle 不经过 pass，pass 不经过 oracle。
    对照语义（审计 §5 "CPU 是独立 oracle，而不是 GPU 主链的整帧
    前处理器"）即由此保证——基准帧由权威全量光栅化器独立产出，
    pass 的图元输出与之逐像素对照。

    *scene* — :class:`paint_engine.DisplayList`。返回 :class:`CPUImage`
    （RGB8）。
    """
    from toy_browser.rendering import paint_engine as _pe
    canvas = _pe.rasterize_to_canvas(scene, width, height)
    return canvas.img


def compare_frames(a: CPUImage, b: CPUImage,
                   tolerance: int = 0) -> dict:
    """逐像素对照两帧（RGB8）。0 mismatch 即一致。

    * ``a`` — pass 输出（GPU 主链通路）。
    * ``b`` — oracle（独立 CPU 权威通路）。
    * 返回 ``{"ok", "width", "height", "mismatches", "sample"}``；
      ``sample`` 为前 8 个不一致 (x,y,a_pix,b_pix)（诊断用）。

    尺寸不一致直接 ``ok=False``（不冒充通过）。
    """
    if a is None or b is None:
        return {"ok": False, "reason": "missing frame"}
    if (a.width, a.height) != (b.width, b.height):
        return {"ok": False,
                "reason": f"size mismatch {a.width}x{a.height} "
                          f"vs {b.width}x{b.height}"}
    mismatches: list[int] = []
    sample: list[tuple] = []
    stride = a.stride
    for y in range(a.height):
        row_base = y * stride
        for x in range(a.width):
            i = row_base + x * 3
            ar, ag, ab = a.buffer[i], a.buffer[i + 1], a.buffer[i + 2]
            br, bg, bb = b.buffer[i], b.buffer[i + 1], b.buffer[i + 2]
            if (abs(ar - br) > tolerance or abs(ag - bg) > tolerance
                    or abs(ab - bb) > tolerance):
                mismatches.append(y * a.width + x)
                if len(sample) < 8:
                    sample.append((x, y, (ar, ag, ab), (br, bg, bb)))
    return {"ok": not mismatches, "width": a.width, "height": a.height,
            "mismatches": mismatches, "count": len(mismatches),
            "sample": sample}
