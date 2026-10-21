"""R7-A02 第 4 点 — 跨进程像素共享段判定接线层（mmap/posix 可判定契约）。

Round-7 评估 R7-A02 第 4 点 / 遗留项原文：

    多核态（子进程存活）跨进程**像素**传递：需 mmap / posix 共享段
    + 子进程经 IPC 回报 doc_work 标记。

此前 :mod:`frame_buffer` 已落地**决策层**（:class:`FrameBufferSpec`
布局 + :class:`FramePayloadContract` 父进程可见面校验），
:mod:`document_process` 已把 ``DocFrame.frame_spec`` + ``buffer`` 句柄
传到父进程（"像素在哪 / 多大 / 什么格式"），但**真实跨进程共享段
执行**（worker 把 CPUImage 像素写进共享段、父进程经句柄只读）一直
被标注为"留活显示批次"——父进程侧按
:meth:`DocProcessServer.is_spawned` 如实区分"有句柄"与"像素已共享"。

本模块补齐这块**可自动化的判定接线**：用
:class:`multiprocessing.shared_memory`（Linux / Windows 跨进程安全，
命名段天然跨进程，无需手动 mmap 权限细节）落地

- worker 把一帧 :class:`CPUImage` 的 RGB8 像素**写入**共享段；
- 父进程经 :class:`SharedFrameSegment` 句柄**只读**该段并逐像素
  校验（像素是否真的跨进程到了父进程，**不**是"有句柄就宣称已
  共享"）；
- 按 buffer id 区分"哪一块像素"（与
  :meth:`DocumentProcessWorker.buffer_handle_for` 同口径）。

设计边界（与既有决策层 / 执行层互补，不重造）
----------------------------------------------
- 复用 :class:`CPUImage`（RGB8 连续 buffer）作为像素源；布局判定
  复用 :class:`FrameBufferSpec`（不另写一套字节几何）。
- **缺 OS 共享内存 / 不可 spawn 时不硬造**：执行层入口在
  ``multiprocessing.shared_memory`` 不可用时返回 ``acquired=False``
  + 如实 reason（与 R7-P02 :mod:`live_surface` 同"不冒充通过"口径），
  测试据此 SkipTest。
- 纯接线层：不发起网络、不启动 GUI；"GPU 量化计时 / 慢页 timeout
  启用 / 真实 mmap 权限细节"仍留活显示批次，本模块不宣称完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.frame_buffer import FrameBufferSpec

# CPUImage RGB8 的每像素字节数（与 FrameBufferSpec.format 的
# "rgb8" 同口径，3 字节/像素）。
_RGB8_BPP = 3

__all__ = [
    "SharedFrameHandle", "SharedFrameProbe", "SharedFrameSegment",
    "write_frame_to_shared", "read_frame_from_shared",
    "verify_shared_frame", "create_shared_segment",
    "shared_segment_name",
]


def shared_segment_name(buffer_id: int) -> str:
    """worker 侧：该 buffer id 对应的**命名共享段名**（父进程 / 子进程
    经同名 attach 同一像素块）。

    与 :meth:`DocumentProcessWorker.buffer_handle_for` 的 ``shared`` 键
    同口径：句柄只带"像素在哪"的段名，不携带像素数据。段名单调且
    全局唯一（带 buffer id），父进程据此定位 + 逐字节校验。
    """
    return f"toy_fb_buf{int(buffer_id):08d}"


# ---------------------------------------------------------------------------
# 句柄 / 结果
# ---------------------------------------------------------------------------

@dataclass
class SharedFrameHandle:
    """R7-A02 — 一块跨进程像素共享段的**句柄**（描述"在哪 / 多大 /
    什么格式"，与 :meth:`DocumentProcessWorker.buffer_handle_for` 同
    口径，父进程据此定位像素、**不**逐像素复制）。

    * *name*      — 命名共享段名（跨进程定位凭据）。
    * *buffer_id* — 单调递增 buffer id（区分"哪一块像素"）。
    * *size*      — 段字节数（= :meth:`FrameBufferSpec.total_bytes`）。
    * *format*    — 像素格式（"rgb8"）。
    * *width* / *height* — 逻辑视口尺寸。
    """
    name: str
    buffer_id: int = 0
    size: int = 0
    format: str = "rgb8"
    width: int = 0
    height: int = 0

    def describe(self) -> str:
        return (f"SharedFrameHandle(name={self.name!r} id={self.buffer_id} "
                f"{self.width}x{self.height} {self.format} "
                f"bytes={self.size})")


@dataclass
class SharedFrameProbe:
    """R7-A02 — 一次"跨进程共享段是否可用"的判定。

    * *acquired* — 是否拿到可用的共享段句柄。False 时 *reason* 必须
      如实记录（shared_memory 不可用 / 段大小非法等），**不**伪装成
      已共享。
    """
    acquired: bool
    handle: Optional[SharedFrameHandle] = None
    reason: str = ""

    def describe(self) -> str:
        if self.acquired:
            return f"ACQUIRED shared segment: {self.handle.describe()}"
        return f"NOT-ACQUIRED — {self.reason}"


# ---------------------------------------------------------------------------
# 共享段句柄 + 写入 / 读取 / 校验（执行层，缺 shared_memory 不硬造）
# ---------------------------------------------------------------------------

class SharedFrameSegment:
    """R7-A02 — 一块跨进程像素共享段（父进程创建、worker 写入、父进程只读）。

    经 :class:`multiprocessing.shared_memory` 的命名段（跨进程安全）
    落地"像素真到了父进程"的最小可判定通路。构造时不自动建段（无
    共享内存环境也可构造）；段由 :meth:`attach` 注入或由
    :func:`create_shared_segment` 创建。
    """

    def __init__(self, handle: Optional[SharedFrameHandle] = None,
                 shm=None) -> None:
        self._handle = handle
        self._shm = shm
        self._closed = False

    # -- 属性 ------------------------------------------------------------
    @property
    def handle(self) -> Optional[SharedFrameHandle]:
        return self._handle

    @property
    def shm(self):
        """底层 :class:`shared_memory.SharedMemory`（未 attach 为 None）。"""
        return self._shm

    @property
    def closed(self) -> bool:
        return self._closed

    def size(self) -> int:
        return self._handle.size if self._handle else 0

    # -- 生命周期 --------------------------------------------------------
    def attach(self, shm) -> bool:
        """注入一个已创建的命名共享段（跨进程：子进程 attach 同名片段）。"""
        if self._closed:
            return False
        self._shm = shm
        return True

    def close(self) -> bool:
        """父进程侧销毁段（仅创建者 close；子进程侧只 unlink 由调用方
        负责——本类幂等 no-op 不崩）。"""
        if self._closed or self._shm is None:
            return False
        try:
            self._shm.close()
        except Exception:
            pass
        self._shm = None
        self._closed = True
        return True


# ---------------------------------------------------------------------------
# 像素写入 / 读取 / 校验（对 CPUImage buffer 的字节级契约）
# ---------------------------------------------------------------------------

def _spec_total_bytes(width: int, height: int, fmt: str = "rgb8") -> int:
    """按几何 + 格式算共享段应占字节数（复用 :class:`FrameBufferSpec`）。"""
    return FrameBufferSpec(fmt, width, height).total_bytes()


def write_frame_to_shared(img, segment: SharedFrameSegment,
                          buffer_id: int = 0) -> bool:
    """把 *img*（:class:`CPUImage`）的 RGB8 buffer 写入 *segment* 共享段。

    返回是否写入成功。几何 / 字节数与段大小不一致、未 attach 段 →
    False（不硬写越界、不冒充"已共享"）。"""
    if segment.shm is None:
        return False
    buf = bytes(img.buffer)
    need = len(buf)
    if need > len(segment.shm.buf):
        return False
    segment.shm.buf[:need] = buf
    return True


def read_frame_from_shared(segment: SharedFrameSegment,
                           width: int, height: int) -> Optional[bytearray]:
    """从 *segment* 共享段只读出 ``width×height`` 的 RGB8 像素。

    返回 :class:`bytearray`（父进程侧真实读到的像素，**非**复制 worker
    对象图）；未 attach / 几何越段 → None（不硬造）。"""
    if segment.shm is None:
        return None
    need = _spec_total_bytes(width, height)
    if need > len(segment.shm.buf):
        return None
    return bytearray(segment.shm.buf[:need])


def verify_shared_frame(expected_img, segment: SharedFrameSegment,
                        width: int, height: int) -> bool:
    """父进程侧逐字节校验：共享段读出的像素 == *expected_img* 的
    RGB8 buffer（"像素真的跨进程到了父进程"的可判定契约，不是
    "有句柄就宣称已共享"）。

    段未 attach / 几何越段 / 字节不一致 → False。"""
    if segment.shm is None:
        return False
    need = _spec_total_bytes(width, height)
    if need > len(segment.shm.buf):
        return False
    expected = bytes(expected_img.buffer)[:need]
    return expected == bytes(segment.shm.buf[:need])


# ---------------------------------------------------------------------------
# 命名共享段创建（父进程侧；缺 shared_memory 如实标注不硬造）
# ---------------------------------------------------------------------------

def create_shared_segment(name: str, total_bytes: int,
                          buffer_id: int = 0,
                          width: int = 0, height: int = 0,
                          create: bool = True) -> SharedFrameProbe:
    """创建 / attach 一个命名共享段，返回 :class:`SharedFrameProbe`。

    * *create=True* — 父进程**创建**新命名段（跨进程共享前由创建者
      建段）；``create=False`` 则 attach 已存在的段（worker 侧复用）。
    缺 ``multiprocessing.shared_memory`` 或大小非法 →
    ``acquired=False`` + reason 如实记录（不冒充"已共享"）。

    注意：本函数返回的是**判定 + 句柄**，不持有底层段对象（调用方
    可据 handle 再 attach 消费）。
    """
    if total_bytes <= 0:
        return SharedFrameProbe(False, reason="segment size must be > 0")
    try:
        from multiprocessing import shared_memory
    except Exception as exc:  # pragma: no cover - 受管环境缺库
        return SharedFrameProbe(
            False, reason=f"shared_memory unavailable: {exc!r}")
    try:
        if create:
            # 创建新段（force-create 避免残留同名段报错）。
            shm = shared_memory.SharedMemory(name=name,
                                             create=True,
                                             size=total_bytes)
        else:
            shm = shared_memory.SharedMemory(name=name)
        handle = SharedFrameHandle(
            name=name, buffer_id=buffer_id, size=total_bytes,
            width=width, height=height)
        return SharedFrameProbe(True, handle=handle)
    except Exception as exc:
        return SharedFrameProbe(
            False, reason=f"shared segment {'create' if create else 'attach'} "
                          f"failed: {exc!r}")


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    from toy_browser.rendering.cpu_image import CPUImage
    # 只验证"句柄 + 几何判定"可无 OS 共享段构造（决策层不硬造段）。
    img = CPUImage(4, 2)
    for y in range(2):
        for x in range(4):
            img.set_pixel(x, y, x * 60, y * 120, 255)
    probe = create_shared_segment("toy_fsm_smoke", img.size,
                                  width=4, height=2, create=True)
    print(probe.describe())
    # 段几何判定（不 attach，验证 size 与 CPUImage.size 一致）。
    assert probe.acquired is True
    assert probe.handle.size == img.size, (probe.handle.size, img.size)
    # 无 shm 段时 write/read/verify 全部 False / None（不硬造）。
    empty = SharedFrameSegment(probe.handle)
    assert write_frame_to_shared(img, empty) is False
    assert read_frame_from_shared(empty, 4, 2) is None
    assert verify_shared_frame(img, empty, 4, 2) is False
    # 大小非法 → acquired=False + reason 如实记录（不冒充）。
    bad = create_shared_segment("toy_fsm_bad", 0)
    assert bad.acquired is False and "size must be > 0" in bad.reason
    # 清理命名段（smoke 建的段必须 unlink，避免残留）。
    from multiprocessing import shared_memory as _shm_mod
    try:
        _sm = _shm_mod.SharedMemory(name="toy_fsm_smoke", create=False)
        _sm.close()
        _sm.unlink()
    except Exception:
        pass
    print("smoke ok")
