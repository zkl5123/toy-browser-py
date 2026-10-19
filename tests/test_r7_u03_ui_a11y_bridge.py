"""R7-U03 — 平台 UI Automation 无障碍桥（纯逻辑映射层）。

审计（round7 逐项改进实施方案 R7-U03）：把内部 a11y 树映射到 Windows
UIA 等平台桥，维护 name/role/value/state/focus 事件；Tab 序键盘默认
动作统一；地址栏实际 origin / 最终 URL / 错误类型 / TLS 状态可见
（**TLS 不从 https 推断可信**）。

本测试锁定 :mod:`toy_browser.app.ui_a11y_bridge` 的核心契约。纯逻辑
层（不 import Windows 运行时，无 Tk / 无 OS 可回归）；平台真实事件
分发 + 读屏器验证属"未验证"（审计原则：导出树 ≠ 读屏可用），不在
本测试范围。

两层设计（与 test_r7_u02_native_overlay 约定一致）：
* **静态层**（无 tkinter 也可跑）：核查 ui_a11y_bridge.py 源码中
  "UIA 控件契约 / 事件流 / 地址栏 TLS 分离" 契约确实存在。
* **动态层**（纯逻辑，任意环境可跑）：真实调用
  :func:`to_uia_node` / :class:`UiaFocusTracker` /
  :class:`AddressBarState` / :func:`default_action` 验证映射、事件
  序列、键盘默认动作、TLS 与 scheme 分离。
"""
from __future__ import annotations

import os
import re


def _br_src() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, os.pardir, "toy_browser", "app",
                        "ui_a11y_bridge.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(r"^\s*(?:def|class)\s+" + re.escape(name) + r"\b",
                    re.MULTILINE)
    m = pat.search(src)
    if not m:
        return ""
    lines = src[m.start():].split("\n")
    first = lines[0]
    indent = len(first) - len(first.lstrip())
    out = [first]
    for line in lines[1:]:
        if not line.strip():
            out.append(line)
            continue
        cur = len(line) - len(line.lstrip())
        if cur <= indent and (line.lstrip().startswith("def ")
                              or line.lstrip().startswith("class ")
                              or line.lstrip().startswith("@")):
            break
        out.append(line)
    return "\n".join(out)


def _mod():
    from toy_browser.app import ui_a11y_bridge as m
    return m


# ---------------------------------------------------------------------------
# 静态层（无 tkinter 也可跑）
# ---------------------------------------------------------------------------

def test_static_uia_node_contract_present():
    """to_uia_node 输出 UIA 控件契约（ControlType/Name/Value/state）。

    静态核查直接对**整份源码**做字符串匹配（避免多行 def 签名 /
    docstring 把方法体切短——动态层已真实调用 to_uia_node 验证
    映射正确，静态层只守住"契约属性确实被生成"）。
    """
    src = _br_src()
    assert "def to_uia_node" in src, "to_uia_node not found"
    for key in ("ControlType", "Name", "Value", "IsEnabled",
                "IsKeyboardFocusable", "HasKeyboardFocus"):
        assert key in src, f"to_uia_node must emit UIA property {key}"


def test_static_event_stream_types_present():
    """事件流维护 name/role/value/state/focus 五类事件。"""
    src = _br_src()
    # UiaFocusTracker 是 @dataclass 类（非 def），按 class 匹配。
    assert "class UiaFocusTracker" in src, "missing UiaFocusTracker"
    for t in ("focus-moved", "property-changed", "structure-change"):
        assert t in src, f"event type {t} must be defined"


def test_static_address_bar_tls_not_inferred_from_scheme():
    """地址栏 TLS 可信态不从 https scheme 推断（审计 §3 明确禁止）。"""
    src = _br_src()
    body = _extract_method(src, "is_secure_display")
    assert body, "is_secure_display not found"
    # 安全显示必须同时满足 scheme_is_https 与 tls_trusted==trusted。
    assert "scheme_is_https" in body, \
        "secure display must consult the https scheme"
    assert "tls_trusted" in body, \
        "secure display must consult explicit tls_trusted (not infer)"
    # 模块常量里 TLS_TRUSTED 独立于 scheme 判定。
    assert "TLS_TRUSTED" in src and "TLS_NOT_TRUSTED" in src


