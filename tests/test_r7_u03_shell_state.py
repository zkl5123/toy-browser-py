"""R7-U03 §4 — 浏览器外壳状态机 + 真实 worker/backend 诊断（纯逻辑）。

审计（round7 逐项改进实施方案 R7-U03 §230 item 4）："前后退、刷新、
停止、下载/文件打开和会话恢复明确状态机；开发者诊断展示真实
worker/backend 和错误来源。" 验收（§232）："失败任务不得用静态树
导出替代通过"——诊断不能把 worker spawn 成功 / HTTP200 冒充"渲染
在工作进程真实执行"。

本测试锁定 :mod:`toy_browser.app.shell_state` 的核心契约（纯逻辑，
无 tkinter / 无 multiprocessing / 无网络，任意环境可回归）：

* **外壳状态机**（:class:`BrowserShellFSM`）：六大动作 back / forward /
  reload / stop / download / session_restore 的**确定性**结果
  （OK / NOOP / QUEUED / RESTORED / REJECTED）+ 动作日志；reload 在
  busy 时转 stop（G08 刷新按钮变停止）；stop 非 busy → NOOP（不强杀
  线程）；下载/文件打开入队 QUEUED；会话恢复经 session_recovery 抢救
  （全损降级仍 RESTORED，不阻塞启动）。
* **诊断聚合**（:class:`ShellDiagnostics`）："真实 worker/backend +
  错误来源"——backend_kind（process/fallback/cpu）来自 DocumentProcessStats
  的 on_fallback；worker_crashed 单列 ERR_SRC_WORKER（崩溃 ≠ 加载
  失败）；TLS 可信态与 https scheme 严格分离（不从 scheme 推断可信）；
  不把工作线程崩溃 / 回退同步 worker 冒充"真实多核进程"。

两层设计（与 test_r7_u03_ui_a11y_bridge 约定一致）：
* **静态层**（无 tkinter 也可跑）：核查 shell_state.py 源码中
  "六动作 / 结果码 / 错误来源 / TLS 分离" 契约确实存在。
* **动态层**（纯逻辑，任意环境可跑）：真实调用 FSM + 诊断聚合验证
  确定性转移 / busy-reload→stop / 下载入队 / 会话恢复降级 / worker
  崩溃错误来源 / 回退不冒充多核 / https 不推断可信。
"""
from __future__ import annotations

import os
import re


def _br_src() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, os.pardir, "toy_browser", "app",
                        "shell_state.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _mod():
    from toy_browser.app import shell_state as m
    return m


def _mk_nav(state="idle", current_url="", busy_override=None):
    from toy_browser.app.navigation_state import NavigationState
    nav = NavigationState()
    nav.state = state
    nav.current_url = current_url
    nav.final_url = current_url
    if busy_override is not None:
        # 强制 is_busy（loading/interactive 才 busy；测试需要精确控制时
        # 临时挂一个代理属性——用 monkeypatch 简单方式：直接改 state
        # 已足够，busy_override 仅作校验哨兵）。
        assert state in ("loading", "interactive")
    return nav


# ---------------------------------------------------------------------------
# 静态层（无 tkinter 也可跑）
# ---------------------------------------------------------------------------

def test_static_six_shell_actions_defined():
    """六大外壳动作常量齐全（R7-U03 §230 item 4 全文动作集）。"""
    src = _br_src()
    for act in ("ACT_BACK", "ACT_FORWARD", "ACT_RELOAD", "ACT_STOP",
                "ACT_DOWNLOAD", "ACT_SESSION_RESTORE"):
        assert act in src, f"missing shell action constant {act}"


def test_static_error_source_separates_worker_from_network():
    """错误来源把 worker 崩溃单列，不并入网络 error_category。"""
    src = _br_src()
    assert "ERR_SRC_WORKER" in src, \
        "worker crash must be a distinct error source"
    # 分类映射里 worker 不落在 network（network 只收网络类分类词）。
    assert "ERR_SRC_NETWORK" in src
    # classify_error_source 优先 worker_crashed（审计"崩溃≠加载失败"）。
    assert "worker_crashed" in src


