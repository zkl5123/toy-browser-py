"""R7-S04 第 5 点 — worker 超时后有界终止契约（纯逻辑层）。

审计 S04 第 5 点："worker 超时后由父进程有界终止并展示错误页，
允许关闭/新导航；日志只记录有限摘要"。本文件锁定**决策层**契约，
不做真实进程终止（那是 DocProcessServer / GUI 调用的事）：

* :class:`WorkerTaskRecord`：注入单调时钟值判定超时，不读系统时钟；
* :class:`WorkerTerminationState`：状态机 idle→terminating→terminated，
  join 预算用尽即 forced_kill，终止完成后 ``may_navigate`` 才放行
  新导航（防旧任务干扰新页）；
* 日志摘要截断到 128 字符（防超长注入）。
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import worker_timeout as wt
from toy_browser.app.worker_timeout import (
    WorkerTaskRecord, WorkerTerminationState, WorkerTaskTimedOut,
)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# -- WorkerTaskRecord --------------------------------------------------------

@_reg
def test_task_not_expired_within_budget():
    rec = WorkerTaskRecord(tab_id=1, navigation_id=1,
                           submitted_at=100.0, timeout=5.0)
    assert not rec.expired(104.0), "预算内不得判超时"
    assert not rec.expired(104.999), "预算内（未及点）不得判超时"


@_reg
def test_task_expired_at_boundary_and_beyond():
    rec = WorkerTaskRecord(tab_id=1, navigation_id=1,
                           submitted_at=100.0, timeout=5.0)
    # 单调时钟：now >= submitted + timeout 即过期（>= 而非 >）。
    # 本测试刻意把边界判定为 >=（保守：到点即终止，不多等）。
    assert rec.expired(105.0), "到点即视为过期（保守：>= 判定）"
    assert rec.expired(110.0)


@_reg
def test_check_raises_when_expired():
    rec = WorkerTaskRecord(tab_id=2, navigation_id=3,
                           submitted_at=0.0, timeout=1.0)
    try:
        rec.check(5.0)
        raise AssertionError("超时任务必须抛 WorkerTaskTimedOut")
    except WorkerTaskTimedOut:
        pass
    # 未超时不抛
    rec2 = WorkerTaskRecord(tab_id=2, navigation_id=3,
                            submitted_at=0.0, timeout=10.0)
    rec2.check(5.0)


@_reg
def test_disabled_timeout_never_expires():
    rec = WorkerTaskRecord(tab_id=1, navigation_id=1,
                           submitted_at=0.0, timeout=0.0)
    assert not rec.expired(9999.0), "timeout<=0 等价未启用"
    rec.check(9999.0)  # 不抛


# -- WorkerTerminationState ---------------------------------------------------

@_reg
def test_termination_state_machine():
    s = WorkerTerminationState()
    assert s.state == "idle"
    assert not s.may_navigate, "idle 状态不得导航（无终止发生）"

    s.begin_terminate(100.0, "task timed out")
    assert s.state == "terminating"
    assert not s.may_navigate, "terminating 中不得导航（防旧任务干扰）"

    s.complete(101.0, natural=True)
    assert s.state == "terminated"
    assert s.graceful
    assert s.may_navigate, "terminated 后允许新导航"


@_reg
def test_force_kill_after_join_budget():
    s = WorkerTerminationState(join_budget=2.0)
    s.begin_terminate(100.0)
    # join 预算内：不得 forced_kill
    assert not s.check_join_budget(101.0)
    # 预算恰好 2s（>= 判定）：可以且应该 forced_kill
    assert s.check_join_budget(102.0)
    # 超出预算：仍可以
    assert s.check_join_budget(110.0)
    # forced_kill 后完成（natural=False）
    s.complete(110.0, natural=False)
    assert not s.graceful
    assert s.may_navigate, "forced_kill 后同样允许新导航（有界，不无限等）"


@_reg
def test_idle_state_check_join_budget_is_false():
    s = WorkerTerminationState(join_budget=1.0)
    assert not s.check_join_budget(100.0), "idle 不得触发 forced_kill"


@_reg
def test_begin_terminate_is_idempotent_after_terminated():
    s = WorkerTerminationState()
    s.begin_terminate(100.0)
    s.complete(101.0)
    s.begin_terminate(200.0, "late retry")  # 已 terminated，忽略
    assert s.state == "terminated"
    assert s.started_at == 100.0, "late retry 不得覆盖原始开始时间"


@_reg
def test_summary_truncated_to_bounded_length():
    s = WorkerTerminationState()
    s.begin_terminate(0.0, "x" * 10000)
    assert len(s.summary) == 128, "摘要必须截断为有限长度"
    assert s.summary == "x" * 128


@_reg
def test_join_elapsed_is_zero_in_idle():
    s = WorkerTerminationState()
    assert s.join_elapsed(100.0) == 0.0


@_reg
def test_join_elapsed_is_nonnegative():
    s = WorkerTerminationState()
    s.begin_terminate(100.0)
    assert s.join_elapsed(90.0) == 0.0, "now 早于 started_at 不得为负"
    assert s.join_elapsed(105.0) == 5.0


# -- 静态锁定 ----------------------------------------------------------------

@_reg
def test_module_is_pure_logic_no_runtime_imports():
    src = inspect.getsource(wt)
    for banned in ("import subprocess", "os.kill", "import signal",
                   "import threading", "import multiprocessing"):
        assert banned not in src, f"worker_timeout 不得 import {banned}"
    # 不读系统单调时钟（判定值全由调用方注入）。
    assert "time.monotonic" not in src, "本模块不得读系统单调时钟"


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
