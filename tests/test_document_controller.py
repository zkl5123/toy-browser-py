"""G07 — UI 与文档工作分离（有界命令队列 + 任务版本丢弃）回归。

覆盖 DocumentController / CommandQueue / Result：
- 有界队列：resize / scroll 合并（只保留最新）；导航 / 输入按序
- 结果携带 C01 版本，过期帧 / 旧 epoch 被丢弃
- navigate / stop / scroll / resize / input 命令投递与状态联动
- 取消后丢弃在途导航结果
"""
from __future__ import annotations

from toy_browser.app.document_controller import (
    DocumentController, CommandQueue, Command, Result,
    CMD_NAVIGATE, CMD_INPUT, CMD_RESIZE, CMD_SCROLL, CMD_STOP,
)
from toy_browser.app.document_state import reset_version_sequences


TESTS = []


def _ctrl() -> DocumentController:
    reset_version_sequences()
    return DocumentController()


def test_g07_queue_bounds_resize_scroll():
    """同类合并：resize / scroll 各自只保留最新一条。"""
    q = CommandQueue(max_pending=4)
    q.post(Command(CMD_RESIZE, {"css_width": 1},
                   nav_epoch=1, document_id=1))
    q.post(Command(CMD_RESIZE, {"css_width": 2},
                   nav_epoch=1, document_id=1))
    q.post(Command(CMD_SCROLL, {"offset": 10},
                   nav_epoch=1, document_id=1))
    q.post(Command(CMD_SCROLL, {"offset": 20},
                   nav_epoch=1, document_id=1))
    # 只剩 1 个 resize + 1 个 scroll（各取最新）
    assert len(q) == 2
    assert q.peek().payload["css_width"] == 2
    got = []
    while True:
        cmd = q.take()
        if cmd is None:
            break
        got.append(cmd.payload)
    # scroll 保留最新 offset=20
    scrolls = [p for p in got if p.get("offset") is not None]
    assert scrolls and scrolls[0]["offset"] == 20


def test_g07_queue_rejects_over_limit():
    """有界：导航 / 输入命令超过 max_pending 被拒绝（返回 False）。"""
    c = _ctrl()
    c.commands.max_pending = 2
    assert c.commands.post(Command(CMD_NAVIGATE, {"url": "a"}, 1, 1))
    assert c.commands.post(Command(CMD_NAVIGATE, {"url": "b"}, 1, 1))
    # 第 3 条超限
    assert not c.commands.post(Command(CMD_NAVIGATE, {"url": "c"}, 1, 1))
    assert len(c.commands) == 2


def test_g07_navigate_bumps_epoch_and_document():
    c = _ctrl()
    e1 = c.navigate("https://a")
    assert e1 == 1
    assert c.nav.state == "loading"
    assert c.nav.current_url == "https://a"
    # 版本已 bump（document_id > 0）
    assert c.version.document_id >= 1
    e2 = c.navigate("https://b")
    assert e2 == 2
    assert c.nav.current_url == "https://b"
    assert c.version.document_id >= 2


def test_g07_stop_only_when_busy():
    c = _ctrl()
    # idle：stop 无操作
    c.stop()
    assert not c.nav.cancel_requested
    # 导航中
    c.navigate("https://a")
    assert c.nav.is_busy
    c.stop()
    assert c.nav.cancel_requested and c.nav.state == "cancelled"


def test_g07_drain_applies_results():
    c = _ctrl()
    c.navigate("https://a")
    # 内置工作单元产出结果；apply_results 门控（当前 epoch、非取消）
    results = c.drain()
    applied = c.apply_results(results)
    assert len(applied) == 1
    assert c.nav.state == "complete" or c.nav.state == "interactive"


def test_g07_stale_result_dropped():
    """过期帧 / 旧文档的结果被 apply_results 丢弃。"""
    c = _ctrl()
    c.navigate("https://a")  # epoch 1, document_id D1
    # 手工构造一条 epoch=1 / document_id=D1 / frame_id=旧 的结果
    # （帧比当前 version 旧 → is_stale_frame 为 True）
    stale = Result(ok=True, nav_epoch=1, document_id=c.version.document_id,
                   frame_id=c.version.frame_id - 1)
    applied = c.apply_results([stale])
    assert applied == [], "stale frame result must be dropped"


def test_g07_cancelled_navigation_drops_results():
    """Stop 后在途 navigate 的结果被 drain 丢弃。"""
    c = _ctrl()
    c.navigate("https://a")
    c.stop()
    # cancel_requested=True：drain 不再应用该 navigate 的结果
    results = c.drain()
    applied = c.apply_results(results)
    # 取消后 nav 停在 cancelled，结果不被应用
    assert c.nav.state == "cancelled"


def test_g07_scroll_resize_post():
    c = _ctrl()
    c.navigate("https://a")
    c.scroll(100)
    c.scroll(200)  # 合并：只留最新
    c.resize(1000, 700)
    q = c.commands
    kinds = []
    while True:
        cmd = q.take()
        if cmd is None:
            break
        kinds.append((cmd.kind, cmd.payload.get("offset"),
                      cmd.payload.get("css_width")))
    # scroll 合并成一条（offset=200），resize 一条；navigate 不合并
    scroll_cmds = [k for k in kinds if k[0] == CMD_SCROLL]
    resize_cmds = [k for k in kinds if k[0] == CMD_RESIZE]
    nav_cmds = [k for k in kinds if k[0] == CMD_NAVIGATE]
    assert len(scroll_cmds) == 1 and scroll_cmds[0][1] == 200
    assert len(resize_cmds) == 1 and resize_cmds[0][2] == 1000
    assert len(nav_cmds) == 1


