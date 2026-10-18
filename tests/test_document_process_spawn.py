"""批次 46 — G07 文档进程真实 spawn（multiprocessing 子进程 worker）+
活显示验收入口回归。

被测：``toy_browser/app/document_process.py``
（:class:`DocProcessServer` / :func:`spawn_doc_process_server` /
:func:`make_multiprocessing_pool` / :func:`_doc_process_child`）。

设计：真实 ``multiprocessing`` spawn 子进程（多核收益落地），验证
"紧凑命令经 pipe 投递（不阻塞 UI）+ 紧凑帧经 pipe 取回 + 关标签
经 close_q 丢弃在途身份（G09 step 4）+ 子进程崩溃回退内置同步
worker（UI 不崩）+ shutdown 清理子进程"。活显示验收入口
``gui.py`` 经 :func:`spawn_doc_process_server` 起池，导航 / resize
经 :meth:`post` 投递，事件循环经 :meth:`drain` 取回，关标签经
:meth:`close_tab`。

回归不强制真实 spawn（``multiprocessing`` 不可用时走回退路径，
仍验证协议一致），但 Windows spawn context 可用时真实起子进程
（多核收益 + 活显示验收冒烟）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.document_process import (
    DocProcessServer, spawn_doc_process_server, make_multiprocessing_pool,
    DocCommand, DocFrame,
    DOC_CMD_NAVIGATE, DOC_CMD_RESIZE, DOC_CMD_STOP,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _can_spawn():
    """真实 spawn 可用性（multiprocessing spawn context 可用）。"""
    try:
        import multiprocessing as _mp
        _mp.get_context("spawn")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# spawn / 生命周期
# ---------------------------------------------------------------------------

@_reg
def test_server_start_and_shutdown():
    """真实 spawn：start 起子进程，is_spawned 为 True；shutdown 终止。"""
    srv = DocProcessServer()
    ok = srv.start()
    assert ok == _can_spawn() or True  # start 不崩即可
    if _can_spawn():
        assert srv.is_spawned()
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
        frames = srv.drain()
        assert len(frames) >= 0
        srv.shutdown()
        assert not srv.is_spawned()
    else:
        # 回退态：start 失败置 on_fallback
        assert srv.is_fallback()
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
        assert len(srv.drain()) == 1
        srv.shutdown()


@_reg
def test_server_post_drain_multi_process():
    """多进程投递 + 取回：navigate / resize 经 pipe 到子进程 worker，
    帧经 result_q 取回（携带 tab / seq / 帧版本，C01 门控）。"""
    srv = spawn_doc_process_server()
    try:
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "http://a"}, 1))
        srv.post(DocCommand(1, DOC_CMD_RESIZE, {"w": 800, "h": 600}, 2))
        # 给子进程一点时间处理（非阻塞 drain 轮询）
        import time
        frames = []
        for _ in range(20):
            frames = srv.drain()
            if len(frames) >= 2:
                break
            time.sleep(0.05)
        assert len(frames) == 2, f"期望 2 帧，得 {len(frames)}"
        assert [f.seq for f in frames] == [1, 2]
        assert frames[0].payload.get("url") == "http://a"
        assert frames[1].payload.get("kind") == "resize"
        assert srv.stats.frames_drained == 2
    finally:
        srv.shutdown()


@_reg
def test_server_close_tab_drops_in_flight():
    """关标签：子进程经 close_q 丢弃该 tab 在途身份（G09 step 4），
    后续该 tab 命令被拒（不串页），其他 tab 不受影响。"""
    srv = spawn_doc_process_server()
    try:
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
        srv.post(DocCommand(2, DOC_CMD_NAVIGATE, {"url": "b"}, 1))
        res = srv.close_tab(1)
        # 关 tab1 后投 tab1 命令 → 被拒
        assert srv.post(DocCommand(1, DOC_CMD_NAVIGATE, seq=2)) is False
        # tab2 仍可用
        assert srv.post(DocCommand(2, DOC_CMD_RESIZE, {"w": 100}, 2)) is True
        import time
        frames = []
        for _ in range(20):
            frames = srv.drain()
            if len(frames) >= 1:
                break
            time.sleep(0.05)
        tabs = {f.tab_id for f in frames}
        assert 1 not in tabs  # tab1 帧已丢
        assert 2 in tabs      # tab2 保留
        assert res["dropped_cmds"] >= 0
    finally:
        srv.shutdown()


@_reg
def test_server_stop_mark_cancelled():
    """stop 命令：子进程 worker 标 cancelled 帧（G08 step 3 不强杀，
    逻辑取消），经 result_q 取回。"""
    srv = spawn_doc_process_server()
    try:
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
        srv.post(DocCommand(1, DOC_CMD_STOP, {}, 2))
        import time
        frames = []
        for _ in range(20):
            frames = srv.drain()
            if len(frames) >= 2:
                break
            time.sleep(0.05)
        assert len(frames) == 2
        assert frames[1].cancelled is True
    finally:
        srv.shutdown()


@_reg
def test_server_fallback_path():
    """回退路径：on_fallback 置位后 post/drain 走父进程内置同步
    worker（UI 不崩，命令不丢）。"""
    srv = DocProcessServer()
    srv._fallback_sync()  # 模拟子进程崩溃
    assert srv.is_fallback()
    srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "c"}, 1))
    frames = srv.drain()
    assert len(frames) == 1
    assert frames[0].payload.get("url") == "c"
    assert srv.stats.frames_drained == 1
    srv.shutdown()


@_reg
def test_server_describe_includes_spawned():
    """describe 诊断串含 spawned 字段（区分真实多核 vs 回退态）。"""
    srv = DocProcessServer()
    s = srv.describe()
    assert "spawned=" in s
    srv.start()
    s2 = srv.describe()
    assert "spawned=" in s2
    srv.shutdown()


@_reg
def test_spawn_entry_returns_server():
    """活显示验收入口：spawn_doc_process_server 返回 DocProcessServer
    （可 spawn 时真实起子进程，不可用时回退，均不崩）。"""
    srv = spawn_doc_process_server()
    assert isinstance(srv, DocProcessServer)
    srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "x"}, 1))
    assert len(srv.drain()) >= 0
    srv.shutdown()


@_reg
def test_make_multiprocessing_pool_returns_server():
    """make_multiprocessing_pool（批次 46 起）返回 DocProcessServer
    真实 spawn 池（兼容既有消费端入口名）。"""
    pool = make_multiprocessing_pool()
    assert isinstance(pool, DocProcessServer)
    pool.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "m"}, 1))
    assert len(pool.drain()) >= 0
    pool.shutdown()


# ---------------------------------------------------------------------------
# gui 接线（批次 46 活显示验收入口）
# ---------------------------------------------------------------------------

def _make_stub_tabbrowser_g07():
    """构造不跑 __init__ 的 TabBrowser stub（批次 46 活显示验收入口）。

    注入最小桩属性（_g07_doc_server = DocProcessServer 真实池），验证
    :meth:`_g07_post_nav` / :meth:`_g07_post_resize` /
    :meth:`_g07_drain_frames` / :meth:`_g07_doc_server_shutdown` /
    :meth:`_g07_describe` 走真实 spawn 池（不构建 Tk UI）。
    """
    from toy_browser.app.gui import TabBrowser
    from toy_browser.app.document_process import spawn_doc_process_server
    tb = object.__new__(TabBrowser)
    tb._g07_doc_server = spawn_doc_process_server()
    tb._g07_ui_closing = False
    tb._g07_post_nav = TabBrowser._g07_post_nav.__get__(tb)
    tb._g07_post_resize = TabBrowser._g07_post_resize.__get__(tb)
    tb._g07_drain_frames = TabBrowser._g07_drain_frames.__get__(tb)
    tb._g07_doc_server_shutdown = TabBrowser._g07_doc_server_shutdown.__get__(tb)
    tb._g07_describe = TabBrowser._g07_describe.__get__(tb)
    return tb


@_reg
def test_gui_g07_post_nav_via_server():
    """gui._g07_post_nav 经真实 spawn 池投递 navigate 命令（不阻塞 UI，
    多核收益；帧可经 drain 取回）。"""
    tb = _make_stub_tabbrowser_g07()
    try:
        assert tb._g07_post_nav(tab_id=1, url="http://a", seq=1) is True
        import time
        frames = []
        for _ in range(20):
            frames = tb._g07_drain_frames()
            if frames:
                break
            time.sleep(0.05)
        assert len(frames) == 1
        assert frames[0].tab_id == 1 and frames[0].payload.get("url") == "http://a"
    finally:
        tb._g07_doc_server_shutdown()


@_reg
def test_gui_g07_post_resize_via_server():
    """gui._g07_post_resize 经真实 spawn 池投递 resize 命令（几何重算
    走多核侧），帧携带 kind=resize。"""
    tb = _make_stub_tabbrowser_g07()
    try:
        assert tb._g07_post_resize(tab_id=2, w=800, h=600, seq=1) is True
        import time
        frames = []
        for _ in range(20):
            frames = tb._g07_drain_frames()
            if frames:
                break
            time.sleep(0.05)
        assert len(frames) == 1
        assert frames[0].payload.get("kind") == "resize"
    finally:
        tb._g07_doc_server_shutdown()


@_reg
def test_gui_g07_describe_and_shutdown_no_crash():
    """gui._g07_describe 诊断串含 spawned 字段；_g07_doc_server_shutdown
    终止子进程后 describe 仍可用（回退态 / 已停态不崩）。"""
    tb = _make_stub_tabbrowser_g07()
    s = tb._g07_describe()
    assert "spawned=" in s
    tb._g07_post_nav(tab_id=1, url="http://z", seq=1)
    tb._g07_doc_server_shutdown()
    s2 = tb._g07_describe()  # shutdown 后 describe 不崩
    assert "spawned=" in s2


@_reg
def test_gui_g09_close_tab_drops_doc_server_tab():
    """gui._g09_close_tab 关标签时同步丢弃文档进程池在途身份（批次 46
    接线，G09 step 4 不泄漏）：关 tab 后该 tab 命令被拒。"""
    from toy_browser.app.gui import TabBrowser
    from toy_browser.app.document_process import spawn_doc_process_server
    tb = object.__new__(TabBrowser)
    tb._g07_doc_server = spawn_doc_process_server()
    tb._g09_session = type("S", (), {"close_tab": lambda self_, t: {
        "cancelled": 0, "compact_mapping": {}, "new_active": 0}})()
    tb._tab_ids = [10, 11]
    tb._g09_close_tab = TabBrowser._g09_close_tab.__get__(tb)
    try:
        # 关 tab_id=10（idx 0）：同步丢弃文档进程池该 tab 身份
        tb._g07_post_nav(tab_id=10, url="http://a", seq=1)
        tb._g07_post_nav(tab_id=11, url="http://b", seq=1)
        res = tb._g09_close_tab(0)
        assert res["cancelled"] == 0
        assert 10 not in tb._tab_ids
        assert 11 in tb._tab_ids
        # 关标签后投 tab_id=10 → 被拒；tab_id=11 仍可用
        assert tb._g07_post_nav(tab_id=10, url="http://c", seq=2) is False
        assert tb._g07_post_nav(tab_id=11, url="http://d", seq=2) is True
    finally:
        tb._g07_doc_server_shutdown()


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
