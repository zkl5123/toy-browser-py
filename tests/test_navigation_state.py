"""G08 — 导航状态、停止与错误回归（round5 docs 批次 3）。

覆盖 NavigationState：
- 状态机转移（idle → loading → complete / failed / cancelled）
- UI 映射（busy / stop_button_visible / ui_label 区分 Loaded/Failed/Stopped）
- Stop 取消（cancel_request 只在 busy 时生效；已终态不重复取消）
- 结果丢弃（旧 epoch / 已取消的 async 结果不覆盖新导航）
- 错误分类描述（DNS / 超时 / HTTP / 重定向 / 无效 URL）
"""
from __future__ import annotations

from toy_browser.app.navigation_state import (
    NavigationState, IDLE, LOADING, INTERACTIVE, COMPLETE, FAILED,
    CANCELLED, describe_error, ERROR_CATEGORY_LABELS,
)


TESTS = []


def test_g08_state_machine_happy_path():
    """idle → loading → interactive → complete（成功加载）。"""
    ns = NavigationState()
    assert ns.state == IDLE
    ns.start_navigation("https://example.com", epoch=1)
    assert ns.state == LOADING and ns.is_busy
    ns.mark_interactive()
    assert ns.state == INTERACTIVE and ns.is_busy
    ns.complete(final_url="https://example.com/2")
    assert ns.state == COMPLETE
    assert not ns.is_busy
    assert ns.final_url == "https://example.com/2"
    assert ns.ui_label() == "Loaded"
    # 转移日志：三次转移
    assert len(ns.transitions) == 3


def test_g08_state_machine_failure():
    """idle → loading → failed（错误分类被记录，可重试）。"""
    ns = NavigationState()
    ns.start_navigation("https://bad.host", epoch=1)
    ns.fail("dns")
    assert ns.state == FAILED
    assert ns.error_category == "dns"
    assert ns.can_retry()
    assert "DNS" in describe_error(ns)
    assert ns.ui_label() == "Failed"


def test_g08_stop_only_when_busy():
    """Stop 只在 LOADING / INTERACTIVE 时生效；终态 / idle 不取消。"""
    ns = NavigationState()
    # idle：无进行中导航
    ns.cancel_request()
    assert ns.state == IDLE and not ns.cancel_requested
    # loading：取消
    ns.start_navigation("https://x", epoch=1)
    ns.cancel_request()
    assert ns.state == CANCELLED and ns.cancel_requested
    assert ns.ui_label() == "Stopped"
    # 已取消：再取消无副作用
    ns.cancel_request()
    assert ns.state == CANCELLED
    # complete 后取消：无进行中导航
    ns.start_navigation("https://y", epoch=2)
    ns.complete()
    ns.cancel_request()
    assert ns.state == COMPLETE  # 不变


def test_g08_cancel_marks_cancel_requested():
    """cancel() 直接调用也置 cancel_requested（transport 收尾标志）。"""
    ns = NavigationState()
    ns.start_navigation("https://x", epoch=1)
    ns.cancel()
    assert ns.cancel_requested and ns.state == CANCELLED


def test_g08_stale_result_dropped():
    """旧 epoch 的加载结果不覆盖新导航（A 不写入 B）。"""
    ns = NavigationState()
    ns.start_navigation("https://a", epoch=1)
    ns.start_navigation("https://b", epoch=2)
    # epoch=1 的结果（属于已废弃的 A 导航）→ 丢弃
    assert not ns.accept_result(1, ok=True, final_url="https://a/2")
    # epoch=2 的结果（当前导航 B）→ 应用
    assert ns.accept_result(2, ok=True, final_url="https://b/2")
    assert ns.state == COMPLETE
    assert ns.final_url == "https://b/2"


def test_g08_cancelled_result_rejected():
    """已取消的导航，其异步结果不再被应用。"""
    ns = NavigationState()
    ns.start_navigation("https://x", epoch=1)
    ns.cancel_request()
    # 取消后 transport 迟到的结果 → 丢弃
    assert not ns.accept_result(1, ok=True)
    assert ns.state == CANCELLED


def test_g08_failed_result_updates_state():
    """失败结果 → FAILED + 错误分类。"""
    ns = NavigationState()
    ns.start_navigation("https://timeout.host", epoch=1)
    assert ns.accept_result(1, ok=False, error_category="timeout")
    assert ns.state == FAILED and ns.error_category == "timeout"
    assert "超时" in describe_error(ns)


def test_g08_ui_label_per_state():
    """各状态 UI 文本不同（完成/失败/取消不再统一写 Loaded）。"""
    ns = NavigationState()
    assert ns.ui_label() == "Ready"
    ns.start_navigation("https://x", 1)
    assert ns.ui_label() == "Loading"
    ns.mark_interactive()
    assert ns.ui_label() == "Interactive"
    ns.complete()
    assert ns.ui_label() == "Loaded"
    # 失败
    ns.start_navigation("https://y", 2)
    ns.fail("http")
    assert ns.ui_label() == "Failed"
    # 取消
    ns.start_navigation("https://z", 3)
    ns.cancel_request()
    assert ns.ui_label() == "Stopped"


def test_g08_error_category_labels():
    """错误分类 → 中文摘要词表完整。"""
    for cat in ("dns", "connect", "timeout", "http", "tls",
               "redirect", "bad_url", "script", ""):
        assert cat in ERROR_CATEGORY_LABELS, f"missing label for {cat!r}"


def test_g08_reset_returns_to_idle():
    ns = NavigationState()
    ns.start_navigation("https://x", 1)
    ns.complete()
    ns.reset()
    assert ns.state == IDLE and ns.epoch == 0
    assert ns.current_url == "" and ns.transitions == []


TESTS.append(("test_g08_state_machine_happy_path",
              test_g08_state_machine_happy_path))
TESTS.append(("test_g08_state_machine_failure",
              test_g08_state_machine_failure))
TESTS.append(("test_g08_stop_only_when_busy",
              test_g08_stop_only_when_busy))
TESTS.append(("test_g08_cancel_marks_cancel_requested",
              test_g08_cancel_marks_cancel_requested))
TESTS.append(("test_g08_stale_result_dropped",
              test_g08_stale_result_dropped))
TESTS.append(("test_g08_cancelled_result_rejected",
              test_g08_cancelled_result_rejected))
TESTS.append(("test_g08_failed_result_updates_state",
              test_g08_failed_result_updates_state))
TESTS.append(("test_g08_ui_label_per_state",
              test_g08_ui_label_per_state))
TESTS.append(("test_g08_error_category_labels",
              test_g08_error_category_labels))
TESTS.append(("test_g08_reset_returns_to_idle",
              test_g08_reset_returns_to_idle))
