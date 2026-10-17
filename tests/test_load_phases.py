"""B10 — 消除加载阻塞：阶段状态机 + 取消 + 用户任务级回归（纯逻辑核心）回归。

依据 ``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B10。被测：``toy_browser/app/load_phases.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.load_phases import (
    PhaseTracker, PhaseBudget, UserTaskRunner, TaskRepro, TaskBlocked,
    PH_CONNECT, PH_DOWNLOAD, PH_PARSE, PH_EXECUTE, PH_LAYOUT, PH_PRESENT,
    PH_DONE, PH_RUNNING, PH_CANCELLED, PH_BLOCKED,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# B10 步骤 1：每次导航携带版本号，旧结果不得覆盖新页面
# ---------------------------------------------------------------------------

@_reg
def test_b10_nav_version_stale_drop():
    """B10 步骤 1 — 旧导航版本的结果被丢弃。"""
    t = PhaseTracker(nav_version=1)
    assert t.drop_stale(1) is False, "current version not stale"
    t.new_navigation()
    assert t.nav_version == 2
    assert t.drop_stale(1) is True, "old version stale after new nav"
    # 旧版本的阶段结果不得应用。
    assert t.accept_phase(PH_PRESENT, result_version=1) is False


@_reg
def test_b10_phase_order_enforced():
    """B10 步骤 2 — 阶段按 连接→下载→解析→执行→布局→绘制 顺序推进。"""
    t = PhaseTracker()
    # 跳过 connect 直接 start download → 阻断（前置未完成）。
    assert t.start_phase(PH_DOWNLOAD) is False
    assert t.phases[PH_DOWNLOAD] == PH_BLOCKED
    assert t.blocked_reason == "phase-connect-incomplete"
    # 按序推进。
    assert t.start_phase(PH_CONNECT) is True
    assert t.finish_phase(PH_CONNECT) is True
    assert t.start_phase(PH_DOWNLOAD) is True
    assert t.finish_phase(PH_DOWNLOAD) is True
    assert t.phases[PH_CONNECT] == PH_DONE and t.phases[PH_DOWNLOAD] == PH_DONE


@_reg
def test_b10_full_pipeline_complete():
    """B10 步骤 2 — 六阶段全完成 → all_done + progress=1.0。"""
    t = PhaseTracker()
    for ph in (PH_CONNECT, PH_DOWNLOAD, PH_PARSE, PH_EXECUTE, PH_LAYOUT, PH_PRESENT):
        assert t.start_phase(ph) is True
        t.finish_phase(ph)
    assert t.all_done is True
    assert t.progress == 1.0
    assert t.status_label() == "complete"


# ---------------------------------------------------------------------------
# B10 步骤 2：取消入口（取消 / 切标签后旧结果不覆盖新页面）
# ---------------------------------------------------------------------------

@_reg
def test_b10_cancel_running_phase():
    """B10 步骤 2 — 取消把 running 阶段翻 cancelled，后续结果拒绝。"""
    t = PhaseTracker()
    t.start_phase(PH_CONNECT)
    t.start_phase.__self__  # just confirm
    t.cancel()
    assert t.is_cancelled is True
    assert t.phases[PH_CONNECT] == PH_CANCELLED
    # 取消后旧结果不得应用。
    assert t.accept_phase(PH_CONNECT, result_version=t.nav_version) is False


@_reg
def test_b10_budget_exhaustion_blocks_phase():
    """B10 步骤 2 — 预算耗尽时阶段被阻断（budget-exhausted 提示）。"""
    budget = PhaseBudget(max_requests=1)
    t = PhaseTracker(nav_version=1, budget=budget)
    budget.record_request()   # 用掉 1 个请求
    budget.record_request()   # 超过预算
    assert budget.exhausted() is True
    assert budget.exhaust_reason() == "requests"
    assert t.start_phase(PH_CONNECT) is False
    assert t.status_label().startswith("budget-exhausted")


# ---------------------------------------------------------------------------
# B10 步骤 4：用户任务"首页可用 → 输入 → 搜索 → 结果可读 → 跳转 → 返回"
# ---------------------------------------------------------------------------

@_reg
def test_b10_task_plan_six_steps():
    """B10 步骤 4 — 固定六步用户任务。"""
    runner = UserTaskRunner()
    assert runner.plan() == ["home-usable", "input", "search",
                             "results-readable", "navigate-result", "back"]


@_reg
def test_b10_task_full_run_ok():
    """B10 步骤 4/5 — 一次完整闭环（六步全 ok）。"""
    runner = UserTaskRunner()
    for step in runner.plan():
        runner.run_step(step, lambda sink: sink.update({"url": "https://x.com/"}))
    assert runner.completed is True
    assert runner.blocked_steps() == []


@_reg
def test_b10_task_blocked_step_marks_blocker():
    """B10 步骤 4 — 被前置问题阻断的步骤明确标 blocked + 原因。"""
    runner = UserTaskRunner()
    runner.run_step("home-usable", lambda sink: None)
    # input 步骤因 JS 多变量声明缺口被阻断（B04 已修，这里模拟仍阻断）。
    def block_input(sink):
        raise TaskBlocked("js-multi-decl-not-fixed")
    st = runner.run_step("input", block_input)
    assert st.status == "blocked"
    assert st.blocker == "js-multi-decl-not-fixed"
    assert "input" in runner.blocked_steps()


@_reg
def test_b10_task_blocked_cascades_skip():
    """B10 步骤 4 — 前置步骤 blocked 时后续步骤级联 skipped。"""
    runner = UserTaskRunner()
    runner.run_step("home-usable", lambda sink: None)

    def block_search(sink):
        raise TaskBlocked("no-search-box")
    runner.run_step("input", lambda sink: None)
    runner.run_step("search", block_search)
    # results-readable 前置被阻断 → skipped。
    st = runner.run_step("results-readable", lambda sink: None)
    assert st.status == "skipped", f"should be skipped: {st.status}"
    assert st.blocker.startswith("previous-step-search")


@_reg
def test_b10_task_summary_failure_distribution():
    """B10 步骤 5 — summary 记录各步骤状态 + 阻断原因（失败分布）。"""
    runner = UserTaskRunner()
    runner.run_step("home-usable", lambda sink: None)

    def block_input(sink):
        raise TaskBlocked("js-error")
    runner.run_step("input", block_input)
    # 后续步骤因前置阻断级联 skipped（显式触发才记录）。
    runner.run_step("search", lambda sink: None)
    runner.run_step("results-readable", lambda sink: None)
    s = runner.summary()
    assert s["home-usable"].startswith("ok")
    assert "blocked" in s["input"] and "js-error" in s["input"]
    assert "skipped" in s["search"]
    assert "skipped" in s["results-readable"]


# ---------------------------------------------------------------------------
# B10 步骤 5：至少 3 次独立会话复测 + 失败分布
# ---------------------------------------------------------------------------

@_reg
def test_b10_repro_min_three_sessions():
    """B10 步骤 5 — 至少 3 次独立会话复测。"""
    repro = TaskRepro(min_sessions=3)
    for i in range(3):
        def mk():
            r = UserTaskRunner()
            for step in r.plan():
                r.run_step(step, lambda sink: sink.update({"i": i}))
            return r
        repro.run_session(mk)
    assert repro.sessions_ok is True
    assert len(repro.sessions) == 3


@_reg
def test_b10_repro_failure_distribution_counts():
    """B10 步骤 5 — 跨会话失败分布统计（不以单站 / 单次成功声称达标）。"""
    repro = TaskRepro(min_sessions=3)

    def session_all_ok():
        r = UserTaskRunner()
        for step in r.plan():
            r.run_step(step, lambda sink: None)
        return r

    def session_blocked_input():
        r = UserTaskRunner()
        r.run_step("home-usable", lambda sink: None)

        def bl(sink):
            raise TaskBlocked("input-blocked")
        r.run_step("input", bl)
        for step in ("search", "results-readable", "navigate-result", "back"):
            r.run_step(step, lambda sink: None)
        return r

    repro.run_session(session_all_ok)
    repro.run_session(session_all_ok)
    repro.run_session(session_blocked_input)
    dist = repro.failure_distribution()
    assert dist["home-usable"]["ok"] == 3
    assert dist["input"]["ok"] == 2 and dist["input"]["blocked"] == 1
    assert repro.sessions_ok is False, "one session blocked → not all complete"


@_reg
def test_b10_repro_report_complete():
    """B10 步骤 5 — 可复核的完整证据（会话数 / 分布 / 达标）。"""
    repro = TaskRepro(min_sessions=2)
    for _ in range(2):
        repro.run_session(lambda: _all_ok_runner())
    rep = repro.report()
    assert rep["sessions"] == 2
    assert rep["all_sessions_complete"] is True
    assert rep["min_sessions"] == 2


def _all_ok_runner():
    r = UserTaskRunner()
    for step in r.plan():
        r.run_step(step, lambda sink: None)
    return r


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
