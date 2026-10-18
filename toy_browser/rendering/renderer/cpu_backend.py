"""批次 47 — P01 RenderBackend 的 CPU 参考实现（纯标准库）。

依据 ``assessments/20261016-round5/docs/GPU光栅化迁移方案.md`` P01
"GPU 接口与模式边界"：``RenderBackend.render(scene, viewport, damage)
-> FrameHandle``。CPU 后端是本契约的**参考实现**（默认 + 保证逐像素
权威，GPU 迁移时对照它验收）：

- :class:`CPURenderBackend.render` — 经批次 45 的
  ``paint_engine.rasterize_to_canvas``（命令分派**直接画到**
  :class:`CPUCanvas`，消除 scratch Bitmap 合并层）把 *scene*（DisplayList）
  光栅化出 :class:`FrameHandle`（携带 C01 帧版本 + CPUImage，``mode="cpu"``）。
- :meth:`present` — CPU 参考语义（无真实 surface，直接成功；GUI 经
  ``frame.image.to_ppm_bytes`` → Tk PhotoImage / PPM / PNG 消费，不逐
  像素读回，P01 "GPU 呈现 vs 光栅化" 分清三阶段）。
- :meth:`snapshot` — 帧本身即 CPU buffer（``frame.image``），直接
  返回（截图 / PNG 导出才回读，P01 第三阶段"交互直接 present，截图
  才异步回读"）。
- :meth:`release_document` / :meth:`register_document` — 文档资源
  归属（C01 文档版本门控 + G09 step 4 关标签不泄漏）。

纯标准库核心，不触碰 tkinter / 网络 / 真实 GPU；``scene`` 接受
:class:`paint_engine.DisplayList`（含 ``commands``）或带 ``.commands``
的对象。
"""

from __future__ import annotations

from typing import Optional

from toy_browser.rendering.renderer.contracts import (
    RenderBackend,
    BackendMode,
)


class CPURenderBackend(RenderBackend):
    """P01 — CPU 参考渲染后端（:class:`RenderBackend` 契约的 CPU
    实现，默认 + 逐像素权威基准）。

    经批次 45 的 ``paint_engine.rasterize_to_canvas`` 直画
    :class:`CPUCanvas`（RGB8 连续 buffer，不还原 0xRRGGBB int 列表），
    产出 :class:`FrameHandle`（``mode="cpu"``）。:class:`AdapterProbe`
    （P02）探测到 wgpu-py 不可用时，:func:`create_backend` 回退到本
    后端（P13 契约，UI 不崩）。
    """

    mode: BackendMode = BackendMode.CPU

    def __init__(self) -> None:
        super().__init__()
        self._frame_seq = 0
        # 每 document 的帧计数（C01 帧版本，丢弃过期结果）。
        self._doc_frames: dict = {}

    # -- P01 接口（CPU 实现）---------------------------------------------

    def render(self, scene, viewport, damage=None):
        """P01 — 经 ``rasterize_to_canvas`` 直画 *scene* 出
        :class:`FrameHandle`（CPU 参考）。

        *scene* — :class:`paint_engine.DisplayList`（或带 ``commands``
        的对象）。*viewport* — 视口尺寸 ``{width, height, css_width,
        css_height, dpr, zoom, scroll_x, scroll_y}``（契约字段，CPU
        后端用 ``width`` / ``height`` 物理尺寸光栅化）。*damage* — 失效
        区域（CPU 后端首版整帧光栅化，damage 留 P09 增量合成批次；本
        契约记录但不过滤，保证与批次 45 逐像素权威）。
        """
        from toy_browser.rendering import paint_engine as _pe
        if viewport is None:
            w, h = 800, 600
        elif isinstance(viewport, dict):
            w = int(viewport.get("width", 800))
            h = int(viewport.get("height", 600))
        else:
            w = getattr(viewport, "width", 800)
            h = getattr(viewport, "height", 600)
        canvas = _pe.rasterize_to_canvas(scene, w, h)
        self._frame_seq += 1
        # C01 文档版本门控：按 scene 身份（id）递增帧（关标签后旧帧
        # stale，丢弃经 release_document）。
        doc_key = id(scene)
        self._doc_frames[doc_key] = self._doc_frames.get(doc_key, 0) + 1
        from toy_browser.rendering.rasterize_cpu import FrameHandle
        handle = FrameHandle(
            frame_id=self._frame_seq,
            width=w,
            height=h,
            image=canvas.img,
            mode=BackendMode.CPU.value,
            document_version=self._doc_frames[doc_key],
        )
        # 注册文档资源（P01 release_document 释放）。
        self.register_document(doc_key)
        return handle

    def describe(self) -> str:
        """P01 / P02 诊断——后端模式 + 已注册文档数 + 帧序号（供
        G10 诊断区显示 backend 状态）。"""
        return (f"{super().describe()} frame_seq={self._frame_seq}")


__all__ = ["CPURenderBackend"]
