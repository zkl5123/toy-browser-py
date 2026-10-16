"""G02 — 鼠标与键盘统一激活回归（assessments/20261016-round5/docs 批次 1）。

验证 activate_element 的统一激活路径：鼠标点击与键盘 Enter 走同一
默认动作表，脚本 onclick 在默认动作前执行。
"""
from __future__ import annotations

from toy_browser.app import gui
from toy_browser.document import dom


TESTS = []


def _page_with_scripts(html: str) -> gui.PageInstance:
    p = gui.PageInstance()
    p.load_html(html, "", 400, 300)
    return p


def test_g02_button_enter_activates():
    """button + Enter：默认动作 = 点击（onclick 执行 + 聚焦）。

    legacy：键盘 Enter 不执行 onclick（SAFE 测试失败）；现在统一
    入口 execute_event_bubbled(click) 后 DOM 状态应更新。
    """
    html = (
        '<html><body>'
        '<p id="label">SAFE</p>'
        '<button id="btn" onclick="document.getElementById(\'label\').textContent=\'CLICKED\'">click</button>'
        '</body></html>'
    )
    p = _page_with_scripts(html)
    # 定位 button 的 DOM index
    btn_idx = None
    tree = p._dom_tree
    counter = [0]

    def _walk(node):
        if node.is_element():
            counter[0] += 1
            if node.attr("id") == "btn":
                nonlocal btn_idx
                btn_idx = counter[0]
        for c in getattr(node, "children", []):
            _walk(c)

    _walk(tree)
    assert btn_idx is not None, "button index must resolve"
    p.set_focus(btn_idx)
    # 模拟键盘 Enter → _activate_focused → activate_element(source=keyboard)
    p.activate_focused_keyboard()
    label_node = None

    def _find(node):
        if node.is_element() and node.attr("id") == "label":
            nonlocal label_node
            label_node = node
        for c in getattr(node, "children", []):
            _find(c)

    _find(tree)
    assert label_node is not None
    assert label_node.text_content() == "CLICKED", (
        f"Enter must run onclick (legacy left it SAFE), got {label_node.text_content()!r}"
    )


def test_g02_checkbox_toggle_unified():
    """checkbox：鼠标与键盘都是 toggle（不再键盘路径误 toggle 文本输入框）。"""
    html = (
        '<html><body>'
        '<input type="checkbox" id="c1" checked>'
        '<input type="text" id="t1" value="x">'
        '</body></html>'
    )
    p = _page_with_scripts(html)
    idx = {}
    counter = [0]

    def _walk(node):
        if node.is_element():
            counter[0] += 1
            n = node
            if "id" in (n.attrs or {}):
                idx[n.attrs["id"]] = counter[0]
        for c in getattr(node, "children", []):
            _walk(c)

    _walk(p._dom_tree)
    # 聚焦 checkbox 并激活 → toggle
    p.set_focus(idx["c1"])
    p.activate_focused_keyboard()
    st = p._form_states.get(idx["c1"])
    assert st and st.get("checked") is False, "checked checkbox must un-toggle"
    # 再次激活 → toggle 回
    p.activate_focused_keyboard()
    st = p._form_states.get(idx["c1"])
    assert st.get("checked") is True, "second activation must re-check"


def test_g02_text_input_focus_only():
    """text 输入框激活只聚焦，不 toggle（legacy 对 input/button 一律
    _toggle_checkbox，文本框也会被误 toggle）。"""
    html = (
        '<html><body>'
        '<input type="text" id="t1" value="x">'
        '</body></html>'
    )
    p = _page_with_scripts(html)
    idx = {}
    counter = [0]

    def _walk(node):
        if node.is_element():
            counter[0] += 1
            n = node
            if "id" in (n.attrs or {}):
                idx[n.attrs["id"]] = counter[0]
        for c in getattr(node, "children", []):
            _walk(c)

    _walk(p._dom_tree)
    p.set_focus(idx["t1"])
    p.activate_focused_keyboard()
    # text 输入框：active_form_field 指向它，且没有 checkbox 状态
    assert p.active_form_field == idx["t1"]
    st = p._form_states.get(idx["t1"])
    assert not st or "checked" not in st, "text input must not gain checked state"


def test_g02_select_focus():
    """select 激活只聚焦（记录 form 状态），不崩溃。"""
    html = (
        '<html><body>'
        '<select id="s1"><option>a</option><option>b</option></select>'
        '</body></html>'
    )
    p = _page_with_scripts(html)
    idx = {}
    counter = [0]

    def _walk(node):
        if node.is_element():
            counter[0] += 1
            n = node
            if "id" in (n.attrs or {}):
                idx[n.attrs["id"]] = counter[0]
        for c in getattr(node, "children", []):
            _walk(c)

    _walk(p._dom_tree)
    p.set_focus(idx["s1"])
    p.activate_focused_keyboard()
    assert p.active_form_field is None or p.active_form_field == idx["s1"]


TESTS.append(("test_g02_button_enter_activates", test_g02_button_enter_activates))
TESTS.append(("test_g02_checkbox_toggle_unified", test_g02_checkbox_toggle_unified))
TESTS.append(("test_g02_text_input_focus_only", test_g02_text_input_focus_only))
TESTS.append(("test_g02_select_focus", test_g02_select_focus))