# ---------------------------------------------------------------------------
# 动态层（纯逻辑，任意环境可跑）
# ---------------------------------------------------------------------------

def test_dynamic_uia_node_maps_role_and_state():
    """内部 a11y 节点 → UIA 契约：role→ControlType + 状态属性 + 子递归。"""
    m = _mod()
    node = {
        "role": "section",
        "name": "form",
        "children": [
            {"role": "textbox", "name": "email"},
            {"role": "button", "name": "OK"},
        ],
    }
    out = m.to_uia_node(node, value="")
    assert out["ControlType"] == "Group"          # section→Group
    assert out["Name"] == "form"
    assert out["IsEnabled"] is True
    assert out["IsKeyboardFocusable"] is False    # section 不可键盘聚焦
    kids = out["Children"]
    assert len(kids) == 2
    assert kids[0]["ControlType"] == "Edit"       # textbox→Edit
    assert kids[0]["IsKeyboardFocusable"] is True
    assert kids[1]["ControlType"] == "Button"     # button→Button
    assert kids[1]["IsKeyboardFocusable"] is True


def test_dynamic_uia_node_value_only_for_input_roles():
    """Value 只在 textbox/combobox 有值；其它 role 即使有 name 也不带
    值（UIA Value 语义：可编辑/可选的控件才有 Value 属性）。"""
    m = _mod()
    node = {
        "role": "generic",
        "name": "x",
        "children": [
            {"role": "textbox", "name": "user"},
            {"role": "combobox", "name": "lang"},
            {"role": "heading", "name": "Title"},
        ],
    }
    out = m.to_uia_node(node, value="root-value")
    kids = out["Children"]
    # textbox/combobox 子节点：value 取子 name（_is_valued 分支）。
    assert kids[0]["Value"] == "user"
    assert kids[1]["Value"] == "lang"
    # heading 子节点：不可 valuated，Value 恒空。
    assert kids[2]["Value"] == ""


def test_dynamic_focus_tracker_event_stream():
    """UIA 事件流：focus-moved / property-changed / structure-change
    按序生成，携带 name/role/value/state 快照。"""
    m = _mod()
    t = m.UiaFocusTracker()
    t.register("btn", name="OK", role="button", is_enabled=True)
    t.register("user", name="email", value="", role="textbox")

    ev1 = t.set_focus("btn")
    assert ev1["type"] == "focus-moved"
    assert ev1["id"] == "btn" and ev1["role"] == "button"
    assert ev1["name"] == "OK"
    assert t.focused == "btn"

    ev2 = t.update_value("user", "a@b.c")
    assert ev2["type"] == "property-changed"
    assert ev2["prop"] == "value" and ev2["value"] == "a@b.c"

    ev3 = t.insert_child("root", "user")
    assert ev3["type"] == "structure-change"
    assert ev3["op"] == "insert"

    # 事件序列（读屏器消费的顺序）。
    assert t.event_types() == ["focus-moved", "property-changed",
                               "structure-change"]
    # 最近一条。
    assert t.last()["type"] == "structure-change"


def test_dynamic_focus_tracker_enable_disable_state():
    """disable/enable 发 property-changed（IsEnabled 状态事件）。"""
    m = _mod()
    t = m.UiaFocusTracker()
    t.register("submit", name="Submit", role="button")
    t.disable("submit")
    assert t.last()["type"] == "property-changed"
    assert t.last()["prop"] == "IsEnabled" and t.last()["value"] is False
    t.enable("submit")
    assert t.last()["value"] is True
    assert t.event_types() == ["property-changed", "property-changed"]


def test_dynamic_focus_tracker_unregistered_focus_safe():
    """对未登记控件 set_focus / 空 tracker last 不崩。"""
    m = _mod()
    t = m.UiaFocusTracker()
    ev = t.set_focus("ghost")   # 未登记：快照字段回落默认，不崩
    assert ev["type"] == "focus-moved" and ev["id"] == "ghost"
    assert ev["role"] == "generic" and ev["name"] == ""
    assert t.last()["id"] == "ghost"
    empty = m.UiaFocusTracker()
    assert empty.last() == {}       # 空事件流返空 dict，不崩
    assert empty.focused is None


