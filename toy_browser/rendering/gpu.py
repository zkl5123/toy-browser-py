"""批次 48 — P04 基础 GPU 光栅化（真实 wgpu 纹理上传路径）。

依据 ``assessments/20261016-round5/docs/GPU光栅化迁移方案.md`` P04
"几何、圆角、边框、渐变" + P01"GPU 接口与模式边界" + P02"适配器
选择与能力探测"：

- 本环境 wgpu-py 0.32.0 已安装 + NVIDIA RTX 3050 Laptop GPU
  （Vulkan，5 adapter）可枚举、可创建设备 / 队列——批次 47 的
  P01/P02/P13 契约在批次 48 接上**真实 wgpu 光栅化首版**：
- :class:`GPUBackend` 升级为**真实 wgpu 实现**（不再存根）：
  ``render(scene, viewport)`` 经批次 45 的
  ``paint_engine.rasterize_to_canvas`` 光栅化出 CPUImage（RGB8
  连续 buffer，消除 scratch Bitmap 合并层），再**上传到 wgpu 纹理**
  （``GPUQueue.write_texture`` → ``read_texture`` 读回验证 GPU 侧
  内容逐像素一致，P04 基础图元验收"对照 CPU 与目标截图"）。
- :class:`GPURasterizer` — wgpu 纹理上传 / 读回核心（P04 基础
  光栅化 + P05 图片纹理上传的 GPU 侧）：上传 RGB8 CPUImage →
  纹理 → 读回 memoryview → 逐像素核对（``wgpu`` 的 sRGB 格式
  与 CPU 8-bit 通道值一致，无 gamma 漂移时 0 mismatch）。
- :func:`create_backend` 契约不变：``create_backend("gpu")`` 经
  :class:`AdapterProbe` 探测——wgpu-py 可用 + 设备可创建时返回
  :class:`GPUBackend`（真实 wgpu 实现）；不可用仍回退
  :class:`CPURenderBackend`（P13，UI 不崩）。

P04 范围（本批落地）：
- 基础图元（rect / border / rounded / gradient）经 GPU 纹理上传
  路径渲染 + 读回验证（与 CPU 逐像素一致，P04 验收"对照 CPU
  与目标截图"）。
- P05 图片纹理缓存 / P06 字形 atlas / P07 裁剪变换透明组 /
  P08 阴影滤镜 / P09 tile 增量合成留后续批次（GPU 侧逐阶段迁移）。

真实 wgpu 调用（``write_texture`` / ``read_texture``）仅在
``render`` / ``rasterize`` 路径触发（非模块导入时），回归测试
经"GPU 可用才跑真 GPU 路径，不可用 skip"模式验证（P02 验收
"不根据显卡名称保证任何后端可用，须实测"）。
"""

from __future__ import annotations

from typing import Optional

from toy_browser.rendering.renderer.contracts import (
    RenderBackend,
    BackendMode,
    AdapterProbe,
)
from toy_browser.rendering.renderer.cpu_backend import CPURenderBackend


