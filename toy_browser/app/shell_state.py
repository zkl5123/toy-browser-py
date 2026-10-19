"""R7-U03 §4 — 浏览器外壳状态机 + 真实 worker/backend 诊断（纯逻辑）。

依据 ``assessments/20261018-round7/docs/逐项改进实施方案.md`` R7-U03 §230
（item 4）：

> "前后退、刷新、停止、下载/文件打开和会话恢复明确状态机；开发者诊断
> 展示真实 worker/backend 和错误来源。"

验收（§232）："失败任务不得用静态树导出替代通过。" 诊断不能把
"HTTP200/有位图/worker spawn 成功"冒充"渲染在工作进程真实执行"。

本模块是**纯逻辑外壳状态机**（不依赖 tkinter / multiprocessing / 网络）：

* :class:`BrowserShellFSM` — 把六大外壳动作（back / forward / reload /
  stop / download-or-open-file / session-restore）路由成**确定性**
  的状态转移 + 动作日志。复用 :class:`NavigationState` 的忙态
  （loading/interactive 时 stop 优先于 reload）、历史栈的
  ``can_back`` / ``can_forward``，**不重造**导航状态机；下载/文件
  打开与"可恢复会话恢复"作为两类带独立结果的动作记入日志。
* :class:`ShellDiagnostics` — 把"真实 worker/backend + 错误来源"
  聚合成一个可诊断视图（审计"开发者诊断展示真实 worker/backend 和
  错误来源"）：
  - backend_kind（``process`` / ``fallback`` / ``cpu``）来自
    :class:`DocumentProcessStats`（``on_fallback`` 表示回退到内置
    同步 worker，不冒充真实进程）；
  - ``worker_crashed`` 单独透出（崩溃 ≠ 加载失败，审计"不能据
    名称宣称已多核渲染"）；
  - 错误来源（:class:`ErrorSource`：``network`` / ``tls`` /
    ``script`` / ``worker`` / ``none``）与 :class:`NavigationState`
    的 ``error_category`` 对齐；
  - 不把 ``https`` 当可信（复用 :mod:`ui_a11y_bridge` 的 TLS 分离
    契约：tls_trusted 由调用方显式给，不从 scheme 推断）。

设计边界（与既有纯逻辑模块一致，不重造、不冒充）：
- 导航忙态 / 错误分类已有（:mod:`navigation_state`）；worker 诊断
  统计已有（:mod:`document_process.DocumentProcessStats`）；损坏
  会话降级已有（:mod:`session_recovery`）；本模块只做"六动作统一
  状态机 + 真实 backend/错误来源聚合视图"，供 GUI 工具栏 / 诊断区
  / 键盘全流程消费。
- 真实多核 worker spawn + 读屏器人工验收属"未验证"，本模块保持
  纯逻辑可回归；真实值由调用方注入（stats / error_category /
  tls_trusted），不在这里 spawn 进程。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 外壳动作（R7-U03 §230 item 4 的六类动作）
# ---------------------------------------------------------------------------

ACT_BACK = "back"
ACT_FORWARD = "forward"
ACT_RELOAD = "reload"
ACT_STOP = "stop"
ACT_DOWNLOAD = "download"
ACT_SESSION_RESTORE = "session_restore"
_SHELL_ACTIONS = (ACT_BACK, ACT_FORWARD, ACT_RELOAD, ACT_STOP,
                  ACT_DOWNLOAD, ACT_SESSION_RESTORE)


# 动作结果码（确定性，供日志 / 读屏播报 / 键盘全流程验收）。
RES_OK = "ok"
RES_NOOP = "noop"                 # 无历史可退 / 无进行中导航可停
RES_QUEUED = "queued"             # 下载 / 文件打开已入队（异步执行）
RES_RESTORED = "restored"         # 会话恢复成功（可能 partial / full）
RES_REJECTED = "rejected"         # 被拒（如 busy 时不可 reload 目标）


# 错误来源（审计"开发者诊断展示...错误来源"；区分网络 / TLS /
# 脚本 / worker 崩溃，不笼统归"加载失败"）。
ERR_SRC_NONE = "none"
ERR_SRC_NETWORK = "network"
ERR_SRC_TLS = "tls"
ERR_SRC_SCRIPT = "script"
ERR_SRC_WORKER = "worker"
_ERR_SOURCES = (ERR_SRC_NONE, ERR_SRC_NETWORK, ERR_SRC_TLS,
                ERR_SRC_SCRIPT, ERR_SRC_WORKER)

# 错误分类词表（与 navigation_state.ERROR_CATEGORY_LABELS 对齐）。
_ERR_CATEGORY_TO_SOURCE = {
    "dns": ERR_SRC_NETWORK,
    "connect": ERR_SRC_NETWORK,
    "timeout": ERR_SRC_NETWORK,
    "http": ERR_SRC_NETWORK,
    "redirect": ERR_SRC_NETWORK,
    "bad_url": ERR_SRC_NETWORK,
    "tls": ERR_SRC_TLS,
    "script": ERR_SRC_SCRIPT,
    "": ERR_SRC_NONE,
}


# ---------------------------------------------------------------------------
# 外壳动作日志条目（确定性，供读屏 / 回归 / 诊断区回放）
# ---------------------------------------------------------------------------

@dataclass
class ShellActionRecord:
    """一次外壳动作的确定性结果（R7-U03 §4 状态机日志）。

    * ``action`` — 动作（六大动作之一）。
    * ``result`` — 结果码（RES_*）。
    * ``detail`` — 自由文本（如下载的 URL / 文件路径、恢复 degraded
      级别；成功动作常为空）。
    * ``url_after`` — 动作后的"应加载 / 已导航到"的 URL（前后退 /
      刷新 / 会话恢复填；stop / download 为空）。
    """
    action: str
    result: str
    detail: str = ""
    url_after: str = ""


# ---------------------------------------------------------------------------
# 浏览器外壳状态机（R7-U03 §230 item 4 的六动作统一路由）
# ---------------------------------------------------------------------------

class BrowserShellFSM:
    """六大外壳动作 → 确定性状态转移 + 动作日志（纯逻辑，不重造导航）。

    依赖注入（不重造已有状态机 / 统计）：

    * :class:`NavigationState` — 忙态（loading/interactive）+ 错误分类；
      stop 在 busy 时优先于 reload（G08 刷新按钮变停止的契约）。
    * ``can_back`` / ``can_forward`` — 历史栈深度（:class:`TabBrowser`
      / :class:`HistoryCommitter` 提供），决定 back/forward 可否执行。
    * :class:`DocumentProcessStats` — 真实 worker/backend 诊断（可选；
      不传时 :class:`ShellDiagnostics` 按 cpu fallback 兜底）。

    动作 API（每个返回 :class:`ShellActionRecord`）：

    * :meth:`back` / :meth:`forward` — 无历史 → NOOP；有历史 → OK，
      给出目标 URL（调用方据此驱动导航，本 FSM 只记"该去哪"）。
    * :meth:`reload` — busy → 转 :meth:`stop`（G08 刷新按钮变停止）；
      非 busy 且有 current_url → OK 重导航；无 URL → NOOP。
    * :meth:`stop` — busy → OK（逻辑取消）；非 busy → NOOP（无进行中
      导航可停，审计"不强杀线程"——只置标志）。
    * :meth:`download` / 文件打开 — 入队 QUEUED（异步执行，本 FSM 不
      阻塞），detail 记 URL / 文件路径；空目标 → REJECTED。
    * :meth:`session_restore` — 用 :func:`session_recovery.recover_session`
      抢救可恢复条目；RECOVERED（附 degraded 级别）；全损降级为
      RESTORED + detail="degraded:full"（仍成功，可打开空会话）。
    """

    def __init__(self, nav, can_back: bool = False,
                 can_forward: bool = False,
                 worker_stats=None) -> None:
        from toy_browser.app.navigation_state import NavigationState
        if not isinstance(nav, NavigationState):
            nav = NavigationState()
        self.nav = nav
        self.can_back = can_back
        self.can_forward = can_forward
        self.worker_stats = worker_stats
        self._log: list[ShellActionRecord] = []
        self._downloads: list[dict] = []
        # back/forward 目标 URL（调用方驱动导航；FSM 不直接改 nav）。
        self._back_url: Optional[str] = None
        self._forward_url: Optional[str] = None

    # -- 查询 --------------------------------------------------------------
    @property
    def log(self) -> list:
        return list(self._log)

    @property
    def downloads(self) -> list:
        """已入队的下载 / 文件打开（detail 含 URL / 文件路径）。"""
        return list(self._downloads)

    def last(self) -> Optional[ShellActionRecord]:
        return self._log[-1] if self._log else None

    def _record(self, action: str, result: str,
                detail: str = "", url_after: str = "") -> ShellActionRecord:
        rec = ShellActionRecord(action=action, result=result,
                                detail=detail, url_after=url_after)
        self._log.append(rec)
        return rec

    # -- 六动作 ------------------------------------------------------------
    def back(self) -> ShellActionRecord:
        """后退一步：无历史 → NOOP；有 → OK + 目标 URL（不直接导航）。"""
        if not self.can_back:
            return self._record(ACT_BACK, RES_NOOP, "no-back-history")
        url = self._back_url or ""
        return self._record(ACT_BACK, RES_OK, "", url_after=url)

    def forward(self) -> ShellActionRecord:
        """前进一步：无历史 → NOOP；有 → OK + 目标 URL。"""
        if not self.can_forward:
            return self._record(ACT_FORWARD, RES_NOOP,
                                "no-forward-history")
        url = self._forward_url or ""
        return self._record(ACT_FORWARD, RES_OK, "", url_after=url)

    def reload(self) -> ShellActionRecord:
        """刷新：busy（loading/interactive）→ 转 stop（G08 契约）；
        非 busy 且有 current_url → OK 重导航；无 URL → NOOP。"""
        if self.nav.is_busy:
            # 忙时刷新键 = 停止键（G08 step 2：刷新按钮变停止）。
            return self.stop()
        url = self.nav.current_url or self.nav.final_url
        if not url:
            return self._record(ACT_RELOAD, RES_NOOP, "no-url-to-reload")
        self._record(ACT_RELOAD, RES_OK, "", url_after=url)
        # FSM 只记"该重导航到 url"，调用方驱动真实导航。
        return self.last()

    def stop(self) -> ShellActionRecord:
        """停止：busy → OK（逻辑取消，不强杀线程）；非 busy → NOOP。"""
        if self.nav.is_busy:
            self.nav.cancel_request()
            return self._record(ACT_STOP, RES_OK,
                                detail=f"cancelled {self.nav.current_url}")
        return self._record(ACT_STOP, RES_NOOP, "no-running-navigation")

    def download(self, url: str, is_file: bool = False,
                 path: str = "") -> ShellActionRecord:
        """下载 / 打开文件：入队 QUEUED（异步），detail 记来源。

        *is_file* 为 True 表示"打开本地文件"（file://），否则为
        下载。空目标 → REJECTED（无内容可下载 / 打开）。
        """
        target = (path or url or "").strip()
        if not target:
            return self._record(ACT_DOWNLOAD, RES_REJECTED,
                                "empty-download-target")
        self._downloads.append({
            "url": target, "is_file": is_file, "path": path,
        })
        kind = "open-file" if is_file else "download"
        return self._record(ACT_DOWNLOAD, RES_QUEUED,
                            detail=f"{kind}:{target}")

    def session_restore(self, payload) -> ShellActionRecord:
        """会话恢复：经 :func:`session_recovery.recover_session` 抢救。

        返回 RESTORED，detail 带 ``degraded:`` 级别（none/partial/full）
        + 抢救条目数。全损（full）仍成功——可打开空会话，不阻塞
        启动（G09 step 5 契约）。
        """
        from toy_browser.app import session_recovery
        entries, report = session_recovery.recover_session(payload)
        detail = f"degraded:{report.degraded} recovered:{len(entries)} " \
                 f"source:{report.source}"
        return self._record(ACT_SESSION_RESTORE, RES_RESTORED, detail=detail)


# ---------------------------------------------------------------------------
# 真实 worker/backend + 错误来源 诊断聚合视图（审计"开发者诊断展示
# 真实 worker/backend 和错误来源"）
# ---------------------------------------------------------------------------

@dataclass
class BackendInfo:
    """真实 worker/backend 诊断（不冒充多核 / 不把崩溃当加载失败）。

    * ``backend_kind`` — ``process``（真实文档进程）/ ``fallback``
      （回退内置同步 worker，多核未真正生效）/ ``cpu``（默认单线程
      CPU，未接 worker 池）。
    * ``worker_pid`` — 真实进程 pid（fallback / cpu 时为 0，非进程）。
    * ``worker_crashed`` — worker 进程崩溃 / 不可用标记（单独透出，
      不并入导航 error_category，避免"崩溃 ≠ 加载失败"被混淆）。
    * ``on_fallback`` — 是否已回退到内置同步 worker。
    """
    backend_kind: str = "cpu"
    worker_pid: int = 0
    worker_crashed: bool = False
    on_fallback: bool = False

    @property
    def real_process(self) -> bool:
        """是否真实文档进程（多核收益的前提；fallback/cpu 都不算）。"""
        return self.backend_kind == "process" and not self.on_fallback \
            and not self.worker_crashed

    def describe(self) -> str:
        """诊断区一行摘要（真实 backend，不写"已多核"冒充）。"""
        if self.real_process:
            return f"process(pid={self.worker_pid})"
        if self.on_fallback or self.worker_crashed:
            return "fallback(sync-worker)" + \
                (" crashed" if self.worker_crashed else "")
        return "cpu(no-worker)"


@dataclass
class ErrorSource:
    """错误来源（审计"展示...错误来源"；区分网络 / TLS / 脚本 /
    worker 崩溃，不笼统归"加载失败"）。

    * ``source`` — 来源（ERR_SRC_*）。
    * ``category`` — :class:`NavigationState.error_category` 原词。
    * ``message`` — 控制台原始错误摘要（保留，不抹掉）。
    """
    source: str = ERR_SRC_NONE
    category: str = ""
    message: str = ""

    @property
    def present(self) -> bool:
        return self.source != ERR_SRC_NONE


def classify_error_source(category: str, worker_crashed: bool = False,
                          backend_kind: str = "cpu") -> ErrorSource:
    """把 :class:`NavigationState.error_category` + worker 状态映射为
    真实错误来源（worker 崩溃优先于导航错误，因其是执行层而非网络层）。"""
    if worker_crashed:
        return ErrorSource(source=ERR_SRC_WORKER, category=category,
                           message="worker process crashed / unavailable")
    src = _ERR_CATEGORY_TO_SOURCE.get((category or "").strip(),
                                      ERR_SRC_NETWORK)
    return ErrorSource(source=src, category=category or "", message="")


class ShellDiagnostics:
    """把"真实 worker/backend + 错误来源 + TLS 分离"聚合成诊断视图。

    审计 R7-U03 §230 item 4："开发者诊断展示**真实** worker/backend
    和错误来源。" 本类只**聚合**调用方注入的真实值（
    :class:`BackendInfo` / :class:`ErrorSource` / tls_trusted），不在
    这里 spawn 进程或推断 TLS——真实值来自
    :class:`DocumentProcessStats` / :class:`NavigationState` / 调用方。

    关键契约（与 :mod:`ui_a11y_bridge` 的 TLS 分离一致）：
    * ``tls_trusted`` 由调用方**显式**传入（来自 TLS 握手真实校验），
      绝不从 URL https 推断（审计 §229）。
    * worker 崩溃单列为 ``ERR_SRC_WORKER``，不并入网络 error_category。
    """

    def __init__(self, backend: Optional[BackendInfo] = None,
                 error: Optional[ErrorSource] = None,
                 tls_trusted: str = "unknown") -> None:
        self.backend = backend or BackendInfo()
        self.error = error or ErrorSource()
        # 用 ui_a11y_bridge 的 TLS 常量（独立字段，不从 scheme 推断）。
        from toy_browser.app.ui_a11y_bridge import (
            TLS_UNKNOWN, TLS_TRUSTED, TLS_NOT_TRUSTED, TLS_NOT_APPLICABLE)
        if tls_trusted not in (TLS_UNKNOWN, TLS_TRUSTED,
                               TLS_NOT_TRUSTED, TLS_NOT_APPLICABLE):
            tls_trusted = TLS_UNKNOWN
        self.tls_trusted = tls_trusted

    @classmethod
    def from_process_stats(cls, stats, error_category: str = "",
                           tls_trusted: str = "unknown") -> "ShellDiagnostics":
        """从 :class:`DocumentProcessStats` 构造（真实 worker 诊断）。

        *stats* 的 ``on_fallback`` / ``worker_crashed`` 决定
        ``backend_kind``（True → fallback，False → process）；pid 由
        调用方经 *stats* 额外属性或 0（纯逻辑回归不持有真实 pid）。
        """
        from toy_browser.app.document_process import DocumentProcessStats
        if not isinstance(stats, DocumentProcessStats):
            stats = DocumentProcessStats()
        kind = "fallback" if stats.on_fallback else "process"
        pid = int(getattr(stats, "worker_pid", 0) or 0)
        backend = BackendInfo(backend_kind=kind, worker_pid=pid,
                              worker_crashed=stats.worker_crashed,
                              on_fallback=stats.on_fallback)
        err = classify_error_source(error_category,
                                    worker_crashed=stats.worker_crashed,
                                    backend_kind=kind)
        return cls(backend=backend, error=err,
                   tls_trusted=tls_trusted)

    # -- 聚合视图 ----------------------------------------------------------
    def snapshot(self) -> dict:
        """诊断区一行 dict（真实 backend + 错误来源 + TLS，不冒充）。"""
        return {
            "backend_kind": self.backend.backend_kind,
            "worker_pid": self.worker_pid,
            "worker_crashed": self.backend.worker_crashed,
            "on_fallback": self.backend.on_fallback,
            "real_process": self.backend.real_process,
            "error_source": self.error.source,
            "error_category": self.error.category,
            "error_message": self.error.message,
            "tls_trusted": self.tls_trusted,
        }

    @property
    def worker_pid(self) -> int:
        return self.backend.worker_pid

    def describe(self) -> str:
        """诊断区文本（"真实 worker/backend 和错误来源"一行）。"""
        parts = [f"backend={self.backend.describe()}"]
        if self.error.present:
            parts.append(f"error={self.error.source}"
                         + (f"({self.error.category})"
                            if self.error.category else ""))
            if self.error.message:
                parts.append(self.error.message)
        parts.append(f"tls={self.tls_trusted}")
        return " ".join(parts)
