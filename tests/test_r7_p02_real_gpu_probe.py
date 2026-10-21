"""R7-P02 真实 GPU 探针 — 在**有 wgpu 设备**的环境（本机 RTX 3050）
实测 surface / readback / present 的"本机可验证层"，把离屏 headless
**无法**验证的"真实 swapchain present"显式标注为开放边界。

Round-7 评估（``渲染与标准差距审计.md`` §5 / ``逐项改进实施方案.md``
R7-P02 第 4、5 点，``assessments/20261018-round7/``）的最高价值
开放项：

* **P02 实测可用** —— 不根据显卡名称保证，``acquire_wgpu_device()``
  必须真拿到 :class:`wgpu.GPUDevice`；
* **§253 独立 oracle** —— ``render_with_pass`` 产帧 vs 独立
  ``cpu_oracle_frame`` 逐像素对照 0 mismatch（"GPU 实际绘制单独通路
  像素对照通过"的**本机**等价形态——经 wgpu 上传/读回路径而非纯
  CPU 软件 pass）；
* **§254 读回策略** —— "常规帧不读回，截图才读回"在**真实 wgpu
  设备 + 真实纹理**上走一遍（``snapshot(frame)`` 与
  ``snapshot(frame, is_capture=True)`` 差异可观测）；
* **§254 surface 边界** —— headless 离屏无真实 window 句柄，
  ``present`` 经 gate 判 ``no-surface`` 可区降，**不崩**；真实
  swapchain present **不在本环境验证**（需活 GUI 窗口接线批次），
  本模块只做"本机能实测到的部分"+ 边界标注。

"不冒充通过"：本模块**不在无 wgpu 环境里伪装通过**——真实 GPU
探针全部 ``SkipTest("wgpu not available ...")``，只在**本机有
wgpu 设备**时才真跑（本机 3.11.9 完整环境实测通过；受管 3.13 无
wgpu 自动 SKIP，不缩分母、不冒充通过）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering import paint_engine as _pe
from toy_browser.rendering.paint_engine import DisplayCommand
from toy_browser.rendering import gpu as _gpu
from toy_browser.rendering import gui_backend_boundary as _gbb
from toy_browser.rendering import render_pass as _rp

try:
    import wgpu  # noqa: F401
except Exception:
    wgpu = None

from skip_support import SkipTest  # noqa: E402

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _require_wgpu():
    """环境门控：无 wgpu 设备 → SkipTest（本机无 wgpu 时显式 SKIP）。"""
    if wgpu is None:
        raise SkipTest("wgpu-py not installed in this environment; "
                       "real-GPU probes are gated (no pass-masking)")
    dev = _gpu.acquire_wgpu_device()
    if dev is None:
        raise SkipTest("acquire_wgpu_device() returned None "
                       "(no usable GPU adapter in this environment)")
    return dev


# ----------------------------------------------------------------------
# 探针 1 — P02 实测可用（不根据显卡名称保证，真拿到 GPUDevice）
# ----------------------------------------------------------------------

@_test
def test_probe1_acquired_real_gpu_device():
    """P02 — ``acquire_wgpu_device`` 在本机实测拿到真实 wgpu GPUDevice。

    不根据显卡名称保证任何后端可用；断言拿到的是 wgpu 真设备对象
    （含 device 生命周期 API），而非模拟/fake。
    """
    dev = _require_wgpu()
    # 真 wgpu 设备有 queue / create_buffer 等 wgpu.GPUDevice 契约方法。
    assert hasattr(dev, "queue"), "GPUDevice 必须有 queue"
    assert hasattr(dev, "create_buffer"), "GPUDevice 必须有 create_buffer"
    assert hasattr(dev, "destroy"), "GPUDevice 必须有 destroy 生命周期"
    # GPUBackend 经真设备构造后 is_real_gpu 必须为 True。
    be = _gpu.GPUBackend(device=dev)
    assert be.is_real_gpu is True, (
        "注入真 wgpu 设备后 is_real_gpu 必须 True（P02 实测）")
    dev.destroy()


# ----------------------------------------------------------------------
# 探针 2 — §253 独立 oracle + 真实 wgpu 上传/读回 0 mismatch
# ----------------------------------------------------------------------

def _build_probe_dl():
    """手工构造含 rect + img 的 DisplayList（text 字形可选，
    保证 wgpu 上传像素与 oracle 对照稳定）。"""
    dl = _pe.DisplayList()
    # rect 红块。
    dl.commands.append(DisplayCommand(
        "rect", x=2, y=2, w=8, h=6, color=_pe.Color(10, 200, 30, 255)))
    # img 蓝像素最近邻放大。
    blue = [0x0000FF] * 16
    dl.commands.append(DisplayCommand(
        "img", x=16, y=16, w=8, h=8,
        img_pixels=blue, img_src_w=4, img_src_h=4))
    return dl


@_test
def test_probe2_render_pass_oracle_zero_mismatch_real_gpu():
    """§253 — ``render_with_pass``（经 wgpu 上传/读回）+ 独立 CPU
    oracle 逐像素对照 0 mismatch。

    与"无 wgpu 纯软件 pass"不同——本探针在**真 wgpu 设备**上
    走 :meth:`GPUBackend.render_with_pass`（内部经
    ``_upload_frame_rgba8`` 上传 wgpu 纹理），再经
    :meth:`verify_frame_vs_oracle` 独立 oracle（``rasterize_to_canvas``
    不经 pass）对照。0 mismatch 即"GPU 侧真实上传/读回 + 图元通路
    都正确"。
    """
    _require_wgpu()
    dl = _build_probe_dl()
    be = _gpu.GPUBackend(device=_gpu.acquire_wgpu_device())
    frame = be.render_with_pass(dl, {"width": 64, "height": 64})
    assert frame.frame_pass is True, "必须带 frame_pass 标记"
    assert frame.texture is not None, (
        "真 wgpu 设备上 render_with_pass 必须上传纹理（texture 非 None）")
    cmp = be.verify_frame_vs_oracle(
        frame, dl, {"width": 64, "height": 64}, tolerance=0)
    assert cmp["ok"], (
        f"真 GPU pass 帧 vs 独立 oracle 不一致: {cmp.get('count')} mismatch")
    assert cmp["oracle_mode"] == "rasterize_to_canvas", (
        "oracle 必须标注独立通路 rasterize_to_canvas")


@_test
def test_probe2b_real_texture_upload_readback_consistent():
    """§253 / 审计 §5 — 真实 wgpu 上传 + 读回**逐像素一致**。

    "CPU 图上传再读回一致只算传输正确，不算加速"——本探针锁定
    这条口径：上传 N 个红像素到 wgpu 显存 → 读回 → 与上传字节
    逐像素一致（RGBA 4 字节/像素）。
    """
    _require_wgpu()
    dev = _gpu.acquire_wgpu_device()
    raster = _gpu.GPURasterizer(dev)
    w, h = 16, 16
    buf = bytes([255, 0, 0, 255] * (w * h))  # 全红不透明
    tex = raster.upload_rgba8(w, h, buf, label="probe2b")
    assert tex is not None, "真 wgpu 上传必须返回纹理句柄"
    back = raster.read_back(tex, w, h)
    assert len(back) == w * h * 4, (
        f"读回字节数 {len(back)} != 期望 {w * h * 4}")
    # 逐像素一致（不冒充 GPU 加速，只锁传输正确）。
    assert back == buf, (
        f"读回像素与上传字节不一致（首 8 字节: "
        f"{list(back[:8])!r} vs 上传 {list(buf[:8])!r}）")
    dev.destroy()


# ----------------------------------------------------------------------
# 探针 3 — §254 读回策略（真 wgpu 设备 + 真纹理差异可观测）
# ----------------------------------------------------------------------

@_test
def test_probe3_readback_policy_capture_gating_on_real_device():
    """§254 — 真 wgpu 设备 + 真纹理下，``snapshot`` 受读回策略门控：

    * 常规交互帧（``is_capture=False``）→ 读回策略判**不读回** →
      回退 ``frame.image``（CPU 形态，**非** read_back 产物）；
    * 截图帧（``is_capture=True``）→ 读回策略判**读回** → 经
      ``read_back`` 产出**新 CPUImage**（非 ``frame.image`` 同一
      对象，且非 None）。
    """
    _require_wgpu()
    dl = _build_probe_dl()
    be = _gpu.GPUBackend(device=_gpu.acquire_wgpu_device())
    frame = be.render(dl, {"width": 64, "height": 64})
    assert frame.texture is not None, (
        "真 wgpu 设备下 render 必须上传纹理")
    # 常规帧：不读回 → 返回 frame.image（同一对象）。
    norm = be.snapshot(frame, is_capture=False)
    assert norm is frame.image, (
        "常规交互帧不读回，须回退 frame.image（同一对象）")
    # 截图帧：读回 → 经 read_back 产出新 CPUImage（非 frame.image）。
    cap = be.snapshot(frame, is_capture=True)
    assert cap is not None, "截图帧读回应产出 CPUImage"
    assert cap is not frame.image, (
        "截图读回应是**新** CPUImage（read_back 产物），非 frame.image")
    assert getattr(cap, "width", 0) == frame.width, (
        "读回 CPUImage 宽度须与帧一致")
    # 读回策略描述可解释（验收）。
    assert "capture-only" in be._readback.describe()
    dev_destroy = getattr(be, "_device", None)
    if dev_destroy is not None and hasattr(dev_destroy, "destroy"):
        dev_destroy.destroy()


# ----------------------------------------------------------------------
# 探针 4 — §254 surface 边界（headless 不崩 + 标注"真实 present"
#            留活窗口接线批次）
# ----------------------------------------------------------------------

@_test
def test_probe4_present_headless_returns_no_surface_not_crash():
    """§254 — headless 离屏下 ``present`` 经 gate 判
    ``no-surface`` 返回可区降标记（**不崩**），并把"真实 swapchain
    present"标注为开放边界（离屏无真实 window 句柄）。

    本探针锁定"本机**能实测**的 surface 边界层"：

    * 真 wgpu 设备 + 无 surface → gate ``DECISION_NO_SURFACE`` →
      ``present`` 返回字符串 ``"no-surface"``（可区降，非 False、
      非异常）；
    * 真 wgpu 设备 + 注入了**非真实** surface（``object()``）→
      ``_present_to_surface`` 捕获形态不匹配 / configure 失败，按
      P13 不崩契约返回 ``False``；
    * **真实 swapchain present 不在本环境验证**：离屏 wgpu 0.32
      的 ``get_canvas_context`` 需 ``present_info["window"]``
      （真实 HWND/GPU surface 句柄），headless 沙箱无 GUI 窗口 →
      真实 surface present **留活窗口接线批次**（本模块显式标注，
      不冒充"已实测真实 present"）。
    """
    _require_wgpu()
    dl = _build_probe_dl()
    be = _gpu.GPUBackend(device=_gpu.acquire_wgpu_device())
    frame = be.render(dl, {"width": 32, "height": 32})
    # 无 surface → "no-surface"（可区降，不崩）。
    r = be.present(frame, None)
    assert r == "no-surface", (
        f"headless 无 surface 须返回 'no-surface'，got {r!r}")
    # gate 三态对照（真 wgpu 设备）。
    gate = _gbb.SurfacePresentGate()
    assert gate.decide(be.is_real_gpu, None) == "no-surface"
    # 注入非真实 surface（object()）→ P13 不崩返回 False。
    r2 = be.present(frame, object())
    assert r2 is False, (
        f"注入非真实 surface 须 P13 不崩返回 False，got {r2!r}")
    # 注入 dict 形态 surface（无 context）→ 解 surface 形态后
    # 因 ctx=None 返回 False（P13 不崩）。
    r3 = be.present(frame, {"context": None, "format": "bgra8unorm",
                            "device": be._device})
    assert r3 is False, (
        f"dict surface 无 context 须返回 False，got {r3!r}")
    dev = getattr(be, "_device", None)
    if dev is not None and hasattr(dev, "destroy"):
        dev.destroy()


# ----------------------------------------------------------------------
# 探针 5（环境感知）— 本机 wgpu 探针是否真的跑通（供工作日志可
#            观测）
# ----------------------------------------------------------------------

@_test
def test_probe5_wgpu_device_reports_in_diagnostic():
    """验收可解释——本机 wgpu 设备可用时，``GPUBackend.describe``
    诊断串含 ``wgpu`` 标签（区别于 ``cpu-fallback``）。"""
    _require_wgpu()
    be = _gpu.GPUBackend(device=_gpu.acquire_wgpu_device())
    desc = be.describe()
    assert "device=wgpu" in desc, (
        f"真 wgpu 设备下 describe 须标注 device=wgpu，got {desc!r}")
    assert "cpu-fallback" not in desc
    dev = getattr(be, "_device", None)
    if dev is not None and hasattr(dev, "destroy"):
        dev.destroy()


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    failed = []
    passed = 0
    for name, f in TESTS:
        try:
            f()
            passed += 1
            print(f"  ok  {name}")
        except SkipTest as e:
            print(f"  SKIP {name}: {e}")
        except Exception as e:
            failed.append((name, repr(e)))
            print(f"  FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed, {len(failed)} failed")
    if failed:
        for n, d in failed:
            print(f"  FAIL {n}: {d}")
        sys.exit(1)