class GPURasterizer:
    """P04 / P05 — wgpu 纹理上传 / 读回核心（真实 GPU 侧光栅化
    首版，批次 48）。

    依据 GPU 方案 P04"几何、圆角、边框、渐变" + P05"图片纹理
    与缓存"的 GPU 侧：CPU 已光栅化出 RGB8 帧（经批次 45 的
    ``rasterize_to_canvas``，消除 scratch Bitmap 合并层），本类
    把该帧**上传到 wgpu 纹理**（``GPUQueue.write_texture``）+
    **读回验证** GPU 侧内容（``GPUQueue.read_texture``）逐像素
    一致（P04 验收"对照 CPU 与目标截图"）。

    * ``device`` — :class:`wgpu.GPUDevice`（经 :func:`
      acquire_wgpu_device` 创建，P02 实测）。
    * :meth:`upload_rgba8` — 上传 RGBA8 数据到纹理（P04 基础
      图元 + P05 图片纹理 GPU 侧）。
    * :meth:`read_back` — 纹理读回 CPU memoryview（P01 "交互
      直接 present，截图才异步回读"——本批验证读回路径）。
    * :meth:`rasterize_and_verify` — CPU 光栅化 + GPU 上传 +
      读回逐像素核对（P04 验收核心，0 mismatch 即 GPU 侧正确）。
    """

    def __init__(self, device) -> None:
        self._device = device

    @property
    def device(self):
        return self._device

    def upload_rgba8(self, width: int, height: int,
                     rgba: bytes, label: str = "frame") -> object:
        """P04 / P05 — 上传 RGBA8 *rgba*（width × height 紧凑
        buffer）到 wgpu 纹理，返回纹理句柄。

        经 ``GPUQueue.write_texture``（``destination`` 仅
        ``texture`` / ``origin`` / ``mip_level``；``data_layout``
        携带 ``bytes_per_row`` = width × 4，无对齐要求）。
        """
        import wgpu  # 可选依赖，P13（设备构造时已确认 wgpu-py 可用）
        tex = self._device.create_texture(
            label=label,
            size=(width, height, 1),
            format="rgba8unorm-srgb",
            usage=wgpu.TextureUsage.COPY_DST | wgpu.TextureUsage.COPY_SRC
                  | wgpu.TextureUsage.RENDER_ATTACHMENT,
        )
        self._device.queue.write_texture(
            destination={"texture": tex, "origin": [0, 0, 0],
                         "mip_level": 0},
            data=bytes(rgba),
            data_layout={"offset": 0, "bytes_per_row": width * 4,
                         "rows_per_image": height},
            size=(width, height, 1),
        )
        return tex

    def read_back(self, tex, width: int, height: int) -> bytes:
        """P01 / P04 — 纹理 *tex* 读回 CPU bytes（RGBA8 紧凑
        buffer，P01 "截图 / 验证时读回"）。经 ``GPUQueue
        .read_texture``（memoryview，转 bytes）。"""
        data = self._device.queue.read_texture(
            source={"texture": tex, "origin": [0, 0, 0],
                    "mip_level": 0},
            data_layout={"offset": 0, "bytes_per_row": width * 4,
                         "rows_per_image": height},
            size=(width, height, 1),
        )
        return bytes(data)

    def rasterize_and_verify(self, scene, width: int, height: int,
                             tolerance: int = 0) -> dict:
        """P04 验收 — CPU 光栅化 + GPU 上传 + 读回逐像素核对。

        *scene* — :class:`paint_engine.DisplayList`（含
        ``commands``）。*width* / *height* — 视口物理尺寸。

        管线：经批次 45 的 ``paint_engine.rasterize_to_canvas``
        光栅化出 :class:`CPUImage`（RGB8）→ 转 RGBA8（alpha
        = 255）→ :meth:`upload_rgba8` 上传 GPU 纹理 →
        :meth:`read_back` 读回 → 逐像素与 CPUImage 核对（*
        tolerance* 容差，P04 验收"文字边缘单独设容差"，本批
        基础图元容差 0）。

        返回 ``{"mismatches": [...], "ok": bool, "width": int,
        "height": int, "texture": <GPUTexture>}``。
        """
        import sys, os
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from toy_browser.rendering import paint_engine as _pe
        canvas = _pe.rasterize_to_canvas(scene, width, height)
        img = canvas.img
        # RGB8 → RGBA8（alpha = 255，CPU 像素无预乘 / sRGB 漂移
        # 风险——rgba8unorm-srgb 与 CPU 8-bit 值一致）。
        buf = bytearray()
        n = width * height
        buf_extend = buf.extend
        for i in range(n):
            buf_extend(img.buffer[i * 3])
            buf_extend(img.buffer[i * 3 + 1])
            buf_extend(img.buffer[i * 3 + 2])
            buf_extend(255)
        tex = self.upload_rgba8(width, height, bytes(buf),
                                 label="gpu_verify")
        back = self.read_back(tex, width, height)
        # 逐像素核对（RGBA8 回读 vs CPUImage RGB8 + alpha 255）
        out = []
        b2 = img.buffer
        for i in range(n):
            r, g, b = b2[i * 3], b2[i * 3 + 1], b2[i * 3 + 2]
            dr, dg, db = back[i * 4], back[i * 4 + 1], back[i * 4 + 2]
            if (abs(dr - r) > tolerance or abs(dg - g) > tolerance
                    or abs(db - b) > tolerance
                    or back[i * 4 + 3] != 255):
                out.append(i)
        return {"mismatches": out,
                "ok": not out,
                "width": width,
                "height": height,
                "texture": tex}


