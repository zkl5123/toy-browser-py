"""批次 39 — G03 原生覆盖层 Tk 接线回归。

被测：``toy_browser/app/editor_overlay.py``
（:class:`EditorOverlayController` / :class:`OverlayControlInfo`）
+ ``toy_browser/app/gui.py`` 的 G03 接线层
（``_g03_overlay`` / ``_g03_control_info`` / ``_g03_ensure_overlay`` /
``_g03_on_scroll`` / ``_g03_on_blur`` / ``_g03_on_destroy`` /
``_g03_dispatch`` / ``_g03_stats`` + G11 编辑层路由留口）。

设计：纯逻辑核心回归（不强制 tkinter）。``EditorOverlayController``
直接驱动批次 34 协议 + 批次 7/9 EditorState，验证"附着 / 四类同步
触发 / 编辑命令 / 提交写回 / password 分离"；gui 侧用最小桩（注入
``_g03_overlay`` + 桩 page）验证接线方法可跑。tkinter 可用时另用
最小 Tk root 冒烟覆盖层 attach 不抛异常。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.editor_overlay import (
    EditorOverlayController, OverlayControlInfo,
    _CMD_INSERT_TEXT, _CMD_DELETE_BACKWARD, _CMD_MOVE_END, _CMD_IME_START,
    _CMD_IME_UPDATE, _CMD_IME_COMMIT, _CMD_PASTE,
)
from toy_browser.app.overlay_protocol import OverlayRect, Viewport
from skip_support import SkipTest, require_tk

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _ctrl(**kw):
    base = dict(node_ref=1, tag="input", value="abc")
    base.update(kw)
    return OverlayControlInfo(**base)


# ---------------------------------------------------------------------------
# 附着 / 显示决策（G03 step 2 失焦撤销）
# ---------------------------------------------------------------------------

@_reg
def test_attach_editable_shows():
    """可编辑 input 附着 → 覆盖层活动（显示）。"""
    c = EditorOverlayController()
    ok = c.attach(_ctrl(), Viewport(0, 0, 800, 600), has_focus=True)
    assert ok is True
    assert c.active


@_reg
def test_attach_readonly_no_edit_overlay():
    """read-only 控件无编辑覆盖层（should_show_overlay False）。"""
    c = EditorOverlayController()
    ok = c.attach(_ctrl(is_read_only=True), Viewport(), has_focus=True)
    assert ok is False
    assert not c.active


@_reg
def test_attach_no_focus_hides():
    """失焦附着 → 隐藏（G03 step 2 "失焦撤销覆盖层"）。"""
    c = EditorOverlayController()
    ok = c.attach(_ctrl(), Viewport(), has_focus=False)
    assert ok is False
    assert not c.active


# ---------------------------------------------------------------------------
# 四类同步触发（滚动 / 缩放 / 失焦 / 销毁）
# ---------------------------------------------------------------------------

@_reg
def test_scroll_out_hides():
    """控件滚出视口 → 自动隐藏（协议 scrolled_out）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(rect=OverlayRect(10, 20, 100, 30)),
             Viewport(0, 0, 800, 600))
    assert c.active
    c.on_scroll(Viewport(0, 1000, 800, 600))  # 滚出
    assert not c.active
    assert c.stats()["last_reason"] == "scrolled_out"


@_reg
def test_scroll_in_restores():
    """滚回视口 → 恢复可见（协议 scrolled_in）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(rect=OverlayRect(10, 500, 100, 30)),
             Viewport(0, 0, 800, 600))
    c.on_scroll(Viewport(0, 1000, 800, 600))  # 滚出
    assert not c.active
    c.on_scroll(Viewport(0, 450, 800, 600))   # 滚回
    assert c.active


@_reg
def test_zoom_repositions():
    """视口缩放 → 按缩放重算位置（协议 zoomed）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(rect=OverlayRect(10, 10, 100, 30)),
             Viewport(0, 0, 800, 600))
    c.on_zoom(1.0, 2.0)
    assert c.stats()["last_reason"] == "zoomed"
    assert c.active


