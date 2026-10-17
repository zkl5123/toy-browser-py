"""批次 21 — G11 键盘全流程纯逻辑路由层回归。

被测：``toy_browser/app/keyboard_router.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.keyboard_router import (
    KeyboardRouter, KeyDecision, tk_binding_result,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 修饰符单键（不消费，fall-through）
# ---------------------------------------------------------------------------

@_reg
def test_modifier_only_not_consumed():
    """Shift / Ctrl / Alt / Meta 单键不消费（Tk 原生 fall-through）。"""
    r = KeyboardRouter(focus_owner="document")
    for m in ("Shift", "Control", "Alt", "Meta", "CapsLock"):
        d = r.route(m)
        assert d.consumed is False, f"{m} should not consume"
        assert d.action == "drop"


@_reg
def test_empty_key_dropped():
    """空按键 → drop 不消费。"""
    r = KeyboardRouter()
    assert r.route("").consumed is False
    assert r.route(None).consumed is False


# ---------------------------------------------------------------------------
# Tab / Shift-Tab（按焦点域路由，G04 break 语义）
# ---------------------------------------------------------------------------

@_reg
def test_tab_document_consumed():
    """焦点在 document → Tab 消费（进页面焦点链）。"""
    r = KeyboardRouter(focus_owner="document")
    d = r.route("Tab")
    assert d.consumed is True and d.action == "tab" and d.target == "document"
    d2 = r.route("Shift-Tab")
    assert d2.action == "shift-tab" and d2.consumed is True


@_reg
def test_tab_chrome_breaks_native():
    """焦点在 chrome（地址栏）→ Tab 不消费（保持 Tk 原生顺序）。"""
    r = KeyboardRouter(focus_owner="chrome")
    d = r.route("Tab")
    assert d.consumed is False
    assert d.action == "tab"
    assert "break-native" in d.note


@_reg
def test_tab_editor_dialog_breaks():
    """焦点在 editor / dialog → Tab 不消费（内部 / 对话框限定顺序）。"""
    for owner in ("editor", "dialog"):
        r = KeyboardRouter(focus_owner=owner)
        d = r.route("Tab")
        assert d.consumed is False, f"{owner} tab should break"


# ---------------------------------------------------------------------------
# 编辑层按键（焦点在 input/textarea 时落地到编辑器）
# ---------------------------------------------------------------------------

@_reg
def test_edit_layer_printable():
    """焦点在编辑层：可打印字符 → edit + insertText 事件。"""
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                       has_active_field=True)
    d = r.route("a")
    assert d.consumed is True and d.action == "edit" and d.target == "edit"
    assert d.input_event is not None
    assert d.input_event.input_type == "insertText"


@_reg
def test_edit_layer_backspace_delete():
    """编辑层 Backspace / Delete → 对应 deleteBackward / deleteForward。"""
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                       has_active_field=True)
    d1 = r.route("Backspace")
    assert d1.action == "edit" and d1.input_event.input_type == "deleteBackward"
    d2 = r.route("Delete")
    assert d2.input_event.input_type == "deleteForward"


@_reg
def test_edit_layer_caret_move():
    """编辑层方向键 / Home / End / Page → caret-move（不派发 beforeinput）。"""
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                       has_active_field=True)
    for k in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
              "Home", "End", "PageUp", "PageDown"):
        d = r.route(k)
        assert d.action == "caret-move", f"{k} → caret-move"
        assert d.input_event is None, f"{k} no beforeinput"
        assert d.consumed is True


@_reg
def test_edit_layer_escape_exit():
    """编辑层 Escape → 退出编辑 / 取消。"""
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                       has_active_field=True)
    d = r.route("Escape")
    assert d.action == "escape" and d.target == "edit"
    assert "exit-edit" in d.note


@_reg
def test_edit_layer_non_edit_key_to_page():
    """编辑层非编辑键（如 PageUp 已在 caret-move；此处测无活动字段）。"""
    # focus_in_edit=True 但 has_active_field=False → 不进编辑层，按页面。
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                      has_active_field=False)
    d = r.route("Return")
    assert d.target == "page" and d.action == "activate"


# ---------------------------------------------------------------------------
# 地址栏（chrome）按键
# ---------------------------------------------------------------------------

@_reg
def test_chrome_return_navigate():
    """地址栏 Return → navigate（提交地址栏文本，不激活页面焦点链）。"""
    r = KeyboardRouter(focus_owner="chrome")
    d = r.route("Return")
    assert d.consumed is True and d.action == "navigate" and d.target == "address"


@_reg
def test_chrome_printable_edit():
    """地址栏可打印字符 → 地址栏输入（GUI 送 addr_entry）。"""
    r = KeyboardRouter(focus_owner="chrome")
    d = r.route("b")
    assert d.consumed is True and d.target == "address" and d.action == "edit"
    assert d.input_event is not None
    assert "address-entry-input" in d.note


@_reg
def test_chrome_escape_clear():
    """地址栏 Escape → 清除 / 取消地址输入。"""
    r = KeyboardRouter(focus_owner="chrome")
    d = r.route("Escape")
    assert d.action == "escape" and d.target == "address"
    assert "clear-address" in d.note


@_reg
def test_chrome_passthrough():
    """地址栏非编辑键（如 F5）→ 不消费，pass-through 给 Tk。"""
    r = KeyboardRouter(focus_owner="chrome")
    d = r.route("F5")
    assert d.consumed is False and d.action == "drop"


# ---------------------------------------------------------------------------
# 页面（document）非编辑层按键
# ---------------------------------------------------------------------------

@_reg
def test_document_return_activate():
    """页面焦点（非编辑层）Return → activate（执行焦点元素默认动作）。"""
    r = KeyboardRouter(focus_owner="document")
    d = r.route("Return")
    assert d.consumed is True and d.action == "activate"
    assert "default-action" in d.note


@_reg
def test_document_space_activate():
    """页面焦点 空格 → activate。"""
    r = KeyboardRouter(focus_owner="document")
    d = r.route("space")
    assert d.action == "activate" and d.consumed is True


@_reg
def test_document_escape_dismiss():
    """页面焦点 Escape → dismiss。"""
    r = KeyboardRouter(focus_owner="document")
    d = r.route("Escape")
    assert d.action == "escape" and "dismiss" in d.note


@_reg
def test_document_caret_no_focus():
    """页面无焦点目标时方向键 → 不消费（drop，无焦点可移动）。"""
    r = KeyboardRouter(focus_owner="document")
    d = r.route("ArrowLeft")
    assert d.consumed is False
    assert "no-focus-target" in d.note


@_reg
def test_document_passthrough():
    """页面非激活 / 非焦点键（如 F1）→ 不消费。"""
    r = KeyboardRouter(focus_owner="document")
    d = r.route("F1")
    assert d.consumed is False and d.action == "drop"


# ---------------------------------------------------------------------------
# dialog / 未知域
# ---------------------------------------------------------------------------

@_reg
def test_dialog_not_consumed():
    """dialog 域按键 → 不消费（对话框内部处理）。"""
    r = KeyboardRouter(focus_owner="dialog")
    d = r.route("Return")
    assert d.consumed is False
    assert "dialog-or-unknown" in d.note


# ---------------------------------------------------------------------------
# tk_binding_result（GUI 绑定返回值）
# ---------------------------------------------------------------------------

@_reg
def test_tk_binding_break_vs_none():
    """consumed=True → "break"；consumed=False → None。"""
    r = KeyboardRouter(focus_owner="document")
    assert tk_binding_result(r.route("Return")) == "break"
    assert tk_binding_result(r.route("F1")) is None
    r2 = KeyboardRouter(focus_owner="chrome")
    assert tk_binding_result(r2.route("Tab")) is None  # break-native


@_reg
def test_router_set_focus_state():
    """set_focus 切换焦点状态后路由随之变化。"""
    r = KeyboardRouter(focus_owner="document")
    assert r.route("Return").action == "activate"
    r.set_focus("chrome", False, False)
    assert r.route("Return").action == "navigate"
    r.set_focus("document", True, True)
    assert r.route("a").action == "edit"


@_reg
def test_router_describe_diagnostic():
    """describe() 输出诊断串（供日志 / 状态栏）。"""
    r = KeyboardRouter(focus_owner="document")
    s = r.describe("Return")
    assert "key=Return" in s
    assert "activate" in s
    assert "consumed=True" in s


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
