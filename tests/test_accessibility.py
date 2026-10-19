"""G11 — 可访问性键盘全流程回归（round5 docs 批次 7）。

覆盖 toy_browser/app/accessibility.py：
- FocusViewport.scroll_for_focus：焦点元素滚入可见区域（已可见不跳、
  顶部越上对齐顶部、底部越下对齐底部、clamp 到 scroll_max）
- mouseless_tasks：从 a11y 树 + tab_order 生成无鼠标任务清单
  （id/role/name/action，键盘全流程可重复）
- focus_action：焦点元素上 Enter/Space/Escape 默认动作
  （link/button activate，textbox commit，escape dismiss）
- KeyboardSession：chrome/document/editor/dialog 键盘状态机
  （tab / shift_tab / enter / escape / 域切换）
"""
from __future__ import annotations

from toy_browser.app.accessibility import (
    FocusViewport, mouseless_tasks, focus_action, KeyboardSession,
    FOCUS_CHROME, FOCUS_DOCUMENT, FOCUS_EDITOR, FOCUS_DIALOG,
)


TESTS = []


# ----------------------------------------------------------------------
# FocusViewport — 焦点滚入可见区域
# ----------------------------------------------------------------------
def test_focus_visible_no_scroll():
    """焦点元素已完全在视口内 → 不滚动（避免无谓跳动）。"""
    vp = FocusViewport(scroll_offset=100, viewport_h=600, content_h=2000)
    # 元素 top=120 height=40：在 [108, 700] 内 → 已可见
    assert vp.scroll_for_focus(120, 40) == 100


def test_focus_above_scroll_to_top():
    """焦点元素顶部在视口之上 → 滚到元素顶部（留 padding）。"""
    vp = FocusViewport(scroll_offset=100, viewport_h=600, content_h=2000)
    # 元素 top=20：在视口 100 之上 → 滚到 20-8=12
    assert vp.scroll_for_focus(20, 40) == 12


def test_focus_below_scroll_to_bottom():
    """焦点元素底部在视口之下 → 滚到让底部可见。"""
    vp = FocusViewport(scroll_offset=100, viewport_h=600, content_h=2000)
    # 元素 top=800 height=100 → 底部 900，视口底 700，需滚到 900-600+8=308
    assert vp.scroll_for_focus(800, 100) == 308


def test_focus_clamped_to_scroll_max():
    """滚动目标 clamp 到 [0, scroll_max]。"""
    vp = FocusViewport(scroll_offset=0, viewport_h=600, content_h=1000)
    # scroll_max = 400；元素 top=900 h=100 → 目标 408 > 400 → clamp 400
    assert vp.scroll_for_focus(900, 100) == 400


def test_focus_contains():
    vp = FocusViewport(scroll_offset=100, viewport_h=600, content_h=2000)
    assert vp.contains(120, 40) is True
    assert vp.contains(800, 100) is False


# ----------------------------------------------------------------------
# mouseless_tasks — 无鼠标任务清单
# ----------------------------------------------------------------------
def _a11y_with_ids():
    return [
        {"role": "section", "name": "main",
         "children": [
             {"id": "nav-link", "role": "link", "name": "首页",
              "url": "https://a"},
             {"id": "btn", "role": "button", "name": "提交"},
             {"id": "field", "role": "textbox", "name": "搜索框"},
         ]},
    ]


def test_mouseless_tasks_orders_by_tab_order():
    tree = _a11y_with_ids()
    tasks = mouseless_tasks(tree, ["nav-link", "btn", "field"])
    assert [t["id"] for t in tasks] == ["nav-link", "btn", "field"]
    # 各条携带 role / name / action
    by_id = {t["id"]: t for t in tasks}
    assert by_id["nav-link"]["action"] == "activate"
    assert by_id["btn"]["action"] == "activate"
    assert by_id["field"]["action"] == "input"
    assert by_id["nav-link"]["name"] == "首页"


def test_mouseless_tasks_unknown_id_fallback_action():
    tree = _a11y_with_ids()
    tasks = mouseless_tasks(tree, ["ghost"])
    assert tasks[0]["id"] == "ghost"
    # 未知 id → 无 role，action 回退 browse
    assert tasks[0]["action"] == "browse"
    assert tasks[0]["role"] is None


def test_mouseless_tasks_empty():
    assert mouseless_tasks(_a11y_with_ids(), []) == []


# ----------------------------------------------------------------------
# focus_action — 键盘默认动作
# ----------------------------------------------------------------------
def test_focus_action_button_enter_space():
    assert focus_action("button", "Enter") == "activate"
    assert focus_action("button", "Space") == "activate"
    assert focus_action("button", "Tab") == "none"


def test_focus_action_link_activate():
    assert focus_action("link", "Enter") == "activate"


def test_focus_action_textbox_commit_enter():
    assert focus_action("textbox", "Enter") == "commit"
    assert focus_action("textbox", "Space") == "none"


def test_focus_action_combobox():
    assert focus_action("combobox", "Enter") == "commit"


def test_focus_action_escape_dismiss():
    assert focus_action("button", "Escape") == "dismiss"
    assert focus_action(None, "Escape") == "dismiss"


def test_focus_action_generic_none():
    assert focus_action("heading", "Enter") == "none"
    assert focus_action(None, "Enter") == "none"


# ----------------------------------------------------------------------
# KeyboardSession — 键盘全流程状态机
# ----------------------------------------------------------------------
def _session():
    return KeyboardSession(
        domain=FOCUS_CHROME, cursor=-1,
        focusable=["a", "b", "c"])