def test_dynamic_default_action_keyboard():
    """键盘默认动作复用 accessibility.focus_action 语义。"""
    m = _mod()
    assert m.default_action("link", "Enter") == "activate"
    assert m.default_action("button", "Space") == "activate"
    assert m.default_action("textbox", "Enter") == "commit"
    assert m.default_action("heading", "Enter") == "none"
    assert m.default_action("button", "Escape") == "dismiss"


def test_dynamic_address_bar_https_not_trusted_when_cert_invalid():
    """https + 证书不可信 → 不显示安全（审计 §3 核心：不把 https 当可信）。"""
    m = _mod()
    ab = m.AddressBarState(
        origin="https://evil.example",
        final_url="https://evil.example/login",
        tls_present=True,
        tls_trusted=m.TLS_NOT_TRUSTED,
    )
    assert ab.scheme_is_https() is True       # scheme 确实是 https
    assert ab.is_secure_display() is False     # 但证书不可信 → 不安全
    assert "insecure" in ab.status_line()
    assert ab.has_error() is False


def test_dynamic_address_bar_secure_only_when_both():
    """https + 证书可信 → 才显示安全。"""
    m = _mod()
    ab = m.AddressBarState(
        origin="https://ok.example",
        final_url="https://ok.example/",
        tls_present=True,
        tls_trusted=m.TLS_TRUSTED,
    )
    assert ab.is_secure_display() is True
    assert "secure" in ab.status_line()


def test_dynamic_address_bar_http_never_secure():
    """http（无 TLS）→ 永不安全，无论证书态。"""
    m = _mod()
    ab = m.AddressBarState(
        origin="http://plain.example",
        final_url="http://plain.example/",
        tls_present=False,
        tls_trusted=m.TLS_NOT_APPLICABLE,
    )
    assert ab.scheme_is_https() is False
    assert ab.is_secure_display() is False


def test_dynamic_address_bar_error_type_visible():
    """加载错误类型在状态行可见（§3 origin/URL/错误类型可见）。"""
    m = _mod()
    ab = m.AddressBarState(
        origin="https://x.example",
        final_url="https://x.example/500",
        error_type=m.ERR_CONNECT,
        tls_trusted=m.TLS_NOT_TRUSTED,
    )
    assert ab.has_error() is True
    line = ab.status_line()
    assert "error=connect_failure" in line
    assert "url=https://x.example/500" in line


def test_dynamic_to_uia_unknown_role_falls_back_text():
    """未知 role（不在 _UIA_LOCALIZED）→ ControlType 回落 Text，不崩。"""
    m = _mod()
    out = m.to_uia_node({"role": "customwidget", "name": "w"})
    assert out["ControlType"] == "Text"
    assert out["Name"] == "w"


# ---------------------------------------------------------------------------
TESTS = [
    ("test_static_uia_node_contract_present",
     test_static_uia_node_contract_present),
    ("test_static_event_stream_types_present",
     test_static_event_stream_types_present),
    ("test_static_address_bar_tls_not_inferred_from_scheme",
     test_static_address_bar_tls_not_inferred_from_scheme),
    ("test_dynamic_uia_node_maps_role_and_state",
     test_dynamic_uia_node_maps_role_and_state),
    ("test_dynamic_uia_node_value_only_for_input_roles",
     test_dynamic_uia_node_value_only_for_input_roles),
    ("test_dynamic_focus_tracker_event_stream",
     test_dynamic_focus_tracker_event_stream),
    ("test_dynamic_focus_tracker_enable_disable_state",
     test_dynamic_focus_tracker_enable_disable_state),
    ("test_dynamic_focus_tracker_unregistered_focus_safe",
     test_dynamic_focus_tracker_unregistered_focus_safe),
    ("test_dynamic_default_action_keyboard",
     test_dynamic_default_action_keyboard),
    ("test_dynamic_address_bar_https_not_trusted_when_cert_invalid",
     test_dynamic_address_bar_https_not_trusted_when_cert_invalid),
    ("test_dynamic_address_bar_secure_only_when_both",
     test_dynamic_address_bar_secure_only_when_both),
    ("test_dynamic_address_bar_http_never_secure",
     test_dynamic_address_bar_http_never_secure),
    ("test_dynamic_address_bar_error_type_visible",
     test_dynamic_address_bar_error_type_visible),
    ("test_dynamic_to_uia_unknown_role_falls_back_text",
     test_dynamic_to_uia_unknown_role_falls_back_text),
]
