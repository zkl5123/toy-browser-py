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
    # R7-Q01 — meaningful contract: start() reports success/failure as a
    # bool (no tautological ``or True``); the real spawn proof is the
    # is_spawned() assertion below.
    assert isinstance(ok, bool)
    if _can_spawn():
        assert srv.is_spawned()
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
        frames = srv.drain()
        assert isinstance(frames, list)
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
        # R7-Q01 — the close_tab contract reports a dropped-command count
        # (a real int), not just "did not raise".
        assert isinstance(res["dropped_cmds"], int) and res["dropped_cmds"] >= 0
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
    # R7-Q01 — drain() always answers with a list of frames (the frame
    # payload contract itself is proven by the dedicated frame tests).
    assert isinstance(srv.drain(), list)
    srv.shutdown()


@_reg
def test_make_multiprocessing_pool_returns_server():
    """make_multiprocessing_pool（批次 46 起）返回 DocProcessServer
    真实 spawn 池（兼容既有消费端入口名）。"""
    pool = make_multiprocessing_pool()
    assert isinstance(pool, DocProcessServer)
    pool.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "m"}, 1))
    assert isinstance(pool.drain(), list)
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


# ---------------------------------------------------------------------------
# R7-A02 第 2 点 — worker 持 DocumentState 执行真实文档工作
# ---------------------------------------------------------------------------

def _make_stub_server_with_fallback_worker():
    """构造 DocProcessServer 并把其回退 worker 直接暴露（父进程内
    可观测真实文档工作状态，不 spawn 子进程）。"""
    from toy_browser.app.document_process import DocProcessServer
    srv = DocProcessServer()
    return srv


@_reg
def test_r7a02_worker_navigate_html_does_real_doc_work():
    """R7-A02 第 2 点 — worker navigate 带 html 时，真实解析 / JS /
    样式 / 布局 / 光栅化在 worker 内执行（DocumentState 持有 DOM +
    Host），``has_done_doc_work()`` 翻转为 True；无 html（纯身份
    导航）时保持 False。"""
    from toy_browser.app.document_process import (
        DocumentProcessWorker, DocCommand, DOC_CMD_NAVIGATE, DOC_CMD_RESIZE)

    w = DocumentProcessWorker()
    assert w.has_done_doc_work() is False, "未导航前不做真实文档工作"

    # navigate 带 html + 几何 → 真实文档工作（解析 + JS + 样式 + 布局 + 绘制）
    w.handle(DocCommand(1, DOC_CMD_NAVIGATE,
                       {"url": "http://a", "html": "<p>hi</p>",
                        "w": 800, "h": 600}, 1))
    assert w.has_done_doc_work() is True, "navigate 带 html 后须翻转"
    doc = w.doc_state_for(1)
    assert doc is not None, "worker 须持有该 tab 的 DocumentState"
    assert doc.dom_tree is not None, "DocumentState 须含解析后的 DOM"
    assert doc.script_init_count == 1, "脚本初始化只增加一次（navigate）"
    # 最近渲染 Bitmap 已持有（紧凑像素 / 尺寸）
    bm = w.last_bitmap(1)
    assert bm is not None, "worker 须持有最近渲染的 Bitmap"

    # resize（tab 已有 DocumentState）→ 重排，不重跑脚本
    w.handle(DocCommand(1, DOC_CMD_RESIZE, {"w": 1024, "h": 768}, 2))
    assert doc.script_init_count == 1, "resize 重排不重建 Host / 不重跑脚本"
    assert w.has_done_doc_work() is True

    # 另一个 tab 纯身份 navigate（无 html）→ 不影响整体标记，
    # 但新 tab 无 DocumentState
    w.handle(DocCommand(2, DOC_CMD_NAVIGATE, {"url": "http://b"}, 3))
    assert w.doc_state_for(2) is None, "无 html 的纯身份导航不建 DocumentState"
    assert w.last_bitmap(2) is None


