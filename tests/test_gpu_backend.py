"""批次 47 — P01–P13 GPU 光栅化迁移（第一阶段：统一渲染契约 + P01/P02
纯逻辑核心）回归。

被测：
- ``toy_browser/rendering/renderer/contracts.py``（RenderBackend 契约
  + BackendMode + AdapterProbe + Capability，P01/P02）
- ``toy_browser/rendering/renderer/cpu_backend.py``（CPURenderBackend，
  经批次 45 的 rasterize_to_canvas 直画，P01 接口的 CPU 参考实现）
- ``toy_browser/rendering/gpu.py``（GPUBackend 存根 + create_backend
  工厂，P13 可选依赖 / CPU 回退契约）

设计：纯逻辑契约（不引入 wgpu-py 依赖、不触碰真实 GPU，活 GPU 验证
留后续批次 + 可选依赖批次）。验证"RenderBackend 接口形态 + 模式
边界（cpu / gpu 显式可探测）+ 适配器探测（无 device_factory 时 GPU
不可用 → 回退 CPU，P13 契约 UI 不崩）+ CPU 参考后端逐像素权威（经
批次 45 直画管线）+ create_backend 自动回退（期望 gpu 但 wgpu-py
不可用 → 返回 CPURenderBackend）"。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering.renderer import (
    RenderBackend, BackendMode, AdapterProbe, Capability, CPURenderBackend,
)
from toy_browser.rendering.gpu import GPUBackend, create_backend
from toy_browser.rendering.rasterize_cpu import FrameHandle
from toy_browser.rendering.paint import Color
from toy_browser.rendering.paint_engine import DisplayList, DisplayCommand

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _mixed_dl():
    """混合命令 DisplayList（rect + border + rounded + gradient + text +
    shadow），与批次 45 直画管线对照。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(kind="rect", x=10, y=10,
                                      w=50, h=40, color=Color(200, 30, 30)))
    dl.commands.append(DisplayCommand(kind="border", x=70, y=10,
                                      w=30, h=30, color=Color(0, 0, 200)))
    dl.commands.append(DisplayCommand(kind="rounded", x=120, y=10,
                                      w=40, h=40, radius=8,
                                      color=Color(0, 180, 90)))
    dl.commands.append(DisplayCommand(kind="text", x=12, y=14,
                                      w=40, h=12, text="Hi",
                                      font_px=10, color=Color(0, 0, 0)))
    return dl


# ---------------------------------------------------------------------------
# P01 — 模式边界 + 契约形态
# ---------------------------------------------------------------------------

@_reg
def test_backend_mode_enum_values():
    """P01 — BackendMode 显式 cpu / gpu 两种模式（边界可探测）。"""
    assert BackendMode.CPU.value == "cpu"
    assert BackendMode.GPU.value == "gpu"


@_reg
def test_render_backend_base_contract():
    """P01 — RenderBackend 基类契约形态：render 抽象（NotImplemented），
    present / snapshot / release_document 提供 CPU 参考语义。"""
    b = RenderBackend()
    # render 未实现 → 抛 NotImplementedError（子类覆写）
    try:
        b.render(None, None)
        raise AssertionError("基类 render 应抛 NotImplementedError")
    except NotImplementedError:
        pass
    # present / snapshot 默认 CPU 语义（帧即 CPU buffer）
    fake = FrameHandle(1, 10, 10)
    assert b.present(fake) is True
    assert b.snapshot(fake) is fake.image


# ---------------------------------------------------------------------------
# P02 — 适配器选择与能力探测（无真实设备也能验证回退路径）
# ---------------------------------------------------------------------------

@_reg
def test_adapter_probe_no_device_factory_gpu_off():
    """P02 + P13 — 无 device_factory（wgpu-py 可选依赖缺失）时：GPU
    候选不可用（reason 记录"no device_factory"），CPU 候选恒可用。"""
    probe = AdapterProbe()
    caps = probe.probe()
    gpu = [c for c in caps if c.mode == BackendMode.GPU][0]
    cpu = [c for c in caps if c.mode == BackendMode.CPU][0]
    assert gpu.available is False
    assert "no device_factory" in gpu.reason or "wgpu" in gpu.reason
    assert cpu.available is True
    assert cpu.adapter == "cpu"