@_reg
def test_blur_hides():
    """失焦 → 隐藏（协议 hidden）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(), Viewport())
    c.on_blur()
    assert not c.active
    assert c.stats()["last_reason"] == "hidden"


@_reg
def test_destroy_terminal():
    """节点销毁 → 覆盖层终态（destroyed 后不再活动）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(), Viewport())
    c.on_node_destroy()
    assert not c.attached
    assert not c.active


# ---------------------------------------------------------------------------
# 编辑命令（G03 step 3/4/5 beforeinput → value → input）
# ---------------------------------------------------------------------------

@_reg
def test_insert_text_updates_value():
    """insert-text → value 更新 + caret 移到插入点。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value="ab"), Viewport())
    c.state.set_caret(0)
    assert c.dispatch(_CMD_INSERT_TEXT, "X")
    assert c.state.value == "Xab"
    assert c.state.caret == 1


@_reg
def test_delete_backward():
    """Backspace（delete-backward）删 caret 前一字符。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value="abc"), Viewport())
    c.state.set_caret(3)
    assert c.dispatch(_CMD_DELETE_BACKWARD)
    assert c.state.value == "ab"


@_reg
def test_move_end_caret():
    """move-end → caret 到行尾。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value="abc"), Viewport())
    c.state.set_caret(0)
    assert c.dispatch(_CMD_MOVE_END)
    assert c.state.caret == 3


@_reg
def test_ime_commit():
    """IME 组合 start → update → commit 最终提交（G03 step 5）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value=""), Viewport())
    c.dispatch(_CMD_IME_START, ime_mark=0)
    c.dispatch(_CMD_IME_UPDATE, "nihon")
    c.dispatch(_CMD_IME_COMMIT, "にほん")
    assert c.state.value == "にほん"
    assert not c.state.is_composing


@_reg
def test_readonly_dispatch_refused():
    """read-only 控件编辑命令全部拒绝（返回 False）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value="x", is_read_only=True), Viewport())
    assert not c.dispatch(_CMD_INSERT_TEXT, "X")


# ---------------------------------------------------------------------------
# 提交 / 写回 / password 分离（G03 step 4）
# ---------------------------------------------------------------------------

@_reg
def test_write_back_writes_real_value():
    """write_back 写真实 value 到 DOM 属性（password 也写真实值）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value="secret", is_password=True), Viewport())
    dom = {}
    val = c.write_back(dom)
    assert val == "secret"
    assert dom["value"] == "secret"  # 真实值，非遮罩


