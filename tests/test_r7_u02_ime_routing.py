"""R7-U02 — editor-layer IME composition routing + select-all entry.

审计 §57 残留："EditorOverlayController 只是状态模型，composition 存在
不等同操作系统输入法已经接入"。 纯逻辑 IME 状态机（
:meth:`EditorState.ime_composition_start/update/commit`）早已完善，但
键盘入口没把 IME 组合事件映射进去（可打印字符一律走 insert-text，
组合中的拼音候选没有被区分），ctrl+a 全选编辑入口也未接。

本测试锁定两块契约：
* **select-all 命令** — ``dispatch("select-all")`` 把选区设为
  ``(0, len(value))``；read-only 拒绝；无覆盖层返回 False。
* **IME 组合三态** — 经 ``dispatch_ime_start`` / ``dispatch_ime_update`` /
  ``dispatch_ime_commit`` 驱动组合区：start 标 ``composition_range``，
  update 在组合区内替换中间态（不派发 input），commit 替换最终文本并
  一次性派发 input；commit 后 ``is_composing`` 归位、
  ``composition_range`` 清空。

测试直接驱动 :class:`EditorOverlayController`（纯逻辑，无 tkinter）。
"""
from __future__ import annotations

import os
import re
import sys


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _ctrl(tag="input", value="", is_password=False, is_read_only=False):
    """构造一个附着了覆盖层的活动控制器。"""
    from toy_browser.app.editor_overlay import EditorOverlayController
    from toy_browser.app.overlay_protocol import OverlayRect, Viewport
    from toy_browser.app.editor_overlay import OverlayControlInfo
    info = OverlayControlInfo(
        node_ref=1, tag=tag, value=value,
        is_password=is_password, is_read_only=is_read_only,
        rect=OverlayRect(10, 20, 200, 30))
    c = EditorOverlayController()
    c.attach(info, Viewport(0, 0, 800, 600), has_focus=True)
    return c


