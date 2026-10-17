"""批次 29 — C02 step 4 帧版本与读写所有权（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` C02
step 4：

> 缓冲池按尺寸复用；**帧版本与读写所有权明确，不能 UI 读取时工作
> 线程仍在改图**。

C02 验收：

> 随机像素、不同 stride、奇数宽度、透明图片和小图逐像素一致；兼容
> 现有 CLI。

C02 step 1–3（:class:`toy_browser.rendering.cpu_image.CPUImage` /
:class:`toy_browser.rendering.cpu_image.ImagePool`，批次 15）已落地
连续像素缓冲区 + 按尺寸复用池；本批补**帧版本与读写所有权**——
保证"UI 读取时工作线程不得仍在改图"（C02 step 4 核心约束）：

* :class:`OwnedBuffer` — 一个带**版本** + **所有权**标记的缓冲区
  句柄（:class:`toy_browser.rendering.cpu_image.CPUImage` 的外壳）：
  * ``version`` — 单调递增帧版本（写入方每改一次 ``bump`` 版本）。
  * ``owner`` — 当前所有权方（``"worker"`` / ``"ui"`` / ``"none"``）：
    工作线程（painter / rasterizer）写入时持有 ``worker`` 所有权；
    UI 读取前必须经 :meth:`request_read` 取得 ``ui`` 所有权
    （工作线程此时不能改图）。
  * :meth:`bump` — 写入方完成一次写入后 ``version += 1`` 并释放
    ``worker`` 所有权（交还可被 UI 请求）。
  * :meth:`request_read` — UI 请求读：成功则所有权转 ``ui``、返回
    当前 ``version``（UI 读这个版本）；若工作线程仍持有
    ``worker`` 所有权则**请求失败**（``acquired=False``，UI 需重试，
    绝不读到"正在被改"的图 —— C02 step 4 "不能 UI 读取时工作线程
    仍在改图"）。
  * :meth:`release_read` — UI 读完释放所有权（转 ``none``，工作线程
    可重新 ``acquire_write``）。
  * :meth:`acquire_write` — 工作线程取写所有权（仅当 ``owner in
    ("none", "ui" 已释放)`` 成功；``ui`` 持有时失败）。
* :class:`FrameVersion` — 帧版本快照（``version`` + ``width`` /
  ``height`` / ``stride`` / ``format``）：UI 读取时记录"我读的是第
  N 帧"，后续工作线程 ``bump`` 后 UI 可比对版本判断图是否已变
  （"读到旧帧则重读"）。
* :class:`OwnershipViolation` — 越权访问审计记录（谁在什么所有权
  状态下尝试了什么操作 + 时间戳序列号），诊断区 / 日志可追踪
  "UI 读到正在被改的图"这类所有权违规（C02 验收"不能 UI 读取时
  工作线程仍在改图"的回归断言）。
* :func:`assert_readable` — 校验某次读操作是否所有权合法（UI 持读权
  且版本一致）；不合法则记 :class:`OwnershipViolation` 并返回 False
  （调用方据此跳过 / 重试，不读脏数据）。

设计要点（与批次 15 ``ImagePool`` **兼容不重叠**）：
- ``ImagePool`` 管"按尺寸复用 + 上限"；本模块管"一个具体 buffer 的
  帧版本 + 读 / 写所有权"。``OwnedBuffer`` 包裹 ``CPUImage``（或直接
  持 buffer），工作线程写 → ``bump`` → UI ``request_read`` → 读 →
  ``release_read`` 的完整生命周期都经所有权闸门，杜绝"UI 读时工作
  线程仍在改图"。
- 单线程确定性（纯逻辑，无真实线程 / 锁）：所有权是**协议层**
  约束（谁在什么状态下能做什么），真实多线程 / 锁 / 共享内存槽
  （C02 step 5）留活显示批次；本批只把"版本 + 所有权"的状态机固化
  成可测纯逻辑。
- 纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib + 批次 15
  ``CPUImage``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.rendering.cpu_image import CPUImage


# 所有权方常量
OWNER_WORKER = "worker"   # 工作线程（写入方：painter / rasterizer）
OWNER_UI = "ui"           # UI 线程（读取方）
OWNER_NONE = "none"       # 无所有权（空闲）


@dataclass
class OwnershipViolation:
    """C02 step 4 — 一次越权访问审计记录。

    * ``operation`` — 试图做的操作（``request_read`` /
      ``acquire_write`` / ``assert_readable`` …）。
    * ``owner_at_time`` — 当时所有权方（``worker`` / ``ui`` /
      ``none``）。
    * ``expected`` — 该操作要求的所有权状态（人类可读）。
    * ``version`` — 当时帧版本。
    * ``seq`` — 审计序号（递增，诊断排序用）。
    * ``reason`` — 一句话原因。
    """
    operation: str
    owner_at_time: str
    expected: str
    version: int
    seq: int
    reason: str = ""

    def summary(self) -> str:
        return (f"[{self.seq}] {self.operation} @owner={self.owner_at_time} "
                f"(want {self.expected}) v{self.version}: {self.reason}")


# ----------------------------------------------------------------------
# 帧版本快照
# ----------------------------------------------------------------------

@dataclass
class FrameVersion:
    """C02 step 4 — 一次 UI 读取锁定的帧版本 + 几何。

    UI 读 :class:`OwnedBuffer` 时记录 ``FrameVersion``；之后工作线程
    若 ``bump`` 出新版本，UI 比对 ``version`` 即可判断"我读的图是否
    已过期"（过期则重读，C02 step 4 帧版本语义）。
    """
    version: int
    width: int
    height: int
    stride: int
    format: str

    def is_stale(self, current_version: int) -> bool:
        """UI 锁的版本是否已落后于 *current_version*（工作线程 bump 过）。"""
        return current_version > self.version


# ----------------------------------------------------------------------
# 带所有权 + 帧版本的缓冲区句柄
# ----------------------------------------------------------------------

class OwnedBuffer:
    """C02 step 4 — :class:`CPUImage` 的**所有权 + 帧版本**外壳。

    生命周期（写入方 → 读取方 闸门）::

        worker.acquire_write()  → 写 image → worker.bump()
        ui.request_read()        → 成功取读权 + 锁 FrameVersion → 读
        ui.release_read()        → 释放读权（worker 可再 acquire_write）

    任何"UI 读时 worker 仍持写权"的越权都被 :meth:`request_read`
    拒绝（返回 ``acquired=False``）并记 :class:`OwnershipViolation`
    （C02 step 4 "不能 UI 读取时工作线程仍在改图"）。
    """

    def __init__(self, image: Optional[CPUImage] = None) -> None:
        self.image = image or CPUImage(width=1, height=1)
        self.version = 0
        self.owner = OWNER_NONE
        # 审计
        self._violations: list[OwnershipViolation] = []
        self._seq = 0

    # ---- 几何 / 版本快照 ------------------------------------------
    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    @property
    def stride(self) -> int:
        return self.image.stride

    def frame_version(self) -> FrameVersion:
        """当前帧版本快照（UI 锁定时取）。"""
        return FrameVersion(
            version=self.version,
            width=self.width, height=self.height,
            stride=self.stride, format=self.image.format)

    # ---- 工作线程（写入方） ----------------------------------------
    def acquire_write(self) -> bool:
        """工作线程取写所有权。

        仅当 *owner* 为 ``none``（空闲）或 ``ui`` 已 ``release_read``
        成功；若 ``ui`` 仍持读权（UI 正在读图）→ 失败 + 记违规
        （C02 step 4：工作线程不能在 UI 读时改图）。"""
        if self.owner == OWNER_UI:
            self._record("acquire_write", "owner=none",
                         "worker 试图在 UI 读时取写权（UI 仍持读权）")
            return False
        self.owner = OWNER_WORKER
        return True

    def bump(self) -> int:
        """工作线程完成一次写入：``version += 1`` + 释放 ``worker``
        所有权（转 ``none``，可被 UI 请求读）。

        仅当 *owner* 为 ``worker`` 合法；否则记违规（无写权却改图）。"""
        if self.owner != OWNER_WORKER:
            self._record("bump", "owner=worker",
                         "无写权却 bump 帧版本（越权改图）")
            return self.version
        self.version += 1
        self.owner = OWNER_NONE
        return self.version

    # ---- UI 线程（读取方） ------------------------------------------
    def request_read(self) -> "tuple[bool, FrameVersion]":
        """UI 请求读所有权 + 锁帧版本。

        * 成功：所有权转 ``ui``、返回 ``(True, FrameVersion)``（UI
          读这个版本）。
        * 失败（工作线程仍持 ``worker`` 写权）：记违规、返回
          ``(False, 当前 FrameVersion)``（UI 需等 worker ``bump`` 后
          重试，**绝不读到正在被改的图** —— C02 step 4）。"""
        if self.owner == OWNER_WORKER:
            self._record("request_read", "owner=none|ui",
                         "worker 仍持写权，UI 读请求被拒（防读到正在改的图）")
            return False, self.frame_version()
        self.owner = OWNER_UI
        return True, self.frame_version()

    def release_read(self) -> None:
        """UI 读完释放读所有权（转 ``none``，worker 可再写）。"""
        if self.owner == OWNER_UI:
            self.owner = OWNER_NONE
        # 非 UI 持有时释放是幂等空操作（不记违规，避免误报）

    # ---- 读校验（C02 验收"读到的是锁定帧，不是正在改的帧"） --------
    def assert_readable(self, locked: FrameVersion) -> bool:
        """校验某次读是否所有权 + 版本合法。

        合法条件（三者同时）：
        - ``owner == ui``（UI 持读权，不是 worker 改图中）；
        - ``locked.version == self.version``（UI 锁的帧就是当前帧，
          没读到旧帧）；
        - 几何一致（``locked.width/height/stride == self.*``）。
        任一不满足 → 记违规 + 返回 False（调用方跳过 / 重读，
        不读脏数据）。"""
        geom_ok = (locked.width == self.width
                   and locked.height == self.height
                   and locked.stride == self.stride)
        ok = (self.owner == OWNER_UI
              and locked.version == self.version
              and geom_ok)
        if not ok:
            self._record("assert_readable", "owner=ui & 同版本 & 同几何",
                         f"读校验失败 owner={self.owner} "
                         f"locked_v{locked.version} cur_v{self.version} "
                         f"geom_ok={geom_ok}")
        return ok

    # ---- 审计 --------------------------------------------------------
    def _record(self, operation: str, expected: str, reason: str) -> None:
        self._seq += 1
        self._violations.append(OwnershipViolation(
            operation=operation,
            owner_at_time=self.owner,
            expected=expected,
            version=self.version,
            seq=self._seq,
            reason=reason,
        ))

    def violations(self) -> list:
        """越权访问审计记录（诊断区 / 回归断言"无越权"用）。"""
        return list(self._violations)

    def violation_count(self) -> int:
        return len(self._violations)

    def clear_violations(self) -> None:
        """清空审计（重置回归基线）。"""
        self._violations = []


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    import sys
    sys.path.insert(0, ".")
    img = CPUImage(width=4, height=4)
    buf = OwnedBuffer(img)
    # 正常生命周期
    assert buf.acquire_write() is True
    img.set_pixel(0, 0, 255, 0, 0)
    v = buf.bump()
    assert v == 1 and buf.owner == OWNER_NONE
    ok, locked = buf.request_read()
    assert ok is True and locked.version == 1
    assert buf.assert_readable(locked) is True
    buf.release_read()
    print("normal flow: version=", buf.version, "violations=",
          buf.violation_count())
    # 越权：worker 在 UI 读时 bump
    ok2, _ = buf.request_read()
    # 此时 ui 持权；模拟 worker 试图再 acquire_write（应被拒）
    assert buf.acquire_write() is False
    print("violations now:", buf.violation_count(), buf.violations()[0].summary())
