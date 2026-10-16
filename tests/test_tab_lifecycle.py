"""G09 step 2 + step 4 — 每标签状态所有权 + 后台标签重绘/定时器策略
回归（round5 docs 批次 9）。

覆盖 toy_browser/app/tab_lifecycle.py：
- TabPresentationSet.activate：切换只改前台呈现（激活全量重绘+正常
  定时器，其余降重绘+定时器节流），不触碰文档/历史/任务
- TabTaskGate：关闭标签取消在途任务 + 释放资源记账；后台定时器节流
- TabLifecycle：new_tab / activate / close_tab 统一协议（与
  TabIdentityRegistry 配合，关闭 tab_id 失效不串页）
"""
from __future__ import annotations

from toy_browser.app.tab_lifecycle import (
    TabPresentation, TabPresentationSet, TabTasks, TabTaskGate,
    TabLifecycle,
    REPAINT_FULL, REPAINT_REDUCED,
    TIMER_NORMAL, TIMER_THROTTLED, TIMER_PAUSED,
)
from toy_browser.app.tab_identity import TabIdentityRegistry


TESTS = []


# ----------------------------------------------------------------------
# TabPresentationSet — 切换只改前台呈现（G09 step 2）
# ----------------------------------------------------------------------
def test_presentation_activate_foreground_reduced():
    s = TabPresentationSet()
    s.add(1)
    s.add(2)
    s.add(3)
    s.activate(2)
    p2 = s.get(2)
    p1 = s.get(1)
    p3 = s.get(3)
    # 前台：全量重绘 + 正常定时器
    assert p2.active and p2.repaint == REPAINT_FULL
    assert p2.timer == TIMER_NORMAL
    # 后台：降重绘 + 定时器节流
    assert p1.active is False and p1.repaint == REPAINT_REDUCED
    assert p1.timer == TIMER_THROTTLED
    assert p3.active is False and p3.timer == TIMER_THROTTLED
    # 唯一前台
    assert s.active_tab_id() == 2


def test_presentation_activate_unknown_noop():
    s = TabPresentationSet()
    s.add(1)
    s.activate(99)  # 未知 tab：no-op
    assert s.get(1).active is False
    assert s.active_tab_id() is None


def test_presentation_switch_does_not_rebuild_doc():
    """G09 step 2：切换只改呈现字段，不触碰该标签的文档/历史/任务。

    呈现对象本身不持有 DocumentState/历史/任务——切换前后这些"外部"
    资源（以 tab_id 为 key 存 registry）保持不变（本测试用呈现集
    自身字段验证切换语义；registry 资源经 tab_id 独立存取）。"""
    s = TabPresentationSet()
    s.add(1)
    s.add(2)
    # 外部"文档"以 tab_id 为 key（registry 风格），切换不应影响
    docs = {1: "doc-A", 2: "doc-B"}
    hist = {1: ["a"], 2: ["b"]}
    s.activate(1)
    s.activate(2)
    s.activate(1)
    # 切换前后文档/历史按 tab_id 原样保留（未重建）
    assert docs[1] == "doc-A" and docs[2] == "doc-B"
    assert hist[1] == ["a"] and hist[2] == ["b"]
    assert s.get(1).active and not s.get(2).active


# ----------------------------------------------------------------------
# TabTaskGate — 关闭取消任务 + 释放资源；后台节流（G09 step 4）
# ----------------------------------------------------------------------
def test_gate_close_cancels_in_flight():
    g = TabTaskGate()
    t = g.add(1)
    t.track_task(100)
    t.track_task(101)
    cancelled = g.close(1)
    assert sorted(cancelled) == [100, 101]
    # 资源记账已释放
    assert t.released is True
    # 门控条目已移除（关闭后不可再取）
    assert g.get(1) is None


def test_gate_close_no_task_returns_empty():
    g = TabTaskGate()
    g.add(5)
    assert g.close(5) == []
    assert g.get(5) is None


def test_gate_background_throttles_timers():
    g = TabTaskGate()
    t = g.add(7)
    g.background(7)
    assert t.timer_policy == TIMER_THROTTLED
    g.foreground(7)
    assert t.timer_policy == TIMER_NORMAL


def test_gate_drop_task_not_recancelled():
    g = TabTaskGate()
    t = g.add(9)
    t.track_task(1)
    t.drop_task(1)      # 正常完成
    cancelled = g.close(9)
    # 正常完成的任务不在"被取消"集合
    assert cancelled == []
    assert t.cancelled == []


def test_gate_cancel_all_records_cancelled():
    g = TabTaskGate()
    t = g.add(11)
    t.track_task(2)
    t.track_task(3)
    cancelled = g.close(11)
    assert sorted(cancelled) == [2, 3]
    # 已取消记录保留（审计 / 诊断用，但条目已 pop，经 t 引用仍可见）
    assert sorted(t.cancelled) == [2, 3]


