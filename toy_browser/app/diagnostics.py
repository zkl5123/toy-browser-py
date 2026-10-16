"""G10 — 工具栏 / 状态栏 / 诊断区（纯逻辑核心）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G10：

1. 顶部采用标签栏、导航栏、内容视口结构；后退/前进按历史栈启停，
   刷新/停止随 G08 状态切换。
2. 标题使用文档 title，加载前可临时显示 URL。
3. 状态栏显示悬停链接目标、真实加载阶段与失败摘要；不以 HTTPS
   图标代替页面整体安全结论。
4. Console / Network / DOM / 渲染计时放可展开诊断区，普通使用不
   堆放内核实现信息。
5. 诊断记录 document_id、backend、各阶段时间、缓存与 fallback 原因；
   提供导出诊断信息。

本模块是**纯逻辑核心**（不依赖 tkinter）：

* :class:`DiagnosticsLog` — 诊断事件流（按 document_id / 阶段记录，
  带缓存 / fallback 原因）；:meth:`export` 导出为文本（导出诊断信息
  按钮的数据源）。
* :func:`toolbar_state` — 后退 / 前进 / 刷新(停止) 按钮的启用态
  映射（历史栈深度 + G08 :class:`NavigationState`），供工具栏控件
  与键盘快捷键共用（"不同加载状态按钮一致"）。
* :func:`status_text` — 状态栏文本：悬停链接目标 / 真实加载阶段 /
  失败摘要（区分完成 / 失败 / 取消，不统一写 "Loaded"；不以 HTTPS
  图标代替整体安全结论）。
* :func:`tab_title` — 标签标题：文档 title，加载前临时显示 URL。

Tk 侧（工具栏 / 状态栏 / 可展开诊断区的布局与控件）在 GUI 层消费
这些纯函数；本批回归覆盖纯逻辑映射与诊断记录。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.navigation_state import (
    NavigationState, IDLE, LOADING, INTERACTIVE, COMPLETE, FAILED,
    CANCELLED, describe_error,
)


# ----------------------------------------------------------------------
# 诊断事件（G10 step 5：document_id / backend / 各阶段时间 / 缓存 /
# fallback 原因）
# ----------------------------------------------------------------------
@dataclass
class DiagEvent:
    """一条诊断记录。

    * ``document_id`` — C01 文档身份（该事件属于哪个文档）。
    * ``phase`` — 阶段（"nav" / "parse" / "layout" / "paint" /
      "script" / "subresource" / "cache" / "fallback" / …）。
    * ``backend`` — 渲染 / 布局 backend（"cpu" 默认；可选 GPU 标注，
      文档明确 GPU 为可选第三方，本浏览器默认 CPU）。
    * ``ts_ms`` — 记录时刻（相对起始的毫秒，供渲染计时）。
    * ``detail`` — 自由文本（缓存命中 / fallback 原因等）。
    """
    document_id: int
    phase: str
    backend: str
    ts_ms: int
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "phase": self.phase,
            "backend": self.backend,
            "ts_ms": self.ts_ms,
            "detail": self.detail,
        }


class DiagnosticsLog:
    """G10 step 4/5 — 可展开诊断区的数据源（事件流 + 导出）。

    普通使用不堆放内核实现信息（step 4）：诊断区默认收起，展开时
    才渲染事件；本日志只负责**记录与导出**，不主动打印。

    * :meth:`record` — 追加一条事件。
    * :meth:`for_document` — 取某文档的全部事件（诊断区按文档过滤）。
    * :meth:`duration_ms` — 某文档各阶段耗时（渲染计时）。
    * :meth:`export` — 导出诊断信息（JSON / 文本），供"导出诊断信息
      按钮"。
    * :meth:`clear_document` — 关闭标签 / 新导航后清理旧文档事件
      （与 C01 生命周期一致：文档销毁后其诊断事件可回收）。
    """

    def __init__(self, backend: str = "cpu") -> None:
        self.backend = backend
        self._events: list[DiagEvent] = []
        self._doc_start_ms: dict[int, int] = {}
        self._clock_offset_ms = 0

    # ------------------------------------------------------------------
    # 时钟（渲染计时：相对某次导航起始的毫秒）
    # ------------------------------------------------------------------
    def start_document(self, document_id: int, ts_ms: Optional[int] = None) -> None:
        """标记 document_id 的计时起点（``ts_ms`` 缺省用单调钟）。"""
        self._doc_start_ms[document_id] = \
            int(ts_ms) if ts_ms is not None else self._now_ms()

    def _now_ms(self) -> int:
        # 单调递增近似（诊断用，不需 wall-clock 精度）
        return int(time.monotonic() * 1000)

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------
    def record(self, document_id: int, phase: str,
               backend: Optional[str] = None,
               ts_ms: Optional[int] = None,
               detail: str = "") -> DiagEvent:
        backend = backend or self.backend
        ts = int(ts_ms) if ts_ms is not None else \
            (self._doc_start_ms.get(document_id, 0))
        ev = DiagEvent(document_id=document_id, phase=phase,
                       backend=backend, ts_ms=ts, detail=detail)
        self._events.append(ev)
        return ev

    def for_document(self, document_id: int) -> list[DiagEvent]:
        return [e for e in self._events if e.document_id == document_id]

    def clear_document(self, document_id: int) -> None:
        self._events = [e for e in self._events
                         if e.document_id != document_id]
        self._doc_start_ms.pop(document_id, None)

    def __len__(self) -> int:
        return len(self._events)

    # ------------------------------------------------------------------
    # 渲染计时（各阶段耗时）
    # ------------------------------------------------------------------
    def duration_ms(self, document_id: int) -> dict:
        """某文档内各 phase 的耗时毫秒。

        * ``phase_first`` / ``phase_last`` — 各 phase 首次 / 末次
          记录时刻（相对文档起始的毫秒）。
        * ``paint_last`` — 末次 paint 时刻（≈ 文档渲染完成时刻）。
        * ``total`` — 从文档起始到末次 paint 的总耗时。
        """
        evs = self.for_document(document_id)
        base = self._doc_start_ms.get(document_id, 0)
        out: dict[str, dict] = {}
        last_paint = None
        for e in evs:
            slot = out.setdefault(e.phase, {"first": e.ts_ms,
                                             "last": e.ts_ms})
            slot["first"] = min(slot["first"], e.ts_ms)
            slot["last"] = max(slot["last"], e.ts_ms)
            if e.phase == "paint":
                last_paint = e.ts_ms
        result = {f"{p}_first": v["first"] for p, v in out.items()}
        result.update({f"{p}_last": v["last"] for p, v in out.items()})
        if last_paint is not None:
            result["paint_last"] = last_paint
            result["total"] = last_paint - base
        return result

    # ------------------------------------------------------------------
    # 导出诊断信息（G10 step 5 导出按钮的数据源）
    # ------------------------------------------------------------------
    def export(self, document_id: Optional[int] = None,
               as_json: bool = False) -> str:
        """导出诊断信息为文本（或 JSON）。*document_id* 为 None
        时导出全部；否则只导出该文档。"""
        evs = self.for_document(document_id) if document_id is not None \
            else list(self._events)
        if as_json:
            return json.dumps(
                {"backend": self.backend,
                 "document_id": document_id,
                 "events": [e.as_dict() for e in evs]},
                ensure_ascii=False, indent=2)
        lines = [f"# diagnostics backend={self.backend} "
                 f"document_id={document_id}", ""]
        for e in evs:
            lines.append(f"  [{e.phase:>12}] doc={e.document_id} "
                         f"@{e.ts_ms}ms {e.detail}".rstrip())
        return "\n".join(lines)

    def summary(self, document_id: int) -> str:
        """G10 step 3 — 诊断区 / 状态栏的失败摘要（缓存 / fallback
        原因），普通使用不刷屏。"""
        evs = [e for e in self.for_document(document_id)
               if e.phase in ("cache", "fallback", "nav")]
        if not evs:
            return ""
        return "；".join(e.detail for e in evs if e.detail)


# ----------------------------------------------------------------------
# 工具栏 / 状态栏 / 标题映射（G10 step 1–3 的纯函数）
# ----------------------------------------------------------------------
def toolbar_state(nav: NavigationState,
                  can_back: bool, can_forward: bool) -> dict:
    """G10 step 1 — 导航按钮的启用 / 标签映射。

    返回工具栏控件的状态字典（后退 / 前进按历史栈启停；刷新/停止
    随 G08 状态切换；不同加载状态按钮一致）：

    * ``back_enabled`` / ``forward_enabled`` — 历史栈是否可走。
    * ``reload_stop_label`` — busy（loading/interactive）时为
      "停止"，否则为"刷新"（G08 刷新按钮变停止）。
    * ``reload_stop_kind`` — ``"stop"`` / ``"reload"``（同一按钮两个
      动作的键名）。
    * ``status_text`` — 状态栏加载阶段文本（见 :func:`status_text`）。
    """
    busy = nav.is_busy
    return {
        "back_enabled": bool(can_back),
        "forward_enabled": bool(can_forward),
        "reload_stop_label": "停止" if busy else "刷新",
        "reload_stop_kind": "stop" if busy else "reload",
        "status_text": status_text(nav),
    }


def status_text(nav: NavigationState,
                hover_url: Optional[str] = None) -> str:
    """G10 step 3 — 状态栏文本：悬停链接目标 > 真实加载阶段 >
    失败摘要。不以 HTTPS 图标代替页面整体安全结论。

    优先级：

    1. 悬停链接目标（hover_url 非空时最优先，提示可点击目标）；
    2. 加载阶段（loading/interactive/complete 各自不同的阶段文本）；
    3. 失败 / 取消摘要（DNS / 超时 / HTTP / 重定向 / 无效 URL）。
    """
    if hover_url:
        return f"前往 {hover_url}"
    if nav.state in (LOADING, INTERACTIVE):
        pct = int(round(nav.progress * 100))
        return f"加载中 {nav.current_url}（{pct}%）" if \
            nav.state == LOADING else f"可交互 {nav.current_url}"
    if nav.state == COMPLETE:
        return f"已加载 {nav.final_url or nav.current_url}"
    if nav.state == FAILED:
        return describe_error(nav) or "加载失败"
    if nav.state == CANCELLED:
        return f"已停止 {nav.current_url}"
    return ""


def tab_title(nav: NavigationState, doc_title: str,
              loading: bool = False) -> str:
    """G10 step 2 — 标签标题：文档 title；加载前临时显示 URL。"""
    if loading:
        return nav.current_url or doc_title or "加载中…"
    return (doc_title or nav.current_url or "新标签页")


# ----------------------------------------------------------------------
# 会话 / 文档安全摘要（G10 step 3：不以 HTTPS 图标代替整体结论）
# ----------------------------------------------------------------------
def security_note(url: str, https_verified: bool = False) -> str:
    """G10 step 3 — 页面整体安全结论文本（不以 HTTPS 图标代替）。

    * http:// 且无 TSL 校验 → 明示"未加密"；
    * https:// 且校验通过 → "已加密（证书校验通过）"；
    * https:// 但校验未通过（自签名 / 不信任）→ 明示"证书未校验"
      （不以绿色锁代替"页面整体安全"）。
    """
    lowered = (url or "").lower()
    if lowered.startswith("https://"):
        if https_verified:
            return "已加密（证书校验通过）"
        return "已加密（证书未完全校验）"
    if lowered.startswith("http://"):
        return "未加密（明文 HTTP）"
    return ""
