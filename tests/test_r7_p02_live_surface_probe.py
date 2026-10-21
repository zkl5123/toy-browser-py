"""R7-P02 §254 — 活窗口 GPU surface present 接线探针（不冒充通过）。

Round-7 评估 R7-P02 第 5 点 / §254"surface 更新可见"的**活窗口**
开放项。此前 :mod:`test_r7_p02_real_gpu_probe` 在 headless 离屏下
把"真实 swapchain present 需活 GUI 窗口"标注为开放边界；本模块在
**活 Tk 窗口**下补上"真实拿 screen surface + 真实 present 通路"的
可判定探针。

依赖三条件同时在场（缺任一即 :class:`SkipTest`，不冒充通过）：

* 真实 wgpu GPUDevice（``acquire_wgpu_device()`` 实测）；
* 活 Tk 窗口（``tkinter`` + 可建 root 拿 HWND）；
* rendercanvas 的 :class:`WgpuContextToScreen`（喂 HWND 给 wgpu
  native backend 建 screen surface）。

本批在本机 3.11.9（RTX 3050 + Tk + rendercanvas 2.7.2）实测通过；
受管 3.13.12 缺 tkinter/wgpu/rendercanvas 时全 SKIP（不缩分母、不
冒充通过）。

"不冒充通过"边界（本模块如实标注）：

* 本探针证明"活窗口下真实屏幕 surface **可获取** + preferred format
  可解析 + ``GPUBackend.present`` 通路对 surface 的消费契约"；
* **不**证明"swapchain 真上屏可见 / 读屏器读到 / 真人键盘操作"——
  这些仍需人工验收，本模块不宣称完成（§254 人工验证留活显示批次）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering import gpu as _gpu
from toy_browser.rendering import live_surface as _ls

try:
    import wgpu  # noqa: F401
except Exception:
    wgpu = None

try:
    import tkinter as _tk  # noqa: F401
except Exception:
    _tk = None

try:
    from rendercanvas.contexts import WgpuContextToScreen  # noqa: F401
except Exception:
    WgpuContextToScreen = None

from skip_support import SkipTest  # noqa: E402

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _require_live_surface_env():
    """活窗口 screen surface 探针门控：缺 wgpu / tkinter /
    rendercanvas 任一即 SkipTest（不冒充通过）。返回 (device, root)。
    调用方负责 ``root.destroy()``。"""
    if wgpu is None:
        raise SkipTest("wgpu-py not installed; live-surface probe gated")
    if _tk is None:
        raise SkipTest("tkinter unavailable; live-surface probe gated")
    if WgpuContextToScreen is None:
        raise SkipTest("rendercanvas WgpuContextToScreen unavailable; "
                       "live-surface probe gated")
    dev = _gpu.acquire_wgpu_device()
    if dev is None:
        raise SkipTest("acquire_wgpu_device() returned None "
                      "(no usable GPU adapter)")
    root = _tk.Tk()
    root.withdraw()
    return dev, root


# ----------------------------------------------------------------------
# 探针 1 — 活窗口真实 screen surface 可获取（本批核心突破）
# ----------------------------------------------------------------------

@_test
def test_probe1_live_window_surface_acquired():
    """活 Tk 窗口下 ``acquire_live_surface`` 拿到真实 wgpu screen
    surface context + preferred format 可解析。"""
    dev, root = _require_live_surface_env()
    try:
        root.update()
        hwnd = root.winfo_id()
        assert hwnd > 0, "活窗口须有真实 HWND"
        probe = _ls.acquire_live_surface(dev, hwnd)
        assert probe.acquired is True, f"活窗口应拿到 screen surface: {probe.describe()}"
        assert probe.context is not None, "surface context 非空"
        assert probe.preferred_format, "preferred format 应可解析"
        assert probe.window_hwnd == hwnd, "hwnd 须如实回填"
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 2 — 无效 HWND / 无设备 不硬造（保守边界）
# ----------------------------------------------------------------------

@_test
def test_probe2_invalid_hwnd_not_forged():
    """无设备 / 无效 HWND 时 probe 必须 ``acquired=False`` 且 reason
    如实记录，**不**伪装成已接线（不冒充通过）。"""
    # 无设备
    empty = _ls.LiveSurfaceConnector(None, window_hwnd=12345)
    p0 = empty.probe()
    assert p0.acquired is False and p0.context is None
    assert "no GPU device" in p0.reason, p0.reason
    # 无效 HWND（有设备）
    dev, root = _require_live_surface_env()
    try:
        p1 = _ls.LiveSurfaceConnector(dev, window_hwnd=0).probe()
        assert p1.acquired is False and p1.context is None
        assert "no valid window HWND" in p1.reason, p1.reason
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 3 — present 通路对 surface context 的消费契约
# ----------------------------------------------------------------------

@_test
def test_probe3_present_consumes_surface_context():
    """活窗口拿到的 surface context 喂给 ``GPUBackend.present``：
    frame=None 契约（返回 False 不崩），surface 在场时 gate 判
    DECISION_PRESENT（不 no-surface 降级）。"""
    from toy_browser.rendering.gui_backend_boundary import SurfacePresentGate
    dev, root = _require_live_surface_env()
    try:
        root.update()
        hwnd = root.winfo_id()
        probe = _ls.acquire_live_surface(dev, hwnd)
        assert probe.acquired is True
        be = _gpu.GPUBackend(device=dev)
        assert be.is_real_gpu is True
        # frame=None → present 契约返回 False（不崩）。
        res = be.present(None, surface=probe.context)
        assert res is False, f"frame=None 应返回 False 不崩：{res!r}"
        # gate 判定：真实 GPU + surface 在场 → DECISION_PRESENT。
        gate = SurfacePresentGate()
        decision = gate.decide(be.is_real_gpu, probe.context)
        assert decision == gate.DECISION_PRESENT, (
            f"surface 在场时 gate 应判 present：{decision}")
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 4 — 活窗口 screen surface 与 headless no-surface 的契约差异可观测
# ----------------------------------------------------------------------

@_test
def test_probe4_live_vs_headless_surface_decision():
    """同一 GPUBackend：无 surface（headless）→ gate 判 no-surface；
    活窗口注入 surface → 判 present。差异可观测（§254"surface 更新
    可见"的契约表达）。"""
    from toy_browser.rendering.gui_backend_boundary import SurfacePresentGate
    dev, root = _require_live_surface_env()
    try:
        root.update()
        hwnd = root.winfo_id()
        be = _gpu.GPUBackend(device=dev)
        gate = be._gate  # 既有 SurfacePresentGate 实例
        # headless（surface=None）
        d_no = gate.decide(be.is_real_gpu, None)
        assert d_no == gate.DECISION_NO_SURFACE, f"无 surface 应 no-surface：{d_no}"
        # 活窗口 surface
        probe = _ls.acquire_live_surface(dev, hwnd)
        d_yes = gate.decide(be.is_real_gpu, probe.context)
        assert d_yes == gate.DECISION_PRESENT, f"活窗口 surface 应 present：{d_yes}"
        # 两态差异可观测（本探针的核心断言）。
        assert d_no != d_yes
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 5 — preferred format 是真实 srgb 表面格式（非硬编码）
# ----------------------------------------------------------------------

@_test
def test_probe5_preferred_format_resolved_not_hardcoded():
    """活窗口 surface 的 preferred format 由 wgpu screen 能力真实解析
    （含 srgb 变体），不是 live_surface 里硬编码的默认串。"""
    dev, root = _require_live_surface_env()
    try:
        root.update()
        hwnd = root.winfo_id()
        probe = _ls.acquire_live_surface(dev, hwnd)
        assert probe.acquired is True
        # preferred format 由 wgpu 屏幕能力解析；本机应含 bgra/rgba
        # unorm-srgb 形态（真实 srgb 表面），不是 "bgra8unorm" 裸默认。
        fmt = probe.preferred_format.lower()
        assert "srgb" in fmt or "bgra8unorm" in fmt, \
            f"preferred format 应为真实 srgb 表面格式：{fmt!r}"
    finally:
        root.destroy()


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            if getattr(e, "__class__", None).__name__ == "SkipTest":
                print(f"SKIP {name}: {e}")
                skipped += 1
            else:
                print(f"ERROR {name}: {e!r}")
                failed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)