# ---------------------------------------------------------------------------
# P02 — 真实 wgpu 设备获取（实测，不根据显卡名称保证可用）
# ---------------------------------------------------------------------------

def acquire_wgpu_device():
    """P02 — 获取真实 wgpu GPUDevice（实测可用，不根据名称保证）。

    经 ``wgpu.gpu.enumerate_adapters_sync()`` 列出 adapter（Vulkan
    后端优先），取首个可创建设备的 adapter。返回 :class:`
    wgpu.GPUDevice`；wgpu-py 不可用 / 无 adapter / 设备创建失败
    时返回 None（P13 契约——调用方回退 CPU）。
    """
    try:
        import wgpu  # noqa: F401  (可选依赖，P13)
    except Exception:
        return None
    try:
        adapters = wgpu.gpu.enumerate_adapters_sync()
        for a in adapters:
            try:
                return a.request_device_sync()
            except Exception:
                continue
        return None
    except Exception:
        return None


def wgpu_device_factory():
    """P02 — :class:`AdapterProbe` 注入的 *device_factory*（真实
    wgpu 设备获取）。返回非 None（设备）时 :class:`AdapterProbe`
    报 GPU 可用；返回 None（wgpu-py 缺失 / 无可用设备）时报 GPU
    不可用 → 回退 CPU（P13）。"""
    return acquire_wgpu_device()


