"""R7-A02 第 4 点 — 连续帧 buffer 布局契约 + 父进程可见面校验（纯逻辑）。

审计 A02 第 4 点：帧用连续 buffer 或受控共享内存，**不**序列化整棵
Python 对象图；父进程不接触 worker 内部 Host。

本模块只落**决策层**（布局怎么算、payload 允不允许、字段是否有限），
不 import 运行时（subprocess / threading / multiprocessing / socket），
也不读时钟。真实跨进程共享内存（mmap / posix 共享段）与"worker 把
像素写进共享段、父进程读句柄"的执行路径留活显示 / GPU 批次，本模块
不冒充"已经跨进程共享了"。

契约（测试锁定）：
  * 像素 buffer 用**固定几何 + 字节布局**描述（format / 尺寸 /
    bytes_per_pixel / total_bytes / stride），对齐 :class:`CPUImage`
    的 ``bytearray`` 连续存储；
  * 父进程**只**接触 worker 传回的**有限摘要**（白名单键）+ buffer
    句柄引用（id / size / format / 几何），**不**接触 Host / DOM /
    任意 Python 对象图——``FramePayloadContract.validate`` 超限即拒；
  * 所有判定无副作用、无 I/O。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# 像素格式 → bytes_per_pixel（与 CPUImage / GPU 上传路径同口径：
# rgb8 每像素 3 字节、rgba8 每像素 4 字节）。
_FORMAT_BPP: dict = {"rgb8": 3, "rgba8": 4}

# 父进程可见面允许的 payload 键（有限摘要 + buffer 句柄引用）。
# 这些键的取值本身也受限（小标量 / 有限字符串 / 句柄 dict），见
# :class:`FramePayloadContract`。
_WHITELIST_KEYS = frozenset({
    "ok", "cancelled", "kind", "url", "frame_id", "doc_id",
    "navigation_id", "error_category", "buffer",
})

# buffer 句柄 dict 内允许的键（只描述"这块像素在哪 / 多大 / 什么
# 格式"，不携带像素数据本身——像素留在 worker / 共享段，父进程读
# 句柄不读像素）。``shared`` 是跨进程共享段名（frame_shared 命名
# 段；父进程据此定位像素，见 R7-A02 第 4 点活显示批次）。
_HANDLE_KEYS = frozenset({"id", "size", "format", "width", "height",
                          "shared"})

# 有限字符串上限（审计第 5 点"日志只记录有限摘要"同口径，防 worker
# 把大文本 / 对象 repr 塞进 payload）。
_MAX_STR_LEN = 128
# buffer 句柄里 size（字节）上限（防无限大 buffer 句柄描述；真实
# 像素数据不走这里）。
_MAX_HANDLE_SIZE = 64 * 1024 * 1024


@dataclass(frozen=True)
class FrameBufferSpec:
    """R7-A02 第 4 点 — 一帧像素的**连续 buffer 布局**（纯描述）。

    对齐 :class:`CPUImage`：``bytearray`` 连续存储，行宽 ``width ×
    bytes_per_pixel``，stride 默认 ``width × bytes_per_pixel``（无行
    padding；调用方需要 padding 时显式传 stride，``layout_ok`` 会校验
    一致性）。

    * ``format`` — ``"rgb8"`` / ``"rgba8"``（决定 bytes_per_pixel）。
    * ``width`` / ``height`` — 逻辑视口尺寸（像素）。
    * ``bytes_per_pixel`` — 缺省按 format 推导（rgb8=3、rgba8=4）。
    * ``stride`` — 行字节数；缺省 ``width × bytes_per_pixel``。

    :meth:`total_bytes` 给 buffer 总字节数（父进程据此申请 / 校验共享
    段大小，不逐像素复制）；:meth:`layout_ok` 校验几何 / stride 自洽。
    """
    format: str
    width: int
    height: int
    bytes_per_pixel: Optional[int] = None
    stride: Optional[int] = None

    def __post_init__(self) -> None:
        if self.format not in _FORMAT_BPP:
            raise ValueError(
                f"unknown pixel format {self.format!r} "
                f"(supported: {sorted(_FORMAT_BPP)})")
        if self.width < 0 or self.height < 0:
            raise ValueError("frame dimensions must be >= 0")

    @property
    def bpp(self) -> int:
        """每像素字节数（显式给定则用给定值，否则按 format 推导）。"""
        if self.bytes_per_pixel is not None:
            return self.bytes_per_pixel
        return _FORMAT_BPP[self.format]

    @property
    def row_bytes(self) -> int:
        """每行字节数（默认无 padding）。"""
        if self.stride is not None:
            return self.stride
        return self.width * self.bpp

    def total_bytes(self) -> int:
        """buffer 总字节数 = stride × height（父进程申请共享段用）。"""
        return self.row_bytes * self.height

    def layout_ok(self) -> bool:
        """几何 / stride 自洽判定（纯逻辑，无 I/O）。

        合法条件：
          * stride 未显式给定，或 stride >= width × bpp（行字节数
            不小于"紧凑"宽度，否则越界 / 重叠）；
          * width / height 非负（构造时已挡）；
          * bpp 与 format 一致（显式覆盖时至少 >= 1）。
        """
        if self.bpp < 1:
            return False
        if self.width < 0 or self.height < 0:
            return False
        if self.stride is not None:
            return self.stride >= self.width * _FORMAT_BPP[self.format]
        return True

    def describe(self) -> str:
        return (f"FrameBufferSpec(format={self.format} "
                f"{self.width}x{self.height} bpp={self.bpp} "
                f"stride={self.row_bytes} bytes={self.total_bytes()})")


@dataclass
class FramePayloadContract:
    """R7-A02 第 4 点 — 父进程可见面校验（worker 传回 payload 的有限性）。

    worker → UI 主线程的帧 payload 只允许：
      * **有限摘要**（白名单键，取值受限：bool / int / <=128 字符
        str）；
      * **buffer 句柄引用**（``buffer`` 键 → 只含
        ``{_HANDLE_KEYS}`` 的 dict，描述"像素在哪 / 多大 / 什么
        格式"，**不**携带像素数据本身）。

    超出白名单、或值不受限（大字符串 / 嵌套对象图 / 列表 / 任意
    可调用 / Host / DOM）的 payload 一律**拒收**——父进程绝不接触
    worker 内部对象图（审计 A02 第 4 点"不序列化整棵 Python 对象
    图；父进程不接触 worker 内部 Host"）。

    :meth:`validate` 返回 ``(ok, reasons)``：``ok`` 为 True 时
    ``reasons`` 为空；为 False 时 ``reasons`` 列出**全部**违规（不
    只报第一个），便于诊断 / 测试锁定。
    """
    #: 允许出现在 payload 顶层的键（不可变，调用方不得扩展）。
    allowed_keys: frozenset = _WHITELIST_KEYS
    #: 允许出现在 buffer 句柄 dict 内的键。
    handle_keys: frozenset = _HANDLE_KEYS

    def validate(self, payload: dict) -> Tuple[bool, list]:
        """校验一个 worker 传回的帧 payload。

        返回 ``(ok, reasons)``。``payload`` 必须是 dict；顶层键必须
        全部落在 :attr:`allowed_keys` 内；各键取值受限；``buffer``
        键（若存在）必须是只含 :attr:`handle_keys` 的 dict。
        """
        reasons: list = []
        if not isinstance(payload, dict):
            return (False, [f"payload must be dict, got "
                            f"{type(payload).__name__}"])
        for key, value in payload.items():
            if key not in self.allowed_keys:
                # 未知键 = 试图把非白名单对象图带进父进程。
                reasons.append(
                    f"non-whitelisted key {key!r} "
                    f"(worker 不得向父进程传对象图)")
                continue
            if key == "buffer":
                r = self._validate_buffer_handle(value)
                if r:
                    reasons.extend(r)
                continue
            if key in ("ok", "cancelled"):
                if not isinstance(value, bool):
                    reasons.append(
                        f"{key!r} must be bool, got {type(value).__name__}")
                continue
            if key in ("frame_id", "doc_id", "navigation_id"):
                if not isinstance(value, int) or isinstance(value, bool):
                    reasons.append(
                        f"{key!r} must be int, got {type(value).__name__}")
                continue
            # kind / url / error_category — 有限字符串（<= 128）。
            if not isinstance(value, str):
                reasons.append(
                    f"{key!r} must be str, got {type(value).__name__}")
            elif len(value) > _MAX_STR_LEN:
                reasons.append(
                    f"{key!r} exceeds {_MAX_STR_LEN} chars "
                    f"({len(value)})—父进程只接收有限摘要")
        return (not reasons, reasons)

    def _validate_buffer_handle(self, value) -> list:
        """校验 ``buffer`` 句柄：只允许描述像素位置 / 大小 / 格式，
        不允许携带像素数据本身或任意对象图。"""
        if value is None:
            return []  # buffer 键可缺省（None = 本帧无像素 buffer）
        if not isinstance(value, dict):
            return [f"buffer must be dict or None, got "
                    f"{type(value).__name__}"]
        reasons = []
        for key, val in value.items():
            if key not in self.handle_keys:
                reasons.append(f"buffer key {key!r} not allowed "
                               f"(handle 只描述像素引用，不传数据)")
                continue
            if key == "id":
                if not (isinstance(val, (int, str))):
                    reasons.append(f"buffer.id must be int/str, got "
                                   f"{type(val).__name__}")
            elif key == "size":
                if not isinstance(val, int) or isinstance(val, bool) \
                        or val < 0 or val > _MAX_HANDLE_SIZE:
                    reasons.append(
                        f"buffer.size must be 0..{_MAX_HANDLE_SIZE} "
                        f"int, got {val!r}")
            elif key == "format":
                if not isinstance(val, str) or val not in _FORMAT_BPP:
                    reasons.append(
                        f"buffer.format must be one of "
                        f"{sorted(_FORMAT_BPP)}, got {val!r}")
            elif key in ("width", "height"):
                if not isinstance(val, int) or isinstance(val, bool) \
                        or val < 0:
                    reasons.append(
                        f"buffer.{key} must be non-negative int, got "
                        f"{val!r}")
            elif key == "shared":
                # 跨进程共享段名（frame_shared 命名段；str 或 None 表
                # 本帧未走共享段）。父进程据此定位像素，仍"不携带像素
                # 数据本身"。
                if val is not None and not (
                        isinstance(val, str)
                        and len(val) <= _MAX_STR_LEN):
                    reasons.append(
                        f"buffer.shared must be str<={_MAX_STR_LEN} or "
                        f"None, got {type(val).__name__}")
        return reasons


__all__ = [
    "FrameBufferSpec", "FramePayloadContract",
    "FrameBufferSpec", "FramePayloadContract",
]
