"""R7-S04 第 5 点接线 — DocProcessServer 暴露终止状态机。

锁定 DocProcessServer 的公开行为：
* ``termination_state`` 属性返回状态机；
* ``shutdown()`` 把状态机推到 terminated（回退态 graceful=True）；
* 终止前 ``may_navigate()`` 为 False，终止后为 True；
* 静态锁：document_process.py 顶部 import 了 worker_timeout，
  没有 subprocess/signal 运行时依赖。
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import document_process as dp
from toy_browser.app.worker_timeout import WorkerTerminationState

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@_reg
def test_initial_state_is_idle():
    s = dp.DocProcessServer()
    assert isinstance(s.termination_state, WorkerTerminationState)
    assert s.termination_state.state == "idle"
    assert not s.may_navigate()


@_reg
def test_shutdown_marks_terminated_graceful():
    s = dp.DocProcessServer()
    s.shutdown()
    assert s.termination_state.state == "terminated"
    assert s.termination_state.graceful, "回退态 shutdown 无真实子进程，graceful"
    assert s.may_navigate()


@_reg
def test_shutdown_idempotent_state_machine():
    s = dp.DocProcessServer()
    s.shutdown()
    state_after_first = s.termination_state.state
    s.shutdown()  # 二次 shutdown 不得破坏状态机
    assert s.termination_state.state == state_after_first == "terminated"


@_reg
def test_begin_terminate_and_complete_via_api():
    import time
    s = dp.DocProcessServer()
    now = time.monotonic()
    s.begin_terminate(now, "task timed out")
    assert s.termination_state.state == "terminating"
    assert not s.may_navigate()
    # join 预算内完成
    s.complete_termination(now + 0.5, natural=True)
    assert s.termination_state.state == "terminated"
    assert s.may_navigate()


@_reg
def test_should_force_kill_checks_join_budget():
    import time
    s = dp.DocProcessServer()
    now = time.monotonic()
    s.begin_terminate(now)
    assert not s.should_force_kill(now + 0.5), "预算内不得 forced_kill"
    s.complete_termination(now + 10.0, natural=False)
    # terminated 后 should_force_kill 永远 False
    assert not s.should_force_kill(now + 20.0)


@_reg
def test_module_does_not_import_runtime_primitives():
    src = inspect.getsource(dp)
    # document_process.py 允许用 multiprocessing 作为可选后端，
    # 但不得在 worker_timeout 契约层面 import subprocess/signal。
    # worker_timeout.py 本身已锁定，这里只确认没有新增运行时依赖。
    assert "subprocess" not in src, "document_process.py 不得 import subprocess"
    assert "import signal" not in src, "document_process.py 不得 import signal"


@_reg
def test_worker_timeout_no_system_clock():
    from toy_browser.app import worker_timeout as wt
    src = inspect.getsource(wt)
    assert "time.monotonic" not in src, \
        "worker_timeout 契约层不得读系统单调时钟"
    assert "time.time" not in src, \
        "worker_timeout 契约层不得读系统墙钟"


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