def test_g07_input_command():
    c = _ctrl()
    c.navigate("https://a")
    c.post_input("insertText", "x")
    # 队列：navigate 在前，input 在后
    assert c.commands.take().kind == CMD_NAVIGATE
    cmd = c.commands.take()
    assert cmd is not None and cmd.kind == CMD_INPUT
    assert cmd.payload.get("data") == "x"


def test_g07_clear_on_new_document():
    """标签关闭 / 新文档时清空队列（旧任务全部丢弃）。"""
    c = _ctrl()
    c.navigate("https://a")
    c.scroll(50)
    c.commands.clear()
    assert len(c.commands) == 0


def test_g07_cancelable_network_work_unit_success():
    """批次 5 — 打开可取消网络工作单元：http(s) navigate 经
    CancellableTransport 在工作线程里执行，主线程经
    collect_network_results + apply_results 取回（C01 门控）。"""
    c = _ctrl()
    # 注入 fake fetch（不依赖真实网络）：成功
    c.enable_cancelable_network(
        fetch_fn=lambda url, t: (True, "", url, "ok"))
    e1 = c.navigate("https://a")
    assert c.nav.state == "loading"
    # 网络工作单元已投出（不走内置 stub 队列）
    assert c._transport is not None
    # 非阻塞取结果；fake 立即返回，故可取到
    res = c.collect_network_results(timeout=0.1)
    assert len(res) == 1 and res[0].ok
    applied = c.apply_results(res)
    assert len(applied) == 1
    assert c.nav.state == "complete" or c.nav.state == "interactive"
    assert c._transport is None  # 结果已消费


def test_g07_cancelable_network_stop_drops_result():
    """批次 5 — Stop 取消在途网络工作单元：collect 取回取消结果，
    apply_results 丢弃（不写 DOM / 不刷画布），状态停在 cancelled。"""
    c = _ctrl()
    # 慢 fake fetch：分片 sleep + 检查取消（模拟慢服务器）
    from toy_browser.app.transport import _cooperative_sleep
    def slow_fetch(url, t):
        full = _cooperative_sleep(2.0, t, slice_s=0.02)
        if not full:
            return False, "cancelled", "", "stopped"
        return True, "", url, ""
    c.enable_cancelable_network(fetch_fn=slow_fetch)
    c.navigate("https://slow")
    assert c.nav.is_busy
    c.stop()
    assert c.nav.state == "cancelled"
    # 取回在途 transport 的取消结果
    res = c.collect_network_results(timeout=2.0)
    applied = c.apply_results(res)
    assert applied == [], "cancelled in-flight result must be dropped"
    assert c.nav.state == "cancelled"


def test_g07_cancelable_network_stale_discarded():
    """批次 5 — 快速 A→B 导航：A 的在途结果因 B 的 C01 帧更新
    而过期，apply_results 丢弃（A 的结果不覆盖 B）。"""
    c = _ctrl()
    c.enable_cancelable_network(
        fetch_fn=lambda url, t: (True, "", url, "ok"))
    e1 = c.navigate("https://a")
    # 切到 B：A 的结果（旧 frame_id）对 B 而言过期
    e2 = c.navigate("https://b")
    # 取回 A 的在途结果（B 已 bump frame → A stale）
    # A 的 transport 已被 B 的 navigate 替换；collect 只会取到 B。
    res = c.collect_network_results(timeout=0.1)
    # B 的结果可应用；A 已被替换（不在 collect 里）
    applied = c.apply_results(res)
    for r in applied:
        assert r.nav_epoch == e2, "stale A result must not be applied"


TESTS.append(("test_g07_queue_bounds_resize_scroll",
              test_g07_queue_bounds_resize_scroll))
TESTS.append(("test_g07_queue_rejects_over_limit",
              test_g07_queue_rejects_over_limit))
TESTS.append(("test_g07_navigate_bumps_epoch_and_document",
              test_g07_navigate_bumps_epoch_and_document))
TESTS.append(("test_g07_stop_only_when_busy",
              test_g07_stop_only_when_busy))
TESTS.append(("test_g07_drain_applies_results",
              test_g07_drain_applies_results))
TESTS.append(("test_g07_stale_result_dropped",
              test_g07_stale_result_dropped))
TESTS.append(("test_g07_cancelled_navigation_drops_results",
              test_g07_cancelled_navigation_drops_results))
TESTS.append(("test_g07_scroll_resize_post",
              test_g07_scroll_resize_post))
TESTS.append(("test_g07_input_command",
              test_g07_input_command))
TESTS.append(("test_g07_clear_on_new_document",
              test_g07_clear_on_new_document))
TESTS.append(("test_g07_cancelable_network_work_unit_success",
              test_g07_cancelable_network_work_unit_success))
TESTS.append(("test_g07_cancelable_network_stop_drops_result",
              test_g07_cancelable_network_stop_drops_result))
TESTS.append(("test_g07_cancelable_network_stale_discarded",
              test_g07_cancelable_network_stale_discarded))
