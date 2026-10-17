"""批次 23 — G07 文档工作单元标签级编排 回归。

被测：``toy_browser/app/tab_document_orchestrator.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.tab_document_orchestrator import (
    TabDocumentOrchestrator, TabDocumentUnit, UIScheduleTracker,
)
from toy_browser.app.document_controller import Result

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 标签创建
# ---------------------------------------------------------------------------

@_reg
def test_create_tab_returns_id():
    """create_tab：返回 tab_id、tab_count 增加、首个标签默认前台。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    assert t1 >= 1
    assert o.tab_count() == 1
    assert o.current_tab == t1


@_reg
def test_create_multiple_tabs():
    """多标签：tab_count 正确、current_tab 不变（不自动切）。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    t2 = o.create_tab()
    t3 = o.create_tab()
    assert o.tab_count() == 3
    assert o.current_tab == t1  # 仍是最初标签
    assert o.unit(t1) is not None
    assert o.unit(t2) is not None
    assert o.unit(t3) is not None


@_reg
def test_unit_has_controller_and_bridge():
    """TabDocumentUnit：持 controller + bridge + pending_results。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    u = o.unit(t1)
    assert u.controller is not None
    assert u.bridge is not None
    assert u.pending_results == []
    assert u.disposed is False


# ---------------------------------------------------------------------------
# G07 — 导航
# ---------------------------------------------------------------------------

@_reg
def test_navigate_returns_epoch():
    """navigate：返回 epoch（> 0）、切到目标标签前台。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    e = o.navigate(t1, "http://example.com")
    assert e >= 1
    assert o.current_tab == t1


@_reg
def test_navigate_unknown_tab_returns_neg():
    """navigate 未知 tab_id → 返回 -1。"""
    o = TabDocumentOrchestrator()
    o.create_tab()
    assert o.navigate(999, "http://x.com") == -1


@_reg
def test_navigate_switches_to_tab():
    """navigate 切到目标标签前台（非当前标签时）。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    t2 = o.create_tab()
    assert o.current_tab == t1
    o.navigate(t2, "http://b.com")
    assert o.current_tab == t2


# ---------------------------------------------------------------------------
# G09 step 2 — 切标签（只改前台呈现，不重建文档）
# ---------------------------------------------------------------------------

@_reg
def test_switch_tab_changes_current():
    """switch_tab：切到目标标签、current_tab 更新。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    t2 = o.create_tab()
    assert o.switch_tab(t2) is True
    assert o.current_tab == t2
    assert o.switch_tab(t1) is True
    assert o.current_tab == t1


@_reg
def test_switch_unknown_tab():
    """switch 未知 tab_id → False、current_tab 不变。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    assert o.switch_tab(999) is False
    assert o.current_tab == t1


@_reg
def test_switch_does_not_discard_results():
    """切标签不丢弃在途结果（G09 step 2 只改呈现）。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    t2 = o.create_tab()
    u1 = o.unit(t1)
    u1.pending_results.append(
        Result(ok=True, nav_epoch=1, document_id=1, frame_id=1))
    o.switch_tab(t2)
    # t1 的在途结果保留（切标签不丢）
    assert len(u1.pending_results) == 1


# ---------------------------------------------------------------------------
# G09 step 4 — 关标签（取消任务 + 丢弃在途结果 + 释放资源）
# ---------------------------------------------------------------------------

@_reg
def test_close_tab_discards_results():
    """close_tab：丢弃在途结果 + 丢弃 controller 命令队列。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    u1 = o.unit(t1)
    u1.pending_results.append(
        Result(ok=True, nav_epoch=1, document_id=1, frame_id=1))
    u1.pending_results.append(
        Result(ok=True, nav_epoch=2, document_id=2, frame_id=1))
    res = o.close_tab(t1)
    assert res["discarded_results"] == 2
    assert o.unit(t1) is None
    assert o.tab_count() == 0


