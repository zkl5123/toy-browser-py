"""R7-P02 §254 — GUI 后端边界契约（surface / 读回 / resize / device-lost）。

Round-7 评估（``逐项改进实施方案.md`` R7-P02 第 5 点，``渲染与标准
差距审计.md`` §5）：

    连接原生 surface/present/swapchain、resize/device-lost，明确
    GUI 后端边界；**普通帧不读回，截图才读回**。

本模块落地其中"明确 GUI 后端边界"的**纯逻辑决策层**（无 wgpu /
无 OS 依赖，P13 不崩）：把"何时读回、何时 present、设备失效后如何
降级"从 GPUBackend 的隐式行为收敛为三个显式、可单测的契约对象：

* :class:`SurfacePresentGate` — surface 生命周期门控。present 前置
  决策：后端无真实 surface（headless / 离屏）→ ``"no-surface"``；
  注入了有效 surface → ``"present"``。与既有
  :meth:`GPUBackend.present` 的 ``no-surface`` 契约同语义（§254
  "surface 更新可见"——只有有 surface 才谈更新可见）。

* :class:`ReadbackPolicy` — 读回策略（§254 核心："普通帧不读回，
  截图才读回"）。:meth:`allow_readback` 对每帧产出 True/False：
  常规交互帧（present 主链）**不**读回（P01"常规交互无每帧读回"）；
  仅截图 / PNG 导出（显式 *is_capture*）才读回。把"读回"从隐式
  行为变成显式策略判定，避免"CPU 像素上传显存再读回"被误当成
  加速（审计 §5"CPU 图上传再读回一致只算传输正确，不算加速"）。

* :class:`ResizeLostRecovery` — resize / device-lost 恢复状态机。
  设备失效（lost）→ 全部已上传纹理 / frame 句柄失效（标记
  *frame invalid*）→ 下次 render 须重走（不能复用失效纹理）；
  resize → 视口尺寸变更，已产帧失效，需重光栅化；降级决策
  :meth:`should_fallback_to_cpu` 给出"设备 lost 且无 surface →
  退化 CPU 形态"的契约。

设计取舍（"不冒充通过"）：本层只做**决策与状态**，真实 wgpu
device-lost 事件订阅 / surface 重建 / swapchain 接线留待活 GPU 批次。
本层保证"边界契约可解释、可单测、不冒充真实 GPU 行为"。

无 OS / wgpu 依赖，纯逻辑可回归（P13 不崩）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# 读回策略（§254 "普通帧不读回，截图才读回"）
# ----------------------------------------------------------------------

#: 帧用途常量（显式标记，替代隐式读回）。
USE_PRESENT = "present"      # 常规交互呈现帧——不读回
USE_CAPTURE = "capture"      # 截图 / PNG 导出帧——才读回


class ReadbackPolicy:
    """§254 — 读回策略：常规交互帧不读回，仅截图才读回。

    * ``allow_readback(use)`` — *use* ∈ {USE_PRESENT, USE_CAPTURE}
      → bool。present 主链的常规帧返回 ``False``（不读回，P01）；
      显式 *is_capture* 的截图帧返回 ``True``。

    契约（审计 §5）："CPU 图上传再读回一致只算传输正确，不算
    加速"——本策略保证**不会**把"每帧都读回"当成性能手段；读回
    只在显式截图场景发生。
    """

    def allow_readback(self, use: str, *, is_capture: bool = False) -> bool:
        # 显式截图（is_capture）→ 读回（截图才读回）。
        if is_capture:
            return True
        # 常规交互（use == USE_PRESENT 且未标记 capture）→ 不读回。
        return use == USE_CAPTURE

    def describe(self) -> str:
        return ("readback=explicit-capture-only "
                "(常规交互帧不读回，截图才读回)")


# ----------------------------------------------------------------------
# surface present 门控（§254 "surface 更新可见"）
# ----------------------------------------------------------------------

@dataclass
class SurfacePresentGate:
    """§254 — surface 生命周期门控（present 前置决策）。

    把 :meth:`GPUBackend.present` 的隐式 ``no-surface`` 契约收敛为
    显式门控：

    * :meth:`decide` — 按 *has_real_gpu* / *surface* 产出决策标记
      （``"present"`` / ``"no-surface"`` / ``"cpu"``），供 present
      主链直接消费（不读回）。
    """

    #: 决策结果常量。
    DECISION_PRESENT = "present"          # 有 surface → present
    DECISION_NO_SURFACE = "no-surface"    # 真实 GPU 但无 surface
    DECISION_CPU = "cpu"                  # CPU 回退形态（无 GPU）

    def decide(self, has_real_gpu: bool, surface) -> str:
        if not has_real_gpu:
            # CPU 回退形态（P13）：帧在 frame.image，GUI 直接消费，
            # 无真实 surface 需 present。
            return self.DECISION_CPU
        if surface is None:
            # 真实 GPU 但无 surface（headless / 离屏）：不抛错，
            # 返回可区降标记（§254 "surface 更新可见"——无 surface
            # 则无更新可见，调用方显式注入 surface 后重试）。
            return self.DECISION_NO_SURFACE
        # 有 surface → present 通路（经既有 _present_to_surface submit）。
        return self.DECISION_PRESENT


# ----------------------------------------------------------------------
# resize / device-lost 恢复状态机（§254 "resize/device-lost"）
# ----------------------------------------------------------------------

@dataclass
class ResizeLostRecovery:
    """§254 — resize / device-lost 恢复状态机 + 降级决策。

    设备失效 / 视口变更后，已上传的 GPU 纹理 / frame 句柄**全部
    失效**（wgpu 设备 lost 后纹理不可用；resize 后旧尺寸 frame 不
    匹配新视口）。状态机显式追踪失效标记，让 render 主链据此决定
    重走光栅化，而非复用失效纹理（"不冒充"——失效帧不能当有效帧
    present / 读回）。

    * ``device_lost: bool`` — 设备是否 lost（触发全量失效）。
    * ``invalidated: bool`` — 当前已产帧是否失效（需重走 render）。
    * ``last_resize`` / ``last_lost_at`` — 最近事件序号（诊断 / 验收
      可解释）。
    """

    device_lost: bool = False
    invalidated: bool = False
    last_resize: int = 0
    last_lost_at: int = 0

    # 事件序号（供诊断 + 验收"记录事件"）。
    _seq: int = field(default=0, init=False, repr=False)

    def _bump(self) -> int:
        self._seq += 1
        return self._seq

    def on_device_lost(self) -> int:
        """设备失效事件 → 全部纹理 / frame 失效 + 进入 lost 态。"""
        self._seq += 1
        self.device_lost = True
        self.invalidated = True
        self.last_lost_at = self._seq
        return self._seq

    def on_device_restored(self) -> int:
        """设备恢复（重建 surface / device）→ 清 lost，但仍需重
        走 render（旧纹理随 lost 已失效）。"""
        self._seq += 1
        self.device_lost = False
        # 恢复后旧 frame 仍失效（需重光栅化 + 重上传）。
        self.invalidated = True
        return self._seq

    def on_resize(self, width: int, height: int) -> int:
        """视口 resize → 已产帧失效（旧尺寸不匹配新视口）。"""
        self._seq += 1
        self.last_resize = self._seq
        self.invalidated = True
        return self._seq

    def on_render_completed(self) -> int:
        """一次有效 render 完成（重光栅化 + 重上传）→ 清失效标记。

        仅当设备未 lost 时（lost 态下 render 需先 on_device_restored
        重建设备 + surface，否则纹理上传失败）。
        """
        self._seq += 1
        if not self.device_lost:
            self.invalidated = False
        return self._seq

    @property
    def needs_re_render(self) -> bool:
        """是否需重走 render（失效或设备 lost）——供主链判定。"""
        return self.invalidated or self.device_lost

    def should_fallback_to_cpu(self, has_surface: bool) -> bool:
        """§254 降级决策——设备 lost 且无 surface → 退化 CPU 形态。

        设备 lost（纹理全失效）且无可用 surface（无法重建 GPU
        呈现）→ 真无法 GPU 通路 → 退化 CPU（P13 不崩）。仍持
        surface 则走 GPU 重走通路（on_device_restored + 重 render）。
        """
        return bool(self.device_lost and not has_surface)

    def snapshot(self) -> dict:
        """诊断快照（供 G10 诊断区 + 验收可解释）。"""
        return {
            "device_lost": self.device_lost,
            "invalidated": self.invalidated,
            "needs_re_render": self.needs_re_render,
            "last_resize": self.last_resize,
            "last_lost_at": self.last_lost_at,
            "seq": self._seq,
        }

    def describe(self) -> str:
        s = self.snapshot()
        return (f"gpu-boundary lost={int(s['device_lost'])} "
                f"invalidated={int(s['invalidated'])} "
                f"need_rerender={int(s['needs_re_render'])} "
                f"resize@{s['last_resize']} lost@{s['last_lost_at']}")