@_reg
def test_adapter_probe_select_falls_back_to_cpu():
    """P02 + P13 — select(GPU) 在 GPU 不可用时自动回退 CPU（UI 不崩，
    reason 记录回退原因供诊断面板解释）。"""
    probe = AdapterProbe()
    cap = probe.select(preferred=BackendMode.GPU)
    assert cap.mode == BackendMode.CPU
    assert cap.available is True
    assert "gpu unavailable" in cap.reason or "fell back" in cap.reason


@_reg
def test_adapter_probe_select_cpu_direct():
    """P02 — select(CPU) 直接返回 CPU 候选（恒可用）。"""
    probe = AdapterProbe()
    cap = probe.select(preferred=BackendMode.CPU)
    assert cap.mode == BackendMode.CPU
    assert cap.available is True


@_reg
def test_adapter_probe_fake_device_factory_gpu_ok():
    """P02 — 注入 fake device_factory（返回非 None）→ GPU 候选可用
    （不根据显卡名称，实测 device，P02 验收）。"""
    class _FakeDev:
        pass
    probe = AdapterProbe(device_factory=lambda: _FakeDev())
    caps = probe.probe()
    gpu = [c for c in caps if c.mode == BackendMode.GPU][0]
    assert gpu.available is True
    cap = probe.select(preferred=BackendMode.GPU)
    assert cap.mode == BackendMode.GPU


@_reg
def test_adapter_probe_describe_diagnostic():
    """P02 验收 — describe 诊断串含 adapter / fallback 状态（供 G10
    诊断区 / 状态栏显示 backend 状态）。"""
    probe = AdapterProbe()
    s = probe.describe()
    assert "gpu=off" in s
    assert "cpu=ok" in s


# ---------------------------------------------------------------------------
# P01 — CPU 参考后端（经批次 45 直画管线，逐像素权威基准）
# ---------------------------------------------------------------------------

@_reg
def test_cpu_backend_render_frame_handle():
    """P01 — CPURenderBackend.render 经 rasterize_to_canvas 直画出
    FrameHandle（mode=cpu + CPUImage，不逐像素读回）。"""
    b = CPURenderBackend()
    dl = _mixed_dl()
    h = b.render(dl, {"width": 80, "height": 60})
    assert isinstance(h, FrameHandle)
    assert h.is_cpu
    assert h.image is not None
    assert h.image.width == 80 and h.image.height == 60
    assert h.frame_id >= 1


@_reg
def test_cpu_backend_frame_matches_rasterize():
    """P01 验收 — CPU 参考后端逐像素权威：FrameHandle.image 与既有
    paint_engine.rasterize（Bitmap）逐像素一致（经 cpu_matches_bitmap
    核对，回归无漂移）。"""
    from toy_browser.rendering import paint_engine as _pe
    from toy_browser.rendering.rasterize_cpu import cpu_matches_bitmap
    b = CPURenderBackend()
    dl = _mixed_dl()
    h = b.render(dl, {"width": 80, "height": 60})
    bmp = _pe.rasterize(dl, 80, 60)
    mismatches = cpu_matches_bitmap(h.image, bmp)
    assert mismatches == [], f"CPU 后端与权威 rasterize 漂移: {len(mismatches)}"


@_reg
def test_cpu_backend_document_lifecycle():
    """P01 — register_document / release_document 资源归属（C01 文档
    版本门控 + G09 step 4 关标签不泄漏）。"""
    b = CPURenderBackend()
    dl = _mixed_dl()
    b.render(dl, {"width": 40, "height": 30})
    assert b.release_document(id(dl)) is True
    assert b.release_document(id(dl)) is False  # 已释放 → False