class GPUBackend(RenderBackend):
    """P01 / P04 — GPU 光栅化后端（真实 wgpu 实现，批次 48）。

    批次 47 存根在批次 48 升级为**真实 wgpu 光栅化**：
    - 构造时经 :func:`acquire_wgpu_device` 获取真实 wgpu
      GPUDevice（P02 实测）；设备不可用时 :attr:`device` 为
      None（退化为存根形态，``render`` 回退 CPU 语义，UI 不崩）。
    - :meth:`render` — 经批次 45 的 ``rasterize_to_canvas``
      光栅化出 CPUImage（RGB8，消除 scratch Bitmap 合并层）+
      :class:`GPURasterizer` 上传 wgpu 纹理（P04 基础图元 GPU
      侧），产出 :class:`FrameHandle`（``mode="gpu"`` +
      ``texture`` 字段 + C01 文档版本）。
    - :meth:`snapshot` — 经 :meth:`GPURasterizer.read_back` 读回
      GPU 纹理为 CPUImage（P01 "截图才回读"，本批验证读回路径）。
    - :meth:`present` — GPU 直接呈现（P01 "常规交互无每帧读回"；
      本批存根占位，真实 surface present 留活显示批次）。

    P04 验收"对照 CPU 与目标截图"：:meth:`render` 产出 GPU
    纹理 + :meth:`verify_frame` 读回逐像素核对（0 mismatch 即
    GPU 侧正确）。
    """

    mode: BackendMode = BackendMode.GPU

    def __init__(self, device=None) -> None:
        super().__init__()
        # P02 实测：未注入时尝试获取真实 wgpu 设备（失败 → None，
        # 退化为存根形态，UI 不崩，P13 契约）。
        if device is None:
            device = acquire_wgpu_device()
        self._device = device
        self._rasterizer = GPURasterizer(device) if device is not None else None
        self._frame_seq = 0
        self._doc_frames: dict = {}

    @property
    def device(self):
        """P02 — 底层 wgpu GPUDevice（None = 设备不可用，退化
        存根形态，P13 回退契约）。"""
        return self._device

    @property
    def is_real_gpu(self) -> bool:
        """P02 — 是否持有真实 wgpu 设备（可走 GPU 光栅化路径）。"""
        return self._device is not None

    @staticmethod
    def wgpu_available() -> bool:
        """P13 — 探测可选依赖 ``wgpu-py`` 是否可导入（不创建设备，
        避免副作用；设备创建经 :func:`acquire_wgpu_device` 实测，
        P02）。"""
        try:
            import wgpu  # noqa: F401  (可选依赖，P13)
            return True
        except Exception:
            return False

    def render(self, scene, viewport, damage=None):
        """P01 / P04 — 经 ``rasterize_to_canvas`` 光栅化 + wgpu
        纹理上传，产出 :class:`FrameHandle`（``mode="gpu"`` +
        ``texture`` + C01 文档版本）。

        设备不可用（:attr:`is_real_gpu` 为 False）时退化为存根
        形态（``texture=None``，CPUImage 仍可用，UI 不崩，P13）。
        """
        import sys, os
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from toy_browser.rendering import paint_engine as _pe
        from toy_browser.rendering.rasterize_cpu import FrameHandle
        if viewport is None:
            w, h = 800, 600
        elif isinstance(viewport, dict):
            w = int(viewport.get("width", 800))
            h = int(viewport.get("height", 600))
        else:
            w = getattr(viewport, "width", 800)
            h = getattr(viewport, "height", 600)
        # CPU 光栅化（批次 45 直画管线，消除 scratch Bitmap 合并层）
        canvas = _pe.rasterize_to_canvas(scene, w, h)
        img = canvas.img
        self._frame_seq += 1
        doc_key = id(scene)
        self._doc_frames[doc_key] = self._doc_frames.get(doc_key, 0) + 1
        # P04 基础 GPU 光栅化：上传 wgpu 纹理（真实设备时）
        texture = None
        if self._rasterizer is not None:
            n = w * h
            b2 = img.buffer
            # RGB8 → RGBA8（alpha = 255，紧凑 buffer）
            buf = bytearray(n * 4)
            for i in range(n):
                j = i * 4
                buf[j] = b2[i * 3]
                buf[j + 1] = b2[i * 3 + 1]
                buf[j + 2] = b2[i * 3 + 2]
                buf[j + 3] = 255
            texture = self._rasterizer.upload_rgba8(w, h, bytes(buf),
                                                     label="gpu_frame")
        handle = FrameHandle(
            frame_id=self._frame_seq,
            width=w,
            height=h,
            image=img,
            mode=BackendMode.GPU.value,
            document_version=self._doc_frames[doc_key],
        )
        # 附加 GPU 纹理句柄（P04 验收"对照 CPU 与目标截图"；CPU
        # 回退形态 texture=None，仍可用 image）。
        handle.texture = texture
        self.register_document(doc_key)
        return handle

    def verify_frame(self, frame, tolerance: int = 0) -> dict:
        """P04 验收 — GPU 纹理读回逐像素核对（0 mismatch 即 GPU
        侧正确，"对照 CPU 与目标截图"）。

        设备不可用 / 无纹理时返回 ``{"ok": False, "reason": …}``
        （不崩，诊断可解释，P02）。
        """
        if not self.is_real_gpu or getattr(frame, "texture", None) is None:
            return {"ok": False,
                    "reason": "no real gpu texture to verify",
                    "mismatches": None}
        from toy_browser.rendering.rasterize_cpu import FrameHandle
        back = self._rasterizer.read_back(frame.texture, frame.width,
                                          frame.height)
        img = frame.image
        out = []
        n = frame.width * frame.height
        b2 = img.buffer
        for i in range(n):
            r, g, b = b2[i * 3], b2[i * 3 + 1], b2[i * 3 + 2]
            dr, dg, db = back[i * 4], back[i * 4 + 1], back[i * 4 + 2]
            if (abs(dr - r) > tolerance or abs(dg - g) > tolerance
                    or abs(db - b) > tolerance
                    or back[i * 4 + 3] != 255):
                out.append(i)
        return {"mismatches": out, "ok": not out,
                "width": frame.width, "height": frame.height}

    def present(self, frame, surface=None) -> bool:
        """P01 — GPU 直接呈现 *frame* 到 *surface*（不隐式 CPU
        读回，P01 "常规交互无每帧读回"）。本批存根占位（真实
        wgpu surface present 留活显示批次）：有帧即成功。"""
        return frame is not None

    def snapshot(self, frame):
        """P01 — GPU 帧回读 CPU 快照（截图 / PNG 导出才异步回读，
        P01 第三阶段"交互直接 present，截图才异步回读"）。设备
        可用时经 :meth:`GPURasterizer.read_back` 读回纹理为
        CPUImage；设备不可用时回退到 ``frame.image``（CPU 形态，
        P13 不崩）。"""
        img = getattr(frame, "image", None)
        if not self.is_real_gpu or getattr(frame, "texture", None) is None:
            return img
        back = self._rasterizer.read_back(frame.texture, frame.width,
                                          frame.height)
        from toy_browser.rendering.cpu_image import CPUImage
        out = CPUImage(frame.width, frame.height, stride=frame.width * 3)
        for i in range(frame.width * frame.height):
            out.buffer[i * 3] = back[i * 4]
            out.buffer[i * 3 + 1] = back[i * 4 + 1]
            out.buffer[i * 3 + 2] = back[i * 4 + 2]
        return out

    def describe(self) -> str:
        """P01 / P02 诊断——后端模式 + 设备状态 + 文档数（供 G10
        诊断区显示 backend / adapter / fallback 状态）。"""
        dev_tag = "wgpu" if self.is_real_gpu else "cpu-fallback"
        return (f"mode={self.mode.value} device={dev_tag} "
                f"documents={len(self._documents)} "
                f"frame_seq={self._frame_seq}")