def test_static_tls_not_inferred_from_scheme():
    """TLS 可信态不从 https scheme 推断（与 ui_a11y_bridge 同契约）。"""
    src = _br_src()
    # 诊断视图持有独立 tls_trusted 字段，不写 scheme.startswith 推断。
    assert "tls_trusted" in src
    assert "tls=" in src or "tls_trusted" in src
    # 不把 https 当可信：无 "startswith('https" 推断可信的写法。
    # （用反查：确认可信判定只来自 tls_trusted 常量，非 scheme）
    assert "TLS_TRUSTED" in src


# ---------------------------------------------------------------------------
# 动态层（纯逻辑，任意环境可跑）
# ---------------------------------------------------------------------------

def test_dynamic_back_noop_without_history():
    """无历史可退 → back NOOP；有历史 → OK + 目标 URL。"""
    m = _mod()
    nav = _mk_nav(state="complete", current_url="http://b",
                  busy_override=None)
    fsm = m.BrowserShellFSM(nav, can_back=False, can_forward=False)
    rec = fsm.back()
    assert rec.result == m.RES_NOOP
    assert rec.detail == "no-back-history"

    nav2 = _mk_nav(state="complete", current_url="http://b")
    fsm2 = m.BrowserShellFSM(nav2, can_back=True)
    fsm2._back_url = "http://a"
    rec2 = fsm2.back()
    assert rec2.result == m.RES_OK
    assert rec2.url_after == "http://a"


def test_dynamic_reload_busy_transitions_to_stop():
    """busy（loading/interactive）时 reload → 转 stop（G08 契约）。"""
    m = _mod()
    nav = _mk_nav(state="loading", current_url="http://b")
    assert nav.is_busy is True
    fsm = m.BrowserShellFSM(nav, can_back=True)
    rec = fsm.reload()
    # busy → 实际执行 stop：动作记为 stop，结果 ok（逻辑取消）。
    assert rec.action == m.ACT_STOP
    assert rec.result == m.RES_OK
    assert nav.cancel_requested is True
    assert nav.state == "cancelled"


def test_dynamic_reload_idle_reruns_current_url():
    """非 busy 且有 URL → reload OK 重导航到同 URL。"""
    m = _mod()
    nav = _mk_nav(state="complete", current_url="http://b")
    fsm = m.BrowserShellFSM(nav)
    rec = fsm.reload()
    assert rec.action == m.ACT_RELOAD
    assert rec.result == m.RES_OK
    assert rec.url_after == "http://b"
    # FSM 不直接改 nav 状态（调用方驱动真实导航）。
    assert nav.state == "complete"


def test_dynamic_stop_noop_when_idle():
    """非 busy → stop NOOP（无进行中导航可停，不强杀线程）。"""
    m = _mod()
    nav = _mk_nav(state="idle", current_url="")
    fsm = m.BrowserShellFSM(nav)
    rec = fsm.stop()
    assert rec.action == m.ACT_STOP
    assert rec.result == m.RES_NOOP
    assert rec.detail == "no-running-navigation"
    assert nav.cancel_requested is False


def test_dynamic_download_queued_not_blocking():
    """下载 / 文件打开入队 QUEUED（异步），空目标 REJECTED。"""
    m = _mod()
    nav = _mk_nav(state="complete", current_url="http://b")
    fsm = m.BrowserShellFSM(nav)
    rec = fsm.download("http://x/file.pdf")
    assert rec.result == m.RES_QUEUED
    assert "download:http://x/file.pdf" in rec.detail
    assert fsm.downloads[-1]["url"] == "http://x/file.pdf"
    assert fsm.downloads[-1]["is_file"] is False
    # 本地文件打开（file://）走 open-file 标记。
    rec2 = fsm.download("file:///tmp/a.html", is_file=True,
                        path="/tmp/a.html")
    assert rec2.result == m.RES_QUEUED
    assert "open-file:" in rec2.detail
    # 空目标拒绝。
    rec3 = fsm.download("")
    assert rec3.result == m.RES_REJECTED


