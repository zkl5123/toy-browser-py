"""R7-A02 第 5 点 — 慢页 / 坏脚本隔离 + 关窗"先停任务再销毁 Tk"回归。

被测：``toy_browser/app/document_process.py``（坏脚本隔离：单 tab
文档工作失败转错误帧、不传播、不杀 worker 进程）+ ``toy_browser/
app/gui.py``（慢页隔离判定层 + 关窗顺序证据 + 真实 spawn 回收）。

round7 实施方案 R7-A02 第 5 点原文：

> 慢页与坏脚本不影响其他标签；关闭窗口先停止任务/回收子进程，再销毁
> Tk root。

两层设计（"不冒充通过"）：

* 静态层（无 OS/wgpu/tkinter 即可导入）——源码契约字符串断言；
* 动态层——真实 worker handle + 真实 spawn 池回收 + gui 接线（gui
  实例化需 Tk 的用例 tkinter 门禁，缺失时 SKIP 而非伪装通过）。

**不冒充通过**（如实边界）：

* 慢页隔离只做**判定层**：过期未产帧的 tab 的迟到帧被隔离、其他
  tab 不受影响；默认 ``timeout=0`` **不启用**（不误伤真实 spawn 的
  异步帧延迟）。"慢页期间 UI 心跳持续 / 输入 P95 ≤ 100ms"需要事件
  循环 tick + 非阻塞 worker，**未实测**（留活显示批次）。
* 坏脚本隔离是真实接线（异常转错误帧、子进程循环不退出），但
  "任意真实坏脚本"未做全量 fuzz——本批用注入异常验证**传播被兜住**
  这一契约。
* 关窗顺序是**顺序记录 + 真实 spawn 回收断言**（进程退出、无遗留
  子进程），Tk root 销毁本身需真机窗口（活显示批次人工验证）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.document_process import (
    DocCommand, DocFrame, DocumentProcessWorker,
    DOC_CMD_NAVIGATE, DOC_CMD_RESIZE, DOC_CMD_SCROLL,
    spawn_doc_process_server,
)
from toy_browser.scripting.script_ipc import validate_frame

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read(path):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(base, path), encoding="utf-8").read()


def _nav_html(tab_id, url="http://x/", seq=1, navigation_id=0, html="<p>ok</p>"):
    return DocCommand(tab_id=tab_id, kind=DOC_CMD_NAVIGATE,
                      payload={"url": url, "html": html, "w": 400, "h": 300},
                      seq=seq, navigation_id=navigation_id)


def _make_stub_browser_v5():
    """带第 5 点字段的 TabBrowser stub（慢页隔离 / 关窗顺序动态层）。"""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        from skip_support import SkipTest
        raise SkipTest("tkinter 不可用，跳过第 5 点 gui 接线动态断言"
                       "（gui 模块导入需 tkinter；不冒充通过）")
    from toy_browser.app import gui as _gui
    from toy_browser.app.document_process import DocProcessServer

    cls = None
    for nm in dir(_gui):
        o = getattr(_gui, nm)
        if isinstance(o, type) and hasattr(o, "handle_scroll_command"):
            cls = o
            break
    assert cls is not None, "gui 缺 handle_scroll_command 所属类"
    tb = cls.__new__(cls)
    tb._r7_a02_heartbeat = None
    tb._r7_a02_trace = None
    tb._r7_a02_nav_id = {}
    tb._r7_a02_seq = 0
    tb._r7_a02_stale_dropped = 0
    tb._g07_doc_server = DocProcessServer()
    tb._g07_ui_closing = False
    tb._g07_close_order = []
    tb._r7_a02_coalescer = None
    tb._r7_a02_backpressure = None
    tb._r7_a02_cancel = None
    tb._r7_a02_nav_gate = None
    tb._r7_a02_tasks = {}
    tb._r7_a02_slow_tabs = set()
    tb._r7_a02_slow_tab_timeout = 0.0
    tb._r7_a02_slow_dropped = 0
    tb._destroy_named_images = lambda: None
    for name in ("_r7_a02_submit", "_r7_a02_note_task",
                 "_r7_a02_isolate_slow_tabs",
                 "_r7_a02_flush_commands", "_r7_a02_discard_pending",
                 "_g07_post_nav", "_g07_post_resize",
                 "_g07_drain_frames", "_g07_doc_server_shutdown",
                 "_on_window_close"):
        setattr(tb, name, getattr(cls, name).__get__(tb))
    return tb


# ----------------------------------------------------------------------
# 坏脚本隔离（document_process.py）
# ----------------------------------------------------------------------

@_test
def test_bad_script_error_frame_isolated_to_its_tab():
    """坏脚本 / 渲染失败 → 该 tab 错误帧（ok=False + script 类别 +
    有限摘要），**不传播**；其他 tab 照常渲染（不影响其他标签）。"""
    from unittest import mock
    from toy_browser.app import document_renderer as rend

    w = DocumentProcessWorker()
    # tab 1：注入渲染异常（坏脚本等价物）
    with mock.patch.object(rend, "render_document_state",
                           side_effect=RuntimeError("bad script boom")):
        f1 = w.handle(_nav_html(1, navigation_id=1))
    assert f1.ok is False, "坏 tab 应得错误帧（不是异常传播）"
    assert f1.error_category == "script", \
        f"错误类别应为 script：{f1.error_category!r}"
    assert "RuntimeError" in f1.payload.get("error_category", ""), \
        f"摘要应含异常类别：{f1.payload.get('error_category')!r}"
    assert len(f1.payload.get("error_category", "")) <= 128, "摘要必须有限"
    # 过父进程可见面校验（白名单键 + 有限字符串）
    reject = validate_frame(f1.tab_id, f1.seq, f1.ok, f1.error_category,
                            f1.payload, f1.navigation_id)
    assert reject is None, f"错误帧应过 IPC 校验：{reject}"
    assert w.doc_error_for(1) is not None, "应记录该 tab 错误摘要"
    # tab 2：真实渲染照常（坏 tab 不影响其他标签）
    f2 = w.handle(_nav_html(2, navigation_id=1))
    assert f2.ok is True, "好 tab 应正常渲染"
    assert w.doc_error_for(2) is None
    assert w.has_done_doc_work() is True
    assert w.doc_state_for(2) is not None, "好 tab 应持有 DocumentState"


@_test
def test_bad_script_restate_error_frame_and_recovery():
    """resize 重排失败 → 该 tab 错误帧；成功后错误摘要被清除
    （不把上一次失败粘成永久坏 tab）。"""
    from unittest import mock
    from toy_browser.app import document_renderer as rend

    w = DocumentProcessWorker()
    w.handle(_nav_html(1, navigation_id=1))
    with mock.patch.object(rend, "render_document_state_restate",
                           side_effect=ValueError("layout blew up")):
        f = w.handle(DocCommand(tab_id=1, kind=DOC_CMD_RESIZE,
                                payload={"w": 800, "h": 600}, seq=2,
                                navigation_id=1))
    assert f.ok is False and f.error_category == "script", \
        "resize 失败应转错误帧"
    assert "layout blew up" in f.payload.get("error_category", "")
    # 恢复：下一次成功 → 摘要清除（坏 tab 不是终身判决）
    f2 = w.handle(DocCommand(tab_id=1, kind=DOC_CMD_RESIZE,
                             payload={"w": 800, "h": 600}, seq=3,
                             navigation_id=1))
    assert f2.ok is True, "恢复后应成功"
    assert w.doc_error_for(1) is None, "成功后应清错误摘要"


@_test
def test_drop_tab_clears_doc_error():
    """关标签清错误摘要（不泄漏已关闭标签诊断状态）。"""
    from unittest import mock
    from toy_browser.app import document_renderer as rend

    w = DocumentProcessWorker()
    with mock.patch.object(rend, "render_document_state",
                           side_effect=RuntimeError("x")):
        w.handle(_nav_html(1, navigation_id=1))
    assert w.doc_error_for(1) is not None
    assert w.drop_tab(1) == 1
    assert w.doc_error_for(1) is None, "drop 后错误摘要应清空"


@_test
def test_static_child_loop_survives_command_error():
    """R7-A02 第 5 点（静态）：子进程循环里 handle 异常转错误帧并
    **continue**（不 break、不退出子进程——旧行为一异常就退出，
    整池挂，连累其他标签）。"""
    src = _read(os.path.join("toy_browser", "app", "document_process.py"))
    i = src.index("def _doc_process_child")
    body = src[i:src.index("\nclass DocProcessServer")]
    assert "continue" in body, "子进程循环缺 continue（异常会退出子进程）"
    assert "DocFrame(" in body, "子进程循环应把异常转成错误帧"
    # 旧行为（异常后 break 退出）不得回来：从 handle 调用到第一个
    # continue 之间不得出现**独立的 break 语句**（注释里的字样不算）。
    import re as _re
    seg = body[body.index("frame = worker.handle(cmd)"):]
    block = seg[:seg.index("continue")]
    assert _re.search(r"^\s*break\s*$", block, _re.M) is None, \
        "异常分支在 continue 前不得 break（= 退出子进程）"


@_test
def test_spawn_pool_close_no_orphan_child():
    """真实 spawn 池：shutdown 后无遗留子进程（进程退出、is_spawned
    为 False）——方案验收"关闭后无遗留子进程"的可测子集。"""
    srv = spawn_doc_process_server()
    try:
        if srv.is_spawned():
            srv.post(_nav_html(1, seq=1))
            proc = srv._proc
            srv.shutdown()
            assert not proc.is_alive(), "shutdown 后子进程不得存活"
            assert proc.exitcode is not None, "子进程应有退出码"
            assert srv.is_spawned() is False, "shutdown 后不得仍报 spawned"
        else:
            # 回退态（spawn 不可用）：无子进程可遗留
            assert srv._proc is None or not srv._proc.is_alive()
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass


# ----------------------------------------------------------------------
# 慢页隔离（gui.py 判定层）
# ----------------------------------------------------------------------

@_test
def test_dynamic_slow_tab_isolated_others_unaffected():
    """慢页隔离：过期未产帧的 tab 被**标记**慢页，其后该 tab 的
    迟到帧被隔离；其他 tab 帧放行；timeout=0（默认）不隔离（不误伤
    真实 spawn 异步延迟）。"""
    tb = _make_stub_browser_v5()
    # 默认未启用 → 不过滤
    frames = [DocFrame(tab_id=1, seq=1, ok=True, navigation_id=1)]
    assert tb._r7_a02_isolate_slow_tabs(frames, now=1e9) == frames, \
        "timeout=0 时不得隔离（默认不启用）"
    # 启用隔离：tab 1 任务过期未产帧（卡住）；tab 2 本轮正常回帧
    tb._r7_a02_slow_tab_timeout = 0.05
    tb._r7_a02_tasks[(1, 1)] = 100.0        # tab 1 旧任务（会过期）
    tb._r7_a02_tasks[(2, 1)] = 100.0        # tab 2 旧任务（本轮帧来了）
    f2a = DocFrame(tab_id=2, seq=1, ok=True, navigation_id=1)
    out0 = tb._r7_a02_isolate_slow_tabs([f2a], now=100.2)
    assert out0 == [f2a], "tab 2 帧到达 = 任务完成，应放行"
    assert 1 in tb._r7_a02_slow_tabs, "tab 1 应被标记慢页（过期未产帧）"
    assert 2 not in tb._r7_a02_slow_tabs, "tab 2 回了帧，不算慢页"
    # 下一轮：慢 tab 1 的迟到帧被隔离；好 tab 2 新帧仍放行
    f1 = DocFrame(tab_id=1, seq=1, ok=True, navigation_id=1)
    f2 = DocFrame(tab_id=2, seq=1, ok=True, navigation_id=1)
    out = tb._r7_a02_isolate_slow_tabs([f1, f2], now=100.25)
    assert f2 in out and f1 not in out, \
        "慢 tab 1 的迟到帧应被隔离，好 tab 2 帧应放行"
    assert tb._r7_a02_slow_dropped == 1
    # 新导航解除隔离（慢 tab 给新页一次重新做人）
    tb._r7_a02_note_task(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE,
                                    payload={}, seq=9, navigation_id=2))
    assert 1 not in tb._r7_a02_slow_tabs, "新导航应解除该 tab 慢页标记"
    out2 = tb._r7_a02_isolate_slow_tabs(
        [DocFrame(tab_id=1, seq=2, ok=True, navigation_id=2)], now=100.3)
    assert len(out2) == 1, "解除隔离后该 tab 帧应放行"


@_test
def test_dynamic_close_order_stops_tasks_before_destroy():
    """关窗顺序：``stop_tasks`` → ``server_shutdown`` → ``destroy_
    images`` → ``root_destroy`` → ``root_quit``——**先停任务 /
    回收子进程，再销毁 Tk root**（方案原文；顺序是可测证据）。"""
    tb = _make_stub_browser_v5()

    class _FakeRoot:
        def __init__(self):
            self.destroyed = 0
            self.quit_calls = 0

        def destroy(self):
            self.destroyed += 1

        def quit(self):
            self.quit_calls += 1

    tb.root = _FakeRoot()
    tb._on_window_close()
    assert tb._g07_close_order == ["stop_tasks", "server_shutdown",
                                   "destroy_images", "root_destroy",
                                   "root_quit"], \
        f"关窗顺序不符（先停任务/回收子进程再销毁 Tk）：{tb._g07_close_order}"
    assert tb.root.destroyed == 1 and tb.root.quit_calls == 1, \
        "root 应被 destroy + quit"
    # 重复关闭幂等：root 仍被销毁（防泄漏），不崩
    tb._on_window_close()
    assert tb.root.destroyed == 2, "重复关闭仍应销毁 root（幂等防泄漏）"


@_test
def test_dynamic_close_window_real_spawn_no_orphan():
    """真实 spawn 池经 gui 关窗路径（_g07_doc_server_shutdown）后
    无遗留子进程（方案验收"关闭后无遗留子进程"的 gui 入口侧断言）。"""
    tb = _make_stub_browser_v5()
    tb._g07_doc_server = spawn_doc_process_server()
    tb._g07_post_nav(tab_id=1, url="http://a", seq=1)
    if not tb._g07_doc_server.is_spawned():
        return  # spawn 不可用环境：回退态无子进程可遗留
    proc = tb._g07_doc_server._proc
    tb._g07_doc_server_shutdown()
    assert not proc.is_alive(), "关窗后子进程不得存活"
    assert proc.exitcode is not None, "子进程应已退出"
    assert tb._g07_close_order[:2] == ["stop_tasks", "server_shutdown"], \
        f"关窗应先停任务再回收子进程：{tb._g07_close_order}"


@_test
def test_static_gui_wires_slow_isolation_and_close_order():
    """R7-A02 第 5 点（静态）：gui 接慢页隔离 + 关窗顺序证据 +
    坏脚本隔离的诊断出口（按函数体断言，不是"文件里有符号"）。"""
    src = _read(os.path.join("toy_browser", "app", "gui.py"))
    for entry, sym in (("def _g07_drain_frames", "_r7_a02_isolate_slow_tabs"),
                       ("def _on_window_close", "_g07_close_order"),
                       ("def _g07_doc_server_shutdown", "stop_tasks")):
        i = src.index(entry)
        body = src[i:src.index("\n    def ", i + 1)]
        assert sym in body, f"{entry} 未接 {sym}（第 5 点）"


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