@_reg
def test_password_display_masked():
    """password 呈现用等长遮罩，真实值分离（display_text）。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value="secret", is_password=True), Viewport())
    masked = c.display_text()
    assert masked == "●" * 6
    assert c.state.value == "secret"  # 真实值保留


@_reg
def test_commit_change_fires_when_dirty():
    """commit 在 value 偏离基线时触发 change。"""
    c = EditorOverlayController()
    c.attach(_ctrl(value=""), Viewport())
    c.dispatch(_CMD_INSERT_TEXT, "hi")
    fired = []
    ok = c.commit(on_change=lambda ev: fired.append(ev.new_value))
    assert ok
    assert fired == ["hi"]


# ---------------------------------------------------------------------------
# gui 侧接线（最小桩）
# ---------------------------------------------------------------------------

class _StubPage:
    """最小 PageInstance 桩（G03 接线所需：yoffset / width / height /
    focus_id / _node_index_to_dom / _save_form_state_to_dom）。"""
    def __init__(self, nodes=None):
        self.yoffset = 0
        self.width = 800
        self.height = 600
        self.focus_id = None
        self._layout_root = None
        self._dom_tree = None
        self._nodes = nodes or {}

    def _node_index_to_dom(self, tree, idx):
        return self._nodes.get(idx)

    def _save_form_state_to_dom(self):
        pass


class _StubNode:
    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or {})

    def attr(self, k):
        return self.attrs.get(k)

    def is_element(self):
        return True


def _stub_gui_browser():
    """绕过 __init__ 的最小 TabBrowser 桩（注入 G03 接线所需属性）。"""
    require_tk()  # R7-Q01 — 缺 Tk 环境 → SKIP，不算功能失败
    from toy_browser.app import gui as _gui
    b = object.__new__(_gui.TabBrowser)
    from toy_browser.app.editor_overlay import EditorOverlayController
    b._g03_overlay = EditorOverlayController()
    b.tabs = [_StubPage()]
    b.current = 0
    return b


@_reg
def test_g03_control_info_input():
    """_g03_control_info 对可编辑 input 返回 OverlayControlInfo。"""
    b = _stub_gui_browser()
    page = b.tabs[0]
    node = _StubNode("input", {"type": "text", "value": "abc"})
    page._nodes[1] = node
    info = b._g03_control_info(page, node, 1)
    assert info is not None
    assert info.tag == "input" and info.value == "abc"


@_reg
def test_g03_control_info_checkbox_none():
    """checkbox 无文本编辑覆盖层（_g03_control_info 返回 None）。"""
    b = _stub_gui_browser()
    page = b.tabs[0]
    node = _StubNode("input", {"type": "checkbox"})
    info = b._g03_control_info(page, node, 1)
    assert info is None


@_reg
def test_g03_ensure_overlay_attaches():
    """_g03_ensure_overlay 附着可编辑 input 覆盖层。"""
    b = _stub_gui_browser()
    page = b.tabs[0]
    node = _StubNode("input", {"type": "text", "value": "abc"})
    page._nodes[1] = node
    ok = b._g03_ensure_overlay(page, node, 1)
    assert ok
    assert b._g03_overlay.active
    assert b._g03_overlay.state.value == "abc"


@_reg
def test_g03_dispatch_routes_to_overlay():
    """_g03_dispatch 把编辑命令路由到覆盖层。"""
    b = _stub_gui_browser()
    page = b.tabs[0]
    node = _StubNode("input", {"type": "text", "value": "ab"})
    page._nodes[1] = node
    b._g03_ensure_overlay(page, node, 1)
    b._g03_overlay.state.set_caret(2)
    b._g03_dispatch("insert-text", "c")
    assert b._g03_overlay.state.value == "abc"


@_reg
def test_g03_on_blur_writes_back_and_hides():
    """_g03_on_blur 写回 DOM value + 失焦撤销覆盖层。"""
    b = _stub_gui_browser()
    page = b.tabs[0]
    node = _StubNode("input", {"type": "text", "value": ""})
    page._nodes[1] = node
    b._g03_ensure_overlay(page, node, 1)
    b._g03_dispatch("insert-text", "hi")
    b._g03_on_blur()
    assert node.attrs.get("value") == "hi"  # 写回 DOM
    assert not b._g03_overlay.active         # 失焦隐藏


@_reg
def test_g03_stats():
    """_g03_stats 返回诊断。"""
    b = _stub_gui_browser()
    page = b.tabs[0]
    node = _StubNode("input", {"type": "text", "value": "x"})
    page._nodes[1] = node
    b._g03_ensure_overlay(page, node, 1)
    s = b._g03_stats()
    assert s["attached"] is True
    assert s["state"] == "visible"


# ---------------------------------------------------------------------------
# 真实 TabBrowser 冒烟（tkinter 可用时验证接线不抛异常）
# ---------------------------------------------------------------------------

_SHARED_ROOT = None

def _shared_root():
    global _SHARED_ROOT
    if _SHARED_ROOT is not None:
        return _SHARED_ROOT
    import tkinter
    r = tkinter.Tk(); r.withdraw(); r.update()
    _SHARED_ROOT = r
    return r


@_reg
def test_real_tabbrowser_g03_smoke():
    """有 Tk 环境时，真实 TabBrowser 的 G03 接线可构造 + 覆盖层
    桩附着不抛异常（活显示 GUI 验证留后续）。"""
    try:
        root = _shared_root()
    except Exception as e:
        raise SkipTest(f"tkinter not available: {e}")
    from toy_browser.app import gui as _gui
    b = _gui.TabBrowser(root)
    try:
        # 覆盖层控制器存在 + 诊断可跑
        assert b._g03_overlay is not None
        assert "attached" in b._g03_stats()
        # _g11_get_router 反映覆盖层活动状态（无覆盖层 → in_edit False）
        r = b._g11_get_router()
        assert r.focus_in_edit is False
    finally:
        pass  # 不 destroy 共享 root（避免全量 image 冲突）


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
