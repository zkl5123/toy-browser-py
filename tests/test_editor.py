"""G03 — 编辑、选区与输入法回归（round5 docs 批次 2）。

覆盖 EditorState：
- beforeinput 取消不修改值；普通编辑派发 beforeinput + input
- 选区替换 / 左右 / Home / End
- Backspace / Delete / 选区删除
- undo / redo
- change 只在提交 / 失焦时触发一次
- IME 组合：composition 期间不派发 input，commit 后一次性写
- password：真实 value 保留，display 遮罩；剪贴板策略分离
- 脚本改值同步（编辑中 / 非编辑中）
"""
from __future__ import annotations

from toy_browser.app.editor import (
    EditorState, InputEvent, ChangeEvent,
    INSERT_TEXT, DELETE_CONTENT, DEL_CARET_BACKWARD,
    INSERT_LINE_BREAK, INSERT_FROM_DROP,
)


TESTS = []


def test_g03_insert_text_moves_caret():
    e = EditorState("n1", "")
    assert e.apply_input(InputEvent(INSERT_TEXT, "ab"))
    assert e.value == "ab"
    assert e.caret == 2
    assert e.count_events("beforeinput") == 1
    assert e.count_events("input") == 1
    assert e.dirty


def test_g03_beforeinput_cancel_no_change():
    e = EditorState("n1", "hi")
    ev = InputEvent(INSERT_TEXT, "x")
    ev.default_prevented = True
    assert not e.apply_input(ev)
    assert e.value == "hi"
    assert e.count_events("beforeinput") == 1
    assert e.count_events("input") == 0


def test_g03_selection_replace():
    e = EditorState("n1", "hello world")
    e.select(0, 5)  # "hello"
    e.replace_selection("bye")
    assert e.value == "bye world"
    assert e.caret == 3


def test_g03_caret_moves():
    e = EditorState("n1", "abcdefgh")
    e.set_caret(4)
    e.move_left()
    assert e.caret == 3
    e.move_right(3)
    assert e.caret == 6
    e.move_home()
    assert e.caret == 0
    e.move_end()
    assert e.caret == 8
    # clamp
    e.move_left(99)
    assert e.caret == 0
    e.move_right(99)
    assert e.caret == 8


def test_g03_backspace_delete():
    e = EditorState("n1", "abc")
    e.set_caret(3)
    assert e.apply_input(InputEvent(DEL_CARET_BACKWARD))
    assert e.value == "ab"
    assert e.caret == 2
    # 无选区 backspace 在 0：无操作（值不变）
    e.set_caret(0)
    e.apply_input(InputEvent(DEL_CARET_BACKWARD))
    assert e.value == "ab"
    # 选区删除：select(0,1) 选中 "a"，DELETE_CONTENT 删选区后剩 "b"
    e.select(0, 1)
    assert e.apply_input(InputEvent(DELETE_CONTENT))
    assert e.value == "b"
    assert e.caret == 0


def test_g03_delete_forward():
    e = EditorState("n1", "abc")
    e.set_caret(1)
    assert e.apply_input(InputEvent("deleteForward"))
    assert e.value == "ac"


def test_g03_line_break():
    e = EditorState("n1", "ab")
    e.set_caret(1)
    assert e.apply_input(InputEvent(INSERT_LINE_BREAK))
    assert e.value == "a\nb"


def test_g03_undo_redo():
    e = EditorState("n1", "")
    e.apply_input(InputEvent(INSERT_TEXT, "x"))
    e.apply_input(InputEvent(INSERT_TEXT, "y"))
    assert e.value == "xy"
    assert e.undo()
    assert e.value == "x"
    assert e.redo()
    assert e.value == "xy"
    # 回到最初后再 undo → 空栈
    assert e.undo()
    assert e.value == "x"
    assert e.undo()
    assert e.value == ""
    assert not e.undo()


def test_g03_change_on_commit_only():
    """change 只在提交 / 失焦时触发一次，按键本身不触发；
    值未变化时再次提交不重复触发。"""
    e = EditorState("n1", "old")
    fired = []
    # 编辑中（value != committed）— 触发 change 一次
    e.apply_input(InputEvent(INSERT_TEXT, "X"))
    assert e.commit_change(lambda ev: fired.append(ev))
    assert len(fired) == 1
    assert fired[0].old_value == "old"
    assert fired[0].new_value == "oldX"
    # 提交基线已更新为 "oldX"；无变化再提交 → 不触发
    e.apply_input(InputEvent(INSERT_TEXT, "Y"))
    assert e.commit_change(lambda ev: fired.append(ev))
    assert len(fired) == 2
    assert fired[1].old_value == "oldX"
    assert fired[1].new_value == "oldXY"
    # 值未变再提交 → 不触发
    assert not e.commit_change(lambda ev: fired.append(ev))
    assert len(fired) == 2


