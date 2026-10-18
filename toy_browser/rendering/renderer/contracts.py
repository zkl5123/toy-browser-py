"""批次 47 — P01 GPU 接口与模式边界 + P02 适配器选择与能力探测。

依据 ``assessments/20261016-round5/docs/GPU光栅化迁移方案.md``：

- P01"GPU 接口与模式边界"：``RenderBackend.render(scene, viewport,
  damage) -> FrameHandle`` / ``present(frame, surface)`` /
  ``snapshot(frame) -> CPUImage`` / ``release_document(document_id)``。
  后端模式（cpu / gpu）显式可探测，避免把所有差异归咎于"GPU 精度"。
- P02"适配器选择与能力探测"：不根据显卡名称保证任何后端可用——
  探测真实能力（设备 / 驱动 / surface 可用性），不可用则回退 CPU
  （P13 可选依赖、打包与 CPU 回退）。

本模块是**纯逻辑契约**：不引入 ``wgpu-py`` 依赖、不触碰真实 GPU /
surface（活 GPU 验证留后续批次 + 可选依赖），用可注入的
``device_factory`` / ``probe`` 做协议回归（无真实设备也能验证回退
路径不崩）。``cpu`` 模式继续遵守项目"纯标准库"约束；``gpu`` 模式
单独声明依赖（P13）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# P01 — 后端模式（cpu / gpu 模式边界，显式可探测）
# ---------------------------------------------------------------------------

class BackendMode(enum.Enum):
    """P01 — 渲染后端模式。``cpu`` 为默认 + 参考实现（纯标准库）；
    ``gpu`` 为可选加速后端（需 wgpu-py 依赖，P13）。"""
    CPU = "cpu"
    GPU = "gpu"


# ---------------------------------------------------------------------------
# P02 — 能力探测结果（不根据显卡名称保证任何后端可用）
# ---------------------------------------------------------------------------

@dataclass
class Capability:
    """P02 — 一个候选后端的**能力探测结果**（纯数据，可跨进程/回归）。

    * ``mode`` — 该候选对应的 :class:`BackendMode`。
    * ``available`` — 该后端当前环境是否**真正可用**（设备 + 驱动 +
      surface 可创建；不根据显卡名称保证，必须实测，P02）。
    * ``adapter`` — 适配器标识（如 "d3d12" / "vulkan" / "software"；
      CPU 模式为 "cpu"）。
    * ``reason`` — 不可用原因（``available=False`` 时供诊断面板
      解释，P02"UI 显示 adapter / backend / fallback 状态"）。
    * ``supports`` — 能力位集合（如 ``{"rgba8", "srgb", "resize"}``；
      本批契约先留空 / CPU 全支持）。
    """
    mode: BackendMode
    available: bool
    adapter: str = ""
    reason: str = ""
    supports: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# P02 — 适配器选择与能力探测（可注入 device / probe，纯逻辑回归不依赖
# 真实设备；wgpu-py 不可用 → 全部回退 CPU）
# ---------------------------------------------------------------------------

class AdapterProbe:
    """P02 — 适配器选择与能力探测（纯逻辑核心）。

    职责（依据 P02"适配器选择与能力探测" + P13"可选依赖与 CPU 回退"）：

    - :meth:`probe` — 探测候选后端，产出 :class:`Capability` 列表
      （``device_factory`` 可注入 fake，使纯逻辑回归不依赖真实 GPU）；
    - :meth:`select` — 按优先级（gpu → cpu）选出**首个可用**后端；
      无可用 GPU 时回退 CPU（P13 契约——UI 不崩）；
    - :meth:`describe` — 诊断串（供 G10 诊断区 / 状态栏显示 adapter /
      backend / fallback 状态，P02 验收）。

    本类**不**自行创建真实 wgpu 设备（活 GPU 验证留后续批次）；
    默认 ``device_factory`` 返回 ``None``（模拟 wgpu-py 不可用环境），
    此时 :meth:`probe` 对 GPU 候选报 ``available=False`` + reason，
    CPU 候选恒可用。
    """

    def __init__(self,
                 device_factory: Optional[Callable[[], object]] = None) -> None:
        # 注入点：真实环境用 ``wgpu.create_device`` 类工厂；纯逻辑
        # 回归用 fake（返回 None 模拟 wgpu-py 不可用，UI 不崩）。
        self._device_factory = device_factory

    def probe(self) -> list:
        """P02 — 探测所有候选后端，返回 :class:`Capability` 列表。

        顺序固定（gpu 候选在前，cpu 兜底在后）；``device_factory``
        返回非 None → GPU 可用；异常 / None → GPU 不可用（回退 CPU，
        P13）。
        """
        caps = []
        # --- GPU 候选（P02：不根据名称保证可用，须实测 device）-------
        gpu_ok = False
        gpu_reason = ""
        gpu_adapter = ""
        if self._device_factory is not None:
            try:
                dev = self._device_factory()
                if dev is not None:
                    gpu_ok = True
                    gpu_adapter = "wgpu"
                else:
                    gpu_reason = "device_factory returned None"
            except Exception as e:
                gpu_reason = "device_factory raised: " + repr(e)
        else:
            gpu_reason = "no device_factory (wgpu-py optional dep absent)"
        caps.append(Capability(
            mode=BackendMode.GPU,
            available=gpu_ok,
            adapter=gpu_adapter,
            reason="" if gpu_ok else gpu_reason,
            supports={"rgba8", "resize"} if gpu_ok else {},
        ))
        # --- CPU 兜底（恒可用，纯标准库参考实现，P01 模式边界）-----
        caps.append(Capability(
            mode=BackendMode.CPU,
            available=True,
            adapter="cpu",
            supports={"rgba8", "srgb", "resize", "snapshot"},
        ))
        return caps

    def select(self, preferred: BackendMode = BackendMode.GPU) -> Capability:
        """P02 + P13 — 按优先级选**首个可用**后端；无可用则回退
        CPU（``available=True`` 的 cpu 候选恒存在，UI 不崩）。

        *preferred* — 调用方期望模式（gpu / cpu）。期望 gpu 但 gpu
        不可用时**自动回退 cpu**（P13"可选依赖、打包与 CPU 回退"），
        并在 :attr:`Capability.reason` 记录回退原因供诊断面板解释。
        """
        caps = self.probe()
        if preferred == BackendMode.CPU:
            for c in caps:
                if c.mode == BackendMode.CPU:
                    return c
        # 期望 gpu：先找可用 gpu
        for c in caps:
            if c.mode == BackendMode.GPU and c.available:
                return c
        # gpu 不可用 → 回退 cpu（P13），记录回退原因
        for c in caps:
            if c.mode == BackendMode.CPU:
                if not c.reason:
                    c.reason = "gpu unavailable, fell back to cpu"
                return c
        raise RuntimeError("AdapterProbe.select: 无可用后端（契约违例）")

    def describe(self) -> str:
        """P02 验收 — 诊断串（adapter / backend / fallback 状态，供
        G10 诊断区 / 状态栏）。"""
        parts = []
        for c in self.probe():
            tag = c.adapter or c.mode.value
            if c.available:
                parts.append(f"{tag}=ok")
            else:
                parts.append(f"{tag}=off({c.reason})")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# P01 — RenderBackend 接口契约（统一呈现 / 快照 / 释放）
# ---------------------------------------------------------------------------

class RenderBackend:
    """P01 — 渲染后端**接口契约**（统一 ``render`` / ``present`` /
    ``snapshot`` / ``release_document``，cpu / gpu 两种实现共用）。

    依据 GPU 方案 P01"GPU 接口与模式边界"：

    .. code-block:: text

        RenderBackend.render(scene, viewport, damage) -> FrameHandle
        RenderBackend.present(frame, surface)
        RenderBackend.snapshot(frame) -> CPUImage
        RenderBackend.release_document(document_id)

    本基类定义方法签名 + 默认行为（``render`` 抽象、``present`` /
    ``snapshot`` / ``release_document`` 提供 CPU 参考语义）；GPU 后端
    子类覆写 ``present`` 直接呈现 GPU surface（不隐式读回，P01
    "不能把每个 GPU render 的返回值强制要求为 Bitmap，否则每帧都
    隐含一次读回"）。子类经 :attr:`mode` 声明自身模式（P01 模式
    边界，显式可探测）。
    """

    #: P01 模式边界——子类声明自身 :class:`BackendMode`（默认 CPU 参考）。
    mode: BackendMode = BackendMode.CPU

    def __init__(self) -> None:
        # P01 — 文档资源注册表（release_document 释放，C01 文档版本门控）。
        self._documents: set = set()

    # -- 接口契约（子类覆写）--------------------------------------------

    def render(self, scene, viewport, damage=None):
        """P01 — 渲染 *scene*（有序图元 / 资源 / 失效区域）到 *viewport*
        （CSS 尺寸 / 物理尺寸 / DPR / 缩放 / 滚动偏移），产出
        :class:`~toy_browser.rendering.rasterize_cpu.FrameHandle`。

        基类抛 :class:`NotImplementedError`；:class:`CPURenderBackend`
        经 ``paint_engine.rasterize_to_canvas`` 直画实现（批次 45）。
        """
        raise NotImplementedError(
            "RenderBackend.render: 子类须实现（P01 契约）")

    def present(self, frame, surface=None) -> bool:
        """P01 — 把 *frame* 呈现到 *surface*。

        CPU 参考语义：无真实 surface，直接成功（GUI 经
        ``frame.image`` 的 PPM/PhotoImage 消费）；返回 True。GPU 子类
        覆写为**直接 present GPU surface**（不隐式 CPU 读回，P01 验收
        "常规交互无每帧 snapshot / 读回"）。
        """
        return frame is not None

    def snapshot(self, frame):
        """P01 — 取回 *frame* 的 CPU 快照（:class:`CPUImage`，供截图 /
        PNG 导出 / 逐像素验证——"交互路径直接 present，截图才异步
        回读"，P01 第三阶段"分清呈现 / 合成 / 光栅化"）。

        CPU 参考语义：帧本身即 CPU buffer（``frame.image``），直接
        返回；GPU 子类覆写为异步读回（本批契约不实现真实读回）。
        """
        return getattr(frame, "image", None)

    def release_document(self, document_id: int) -> bool:
        """P01 — 释放 *document_id* 的文档资源（C01 文档版本门控；
        G09 step 4 关标签不泄漏）。返回是否释放了存在的文档。"""
        if document_id in self._documents:
            self._documents.discard(document_id)
            return True
        return False

    def register_document(self, document_id: int) -> None:
        """P01 — 注册 *document_id*（文档首次渲染时调用，资源归属
        该文档，便于 :meth:`release_document` 释放）。"""
        self._documents.add(document_id)

    # -- 诊断 ------------------------------------------------------------

    def describe(self) -> str:
        """P01 / P02 诊断——后端模式 + 已注册文档数（供 G10 诊断区
        显示 backend 状态）。"""
        return (f"mode={self.mode.value} documents={len(self._documents)}")


__all__ = [
    "BackendMode",
    "Capability",
    "AdapterProbe",
    "RenderBackend",
]
