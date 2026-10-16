"""G08 step 3 + G07 step 3 — 可取消网络工作单元（transport）回归。

覆盖 CancellableTransport / TransportResult：
- 同步 run：成功 / 失败结果携带 C01 版本
- 异步 run + cancel：Stop 立即反馈（不阻塞主线程），工作线程在
  可取消点收尾（不强杀线程）
- 取消后结果 cancelled=True；过期 / 已取消结果经 NavigationState.
  accept_result 被丢弃（A 的完成事件不覆盖 B）
- 可取消 sleep 在取消后立即返回（慢服务器可测）
- 结果超时等待返回 None

不依赖真实网络 / 活显示：注入分片 sleep 的 fake fetch 回调。
"""
from __future__ import annotations

import threading
import time

from toy_browser.app.transport import (
    CancellableTransport, TransportResult, _cooperative_sleep,
)
from toy_browser.app.navigation_state import NavigationState
from toy_browser.app.document_state import DocumentVersion, \
    reset_version_sequences


TESTS = []


def _fake_fetch_fast(ok=True, err_cat="", final="", detail=""):
    def fetch(url, transport):
        return ok, err_cat, final or url, detail
    return fetch


def test_transport_sync_success_carries_version():
    """同步 run：成功结果携带 C01 document_id/frame_id/nav_epoch。"""
    transport = CancellableTransport(document_id=5, frame_id=7,
                                     nav_epoch=3)
    transport.run(_fake_fetch_fast(ok=True, final="https://x/final"),
                  "https://x")
    res = transport.wait()
    assert res is not None
    assert res.ok
    assert res.document_id == 5 and res.frame_id == 7 and res.nav_epoch == 3
    assert res.final_url == "https://x/final"
    assert not res.cancelled


def test_transport_sync_failure_carries_category():
    transport = CancellableTransport(document_id=1, frame_id=1,
                                     nav_epoch=1)
    transport.run(_fake_fetch_fast(ok=False, err_cat="timeout"),
                  "https://t")
    res = transport.wait()
    assert res.ok is False and res.error_category == "timeout"


def test_transport_cancel_before_run_is_immediate():
    """取消标志先置位：run 立即产出 cancelled 结果（UI 立即反馈）。"""
    transport = CancellableTransport(document_id=1, frame_id=1, nav_epoch=1)
    transport.cancel()
    transport.run(_fake_fetch_fast(ok=True), "https://x")
    res = transport.wait()
    assert res.cancelled and res.error_category == "cancelled"
    assert res.ok is False


def test_transport_cooperative_sleep_returns_on_cancel():
    """可取消 sleep：取消后立即返回 False（慢服务器可测，不强杀线程）。"""
    transport = CancellableTransport()
    transport._cancelled = True  # 直接置位（模拟取消）
    t0 = time.monotonic()
    full = _cooperative_sleep(5.0, transport, slice_s=0.05)
    elapsed = time.monotonic() - t0
    assert full is False
    assert elapsed < 1.0, "cancel must return the sleep promptly"


def test_transport_async_cancel_then_wait_no_thread_kill():
    """异步 run + cancel：主线程立即拿到取消结果（不阻塞）；
    工作线程自行收尾（不强杀线程，G08 step 3）。"""
    transport = CancellableTransport(document_id=2, frame_id=2,
                                     nav_epoch=1,
                                     thread_name="toy-test")
    # 一个"慢" fetch：分片 sleep + 检查取消（模拟慢服务器）
    def slow_fetch(url, t):
        full = _cooperative_sleep(3.0, t, slice_s=0.02)
        if not full:
            return False, "cancelled", "", "user stopped"
        return True, "", url, ""

    transport.run(slow_fetch, "https://slow", async_=True)
    # 立即取消（主线程不等待 3s）
    transport.cancel()
    res = transport.wait(timeout=2.0)
    assert res is not None
    assert res.cancelled, "cancelled task must report cancelled"
    assert res.ok is False
    # 工作线程已收尾（不强杀：等它自然结束）
    assert transport.finished
    if transport._thread is not None:
        transport._thread.join(timeout=2.0)


def test_transport_cancelled_result_rejected_by_navstate():
    """取消的 transport 结果经 NavigationState 门控被丢弃：
    cancel_request 后 accept_result（当前 epoch、成功）被拒绝，
    状态停在 CANCELLED。"""
    reset_version_sequences()
    nav = NavigationState()
    nav.start_navigation("https://a", epoch=1)
    nav.cancel_request()  # G08 step 3 — 逻辑取消（UI 立即反馈）
    assert nav.state == "cancelled"
    # 迟到的成功结果（当前 epoch）→ 因已取消被丢弃（过期完成事件无效）
    assert not nav.accept_result(1, ok=True, final_url="https://a/2")
    assert nav.state == "cancelled"


def test_transport_stale_result_rejected_by_document_version():
    """过期帧结果（document_id/frame_id 旧）经 C01 门控被丢弃。"""
    reset_version_sequences()
    version = DocumentVersion()
    version.new_document()  # document_id=1, frame_id=1
    old = TransportResult(ok=True, document_id=1, frame_id=1, nav_epoch=1)
    version.bump_frame()  # frame_id -> 2：旧帧 stale
    assert old.is_stale(version)
    newer = TransportResult(ok=True, document_id=1, frame_id=2,
                            nav_epoch=1)
    assert not newer.is_stale(version)


def test_transport_async_timeout_returns_none():
    """wait 超时（工作线程未完成）返回 None。"""
    transport = CancellableTransport(thread_name="toy-timeout")
    def _hold(url, t):
        _cooperative_sleep(2.0, t, slice_s=0.05)
        return True, "", url, ""
    transport.run(_hold, "https://hold", async_=True)
    got = transport.wait(timeout=0.05)
    assert got is None  # 尚未完成
    transport.cancel()
    res = transport.wait(timeout=2.0)
    assert res is not None


def test_transport_wait_returns_success_after_async_done():
    transport = CancellableTransport(document_id=3, frame_id=3,
                                     nav_epoch=1, thread_name="toy-ok")
    transport.run(_fake_fetch_fast(ok=True), "https://ok", async_=True)
    res = transport.wait(timeout=2.0)
    assert res is not None and res.ok
    assert transport.finished


TESTS.append(("test_transport_sync_success_carries_version",
              test_transport_sync_success_carries_version))
TESTS.append(("test_transport_sync_failure_carries_category",
              test_transport_sync_failure_carries_category))
TESTS.append(("test_transport_cancel_before_run_is_immediate",
              test_transport_cancel_before_run_is_immediate))
TESTS.append(("test_transport_cooperative_sleep_returns_on_cancel",
              test_transport_cooperative_sleep_returns_on_cancel))
TESTS.append(("test_transport_async_cancel_then_wait_no_thread_kill",
              test_transport_async_cancel_then_wait_no_thread_kill))
TESTS.append(("test_transport_cancelled_result_rejected_by_navstate",
              test_transport_cancelled_result_rejected_by_navstate))
TESTS.append(("test_transport_stale_result_rejected_by_document_version",
              test_transport_stale_result_rejected_by_document_version))
TESTS.append(("test_transport_async_timeout_returns_none",
              test_transport_async_timeout_returns_none))
TESTS.append(("test_transport_wait_returns_success_after_async_done",
              test_transport_wait_returns_success_after_async_done))
