"""批次 21 — G11 键盘全流程纯逻辑路由层（GUI 可注入的按键 → 决策映射）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G11
（批次 7 落了 :class:`KeyboardSession` 的 chrome/document/editor/dialog
焦点路由 + Tab/Shift+Tab/Enter/Escape 全流程；Tk 侧绑定与活显示验收
留本批）。

与已有模块的关系（本模块**不重复**实现按键映射，只做统一路由）：
- **G04**（:class:`~toy_browser.app.foci.FocusManager`）已有 chrome /
  document / editor / dialog 四路路由 + Tab / Return 消费判定；本模块
  把 G11 的完整按键集（Backspace / Delete / Home / End / 方向键 /
  打印键 / Esc / 组合键）纳入同一纯逻辑层。
- **G11**（:class:`~toy_browser.app.accessibility.KeyboardSession`）
  拥有 Tab / Shift+Tab / Enter / Escape 的全流程状态；本模块通过
  可选注入复用其决策。
- **B06**（:func:`toy_browser.app.field_editing.key_to_input_event` +
  :class:`FieldRegistry`）已有编辑层按键 → beforeinput 映射 + 路由
  三分；本模块复用其映射，不重复。

GUI 侧只需对每次按键调用 :meth:`KeyboardRouter.route` 拿一个
:class:`KeyDecision`（action / 目标域 / inputType / 是否消费），把
绑定简化为"查表 + 分派"。无 Tk / 无显示环境依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.field_editing import key_to_input_event
from toy_browser.app.editor import InputEvent

# ---------------------------------------------------------------------------
# G11 完整按键集分类（纯逻辑：哪些键进编辑层 / 哪些是焦点 / 激活 / 导航）
# ---------------------------------------------------------------------------

# 编辑层按键（焦点在 input/textarea 时落地到编辑器）—— 复用 B06 映射。
# 焦点 / 移动键（编辑层内移动 caret，不进页面焦点链）。
_CARET_MOVE = {"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
               "Home", "End", "PageUp", "PageDown"}
# 激活 / 导航键（页面焦点链上执行默认动作）。
_ACTIVATE = {"Return", "KP_Enter", "space", "Escape"}
# 标签移动键。
_TAB_KEYS = {"Tab", "Shift-Tab"}
# 组合键修饰符（单独按下无效果，需与主键组合）。
_MODIFIERS = {"Shift", "Control", "Alt", "Meta", "CapsLock"}


@dataclass
class KeyDecision:
    """一次按键的路由决策（GUI 查表 + 分派的产物）。

    - ``consumed`` — 本路由层是否消费（True → GUI 不应 fall-through）。
    - ``target``   — 目标域：``address`` / ``edit`` / ``page`` /
      ``chrome`` / ``dialog``。
    - ``action``   — 要执行的动作：
      ``navigate``（地址栏提交）/ ``edit``（编辑层输入）/
      ``caret-move``（编辑层 caret 移动）/ ``activate``（页面默认动作）/
      ``tab`` / ``shift-tab`` / ``escape`` / ``drop``（不处理）。
    - ``input_event`` — 编辑层按键的 beforeinput 事件（可空）。
    - ``note``     — 诊断说明（如"非编辑键在字段上→归页面"）。
    """
    consumed: bool = False
    target: str = "page"
    action: str = "drop"
    input_event: Optional[InputEvent] = None
    note: str = ""


class KeyboardRouter:
    """G11 — 纯逻辑键盘路由（把按键 + 当前焦点状态 → 统一决策）。

    焦点状态由调用方（GUI）提供：
    - ``focus_owner`` — 当前键盘焦点归属（chrome / document / editor /
      dialog，G04 四域）。
    - ``focus_in_edit`` — 焦点是否在原生编辑层字段（input/textarea）。
    - ``has_active_field`` — 编辑层存在焦点字段（决定是否进编辑层）。

    按键 API 返回 :class:`KeyDecision`；GUI 按 ``action`` 分派即可，
    不重复实现按键映射（复用 B06 :func:`key_to_input_event` +
    G04 路由语义）。
    """

    def __init__(self, focus_owner: str = "document",
                 focus_in_edit: bool = False,
                 has_active_field: bool = False) -> None:
        self.focus_owner = focus_owner
        self.focus_in_edit = focus_in_edit
        self.has_active_field = has_active_field

    # -- 状态切换（GUI 在焦点变化时调用） ------------------------------------

    def set_focus(self, owner: str, in_edit: bool,
                  has_active_field: bool) -> "KeyboardRouter":
        self.focus_owner = owner
        self.focus_in_edit = in_edit
        self.has_active_field = has_active_field
        return self

    # -- 统一路由 -------------------------------------------------------------

    def route(self, key: str) -> KeyDecision:
        """G11 — 把一次按键路由到 目标域 + 动作（纯逻辑，不改状态）。

        优先级：
        1. 修饰符单键（Shift / Ctrl / …）→ 不消费（fall-through）。
        2. Tab / Shift-Tab：chrome / editor / dialog → break（不消费）；
           document → 进页面焦点链（tab / shift-tab）。
        3. 焦点在编辑层（``focus_in_edit`` 且有活动字段）：
           - 编辑层按键（可打印 / Backspace / Delete / Enter）→ edit；
           - caret 移动键（方向 / Home / End / Page）→ caret-move；
           - Escape → escape（退出编辑 / 取消）；
           - 非编辑键（Tab / 方向焦点等）→ 归页面（drop，note 说明）。
        4. 焦点在地址栏（chrome）：可打印 / 导航键 → 地址栏输入
           （target=address，action=navigate 由 GUI 在 Return 时执行）；
           Return → navigate。
        5. 焦点在页面（document）且非编辑层：Return / 空格 → activate
           （执行焦点元素默认动作）；Escape → escape。
        """
        k = (key or "").strip()
        if not k:
            return KeyDecision(consumed=False, action="drop")

        # 1 — 修饰符单键不消费。
        if k in _MODIFIERS:
            return KeyDecision(consumed=False, target="page",
                               action="drop", note="modifier-only")

        # 2 — Tab / Shift-Tab。
        if k in _TAB_KEYS:
            action = "tab" if k == "Tab" else "shift-tab"
            if self.focus_owner in ("chrome", "editor", "dialog"):
                # 保持 Tk 原生 / 对话框 / 编辑层内部顺序（G04 break）。
                return KeyDecision(consumed=False, target=self.focus_owner,
                                   action=action,
                                   note="break-native-order")
            return KeyDecision(consumed=True, target="document",
                               action=action)

        # 3 — 焦点在编辑层。
        if self.focus_in_edit and self.has_active_field:
            ev = key_to_input_event(k)
            if ev is not None:
                # 可打印 / Backspace / Delete / Enter → 编辑层输入。
                return KeyDecision(consumed=True, target="edit",
                                   action="edit", input_event=ev)
            if k in _CARET_MOVE:
                # caret 移动（编辑层内）→ 不派发 beforeinput，仅移动。
                return KeyDecision(consumed=True, target="edit",
                                   action="caret-move",
                                   note=f"caret:{k}")
            if k == "Escape":
                return KeyDecision(consumed=True, target="edit",
                                   action="escape", note="exit-edit")
            # 非编辑键（Tab 已在 2 处理；方向焦点等）→ 归页面。
            return KeyDecision(consumed=True, target="page",
                               action="drop",
                               note=f"non-edit-key-{k}-to-page")

        # 4 — 焦点在地址栏（chrome）。
        if self.focus_owner == "chrome":
            if k == "Return":
                return KeyDecision(consumed=True, target="address",
                                   action="navigate")
            if key_to_input_event(k) is not None:
                # 可打印字符 → 地址栏输入（GUI 把字符送 addr_entry）。
                return KeyDecision(consumed=True, target="address",
                                   action="edit",
                                   input_event=key_to_input_event(k),
                                   note="address-entry-input")
            if k == "Escape":
                return KeyDecision(consumed=True, target="address",
                                   action="escape", note="clear-address")
            return KeyDecision(consumed=False, target="address",
                               action="drop", note="address-passthrough")

        # 5 — 焦点在页面（document）且非编辑层。
        if self.focus_owner == "document":
            if k in ("Return", "KP_Enter", "space"):
                return KeyDecision(consumed=True, target="page",
                                   action="activate", note="default-action")
            if k == "Escape":
                return KeyDecision(consumed=True, target="page",
                                   action="escape", note="dismiss")
            if k in _CARET_MOVE:
                return KeyDecision(consumed=False, target="page",
                                   action="drop", note="no-focus-target")
            return KeyDecision(consumed=False, target="page",
                               action="drop", note="page-passthrough")

        # dialog / 其他 → 不消费。
        return KeyDecision(consumed=False, target=self.focus_owner,
                           action="drop", note="dialog-or-unknown")

    # -- 诊断 ---------------------------------------------------------------

    def describe(self, key: str) -> str:
        """G11 — 诊断一次按键的路由结果（供日志 / 状态栏）。"""
        d = self.route(key)
        return (f"key={key} owner={self.focus_owner} "
                f"in_edit={self.focus_in_edit} "
                f"→ {d.action} @ {d.target} "
                f"consumed={d.consumed}"
                + (f" [{d.note}]" if d.note else ""))


# ---------------------------------------------------------------------------
# GUI 绑定辅助（纯逻辑：把 :class:`KeyDecision` 翻译成 Tk 应返回的值）
# ---------------------------------------------------------------------------

def tk_binding_result(decision: KeyDecision) -> Optional[str]:
    """GUI 绑定函数里 :meth:`KeyboardRouter.route` 之后，决定 Tk 绑定
    应返回 ``"break"``（消费，阻止 fall-through）还是 ``None``
    （不消费，让 Tk 原生处理）。

    - ``consumed=True`` → ``"break"``（路由层已消费，阻断 Tk 默认）。
    - ``consumed=False`` → ``None``（fall-through 给 Tk 原生顺序）。
    """
    if decision.consumed:
        return "break"
    return None


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    r = KeyboardRouter(focus_owner="document", focus_in_edit=False,
                       has_active_field=False)
    for k in ("Tab", "Return", "a", "Escape", "ArrowLeft"):
        print(r.describe(k))
