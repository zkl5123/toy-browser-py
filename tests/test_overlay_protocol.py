"""批次 34 — G03 step 2 原生覆盖层位置同步/撤销协议 回归。

被测：``toy_browser/app/overlay_protocol.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.editor import EditorState
from toy_browser.app.overlay_protocol import (
    OverlayPlacement, OverlayRect, Viewport,
    should_show_overlay, overlay_caret_position, password_overlay_text,
    STATE_HIDDEN, STATE_VISIBLE, STATE_DESTROYED,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# OverlayPlacement 状态机
# ---------------------------------------------------------------------------

@_reg
def test_show_hide_basic():
    """hidden → show → visible → hide → hidden。"""
    ov = OverlayPlacement(kind="entry", rect=OverlayRect(10, 20, 200, 30))
    assert ov.state == STATE_HIDDEN
    assert ov.show() is True
    assert ov.state == STATE_VISIBLE and ov.is_visible()
    assert ov.hide() is True
    assert ov.state == STATE_HIDDEN
    assert ov.count_reasons("shown") == 1 and ov.count_reasons("hidden") == 1


@_reg
def test_destroy_is_terminal():
    """destroy 是终态：show / hide / reposition / sync_* 全拒。"""
    ov = OverlayPlacement(kind="entry", rect=OverlayRect(0, 0, 100, 30))
    ov.show()
    ov.destroy()
    assert ov.state == STATE_DESTROYED
    assert ov.show() is False
    assert ov.hide() is False
    ov.reposition(OverlayRect(5, 5, 50, 10))  # 拒
    assert ov.rect.x == 0  # 未变
    ov.sync_scroll(Viewport())  # 拒
    ov.sync_zoom(1.0, 2.0)  # 拒
    assert ov.state == STATE_DESTROYED
    assert ov.count_reasons("destroyed") == 1


@_reg
def test_reposition_visible_keeps_visible():
    """reposition 在 visible 时保留可见、只更新 rect。"""
    ov = OverlayPlacement(kind="textarea", rect=OverlayRect(0, 0, 100, 60))
    ov.show()
    ov.reposition(OverlayRect(20, 40, 120, 80))
    assert ov.state == STATE_VISIBLE
    assert ov.rect.x == 20 and ov.rect.w == 120
    assert ov.count_reasons("repositioned") >= 1


# ---------------------------------------------------------------------------
# sync_scroll（G03 step 2 "滚动…同步或撤销覆盖层"）
# ---------------------------------------------------------------------------

@_reg
def test_scroll_out_hides():
    """visible 控件滚出视口 → 自动隐藏。"""
    ov = OverlayPlacement(kind="entry", rect=OverlayRect(10, 20, 200, 30))
    ov.show()
    vp_out = Viewport(scroll_x=0, scroll_y=1000, width=800, height=600)
    ov.sync_scroll(vp_out)
    assert ov.state == STATE_HIDDEN
    assert ov.last_reason() == "scrolled_out"


@_reg
def test_scroll_in_shows():
    """hidden 控件滚回视口 → 自动显示。"""
    ov = OverlayPlacement(kind="entry", rect=OverlayRect(10, 20, 200, 30))
    ov.show()
    ov.sync_scroll(Viewport(scroll_x=0, scroll_y=1000, width=800, height=600))
    assert ov.state == STATE_HIDDEN
    ov.sync_scroll(Viewport(scroll_x=0, scroll_y=0, width=800, height=600))
    assert ov.state == STATE_VISIBLE
    assert ov.last_reason() == "scrolled_in"


@_reg
def test_scroll_no_change_within_viewport():
    """控件一直在视口内滚动 → 状态不变（只记审计）。"""
    ov = OverlayPlacement(kind="entry", rect=OverlayRect(100, 100, 200, 30))
    ov.show()
    # 视口从 (0,0) 滚到 (0,50)，控件仍在视口内
    ov.sync_scroll(Viewport(scroll_x=0, scroll_y=50, width=800, height=600))
    assert ov.state == STATE_VISIBLE
    assert ov.last_reason() == "repositioned"


# ---------------------------------------------------------------------------
# sync_zoom（G03 step 2 "缩放…同步"）
# ---------------------------------------------------------------------------

@_reg
def test_zoom_scales_rect():
    """缩放 1.0 → 2.0：rect 各分量 ×2。"""
    ov = OverlayPlacement(kind="entry", rect=OverlayRect(10, 20, 100, 40))
    ov.show()
    ov.sync_zoom(1.0, 2.0)
    assert abs(ov.rect.x - 20) < 1e-9
    assert abs(ov.rect.y - 40) < 1e-9
    assert abs(ov.rect.w - 200) < 1e-9
    assert abs(ov.rect.h - 80) < 1e-9
    assert ov.last_reason() == "zoomed"


@_reg
def test_zoom_invalid_refuses():
    """old_zoom<=0 / destroyed → 拒。"""
    ov = OverlayPlacement(kind="entry", rect=OverlayRect(10, 20, 100, 40))
    ov.sync_zoom(0.0, 2.0)  # 拒
    assert abs(ov.rect.x - 10) < 1e-9
    ov2 = OverlayPlacement(kind="entry", rect=OverlayRect(10, 20, 100, 40))
    ov2.destroy()
    ov2.sync_zoom(1.0, 2.0)  # 拒
    assert abs(ov2.rect.x - 10) < 1e-9


# ---------------------------------------------------------------------------
# should_show_overlay（G03 step 2 协议 + 可编辑判定）
# ---------------------------------------------------------------------------

@_reg
def test_should_show_requires_focus():
    """失焦 → 不显示。"""
    st = EditorState("n1", "hi")
    r = OverlayRect(10, 20, 100, 30)
    vp = Viewport(scroll_x=0, scroll_y=0, width=800, height=600)
    assert should_show_overlay(st, vp, r, has_focus=True) is True
    assert should_show_overlay(st, vp, r, has_focus=False) is False


@_reg
def test_should_show_readonly_no():
    """read-only 控件 → 不显示（无编辑覆盖层）。"""
    st = EditorState("n1", "hi", is_password=False, read_only=True)
    r = OverlayRect(10, 20, 100, 30)
    vp = Viewport(scroll_x=0, scroll_y=0, width=800, height=600)
    assert should_show_overlay(st, vp, r, has_focus=True) is False


@_reg
def test_should_show_out_of_viewport_no():
    """控件在视口外 → 不显示。"""
    st = EditorState("n1", "hi")
    r = OverlayRect(10, 2000, 100, 30)  # 视口外
    vp = Viewport(scroll_x=0, scroll_y=0, width=800, height=600)
    assert should_show_overlay(st, vp, r, has_focus=True) is False


@_reg
def test_should_show_in_viewport_yes():
    """控件在视口内 + 焦点 + 可编辑 → 显示。"""
    st = EditorState("n1", "hi")
    r = OverlayRect(10, 20, 100, 30)
    vp = Viewport(scroll_x=0, scroll_y=0, width=800, height=600)
    assert should_show_overlay(st, vp, r, has_focus=True) is True


# ---------------------------------------------------------------------------
# overlay_caret_position（可解释选区位置，G03 step 6）
# ---------------------------------------------------------------------------

@_reg
def test_caret_position_single_line():
    """单行：caret 在 3 → (0, 3)。"""
    st = EditorState("n", "abcdef")
    st.set_caret(3)
    assert overlay_caret_position(st) == (0, 3)


@_reg
def test_caret_position_multi_line():
    """多行："abc\\ndef" caret=5（"d"）→ (1, 1)。"""
    st = EditorState("n", "abc\ndef")
    st.set_caret(5)
    assert overlay_caret_position(st) == (1, 1)


@_reg
def test_caret_position_empty_value():
    """空值 → (0,0) 安全。"""
    st = EditorState("n", "")
    assert overlay_caret_position(st) == (0, 0)


@_reg
def test_caret_position_end_of_value():
    """caret 在末尾 → 正确行/列。"""
    st = EditorState("n", "a\nbb")
    st.set_caret(4)
    # 行 0: "a"(0..1)；行 1: "bb"(2..4)；caret=4 在行 1 末尾 → col=2
    assert overlay_caret_position(st) == (1, 2)


# ---------------------------------------------------------------------------
# password_overlay_text（G03 step 4 显示 / 剪贴板分离）
# ---------------------------------------------------------------------------

@_reg
def test_password_separation():
    """password 控件：overlay 遮罩等长、clipboard 真实值等值。"""
    st = EditorState("n", "secret", is_password=True)
    o, c = password_overlay_text(st)
    assert o == "●" * 6
    assert c == "secret"
    assert len(o) == len(c)
    assert o != c  # 遮罩 ≠ 真实值


@_reg
def test_password_separation_empty():
    """空 password 值：双空。"""
    st = EditorState("n", "", is_password=True)
    o, c = password_overlay_text(st)
    assert o == "" and c == ""


@_reg
def test_non_password_identity():
    """非 password：overlay == clipboard == value。"""
    st = EditorState("n", "hello", is_password=False)
    o, c = password_overlay_text(st)
    assert o == "hello" and c == "hello" and o == c


@_reg
def test_password_mask_not_leaked_to_clipboard():
    """遮罩字符不得出现在剪贴板值（G03 step 4 验收）。"""
    st = EditorState("n", "xYz", is_password=True)
    o, c = password_overlay_text(st)
    assert "●" not in c
    assert c == "xYz"


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