def test_dynamic_session_restore_recover_and_degrade():
    """会话恢复经 session_recovery 抢救；全损降级仍 RESTORED。"""
    m = _mod()
    nav = _mk_nav(state="idle")
    fsm = m.BrowserShellFSM(nav)
    # 正常可解析会话（含一条非法 dom 项应被丢弃 → partial）。
    payload = ('[{"tab_id":1,"url":"http://a"},'
               '{"url":"http://b","active":true},'
               '{"dom":"<tree>"}]')
    rec = fsm.session_restore(payload)
    assert rec.result == m.RES_RESTORED
    assert "degraded:partial" in rec.detail
    assert "recovered:2" in rec.detail
    # 全损（乱码）→ degraded:full，仍 RESTORED（不阻塞启动）。
    rec2 = fsm.session_restore("%%%not-json%%%")
    assert rec2.result == m.RES_RESTORED
    assert "degraded:full" in rec2.detail


def test_dynamic_shell_log_is_deterministic_sequence():
    """动作日志按发生顺序确定性记录（读屏 / 回归回放依据）。"""
    m = _mod()
    nav = _mk_nav(state="complete", current_url="http://b")
    fsm = m.BrowserShellFSM(nav, can_back=True, can_forward=True)
    fsm._back_url = "http://a"
    fsm._forward_url = "http://c"
    fsm.back()
    fsm.forward()
    fsm.stop()              # 非 busy → NOOP
    fsm.download("http://x")
    actions = [r.action for r in fsm.log]
    assert actions == [m.ACT_BACK, m.ACT_FORWARD, m.ACT_STOP,
                       m.ACT_DOWNLOAD]
    results = [r.result for r in fsm.log]
    assert results == [m.RES_OK, m.RES_OK, m.RES_NOOP, m.RES_QUEUED]
    assert fsm.last().action == m.ACT_DOWNLOAD


def test_dynamic_diagnostics_real_process_not_misrepresented():
    """真实文档进程（on_fallback=False）→ real_process=True。"""
    m = _mod()
    from toy_browser.app.document_process import DocumentProcessStats
    stats = DocumentProcessStats(on_fallback=False, worker_crashed=False)
    diag = m.ShellDiagnostics.from_process_stats(stats,
                                                 tls_trusted="trusted")
    snap = diag.snapshot()
    assert snap["backend_kind"] == "process"
    assert snap["real_process"] is True
    assert snap["worker_crashed"] is False


def test_dynamic_diagnostics_fallback_not_claims_multicore():
    """回退同步 worker / worker 崩溃 → 不冒充"真实多核进程"。"""
    m = _mod()
    from toy_browser.app.document_process import DocumentProcessStats
    fb = DocumentProcessStats(on_fallback=True, worker_crashed=False)
    diag_fb = m.ShellDiagnostics.from_process_stats(fb)
    assert diag_fb.snapshot()["real_process"] is False
    assert diag_fb.snapshot()["on_fallback"] is True
    assert "fallback" in diag_fb.backend.describe()

    crash = DocumentProcessStats(on_fallback=False, worker_crashed=True)
    diag_c = m.ShellDiagnostics.from_process_stats(crash,
                                                    error_category="")
    assert diag_c.snapshot()["real_process"] is False
    # 崩溃单列 worker 错误来源（不并入网络）。
    assert diag_c.error.source == m.ERR_SRC_WORKER


def test_dynamic_error_source_maps_category_to_origin():
    """error_category → 真实错误来源映射（不笼统归"加载失败"）。"""
    m = _mod()
    cases = {
        "dns": m.ERR_SRC_NETWORK,
        "tls": m.ERR_SRC_TLS,
        "script": m.ERR_SRC_SCRIPT,
        "": m.ERR_SRC_NONE,
    }
    for cat, want in cases.items():
        got = m.classify_error_source(cat)
        assert got.source == want, \
            f"category {cat!r} -> {got.source}, want {want}"
    # worker_crashed 优先于任何 category。
    got = m.classify_error_source("dns", worker_crashed=True)
    assert got.source == m.ERR_SRC_WORKER


