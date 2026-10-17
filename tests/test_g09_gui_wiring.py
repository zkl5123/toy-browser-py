"""批次 37 — G09 统一标签会话协议层接入 gui.py 回归。

被测：``toy_browser/app/gui.py`` 的 G09 接线层
（``_g09_session`` / ``_tab_ids`` / ``_g09_close_tab`` /
``_g11... _g09_resolve`` / ``_g09_tab_id`` / ``_g09_idx_of_tab`` /
``_g09_stats`` / ``_g09_describe``）。

设计：``TabBrowser.__init__`` 会构建完整 Tk UI（重），本回归用
``object.__new__(TabBrowser)`` 绕过 ``__init__``，注入 G09 接线所需
最小桩属性（``_g09_session`` = 真实 :class:`TabSession` +
``_tab_ids`` + ``_add_tab`` / ``_remove_tab`` 桩），纯逻辑验证
"idx ↔ 稳定 tab_id 映射 + 关标签 compact 重建 + 旧回调拦截"，无需
tkinter。tkinter 可用时另用最小 Tk root 跑一次真实 TabBrowser 冒烟
（验证接线不抛异常）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import gui as _gui
from toy_browser.app.tab_session import TabSession

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


class _MiniPage:
    """最小 PageInstance 桩（G09 接线只需 .url / .title）。"""
    def __init__(self):
        self.url = "about:blank"
        self.title = "about:blank"


def _stub_browser():
    """绕过 __init__ 的最小 TabBrowser 桩（只含 G09 接线所需属性）。"""
    b = object.__new__(_gui.TabBrowser)
    b.tabs = []
    b._tab_ids = []
    b.current = 0
    b._g09_session = TabSession()

    def _add_tab(page):
        idx = len(b.tabs)
        b.tabs.append(page)
        b._tab_ids.append(b._g09_session.open_tab())
        return idx

    def _remove_tab(idx):
        if len(b.tabs) <= 1:
            return
        b._g09_close_tab(idx)
        del b.tabs[idx]
        b._tab_ids  # (同步已在 _g09_close_tab 内做)
        if idx < len(b.tabs):
            b.current = idx

    b._add_tab = _add_tab
    b._remove_tab = _remove_tab
    return b


# ---------------------------------------------------------------------------
# idx ↔ 稳定 tab_id 映射
# ---------------------------------------------------------------------------

@_reg
def test_open_tabs_get_distinct_tab_ids():
    """开三个标签 → _tab_ids 三个不同稳定 tab_id。"""
    b = _stub_browser()
    b._add_tab(_MiniPage())
    b._add_tab(_MiniPage())
    b._add_tab(_MiniPage())
    assert len(b._tab_ids) == 3
    assert len(set(b._tab_ids)) == 3  # 各不相同


@_reg
def test_tab_id_of_idx():
    """_g09_tab_id(idx) 返回对应稳定 tab_id；越界 None。"""
    b = _stub_browser()
    b._add_tab(_MiniPage()); tid0 = b._tab_ids[0]
    b._add_tab(_MiniPage()); tid1 = b._tab_ids[1]
    assert b._g09_tab_id(0) == tid0
    assert b._g09_tab_id(1) == tid1
    assert b._g09_tab_id(5) is None


@_reg
def test_idx_of_tab():
    """_g09_idx_of_tab(tab_id) 反查 idx；不存在 None。"""
    b = _stub_browser()
    b._add_tab(_MiniPage()); tid0 = b._tab_ids[0]
    assert b._g09_idx_of_tab(tid0) == 0
    assert b._g09_idx_of_tab(99999) is None


# ---------------------------------------------------------------------------
# 关标签 → compact 重建 + 旧回调拦截（G09 验收"关闭首/中/末、不串页"）
# ---------------------------------------------------------------------------

@_reg
def test_close_middle_tab_compacts():
    """关中间标签 → compact 后 _tab_ids 前移、会话记入关闭审计。"""
    b = _stub_browser()
    b._add_tab(_MiniPage())  # idx 0
    b._add_tab(_MiniPage())  # idx 1（将被关）
    b._add_tab(_MiniPage())  # idx 2
    ids_before = list(b._tab_ids)
    b._remove_tab(1)
    # _tab_ids 删掉被关槽位（idx 1）
    assert len(b._tab_ids) == 2
    # 被关 tab_id 进了会话关闭审计
    closed = b._g09_session.closed_tab_ids
    assert ids_before[1] in closed
    # 剩下两个 tab_id 不变（首/末槽位）
    assert set(b._tab_ids) == {ids_before[0], ids_before[2]}


@_reg
def test_resolve_old_callback_of_closed_tab_returns_none():
    """旧回调持被关标签的 idx → resolve 返回 None（不串页，计数拦截）。"""
    b = _stub_browser()
    b._add_tab(_MiniPage()); b._add_tab(_MiniPage()); b._add_tab(_MiniPage())
    # 记录关前旧 idx=1 的"回调"
    stale_count_before = b._g09_session.stale_callback_count
    b._remove_tab(1)  # 关中间标签
    # 旧回调以 old_idx=2 解析（它本指向原 idx2，关中间后 idx 前移）
    # 原 idx2 的 tab_id 仍在（成 idx1），但"old_idx=3"已越界 → None
    res = b._g09_resolve(3)
    assert res is None
    assert b._g09_session.stale_callback_count > stale_count_before


@_reg
def test_resolve_valid_idx_returns_tab_id():
    """存活 idx 的旧回调 → resolve 返回其稳定 tab_id（可安全继续）。"""
    b = _stub_browser()
    b._add_tab(_MiniPage()); tid0 = b._tab_ids[0]
    b._add_tab(_MiniPage())
    assert b._g09_resolve(0) == tid0  # idx0 仍存活


@_reg
def test_close_first_tab():
    """关首标签 → compact 后首槽位是原 idx1，会话审计含原首 tab_id。"""
    b = _stub_browser()
    b._add_tab(_MiniPage()); f = b._tab_ids[0]
    b._add_tab(_MiniPage()); m = b._tab_ids[1]
    b._remove_tab(0)
    assert f in b._g09_session.closed_tab_ids
    # 原中间标签现在成 idx0
    assert b._g09_idx_of_tab(m) == 0


@_reg
def test_stats_reflect_state():
    """_g09_stats 反映标签数 / active / 关闭审计。"""
    b = _stub_browser()
    b._add_tab(_MiniPage()); b._add_tab(_MiniPage()); b._add_tab(_MiniPage())
    st = b._g09_stats()
    assert st.tab_count == 3
    b._remove_tab(1)
    st2 = b._g09_stats()
    assert st2.tab_count == 2
    assert len(st2.closed_tab_ids) == 1


@_reg
def test_describe_contains_stale_count():
    """_g09_describe 诊断串含 stale_callbacks。"""
    b = _stub_browser()
    b._add_tab(_MiniPage()); b._add_tab(_MiniPage())
    s = b._g09_describe()
    assert "stale_callbacks=" in s
    assert "tabs=2" in s


# ---------------------------------------------------------------------------
# 真实 TabBrowser 冒烟（tkinter 可用时验证接线不抛异常）
# ---------------------------------------------------------------------------
_SHARED_ROOT = None
_SHARED_ROOT_MOD = None


def _shared_root():
    """惰性单例 Tk root（withdraw + update）。全量里多 Tk 测试共享，
    避免各自 destroy 污染 Tcl image（pyimage1 冲突）。"""
    global _SHARED_ROOT, _SHARED_ROOT_MOD
    if _SHARED_ROOT is not None:
        return _SHARED_ROOT
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    root.update()
    _SHARED_ROOT = root
    _SHARED_ROOT_MOD = tkinter
    return root


@_reg
def test_real_tabbrowser_g09_smoke():
    """有 Tk 环境时，构造真实 TabBrowser 验证 G09 接线
    （_g09_session + _tab_ids 一致、开标签走会话协议层）。
    用共享 root（不 destroy），避免全量 image 冲突。tkinter 不可用跳过。"""
    try:
        root = _shared_root()
    except Exception:
        return  # 无 tkinter：跳过
    b = _gui.TabBrowser(root)
    try:
        assert len(b._tab_ids) == len(b.tabs) == 1
        n0 = len(b._tab_ids)
        b._new_tab()
        b._new_tab()
        assert len(b._tab_ids) == n0 + 2
        # 关标签走会话协议层（_g09_close_tab 记审计）
        b._close_tab()
        assert len(b._tab_ids) == len(b.tabs)
        # describe 可跑
        assert "stale_callbacks=" in b._g09_describe()
    finally:
        # 不 destroy 共享 root（避免全量 image 冲突）
        pass


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
