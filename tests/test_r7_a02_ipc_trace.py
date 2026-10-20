"""R7-A02 — 版本化 IPC（navigation_id / 合并 / 背压 / 取消确认）+ UI 心跳 task trace 回归。

被测：``toy_browser/app/document_process.py``（R7-A02 新增部分）
+ ``toy_browser/app/gui.py`` 的 ``_commit_navigation`` 接线 +
``_r7_a02_describe`` 诊断。

round7 实施方案 R7-A02「把实际页面工作移出 UI 线程」：

1. 先在默认导航/resize/scroll 入口加入 UI 心跳和 task trace，
   确认真正调用路径。（→ ``UiHeartbeat`` / ``TaskTrace``
   + ``gui._commit_navigation`` 接线）
3. 定义版本化 IPC：tab_id/navigation_id/document_version/frame_id，
   命令合并、背压、取消确认、进程退出回收。（→ ``DocCommand.
   navigation_id`` / ``ipc_identity`` / ``CommandCoalescer`` /
   ``BackpressureGate`` / ``CancelTracker`` / ``NavVersionGate``）

两层设计（"不冒充通过"）：

* 静态层（无 OS/wgpu/tkinter 即可导入）——源码契约字符串断言。
* 动态层——真实调用各组件 + 真实 worker handle + gui 接线源码
  契约（gui 实例化需 Tk，静态断言接线点，动态层跑纯逻辑组件）。

**不冒充通过**：本批只做**可测的决策层**（版本化身份 / 合并 /
背压 / 取消确认 / 心跳计数 / trace 调用路径）。真实多核加速
（worker 持 DocumentSession 执行真实解析 + 布局 + 绘制）、
真实墙钟响应延迟（P95 ≤ 100ms）、连续 buffer / 共享内存帧
传输、子进程真实 spawn 回收均**未验证**（留活 UI / GPU 批次）。

无 OS / wgpu / tkinter 依赖即可回归（P13 不崩）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.document_process import (
    DocCommand, DocFrame,
    DOC_CMD_NAVIGATE, DOC_CMD_RESIZE, DOC_CMD_SCROLL, DOC_CMD_STOP,
    DocumentProcessWorker, CommandCoalescer, BackpressureGate,
    CancelTracker, NavVersionGate, UiHeartbeat, TaskTrace, TraceSpan,
    COALESCABLE_COMMANDS,
)

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read(path):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(base, path), encoding="utf-8").read()


def _make_stub_browser():
    """构造不跑 ``__init__`` 的 TabBrowser stub（R7-A02 第 1 点动态层）。

    注入最小桩：一个 fake page（scroll_max / scroll_by / scroll_to /
    content_height / height）+ no-op ``_refresh_canvas`` + 真实
    :class:`UiHeartbeat` / :class:`TaskTrace` + 真实文档进程池（resize
    投递走真实 :class:`DocProcessServer`，回退态即可，不需 spawn）。
    scroll / resize 入口经 ``__get__`` 绑定到 stub 上，不开 Tk 主循环
    即可验证"入口真实记 span / 推进心跳"。
    """
    from toy_browser.app import gui as _gui
    from toy_browser.app.document_process import (
        UiHeartbeat, TaskTrace, DocProcessServer)

    class _FakePage:
        height = 600
        content_height = 2000

        def scroll_max(self):
            return max(0, self.content_height - self.height)

        def scroll_by(self, dy):
            pass

        def scroll_to(self, y):
            pass

    cls = None
    for nm in dir(_gui):
        o = getattr(_gui, nm)
        if isinstance(o, type) and hasattr(o, "handle_scroll_command"):
            cls = o
            break
    assert cls is not None, "gui 缺 handle_scroll_command 所属类"
    tb = cls.__new__(cls)
    tb.tabs = [_FakePage()]
    tb.current = 0
    tb._r7_a02_heartbeat = UiHeartbeat()
    tb._r7_a02_trace = TaskTrace()
    tb._r7_a02_nav_id = {}
    tb._g07_doc_server = DocProcessServer()
    # 绑定入口（不跑 __init__，故手工绑定）+ no-op 呈现（无 Tk）
    tb.handle_scroll_command = cls.handle_scroll_command.__get__(tb)
    tb.handle_wheel_event = cls.handle_wheel_event.__get__(tb)
    tb._g07_post_resize = cls._g07_post_resize.__get__(tb)
    tb._refresh_canvas = lambda idx: None
    return tb


# ----------------------------------------------------------------------
# 静态层
# ----------------------------------------------------------------------

@_test
def test_static_doc_process_ipc_contract():
    src = _read(os.path.join("toy_browser", "app", "document_process.py"))
    for sym in ("navigation_id", "CommandCoalescer", "BackpressureGate",
                "CancelTracker", "NavVersionGate", "UiHeartbeat",
                "TaskTrace", "ipc_identity", "COALESCABLE_COMMANDS"):
        assert sym in src, f"document_process.py 缺 {sym} 契约"


@_test
def test_static_gui_navigation_wiring_contract():
    src = _read(os.path.join("toy_browser", "app", "gui.py"))
    # R7-A02 第 1 点：默认导航入口接心跳 + trace。
    assert "_r7_a02_heartbeat" in src, "gui 未接 UI 心跳"
    assert "_r7_a02_trace" in src, "gui 未接 task trace"
    assert "_commit_navigation" in src, "gui 缺默认导航入口"
    # R7-A02 第 3 点：版本化 IPC 门控 + 合并 + 背压 + 取消确认。
    for sym in ("_r7_a02_nav_gate", "_r7_a02_coalescer",
                "_r7_a02_backpressure", "_r7_a02_cancel",
                "_r7_a02_nav_id", "_r7_a02_describe"):
        assert sym in src, f"gui 缺 {sym} 契约"


@_test
def test_static_navigation_id_in_command_and_frame():
    """版本化 IPC 四元组：DocCommand / DocFrame 都带 navigation_id。"""
    assert "navigation_id" in DocCommand.__dataclass_fields__, \
        "DocCommand 缺 navigation_id 字段"
    assert "navigation_id" in DocFrame.__dataclass_fields__, \
        "DocFrame 缺 navigation_id 字段"
    # 向后兼容：默认 0（既有单导航场景不传也工作）。
    c = DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE)
    assert c.navigation_id == 0, f"navigation_id 默认应为 0：{c.navigation_id}"


@_test
def test_static_coalescable_commands_contract():
    """可合并命令仅 resize / scroll（navigate / stop 不可合并）。"""
    assert DOC_CMD_RESIZE in COALESCABLE_COMMANDS, "resize 应可合并"
    assert DOC_CMD_SCROLL in COALESCABLE_COMMANDS, "scroll 应可合并"
    assert DOC_CMD_NAVIGATE not in COALESCABLE_COMMANDS, \
        "navigate 不可合并（每次是新文档身份）"
    assert DOC_CMD_STOP not in COALESCABLE_COMMANDS, \
        "stop 不可合并（每次取消都要被确认）"


# ----------------------------------------------------------------------
# 动态层
# ----------------------------------------------------------------------

@_test
def test_dynamic_navigation_id_monotonic_per_tab():
    """navigation_id：同一 tab 内每次导航 +1（版本化 IPC 四元组之一）。"""
    w = DocumentProcessWorker()
    f1 = w.handle(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE,
                             payload={"url": "a"}, seq=1, navigation_id=1))
    f2 = w.handle(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE,
                             payload={"url": "b"}, seq=2, navigation_id=2))
    assert f1.navigation_id == 1, f"首帧 nav_id 应为 1：{f1.navigation_id}"
    assert f2.navigation_id == 2, f"次帧 nav_id 应为 2：{f2.navigation_id}"
    ident = w.ipc_identity(1)
    # 四元组齐全
    for key in ("tab_id", "navigation_id", "document_version", "frame_id"):
        assert key in ident, f"ipc_identity 缺 {key}"
    assert ident["navigation_id"] == 2, f"identity nav_id 应为 2：{ident}"


@_test
def test_dynamic_navigation_id_per_tab_independent():
    """不同 tab 的 navigation_id 互不干扰（慢页不影响其他标签）。"""
    w = DocumentProcessWorker()
    w.handle(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE, seq=1,
                        navigation_id=1))
    w.handle(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE, seq=2,
                        navigation_id=2))
    w.handle(DocCommand(tab_id=2, kind=DOC_CMD_NAVIGATE, seq=1,
                        navigation_id=1))
    assert w.ipc_identity(1)["navigation_id"] == 2, "tab1 应为 2"
    assert w.ipc_identity(2)["navigation_id"] == 1, "tab2 应为 1"


@_test
def test_dynamic_coalescer_keeps_latest_geometry():
    """命令合并：同 (tab, nav, kind) 的 resize 只保留最新一条。"""
    co = CommandCoalescer()
    for i, w in enumerate((800, 900, 1000), start=1):
        co.push(DocCommand(tab_id=1, kind=DOC_CMD_RESIZE,
                           payload={"w": w}, seq=i, navigation_id=1))
    co.push(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE,
                       payload={"url": "x"}, seq=4, navigation_id=1))
    out = co.drain()
    assert len(out) == 2, f"合并后应 2 条（1 resize + 1 nav）：{len(out)}"
    resize = [c for c in out if c.kind == DOC_CMD_RESIZE]
    assert len(resize) == 1, "应只剩 1 条 resize"
    assert resize[0].payload["w"] == 1000, \
        f"应保留最新几何 w=1000：{resize[0].payload}"
    assert co.merged == 2, f"应合并掉 2 条旧命令：{co.merged}"


@_test
def test_dynamic_coalescer_navigate_not_merged():
    """退化契约：只有 navigate（不可合并）时原样保留、按 seq 排序。"""
    co = CommandCoalescer()
    co.push(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE, seq=2,
                       payload={"url": "b"}, navigation_id=1))
    co.push(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE, seq=1,
                       payload={"url": "a"}, navigation_id=1))
    out = co.drain()
    assert len(out) == 2, f"navigate 不合并应保留 2 条：{len(out)}"
    assert [c.seq for c in out] == [1, 2], \
        f"应按 seq 升序保证因果：{[c.seq for c in out]}"
    assert co.merged == 0, f"无可合并命令时 merged 应为 0：{co.merged}"


@_test
def test_dynamic_backpressure_gate_rejects_at_limit():
    """背压：在途达上限拒收（避免慢页把命令队列撑爆）。"""
    g = BackpressureGate(limit=3)
    assert g.admit(0) is True, "depth 0 应准入"
    assert g.admit(2) is True, "depth 2 (<3) 应准入"
    assert g.admit(3) is False, "depth 3 (>=limit) 应拒收"
    assert g.dropped == 1, f"应计 1 次丢弃：{g.dropped}"
    assert g.is_saturated is True, "最近一次应标记饱和"


@_test
def test_dynamic_cancel_tracker_ack():
    """取消确认：取消须被 ack（不静默），未确认保持在途。"""
    ct = CancelTracker()
    t1 = ct.request(1, navigation_id=1)
    assert ct.pending() == 1, f"发起后应 1 条在途：{ct.pending()}"
    assert ct.is_acked(t1) is False, "未 ack 前不应为已确认"
    assert ct.ack(t1) is True, "ack 应成功"
    assert ct.pending() == 0, f"ack 后应 0 条在途：{ct.pending()}"
    assert ct.is_acked(t1) is True, "ack 后应为已确认"
    # 重复 / 未知 token 返回 False
    assert ct.ack(t1) is False, "重复 ack 应返回 False"
    assert ct.ack(9999) is False, "未知 token ack 应返回 False"


@_test
def test_dynamic_cancel_tracker_ack_tab():
    """按 tab 批量确认（关标签场景：该 tab 全部取消一次性确认）。"""
    ct = CancelTracker()
    ct.request(1, navigation_id=1)
    ct.request(1, navigation_id=2)
    ct.request(2, navigation_id=1)
    assert ct.pending() == 3, f"应 3 条在途：{ct.pending()}"
    n = ct.ack_tab(1)
    assert n == 2, f"tab1 应确认 2 条：{n}"
    assert ct.pending() == 1, f"剩 tab2 的 1 条：{ct.pending()}"


@_test
def test_dynamic_nav_gate_old_nav_cannot_overwrite():
    """核心验收："旧任务不可覆盖新页"——旧导航的迟到帧被丢弃。"""
    gate = NavVersionGate()
    assert gate.admit(1, 1, 1) is True, "首个 nav1 帧应可应用"
    assert gate.admit(1, 2, 1) is True, "新导航 nav2 帧应可应用"
    # 旧导航 nav1 的迟到帧（frame 更大也不行）
    assert gate.admit(1, 1, 9) is False, \
        "旧导航的迟到帧必须丢弃（旧任务不可覆盖新页）"
    # 同导航内旧帧
    assert gate.admit(1, 2, 1) is False, "同导航内旧帧必须丢弃"
    assert gate.admit(1, 2, 2) is True, "同导航内新帧应可应用"
    assert gate.current(1) == (2, 2), f"当前应 (2,2)：{gate.current(1)}"


@_test
def test_dynamic_nav_gate_reset_on_tab_close():
    """关标签重置门控（不泄漏已关闭标签的版本状态）。"""
    gate = NavVersionGate()
    gate.admit(1, 5, 3)
    assert gate.current(1) == (5, 3)
    gate.reset(1)
    assert gate.current(1) == (0, 0), f"reset 后应归零：{gate.current(1)}"
    # reset 后可重新接纳（新标签复用 tab_id 场景）
    assert gate.admit(1, 1, 1) is True, "reset 后应重新接纳"


@_test
def test_dynamic_heartbeat_counts_real_calls():
    """UI 心跳：每次真实调用推进计数（慢页期间 UI 被推进的证据）。"""
    hb = UiHeartbeat(clock=lambda: 1.0)
    assert hb.beats == 0, "初始应 0 次"
    assert hb.beat() == 1, "首次 beat 应返回 1"
    hb.beat()
    hb.beat()
    assert hb.beats == 3, f"3 次调用后应 3：{hb.beats}"
    assert hb.last_ts == 1.0, f"应记录最近时间戳：{hb.last_ts}"


@_test
def test_dynamic_task_trace_records_call_path():
    """task trace：记录真实调用路径（span 名 + 身份 + 耗时）。"""
    tr = TaskTrace(clock=lambda: 0.0)
    sp = tr.start("navigate", tab_id=1, navigation_id=1)
    assert isinstance(sp, TraceSpan), "start 应返回 TraceSpan"
    assert sp.name == "navigate", f"span 名应为 navigate：{sp.name}"
    assert sp.tab_id == 1 and sp.navigation_id == 1, "span 应带身份"
    tr.end(sp, ok=True, note="http://a")
    assert tr.count() == 1, f"应归档 1 条：{tr.count()}"
    assert tr.count("navigate") == 1, "按名计数应为 1"
    assert tr.names() == ["navigate"], f"调用路径应为 [navigate]：{tr.names()}"
    assert tr.last().note == "http://a", "span 应带 note"
    assert tr.last().duration >= 0.0, "span 应有非负耗时"


@_test
def test_dynamic_task_trace_bounded():
    """task trace 有界（超上限丢最旧，不无界增长）。"""
    tr = TaskTrace(clock=lambda: 0.0, max_spans=3)
    for i in range(10):
        sp = tr.start("tick", tab_id=1, navigation_id=i)
        tr.end(sp)
    assert tr.count() == 3, f"上限 3 应只留 3 条：{tr.count()}"


@_test
def test_dynamic_worker_frame_carries_navigation_id():
    """worker 产出的帧携带 navigation_id（UI 侧门控依据）。"""
    w = DocumentProcessWorker()
    f = w.handle(DocCommand(tab_id=3, kind=DOC_CMD_NAVIGATE,
                            payload={"url": "http://x"}, seq=1,
                            navigation_id=7))
    assert f.navigation_id == 7, f"帧应携带 nav_id=7：{f.navigation_id}"
    assert f.payload.get("navigation_id") == 7, "payload 也应带 navigation_id"
    assert f.tab_id == 3, f"帧 tab_id 应为 3：{f.tab_id}"


@_test
def test_dynamic_worker_stop_frame_cancelled_with_nav():
    """stop 帧标 cancelled + 带 navigation_id（取消可被确认）。"""
    w = DocumentProcessWorker()
    w.handle(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE, seq=1,
                        navigation_id=1))
    f = w.handle(DocCommand(tab_id=1, kind=DOC_CMD_STOP, seq=2,
                            navigation_id=1))
    assert f.cancelled is True, "stop 帧应标 cancelled"
    assert f.navigation_id == 1, f"stop 帧 nav_id 应为 1：{f.navigation_id}"


# ----------------------------------------------------------------------
# R7-A02 第 1 点 — scroll / resize 入口的 UI 心跳 + task trace 接线
# ----------------------------------------------------------------------

@_test
def test_static_scroll_resize_entries_wired_to_trace():
    """R7-A02 第 1 点：默认 scroll / resize 入口接 trace + 心跳。

    静态断言接线点（gui 实例化需 Tk，故静态锁源码契约）：三个入口
    （scrollbar command / wheel / resize 投递）都经
    ``_r7_a02_record`` 开 span、``_r7_a02_finish`` 收尾。
    """
    src = _read(os.path.join("toy_browser", "app", "gui.py"))
    for sym in ("_r7_a02_record", "_r7_a02_finish"):
        assert sym in src, f"gui 缺 {sym}（scroll/resize trace helper）"
    # 三个入口各自接线：定位到函数体后确认内部调用了 record/finish
    for entry in ("def handle_scroll_command", "def handle_wheel_event",
                  "def _g07_post_resize"):
        i = src.index(entry)
        body = src[i:src.index("\n    def ", i + 1)]
        assert "_r7_a02_record" in body, \
            f"{entry} 未接 task trace（缺 _r7_a02_record）"
        assert "_r7_a02_finish" in body, \
            f"{entry} 未收尾 span（缺 _r7_a02_finish）"


@_test
def test_dynamic_scroll_entry_records_trace_and_heartbeat():
    """R7-A02 第 1 点 — scrollbar command 真实调用 → 心跳推进 + 归档
    一条 ``scroll`` span（真正调用路径证据，不是"模块存在"）。"""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        from skip_support import SkipTest
        raise SkipTest("tkinter 不可用，跳过 scroll 入口接线动态断言")
    tb = _make_stub_browser()
    assert tb._r7_a02_heartbeat.beats == 0, "初始应 0 次心跳"
    tb.handle_scroll_command(0, "scroll", 2, "units")
    assert tb._r7_a02_heartbeat.beats == 1, \
        f"scroll 一次应推进 1 次心跳：{tb._r7_a02_heartbeat.beats}"
    assert tb._r7_a02_trace.count("scroll") == 1, \
        f"应归档 1 条 scroll span：{tb._r7_a02_trace.names()}"
    # wheel 入口同样接线
    tb.handle_wheel_event(0, -120)
    assert tb._r7_a02_heartbeat.beats == 2, "wheel 再推进 1 次"
    assert tb._r7_a02_trace.count("scroll") == 2, \
        f"wheel 也应归档 scroll span：{tb._r7_a02_trace.names()}"


@_test
def test_dynamic_resize_entry_records_trace_and_heartbeat():
    """R7-A02 第 1 点 — resize 投递真实调用 → 心跳推进 + 归档一条
    ``resize`` span。

    **不冒充通过**：span 只证明"resize 命令从 UI 入口真实发出"，
    **不**证明重排在多核侧执行（后者由 :meth:`_r7_a02_doc_mode`
    依真实接线判定）。本断言只锁"入口被调用"这一可测事实。
    """
    try:
        import tkinter  # noqa: F401
    except ImportError:
        from skip_support import SkipTest
        raise SkipTest("tkinter 不可用，跳过 resize 入口接线动态断言")
    tb = _make_stub_browser()
    ok = tb._g07_post_resize(tab_id=0, w=1024, h=768, seq=1)
    assert ok is True, "resize 投递应成功"
    assert tb._r7_a02_heartbeat.beats == 1, \
        f"resize 一次应推进 1 次心跳：{tb._r7_a02_heartbeat.beats}"
    assert tb._r7_a02_trace.count("resize") == 1, \
        f"应归档 1 条 resize span：{tb._r7_a02_trace.names()}"
    span = tb._r7_a02_trace.last()
    assert span.tab_id == 0, f"span 应带 tab_id：{span.tab_id}"


@_test
def test_dynamic_trace_helper_nav_id_from_current_tab():
    """R7-A02 第 1 点 — scroll / resize 不新建文档身份：span 的
    navigation_id 取当前 tab 已登记的 nav_id（与 worker 同源），
    未导航过的 tab 为 0（不冒充新导航）。"""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        from skip_support import SkipTest
        raise SkipTest("tkinter 不可用，跳过 nav_id 口径动态断言")
    tb = _make_stub_browser()
    # 未导航过 → nav_id 0
    tb.handle_scroll_command(0, "scroll", 1, "units")
    assert tb._r7_a02_trace.last().navigation_id == 0, \
        "未导航的 tab scroll span nav_id 应为 0"
    # 登记 nav_id=3（模拟导航后）→ scroll span 沿用 3
    tb._r7_a02_nav_id[0] = 3
    tb.handle_scroll_command(0, "scroll", 1, "units")
    assert tb._r7_a02_trace.last().navigation_id == 3, \
        f"导航后 scroll span 应沿用 nav_id=3：" \
        f"{tb._r7_a02_trace.last().navigation_id}"


@_test
def test_dynamic_drop_tab_clears_navigation_identity():
    """关标签清 navigation_id（不泄漏已关闭标签身份）。"""
    w = DocumentProcessWorker()
    w.handle(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE, seq=1,
                        navigation_id=5))
    assert w.ipc_identity(1)["navigation_id"] == 5
    assert w.drop_tab(1) == 1, "drop_tab 应返回 1（存在）"
    assert w.ipc_identity(1)["navigation_id"] == 0, \
        "drop 后 nav_id 应归零"
    assert w.drop_tab(1) == 0, "重复 drop 应返回 0"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {name}: {e!r}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