def test_kbd_tab_from_chrome_enters_document():
    s = _session()
    r = s.tab()
    assert r["domain"] == FOCUS_DOCUMENT
    assert r["cursor"] == 0
    assert r["focused_id"] == "a"


def test_kbd_tab_cycles_forward():
    s = _session()
    s.tab()               # a (0)
    assert s.tab()["cursor"] == 1
    assert s.tab()["cursor"] == 2
    assert s.tab()["cursor"] == 0   # 循环回 a


def test_kbd_shift_tab_back_to_chrome():
    s = _session()
    s.tab()               # a
    r = s.shift_tab()     # 首位 → 回 chrome
    assert r["domain"] == FOCUS_CHROME
    assert r["cursor"] == -1
    assert r["action"] == "to-chrome"


def test_kbd_shift_tab_walks_back():
    s = _session()
    s.tab()               # a
    s.tab()               # b
    s.tab()               # c
    assert s.shift_tab()["cursor"] == 1  # b
    assert s.shift_tab()["cursor"] == 0  # a


def test_kbd_enter_activates_current():
    s = _session()
    s.tab()               # a
    r = s.enter()
    assert r["action"] == "activate"
    assert r["focused_id"] == "a"


def test_kbd_enter_no_focus_none():
    s = _session()
    assert s.enter()["action"] == "none"


def test_kbd_escape_dialog_to_document():
    s = _session()
    s.tab()
    s.enter_dialog()
    assert s.domain == FOCUS_DIALOG
    r = s.escape()
    assert r["domain"] == FOCUS_DOCUMENT
    assert r["action"] == "dismiss"


def test_kbd_escape_document_to_chrome():
    s = _session()
    s.tab()
    r = s.escape()
    assert r["domain"] == FOCUS_CHROME
    assert r["action"] == "dismiss"


def test_kbd_enter_editor_by_id():
    s = _session()
    s.tab()
    r = s.enter_editor("b")
    assert r["domain"] == FOCUS_EDITOR
    assert r["cursor"] == 1
    assert r["focused_id"] == "b"
    # 未知 id 不切换域
    r2 = s.enter_editor("zzz")
    assert r2["domain"] == FOCUS_EDITOR  # 保持 editor（enter_editor 仅切换已知）
    # 已知才移动；未知 id 在任何起始域都不切换（R7-Q01: 去掉恒真 or True）
    s2 = _session()
    s2.tab()
    before_domain = s2.domain
    assert s2.enter_editor("zzz")["domain"] == before_domain


def test_kbd_report_snapshot():
    s = _session()
    s.tab()
    rep = s.report()
    assert rep["domain"] == FOCUS_DOCUMENT
    assert rep["cursor"] == 0
    assert rep["focused_id"] == "a"
    assert rep["n_focusable"] == 3


TESTS.append(("test_focus_visible_no_scroll",
              test_focus_visible_no_scroll))
TESTS.append(("test_focus_above_scroll_to_top",
              test_focus_above_scroll_to_top))
TESTS.append(("test_focus_below_scroll_to_bottom",
              test_focus_below_scroll_to_bottom))
TESTS.append(("test_focus_clamped_to_scroll_max",
              test_focus_clamped_to_scroll_max))
TESTS.append(("test_focus_contains", test_focus_contains))
TESTS.append(("test_mouseless_tasks_orders_by_tab_order",
              test_mouseless_tasks_orders_by_tab_order))
TESTS.append(("test_mouseless_tasks_unknown_id_fallback_action",
              test_mouseless_tasks_unknown_id_fallback_action))
TESTS.append(("test_mouseless_tasks_empty", test_mouseless_tasks_empty))
TESTS.append(("test_focus_action_button_enter_space",
              test_focus_action_button_enter_space))
TESTS.append(("test_focus_action_link_activate",
              test_focus_action_link_activate))
TESTS.append(("test_focus_action_textbox_commit_enter",
              test_focus_action_textbox_commit_enter))
TESTS.append(("test_focus_action_combobox",
              test_focus_action_combobox))
TESTS.append(("test_focus_action_escape_dismiss",
              test_focus_action_escape_dismiss))
TESTS.append(("test_focus_action_generic_none",
              test_focus_action_generic_none))
TESTS.append(("test_kbd_tab_from_chrome_enters_document",
              test_kbd_tab_from_chrome_enters_document))
TESTS.append(("test_kbd_tab_cycles_forward",
              test_kbd_tab_cycles_forward))
TESTS.append(("test_kbd_shift_tab_back_to_chrome",
              test_kbd_shift_tab_back_to_chrome))
TESTS.append(("test_kbd_shift_tab_walks_back",
              test_kbd_shift_tab_walks_back))
TESTS.append(("test_kbd_enter_activates_current",
              test_kbd_enter_activates_current))
TESTS.append(("test_kbd_enter_no_focus_none",
              test_kbd_enter_no_focus_none))
TESTS.append(("test_kbd_escape_dialog_to_document",
              test_kbd_escape_dialog_to_document))
TESTS.append(("test_kbd_escape_document_to_chrome",
              test_kbd_escape_document_to_chrome))
TESTS.append(("test_kbd_enter_editor_by_id",
              test_kbd_enter_editor_by_id))
TESTS.append(("test_kbd_report_snapshot",
              test_kbd_report_snapshot))