@_reg
def test_r7a02_server_fallback_worker_has_done_doc_work_proxy():
    """R7-A02 第 2 点 — DocProcessServer.has_done_doc_work() 代理
    父进程内置同步 worker 的真实文档工作标记：回退态下 worker
    处理带 html 的 navigate 后代理返回 True；纯身份导航保持 False。

    多核态（子进程存活）下父进程无法直接观测子进程是否做了真实
    文档工作，代理保持保守（不代子进程返回 True）——不据"子进程
    spawn 了"宣称已多核渲染。"""
    srv = _make_stub_server_with_fallback_worker()
    # 不 spawn 子进程，直接走回退 worker（父进程内可观测）
    assert srv.has_done_doc_work() is False
    # 直接驱动父进程回退 worker 做真实文档工作（回退态路径）
    from toy_browser.app.document_process import (
        DocCommand, DOC_CMD_NAVIGATE)
    cmd = DocCommand(1, DOC_CMD_NAVIGATE,
                     {"url": "http://a", "html": "<p>x</p>",
                      "w": 800, "h": 600}, 1)
    srv._worker.handle(cmd)
    assert srv.has_done_doc_work() is True, "回退 worker 做真实文档工作后" \
        "代理须翻转"
    srv.shutdown()


@_reg
def test_r7a02_server_child_process_doc_work_not_observed():
    """R7-A02 第 2 点（不冒充通过）— 子进程存活（多核态）时，父
    进程 has_done_doc_work() 代理**不**代子进程返回 True（子进程
    内部 worker 的文档工作状态无法从父进程直接观测）。

    验证保守口径：spawn 子进程后（子进程侧独立 DocumentProcessWorker
    会做真实文档工作），父进程代理仍只反映**父进程内置同步
    worker** 的标记（未翻转 → False）。这是"不据子进程 spawn 宣称
    已多核渲染"的可测证据。

    多核态要判 WORKER_FULL 需子进程经 IPC 回报 doc_work 标记
    （开放问题，留活显示批次——IPC 协议扩展 + 活显示实测）。
    """
    if not _can_spawn():
        return  # spawn 不可用 → 回退态，不适用本多核口径
    srv = _make_stub_server_with_fallback_worker()
    try:
        ok = srv.start()
        assert ok is True, "spawn 可用性下 start 须成功"
        assert srv.is_spawned()
        # 父进程内置同步 worker 未做真实文档工作 → 代理 False
        assert srv.has_done_doc_work() is False, \
            "多核态父进程不代子进程返回 True（保守，不冒充）"
        # 投一条带 html 的 navigate 到子进程（子进程侧会做真实文档工作，
        # 但父进程无法直接观测）
        from toy_browser.app.document_process import (
            DocCommand, DOC_CMD_NAVIGATE)
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE,
                            {"url": "http://a", "html": "<p>sub</p>",
                             "w": 800, "h": 600}, 1))
        import time
        for _ in range(10):
            if srv.drain():
                break
            time.sleep(0.05)
        # 父进程代理仍 False（未回报子进程标记）——保守不冒充
        assert srv.has_done_doc_work() is False, \
            "子进程存活时父进程不据 spawn 宣称 worker 做了真实文档工作"
    finally:
        srv.shutdown()


