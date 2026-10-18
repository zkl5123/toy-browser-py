"""批次 47 — P01–P13 GPU 光栅化迁移（第一阶段：统一渲染契约 + P01/P02 纯逻辑核心）。

依据 ``assessments/20261016-round5/docs/GPU光栅化迁移方案.md`` P01"GPU
接口与模式边界" + P02"适配器选择与能力探测" + P13"可选依赖、打包与
CPU 回退"。本包落地**可回归的纯逻辑核心**（不引入 wgpu-py 依赖、
不触碰真实 GPU）：

- :mod:`contracts` — :class:`RenderBackend` 接口契约 + :class:`BackendMode`
  （cpu / gpu 模式边界）+ :class:`AdapterProbe`（无真实设备时的能力
  探测 + CPU 回退，P02）。
- :mod:`cpu_backend` — :class:`CPURenderBackend`（CPU 参考后端，经批次
  45 的 ``paint_engine.rasterize_to_canvas`` 直画 :class:`CPUCanvas` +
  :class:`CPUImage` 快照，P01 接口的 CPU 实现）。

GPU 后端（:mod:`toy_browser.rendering.gpu`）与 ``wgpu-py`` 可选依赖
留后续批次（本环境无出站网络，装不上 wgpu-py，也无法活 GPU 验证）；
P13 契约先落地：``create_backend("gpu")`` 在 wgpu-py 不可用时自动
回退到 :class:`CPURenderBackend`（UI 不崩，诊断面板可解释原因）。
"""

from toy_browser.rendering.renderer.contracts import (
    RenderBackend,
    BackendMode,
    AdapterProbe,
    Capability,
)
from toy_browser.rendering.renderer.cpu_backend import CPURenderBackend

__all__ = [
    "RenderBackend",
    "BackendMode",
    "AdapterProbe",
    "Capability",
    "CPURenderBackend",
]
