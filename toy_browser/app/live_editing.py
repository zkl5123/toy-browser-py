"""R7-U02 — 活窗口真实编辑层/IME 接线（键盘序列 + IME 组合驱动真实 Tk 控件）。

依据 ``assessments/20261018-round7/docs/逐项改进实施方案.md`` R7-U02
+ GUI 审计 §57。此前 :mod:`native_overlay`（R7-U02）已把
:class:`EditorState` 的 value/选区/password 驱动到真实 Tk Entry/Text
控件；本模块补上活窗口批次里"**可自动化到最大程度**"的接线层——

把三块已验收的纯逻辑核心接在一起，驱动到真实 Tk 控件：

- :class:`FieldRegistry`（B06）：按稳定 ``node_id`` 索引字段编辑态、
  键盘路由三分、批量局部刷新；
- :class:`KeyboardRouter`（G11）：按键 → 目标域/动作 的统一纯逻辑
  路由（含 :func:`tk_binding_result` 转 Tk break/None）；
- :class:`EditorState`（G03）：value/选区/caret/IME 组合/undo/事件顺序；
- :class:`NativeOverlayWidget`（R7-U02）：把编辑态驱动到真实 Tk 控件
  的双向同步 + password 遮罩 + 几何 + 销毁终态。

本模块是"**接线层**"：把一条**自动化键盘序列**和一段**IME 组合
交互**按 GUI 真实会走的顺序（路由 → 分派 → 编辑落地 → 回写控件）
跑一遍，产出可判定的结果对象。

设计边界（与既有纯逻辑核心互补，不重造）
----------------------------------------
- 不重复实现按键映射（复用 B06 :func:`key_to_input_event` + G11
  路由）、不重复实现编辑状态机（复用 G03 :class:`EditorState`）、
  不重复实现控件同步（复用 :class:`NativeOverlayWidget`）。
- 控件为**可选注入**：注入桩控件即可无 Tk 回归；活窗口下注入真实
  Tk Entry/Text（:meth:`NativeOverlayWidget.bind_widget`）驱动验证。
- **IME 组合可自动化的部分**（拼音中间态 → 汉字提交的组合区 /
  ``is_composing`` / 最终 value 同步到控件）经 :meth:`run_ime_composition`
  验证；**真实系统输入法选词、真人键盘、读屏器读到**仍属人工验收
  边界，本模块不宣称完成（与 R7-P02 §254 同口径）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from toy_browser.app.editor import (
    EditorState, InputEvent,
    INSERT_TEXT, DEL_CARET_BACKWARD,
)
from toy_browser.app.field_editing import FieldRegistry, key_to_input_event
from toy_browser.app.keyboard_router import KeyboardRouter, KeyDecision
from toy_browser.app.native_overlay import (
    NativeOverlayWidget, KIND_ENTRY, KIND_TEXTAREA,
)
from toy_browser.app.overlay_protocol import OverlayRect


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------

@dataclass
class KeyTraceEntry:
    """一条按键经路由 + 编辑落地 + 控件回写后的轨迹（可诊断）。

    * *key*       — 原始按键串（如 "a" / "Backspace" / "Return"）。
    * *target*    — 路由目标域（address / edit / page / ...）。
    * *action*    — 路由动作（edit / caret-move / navigate / ...）。
    * *consumed*  — 路由层是否消费（True → Tk 应 break）。
    * *applied*   — 是否实际改动了编辑值（落地成功）。
    * *note*     — 诊断说明。
    """
    key: str
    target: str = "page"
    action: str = "drop"
    consumed: bool = False
    applied: bool = False
    note: str = ""

    def describe(self) -> str:
        return (f"{self.key!r} → {self.action}@{self.target} "
                f"consumed={self.consumed} applied={self.applied}"
                + (f" [{self.note}]" if self.note else ""))


@dataclass
class ImeTraceEntry:
    """一次 IME 组合阶段的结果（可诊断）。

    * *phase* — "start" / "update" / "commit" / "cancel"。
    * *text*  — 本阶段文本（update 的拼音 / commit 的最终汉字）。
    * *composing* — 执行后 ``is_composing`` 状态。
    * *value* — 执行后控件 / 模型 value。
    * *applied* — 是否实际生效（被只读 / 已销毁拒绝则 False）。
    """
    phase: str
    text: str = ""
    composing: bool = False
    value: str = ""
    applied: bool = False
    note: str = ""

    def describe(self) -> str:
        return (f"ime:{self.phase}({self.text!r}) "
                f"composing={self.composing} value={self.value!r}"
                + (f" [{self.note}]" if self.note else ""))


@dataclass
class SequenceResult:
    """一次自动化键盘序列驱动的结果（全量轨迹 + 终态 value）。"""
    entries: list = field(default_factory=list)
    final_value: str = ""
    edited_count: int = 0
    applied_count: int = 0

    def describe(self) -> str:
        lines = [f"sequence={self.applied_count}/{len(self.entries)} applied, "
                 f"final_value={self.final_value!r}"]
        lines.extend("  " + e.describe() for e in self.entries)
        return "\n".join(lines)


@dataclass
class ImeResult:
    """一段 IME 组合交互驱动的结果。"""
    entries: list = field(default_factory=list)
    final_value: str = ""
    end_composing: bool = False
    committed: bool = False

    def describe(self) -> str:
        lines = [f"ime final_value={self.final_value!r} "
                 f"end_composing={self.end_composing} "
                 f"committed={self.committed}"]
        lines.extend("  " + e.describe() for e in self.entries)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 接线层
# ---------------------------------------------------------------------------

class LiveEditingSession:
    """R7-U02 — 活窗口编辑/IME 接线会话。

    把 :class:`FieldRegistry` + :class:`KeyboardRouter` +
    :class:`NativeOverlayWidget` 接成一条"自动化键盘序列 / IME 组合
    交互 → 路由 → 编辑落地 → 真实控件回写"的流水线。控件经
    :meth:`attach_widget` 注入（桩或真实 Tk 控件）。

    用法::

        sess = LiveEditingSession()
        sess.register_field("email", value="", kind=KIND_ENTRY)
        sess.focus_field("email")
        sess.attach_widget(overlay)          # NativeOverlayWidget(真实 Entry)
        res = sess.inject_key_sequence("a", "b", "@", "x.com")
        assert res.final_value == "ab@x.com"
        # IME 组合（拼音 → 汉字）
        ire = sess.run_ime_composition(
            "huan","ng","h","欢迎", commit=True)
        assert ire.final_value.endswith("欢迎") and not ire.end_composing
    """

    def __init__(self) -> None:
        self._registry = FieldRegistry()
        self._overlays: dict = {}     # node_id → NativeOverlayWidget
        self._active_overlay = None   # 当前焦点字段的控件
        self._active_field = None
        # 键盘路由器（G11）：焦点在编辑层时的按键路由。
        self._router = KeyboardRouter(
            focus_owner="editor", focus_in_edit=True,
            has_active_field=False)

    # -- 字段注册 / 焦点（B06 稳定身份） ----------------------------------

    def register_field(self, node_id, value: str = "",
                       kind: str = KIND_ENTRY,
                       is_password: bool = False,
                       read_only: bool = False,
                       disabled: bool = False,
                       rect: Optional[OverlayRect] = None) -> NativeOverlayWidget:
        """注册字段（B06 稳定 ``node_id``）并为其建
        :class:`NativeOverlayWidget`（尚未绑定控件）。幂等。"""
        self._registry.register(node_id, value, is_password,
                                read_only, disabled)
        ov = self._overlays.get(node_id)
        if ov is None:
            st = self._registry.get(node_id)
            ov = NativeOverlayWidget(st, kind=kind, rect=rect)
            self._overlays[node_id] = ov
        return ov

    def get_overlay(self, node_id) -> Optional[NativeOverlayWidget]:
        return self._overlays.get(node_id)

    def attach_widget(self, node_id, widget) -> bool:
        """给字段 *node_id* 的覆盖层绑定控件（真实 Tk Entry/Text 或桩）。

        若该字段当前是焦点字段，则同步更新活动控件。"""
        ov = self._overlays.get(node_id)
        if ov is None:
            return False
        ov.bind_widget(widget)
        if self._active_field == node_id:
            self._active_overlay = ov
        return True

    def focus_field(self, node_id) -> bool:
        """把焦点移到字段 *node_id*（B06 失焦旧字段提交 change）。

        返回字段是否存在且可聚焦。"""
        st = self._registry.focus(node_id)
        if st is None:
            self._active_field = None
            self._active_overlay = None
            self._router.set_focus("document", False, False)
            return False
        self._active_field = node_id
        self._active_overlay = self._overlays.get(node_id)
        # 路由状态：焦点在编辑层且有活动字段。
        self._router.set_focus(
            "editor", True, not st.is_read_only)
        return True

    def release_focus(self) -> None:
        """失焦（所有字段）：提交 change，路由回 document。"""
        st = self._registry.focused_field()
        if st is not None:
            st.commit_change()
        self._active_field = None
        self._active_overlay = None
        self._router.set_focus("document", False, False)

    # -- 键盘序列（G11 路由 → B06 落地 → 控件回写） ---------------------

    def route_key(self, key: str) -> KeyDecision:
        """把一次按键经 G11 路由器得出 :class:`KeyDecision`。

        路由状态随 :meth:`focus_field` / :meth:`release_focus` 维护。"""
        return self._router.route(key)

    def apply_key(self, key: str) -> bool:
        """一次按键落地（经 :meth:`route_key` 判定后，按 G11 动作分派到
        焦点字段 :class:`EditorState` + 回写控件）。

        分派规则（对齐 G11 :class:`KeyDecision.action`）：
        - ``edit``        → :meth:`EditorState.apply_input`（B06 映射的
          beforeinput；可打印 / Backspace / Delete / Enter 落地，
          并回写控件）；
        - ``caret-move``  → :class:`EditorState` 光标命令（Home / End /
          左右 / 上下 / Page），**不**派发 beforeinput、不改 value，
          仅移动 caret（控件 caret 同步）；
        - ``navigate``    → :meth:`EditorState.commit_change`（Return 提交
          change，编辑值保留、控件不写新字符）；
        - 其余（drop / escape / tab / ...）→ 不改编辑值。

        只读 / 未聚焦字段 → 返回 False。返回是否"对编辑态有作用"
        （edit 落地或 caret 移动或提交成功）。"""
        decision = self.route_key(key)
        st = self._registry.focused_field()
        if st is None:
            return False
        ov = self._active_overlay

        if decision.action == "edit":
            ev = key_to_input_event(key)
            if ev is None:
                return False
            changed = st.apply_input(ev)
            if changed and ov is not None:
                ov.sync_to_widget()
            return changed

        if decision.action == "caret-move":
            self._apply_caret_move(st, key)
            if ov is not None:
                ov.sync_to_widget()
                ov.sync_selection()
            return True

        if decision.action == "navigate":
            # Return 提交 change（编辑值保留；G11 地址栏 navigate 语义）。
            return st.commit_change()

        return False

    def _apply_caret_move(self, st: EditorState, key: str) -> None:
        """把 caret-move 键映射到 :class:`EditorState` 光标命令
        （G03 左右 / Home / End；上下 / Page 玩具简化为 no-op）。"""
        if key in ("ArrowLeft",):
            st.move_left()
        elif key in ("ArrowRight",):
            st.move_right()
        elif key in ("Home",):
            st.move_home()
        elif key in ("End",):
            st.move_end()
        # ArrowUp / ArrowDown / PageUp / PageDown：单行 Entry 无多行
        # caret，玩具简化不移动（textarea 可由真实 Text 原生处理）。

    def inject_key_sequence(self, *keys: str) -> SequenceResult:
        """注入一条**自动化键盘序列**：逐键经 G11 路由 + 编辑/caret/
        提交落地 + 控件回写，产出全量轨迹（可诊断 / 可回归）。

        这是"活窗口 U02 可自动化到最大程度"的核心入口——把 GUI 真实
        会走的按键顺序（路由→分派→编辑→回写）确定性跑一遍，不依赖
        真人键盘。"""
        result = SequenceResult()
        for key in keys:
            decision = self.route_key(key)
            applied = self.apply_key(key)
            entry = KeyTraceEntry(
                key=key, target=decision.target, action=decision.action,
                consumed=decision.consumed, applied=applied,
                note=decision.note)
            result.entries.append(entry)
            if decision.action == "edit":
                result.edited_count += 1
            if applied:
                result.applied_count += 1
        # 终态 value（当前焦点字段；无焦点则空）。
        st = self._registry.focused_field()
        result.final_value = st.value if st is not None else ""
        return result

    # -- IME 组合（G03 三阶段：拼音中间态 → 汉字提交） -------------------

    def run_ime_composition(self, *phases,
                           cancel: bool = False) -> ImeResult:
        """驱动一段 IME 组合交互到焦点字段 + 控件。

        *phases* 是 (text, mark) 元组序列，表达"拼音中间态 → 汉字
        提交"的过程：

        - 第一段自动 :meth:`EditorState.ime_composition_start`（mark 取
          当前 caret）；
        - 中间段经 :meth:`EditorState.ime_composition_update`（组合区
          替换、不派发 input、is_composing 保持 True）；
        - 最后段若 *cancel* 为真则 :meth:`EditorState.ime_composition_commit`
          前走组合取消语义（:func:`_cancel_composition`）；否则最后
          一段作为最终汉字经 :meth:`ime_composition_commit` 提交。

        每段执行后回写控件（:meth:`NativeOverlayWidget.sync_to_widget`），
        使**真实 Tk 控件 value 与 IME 组合状态同步可断言**。真实系统
        输入法选词 / 真人操作不在自动化范围（留人工，§同 R7-P02）。
        """
        result = ImeResult()
        st = self._registry.focused_field()
        if st is None:
            result.entries.append(ImeTraceEntry("start", note="no-focused-field"))
            result.final_value = ""
            result.end_composing = False
            return result

        if cancel:
            # 组合取消（如 Esc 取消整段拼音）。
            _cancel_composition(st)
            if self._active_overlay is not None:
                self._active_overlay.sync_to_widget()
            result.entries.append(ImeTraceEntry(
                "cancel", composing=st.is_composing,
                value=st.value, applied=True))
            result.final_value = st.value
            result.end_composing = st.is_composing
            result.committed = False
            return result

        if not phases:
            result.final_value = st.value
            result.end_composing = st.is_composing
            result.committed = False
            return result

        # 组合开始（mark = 当前 caret）。
        start_mark = st.caret
        st.ime_composition_start(start_mark)
        if self._active_overlay is not None:
            self._active_overlay.sync_to_widget()
        result.entries.append(ImeTraceEntry(
            "start", text="", composing=st.is_composing,
            value=st.value, applied=not st.is_read_only))

        # 中间态（除最后一段外）：拼音更新，不提交。
        for i, phase in enumerate(phases):
            text, mark = _phase_text_mark(phase, i, start_mark)
            is_last = (i == len(phases) - 1)
            if not is_last:
                st.ime_composition_update(text, mark)
                if self._active_overlay is not None:
                    self._active_overlay.sync_to_widget()
                result.entries.append(ImeTraceEntry(
                    "update", text=text, composing=st.is_composing,
                    value=st.value, applied=not st.is_read_only))
            else:
                # 最后一段：作为最终汉字提交。
                st.ime_composition_commit(text)
                if self._active_overlay is not None:
                    self._active_overlay.sync_to_widget()
                result.entries.append(ImeTraceEntry(
                    "commit", text=text, composing=st.is_composing,
                    value=st.value, applied=not st.is_read_only))
                result.committed = True

        result.final_value = st.value
        result.end_composing = st.is_composing
        return result

    # -- 双向同步（审计"value/selection 双向同步"） --------------------

    def sync_round_trip(self, node_id) -> bool:
        """模型 → 控件 → 模型 一轮同步（双向，审计验收项）。

        先 :meth:`sync_to_widget`（模型写控件），再
        :meth:`sync_from_widget`（控件读回模型），返回控件读回是否成功。
        未绑定控件 / 已销毁 → False。"""
        ov = self._overlays.get(node_id)
        if ov is None:
            return False
        ok = ov.sync_to_widget()
        if not ok:
            return False
        return ov.sync_from_widget()

    # -- 查询 -----------------------------------------------------------

    def focused_value(self) -> str:
        st = self._registry.focused_field()
        return st.value if st is not None else ""

    def field_state(self, node_id) -> Optional[EditorState]:
        return self._registry.get(node_id)

    def describe_active(self) -> str:
        st = self._registry.focused_field()
        if st is None:
            return "no-focused-field"
        return (f"field={st.node_ref!r} value={st.value!r} "
                f"caret={st.caret} composing={st.is_composing} "
                f"dirty={st.dirty}")


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _phase_text_mark(phase, index: int, start_mark: int):
    """把 *phase*（(text, mark) 或 str）规范成 (text, mark)。

    str 表示该阶段文本；mark 默认取上阶段 end（简化：用 index 累加）。
    """
    if isinstance(phase, str):
        # 简化：拼音阶段 mark 取 start_mark + index（逐段累加起点）。
        return phase, start_mark + index
    text, mark = phase
    return text, mark


def _cancel_composition(st: EditorState) -> None:
    """组合取消：清除组合区（deleteByComposition 语义），
    is_composing 复位。仅当组合进行中且非只读。"""
    if not st.is_composing or st.is_read_only:
        return
    # 复用 G03 deleteByComposition：删除组合区整段并复位。
    from toy_browser.app.editor import DELETE_BY_COMPOSITION
    st.apply_input(InputEvent(DELETE_BY_COMPOSITION))


# ---------------------------------------------------------------------------
# 便捷构造
# ---------------------------------------------------------------------------

def create_session() -> LiveEditingSession:
    """R7-U02 — 新建一个活窗口编辑/IME 接线会话。"""
    return LiveEditingSession()


__all__ = [
    "LiveEditingSession", "KeyTraceEntry", "ImeTraceEntry",
    "SequenceResult", "ImeResult", "create_session",
]


# ---------------------------------------------------------------------------
# 烟测
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    s = create_session()
    s.register_field("email", value="", kind=KIND_ENTRY)
    s.focus_field("email")
    r = s.inject_key_sequence("a", "b", "Backspace", "@", "x")
    print(r.describe())
    # IME 组合：拼音 "nshen" 中间态 → 最终 "你好"
    s2 = create_session()
    s2.register_field("greet", value="")
    s2.focus_field("greet")
    ir = s2.run_ime_composition("nsh", "ni hao", "你好")
    print(ir.describe())