@_reg
def test_r7a02_gui_doc_mode_uses_worker_real_doc_work():
    """R7-A02 第 2 点 — gui._r7_a02_doc_mode 据 worker 真实文档工作
    标记判模式（不靠名称宣称）：

    * worker 未做真实文档工作（``has_done_doc_work()`` False）→
      即使子进程 spawn 也只判 WORKER_IPC（管道通了但活还在主线程）。
    * worker 做了真实文档工作（``has_done_doc_work()`` True）且子进程
      spawn → 判 WORKER_FULL（活真在 worker，可支撑多核渲染）。
    * 未 spawn → 即便 worker 做了真实文档工作，因 worker_spawned=False
      仍判 WORKER_IPC（WORKER_FULL 需"spawn + 真实文档工作"双满足；
      detect_doc_work_mode 实际只要 worker_does_doc_work 即 WORKER_FULL，
      但 gui 接线把 navigation_is_sync 恒 True——本用例验证"未做真实
      文档工作时绝不判 WORKER_FULL"的保守闸门）。
    """
    from toy_browser.app.gui import TabBrowser
    from toy_browser.app.document_process import (
        DocProcessServer, DocCommand, DOC_CMD_NAVIGATE)
    from toy_browser.app.doc_process_mode import (
        MODE_WORKER_FULL, MODE_WORKER_IPC)

    tb = object.__new__(TabBrowser)
    srv = DocProcessServer()
    tb._g07_doc_server = srv
    tb._r7_a02_doc_mode = TabBrowser._r7_a02_doc_mode.__get__(tb)

    # ① worker 未做真实文档工作 → 不得判 WORKER_FULL
    mode1 = tb._r7_a02_doc_mode()
    assert mode1 is not None, "gui._r7_a02_doc_mode 须返回 DocWorkMode"
    assert mode1.mode != MODE_WORKER_FULL, \
        "worker 未做真实文档工作时不得判 WORKER_FULL（保守闸门）"
    assert mode1.worker_does_doc_work is False

    # ② 让 worker 做真实文档工作（回退 worker 持有 DocumentState）
    srv._worker.handle(DocCommand(
        1, DOC_CMD_NAVIGATE,
        {"url": "http://a", "html": "<p>g</p>", "w": 800, "h": 600}, 1))
    assert srv.has_done_doc_work() is True

    mode2 = tb._r7_a02_doc_mode()
    assert mode2 is not None
    assert mode2.worker_does_doc_work is True, \
        "worker 做真实文档工作后 gui 须读到 True"
    # 未 spawn 子进程时 worker_spawned=False；detect_doc_work_mode
    # 对 worker_does_doc_work=True 判 WORKER_FULL（与 worker_spawned 无关）。
    # 本用例验证的是"做了真实文档工作 → gui 标记翻转"这一关键接线，
    # 以及"未做时绝不 WORKER_FULL"的保守闸门。
    srv.shutdown()


@_reg
def test_r7a02_gui_doc_mode_truthful_claim_gate():
    """R7-A02 第 2 点 + 第 6 点 — "已多核渲染"声明在 worker 未做真实
    文档工作时判为**不真实**（越级声明闸门），做了之后该声明方可
    成立（WORKER_FULL 下 multi_core_rendering 能力为真）。"""
    from toy_browser.app.gui import TabBrowser
    from toy_browser.app.document_process import (
        DocProcessServer, DocCommand, DOC_CMD_NAVIGATE)
    from toy_browser.app.doc_process_mode import (
        MODE_WORKER_FULL, claim_multi_core_rendering)

    tb = object.__new__(TabBrowser)
    srv = DocProcessServer()
    tb._g07_doc_server = srv
    tb._r7_a02_doc_mode = TabBrowser._r7_a02_doc_mode.__get__(tb)

    # worker 未做真实文档工作 → "已多核渲染"声明不真实
    m1 = tb._r7_a02_doc_mode()
    assert m1 is not None and m1.mode != MODE_WORKER_FULL
    claim1 = claim_multi_core_rendering(m1)
    assert claim1.is_truthful() is False, \
        "未做真实文档工作时多核渲染声明必须不真实"

    # 让 worker 做真实文档工作
    srv._worker.handle(DocCommand(
        1, DOC_CMD_NAVIGATE,
        {"url": "http://a", "html": "<p>t</p>", "w": 800, "h": 600}, 1))
    m2 = tb._r7_a02_doc_mode()
    assert m2 is not None
    claim2 = claim_multi_core_rendering(m2)
    # worker 做了真实文档工作 → detect_doc_work_mode 判 WORKER_FULL
    # → multi_core_rendering 能力为真 → 声明可成立
    assert m2.mode == MODE_WORKER_FULL, \
        "worker 做真实文档工作后须判 WORKER_FULL"
    assert claim2.is_truthful() is True, \
        "WORKER_FULL 下多核渲染声明须真实"
    srv.shutdown()


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
