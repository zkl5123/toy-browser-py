"""G06 — 动态视口、DPI 与页面缩放（统一坐标契约）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G06：

1. 新增 ``ViewportState``，避免散落的视口 / zoom 比例计算。
2. 布局使用 CSS 像素；GUI 图像使用物理像素。UI 逻辑事件 → CSS
   坐标 = ``(ui - content_origin) / page_zoom + scroll_offset``；
   只有物理事件坐标才先转逻辑坐标。
3. 尺寸 / zoom 变化 → 重排 + 夹紧滚动 + 更新 scrollbar / 命中树
   （重排调用由 :class:`PageInstance` 承担，本模块只做坐标换算
   与 resize 合并器的纯逻辑）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ViewportState:
    """G06 — 视口 + zoom 统一状态。

    * ``css_width`` / ``css_height`` — 布局用的 CSS 像素（viewport）。
    * ``ui_width`` / ``ui_height`` — 物理 / 设备像素（Tk 控件实际
      尺寸；DPI 已折算）。
    * ``device_scale`` — 物理像素 / CSS 像素（DPI 缩放）。
    * ``page_zoom`` — 页面缩放（1.0 = 100%）。
    * ``scroll_x`` / ``scroll_y`` — CSS 像素的文档滚动偏移。
    """

    css_width: int = 800
    css_height: int = 600
    ui_width: int = 800
    ui_height: int = 600
    device_scale: float = 1.0
    page_zoom: float = 1.0
    scroll_x: int = 0
    scroll_y: int = 0

    # ------------------------------------------------------------------
    # 换算（G06 step 3 的坐标公式）
    # ------------------------------------------------------------------
    def css_to_ui(self, cx: float, cy: float) -> tuple[float, float]:
        """CSS 文档坐标 → 物理像素（控件坐标系）。

        物理 = CSS × zoom × device_scale − 滚动偏移（视口原点）。
        """
        s = self.page_zoom * self.device_scale
        return (cx * s - self.scroll_x * self.device_scale,
                cy * s - self.scroll_y * self.device_scale)

    def ui_to_css(self, ux: float, uy: float) -> tuple[float, float]:
        """物理像素（控件坐标）→ CSS 文档坐标（G06 step 3 的逆公式）。

        只有物理事件坐标才调用；CSS 逻辑坐标不再二次换算。
        """
        s = self.page_zoom * self.device_scale
        if s == 0:
            return (ux, uy)
        return (ux / s + self.scroll_x, uy / s + self.scroll_y)

    def logical_to_css(self, lx: float, ly: float) -> tuple[float, float]:
        """Tk 逻辑坐标（UI 像素，未按 DPI 折算的控件坐标）→ CSS 文档
        坐标。DPI 感知场景下先除 device_scale 再除 zoom。"""
        return self.ui_to_css(lx / self.device_scale, ly / self.device_scale)

    # ------------------------------------------------------------------
    # 重排辅助（G06 step 4）
    # ------------------------------------------------------------------
    def clamp_scroll(self, scroll_max_x: int, scroll_max_y: int) -> None:
        """尺寸 / zoom 变化后夹紧滚动到新的可滚范围（文档内容不变）。"""
        self.scroll_x = max(0, min(self.scroll_x, max(0, scroll_max_x)))
        self.scroll_y = max(0, min(self.scroll_y, max(0, scroll_max_y)))

    def set_size(self, ui_w: int, ui_h: int, device_scale: float = 1.0) -> bool:
        """更新物理尺寸；返回视口 CSS 尺寸是否变化（调用方据此重排）。"""
        s = device_scale or 1.0
        old_css = (self.css_width, self.css_height)
        self.ui_width = int(ui_w)
        self.ui_height = int(ui_h)
        self.device_scale = s
        self.css_width = max(1, int(round(self.ui_width / (s * self.page_zoom))))
        self.css_height = max(1, int(round(self.ui_height / (s * self.page_zoom))))
        return old_css != (self.css_width, self.css_height)

    def set_zoom(self, page_zoom: float, anchor_css: tuple[float, float] | None = None
                 ) -> bool:
        """页面缩放；可选围绕 *anchor_css*（文档坐标）保持不动。

        返回 CSS 视口尺寸是否变化。
        """
        page_zoom = max(0.1, min(4.0, page_zoom))
        old = (self.css_width, self.css_height)
        if anchor_css is not None:
            # 让 anchor_css 保持在控件中心
            cx, cy = anchor_css
            # 控件中心的 UI 坐标
            mid_ui_x = self.ui_width / 2
            mid_ui_y = self.ui_height / 2
            # 新 zoom 下的 device_scale（不变）
            new_s = page_zoom * self.device_scale
            self.scroll_x = cx - mid_ui_x / new_s
            self.scroll_y = cy - mid_ui_y / new_s
        self.page_zoom = page_zoom
        self.css_width = max(1, int(round(self.ui_width / (self.device_scale * page_zoom))))
        self.css_height = max(1, int(round(self.ui_height / (self.device_scale * page_zoom))))
        return old != (self.css_width, self.css_height)


class ResizeCoalescer:
    """G06 step 1 — 连续 resize 合并器（约 16–50ms 提交一次最新尺寸）。

    纯逻辑，可测试：每次 ``offer`` 记录最新尺寸与到达时刻；
    ``flush`` 在 (now - 首次到达) ≥ min_interval_ms 后返回合并后的
    尺寸，否则返回 None（不提交，保持 pending）。
    """

    def __init__(self, min_interval_ms: float = 16.0) -> None:
        self.min_interval_ms = min_interval_ms
        self._latest: tuple[int, int] | None = None
        self._arrived_ms: float = -1.0

    def offer(self, ui_w: int, ui_h: int, now_ms: float) -> None:
        """记录最新尺寸（连续 resize 只留最后一个；
        到达时刻取该批次第一个 offer，作为合并窗口的起点）。"""
        if self._latest is None:
            self._arrived_ms = now_ms
        self._latest = (int(ui_w), int(ui_h))

    def flush(self, now_ms: float) -> tuple[int, int] | None:
        """合并窗口到点时返回合并后的尺寸，否则 None。"""
        if self._latest is None:
            return None
        if (now_ms - self._arrived_ms) >= self.min_interval_ms:
            out = self._latest
            self._latest = None
            self._arrived_ms = -1.0
            return out
        return None

    def pending(self) -> bool:
        return self._latest is not None
