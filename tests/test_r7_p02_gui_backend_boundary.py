"""R7-P02 §254 — GUI 后端边界契约（surface / 读回 / resize / device-lost）。

Round-7 评估（``逐项改进实施方案.md`` R7-P02 第 5 点，``渲染与标准
差距审计.md`` §5）：

    连接原生 surface/present/swapchain、resize/device-lost，明确
    GUI 后端边界；**普通帧不读回，截图才读回**。

本批落地"明确 GUI 后端边界"的**纯逻辑决策层**（无 wgpu / 无 OS，
P13 不崩）：

* :class:`SurfacePresentGate` — surface 生命周期门控（present 前置
  决策：cpu / no-surface / present）。
* :class:`ReadbackPolicy` — 读回策略（常规帧不读回，截图才读回；
  审计 §5 "CPU 图上传再读回一致只算传输正确，不算加速"）。
* :class:`ResizeLostRecovery` — resize / device-lost 恢复状态机
  （失效标记 + 降级 CPU 决策 + 事件序号）。

两层设计（"不冒充通过"——区分静态契约与动态真实调用）：

* **静态层**——源码契约字符串断言：gate 三态、读回策略常量、
  recovery 状态机方法就位、gpu.py present 经 gate 门控 / snapshot
  经 readback 门控 / 新增 device-lost / resize 入口。
* **动态层**——真实调用：SurfacePresentGate 三态判定、ReadbackPolicy
  常规帧 False / 截图 True、ResizeLostRecovery 设备 lost→失效 /
  resize→失效 / 恢复→需重 render / render 完成→清失效 / 降级 CPU
  决策、GPUBackend 上 present 契约不变 + handle_device_lost /
  request_resize / gui_boundary 快照。

无 OS / wgpu 依赖即可回归（P13 不崩）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering import gui_backend_boundary as _gbb
from toy_browser.rendering import paint_engine as _pe
from toy_browser.rendering.paint_engine import DisplayCommand
from toy_browser.rendering import gpu as _gpu

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read_module(name):
    import importlib
    mod = importlib.import_module(name)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(
        os.path.join(base, "toy_browser", "rendering",
                     os.path.basename(mod.__file__)),
        encoding="utf-8").read()


# ----------------------------------------------------------------------
# 静态层——源码契约断言
# ----------------------------------------------------------------------

@_test
def test_static_gate_three_states():
    """gate 源码含三态决策常量（cpu / no-surface / present）。"""
    src = _read_module("toy_browser.rendering.gui_backend_boundary")
    assert "DECISION_CPU" in src
    assert "DECISION_NO_SURFACE" in src
    assert "DECISION_PRESENT" in src


@_test
def test_static_readback_policy_capture_only():
    """读回策略源码含常规/截图用途常量 + "截图才读回"语义。"""
    src = _read_module("toy_browser.rendering.gui_backend_boundary")
    assert "USE_PRESENT" in src and "USE_CAPTURE" in src, (
        "读回策略必须有 present / capture 两种用途常量")
    assert "allow_readback" in src, "必须有 allow_readback 判定入口"


@_test
def test_static_recovery_state_machine_methods():
    """recovery 源码含 lost / restored / resize / render-completed /
    fallback 决策入口。"""
    src = _read_module("toy_browser.rendering.gui_backend_boundary")
    for m in ("on_device_lost", "on_device_restored", "on_resize",
              "on_render_completed", "should_fallback_to_cpu",
              "needs_re_render"):
        assert m in src, f"recovery 缺 {m} 契约方法"


@_test
def test_static_gpu_present_via_gate():
    """gpu.py present 经 SurfacePresentGate 门控（非直接 if）。"""
    src = _read_module("toy_browser.rendering.gpu")
    assert "self._gate.decide" in src, "present 必须经 gate.decide 门控"
    assert "SurfacePresentGate" in src


@_test
def test_static_gpu_snapshot_via_readback():
    """gpu.py snapshot 经 ReadbackPolicy 门控（is_capture 参数）。"""
    src = _read_module("toy_browser.rendering.gpu")
    assert "is_capture" in src, "snapshot 必须带 is_capture 读回策略参数"
    assert "self._readback.allow_readback" in src, (
        "snapshot 必须经 ReadbackPolicy.allow_readback 门控")
    assert "handle_device_lost" in src and "request_resize" in src, (
        "gpu 必须暴露 device-lost / resize 边界入口")


# ----------------------------------------------------------------------
# 动态层——真实调用
# ----------------------------------------------------------------------

@_test
def test_dynamic_gate_decides_three_states():
    """gate 三态：无 GPU→cpu；有 GPU 无 surface→no-surface；有
    GPU 有 surface→present。"""
    gate = _gbb.SurfacePresentGate()
    assert gate.decide(False, None) == "cpu"
    assert gate.decide(False, "dummy-surface") == "cpu"  # 无 GPU 恒 cpu
    assert gate.decide(True, None) == "no-surface"
    assert gate.decide(True, "surface-obj") == "present"


@_test
def test_dynamic_readback_normal_frame_no_readback():
    """常规交互帧（非 capture）不读回（P01 "常规交互无每帧读回"）。"""
    pol = _gbb.ReadbackPolicy()
    assert pol.allow_readback(_gbb.USE_PRESENT) is False, (
        "常规 present 帧必须不读回")
    # 显式截图（is_capture）才读回。
    assert pol.allow_readback(_gbb.USE_CAPTURE, is_capture=True) is True
    # 常规帧即便标记 is_capture（截图场景）也读回（截图才读回）。
    assert pol.allow_readback(_gbb.USE_PRESENT, is_capture=True) is True


@_test
def test_dynamic_readback_policy_describe():
    """读回策略 describe 标注"仅显式截图读回"（验收可解释）。"""
    pol = _gbb.ReadbackPolicy()
    desc = pol.describe()
    assert "capture-only" in desc and "截图才读回" in desc, (
        f"describe 必须标注读回语义: {desc!r}")


@_test
def test_dynamic_recovery_device_lost_invalidates():
    """设备 lost → 失效标记 + needs_re_render=True + 事件序号。"""
    rec = _gbb.ResizeLostRecovery()
    assert rec.needs_re_render is False
    seq = rec.on_device_lost()
    assert seq >= 1, f"事件序号须递增: {seq}"
    assert rec.device_lost is True
    assert rec.invalidated is True
    assert rec.needs_re_render is True
    assert rec.last_lost_at == seq


@_test
def test_dynamic_recovery_resize_invalidates():
    """resize → 已产帧失效（旧尺寸不匹配），needs_re_render=True。"""
    rec = _gbb.ResizeLostRecovery()
    seq = rec.on_resize(1280, 720)
    assert rec.invalidated is True
    assert rec.needs_re_render is True
    assert rec.last_resize == seq


@_test
def test_dynamic_recovery_render_clears_invalidation():
    """有效 render 完成（非 lost）→ 清失效标记；lost 态不清。"""
    rec = _gbb.ResizeLostRecovery()
    rec.on_resize(100, 100)
    assert rec.needs_re_render is True
    rec.on_render_completed()
    assert rec.needs_re_render is False, "非 lost 态 render 完成须清失效"
    # lost 态 render 完成不清（需先 on_device_restored 重建）。
    rec2 = _gbb.ResizeLostRecovery()
    rec2.on_device_lost()
    rec2.on_render_completed()
    assert rec2.needs_re_render is True, (
        "设备 lost 态下 render 完成不得清失效（纹理全失效）")


@_test
def test_dynamic_recovery_fallback_cpu_decision():
    """降级 CPU 决策——设备 lost + 无 surface → 退化 CPU；有
    surface 则不降级（走 GPU 重走通路）。"""
    rec = _gbb.ResizeLostRecovery()
    rec.on_device_lost()
    assert rec.should_fallback_to_cpu(has_surface=False) is True
    assert rec.should_fallback_to_cpu(has_surface=True) is False
    # 非 lost 态（常态）不降级 CPU（正常 GPU 通路）。
    rec2 = _gbb.ResizeLostRecovery()
    assert rec2.should_fallback_to_cpu(has_surface=False) is False, (
        "常态（未 lost）不触发 CPU 降级")


@_test
def test_dynamic_gpu_backend_present_contract_unchanged():
    """GPUBackend.present 经 gate 门控后契约不变：

    * CPU 形态（无 wgpu 设备）+ 无 surface → gate 判 cpu → True。
    * 真实 wgpu 设备 + 无 surface → gate 判 no-surface → "no-surface"
      （P13 不崩，headless 优雅降级）。

    按 ``be.is_real_gpu`` 适配两种环境（受管无 wgpu / 完整有 wgpu），
    均验证"present 经 gate 门控且契约不变（不抛错、可区降）"。
    """
    dl = _pe.DisplayList()
    dl.commands.append(DisplayCommand(
        "rect", x=10, y=10, w=50, h=40,
        color=_pe.Color(200, 30, 30, 255)))
    be = _gpu.GPUBackend(device=None)
    frame = be.render(dl, {"width": 200, "height": 200})
    if be.is_real_gpu:
        # 真实 wgpu 设备 + 无 surface → no-surface 可区降标记。
        assert be.present(frame, None) == "no-surface", (
            "真实 GPU 无 surface 须返回 'no-surface'（P13 不崩）")
    else:
        # CPU 回退形态（无 wgpu 设备）→ True。
        assert be.present(frame, None) is True, (
            "CPU 回退形态 present 须返回 True（P13 不崩）")
    # frame is None → False（与 gate 无关，present 前置守卫）。
    assert be.present(None, None) is False


@_test
def test_dynamic_gpu_backend_device_lost_and_boundary():
    """GPUBackend 边界入口——handle_device_lost / request_resize /
    gui_boundary 快照 + 降级决策联动。"""
    dl = _pe.DisplayList()
    dl.commands.append(DisplayCommand(
        "rect", x=0, y=0, w=10, h=10, color=_pe.Color(20, 30, 40, 255)))
    be = _gpu.GPUBackend(device=None)
    frame = be.render(dl, {"width": 100, "height": 100})
    assert be.needs_re_render() is False, "新 render 后常态不需重走"
    # 设备 lost → 失效 + 需重走 + 降级 CPU（无 surface）。
    be.handle_device_lost()
    assert be.needs_re_render() is True
    assert be._recovery.should_fallback_to_cpu(has_surface=False) is True
    # 设备恢复 → 仍失效（旧纹理失效，需重 render）。
    be.handle_device_restored()
    assert be.needs_re_render() is True
    # resize → 失效；mark_render_completed（非 lost）清失效。
    be.request_resize(200, 150)
    assert be.needs_re_render() is True
    be.mark_render_completed()
    assert be.needs_re_render() is False
    # gui_boundary 快照可解释。
    gb = be.gui_boundary
    assert gb["present_gate"] == "SurfacePresentGate"
    assert "截图才读回" in gb["readback_policy"]
    assert gb["resize_lost"]["device_lost"] is False


@_test
def test_dynamic_gpu_backend_snapshot_readback_policy():
    """GPUBackend.snapshot 经读回策略：

    * 常规帧（is_capture=False）不读回——CPU 形态返回 frame.image；
      有 wgpu 设备 + 纹理时本也应读回，但常规交互帧按策略不读回
      （读回策略门控优先于设备形态）。
    * is_capture=True（截图）才读回——CPU 形态（无 wgpu 纹理）退化
      返回 frame.image；真实 wgpu 设备 + 纹理时经 read_back 产出
      新的 CPUImage（非 None）。
    """
    dl = _pe.DisplayList()
    dl.commands.append(DisplayCommand(
        "rect", x=0, y=0, w=10, h=10, color=_pe.Color(1, 2, 3, 255)))
    be = _gpu.GPUBackend(device=None)
    frame = be.render(dl, {"width": 50, "height": 50})
    # 常规帧（is_capture 默认 False）→ 读回策略判不读回 → frame.image。
    assert be.snapshot(frame) is frame.image, (
        "常规交互帧不读回，须回退 frame.image")
    cap = be.snapshot(frame, is_capture=True)
    # 截图帧：CPU 形态（无 wgpu 设备/纹理）退化 frame.image；
    # 真实 wgpu 设备 + 纹理则经 read_back 产出新 CPUImage（非 None）。
    if be.is_real_gpu and getattr(frame, "texture", None) is not None:
        assert cap is not None and cap is not frame.image, (
            "真实 GPU capture 读回应产出新 CPUImage（经 read_back）")
    else:
        assert cap is frame.image, (
            "CPU 形态 capture 无纹理可回退 frame.image（P13 不崩）")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")
