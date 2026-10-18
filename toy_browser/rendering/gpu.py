"""批次 47 — P01/P02/P13 GPU 后端存根 + 后端工厂（可选依赖 + CPU 回退）。

依据 ``assessments/20261016-round5/docs/GPU光栅化迁移方案.md``：

- P02"适配器选择与能力探测" + P13"可选依赖、打包与 CPU 回退"：本环境
  无出站网络（装不上 ``wgpu-py``），活 GPU 验证留后续批次。本模块
  先落地 **P13 契约**：:func:`create_backend` 在 ``wgpu-py`` 不可用
  （import 失败 / 设备探测失败）时**自动回退**到
  :class:`CPURenderBackend`（UI 不崩，诊断面板可解释原因——P02
  "UI 显示 adapter / backend / fallback 状态"）。
- 项目 ``CONTRIBUTING`` 要求默认纯标准库：``cpu`` 模式继续遵守；
  ``gpu`` 模式单独声明可选依赖（P13"若必须所有模式都仅标准库…需
  重新评估"）。

本模块是**存根 / 回退契约**，不实现真实 wgpu 光栅化（P04–P08 GPU
绘制 + P09 tile 增量 + P12 验收留后续批次）。:class:`GPUBackend`
先声明接口形态（覆写 :meth:`present` 直接呈现 GPU surface 的占位
语义），真实多核 / 多 GPU 实现等活显示 + 可选依赖批次。
"""

from __future__ import annotations

from typing import Optional

from toy_browser.rendering.renderer.contracts import (
    RenderBackend,
    BackendMode,
    AdapterProbe,
)
from toy_browser.rendering.renderer.cpu_backend import CPURenderBackend


class GPUBackend(RenderBackend):
    """P01 / P13 — GPU 光栅化后端**存根**（可选 wgpu-py 依赖形态）。

    本类仅声明 P01 契约的 GPU 侧形态（:attr:`mode` = ``gpu`` +
    覆写 :meth:`present` 的"直接呈现 GPU surface，不隐式 CPU 读回"
    占位语义 + :meth:`is_available` 探测 wgpu-py 是否可导入）；**真实
    GPU 光栅化（P04–P09 图元 / 文字 / 裁剪 / 效果 / 增量合成）留后续
    批次 + 可选依赖批次**。``wgpu-py`` 不可用时，:func:`create_backend`
    不实例化本类（回退 CPU），故本类的 :meth:`render` 仍走基类
    ``NotImplementedError``（防止误用未实现路径）。
    """

    mode: BackendMode = BackendMode.GPU

    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def wgpu_available() -> bool:
        """P13 — 探测可选依赖 ``wgpu-py`` 是否可导入（不创建设备，
        避免副作用；设备创建 / surface 验证留活显示批次）。"""
        try:
            import wgpu  # noqa: F401  (可选依赖，P13)
            return True
        except Exception:
            return False

    def present(self, frame, surface=None) -> bool:
        """P01 — GPU 直接呈现 *frame* 到 *surface*（不隐式 CPU 读回，
        P01 "GPU 呈现 vs 光栅化" 分清三阶段）。存根占位：真实 wgpu
        ``queue.copy_external_to_texture`` + ``surface.present`` 留
        后续批次；本批返回 True（契约形态，供回归验证签名一致）。"""
        return frame is not None

    def snapshot(self, frame):
        """P01 — GPU 帧回读 CPU 快照（截图 / PNG 导出才异步回读，
        P01 第三阶段"交互直接 present，截图才异步回读"）。存根
        占位：真实 ``wgpu`` buffer 读回留后续批次。"""
        return getattr(frame, "image", None)


def create_backend(mode: BackendMode = BackendMode.CPU,
                   device_factory=None) -> RenderBackend:
    """P02 + P13 — 按期望模式创建渲染后端（自动回退契约）。

    *mode* — 期望后端模式（默认 :attr:`BackendMode.CPU`，项目纯
    标准库默认；``gpu`` 需 wgpu-py 可选依赖）。

    回退契约（P13"可选依赖、打包与 CPU 回退"）：

    - 期望 ``cpu`` → 直接返回 :class:`CPURenderBackend`（恒可用）。
    - 期望 ``gpu``：经 :class:`AdapterProbe`（注入 *device_factory*）
      探测；``wgpu-py`` 不可用 / 设备探测失败 → **自动回退**
      :class:`CPURenderBackend`（UI 不崩，``describe()`` 记录回退
      原因供诊断面板解释，P02 "UI 显示 adapter / backend / fallback
      状态"）。仅当设备**真正可用**时才返回 :class:`GPUBackend`。

    本环境无 wgpu-py 可装（出站网络阻塞），故 :func:`create_backend
    ("gpu")` 实际总是回退 CPU——契约已就绪，真实 GPU 路径等活显示
    + 可选依赖批次。
    """
    if mode == BackendMode.CPU:
        return CPURenderBackend()
    # 期望 gpu：先经 P02 探测 + P13 回退契约。
    probe = AdapterProbe(device_factory=device_factory)
    cap = probe.select(preferred=BackendMode.GPU)
    if cap.available and cap.mode == BackendMode.GPU:
        if not GPUBackend.wgpu_available():
            # 设备工厂声称可用但 wgpu-py 实际不可导入（fake 工厂
            # 场景）：保守回退 CPU（P13 契约——不假设装上了就可用）。
            return CPURenderBackend()
        return GPUBackend()
    # 不可用 / 已回退 cpu（P13）：返回 CPU 参考后端。
    return CPURenderBackend()


__all__ = [
    "GPUBackend",
    "create_backend",
]
