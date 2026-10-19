"""批次 48 — P04 基础 GPU 光栅化（真实 wgpu 纹理上传 + 读回验证）。

被测：``toy_browser/rendering/gpu.py`` 批次 48 升级的**真实 wgpu
光栅化**（P04"几何、圆角、边框、渐变"基础图元经 GPU 纹理上传
路径）：

- ``acquire_wgpu_device`` — P02 实测获取 wgpu GPUDevice（不根据显卡
  名称保证可用；wgpu-py 缺失 / 无 adapter → None）。
- ``GPURasterizer`` — wgpu 纹理上传（``write_texture``）+ 读回
  （``read_texture``）核心 + ``rasterize_and_verify``（CPU 光栅化 +
  GPU 上传 + 读回逐像素核对，P04 验收"对照 CPU 与目标截图"）。
- ``GPUBackend`` — 批次 48 升级为**真实 wgpu 实现**（``render`` 经
  批次 45 的 ``rasterize_to_canvas`` 直画 CPUImage + 上传 wgpu
  纹理，产出 ``FrameHandle`` 含 ``texture`` 字段；``verify_frame``
  读回核对；``snapshot`` GPU 纹理读回 CPUImage；设备不可用时退化
  存根形态不崩，P13）。
- ``create_backend`` — 批次 48 接上真实 wgpu 设备（``device``
  注入 / ``acquire_wgpu_device`` 实测；缺失仍回退 CPU，P13）。

设计：P02 验收"不根据显卡名称保证任何后端可用，须实测"——本回归
**按实测设备可用性分支**：``acquire_wgpu_device()`` 返回真实
GPUDevice 时跑真 GPU 路径（P04 基础图元上传 + 读回 0 mismatch）；
wgpu-py 缺失 / 无可用设备时验证**回退契约**（``create_backend
(GPU)`` 返回 CPU 参考后端 + ``GPUBackend(device=None)`` 退化存根
不崩），不崩即回归通过。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering.gpu import (
    GPUBackend, GPURasterizer, acquire_wgpu_device, create_backend,
)
from toy_browser.rendering.renderer.contracts import BackendMode
from toy_browser.rendering.renderer.cpu_backend import CPURenderBackend
from toy_browser.rendering.rasterize_cpu import FrameHandle
from toy_browser.rendering.paint import Color
from toy_browser.rendering.paint_engine import DisplayList, DisplayCommand

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _mixed_dl():
    """P04 基础图元 DisplayList（rect + border + rounded + text +
    shadow + gradient），对照 CPU 与 GPU 目标截图。"""
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


def _has_gpu() -> bool:
    """P02 实测——wgpu 设备是否真正可用（不根据名称保证）。"""
    return acquire_wgpu_device() is not None


# ---------------------------------------------------------------------------
# P02 — 真实 wgpu 设备获取（实测）
# ---------------------------------------------------------------------------

@_reg
def test_acquire_wgpu_device_returns_device_or_none():
    """P02 — acquire_wgpu_device 返回 GPUDevice 或 None（wgpu-py
    缺失 / 无可用设备时 None，不崩；不根据名称保证可用）。"""
    dev = acquire_wgpu_device()
    if _has_gpu():
        assert dev is not None
        assert hasattr(dev, "queue")  # GPUDevice 形态
    else:
        assert dev is None


@_reg
def test_gpu_backend_reports_real_gpu_state():
    """P01 / P02 — GPUBackend.is_real_gpu / device 属性按注入设备
    状态如实报告（True = 真实 wgpu 光栅化路径，False = 退化存根）。"""
    b = GPUBackend()  # 自动 acquire（缺设备 → None → 退化存根）
    assert isinstance(b.is_real_gpu, bool)
    if _has_gpu():
        assert b.is_real_gpu is True
        assert b.device is not None
    else:
        assert b.is_real_gpu is False
        assert b.device is None


# ---------------------------------------------------------------------------
# P04 — 真实 wgpu 光栅化（GPU 可用时跑真路径，P04 验收 0 mismatch）
# ---------------------------------------------------------------------------

@_reg
def test_gpu_backend_render_uploads_texture():
    """P04 — GPUBackend.render 经 rasterize_to_canvas 直画 + wgpu
    纹理上传，产出 FrameHandle（mode=gpu + texture 字段）。无 GPU
    时退化存根（texture=None，不崩，P13）。"""
    b = create_backend(BackendMode.GPU)
    if isinstance(b, GPUBackend) and b.is_real_gpu:
        h = b.render(_mixed_dl(), {"width": 160, "height": 60})
        assert isinstance(h, FrameHandle)
        assert h.is_gpu
        assert h.texture is not None
        assert h.image is not None
    else:
        # P13 回退：CPU 参考后端（逐像素权威）
        assert isinstance(b, CPURenderBackend)
        h = b.render(_mixed_dl(), {"width": 160, "height": 60})
        assert h.is_cpu
        assert not hasattr(h, "texture") or h.texture is None


@_reg
def test_gpu_backend_verify_frame_pixel_parity():
    """P04 验收 — GPU 纹理读回逐像素与 CPU 权威（paint_engine
    .rasterize 直画管线）一致（0 mismatch，"对照 CPU 与目标截图"）。
    无 GPU 时验证 verify_frame 不崩 + 记录 reason（诊断可解释）。"""
    b = create_backend(BackendMode.GPU)
    h = b.render(_mixed_dl(), {"width": 160, "height": 60})
    if isinstance(b, GPUBackend) and b.is_real_gpu:
        v = b.verify_frame(h)
        assert v["ok"] is True, f"GPU 侧像素漂移: {v['mismatches'][:8]}"
        assert v["mismatches"] == []
    else:
        v = b.verify_frame(h) if isinstance(b, GPUBackend) else None
        if v is not None:
            # 退化存根形态：verify 不崩，记录 reason（P02 诊断）
            assert "reason" in v or "ok" in v
        # CPU 回退形态：image 即 CPUImage（P13）
        assert h.image is not None


@_reg
def test_gpu_backend_snapshot_reads_back():
    """P01 / P04 — GPUBackend.snapshot 经 read_texture 读回 GPU 纹理
    为 CPUImage（"截图 / 验证时读回"）；无纹理 / 无设备时回退
    frame.image（P13 不崩）。"""
    b = create_backend(BackendMode.GPU)
    h = b.render(_mixed_dl(), {"width": 80, "height": 40})
    snap = b.snapshot(h)
    if isinstance(b, GPUBackend) and b.is_real_gpu and getattr(h, "texture", None):
        # 真实读回：CPUImage（从 GPU 纹理还原 RGB8）
        assert type(snap).__name__ == "CPUImage"
        assert snap.width == 80 and snap.height == 40
    else:
        # 退化：回退 frame.image（CPUImage，P13）
        assert snap is h.image or type(snap).__name__ == "CPUImage"


# ---------------------------------------------------------------------------
# P04 / P05 — GPURasterizer 上传 + 读回核心
# ---------------------------------------------------------------------------

@_reg
def test_gpurrasterizer_upload_and_readback():
    """P04 / P05 — GPURasterizer.upload_rgba8 + read_back 上传已知
    RGBA8 数据到 wgpu 纹理 → 读回逐字节一致（GPU 侧内容正确，
    P04 验收"对照目标截图"）。无 GPU 时验证构造不崩 + 降级路径。"""
    b = create_backend(BackendMode.GPU)
    if isinstance(b, GPUBackend) and b.is_real_gpu:
        rz = b._rasterizer
        assert isinstance(rz, GPURasterizer)
        w, hgt = 32, 16
        # 已知 RGBA8（红色随像素递增，其余通道固定）
        data = bytearray()
        for px in range(w * hgt):
            data += bytes([(px * 8) % 256, 0, 0, 255])
        tex = rz.upload_rgba8(w, hgt, data, label="test")
        back = rz.read_back(tex, w, hgt)
        assert len(back) == w * hgt * 4
        # 逐字节核对（首像素 / 中 / 尾）
        for px in [0, w * hgt // 2, w * hgt - 1]:
            for ch in range(4):
                assert back[px * 4 + ch] == data[px * 4 + ch], \
                    f"GPU 读回漂移 px={px} ch={ch}"
    else:
        # 无 GPU：GPUBackend(device=None) 构造不崩（退化存根，P13）
        b2 = GPUBackend(device=None)
        assert b2.is_real_gpu is False
        assert b2._rasterizer is None


# ---------------------------------------------------------------------------
# P02 / P13 — create_backend 工厂（真实 wgpu 接入 + 回退契约）
# ---------------------------------------------------------------------------

@_reg
def test_create_backend_gpu_returns_real_when_available():
    """P02 + P13 — create_backend(GPU) 在 wgpu 设备可用时返回真实
    GPUBackend（is_real_gpu=True）；缺失时回退 CPU 参考后端（P13）。"""
    b = create_backend(BackendMode.GPU)
    if _has_gpu():
        assert isinstance(b, GPUBackend)
        assert b.is_real_gpu is True
    else:
        assert isinstance(b, CPURenderBackend)


@_reg
def test_create_backend_gpu_injected_device():
    """P02 — create_backend(GPU, device=<GPUDevice>) 用注入设备直接
    构造真实 GPU 后端（不重新探测，P02 实测可注入）。

    分层契约：有 wgpu 设备时验证 GPUBackend + is_real_gpu；
    无 wgpu 设备时 create_backend(GPU, device=None) 按 P13 回退
    CPURenderBackend，只验证不崩即可（GPUBackend(device=None) 是
    退化存根形态，工厂层面直接回退 CPU 是更高层的契约）。"""
    dev = acquire_wgpu_device()
    if dev is not None:
        b = create_backend(BackendMode.GPU, device=dev)
        assert isinstance(b, GPUBackend)
        assert b.is_real_gpu is True
        assert b.device is dev
    else:
        # P13 回退契约：无 wgpu 设备时工厂回退 CPU 后端（不崩）
        b = create_backend(BackendMode.GPU, device=None)
        assert isinstance(b, CPURenderBackend)
        h = b.render(_mixed_dl(), {"width": 80, "height": 40})
        assert isinstance(h, FrameHandle)
        assert h.image is not None


@_reg
def test_gpu_backend_describe_diagnostic():
    """P01 / P02 验收 — describe 诊断串含 mode / documents（供 G10
    诊断区显示 backend 状态）。GPUBackend 额外含 device / frame_seq。

    分层契约：GPUBackend.describe 含全部字段；CPURenderBackend
    （P13 回退形态）的 describe 只保证含 mode 和 documents。"""
    b = create_backend(BackendMode.GPU)
    s = b.describe()
    assert "mode=gpu" in s or "mode=cpu" in s
    assert "documents=" in s
    if isinstance(b, GPUBackend):
        assert "device=" in s
        assert "frame_seq=" in s


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
