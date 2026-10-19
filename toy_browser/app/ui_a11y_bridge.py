"""R7-U03 — 平台 UI Automation 无障碍桥（纯逻辑映射层）。

依据 ``assessments/20261018-round7/docs/逐项改进实施方案.md`` R7-U03：

1. 将现有 accessibility tree（``browser.collect_accessibility_tree`` +
   ``browser.tab_order``）视为**内部基础**，映射到 Windows UI Automation
   等平台桥，维护 ``name / role / value / state / focus`` 事件。
2. Tab 顺序、可见焦点、焦点进入视口、模态对话框焦点归还与键盘默认动作
   统一。
3. 地址栏实际 origin / 最终 URL / 错误类型可见；**TLS 状态不应仅由 URL
   是否 https 推断可信**。

审计核心原则（评估方法与标准基线）："导出可访问性树**不等于**操作
系统读屏可用。没有本轮平台证据时只能写'内部模型已存在'，不能写
'完整无障碍已完成'。"

因此本模块是**纯逻辑映射层**（不 import Windows 运行时 / comtypes /
UIA，可无 OS 回归）：

* :func:`to_uia_node` — 把内部 a11y 节点映射为一个 UIA 控件契约 dict
  （``ControlType`` / ``LocalizedControlType`` / ``Name`` / ``Value`` /
  ``IsEnabled`` / ``IsKeyboardFocusable`` / ``IsFocusable`` /
  ``HasKeyboardFocus`` / 子节点）。这是"把内部树说成 UIA 能理解的话"
  的最小可测映射，不含真实 Windows 事件分发。
* :class:`UiaFocusTracker` — 维护 name/role/value/state/focus **事件流**
  （``focus-moved`` / ``property-changed`` / ``structure-change``），
  读屏器消费的就是这类事件；本模块只生成事件供断言，不做 OS 广播。
* :func:`default_action` — Tab 序上每个 focusable 的键盘默认动作
  （复用 :func:`toy_browser.app.accessibility.focus_action` 语义）。
* :class:`AddressBarState` — 地址栏可见性契约（§3）：origin / 最终
  URL / 加载错误类型 / TLS 信任态。**TLS 是否可信由调用方显式传
  ``tls_trusted``，不从 scheme==https 推断**（审计明确禁止）。

设计边界（与既有纯逻辑模块一致，不重造）：
- 内部 a11y 树已有（G11 批次 7）；键盘全流程状态机已有
  （``accessibility.KeyboardSession``）；本模块只做"内部树 → 平台桥
  契约"的映射与事件流，不重复实现树构建或键盘状态机。
- 真实 Windows UI Automation 事件分发（comtypes / UIAutomation 互操作
  + 真实读屏器验证）属"未验证"，本模块不冒充其通过；调用方在
  具备平台环境时把 :func:`to_uia_node` 的契约喂给平台事件桥即可。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# UIA 本地化控件类型名（与 Windows UIA ControlType 字符串对齐）。
_UIA_LOCALIZED = {
    "link": "Hyperlink",
    "button": "Button",
    "textbox": "Edit",
    "heading": "Text",
    "listitem": "ListItem",
    "list": "List",
    "table": "Table",
    "image": "Image",
    "iframe": "Pane",
    "combobox": "ComboBox",
    "section": "Group",
    "generic": "Text",
    "paragraph": "Text",
    "blockquote": "Text",
    "code": "Text",
    "separator": "Separator",
    "linebreak": "Separator",
}


def to_uia_node(a11y_node: dict, uia_control_type: dict | None = None,
                value: str = "", is_enabled: bool = True,
                has_keyboard_focus: bool = False,
                keyboard_focusable: bool = False) -> dict:
    """把一个内部 a11y 节点映射为 UIA 控件契约（纯逻辑，不依赖 OS）。

    返回 dict：

    * ``ControlType`` — UIA 控件类型名（按 *a11y_node* 的 ``role`` 查
      :data:`_UIA_LOCALIZED`；未知 role 回落 ``Text``）。
    * ``Name`` — 可访问名（aria-label > 文本 > img alt，沿用内部树）。
    * ``Value`` — 值（textbox / combobox 有值；其它为空）。
    * ``IsEnabled`` / ``IsKeyboardFocusable`` / ``IsFocusable`` /
      ``HasKeyboardFocus`` — UIA 状态属性。
    * ``Children`` — 子节点经同一映射（递归）。

    *a11y_node* 形如 ``{"role": "button", "name": "OK",
    "children": [...]}``（``collect_accessibility_tree`` 产物）。
    """
    role = a11y_node.get("role") or "generic"
    localized = _UIA_LOCALIZED.get(role, "Text")
    out: dict = {
        "ControlType": localized,
        "Name": a11y_node.get("name") or "",
        "Value": value,
        "IsEnabled": is_enabled,
        "IsKeyboardFocusable": keyboard_focusable,
        "IsFocusable": keyboard_focusable,
        "HasKeyboardFocus": has_keyboard_focus,
    }
    # 键盘可焦点的控件才是 focusable（UIA 语义：focusable ⊇ keyboard-
    # focusable；玩具简化两者同）。
    children = a11y_node.get("children") or []
    if children:
        out["Children"] = [
            to_uia_node(
                c, uia_control_type,
                value=c.get("name", "") if _is_valued(c.get("role")) else "",
                is_enabled=is_enabled,
                has_keyboard_focus=False,
                keyboard_focusable=_is_keyboard_focusable(c.get("role")),
            )
            for c in children
        ]
    return out


def _is_valued(role: str | None) -> bool:
    return role in ("textbox", "combobox")


def _is_keyboard_focusable(role: str | None) -> bool:
    """哪些 role 键盘可聚焦（与 browser.tab_order 的 focusable 判定一致：
    link / button / textbox(input/textarea) / combobox(select)）。"""
    return role in ("link", "button", "textbox", "combobox")


def default_action(role: Optional[str], key: str = "Enter") -> str:
    """Tab 序上焦点元素 *role* 在 *key* 下的键盘默认动作。

    复用 :mod:`toy_browser.app.accessibility` 的 :func:`focus_action`
    语义（link/button→activate；textbox/combobox→commit；Escape→
    dismiss；其它→none）。此处做薄封装以便 UIA 事件流统一调用。
    """
    from toy_browser.app.accessibility import focus_action
    return focus_action(role, key)


# ---------------------------------------------------------------------------
# UIA 事件流（读屏器消费 name/role/value/state/focus 事件）
# ---------------------------------------------------------------------------

# 事件类型（UIA 对应 property-change / structure-change / focus）。
_EV_FOCUS = "focus-moved"
_EV_PROP = "property-changed"
_EV_STRUCT = "structure-change"
_EVENT_TYPES = (_EV_FOCUS, _EV_PROP, _EV_STRUCT)


@dataclass
class UiaFocusTracker:
    """维护 UIA name/role/value/state/focus **事件流**（纯逻辑）。

    读屏器通过订阅事件流获知"当前焦点在哪个控件、其 name/value/state
    如何变化"。本 tracker 只**生成**事件（供断言 / 回归），不做 OS
    事件广播——平台事件分发由调用方在真实 UIA 环境里接线。

    * :meth:`set_focus` — 焦点移到某控件 id（发 ``focus-moved``，
      记录新焦点 + 其 name/value/state）。
    * :meth:`update_value` — 改某控件 value（发 ``property-changed``）。
    * :meth:`enable` / :meth:`disable` — 改 IsEnabled（发
      ``property-changed``）。
    * :meth:`insert_child` / :meth:`remove_child` — 结构变化（发
      ``structure-change``）。
    * :meth:`events` / :meth:`last` / :meth:`focused` — 查询事件流。
    """

    events: list = field(default_factory=list)
    _focus_id: Optional[str] = None
    _controls: dict = field(default_factory=dict)

    def register(self, ctrl_id: str, name: str = "",
                 value: str = "", role: str = "generic",
                 is_enabled: bool = True) -> None:
        """登记一个 UIA 控件（供事件流携带 name/value/state 快照）。"""
        self._controls[ctrl_id] = {
            "id": ctrl_id, "name": name, "value": value,
            "role": role, "is_enabled": is_enabled,
        }

    def _emit(self, etype: str, payload: dict) -> dict:
        ev = {"type": etype, **payload}
        self.events.append(ev)
        return ev

    def set_focus(self, ctrl_id: Optional[str]) -> dict:
        """焦点移到 *ctrl_id*（None 表示失焦/回到 chrome）。"""
        self._focus_id = ctrl_id
        c = self._controls.get(ctrl_id, {})
        return self._emit(_EV_FOCUS, {
            "id": ctrl_id,
            "name": c.get("name", ""),
            "role": c.get("role", "generic"),
            "value": c.get("value", ""),
            "is_enabled": c.get("is_enabled", True),
        })

    def update_value(self, ctrl_id: str, value: str) -> dict:
        """改控件 value → 发 property-changed。"""
        if ctrl_id in self._controls:
            self._controls[ctrl_id]["value"] = value
        return self._emit(_EV_PROP, {"id": ctrl_id, "prop": "value",
                                     "value": value})

    def enable(self, ctrl_id: str) -> dict:
        if ctrl_id in self._controls:
            self._controls[ctrl_id]["is_enabled"] = True
        return self._emit(_EV_PROP, {"id": ctrl_id, "prop": "IsEnabled",
                                     "value": True})

    def disable(self, ctrl_id: str) -> dict:
        if ctrl_id in self._controls:
            self._controls[ctrl_id]["is_enabled"] = False
        return self._emit(_EV_PROP, {"id": ctrl_id, "prop": "IsEnabled",
                                     "value": False})

    def insert_child(self, parent_id: str, child_id: str) -> dict:
        """父节点新增子控件 → structure-change。"""
        return self._emit(_EV_STRUCT, {"op": "insert",
                                       "parent": parent_id,
                                       "child": child_id})

    def remove_child(self, parent_id: str, child_id: str) -> dict:
        return self._emit(_EV_STRUCT, {"op": "remove",
                                       "parent": parent_id,
                                       "child": child_id})

    # -- 查询 --------------------------------------------------------------
    @property
    def focused(self) -> Optional[str]:
        return self._focus_id

    def last(self) -> dict:
        """最近一条事件（无事件时返空 dict，不崩）。"""
        return self.events[-1] if self.events else {}

    def event_types(self) -> list:
        """按发生顺序返回所有事件的 type 列表（供回归断言事件序列）。"""
        return [e["type"] for e in self.events]


# ---------------------------------------------------------------------------
# 地址栏可见性契约（R7-U03 §3：origin / 最终 URL / 错误类型 / TLS 可信）
# ---------------------------------------------------------------------------

# 常见加载错误类型（与 GUI 错误页 / net 层 error 对齐）。
ERR_NONE = ""
ERR_DNS = "dns_failure"
ERR_CONNECT = "connect_failure"
ERR_TLS = "tls_failure"
ERR_REDIRECT_LOOP = "redirect_loop"
ERR_TIMEOUT = "timeout"
ERR_FORBIDDEN = "forbidden"

# TLS 信任态（不从 URL scheme 推断——审计明确禁止）。
TLS_NOT_APPLICABLE = "not_applicable"
TLS_TRUSTED = "trusted"
TLS_NOT_TRUSTED = "not_trusted"
TLS_UNKNOWN = "unknown"


@dataclass
class AddressBarState:
    """地址栏实际 origin / 最终 URL / 错误类型 / TLS 可信态可见（§3）。

    **关键契约**：*tls_trusted* 必须由调用方**显式传入**（来自 TLS 握手
    结果的真实校验），**绝不能**由 URL 是否 ``https`` 推断（审计原文：
    "TLS 状态不应仅由 URL 是否 https 推断可信"）。本类把
    "scheme 是 https" 与 "证书可信" 严格分离——``scheme_is_https`` 与
    ``tls_trusted`` 是两个独立字段，:meth:`is_secure_display` 只在
    *二者同时成立* 时显示"安全"图标。

    * ``origin`` — 当前文档 origin（协议+host+port，去掉 path/query）。
    * ``final_url`` — 重定向链收敛后的最终 URL（区别于"请求时填的
      URL"；用户看到的应是收敛后的地址）。
    * ``error_type`` — 加载错误类型（``ERR_NONE`` 表示成功）。
    """

    origin: str = ""
    final_url: str = ""
    error_type: str = ERR_NONE
    tls_trusted: str = TLS_NOT_APPLICABLE
    tls_present: bool = False

    def scheme_is_https(self) -> bool:
        """最终 URL 的 scheme 是否 https（**不等于**可信）。"""
        return self.final_url.startswith("https://")

    def is_secure_display(self) -> bool:
        """是否向用户显示"安全"。

        只有 *scheme 是 https* 且 *证书可信*（``tls_trusted`` 为
        ``trusted``）才显示安全；任何一项不满足都显示"不安全"。
        这直接落实审计"不把 https 当可信"的要求。
        """
        return self.scheme_is_https() and self.tls_trusted == TLS_TRUSTED

    def status_line(self) -> str:
        """地址栏诊断行（origin / 最终 URL / 错误 / TLS，供状态栏或读屏）。"""
        parts = [f"origin={self.origin or '-'}",
                 f"url={self.final_url or '-'}"]
        if self.error_type:
            parts.append(f"error={self.error_type}")
        parts.append(f"tls={self.tls_trusted}")
        parts.append("secure" if self.is_secure_display() else "insecure")
        return " ".join(parts)

    def has_error(self) -> bool:
        return self.error_type != ERR_NONE
