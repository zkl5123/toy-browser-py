"""G10 — 工具栏 / 状态栏 / 诊断区回归（round5 docs 批次 6）。

覆盖纯逻辑核心（不依赖 tkinter / 活显示）：
- DiagnosticsLog：record / for_document / clear_document / duration_ms
  / 文本+JSON 导出 / summary（缓存与 fallback 原因）
- toolbar_state：后退/前进按历史栈启停，刷新/停止随 G08 状态切换
  （不同加载状态按钮一致）
- status_text：悬停链接目标 > 真实加载阶段 > 失败摘要（区分
  完成/失败/取消，不统一写 Loaded；不以 HTTPS 图标代替整体安全结论）
- tab_title：文档 title，加载前临时显示 URL
- security_note：不以 HTTPS 图标代替页面整体安全结论
"""
from __future__ import annotations

import json

from toy_browser.app.diagnostics import (
    DiagnosticsLog, DiagEvent, toolbar_state, status_text, tab_title,
    security_note,
)
from toy_browser.app.navigation_state import NavigationState


TESTS = []


def _loading_nav(url="https://x", progress=0.4):
    nav = NavigationState()
    nav.start_navigation(url, epoch=1)
    nav.progress = progress
    return nav


# ----------------------------------------------------------------------
# DiagnosticsLog
# ----------------------------------------------------------------------
def test_diag_record_and_filter_by_document():
    log = DiagnosticsLog()
    log.start_document(1)
    log.record(1, "nav", ts_ms=0, detail="GET https://a")
    log.record(1, "parse", ts_ms=5)
    log.start_document(2)
    log.record(2, "nav", ts_ms=100, detail="GET https://b")
    assert len(log) == 3
    assert len(log.for_document(1)) == 2
    assert len(log.for_document(2)) == 1
    # 清理文档 1 后只剩文档 2
    log.clear_document(1)
    assert len(log) == 1
    assert log.for_document(1) == []


def test_diag_duration_ms():
    log = DiagnosticsLog()
    log.start_document(1, ts_ms=0)
    log.record(1, "parse", ts_ms=0)
    log.record(1, "parse", ts_ms=40)   # 同一 phase 两次
    log.record(1, "layout", ts_ms=40)
    log.record(1, "paint", ts_ms=90)
    d = log.duration_ms(1)
    assert d["parse_first"] == 0 and d["parse_last"] == 40
    assert d["layout_first"] == 40 and d["layout_last"] == 40
    assert d["paint_first"] == 90 and d["paint_last"] == 90
    # 总耗时 = 末次 paint - 文档起始
    assert d["total"] == 90


def test_diag_export_text_and_json():
    log = DiagnosticsLog()
    log.start_document(1)
    log.record(1, "cache", ts_ms=0, detail="命中 ETag")
    log.record(1, "fallback", ts_ms=3, detail="回退 CPU 光栅")
    txt = log.export(1)
    assert "cache" in txt and "fallback" in txt and "cpu" in txt
    # 仅导出该文档（不含其它文档）
    log.record(2, "nav", ts_ms=0)
    assert "doc=2" not in log.export(1)
    # JSON 导出可解析
    data = json.loads(log.export(1, as_json=True))
    assert data["backend"] == "cpu" and data["document_id"] == 1
    assert data["events"][0]["phase"] == "cache"
    assert any(e["phase"] == "fallback" for e in data["events"])


def test_diag_summary_lists_cache_fallback():
    log = DiagnosticsLog()
    log.start_document(1)
    log.record(1, "cache", detail="命中")
    log.record(1, "fallback", detail="回退 CPU")
    s = log.summary(1)
    assert "命中" in s and "回退 CPU" in s
    # 无相关事件 → 空摘要（普通使用不刷屏）
    log2 = DiagnosticsLog()
    log2.record(9, "paint", detail="x")
    assert log2.summary(9) == ""


def test_diag_backend_default_cpu():
    """文档明确 GPU 为可选第三方；默认 backend=cpu。"""
    log = DiagnosticsLog()
    assert log.backend == "cpu"
    ev = log.record(1, "paint")
    assert isinstance(ev, DiagEvent) and ev.backend == "cpu"
    # 可显式标注可选 GPU backend
    ev2 = log.record(1, "paint", backend="gpu")
    assert ev2.backend == "gpu"


# ----------------------------------------------------------------------
# toolbar_state（按钮随 G08 状态一致）
# ----------------------------------------------------------------------
def test_toolbar_idle_reload_enabled():
    nav = NavigationState()  # idle
    st = toolbar_state(nav, can_back=True, can_forward=False)
    assert st["back_enabled"] is True
    assert st["forward_enabled"] is False
    assert st["reload_stop_label"] == "刷新"
    assert st["reload_stop_kind"] == "reload"


def test_toolbar_loading_stop_shown():
    nav = _loading_nav()
    st = toolbar_state(nav, can_back=False, can_forward=False)
    assert st["reload_stop_label"] == "停止"
    assert st["reload_stop_kind"] == "stop"
    assert "加载中" in st["status_text"]


