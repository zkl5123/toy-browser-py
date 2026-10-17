"""批次 22 — C01 公共 API 包装层 GUI 接入 + G10 诊断区展开收起（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md``
- C01 step 4："建立 DocumentController 的唯一修改入口，把 GUI、CLI 和
  测试都接到同一流程，保留公共 API 包装层兼容旧调用"（批次 9 落了
  CLI / 测试侧 ``render_document_state``；本批补 GUI 侧
  ``PageInstance`` → ``DocumentState`` 的接入纯逻辑核心）。
- G10 step 4："Console/Network/DOM/渲染计时放可展开诊断区，普通使用
  不堆放内核实现信息"（批次 6 落了展开 / 收起纯逻辑 + 事件流；本批
  补诊断区布局规格 + 关闭诊断区不改变页面行为验收）。

与已有模块的关系（本模块**不重复**实现，只做统一接入层）：
- **C01 step 4**（:mod:`toy_browser.app.document_renderer` 的
  :func:`render_document_state` / :func:`render_document_state_restate`）
  已有 CLI / 测试侧包装；本模块的 :class:`GuiDocumentBridge` 把
  ``PageInstance`` 的字段（``dom_tree`` / ``script_host`` /
  ``_last_html`` / ``_last_css`` / ``_reuse_doc`` / ``doc_version``）
  桥接到 ``DocumentState`` 生命周期，使 GUI 走**同一入口**。
- **G10 step 4**（:mod:`toy_browser.app.diagnostics` 的
  :class:`DiagnosticsLog` + ``toolbar_state`` / ``status_text``）
  已有展开 / 收起 + 事件流；本模块的 :class:`DiagnosticPanel` 补
  布局规格（诊断区尺寸 / 折叠态 / 事件过滤）+ "关闭诊断区不改变
  页面行为"验收（诊断区开关对渲染结果无副作用）。
- **G10 step 5**（诊断记录 document_id / backend / 各阶段时间 / 缓存
  与 fallback 原因）由 ``DiagnosticsLog.record`` 承担，本模块只做
  面板层组装。

GUI 侧（Tk 真实诊断区控件 / 折叠动画 / 导出按钮）需活显示环境验收；
本批为**纯逻辑核心**，不触碰 tkinter。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.document_state_full import DocumentState
from toy_browser.app.diagnostics import DiagnosticsLog, DiagEvent


# ---------------------------------------------------------------------------
# C01 step 4 — GUI 侧接入：PageInstance → DocumentState 桥
# ---------------------------------------------------------------------------

class GuiDocumentBridge:
    """C01 step 4 — 把 GUI 的 ``PageInstance`` 接到 ``DocumentState``
    同一生命周期入口（纯逻辑核心）。

    职责：
    - 持有 :class:`DocumentState`（C01 完整生命周期容器）。
    - 提供 ``begin_document`` / ``restate`` / ``render`` 三个入口，
      分别对应"新文档加载（解析 + 脚本初始化）"/"重排（不重建 Host）"/
      "光栅化（只 bump frame_id）"。
    - 暴露 ``document`` 供 GUI 侧读取版本 / DOM / Host。
    - ``sync_page_state`` 把 GUI 侧的 ``hover_id`` / ``focus_id`` /
      ``active_form_field`` 映射到 ``DocumentState.node_ids``（稳定
      node_id，重排后点击监听仍在）。

    与 :func:`document_renderer.render_document_state` 的关系：本桥
    是**状态管理入口**（版本 / 生命周期）；实际渲染管线（解析 → 脚
    本 → 样式 → 布局 → 光栅化）仍由 :mod:`document_renderer` 承担。
    GUI 侧先经本桥 bump 版本，再调渲染管线；或经
    :meth:`render_pipeline` 一步完成。
    """

    def __init__(self) -> None:
        self._doc = DocumentState()
        self._initialized = False

    @property
    def document(self) -> DocumentState:
        return self._doc

    @property
    def initialized(self) -> bool:
        return self._initialized

    # -- 新文档加载（C01 step 2：解析 + 脚本初始化 = 新文档） --------------

    def begin_document(self, dom_tree, script_host,
                       policy: Optional[dict] = None,
                       base_url: str = "") -> None:
        """C01 — 新文档加载：分配 C01 文档身份 + 绑定 DOM / Host /
        策略 / base_url + 脚本初始化计数 +1。"""
        self._doc.new_document(dom_tree, script_host,
                               policy=policy or {}, base_url=base_url)
        self._initialized = True

    # -- 重排（C01 step 2：hover/focus/scroll/resize 只 bump layout）----

    def restate(self) -> None:
        """重排：只 bump ``layout_revision``，不重建 Host / 不重跑
        脚本（``script_init_count`` 保持不变）。"""
        if not self._initialized:
            raise ValueError("restate 需要先 begin_document")
        self._doc.mark_restate()

    # -- 渲染（C01 step 3：只 bump frame_id）-----------------------------

    def render(self) -> None:
        """光栅化：只 bump ``frame_id``。"""
        if not self._initialized:
            raise ValueError("render 需要先 begin_document")
        self._doc.mark_render()

    # -- 新导航（C01 step 5：新上下文，旧上下文待销毁）-------------------

    def navigate(self) -> DocumentState:
        """新导航：创建新 ``DocumentState``，旧上下文标记
        ``disposed``（不能跨源复用策略 / JS 全局）。"""
        self._doc = self._doc.new_context()
        self._initialized = False
        return self._doc

    # -- 稳定 node_id（C01 step 1：点击监听 / 焦点不因重排失效）---------

    def node_id(self, element_index: int) -> int:
        return self._doc.node_id(element_index)

    # -- GUI 侧状态同步 ---------------------------------------------------

    def sync_page_state(self, hover_id: Optional[int] = None,
                        focus_id: Optional[int] = None,
                        active_form_field: Optional[int] = None) -> dict:
        """把 GUI 侧的 ``hover_id`` / ``focus_id`` /
        ``active_form_field`` 映射到稳定 node_id（重排后仍有效）。

        返回 ``{"hover_node_id", "focus_node_id", "form_field_node_id"}``。
        """
        return {
            "hover_node_id": self._doc.node_id(hover_id)
                if hover_id is not None else None,
            "focus_node_id": self._doc.node_id(focus_id)
                if focus_id is not None else None,
            "form_field_node_id": self._doc.node_id(active_form_field)
                if active_form_field is not None else None,
        }

    # -- 渲染管线一步完成（委托 document_renderer）-----------------------

    def render_pipeline(self, html: str, css_text: str = "",
                        width: int = 800, height: int = 600,
                        base_url: str = "",
                        policy: Optional[dict] = None,
                        restate_only: bool = False):
        """C01 step 4 — GUI 走同一入口：

        - ``restate_only=False``（新文档）：:meth:`begin_document` +
          :func:`document_renderer.render_document_state`。
        - ``restate_only=True``（重排）：:meth:`restate` +
          :func:`document_renderer.render_document_state_restate`。

        返回 :class:`~toy_browser.rendering.paint.Bitmap`。
        """
        from toy_browser.app.document_renderer import (
            render_document_state, render_document_state_restate,
        )
        from toy_browser.document import dom as dom_mod
        from toy_browser.scripting import js as js_mod
        if restate_only:
            if not self._initialized:
                raise ValueError("restate_only 需要先 begin_document")
            self.restate()
            return render_document_state_restate(
                self._doc, css_text=css_text,
                width=width, height=height)
        # 新文档
        tree = dom_mod.parse(html)
        host = js_mod.Host(tree, lambda _s: None)
        host.execute_all()
        self.begin_document(tree, host, policy=policy,
                            base_url=base_url or html)
        return render_document_state(
            self._doc, html, css_text=css_text,
            width=width, height=height,
            base_url=base_url or html, policy=policy)

    # -- 诊断 -------------------------------------------------------------

    def version_snapshot(self) -> dict:
        """C01 — 当前文档版本快照（供诊断 / 审计）。"""
        v = self._doc.doc_version
        return {
            "document_id": getattr(v, "document_id", 0),
            "navigation_epoch": getattr(v, "navigation_epoch", 0),
            "layout_revision": getattr(v, "layout_revision", 0),
            "frame_id": getattr(v, "frame_id", 0),
            "script_init_count": self._doc.script_init_count,
            "initialized": self._initialized,
            "disposed": self._doc.disposed,
        }


# ---------------------------------------------------------------------------
# G10 step 4 — 诊断区面板（布局规格 + 折叠态 + 事件过滤）
# ---------------------------------------------------------------------------

@dataclass
class DiagnosticPanel:
    """G10 step 4 — 可展开诊断区的面板层（纯逻辑核心）。

    职责：
    - 管理诊断区**展开 / 收起态**（默认收起，普通使用不堆放内核实现
      信息——G10 验收"关闭诊断区不改变页面行为"）。
    - 按 document_id 过滤事件（诊断区只显示当前文档的事件）。
    - 暴露**布局规格**（诊断区尺寸 / 折叠态 / 事件列表），供 Tk 侧
      控件消费；本模块不做 Tk 绑定。
    - ``close`` / ``open`` 对渲染结果**无副作用**（诊断区开关不触发
      重排 / 不 bump 版本——验收"关闭诊断区不改变页面行为"）。

    与 :class:`DiagnosticsLog` 的关系：本面板*消费*事件流（读），
    不*写入*事件（写由渲染 / 脚本管线经 ``record`` 承担）。
    """
    log: DiagnosticsLog = field(default_factory=DiagnosticsLog)
    expanded: bool = False
    # 诊断区布局规格（G10 step 4：可展开诊断区）
    panel_min_height: int = 60
    panel_max_height: int = 300
    panel_default_height: int = 120
    # 当前显示的 document_id（None = 显示全部）
    visible_document_id: Optional[int] = None

    # -- 展开 / 收起（G10 step 4）------------------------------------------

    def toggle(self) -> bool:
        """切换展开 / 收起；返回新态。"""
        self.expanded = not self.expanded
        return self.expanded

    def open(self) -> None:
        self.expanded = True

    def close(self) -> None:
        self.expanded = False

    @property
    def panel_height(self) -> int:
        """当前诊断区高度（收起 = 0；展开 = default_height）。"""
        if not self.expanded:
            return 0
        return self.panel_default_height

    # -- 事件过滤（诊断区按文档显示）--------------------------------------

    def visible_events(self) -> list[DiagEvent]:
        """当前面板显示的事件（按 ``visible_document_id`` 过滤；
        None = 全部）。"""
        if not self.expanded:
            return []
        if self.visible_document_id is None:
            return list(self.log._events)
        return self.log.for_document(self.visible_document_id)

    def event_count(self) -> int:
        return len(self.visible_events())

    # -- 布局规格（供 Tk 侧控件消费）-------------------------------------

    def layout_spec(self) -> dict:
        """G10 step 4 — 诊断区布局规格（Tk 侧读此 dict 建控件）。

        - ``expanded`` / ``panel_height`` — 折叠态。
        - ``event_count`` / ``phases`` — 事件统计（渲染计时）。
        - ``backend`` — 渲染 backend（"cpu" 默认；GPU 可选标注）。
        """
        durations = self.log.duration_ms(
            self.visible_document_id or 0)
        return {
            "expanded": self.expanded,
            "panel_height": self.panel_height,
            "min_height": self.panel_min_height,
            "max_height": self.panel_max_height,
            "event_count": self.event_count(),
            "phases": durations,
            "backend": self.log.backend,
            "document_id": self.visible_document_id,
        }

    # -- 验收：关闭诊断区不改变页面行为 -----------------------------------

    def assert_close_no_side_effect(self,
                                     version_before: dict,
                                     version_after: dict) -> bool:
        """G10 验收 — 关闭 / 打开诊断区**不改变** C01 文档版本
        （不 bump layout_revision / frame_id / 不重建 Host）。

        传入关闭前后的 :meth:`GuiDocumentBridge.version_snapshot`，
        返回版本是否一致（True = 无副作用，验收通过）。
        """
        keys = ("document_id", "navigation_epoch", "layout_revision",
                "frame_id", "script_init_count")
        return all(version_before.get(k) == version_after.get(k)
                   for k in keys)


# ---------------------------------------------------------------------------
# G10 step 5 — 诊断导出（面板层组装）
# ---------------------------------------------------------------------------

def export_diagnostic_snapshot(panel: DiagnosticPanel,
                               document_id: Optional[int] = None) -> str:
    """G10 step 5 — 从面板导出诊断信息（JSON 文本）。

    含：backend / document_id / 各阶段耗时 / 事件列表（缓存 / fallback
    原因）。供"导出诊断信息"按钮的数据源。
    """
    import json
    did = document_id if document_id is not None \
        else panel.visible_document_id
    spec = panel.layout_spec()
    events = []
    if did is not None:
        for ev in panel.log.for_document(did):
            events.append(ev.as_dict())
    payload = {
        "backend": spec["backend"],
        "document_id": did,
        "phases_ms": spec["phases"],
        "event_count": spec["event_count"],
        "events": events,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    b = GuiDocumentBridge()
    print(b.version_snapshot())
    p = DiagnosticPanel()
    p.open()
    print(p.layout_spec())
    p.close()
    print(p.layout_spec())