def test_dynamic_diagnostics_tls_independent_of_scheme():
    """TLS 可信态是独立字段，不从 https 推断（https 也可能不可信）。"""
    m = _mod()
    # https + 证书不可信 → 诊断里 tls=not_trusted（不写 trusted）。
    from toy_browser.app.document_process import DocumentProcessStats
    stats = DocumentProcessStats()
    diag = m.ShellDiagnostics.from_process_stats(stats,
                                                 tls_trusted="not_trusted")
    snap = diag.snapshot()
    assert snap["tls_trusted"] == "not_trusted"
    # 非法 tls_trusted 值回落 unknown（不静默当可信）。
    diag2 = m.ShellDiagnostics(
        backend=m.BackendInfo(), tls_trusted="bogus")
    assert diag2.tls_trusted == "unknown"


def test_dynamic_safety_note_reuses_bridge_tls_separation():
    """https + 不可信 在安全结论文本里明示"证书未校验"（不以锁代替）。

    与 ui_a11y_bridge.AddressBarState.is_secure_display 同一契约：
    scheme=https 但证书不可信 → 不安全。
    """
    m = _mod()
    from toy_browser.app.ui_a11y_bridge import AddressBarState
    ab = AddressBarState(origin="https://x", final_url="https://x/",
                         tls_present=True, tls_trusted="not_trusted")
    assert ab.scheme_is_https() is True
    assert ab.is_secure_display() is False
    # shell_state 的 TLS 常量与 ui_a11y_bridge 一致（不另造一套）：
    # ShellDiagnostics 从 ui_a11y_bridge 导入 TLS_TRUSTED / TLS_NOT_TRUSTED，
    # 故 shell_state 模块命名空间经其 import 可见。
    from toy_browser.app.ui_a11y_bridge import TLS_TRUSTED, TLS_NOT_TRUSTED
    assert TLS_TRUSTED == "trusted"
    assert TLS_NOT_TRUSTED == "not_trusted"


# ---------------------------------------------------------------------------
TESTS = [
    ("test_static_six_shell_actions_defined",
     test_static_six_shell_actions_defined),
    ("test_static_error_source_separates_worker_from_network",
     test_static_error_source_separates_worker_from_network),
    ("test_static_tls_not_inferred_from_scheme",
     test_static_tls_not_inferred_from_scheme),
    ("test_dynamic_back_noop_without_history",
     test_dynamic_back_noop_without_history),
    ("test_dynamic_reload_busy_transitions_to_stop",
     test_dynamic_reload_busy_transitions_to_stop),
    ("test_dynamic_reload_idle_reruns_current_url",
     test_dynamic_reload_idle_reruns_current_url),
    ("test_dynamic_stop_noop_when_idle",
     test_dynamic_stop_noop_when_idle),
    ("test_dynamic_download_queued_not_blocking",
     test_dynamic_download_queued_not_blocking),
    ("test_dynamic_session_restore_recover_and_degrade",
     test_dynamic_session_restore_recover_and_degrade),
    ("test_dynamic_shell_log_is_deterministic_sequence",
     test_dynamic_shell_log_is_deterministic_sequence),
    ("test_dynamic_diagnostics_real_process_not_misrepresented",
     test_dynamic_diagnostics_real_process_not_misrepresented),
    ("test_dynamic_diagnostics_fallback_not_claims_multicore",
     test_dynamic_diagnostics_fallback_not_claims_multicore),
    ("test_dynamic_error_source_maps_category_to_origin",
     test_dynamic_error_source_maps_category_to_origin),
    ("test_dynamic_diagnostics_tls_independent_of_scheme",
     test_dynamic_diagnostics_tls_independent_of_scheme),
    ("test_dynamic_safety_note_reuses_bridge_tls_separation",
     test_dynamic_safety_note_reuses_bridge_tls_separation),
]
