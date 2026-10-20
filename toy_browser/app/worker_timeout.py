# R7-S04 第 5 点 — worker 超时后父进程的有界终止契约（纯逻辑层）
#
# 审计 S04 第 5 点："worker 超时后由父进程有界终止并展示错误页，
# 允许关闭/新导航；日志只记录有限摘要"。
#
# 本模块只提供**决策**：给定任务起始时间和超时预算，父进程该做
# 哪个动作（继续等 / 发起有界终止 / 终止已完成可展示错误页）。
# 真实进程终止（terminate/kill/join）仍由调用方（DocProcessServer
# 或 GUI）执行，这里不冒充"已经终止"——只给出**该不该终止、
# 终止后能不能再导航**的判定。
#
# 契约（测试锁定）：
#   * 时钟由调用方注入（单调值），本模块自身不读系统时钟；
#   * 终止是**有界**的：``terminate`` 阶段有一个 join 预算，
#     超时后标记 ``forced_kill`` 但仍允许关闭/新导航（不无限等待）；
#   * 日志摘要截断到有限长度（防注入超长字符串）；
#   * 终止完成前**不允许**发起新导航（粘性终态，防旧任务干扰新页）。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class WorkerTaskTimedOut(Exception):
    """R7-S04 第 5 点 — 任务在父进程侧的超时预算内未产出帧。
    调用方据此发起有界终止，而不是无限等待。"""


@dataclass
class WorkerTaskRecord:
    """一个任务在父进程侧的可见状态（不含 worker 内部细节）。"""
    tab_id: int
    navigation_id: int
    #: 任务提交的单调时钟值（由调用方注入）。
    submitted_at: float
    #: 任务超时预算（秒）；<= 0 等价未启用（不限时）。
    timeout: float = 0.0

    def expired(self, now: float) -> bool:
        """任务是否已超时（纯判定，不读时钟）。"""
        if self.timeout <= 0:
            return False
        return now >= self.submitted_at + self.timeout

    def check(self, now: float) -> None:
        """已超时即抛 :class:`WorkerTaskTimedOut`。"""
        if self.expired(now):
            raise WorkerTaskTimedOut(
                f"task tab={self.tab_id} nav={self.navigation_id} "
                f"timed out (budget={self.timeout}s)")


@dataclass
class WorkerTerminationState:
    """父进程对有界终止的跟踪状态。

    状态机：``idle → terminating → terminated``（终止后是否允许
    再导航由调用方根据 ``may_navigate`` 判定）。
    """
    state: str = "idle"   # idle | terminating | terminated
    #: 发起终止时的单调时钟值。
    started_at: float = 0.0
    #: join 预算（秒）：超时后标记 forced_kill 但仍允许导航。
    join_budget: float = 2.0
    #: 终止完成时记录有限摘要（防注入）。
    summary: str = ""
    #: 是否在 join 预算内自然结束（False = forced_kill）。
    graceful: bool = True

    def begin_terminate(self, now: float, summary: str = "") -> None:
        """发起有界终止（幂等：已 terminated 则忽略）。"""
        if self.state == "terminated":
            return
        self.state = "terminating"
        self.started_at = now
        self.summary = summary[:128]  # 有限摘要，防超长注入

    def join_elapsed(self, now: float) -> float:
        """距发起终止已过去多久（纯判定，不读时钟）。"""
        if self.state == "idle":
            return 0.0
        return max(0.0, now - self.started_at)

    def check_join_budget(self, now: float) -> bool:
        """join 预算是否已用尽（超时后调用方强制终止，不再等待）。
        返回 True 表示**可以且应该**发出 forced_kill。"""
        if self.state != "terminating":
            return False
        return self.join_elapsed(now) >= self.join_budget

    def complete(self, now: float, natural: bool = True) -> None:
        """终止流程完成（自然退出或 forced_kill 之后）。

        *natural* — worker 是否在 join 预算内自行结束（True 是，
        False 是 forced_kill 之后）。
        """
        self.state = "terminated"
        self.graceful = natural
        # 有限摘要：保留原因，不保留 traceback。

    @property
    def may_navigate(self) -> bool:
        """终止完成后是否允许新导航。

        审计第 5 点："允许关闭/新导航"——只有 state == terminated
        才放行；terminating 中仍不允许（防旧任务干扰新页）。
        """
        return self.state == "terminated"

    def describe(self) -> str:
        return (f"state={self.state} graceful={self.graceful} "
                f"summary={self.summary!r}")