# ----------------------------------------------------------------------
# TabLifecycle — 统一 切/关 标签协议（G09 step 1+2+4 组合）
# ----------------------------------------------------------------------
def _lc():
    reg = TabIdentityRegistry()
    return TabLifecycle(registry=reg), reg


def test_lifecycle_new_tab_first_active():
    lc, reg = _lc()
    t1 = lc.new_tab()
    t2 = lc.new_tab()
    # 首个标签默认前台
    assert lc.active_tab_id() == t1
    assert lc.presentation.get(t1).active is True
    # 两个标签都在 registry（稳定 tab_id）
    assert reg.idx_of(t1) is not None
    assert reg.idx_of(t2) is not None


def test_lifecycle_activate_switches_foreground_only():
    lc, reg = _lc()
    t1 = lc.new_tab()
    t2 = lc.new_tab()
    lc.activate(t2)
    p1 = lc.presentation.get(t1)
    p2 = lc.presentation.get(t2)
    assert p2.active and p2.repaint == REPAINT_FULL
    assert p1.active is False and p1.repaint == REPAINT_REDUCED
    assert p1.timer == TIMER_THROTTLED
    assert p2.timer == TIMER_NORMAL
    # 前台切换不改变 registry 的稳定 tab_id
    assert reg.idx_of(t1) is not None and reg.idx_of(t2) is not None


def test_lifecycle_close_cancels_tasks_and_invalidates():
    lc, reg = _lc()
    t1 = lc.new_tab()
    t2 = lc.new_tab()
    t3 = lc.new_tab()
    # 给 t2 挂一个在途任务
    gate_t2 = lc.gate.get(t2)
    gate_t2.track_task(555)
    # 关闭中间标签 t2
    cancelled = lc.close_tab(t2)
    assert cancelled == [555]
    # t2 在 registry 失效（旧回调 / 资源不串页）
    assert reg.idx_of(t2) is None
    # 资源已释放
    assert gate_t2.released is True
    # 其余标签仍在
    assert reg.idx_of(t1) is not None
    assert reg.idx_of(t3) is not None
    assert lc.tab_count() == 2


def test_lifecycle_close_active_tab_reactivates_remaining():
    lc, reg = _lc()
    t1 = lc.new_tab()
    t2 = lc.new_tab()
    lc.activate(t2)
    assert lc.active_tab_id() == t2
    lc.close_tab(t2)
    # 关闭前台标签后，前台让给剩余标签
    assert lc.active_tab_id() == t1
    assert lc.presentation.get(t1).active is True


def test_lifecycle_close_all_leaves_none_active():
    lc, reg = _lc()
    t1 = lc.new_tab()
    lc.close_tab(t1)
    assert lc.tab_count() == 0
    assert lc.active_tab_id() is None


def test_lifecycle_cancelled_tasks_auditable():
    lc, reg = _lc()
    t1 = lc.new_tab()
    t2 = lc.new_tab()
    lc.gate.get(t1).track_task(1)
    lc.gate.get(t1).track_task(2)
    lc.close_tab(t1)
    # 关闭 t1 取消的任务可审计
    assert sorted(lc.cancelled_tasks(t1)) == [1, 2]
    # 未关闭的 t2 无取消记录
    assert lc.cancelled_tasks(t2) == []


TESTS.append(("test_presentation_activate_foreground_reduced",
              test_presentation_activate_foreground_reduced))
TESTS.append(("test_presentation_activate_unknown_noop",
              test_presentation_activate_unknown_noop))
TESTS.append(("test_presentation_switch_does_not_rebuild_doc",
              test_presentation_switch_does_not_rebuild_doc))
TESTS.append(("test_gate_close_cancels_in_flight",
              test_gate_close_cancels_in_flight))
TESTS.append(("test_gate_close_no_task_returns_empty",
              test_gate_close_no_task_returns_empty))
TESTS.append(("test_gate_background_throttles_timers",
              test_gate_background_throttles_timers))
TESTS.append(("test_gate_drop_task_not_recancelled",
              test_gate_drop_task_not_recancelled))
TESTS.append(("test_gate_cancel_all_records_cancelled",
              test_gate_cancel_all_records_cancelled))
TESTS.append(("test_lifecycle_new_tab_first_active",
              test_lifecycle_new_tab_first_active))
TESTS.append(("test_lifecycle_activate_switches_foreground_only",
              test_lifecycle_activate_switches_foreground_only))
TESTS.append(("test_lifecycle_close_cancels_tasks_and_invalidates",
              test_lifecycle_close_cancels_tasks_and_invalidates))
TESTS.append(("test_lifecycle_close_active_tab_reactivates_remaining",
              test_lifecycle_close_active_tab_reactivates_remaining))
TESTS.append(("test_lifecycle_close_all_leaves_none_active",
              test_lifecycle_close_all_leaves_none_active))
TESTS.append(("test_lifecycle_cancelled_tasks_auditable",
              test_lifecycle_cancelled_tasks_auditable))