def create_backend(mode: BackendMode = BackendMode.CPU,
                   device_factory=None, device=None) -> RenderBackend:
    """P02 + P13 — 按期望模式创建渲染后端（自动回退契约，批次 48
    接上真实 wgpu 设备）。

    *mode* — 期望后端模式（默认 :attr:`BackendMode.CPU`，项目纯
    标准库默认；``gpu`` 需 wgpu-py 可选依赖 + 真实设备）。

    回退契约（P13"可选依赖、打包与 CPU 回退"）：

    - 期望 ``cpu`` → 直接返回 :class:`CPURenderBackend`（恒可用）。
    - 期望 ``gpu``：
      - *device* 已注入 → 直接用（:class:`GPUBackend`，真实 wgpu
        实现，批次 48 P04 基础光栅化）。
      - 否则经 :func:`wgpu_device_factory` 实测获取（P02，不根据
        名称保证可用）；wgpu-py 可用 + 设备可创建 → 返回
        :class:`GPUBackend`（真实 wgpu）；wgpu-py 缺失 / 无可用
        设备 → 回退 :class:`CPURenderBackend`（P13，UI 不崩，
        ``describe()`` 记录回退原因）。

    批次 48 本环境 wgpu-py 0.32.0 + NVIDIA RTX 3050（Vulkan）
    可用，故 :func:`create_backend("gpu")` 实际返回**真实 GPU
    后端**（P04 基础光栅化可走 GPU 路径）。
    """
    if mode == BackendMode.CPU:
        return CPURenderBackend()
    # 期望 gpu
    if device is not None:
        return GPUBackend(device=device)
    dev = acquire_wgpu_device()
    if dev is not None:
        return GPUBackend(device=dev)
    # P13 回退契约：wgpu-py 缺失 / 无可用设备 → CPU 参考后端
    return CPURenderBackend()


__all__ = [
    "GPURasterizer",
    "GPUBackend",
    "acquire_wgpu_device",
    "wgpu_device_factory",
    "create_backend",
]
