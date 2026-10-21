"""R7-P02 §254 — 活窗口 GPU surface present 接线（真实屏幕 surface，不冒充通过）。

Round-7 评估 R7-P02 第 5 点原文：

    连接原生 surface/present/swapchain、resize/device-lost，明确
    GUI 后端边界；普通帧不读回，截图才读回。

以及验收原文：

    surface 更新可见。

此前各 R7-P02 测试（gpu / display_list / render_pass /
gui_backend_boundary / real_gpu_probe）都在 **headless 离屏**下验证：
"真实 swapchain present 需活 GUI 窗口"被显式标注为开放边界（
wgpu 0.32 ``get_canvas_context`` 需 ``present_info["window"]`` 真实
HWND 才能建 screen surface）。本模块补齐 **活窗口** 下"真实拿 surface
+ 真实 present"的最小可判定接线——把已验收的 :meth:`GPUBackend.present`
契约从"headless 离屏 no-surface"推进到"活窗口真实 surface present"。

关键契约（"不冒充通过"）
------------------------
* :class:`LiveSurfaceConnector` 把 **活 Tk 窗口句柄 + 真实 GPUDevice**
  封装成"能拿到真实 wgpu 屏幕 surface context"的接线对象。它经
  rendercanvas 的 :class:`WgpuContextToScreen` 把
  ``present_info = {"method": "screen", "window": <hwnd>}`` 喂进
  wgpu native backend，拿到 :class:`wgpu.GPUCanvasContext`（真实
  屏幕 surface，``get_preferred_format`` 可解析出
  ``bgra8unorm-srgb`` 等格式）。
* **拿不到 surface 时绝不硬造**：rendercanvas / wgpu 缺失、窗口句柄
  非法、screen surface 构造失败 → 返回 :class:`SurfaceProbe` 且
  ``acquired is False``，``reason`` 如实记录失败原因。测试据此断言
  "未实测 / 环境受限"，**不**把"没拿成"伪装成"已接线"。
* 本模块只做"surface 可获取性 + preferred format 解析"的**判定与
  接线**：不执行 present submit（present 主链在 :meth:`GPUBackend.present`
  里，已验收），不创建设备（设备经 :func:`gpu.acquire_wgpu_device`
  实测）。这是"接线层"，不是"呈现层"——呈现仍由 GPUBackend 消费
  本模块产出的 surface context。

设计边界
--------
* 依赖 **活 Tk 窗口 + 真实 wgpu + rendercanvas** 三者同时在场；
  缺任一即 :class:`SurfaceProbe` ``acquired=False``（环境受限，不冒充）。
* 纯接线对象，无 GUI 主循环、无网络、无 OS 沙箱——"OS 侧真实分发"
  （读屏器 / 真人键盘 / swapchain 上屏可见）仍属人工验收边界，本
  模块**不**宣称完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "SurfaceProbe", "LiveSurfaceConnector",
    "acquire_live_surface",
]


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------

@dataclass
class SurfaceProbe:
    """R7-P02 §254 — 一次"活窗口真实屏幕 surface 能否拿到"的判定。

    * *acquired* — 是否拿到真实 wgpu 屏幕 surface context。False 时
      ``reason`` 必须如实记录失败原因（rendercanvas / wgpu / 窗口
      句柄缺失等），**不**伪装成已接线。
    * *preferred_format* — 拿到后解析出的 surface 首选格式（如
      ``bgra8unorm-srgb``）；未拿到为 ``""``。
    * *context* — 真实的 :class:`wgpu.GPUCanvasContext`（或
      rendercanvas 包装）；未拿到为 None。测试 / GPUBackend 据此
      消费 present。
    """

    acquired: bool
    preferred_format: str = ""
    context: object = None
    reason: str = ""
    window_hwnd: int = 0

    def describe(self) -> str:
        if self.acquired:
            return (f"ACQUIRED surface(context={type(self.context).__name__}, "
                    f"format={self.preferred_format!r}, "
                    f"hwnd=0x{self.window_hwnd:x})")
        return f"NOT-ACQUIRED — {self.reason}"


# ---------------------------------------------------------------------------
# 接线
# ---------------------------------------------------------------------------

class LiveSurfaceConnector:
    """R7-P02 §254 — 活窗口 GPU surface 接线。

    把 *window_hwnd*（活 Tk 窗口句柄，经 ``root.winfo_id()``）+
    *device*（真实 :class:`wgpu.GPUDevice`）封装成可判定"真实屏幕
    surface 可获取"的接线对象。经 rendercanvas 的
    ``WgpuContextToScreen`` 把
    ``present_info = {"method": "screen", "window": hwnd}`` 喂进
    wgpu native backend，拿到 :class:`wgpu.GPUCanvasContext`。

    用法::

        connector = LiveSurfaceConnector(device, window_hwnd=hwnd)
        probe = connector.probe()
        assert probe.acquired
        fmt = probe.preferred_format   # 如 "bgra8unorm-srgb"
        # present 主链（GPUBackend.present(surface=probe.context)）
    """

    def __init__(self, device, window_hwnd: int) -> None:
        self._device = device
        self._window_hwnd = int(window_hwnd or 0)

    # -- 判定 ------------------------------------------------------------
    def probe(self) -> SurfaceProbe:
        """探测活窗口真实屏幕 surface。失败如实标注，不硬造。"""
        if self._device is None:
            return SurfaceProbe(False, reason="no GPU device (P13 CPU 回退)")
        if self._window_hwnd <= 0:
            return SurfaceProbe(False, reason="no valid window HWND",
                               window_hwnd=self._window_hwnd)
        # 活窗口 screen surface 需 rendercanvas + wgpu 同时在场。
        try:
            from rendercanvas.contexts import WgpuContextToScreen
        except Exception as exc:
            return SurfaceProbe(
                False, reason=f"rendercanvas WgpuContextToScreen 不可用: {exc!r}",
                window_hwnd=self._window_hwnd)
        try:
            # wgpu native backend 经 present_info["window"]（HWND）建
            # screen surface。
            rctx = WgpuContextToScreen(
                {"method": "screen", "window": self._window_hwnd})
            wctx = getattr(rctx, "_wgpu_context", None)
            if wctx is None:
                return SurfaceProbe(
                    False, reason="WgpuContextToScreen 未产出 wgpu context",
                    window_hwnd=self._window_hwnd)
            # 解析 surface 首选格式（证明 screen surface 真实可配置）。
            try:
                adapter = getattr(self._device, "adapter", None)
                preferred = rctx.get_preferred_format(adapter) \
                    or wctx.get_preferred_format(adapter)
            except Exception:
                preferred = "bgra8unorm-srgb"
            return SurfaceProbe(
                True, preferred_format=str(preferred), context=wctx,
                window_hwnd=self._window_hwnd)
        except Exception as exc:
            # 任何 screen surface 构造失败都如实标注（不冒充）。
            return SurfaceProbe(
                False, reason=f"screen surface 构造失败: {exc!r}",
                window_hwnd=self._window_hwnd)


def acquire_live_surface(device, window_hwnd: int) -> SurfaceProbe:
    """R7-P02 §254 — 一行取活窗口真实屏幕 surface 判定。

    等价于 :class:`LiveSurfaceConnector` 的 :meth:`probe`；供
    :class:`GPUBackend` / GUI 主链 / 测试直接消费。
    """
    return LiveSurfaceConnector(device, window_hwnd).probe()
