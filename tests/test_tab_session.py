"""批次 25 — G09 step 1 统一标签会话协议层 回归。

被测：``toy_browser/app/tab_session.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.tab_session import TabSession, TabSessionStats
from toy_browser.app.tab_identity import TabSnapshot

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 开标签
# ---------------------------------------------------------------------------

@_reg
def test_open_tab_allocates():
    """open_tab：分配稳定 tab_id、tab_count 增加、首个标签前台。"""
    s = TabSession()
    t1 = s.open_tab()
    assert t1 == 1
    assert s.tab_count == 1
    assert s.active_tab_id == 1


@_reg
def test_open_three_tabs():
    """至少三标签（G09 验收"至少三标签"）。"""
    s = TabSession()
    ids = [s.open_tab() for _ in range(3)]
    assert s.tab_count == 3
    assert len(set(ids)) == 3  # tab_id 稳定唯一
    assert s.tab_ids == ids


@_reg
def test_tab_ids_by_idx():
    """tab_ids 按 idx 顺序（稳定身份）。"""
    s = TabSession()
    t1, t2, t3 = s.open_tab(), s.open_tab(), s.open_tab()
    assert s.tab_ids == [t1, t2, t3]


# ---------------------------------------------------------------------------
# 关闭首 / 中 / 末标签（G09 验收）
# ---------------------------------------------------------------------------

@_reg
def test_close_middle_tab_rebuilds_mapping():
    """关中间标签：compact_mapping 重建旧 idx→新 idx、前台让给剩余标签。"""
    s = TabSession()
    t1, t2, t3 = s.open_tab(), s.open_tab(), s.open_tab()
    res = s.close_tab(t2)  # 关中间
    # t2 在 idx 1，关闭后 t3 前移到 idx 1
    assert res["new_active"] == t1  # 前台让给 t1（首个）
    assert t2 in s.closed_tab_ids
    # compact 映射：旧 idx 0→0（t1 不动），旧 idx 2→1（t3 前移）
    assert res["compact_mapping"].get(0) == 0
    assert res["compact_mapping"].get(2) == 1
    assert s.tab_ids == [t1, t3]


@_reg
def test_close_first_tab():
    """关首标签：剩余标签前移、compact 映射正确。"""
    s = TabSession()
    t1, t2, t3 = s.open_tab(), s.open_tab(), s.open_tab()
    res = s.close_tab(t1)  # 关 idx 0
    assert res["new_active"] == t2  # 前台让给剩余首个（t2）
    assert s.tab_ids == [t2, t3]
    # compact：旧 idx 1→0（t2），旧 idx 2→1（t3）
    assert res["compact_mapping"].get(1) == 0
    assert res["compact_mapping"].get(2) == 1


@_reg
def test_close_last_tab():
    """关末标签：剩余标签不动、compact 映射旧 idx 不变。"""
    s = TabSession()
    t1, t2, t3 = s.open_tab(), s.open_tab(), s.open_tab()
    res = s.close_tab(t3)  # 关 idx 2
    assert res["new_active"] == t1  # 前台仍 t1
    assert s.tab_ids == [t1, t2]
    assert res["compact_mapping"].get(0) == 0
    assert res["compact_mapping"].get(1) == 1


@_reg
def test_close_unknown_tab():
    """关未知 tab_id：无 cancelled、tab_count 不变。"""
    s = TabSession()
    s.open_tab()
    res = s.close_tab(999)
    assert res["cancelled"] == []
    assert s.tab_count == 1


@_reg
def test_close_all_tabs_active_none():
    """关全部标签：active=None、tab_count=0。"""
    s = TabSession()
    t1, t2 = s.open_tab(), s.open_tab()
    s.close_tab(t1)
    res = s.close_tab(t2)
    assert res["new_active"] is None
    assert s.tab_count == 0
    assert s.active_tab_id is None


@_reg
def test_close_cancels_tasks_no_leak():
    """关标签取消在途任务（G09 验收"不泄漏已关闭标签任务"）。"""
    s = TabSession()
    t1, t2 = s.open_tab(), s.open_tab()
    # 给 t2 门控加在途任务
    gate_task = s.lifecycle.gate.get(t2)
    gate_task.track_task(100)
    gate_task.track_task(101)
    res = s.close_tab(t2)
    assert 100 in res["cancelled"]
    assert 101 in res["cancelled"]
    # t1 的在途不受影响
    assert 100 not in s.closed_tab_ids  # closed 记 tab_id 非任务


# ---------------------------------------------------------------------------
# 切换标签（G09 step 2 只改呈现，不重建文档）
# ---------------------------------------------------------------------------

@_reg
def test_switch_tab():
    """switch：切到目标标签前台、active_tab_id 更新。"""
    s = TabSession()
    t1, t2 = s.open_tab(), s.open_tab()
    assert s.switch(t2) is True
    assert s.active_tab_id == t2
    assert s.switch(t1) is True
    assert s.active_tab_id == t1


@_reg
def test_switch_unknown_tab():
    """switch 未知 tab_id → False、active 不变。"""
    s = TabSession()
    t1 = s.open_tab()
    assert s.switch(999) is False
    assert s.active_tab_id == t1


# ---------------------------------------------------------------------------
# 旧回调防护（G09 step 1 验收"旧回调不能操作其他标签"）
# ---------------------------------------------------------------------------

@_reg
def test_resolve_callback_valid_idx():
    """resolve_callback：旧 idx 仍映射存活 tab_id → 返回该 tab_id。"""
    s = TabSession()
    t1, t2, t3 = s.open_tab(), s.open_tab(), s.open_tab()
    # idx 0 → t1（存活）
    assert s.resolve_callback(0) == t1
    assert s.resolve_callback(1) == t2
    assert s.resolve_callback(2) == t3
    assert s.stale_callback_count == 0  # 未被拦截


@_reg
def test_resolve_callback_stale_after_close():
    """关中间标签后，旧 idx 回调若错位 → 拦截（no-op + 计数）。"""
    s = TabSession()
    t1, t2, t3 = s.open_tab(), s.open_tab(), s.open_tab()
    s.close_tab(t2)  # 关 idx 1；t3 前移到 idx 1
    # 旧 idx 2（原 t3 的槽位）现已越界 → 拦截
    assert s.resolve_callback(2) is None
    assert s.stale_callback_count == 1
    # 旧 idx 1（原 t2 已关）→ 现映射 t3（前移）→ 返回 t3（安全）
    got = s.resolve_callback(1)
    assert got == t3


@_reg
def test_stale_callback_count_increments():
    """多次无效旧回调 → 计数累加（诊断区可展示"拦截了多少旧回调"）。"""
    s = TabSession()
    s.open_tab()
    s.close_tab(s.open_tab())
    base = s.stale_callback_count
    for i in range(3):
        s.resolve_callback(99 + i)  # 越界 idx → 全拦截
    assert s.stale_callback_count == base + 3


@_reg
def test_resolve_callback_no_tab():
    """无任何标签时旧回调 → 拦截。"""
    s = TabSession()
    assert s.resolve_callback(0) is None
    assert s.stale_callback_count == 1


# ---------------------------------------------------------------------------
# 会话快照 / 恢复（G09 step 5：仅保存可恢复状态）
# ---------------------------------------------------------------------------

@_reg
def test_snapshot_restore():
    """snapshot → restore：url / active / 标签数保持。"""
    s = TabSession()
    t1 = s.open_tab()
    t2 = s.open_tab()
    s.registry.set_resource(t1, "url", "http://a.com")
    s.registry.set_resource(t2, "url", "http://b.com")
    s.switch(t2)
    snaps = s.snapshot()
    assert len(snaps) == 2
    s2 = TabSession.restore(snaps)
    assert s2.tab_count == 2
    # 恢复后 active 标签应是 t2 的等价（url=b.com）
    active = s2.active_tab_id
    assert s2.registry.get_resource(active, "url") == "http://b.com"


@_reg
def test_snapshot_restorable_fields():
    """TabSnapshot 携带可恢复状态（url / title / back / fwd / scroll /
    active；不含 DOM / 位图 / 脚本 Host——G09 step 5）。"""
    s = TabSession()
    t1 = s.open_tab()
    s.registry.set_resource(t1, "url", "http://x.com")
    s.registry.set_resource(t1, "title", "X")
    s.registry.set_resource(t1, "back_stack", ["http://0", "http://1"])
    s.registry.set_resource(t1, "fwd_stack", [])
    s.registry.set_resource(t1, "scroll", 120)
    snaps = s.snapshot()
    sn = snaps[0]
    assert isinstance(sn, TabSnapshot)
    assert sn.url == "http://x.com"
    assert sn.title == "X"
    assert sn.back_stack == ["http://0", "http://1"]
    assert sn.fwd_stack == []
    assert sn.scroll == 120
    assert sn.active is True


# ---------------------------------------------------------------------------
# 审计 / 诊断
# ---------------------------------------------------------------------------

@_reg
def test_stats():
    """stats：tab_count / active / closed / stale_callbacks 完整。"""
    s = TabSession()
    t1, t2 = s.open_tab(), s.open_tab()
    s.close_tab(t2)
    st = s.stats()
    assert isinstance(st, TabSessionStats)
    assert st.tab_count == 1
    assert st.active_tab_id == t1
    assert t2 in st.closed_tab_ids
    assert st.stale_callbacks >= 0


@_reg
def test_describe():
    """describe：诊断串含关键位。"""
    s = TabSession()
    s.open_tab()
    d = s.describe()
    assert "tabs=1" in d
    assert "closed=0" in d


@_reg
def test_closed_tab_ids_audit():
    """closed_tab_ids 记录所有被关闭的 tab_id（审计"不泄漏已关闭标签"）。"""
    s = TabSession()
    ids = [s.open_tab() for _ in range(3)]
    s.close_tab(ids[1])
    assert s.closed_tab_ids == [ids[1]]
    s.close_tab(ids[0])
    assert s.closed_tab_ids == [ids[1], ids[0]]


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