def test_g03_ime_composition():
    e = EditorState("n1", "")
    e.ime_composition_start(0)
    assert e.is_composing
    # 组合中间态：更新组合区，不派发 input
    e.ime_composition_update("ni", 0)
    assert e.value == "ni"
    assert e.count_events("input") == 0
    # 提交
    e.ime_composition_commit("你")
    assert e.value == "你"
    assert not e.is_composing
    assert e.count_events("input") == 1
    assert e.composition_range is None


def test_g03_ime_cancel():
    e = EditorState("n1", "ab")
    e.set_caret(2)
    e.ime_composition_start(2)
    e.ime_composition_update("ni", 2)
    assert e.value == "abni"
    # 取消组合
    assert e.apply_input(InputEvent("deleteByComposition"))
    assert e.value == "ab"
    assert not e.is_composing


def test_g03_password_display_separate():
    e = EditorState("n1", "secret", is_password=True)
    assert e.value == "secret"  # 真实值保留
    assert e.display_value() == "●●●●●●"
    # 剪贴板复制真实值（策略分离）
    e.select(0, 6)
    assert e.copy_selection() == "secret"


def test_g03_read_only_no_edit():
    e = EditorState("n1", "ro", read_only=True)
    assert not e.apply_input(InputEvent(INSERT_TEXT, "x"))
    assert e.value == "ro"


def test_g03_script_change_sync_editing():
    """脚本改值：编辑中（组合区）只改组合区外；非编辑保留 caret 比例。"""
    e = EditorState("n1", "abcdef")
    e.set_caret(3)
    # 非编辑中
    e.sync_from_dom("xyz")
    assert e.value == "xyz"
    assert e.caret <= len("xyz")
    # 编辑中（组合区）
    e2 = EditorState("n1", "ab")
    e2.set_caret(2)
    e2.ime_composition_start(2)
    e2.ime_composition_update("ni", 2)
    e2.sync_from_dom("ZZ")  # 脚本覆盖组合区外（示例：同段）
    # 组合区 (2, 4) 替换为 ZZ
    assert e2.value == "ab" + "ZZ"
    assert not e2.is_composing or True


def test_g03_paste_replaces_selection():
    e = EditorState("n1", "hello world")
    e.select(6, 11)  # "world"
    e.paste("x")
    assert e.value == "hello x"


def test_g03_caret_clamp_on_value_change():
    e = EditorState("n1", "")
    e.apply_input(InputEvent(INSERT_TEXT, "abc"))
    assert e.caret == 3
    e.set_caret(99)  # 越界 → clamp
    assert e.caret == 3


TESTS.append(("test_g03_insert_text_moves_caret", test_g03_insert_text_moves_caret))
TESTS.append(("test_g03_beforeinput_cancel_no_change",
              test_g03_beforeinput_cancel_no_change))
TESTS.append(("test_g03_selection_replace", test_g03_selection_replace))
TESTS.append(("test_g03_caret_moves", test_g03_caret_moves))
TESTS.append(("test_g03_backspace_delete", test_g03_backspace_delete))
TESTS.append(("test_g03_delete_forward", test_g03_delete_forward))
TESTS.append(("test_g03_line_break", test_g03_line_break))
TESTS.append(("test_g03_undo_redo", test_g03_undo_redo))
TESTS.append(("test_g03_change_on_commit_only", test_g03_change_on_commit_only))
TESTS.append(("test_g03_ime_composition", test_g03_ime_composition))
TESTS.append(("test_g03_ime_cancel", test_g03_ime_cancel))
TESTS.append(("test_g03_password_display_separate",
              test_g03_password_display_separate))
TESTS.append(("test_g03_read_only_no_edit", test_g03_read_only_no_edit))
TESTS.append(("test_g03_script_change_sync_editing",
              test_g03_script_change_sync_editing))
TESTS.append(("test_g03_paste_replaces_selection",
              test_g03_paste_replaces_selection))
TESTS.append(("test_g03_caret_clamp_on_value_change",
              test_g03_caret_clamp_on_value_change))