def test_toolbar_complete_back_returns():
    nav = NavigationState()
    nav.start_navigation("https://a", 1)
    nav.complete()
    st = toolbar_state(nav, can_back=True, can_forward=True)
    assert st["reload_stop_label"] == "刷新"  # 非 busy → 刷新
    assert st["back_enabled"] and st["forward_enabled"]
    assert "已加载" in st["status_text"]


def test_toolbar_failed_retry_state():
    nav = NavigationState()
    nav.start_navigation("https://bad.host", 1)
    nav.fail("dns")
    st = toolbar_state(nav, can_back=False, can_forward=False)
    assert st["reload_stop_kind"] == "reload"
    assert "DNS" in st["status_text"]


def test_toolbar_cancelled_state():
    nav = NavigationState()
    nav.start_navigation("https://x", 1)
    nav.cancel_request()
    st = toolbar_state(nav, can_back=False, can_forward=False)
    assert st["reload_stop_kind"] == "reload"
    assert "已停止" in st["status_text"]


# ----------------------------------------------------------------------
# status_text（悬停 > 阶段 > 失败摘要）
# ----------------------------------------------------------------------
def test_status_hover_url_wins():
    nav = _loading_nav()
    assert status_text(nav, hover_url="https://hover.target/page") == \
        "前往 https://hover.target/page"
    # 无悬停 → 阶段文本
    assert "加载中" in status_text(nav)


def test_status_complete_shows_final_url():
    nav = NavigationState()
    nav.start_navigation("https://a", 1)
    nav.complete(final_url="https://a/2")
    assert "已加载" in status_text(nav) and "https://a/2" in status_text(nav)


def test_status_failed_uses_error_description():
    nav = NavigationState()
    nav.start_navigation("https://t.host", 1)
    nav.fail("timeout")
    t = status_text(nav)
    assert "超时" in t
    # 不以 HTTPS 图标代替整体安全结论（文本里不出现"安全"图标式断言）
    assert "已加密" not in t


def test_status_idle_empty():
    assert status_text(NavigationState()) == ""


# ----------------------------------------------------------------------
# tab_title
# ----------------------------------------------------------------------
def test_tab_title_loading_shows_url():
    nav = _loading_nav("https://pending")
    assert tab_title(nav, "", loading=True) == "https://pending"
    # 有文档 title 时显示 title
    assert tab_title(nav, "Page", loading=False) == "Page"
    # 空 title + 非 loading → 回退 URL
    assert tab_title(nav, "", loading=False) == "https://pending"


# ----------------------------------------------------------------------
# security_note（不以 HTTPS 图标代替整体安全结论）
# ----------------------------------------------------------------------
def test_security_note_http_not_encrypted():
    assert "未加密" in security_note("http://example.com")


def test_security_note_https_verified():
    assert "证书校验通过" in security_note("https://example.com",
                                            https_verified=True)


def test_security_note_https_unverified_warns():
    """https 但证书未完全校验 → 明示"未完全校验"（不以绿锁代替）。"""
    assert "未完全校验" in security_note("https://selfsigned.local",
                                         https_verified=False)


def test_security_note_blank_or_other_empty():
    assert security_note("") == ""
    assert security_note("about:blank") == ""


TESTS.append(("test_diag_record_and_filter_by_document",
              test_diag_record_and_filter_by_document))
TESTS.append(("test_diag_duration_ms", test_diag_duration_ms))
TESTS.append(("test_diag_export_text_and_json",
              test_diag_export_text_and_json))
TESTS.append(("test_diag_summary_lists_cache_fallback",
              test_diag_summary_lists_cache_fallback))
TESTS.append(("test_diag_backend_default_cpu",
              test_diag_backend_default_cpu))
TESTS.append(("test_toolbar_idle_reload_enabled",
              test_toolbar_idle_reload_enabled))
TESTS.append(("test_toolbar_loading_stop_shown",
              test_toolbar_loading_stop_shown))
TESTS.append(("test_toolbar_complete_back_returns",
              test_toolbar_complete_back_returns))
TESTS.append(("test_toolbar_failed_retry_state",
              test_toolbar_failed_retry_state))
TESTS.append(("test_toolbar_cancelled_state",
              test_toolbar_cancelled_state))
TESTS.append(("test_status_hover_url_wins", test_status_hover_url_wins))
TESTS.append(("test_status_complete_shows_final_url",
              test_status_complete_shows_final_url))
TESTS.append(("test_status_failed_uses_error_description",
              test_status_failed_uses_error_description))
TESTS.append(("test_status_idle_empty", test_status_idle_empty))
TESTS.append(("test_tab_title_loading_shows_url",
              test_tab_title_loading_shows_url))
TESTS.append(("test_security_note_http_not_encrypted",
              test_security_note_http_not_encrypted))
TESTS.append(("test_security_note_https_verified",
              test_security_note_https_verified))
TESTS.append(("test_security_note_https_unverified_warns",
              test_security_note_https_unverified_warns))
TESTS.append(("test_security_note_blank_or_other_empty",
              test_security_note_blank_or_other_empty))