def _gui_source() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    gui_path = os.path.join(here, os.pardir, "toy_browser", "app", "gui.py")
    with open(gui_path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_method(src: str, method_name: str) -> str:
    pat = re.compile(r"^\s*def " + re.escape(method_name) + r"\b", re.MULTILINE)
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
        if cur <= indent and line.lstrip().startswith("def "):
            break
        out.append(line)
    return "\n".join(out)


def _ov_source() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    ov_path = os.path.join(here, os.pardir, "toy_browser", "app",
                           "editor_overlay.py")
    with open(ov_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# select-all 命令
# ---------------------------------------------------------------------------

def test_select_all_expands_selection_to_full_value():
    c = _ctrl(value="hello world")
    assert c.dispatch("select-all") is True
    st = c.state
    assert st.selection_start == 0
    assert st.selection_end == len(st.value), f"end={st.selection_end}"
    # 选区文本 = 整段 value
    assert st.copy_selection() == "hello world"


def test_select_all_then_insert_replaces_full_value():
    c = _ctrl(value="abc")
    c.dispatch("select-all")
    # 选区被替换（insert-text 替换当前选区）
    c.dispatch("insert-text", "XYZ")
    assert c.state.value == "XYZ", f"value={c.state.value!r}"


def test_select_all_on_readonly_refused():
    c = _ctrl(value="abc", is_read_only=True)
    assert c.dispatch("select-all") is False
    # read-only 选区不应被改动
    assert c.state.selection_start == len("abc")  # caret 默认末尾


def test_select_all_no_overlay_returns_false():
    from toy_browser.app.editor_overlay import EditorOverlayController
    c = EditorOverlayController()
    assert c.dispatch("select-all") is False


# ---------------------------------------------------------------------------
# IME 组合三态
# ---------------------------------------------------------------------------

def test_ime_start_marks_composition_range():
    c = _ctrl(value="")
    c.dispatch("ime-start", ime_mark=0)
    st = c.state
    assert st.is_composing is True
    assert st.composition_range == (0, 0)


def test_ime_update_replaces_composition_intermediate():
    c = _ctrl(value="")
    c.dispatch("ime-start", ime_mark=0)
    # 拼音候选中间态（不派发 input）
    c.dispatch("ime-update", "zhong", 0)
    st = c.state
    assert st.value == "zhong", f"value={st.value!r}"
    assert st.is_composing is True
    # compositionend 前不派发 input：input 事件数应为 0
    input_events = [e for (kind, _ev) in st.event_log if kind == "input"]
    assert input_events == [], "ime-update must NOT emit input yet"
    assert st.composition_range == (0, 5)


def test_ime_commit_replaces_final_and_emits_input():
    c = _ctrl(value="")
    c.dispatch("ime-start", ime_mark=0)
    c.dispatch("ime-update", "zhong", 0)
    c.dispatch("ime-commit", "中")
    st = c.state
    assert st.value == "中", f"value={st.value!r}"
    assert st.is_composing is False
    assert st.composition_range is None
    input_events = [ev for (kind, ev) in st.event_log if kind == "input"]
    assert len(input_events) == 1, "commit must emit exactly one input"


def test_ime_full_chinese_phrase_commit():
    """审计验收原文：真实中文输入法输入/选词/提交。"""
    c = _ctrl(value="")
    # 输入 "nihao" 组合 → 提交 "你好"
    c.dispatch("ime-start", ime_mark=0)
    c.dispatch("ime-update", "ni hao", 0)
    c.dispatch("ime-commit", "你好")
    st = c.state
    assert st.value == "你好"
    assert st.is_composing is False
    assert st.composition_range is None
    # dirty 应为 True（value 偏离初始 ""）
    assert st.dirty is True


# ---------------------------------------------------------------------------
# R7-U02 便捷驱动入口（dispatch_ime_*）
# ---------------------------------------------------------------------------

def test_dispatch_ime_start_update_commit_convenience():
    c = _ctrl(value="")
    assert c.dispatch_ime_start() is True
    assert c.state.is_composing is True
    assert c.dispatch_ime_update("xin") is True
    assert c.state.value == "xin"
    assert c.dispatch_ime_commit("新") is True
    assert c.state.value == "新"
    assert c.state.is_composing is False
    assert c.state.composition_range is None


def test_dispatch_ime_commit_empty_uses_composition_text():
    """commit 不带 final_text 时，用组合区内当前文本。"""
    c = _ctrl(value="")
    c.dispatch_ime_start()
    c.dispatch_ime_update("a")   # 便捷方法：mark 内部取 caret
    c.dispatch_ime_commit()      # final_text 为空 → 用组合区文本 "a"
    st = c.state
    assert st.value == "a"
    assert st.is_composing is False


def test_dispatch_ime_no_overlay_returns_false():
    from toy_browser.app.editor_overlay import EditorOverlayController
    c = EditorOverlayController()
    assert c.dispatch_ime_start() is False
    assert c.dispatch_ime_update("x") is False
    assert c.dispatch_ime_commit("x") is False


# ---------------------------------------------------------------------------
# 静态层：_g11_dispatch edit 分支接了 IME 组合路由
# ---------------------------------------------------------------------------

def test_static_g11_edit_routes_ime_update_when_composing():
    """_g11_dispatch edit 分支：组合进行中可打印字符走 ime-update。"""
    src = _gui_source()
    body = _extract_method(src, "_g11_dispatch")
    assert body, "_g11_dispatch not found in gui.py"
    assert "ime-update" in body, "edit branch must route composing chars to ime-update"
    assert "is_composing" in body, "edit branch must check the composing state"
    assert "insert-text" in body, "non-composing chars must still go to insert-text (back-compat)"


def test_static_select_all_is_valid_command():
    """editor_overlay.VALID_OVERLAY_COMMANDS 含 select-all。"""
    src = _ov_source()
    assert "select-all" in src, "select-all must be a valid overlay command"


def test_static_dispatch_handles_select_all():
    """dispatch 方法有 select-all 分支（驱动 EditorState.select）。"""
    src = _ov_source()
    body = _extract_method(src, "dispatch")
    assert body, "dispatch not found in editor_overlay.py"
    # 分支经 _CMD_SELECT_ALL 常量匹配，并调用 st.select 扩选区。
    assert "_CMD_SELECT_ALL" in body, "dispatch must match the select-all command"
    assert "select(0, len(st.value))" in body, (
        "select-all must expand the selection to the full value via select()"
    )


# ---------------------------------------------------------------------------
TESTS = [
    ("test_select_all_expands_selection_to_full_value",
     test_select_all_expands_selection_to_full_value),
    ("test_select_all_then_insert_replaces_full_value",
     test_select_all_then_insert_replaces_full_value),
    ("test_select_all_on_readonly_refused",
     test_select_all_on_readonly_refused),
    ("test_select_all_no_overlay_returns_false",
     test_select_all_no_overlay_returns_false),
    ("test_ime_start_marks_composition_range",
     test_ime_start_marks_composition_range),
    ("test_ime_update_replaces_composition_intermediate",
     test_ime_update_replaces_composition_intermediate),
    ("test_ime_commit_replaces_final_and_emits_input",
     test_ime_commit_replaces_final_and_emits_input),
    ("test_ime_full_chinese_phrase_commit",
     test_ime_full_chinese_phrase_commit),
    ("test_dispatch_ime_start_update_commit_convenience",
     test_dispatch_ime_start_update_commit_convenience),
    ("test_dispatch_ime_commit_empty_uses_composition_text",
     test_dispatch_ime_commit_empty_uses_composition_text),
    ("test_dispatch_ime_no_overlay_returns_false",
     test_dispatch_ime_no_overlay_returns_false),
    ("test_static_g11_edit_routes_ime_update_when_composing",
     test_static_g11_edit_routes_ime_update_when_composing),
    ("test_static_select_all_is_valid_command",
     test_static_select_all_is_valid_command),
    ("test_static_dispatch_handles_select_all",
     test_static_dispatch_handles_select_all),
]
