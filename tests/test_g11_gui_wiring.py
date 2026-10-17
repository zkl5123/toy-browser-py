"""批次 36 — G11 Tk 侧键盘绑定桥接入 gui.py 回归。

被测：``toy_browser/app/gui.py`` 的 G11 接线层
（``_g11_get_router`` / ``_g11_dispatch`` / ``_install_g11_bindings``）。

设计：``TabBrowser.__init__`` 会构建完整 Tk UI（重），本回归用
``object.__new__(TabBrowser)`` 绕过 ``__init__``，只注入 G11 分派所需
的**最小桩属性**（``_g11_dispatch_log`` + 分派目标方法的桩），从而
纯逻辑验证"决策 → 动作"的分派映射 + 增量补全语义（Tab/Return/
Control 这些 G04 已专门绑定的 action 不重复执行），无需 tkinter。
``_install_g11_bindings`` 需 root，单独用一个最小 Tk root 验证安装
不抛异常（tkinter 不可用则跳过）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import gui as _gui
from toy_browser.app.keyboard_router import KeyDecision

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _stub_browser():
    """绕过 __init__ 的最小 TabBrowser 桩（只含 G11 分派所需属性）。"""
    b = object.__new__(_gui.TabBrowser)
    b._g11_dispatch_log = []
    b._activated = []
    b._cleared_addr = []
    b._focus_address_calls = []
    b._tab_calls = []
    b._activate_focused = lambda: b._activated.append("activate")
    b._focus_address = lambda: b._focus_address_calls.append("focus")
    b._tab_focus = lambda forward=True: b._tab_calls.append(forward)
    class _AddrVar:
        def __init__(self): self.v = ""
        def set(self, s): self.v = s
        def get(self): return self.v
    b.addr_var = _AddrVar()
    b._owner = "document"
    b._current_focus_owner = lambda: b._owner
    return b


def _decision(action, target="document", consumed=True, note=""):
    return KeyDecision(consumed=consumed, target=target, action=action,
                       note=note)


def _fake_event(keysym=""):
    class _Ev:
        def __init__(self, k):
            self.keysym = k
            self.state = 0
            self.char = ""
    return _Ev(keysym)


# ---------------------------------------------------------------------------
# _g11_dispatch 分派映射
# ---------------------------------------------------------------------------

@_reg
def test_dispatch_tab_not_reexecuted():
    """Tab（G04 已专门绑定）→ 记录但不重复执行焦点移动。"""
    b = _stub_browser()
    d = _decision("tab", target="document")
    b._g11_dispatch(d, _fake_event("Tab"))
    assert len(b._g11_dispatch_log) == 1
    assert b._tab_calls == []
    assert b._g11_dispatch_log[0][0] == "tab"


@_reg
def test_dispatch_return_navigate_not_reexecuted():
    """navigate（G04 Return 已专门绑定）→ 记录但不导航。"""
    b = _stub_browser()
    b._owner = "chrome"
    d = _decision("navigate", target="address")
    b._g11_dispatch(d, _fake_event("Return"))
    assert len(b._g11_dispatch_log) == 1
    assert b._cleared_addr == []


@_reg
def test_dispatch_activate_document():
    """document 的 activate（空格）→ 激活焦点元素。"""
    b = _stub_browser()
    d = _decision("activate", target="document")
    b._g11_dispatch(d, _fake_event("space"))
    assert b._activated == ["activate"]


@_reg
def test_dispatch_escape_chrome_clears_address():
    """chrome/address 的 escape → 清空地址栏 + 聚焦地址栏。"""
    b = _stub_browser()
    b._owner = "chrome"
    b.addr_var.v = "example.com"
    d = _decision("escape", target="chrome")
    b._g11_dispatch(d, _fake_event("Escape"))
    assert b.addr_var.v == ""
    assert b._focus_address_calls == ["focus"]


@_reg
def test_dispatch_escape_document_blurs():
    """document 的 escape → 取消焦点（blur）。"""
    b = _stub_browser()
    class _Page:
        focus_id = 5
        def set_focus(self, idx):
            self.focus_id = idx
    b.tabs = [_Page()]
    b.current = 0
    b._refresh_canvas = lambda idx: None
    d = _decision("escape", target="document")
    b._g11_dispatch(d, _fake_event("Escape"))
    assert b.tabs[0].focus_id is None


@_reg
def test_dispatch_caret_move_forward_keys():
    """document caret-move：Down/Right/PageDown/End → 焦点前进。"""
    b = _stub_browser()
    for k in ("Down", "Right", "PageDown", "End"):
        b._tab_calls = []
        d = _decision("caret-move", target="document")
        b._g11_dispatch(d, _fake_event(k))
        assert b._tab_calls == [True], f"{k} should move forward"


@_reg
def test_dispatch_caret_move_backward_keys():
    """document caret-move：Up/Left/PageUp/Home → 焦点后退。"""
    b = _stub_browser()
    for k in ("Up", "Left", "PageUp", "Home"):
        b._tab_calls = []
        d = _decision("caret-move", target="document")
        b._g11_dispatch(d, _fake_event(k))
        assert b._tab_calls == [False], f"{k} should move backward"


@_reg
def test_dispatch_edit_address_passthrough():
    """chrome 的可打印键（edit@address）→ 记录但 fall-through。"""
    b = _stub_browser()
    d = _decision("edit", target="address")
    b._g11_dispatch(d, _fake_event("a"))
    assert len(b._g11_dispatch_log) == 1
    assert b._g11_dispatch_log[0][0] == "edit"


# ---------------------------------------------------------------------------
# _g11_get_router（焦点 owner → KeyboardRouter）
# ---------------------------------------------------------------------------

@_reg
def test_get_router_document():
    """owner=document → KeyboardRouter focus_owner='document'。"""
    b = _stub_browser()
    b._owner = "document"
    r = b._g11_get_router()
    assert r.focus_owner == "document"
    assert r.focus_in_edit is False
    assert r.has_active_field is False


@_reg
def test_get_router_chrome():
    """owner=chrome → KeyboardRouter focus_owner='chrome'。"""
    b = _stub_browser()
    b._owner = "chrome"
    r = b._g11_get_router()
    assert r.focus_owner == "chrome"


@_reg
def test_dispatch_records_consumed_flag():
    """分派日志记录 consumed 标志（第 3 元素）。"""
    b = _stub_browser()
    d1 = _decision("tab", consumed=True)
    d2 = _decision("drop", target="chrome", consumed=False)
    b._g11_dispatch(d1, _fake_event("Tab"))
    b._g11_dispatch(d2, _fake_event("x"))
    assert b._g11_dispatch_log[0][2] is True
    assert b._g11_dispatch_log[1][2] is False


# ---------------------------------------------------------------------------
# _install_g11_bindings（需 root；tkinter 可用时验证安装不抛异常）
# ---------------------------------------------------------------------------

@_reg
def test_install_g11_bindings_smoke():
    """有 Tk 环境时，_install_g11_bindings 在真实 root 上装 <Key> 绑定
    不抛异常。无 tkinter 则跳过。"""
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        root.update()
    except Exception:
        return
    try:
        b = _stub_browser()
        b.root = root
        b._install_g11_bindings()
        binds = root.bind("<Key>")
        assert binds
        root.unbind("<Key>")
    finally:
        root.destroy()


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