@_reg
def test_close_tab_cancels_tasks():
    """close_tab：经 lifecycle 取消在途任务。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    # 给 lifecycle 门控加在途任务
    gate_task = o.lifecycle.gate.get(t1)
    gate_task.track_task(42)
    gate_task.track_task(43)
    res = o.close_tab(t1)
    assert 42 in res["cancelled_tasks"]
    assert 43 in res["cancelled_tasks"]


@_reg
def test_close_active_tab_reassigns_active():
    """关闭前台标签：前台让给剩余标签（G09 step 4）。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    t2 = o.create_tab()
    o.switch_tab(t2)
    assert o.current_tab == t2
    o.close_tab(t2)
    # 关 t2 后前台让给 t1
    assert o.current_tab == t1
    assert o.tab_count() == 1


@_reg
def test_close_unknown_tab():
    """close 未知 tab_id：discarded=0、不抛异常。"""
    o = TabDocumentOrchestrator()
    o.create_tab()
    res = o.close_tab(999)
    assert res["discarded_results"] == 0


@_reg
def test_close_all_tabs_active_none():
    """关全部标签：current_tab=None。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    o.close_tab(t1)
    assert o.current_tab is None
    assert o.tab_count() == 0


# ---------------------------------------------------------------------------
# G07 step 6 — 结果应用（C01 版本门控）
# ---------------------------------------------------------------------------

@_reg
def test_apply_results_empty():
    """apply_results：无在途结果 → 返回空列表。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    assert o.apply_results(t1) == []


@_reg
def test_apply_results_c1_gate():
    """apply_results：经 C01 版本门控，过期 epoch 丢弃。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    u1 = o.unit(t1)
    # 加一个旧 epoch 结果（nav_epoch=0 早于当前 epoch=1）
    u1.pending_results.append(
        Result(ok=True, nav_epoch=0, document_id=1, frame_id=1))
    applied = o.apply_results(t1)
    # 旧 epoch 结果被丢弃（C01 门控）
    assert all(r.nav_epoch != 0 for r in applied) or applied == []


@_reg
def test_apply_unknown_tab():
    """apply 未知 tab_id → 返回空。"""
    o = TabDocumentOrchestrator()
    assert o.apply_results(999) == []


# ---------------------------------------------------------------------------
# G07 step 6 — UI 调度追踪
# ---------------------------------------------------------------------------

@_reg
def test_ui_p95_empty():
    """无样本 → p95=None。"""
    o = TabDocumentOrchestrator()
    assert o.ui_p95_ms() is None


@_reg
def test_ui_p95_recorded():
    """navigate / apply 记录 UI 调度打点 → p95 非 None。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    o.navigate(t1, "http://x.com")
    o.apply_results(t1)
    p95 = o.ui_p95_ms()
    assert p95 is not None
    assert p95 >= 0


@_reg
def test_ui_tracker_p95_value():
    """UIScheduleTracker：p95 计算正确。"""
    tr = UIScheduleTracker()
    for i in range(100):
        tr.record(float(i))  # 0..99
    p95 = tr.p95_ms()
    assert p95 == 94.0 or p95 == 95.0  # p95 of 0..99
    assert len(tr) == 100


@_reg
def test_ui_tracker_max_samples():
    """UIScheduleTracker：超 max_samples 只保留最新。"""
    tr = UIScheduleTracker(max_samples=10)
    for i in range(20):
        tr.record(float(i))
    assert len(tr) == 10
    # 保留最新 10 个（10..19）
    assert tr.samples[0] == 10.0
    tr.clear()
    assert len(tr) == 0


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------

@_reg
def test_describe_known_tab():
    """describe：已知 tab → 含 epoch / nav / pending / queue。"""
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    o.navigate(t1, "http://x.com")
    s = o.describe(t1)
    assert "tab" in s
    assert "epoch=" in s
    assert "pending=" in s


@_reg
def test_describe_unknown_tab():
    """describe 未知 tab → 'no unit'。"""
    o = TabDocumentOrchestrator()
    s = o.describe(999)
    assert "no unit" in s


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
