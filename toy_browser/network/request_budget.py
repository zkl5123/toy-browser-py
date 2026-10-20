# R7-S04 — 网络请求的时间与字节预算（纯逻辑层，不 import 运行时）
#
# 审计 S04 第 3 点：deadline、取消与连接关闭要放进**共同 finally**，
# 超限连接不得回池。本模块只提供**决策**（该不该中止、还有多少
# 预算），不做 socket 操作——真实连接关闭仍由 net.py 调用方执行，
# 这里不冒充"已经关了"。
#
# 契约（测试锁定）：
#   * 时间预算用单调时钟（调用方注入 now_fn，本模块自己不读时钟，
#     唯一例外是 Deadline.now 工厂）；
#   * 取消是**粘性**的：一旦 cancel() 置位，后续 check 永远抛错；
#   * 页级聚合预算：原始字节与解压后字节**分别**累计（解压炸弹
#     原始字节再小，解压字节也会顶到预算），任一超限即剩余请求
#     全部拒绝（审计 S04 第 1 点的"每页面总预算"）；
#   * 所有判定无副作用、无 I/O。
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


class RequestCancelled(Exception):
    """R7-S04 — 请求在 deadline 前被取消。net 层把它归入
    "连接必须关闭、不入池"的失败类（与 ResponseTooLarge 同等待遇）。"""


@dataclass(frozen=True)
class Deadline:
    """一次性 wall-clock 预算。

    ``monotonic`` 是 :func:`time.monotonic` 的值（构造时注入，
    本类自己不读时钟——保持纯逻辑、可测试）。
    """
    monotonic: float
    budget: float  # 秒；<=0 表示未启用

    def remaining(self, now: float) -> float:
        """*now* 必须由调用方注入（本模块自己不读时钟，纯逻辑可测）。"""
        if self.budget <= 0:
            return float("inf")
        return max(0.0, self.monotonic + self.budget - now)

    def expired(self, now: float) -> bool:
        return self.remaining(now) <= 0.0

    @classmethod
    def now(cls, budget: float) -> "Deadline":
        """按**当前单调时钟**建预算（本模块唯一允许读时钟的入口）。"""
        return cls(time.monotonic(), budget)


@dataclass
class CancellationScope:
    """一次请求（或其重定向链）的取消状态。粘性：取消后不可撤销。"""
    cancelled: bool = False
    #: 取消原因摘要（有限字符串，不带 traceback——审计第 5 点
    # "日志只记录有限摘要"）。
    reason: str = ""

    def cancel(self, reason: str = "cancelled") -> None:
        if not self.cancelled:
            self.cancelled = True
            self.reason = reason[:128]  # 有限摘要，防注入超长原因

    def check(self) -> None:
        """已取消即抛 :class:`RequestCancelled`（调用方放在共同
        finally 之前检查，确保清理路径统一）。"""
        if self.cancelled:
            raise RequestCancelled(self.reason or "cancelled")


@dataclass
class PageBudget:
    """每页面（client）聚合字节预算。

    原始字节与解压后字节分别累计——解压炸弹即使原始字节很小，
    解压字节也会顶到预算上限。超限后 :attr:`exhausted` 为 True，
    该 client 的**剩余请求**全部拒绝（审计 S04 第 1 点"每页面
    总预算"）。

    本对象无默认上限语义：构造时显式传 ``raw`` / ``decoded``，
    调用方（net.HttpClient）负责给合理默认，避免"缺省即无限制"
    这种隐性缺省。
    """
    raw: int = 256 * 1024 * 1024
    decoded: int = 256 * 1024 * 1024
    raw_used: int = 0
    decoded_used: int = 0

    def charge(self, raw_bytes: int, decoded_bytes: int) -> None:
        """一次成功请求之后记账（先记账后继续，失败请求不计）。"""
        self.raw_used += max(0, raw_bytes)
        self.decoded_used += max(0, decoded_bytes)

    def would_exceed(self, raw_bytes: int, decoded_bytes: int) -> bool:
        return (self.raw_used + max(0, raw_bytes) > self.raw
                or self.decoded_used + max(0, decoded_bytes) > self.decoded)

    @property
    def exhausted(self) -> bool:
        # 恰好用满（== 上限）也算耗尽——没有余量了，剩余请求同样拒绝。
        return self.raw_used >= self.raw or self.decoded_used >= self.decoded

    def remaining_for(self, raw_bytes: int, decoded_bytes: int) -> bool:
        """还能容纳这一笔吗（不记账，纯判定）。"""
        return not self.would_exceed(raw_bytes, decoded_bytes)
