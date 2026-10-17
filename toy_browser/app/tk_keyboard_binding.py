"""批次 35 — G11 Tk 侧真实键盘绑定桥（event_generate 可驱动验证）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G11。

批次 7 落了 :class:`~toy_browser.app.accessibility.KeyboardSession`
（chrome / document / editor / dialog 焦点路由 + Tab / Shift+Tab /
Enter / Escape 全流程纯逻辑）；批次 21 落了 :class:
`~toy_browser.app.keyboard_router.KeyboardRouter`（G11 完整按键集
→ 统一 :class:`KeyDecision`，含 Backspace / Delete / Home / End /
方向键 / 打印键 / Esc / 组合键 + :func:`tk_binding_result` 把决策
翻译成 Tk 的 ``"break"`` / ``None`` 返回值）。

**缺口**：``gui.py`` 目前只把 G04 的 Tab / Return 接到 Tk 绑定
（``_tab_focus_routed`` / ``_return_routed`` + ``FocusManager``），
G11 的完整按键集（Backspace / Delete / Home / End / 方向键 / 打印
键 / Escape / 组合键）尚未进 Tk 绑定。本批补 **Tk 侧真实键盘绑定
桥**：把批次 21 的纯逻辑路由接到 Tk 真实键盘事件上。

设计（与批次 7/21 **兼容不重叠**）：
- 本模块**不重复**按键映射（复用 :func:
  ``toy_browser.app.keyboard_router.KeyboardRouter.route`` +
  :func:`tk_binding_result`），只做 Tk 事件 ↔ 路由键名 的转换 +
  可注入的绑定安装。
- 纯逻辑核心（:class:`TkJointEvent` / :func:`tk_key_string` /
  :func:`route_event`）不依赖 tkinter，可直接回归；tkinter 集成
  （:func:`install`）仅在安装了 Tk 环境时驱动真实按键（测试用
  ``event_generate`` 注入，无需人工按键）。
- 焦点状态由调用方（GUI）经 ``get_router()`` 提供——每次按键调用
  拿当前 :class:`KeyboardRouter`（焦点 / 编辑层状态随操作变化），
  本模块不缓存路由状态。

活显示批次把 :func:`install` 接进 ``gui.py``（``root.bind("<Key>",
handler)`` + ``get_router`` 从 ``_current_focus_owner`` / 编辑层
状态构造 :class:`KeyboardRouter`，``dispatch`` 分派到既有
``_tab_focus_routed`` / ``_return_routed`` / 编辑层 / 激活动作）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

# 纯逻辑路由层（批次 21）；tkinter 集成时正常 import，直接跑本文件
# 无包路径时降级。
try:
    from toy_browser.app.keyboard_router import (
        KeyboardRouter, KeyDecision, tk_binding_result,
    )
except Exception:  # pragma: no cover - 直接跑本文件且无包路径
    KeyboardRouter = None  # type: ignore
    KeyDecision = None  # type: ignore
    tk_binding_result = None  # type: ignore


# ----------------------------------------------------------------------
# Tk 按键事件联合（纯逻辑表示，不依赖 tkinter）
# ----------------------------------------------------------------------

# Tk ``event.state`` 修饰键位（Windows 常见映射）。
SHIFT_BIT = 1
LOCK_BIT = 2
CONTROL_BIT = 4
MOD2_BIT = 8
MOD3_BIT = 16
ALT_BIT = 32       # Mod4 — Alt / AltGr
META_BIT = 128     # Mod1 — Win 键 / Meta


@dataclass
class TkJointEvent:
    """一次 Tk 按键事件的路由相关字段（纯逻辑表示）。

    * ``keysym`` — Tk ``event.keysym``（如 ``"a"`` / ``"Tab"`` /
      ``"BackSpace"`` / ``"Return"``）。
    * ``state``  — Tk ``event.state``（修饰键位掩码）。
    * ``char``   — Tk ``event.char``（可打印字符；控制键为空）。

    与 :class:`KeyboardRouter.route` 的键名字符串解耦——测试可直接
    构造（无需真实 tkinter 事件对象）。
    """
    keysym: str = ""
    state: int = 0
    char: str = ""


# Tk keysym → 路由键名 的别名规范化（批次 21 路由层用的键名）。
# Tk 用 ``"BackSpace"``（大写 S），路由层用 ``"Backspace"``；
# ``"Return"`` / ``"KP_Enter"`` / ``"space"`` 等保持。
# 方向键：Tk keysym 是 ``"Left"`` / ``"Right"`` / ``"Up"`` /
# ``"Down"``，路由层是 ``"ArrowLeft"`` / … 需映射。
# 翻页：Tk ``"Prior"`` / ``"Next"`` → 路由层 ``"PageUp"`` / ``"PageDown"``。
_KEYSYM_ALIAS = {
    "BackSpace": "Backspace",
    "Return": "Return",
    "Enter": "Return",
    "Escape": "Escape",
    # 方向键（Tk 无 "Arrow" 前缀）
    "Left": "ArrowLeft",
    "Right": "ArrowRight",
    "Up": "ArrowUp",
    "Down": "ArrowDown",
    # 翻页（Tk 用 Prior/Next）
    "Prior": "PageUp",
    "Next": "PageDown",
    # 数字小键盘 Enter（Tk 区分 Return/KP_Enter）
    "KP_Enter": "KP_Enter",
}


def _normalize_keysym(keysym: str) -> str:
    return _KEYSYM_ALIAS.get(keysym or "", keysym or "")


def modifier_prefix(state: int) -> str:
    """把 Tk ``state`` 修饰键位翻译成前缀串（如 ``"Control-Shift-"``）。

    无修饰键 → 空串。前缀顺序 Control → Shift → Alt → Meta（与 Tk
    组合键记法一致）。
    """
    parts = []
    if state & CONTROL_BIT:
        parts.append("Control")
    if state & SHIFT_BIT:
        parts.append("Shift")
    if state & ALT_BIT:
        parts.append("Alt")
    if state & META_BIT:
        parts.append("Meta")
    return "-".join(parts)


def tk_key_string(ev: TkJointEvent) -> str:
    """把一次 Tk 按键事件翻译成 :class:`KeyboardRouter.route` 的键名。

    规则：
    - 有修饰键（Control / Shift / Alt / Meta）→ 前缀组合键名
      （``"Control-l"`` / ``"Shift-Tab"`` / ``"Alt-Right"``）。
    - 无修饰键 → 规范化键名（``"a"`` / ``"Backspace"`` / ``"Return"``
      / ``"space"``）。

    可打印移位字符：Tk 对 Shift+a 的 ``keysym`` 已是 ``"A"``，
    无修饰组合键名时直接取规范化 keysym（``"A"``），路由层
    :func:`key_to_input_event` 识别为可打印单字符 → insertText。
    """
    base = _normalize_keysym(ev.keysym)
    prefix = modifier_prefix(ev.state)
    if prefix:
        return prefix + "-" + base
    return base


# ----------------------------------------------------------------------
# 路由桥（纯逻辑：Tk 事件 → KeyDecision）
# ----------------------------------------------------------------------

def route_event(router: "KeyboardRouter", ev: TkJointEvent
                ) -> "KeyDecision":
    """把一次 Tk 按键事件经 :func:`tk_key_string` 翻译后交给
    :class:`KeyboardRouter.route`，返回 :class:`KeyDecision`。

    纯逻辑（不碰 tkinter）；*router* 由调用方按当前焦点 / 编辑层
    状态构造（:meth:`KeyboardRouter.set_focus` 或直接初始化）。
    """
    key = tk_key_string(ev)
    return router.route(key)


def binding_result(decision: "KeyDecision") -> Optional[str]:
    """:class:`KeyDecision` → Tk 绑定函数应返回值（``"break"`` /
    ``None``）。委托 :func:`toy_browser.app.keyboard_router.
    tk_binding_result`（批次 21）。"""
    if tk_binding_result is not None:
        return tk_binding_result(decision)
    # 降级（无包路径直接跑本文件）：等价语义。
    return "break" if decision.consumed else None


# ----------------------------------------------------------------------
# Tk 集成（可选：仅安装了 tkinter 时驱动真实按键）
# ----------------------------------------------------------------------

class KeyDispatch(Protocol):
    """分派协议：:func:`install` 把 :class:`KeyDecision` 交给
    ``dispatch`` 执行动作（navigate / tab / edit / activate / escape /
    …），``dispatch`` 返回 None（动作是否成功由调用方决定，不影响
    Tk 的 ``"break"`` 语义——``break`` 只由路由层 ``consumed`` 决定）。"""
    def __call__(self, decision, event) -> None: ...


def _tk_event_to_joint(event) -> TkJointEvent:
    """真实 Tk ``KeyEvent`` → :class:`TkJointEvent`。"""
    keysym = getattr(event, "keysym", "") or ""
    state = int(getattr(event, "state", 0) or 0)
    ch = getattr(event, "char", "") or ""
    return TkJointEvent(keysym=keysym, state=state, char=ch)


def install(root, get_router, dispatch) -> object:
    """G11 Tk 侧真实键盘绑定桥：在 *root* 上安装 ``<Key>`` 绑定。

    * *get_router* — 每次按键调用的 callable，返回当前焦点 / 编辑层
      状态下的 :class:`KeyboardRouter`（不缓存；焦点状态随操作变化）。
    * *dispatch* — 分派协议 :class:`KeyDispatch`：``dispatch(
      decision, event)``。

    返回安装的内层 handler（供测试 / 解绑用）。绑定函数：
    1. 取当前 :class:`KeyboardRouter`；
    2. 把 Tk 事件 → :class:`KeyDecision`（:func:`route_event`）；
    3. 调 ``dispatch``（执行动作）；
    4. 返回 :func:`binding_result`（``"break"`` 消费 / ``None``
       fall-through，G04 break 语义）。

    仅在实际 Tk 环境可用时调用；测试用 ``root.event_generate`` 注入
    按键驱动（无需人工按键）。
    """
    def handler(event):
        router = get_router()
        decision = route_event(router, _tk_event_to_joint(event))
        dispatch(decision, event)
        return binding_result(decision)
    root.bind("<Key>", handler)
    return handler


__all__ = [
    "TkJointEvent", "SHIFT_BIT", "CONTROL_BIT", "ALT_BIT", "META_BIT",
    "modifier_prefix", "tk_key_string", "route_event", "binding_result",
    "KeyDispatch", "install",
]
