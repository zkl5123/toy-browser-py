"""B10 — 消除加载阻塞：阶段状态机 + 取消 + 用户任务级回归骨架（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B10（批次 19 纯逻辑落点；GUI 任务队列 / 活显示验收留后续）。

与已有模块的关系：
- **B10 步骤 1**（每次导航携带版本号，取消 / 切标签后旧结果不得覆盖
  新页面）由 :class:`PhaseTracker` 的 ``nav_version`` +
  :meth:`accept_phase` / :meth:`drop_stale` 承担，与批次 17
  ``NavigationController.drop_stale`` + ``DocumentContext.task_version``
  对齐。
- **B10 步骤 2**（连接 / 下载 / 解析 / 执行 / 布局 / 绘制阶段状态 +
  取消入口；预算耗尽与网站拒绝访问分别提示）由
  :class:`PhaseTracker` 的阶段状态机 + :class:`PhaseBudget` 承担。
- **B10 步骤 4**（在线任务固定为"首页可用 → 输入 → Enter/按钮搜索 →
  结果可读 → 结果跳转 → 返回"，被前置问题阻断的步骤明确标为
  ``blocked``）由 :class:`UserTaskRunner` 承担。
- **B10 步骤 5**（至少 3 次独立会话复测，记录失败分布）由
  :class:`TaskRepro` 承担。

无 Tk / 无网络 I/O；``tests/test_load_phases.py`` 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- B10 步骤 2：阶段词表（连接 → 下载 → 解析 → 执行 → 布局 → 绘制） --------

PH_CONNECT = "connect"
PH_DOWNLOAD = "download"
PH_PARSE = "parse"
PH_EXECUTE = "execute"
PH_LAYOUT = "layout"
PH_PRESENT = "present"

# 阶段顺序（用于进度 / 完成判定）。
_PHASE_ORDER = [PH_CONNECT, PH_DOWNLOAD, PH_PARSE, PH_EXECUTE,
                PH_LAYOUT, PH_PRESENT]

# 阶段状态词表。
PH_PENDING = "pending"
PH_RUNNING = "running"
PH_DONE = "done"
PH_CANCELLED = "cancelled"
PH_BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# B10 步骤 2：阶段状态机 + 预算
# ---------------------------------------------------------------------------

class PhaseTracker:
    """B10 步骤 1/2 — 加载阶段状态机（带版本 + 取消 + 预算）。

    - ``nav_version`` — 本次导航版本号；旧版本结果丢弃（B10 步骤 1）。
    - ``phases`` — 阶段名 → 状态（``pending / running / done /
      cancelled / blocked``）。
    - ``budget`` — :class:`PhaseBudget`（响应大小 / 请求数 / 时间 /
      内存，B08 步骤 4 / B10 步骤 2 "预算耗尽"）。
    - 取消入口 :meth:`cancel`：把 running 阶段翻 cancelled，
      后续 :meth:`accept_phase` 拒绝已取消 / 旧版本的结果。
    """

    def __init__(self, nav_version: int = 1,
                 budget: Optional["PhaseBudget"] = None) -> None:
        self.nav_version = nav_version
        self.phases: dict[str, str] = {p: PH_PENDING for p in _PHASE_ORDER}
        self.budget = budget or PhaseBudget()
        self._current_phase: Optional[str] = None
        self.cancelled: bool = False
        self.blocked_reason: str = ""
        self._log: list[tuple[str, str, str]] = []   # (phase, old, new)

    # -- 阶段推进 ------------------------------------------------------------

    def current_phase(self) -> Optional[str]:
        return self._current_phase

    def start_phase(self, phase: str) -> bool:
        """进入某阶段（前置阶段须已 done；取消 / 超预算时拒绝）。"""
        if phase not in self.phases:
            return False
        if self.cancelled or self.budget.exhausted():
            self.phases[phase] = PH_BLOCKED
            self.blocked_reason = ("cancelled" if self.cancelled
                                   else self.budget.exhaust_reason())
            self._log.append((phase, self.phases.get(phase, ""),
                              PH_BLOCKED))
            return False
        # 前置阶段必须全部 done（连接→下载→…→绘制 顺序）。
        idx = _PHASE_ORDER.index(phase)
        for p in _PHASE_ORDER[:idx]:
            if self.phases.get(p) != PH_DONE:
                self.phases[phase] = PH_BLOCKED
                self.blocked_reason = f"phase-{p}-incomplete"
                return False
        self._current_phase = phase
        self.phases[phase] = PH_RUNNING
        self._log.append((phase, PH_PENDING, PH_RUNNING))
        return True

    def finish_phase(self, phase: str, ok: bool = True) -> bool:
        """完成某阶段；ok=False 时置 blocked（记录原因）。"""
        if phase not in self.phases:
            return False
        if self.cancelled:
            self.phases[phase] = PH_CANCELLED
            self._log.append((phase, self.phases.get(phase, ""),
                              PH_CANCELLED))
            return False
        if not ok:
            self.phases[phase] = PH_BLOCKED
            self.blocked_reason = f"{phase}-failed"
            self._log.append((phase, PH_RUNNING, PH_BLOCKED))
            return False
        self.phases[phase] = PH_DONE
        if self._current_phase == phase:
            self._current_phase = None
        self._log.append((phase, PH_RUNNING, PH_DONE))
        return True

    def accept_phase(self, phase: str, result_version: int,
                     ok: bool = True) -> bool:
        """B10 步骤 1 — 收到异步阶段结果：旧版本 / 已取消时丢弃。"""
        if result_version < self.nav_version:
            return False
        if self.cancelled:
            return False
        return self.finish_phase(phase, ok=ok)

    def drop_stale(self, version: int) -> bool:
        """B10 步骤 1 — 旧导航版本的结果丢弃（真值 = 已过期）。"""
        return version < self.nav_version

    def new_navigation(self) -> None:
        """B10 步骤 1 — 新导航：bump 版本、复位阶段（旧结果自然丢弃）。"""
        self.nav_version += 1
        self.cancelled = False
        self.blocked_reason = ""
        self._current_phase = None
        for p in _PHASE_ORDER:
            self.phases[p] = PH_PENDING

    # -- 取消入口（B10 步骤 2 "取消/切标签"） --------------------------------

    def cancel(self) -> None:
        self.cancelled = True
        if self._current_phase:
            self.phases[self._current_phase] = PH_CANCELLED
            self._log.append((self._current_phase, PH_RUNNING,
                              PH_CANCELLED))
        # 后续 pending 阶段也标记 cancelled（不再推进）。
        for p in _PHASE_ORDER:
            if self.phases.get(p) == PH_PENDING:
                self.phases[p] = PH_CANCELLED

    # -- 进度 / 完成 ----------------------------------------------------------

    @property
    def progress(self) -> float:
        """B10 步骤 2 — 完成阶段数 / 总阶段数（0.0–1.0）。"""
        done = sum(1 for p in _PHASE_ORDER
                   if self.phases.get(p) == PH_DONE)
        return done / len(_PHASE_ORDER)

    @property
    def all_done(self) -> bool:
        return all(self.phases.get(p) == PH_DONE for p in _PHASE_ORDER)

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled

    def status_label(self) -> str:
        """B10 步骤 2 — 区分"预算耗尽 / 网站拒绝 / 取消 / 完成 / 进行中"。"""
        if self.cancelled:
            return "cancelled"
        if self.budget.exhausted():
            return f"budget-exhausted({self.budget.exhaust_reason()})"
        if self.all_done:
            return "complete"
        if self.blocked_reason:
            return f"blocked({self.blocked_reason})"
        return f"in-progress({self.current_phase or 'idle'})"

    def transition_log(self) -> list:
        return list(self._log)


@dataclass
class PhaseBudget:
    """B10 步骤 2 + B08 步骤 4 — 响应大小 / 请求数 / 时间 / 内存预算。

    - ``max_requests`` — 请求数预算（超过则后续下载阶段阻断）。
    - ``max_bytes``    — 累计响应大小预算。
    - ``max_seconds``  — 时间预算。
    - ``max_mem``      — 内存预算（toy 占位）。
    耗尽时 :meth:`exhausted` 返回 True + ``exhaust_reason()`` 说明。
    """
    max_requests: int = 64
    max_bytes: int = 32 * 1024 * 1024
    max_seconds: float = 60.0
    max_mem: int = 512 * 1024 * 1024
    requests_used: int = 0
    bytes_used: int = 0
    seconds_used: float = 0.0
    mem_used: int = 0

    def record_request(self, nbytes: int = 0, seconds: float = 0.0,
                      mem: int = 0) -> None:
        self.requests_used += 1
        self.bytes_used += nbytes
        self.seconds_used += seconds
        self.mem_used = max(self.mem_used, mem)

    def exhausted(self) -> bool:
        if self.requests_used > self.max_requests:
            return True
        if self.bytes_used > self.max_bytes:
            return True
        if self.seconds_used > self.max_seconds:
            return True
        if self.mem_used > self.max_mem:
            return True
        return False

    def exhaust_reason(self) -> str:
        if self.requests_used > self.max_requests:
            return "requests"
        if self.bytes_used > self.max_bytes:
            return "bytes"
        if self.seconds_used > self.max_seconds:
            return "time"
        if self.mem_used > self.max_mem:
            return "memory"
        return ""


# ---------------------------------------------------------------------------
# B10 步骤 4：用户任务级回归骨架（"首页可用 → 输入 → 搜索 → 结果 →
# 跳转 → 返回"，被前置问题阻断的步骤明确标为 blocked）
# ---------------------------------------------------------------------------

@dataclass
class TaskStep:
    """B10 步骤 4 — 一个用户任务步骤。

    - ``status`` — ``pending`` / ``ok`` / ``blocked`` / ``skipped``。
    - ``blocker`` — 被前置问题阻断的原因（``blocked`` 时非空）。
    - ``evidence`` — 保存的 URL / DOM / 错误 / 请求摘要 / 时间。
    """
    name: str
    status: str = "pending"
    blocker: str = ""
    evidence: dict = field(default_factory=dict)

    def mark_ok(self, evidence: Optional[dict] = None) -> "TaskStep":
        self.status = "ok"
        if evidence:
            self.evidence.update(evidence)
        return self

    def mark_blocked(self, blocker: str) -> "TaskStep":
        self.status = "blocked"
        self.blocker = blocker
        return self

    @property
    def done(self) -> bool:
        return self.status in ("ok", "blocked")


class UserTaskRunner:
    """B10 步骤 4 — 固定用户任务"首页可用 → 输入 → 搜索 → 结果可读
    → 结果跳转 → 返回"，逐步执行 + 阻断标记。

    每个步骤接受一个 ``fn``（纯逻辑钩子，返回 evidence dict 或抛
    ``TaskBlocked(reason)``）；被前置问题阻断的步骤标记 ``blocked``，
    后续步骤级联跳过（``skipped``）。
    """

    # B10 步骤 4 固定六步。
    _STEPS = ("home-usable", "input", "search", "results-readable",
              "navigate-result", "back")

    def __init__(self) -> None:
        self.steps: list[TaskStep] = [TaskStep(n) for n in self._STEPS]
        self._index: int = 0
        self._completed: bool = False

    def plan(self) -> list[str]:
        """B10 步骤 4 — 任务步骤序列（固定六步）。"""
        return list(self._STEPS)

    def run_step(self, name: str, fn) -> TaskStep:
        """执行一步：``fn(evidence_sink)``；``TaskBlocked`` → blocked。

        前置步骤未 ``ok``（被阻断 / 跳过）时，本步骤级联标记
        ``skipped``（B10 步骤 4 "被前置问题阻断的步骤明确标为
        blocked"，后续步骤跳过）。
        """
        st = self._find(name)
        if st.status != "pending":
            return st
        # 前置步骤必须 ok（被阻断则本级联跳过）。
        prev = self._previous(name)
        if prev is not None and prev.status != "ok":
            st.status = "skipped"
            st.blocker = f"previous-step-{prev.name}-{prev.status}"
            return st
        sink: dict = {}
        try:
            fn(sink)
            st.mark_ok(sink)
        except TaskBlocked as e:
            st.mark_blocked(str(e))
            return st
        self._advance(name)
        return st

    def _find(self, name: str) -> TaskStep:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)

    def _previous(self, name: str) -> Optional[TaskStep]:
        idx = self._STEPS.index(name)
        return self.steps[idx - 1] if idx > 0 else None

    def _advance(self, name: str) -> None:
        idx = self._STEPS.index(name)
        if idx == len(self._STEPS) - 1 and \
                all(s.status == "ok" for s in self.steps):
            self._completed = True

    @property
    def completed(self) -> bool:
        """B10 步骤 4/5 — 一次闭环完成（全部 ok）。"""
        return self._completed

    def blocked_steps(self) -> list[str]:
        return [s.name for s in self.steps if s.status == "blocked"]

    def summary(self) -> dict:
        """B10 步骤 5 — 失败分布（各步骤状态 + 阻断原因）。"""
        out = {}
        for s in self.steps:
            out[s.name] = s.status + (f"({s.blocker})" if s.blocker else "")
        return out


# 给 TaskStep 的 skipped 状态提供显式语义（B10 步骤 4 "被前置问题
# 阻断的步骤明确标为 blocked，后续步骤跳过"）。
def _mark_skipped(self, reason: str = "") -> "TaskStep":
    self.status = "skipped"
    if reason:
        self.blocker = reason
    return self


TaskStep.mark_skipped = _mark_skipped  # type: ignore[attr-defined]


class TaskBlocked(Exception):
    """B10 步骤 4 — 步骤被前置问题阻断（钩子抛出）。"""
    pass


# ---------------------------------------------------------------------------
# B10 步骤 5：独立会话复测 + 失败分布
# ---------------------------------------------------------------------------

class TaskRepro:
    """B10 步骤 5 — 至少 3 次独立会话复测，记录失败分布。

    每次会话跑一遍 :class:`UserTaskRunner`，把六步状态 + 阻断原因存
    入 ``sessions``；``failure_distribution`` 统计各步骤跨会话的
    ``ok / blocked / skipped`` 计数（不以单站 / 单次成功声称达标）。
    """

    def __init__(self, min_sessions: int = 3) -> None:
        self.min_sessions = min_sessions
        self.sessions: list[dict] = []

    def run_session(self, steps_fn) -> dict:
        """跑一次会话：``steps_fn()`` 返回一个 :class:`UserTaskRunner`
        （已执行），取其 summary 存入。"""
        runner = steps_fn()
        summary = runner.summary()
        self.sessions.append(summary)
        return summary

    def failure_distribution(self) -> dict:
        """跨会话的失败分布：{step: {"ok": n, "blocked": n, "skipped": n}}。"""
        dist: dict = {}
        for summary in self.sessions:
            for step, status in summary.items():
                base = status.split("(", 1)[0]
                d = dist.setdefault(step, {"ok": 0, "blocked": 0,
                                            "skipped": 0})
                if base in d:
                    d[base] += 1
        return dist

    @property
    def sessions_ok(self) -> bool:
        """B10 步骤 5 — 至少 min_sessions 次会话且每次全闭环。"""
        if len(self.sessions) < self.min_sessions:
            return False
        for summary in self.sessions:
            if not all(v.startswith("ok") for v in summary.values()):
                return False
        return True

    def report(self) -> dict:
        """B10 步骤 5 — 可复核的完整证据（会话数 / 失败分布 / 达标）。"""
        return {
            "sessions": len(self.sessions),
            "min_sessions": self.min_sessions,
            "distribution": self.failure_distribution(),
            "all_sessions_complete": self.sessions_ok,
        }
