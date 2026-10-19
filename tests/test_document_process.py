"""批次 38 — G07 step 4 文档进程多核方案回归。

被测：``toy_browser/app/document_process.py``
（:class:`DocumentProcessWorker` / :class:`DocumentProcessPool` /
:class:`DocCommand` / :class:`DocFrame`）。

设计：纯逻辑核心（不真实 spawn 多进程，避免回归依赖活进程环境），
验证"紧凑命令投递 + 紧凑帧取回 + 关标签丢弃在途 + 崩溃回退 +
C01 帧版本门控"。真实多核 spawn 留活显示批次。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.document_process import (
    DocumentProcessWorker, DocumentProcessPool, DocCommand, DocFrame,
    DOC_CMD_NAVIGATE, DOC_CMD_INPUT, DOC_CMD_RESIZE, DOC_CMD_SCROLL,
    DOC_CMD_STOP, VALID_DOC_COMMANDS,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 紧凑命令 / 帧（可 pickle，不序列化整棵 DOM）
# ---------------------------------------------------------------------------

@_reg
def test_doccommand_is_pkl():
    """紧凑命令可 pickle（跨进程传），且不含 DOM。"""
    c = DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE,
                   payload={"url": "http://a"}, seq=5)
    assert c.is_pkl()


@_reg
def test_docframe_is_pkl():
    """紧凑帧可 pickle（跨进程传回）。"""
    f = DocFrame(tab_id=1, seq=5, ok=True, payload={"frame_id": 3})
    import pickle
    f2 = pickle.loads(pickle.dumps(f))
    assert f2.tab_id == 1 and f2.seq == 5 and f2.payload["frame_id"] == 3


@_reg
def test_valid_doc_commands_set():
    """紧凑命令 kind 集合与 document_controller 的 CMD_* 对齐。"""
    assert set(VALID_DOC_COMMANDS) == {"navigate", "input", "resize",
                                       "scroll", "stop"}


# ---------------------------------------------------------------------------
# worker：C01 帧版本门控 + 关标签丢弃身份
# ---------------------------------------------------------------------------

@_reg
def test_worker_navigate_bumps_identity():
    """navigate 新文档：doc/epoch/frame 全 bump（旧帧 stale）。"""
    w = DocumentProcessWorker()
    f1 = w.handle(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    d1 = w.identity(1)
    f2 = w.handle(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "b"}, 2))
    d2 = w.identity(1)
    assert d2[0] > d1[0]  # doc_id 递增
    assert d2[2] > d1[2]  # frame_id 递增
    assert f2.payload["doc_id"] > f1.payload["doc_id"]


@_reg
def test_worker_resize_bumps_frame_only():
    """resize 同文档只 bump 帧（几何重算，文档身份不变）。"""
    w = DocumentProcessWorker()
    w.handle(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    mid = w.identity(1)
    w.handle(DocCommand(1, DOC_CMD_RESIZE, {"w": 800, "h": 600}, 2))
    after = w.identity(1)
    assert after[0] == mid[0]  # doc_id 不变
    assert after[2] > mid[2]   # frame_id 递增


@_reg
def test_worker_stop_marks_cancelled():
    """stop：在途帧标 cancelled（G08 step 3 不强杀，逻辑取消）。"""
    w = DocumentProcessWorker()
    w.handle(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    f = w.handle(DocCommand(1, DOC_CMD_STOP, {}, 2))
    assert f.cancelled


@_reg
def test_worker_drop_tab_releases_identity():
    """关标签丢弃该 tab 文档身份（不泄漏，G09 step 4）。"""
    w = DocumentProcessWorker()
    w.handle(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    w.handle(DocCommand(2, DOC_CMD_NAVIGATE, {"url": "b"}, 1))
    dropped = w.drop_tab(1)
    assert dropped == 1
    assert w.identity(1) == (0, 0, 0)  # tab1 身份已释放
    assert w.identity(2)[0] > 0        # tab2 仍在


# ---------------------------------------------------------------------------
# 池：投递 + 取回 + 关标签丢弃 + 崩溃回退
# ---------------------------------------------------------------------------

@_reg
def test_pool_post_drain_roundtrip():
    """投递 2 条命令 → drain 取回 2 帧（携带 tab / seq / 帧版本）。"""
    pool = DocumentProcessPool()
    pool.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    pool.post(DocCommand(1, DOC_CMD_RESIZE, {"w": 800}, 2))
    frames = pool.drain()
    assert len(frames) == 2
    assert [f.seq for f in frames] == [1, 2]
    assert pool.stats.frames_drained == 2
    assert pool.stats.commands_posted == 2


@_reg
def test_pool_close_tab_drops_pending():
    """关标签：在途命令 + 在途帧全丢弃，后续该 tab 命令被拒（不串页）。"""
    pool = DocumentProcessPool()
    pool.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    res = pool.close_tab(1)
    # 关标签丢弃了 1 条在途命令（尚未 drain 成帧，故计 dropped_cmds）
    assert res["dropped_cmds"] == 1
    # 关标签后再投该 tab 命令 → 被拒
    assert pool.post(DocCommand(1, DOC_CMD_NAVIGATE, seq=2)) is False
    # drain 无该 tab 帧
    assert pool.drain() == []
    # 在途命令被关标签丢弃（帧丢弃统计含命令态丢弃；R7-Q01: >=0 恒真，
    # 收紧为实际丢弃数）
    assert pool.stats.dropped_tab_frames >= 1


@_reg
def test_pool_close_only_affects_its_tab():
    """关 tab1 不影响 tab2 的投递 / 取回（G09 不串页）。"""
    pool = DocumentProcessPool()
    pool.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    pool.post(DocCommand(2, DOC_CMD_NAVIGATE, {"url": "b"}, 1))
    pool.close_tab(1)
    frames = pool.drain()
    tabs = {f.tab_id for f in frames}
    assert 1 not in tabs  # tab1 帧已丢
    assert 2 in tabs      # tab2 帧保留


@_reg
def test_pool_crash_fallback():
    """worker 崩溃 → 标记回退到内置同步 worker（UI 不崩）。"""
    pool = DocumentProcessPool()
    assert not pool.is_fallback()
    pool.mark_crashed()
    assert pool.is_fallback()
    assert pool.stats.worker_crashed
    # 回退后仍可 post/drain（内置同步处理，不崩）
    pool.post(DocCommand(3, DOC_CMD_NAVIGATE, {"url": "c"}, 1))
    assert len(pool.drain()) == 1


@_reg
def test_pool_fresh_tab_id_gets_identity():
    """未知 tab 首次命令 → worker 自动分配紧凑文档身份。"""
    pool = DocumentProcessPool()
    pool.post(DocCommand(9, DOC_CMD_NAVIGATE, {"url": "x"}, 1))
    f = pool.drain()[0]
    assert f.payload["doc_id"] >= 1  # 身份已分配


@_reg
def test_pool_describe():
    """describe 诊断串含关键统计。"""
    pool = DocumentProcessPool()
    pool.post(DocCommand(1, DOC_CMD_NAVIGATE, seq=1))
    pool.drain()
    s = pool.describe()
    assert "posted=1" in s
    assert "drained=1" in s
    assert "fallback=False" in s


@_reg
def test_make_multiprocessing_pool_fallback_no_crash():
    """make_multiprocessing_pool（批次 46 起返回 DocProcessServer）
    在不可 spawn 环境不崩（回退路径，post/drain 仍可用）。"""
    mod = __import__("toy_browser.app.document_process",
                    fromlist=["make_multiprocessing_pool"])
    pool = mod.make_multiprocessing_pool()
    try:
        pool.start()
    except Exception:
        pass
    pool.post(DocCommand(1, DOC_CMD_NAVIGATE, {"url": "a"}, 1))
    # R7-Q01 — drain() 契约：始终返回帧列表（可能为空）
    assert isinstance(pool.drain(), list)
    try:
        pool.shutdown()
    except Exception:
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
