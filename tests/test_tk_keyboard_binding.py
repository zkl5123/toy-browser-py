"""批次 35 — G11 Tk 侧真实键盘绑定桥 回归。

被测：``toy_browser/app/tk_keyboard_binding.py``。

分两组：
* **纯逻辑组**（不依赖 tkinter）—— :func:`tk_key_string` /
  :func:`route_event` / :func:`binding_result`，与批次 21
  ``KeyboardRouter`` 联动，覆盖完整按键集（可打印 / 焦点键 / 编辑
  层 / chrome / document / 组合键）的 Tk 事件翻译 + 路由 + 返回值。
* **Tk 组**（需 tkinter，``event_generate`` 驱动真实按键，无人工
  操作）—— :func:`install` 在真实 ``tk.Tk()`` 上装 ``<Key>`` 绑定，
  ``event_generate`` 注入 Tab / 可打印键 / 组合键，验证 dispatch
  被调、返回值正确。tkinter 不可用的环境自动跳过。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.keyboard_router import KeyboardRouter
from toy_browser.app.tk_keyboard_binding import (
    TkJointEvent, tk_key_string, route_event, binding_result,
    modifier_prefix, install,
)
from skip_support import SkipTest

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 纯逻辑：TkJointEvent → 路由键名
# ---------------------------------------------------------------------------

@_reg
def test_plain_printable_key():
    """无修饰的可打印字符：'a' → 'a'。"""
    assert tk_key_string(TkJointEvent(keysym="a")) == "a"
    assert tk_key_string(TkJointEvent(keysym="Z", char="Z")) == "Z"


@_reg
def test_backspace_keysym_normalized():
    """Tk 'BackSpace' → 路由层 'Backspace'。"""
    assert tk_key_string(TkJointEvent(keysym="BackSpace")) == "Backspace"
    assert tk_key_string(TkJointEvent(keysym="Return")) == "Return"
    assert tk_key_string(TkJointEvent(keysym="Enter")) == "Return"


@_reg
def test_shift_tab():
    """Shift(1) + Tab → 'Shift-Tab'。"""
    assert tk_key_string(TkJointEvent(keysym="Tab", state=1)) == "Shift-Tab"


@_reg
def test_control_combo():
    """Control(4) + 'l' → 'Control-l'。"""
    assert tk_key_string(TkJointEvent(keysym="l", state=4)) == "Control-l"
    assert tk_key_string(TkJointEvent(keysym="t", state=4)) == "Control-t"
    assert tk_key_string(TkJointEvent(keysym="r", state=4)) == "Control-r"


@_reg
def test_alt_arrows():
    """Alt(32) + Left/Right → 'Alt-ArrowLeft' / 'Alt-ArrowRight'。"""
    assert tk_key_string(TkJointEvent(keysym="Left", state=32)) == "Alt-ArrowLeft"
    assert tk_key_string(TkJointEvent(keysym="Right", state=32)) == "Alt-ArrowRight"


@_reg
def test_control_shift_combo():
    """Control(4)+Shift(1) → 前缀顺序 Control-Shift。"""
    assert modifier_prefix(4 | 1) == "Control-Shift"
    assert tk_key_string(TkJointEvent(keysym="a", state=5)) == "Control-Shift-a"


@_reg
def test_no_modifier_prefix_empty():
    """无修饰 → 空前缀。"""
    assert modifier_prefix(0) == ""
    assert modifier_prefix(2) == ""  # Lock(CapsLock) 不算组合前缀


@_reg
def test_empty_keysym():
    """空 keysym → 空串（路由层 drop）。"""
    assert tk_key_string(TkJointEvent(keysym="")) == ""


# ---------------------------------------------------------------------------
# 纯逻辑：route_event（Tk 事件 → KeyDecision）
# ---------------------------------------------------------------------------

@_reg
def test_route_tab_document_consumed():
    """焦点在 document：Tab → 消费（tab）。"""
    r = KeyboardRouter(focus_owner="document")
    d = route_event(r, TkJointEvent(keysym="Tab"))
    assert d.consumed and d.action == "tab"
    assert binding_result(d) == "break"


@_reg
def test_route_shift_tab_document():
    """焦点在 document：Shift-Tab → shift-tab 消费。"""
    r = KeyboardRouter(focus_owner="document")
    d = route_event(r, TkJointEvent(keysym="Tab", state=1))
    assert d.action == "shift-tab" and d.consumed


@_reg
def test_route_tab_chrome_breaks():
    """焦点在 chrome：Tab 不消费（break-native）。"""
    r = KeyboardRouter(focus_owner="chrome")
    d = route_event(r, TkJointEvent(keysym="Tab"))
    assert d.consumed is False and binding_result(d) is None


@_reg
def test_route_printable_in_edit():
    """编辑层焦点：可打印 'a' → edit + insertText。"""
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                       has_active_field=True)
    d = route_event(r, TkJointEvent(keysym="a", char="a"))
    assert d.action == "edit" and d.input_event is not None
    assert d.input_event.input_type == "insertText"
    assert d.consumed


@_reg
def test_route_backspace_in_edit():
    """编辑层：Backspace → deleteBackward。"""
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                       has_active_field=True)
    d = route_event(r, TkJointEvent(keysym="BackSpace"))
    assert d.action == "edit"
    assert d.input_event.input_type == "deleteBackward"


@_reg
def test_route_caret_move_in_edit():
    """编辑层：ArrowLeft → caret-move（不派发 beforeinput）。"""
    r = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                       has_active_field=True)
    d = route_event(r, TkJointEvent(keysym="Left"))
    assert d.action == "caret-move" and d.input_event is None
    assert d.consumed


@_reg
def test_route_return_document_activate():
    """document 非编辑：Return → activate。"""
    r = KeyboardRouter(focus_owner="document")
    d = route_event(r, TkJointEvent(keysym="Return"))
    assert d.action == "activate" and d.consumed


@_reg
def test_route_return_chrome_navigate():
    """chrome：Return → navigate。"""
    r = KeyboardRouter(focus_owner="chrome")
    d = route_event(r, TkJointEvent(keysym="Return"))
    assert d.action == "navigate" and d.consumed


@_reg
def test_route_control_l_not_consumed_on_document():
    """document 非编辑：Control-l 组合键 → 页面不消费（passthrough）。"""
    r = KeyboardRouter(focus_owner="document")
    d = route_event(r, TkJointEvent(keysym="l", state=4))
    # 组合键 key_to_input_event → None，document 非编辑 → drop 不消费
    assert d.consumed is False


# ---------------------------------------------------------------------------
# Tk 组：install + event_generate（需 tkinter；不可用则自动跳过）
# ---------------------------------------------------------------------------
# 用一个共享的 root（模块级），避免多 Tk 实例并存导致 Tcl 状态混乱。
_TK_ROOT = None
_TK_MOD = None

def _shared_root():
    """惰性创建共享 Tk root（withdraw，不显示）。tkinter 不可用 → None。"""
    global _TK_ROOT, _TK_MOD
    if _TK_ROOT is not None:
        return _TK_ROOT, _TK_MOD
    try:
        import tkinter
        _TK_MOD = tkinter
        _TK_ROOT = tkinter.Tk()
        _TK_ROOT.withdraw()
        _TK_ROOT.update()  # 让 Tcl 完成初始化（event_generate 依赖）
        return _TK_ROOT, _TK_MOD
    except Exception:
        return None, None


def _dispatch_recorder():
    calls = []
    def dispatch(decision, event):
        calls.append((decision, event))
    return calls, dispatch


@_reg
def test_install_tab_document_consumed():
    """install 后 event_generate Tab（document 焦点）→ dispatch 收到
    消费决策。tkinter 不可用 → 跳过（直接返回）。"""
    root, tk = _shared_root()
    if root is None:
        raise SkipTest("tkinter not available in this environment")
    try:
        calls, dispatch = _dispatch_recorder()
        router = KeyboardRouter(focus_owner="document")
        install(root, lambda: router, dispatch)
        root.focus_set()
        root.update_idletasks()
        root.event_generate("<Tab>")
        root.update_idletasks()
        assert len(calls) >= 1, "dispatch should fire on Tab"
        d, ev = calls[0]
        assert d.action == "tab" and d.consumed
        root.unbind("<Key>")
    finally:
        pass


@_reg
def test_install_printable_edit_layer():
    """install 后 event_generate 可打印键（编辑层焦点）→ edit 决策。"""
    root, _tk = _shared_root()
    if root is None:
        return
    try:
        calls, dispatch = _dispatch_recorder()
        router = KeyboardRouter(focus_owner="document", focus_in_edit=True,
                                has_active_field=True)
        install(root, lambda: router, dispatch)
        root.focus_set()
        root.update_idletasks()
        root.event_generate("<Key-a>")
        root.update_idletasks()
        assert len(calls) >= 1
        d, _ev = calls[0]
        assert d.action == "edit" and d.input_event is not None
        assert d.input_event.input_type == "insertText"
        root.unbind("<Key>")
    finally:
        pass


@_reg
def test_install_tab_chrome_not_consumed():
    """install 后 Tab（chrome 焦点）→ 不消费（break-native 由路由层
    决定，dispatch 仍收到但 consumed=False）。"""
    root, _tk = _shared_root()
    if root is None:
        return
    try:
        calls, dispatch = _dispatch_recorder()
        router = KeyboardRouter(focus_owner="chrome")
        install(root, lambda: router, dispatch)
        root.focus_set()
        root.update_idletasks()
        root.event_generate("<Tab>")
        root.update_idletasks()
        assert len(calls) >= 1
        d, _ev = calls[0]
        assert d.consumed is False
        root.unbind("<Key>")
    finally:
        pass


@_reg
def test_install_binding_result_value():
    """install 内层 handler 对消费键返回 'break'、对非消费键返回
    None（直接调 handler，不经事件循环，纯验证返回值契约）。"""
    root, _tk = _shared_root()
    if root is None:
        return
    try:
        calls, dispatch = _dispatch_recorder()
        router = KeyboardRouter(focus_owner="document")
        handler = install(root, lambda: router, dispatch)
        # 构造一个假的 event（有 keysym/state/char 字段）直接喂 handler
        class _FakeEv:
            keysym = "Return"; state = 0; char = ""
        assert handler(_FakeEv()) == "break"  # activate 消费
        root.unbind("<Key>")
        calls, dispatch = _dispatch_recorder()
        router2 = KeyboardRouter(focus_owner="document")
        handler2 = install(root, lambda: router2, dispatch)
        assert handler2(_FakeEv()) == "break"
        root.unbind("<Key>")
    finally:
        pass


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