# ---------------------------------------------------------------------------
# P13 — 可选依赖、打包与 CPU 回退（create_backend 工厂）
# ---------------------------------------------------------------------------

@_reg
def test_create_backend_default_cpu():
    """P13 — create_backend() 默认 CPU 模式 → CPURenderBackend（纯
    标准库默认，项目约束）。"""
    b = create_backend()
    assert isinstance(b, CPURenderBackend)
    assert b.mode == BackendMode.CPU


@_reg
def test_create_backend_gpu_respects_environment():
    """P02 + P13 — create_backend(GPU) 按**实测设备可用性**决定形态
    （批次 48 接上真实 wgpu 设备后，本契约变为环境感知）：wgpu 设备
    可用 → 返回真实 GPUBackend（P04 基础光栅化）；wgpu-py 缺失 /
    无可用设备 → 回退 CPURenderBackend（UI 不崩，诊断可解释）。"""
    from toy_browser.rendering.gpu import acquire_wgpu_device
    b = create_backend(BackendMode.GPU)
    has_dev = acquire_wgpu_device() is not None
    if has_dev:
        # 本环境 wgpu + NVIDIA 可用 → 真实 GPU 后端（批次 48 P04）
        assert isinstance(b, GPUBackend)
        assert b.is_real_gpu
        h = b.render(_mixed_dl(), {"width": 40, "height": 20})
        assert h.texture is not None  # P04 GPU 纹理已上传
    else:
        # 无 wgpu 设备 → P13 CPU 回退契约
        assert isinstance(b, CPURenderBackend)
        assert b.mode == BackendMode.CPU
        h = b.render(_mixed_dl(), {"width": 40, "height": 20})
        assert h.is_cpu


@_reg
def test_gpu_backend_wgpu_probe():
    """P13 — GPUBackend.wgpu_available() 探测可选依赖（不创建设备，
    避免副作用；批次 48 起 wgpu-py 可安装，探测为 bool 不崩）。"""
    assert isinstance(GPUBackend.wgpu_available(), bool)


@_reg
def test_gpu_backend_present_contract():
    """P01/P03 — GPUBackend 的 present / snapshot 契约形态（P03 将
    present 从存根占位升级为**分层呈现契约**：无帧 → False；CPU
    回退 → True；真实设备但无 surface → "no-surface"；注入合法
    surface → 真实 wgpu submit，False/True 反映成败）。"""
    b = GPUBackend()
    assert b.mode == BackendMode.GPU
    fake = FrameHandle(1, 10, 10, mode="gpu")
    fake.texture = None  # P03 — GPU 形态帧由 render() 注入纹理句柄
    # P03 — 无帧可呈现 → False（契约最底层，环境无关）。
    assert b.present(None) is False
    # P03 — CPU 回退形态（P13）：无 wgpu 设备时帧在 frame.image，
    # GUI 直接消费，等价"呈现成功"（True）。
    if not b.is_real_gpu:
        assert b.present(fake) is True
    # P03 — 真实设备但无 surface：不抛错，返回可区降标记，让调用方
    # 显式注入 surface 后重试（P03 与 P13 分层验收）。
    else:
        assert b.present(fake) == "no-surface"
    # P03 — 注入 fake surface（非真实 GPUCanvasContext）：分层契约。
    # - 真实 wgpu 设备形态：_present_to_surface 捕获 surface 形态不匹配 /
    #   configure 失败，按 P13 不崩契约返回 False（真实 surface present
    #   验收需 GUI 接线批次）。
    # - CPU 回退形态（P13 无 wgpu）：present 在 is_real_gpu 检查处直接
    #   返回 True，不会进入 _present_to_surface；因此即使传 object()
    #   也按 CPU 参考语义"呈现成功"。
    if b.is_real_gpu:
        assert b.present(fake, surface=object()) is False
    else:
        assert b.present(fake, surface=object()) is True
    snap = b.snapshot(fake)
    assert snap is fake.image or type(snap).__name__ == "CPUImage"


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
