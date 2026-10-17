"""批次 31 — G03 step 6 脚本改值同步可解释性 + 失焦重绘不还原 回归。

被测：``toy_browser/app/caret_explain.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.editor import EditorState
from toy_browser.app.caret_explain import (
    line_col_of, explain_caret_after_sync, CaretExplanation,
    RepaintGuard, RepaintDecision, dom_property_value,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# line_col_of（行号 / 列定位）
# ---------------------------------------------------------------------------

@_reg
def test_line_col_of_single_line():
    """单行文本：line 恒 0、col = 位置。"""
    assert line_col_of("hello", 0) == (0, 0)
    assert line_col_of("hello", 5) == (0, 5)
    assert line_col_of("hello", 3) == (0, 3)


@_reg
def test_line_col_of_multi_line():
    """多行文本（\\n 分行）：line = pos 之前 \\n 数、col = 行内列。"""
    t = "ab\ncd\nef"
    # pos 0..1 = line0（"ab"）；pos 2 = \n（行末 col 2）；
    # pos 3..4 = line1（"cd"）；pos 5 = \n；pos 6..7 = line2（"ef"）
    assert line_col_of(t, 0) == (0, 0)
    assert line_col_of(t, 1) == (0, 1)
    assert line_col_of(t, 3) == (1, 0)
    assert line_col_of(t, 4) == (1, 1)
    assert line_col_of(t, 6) == (2, 0)
    assert line_col_of(t, 7) == (2, 1)


@_reg
def test_line_col_of_bounds_and_empty():
    """越界 / 空串安全（clamp）。"""
    assert line_col_of("", 5) == (0, 0)
    assert line_col_of("abc", 99) == (0, 3)
    assert line_col_of("abc", -1) == (0, 0)


# ---------------------------------------------------------------------------
# explain_caret_after_sync（G03 step 6 "保留可解释的选区位置"）
# ---------------------------------------------------------------------------

@_reg
def test_explain_caret_basic():
    """基本同步：旧值 5 字符 caret 末尾（比例 1.0）→ 同步到新值，
    解释含 caret / line / col / 旧值比例。"""
    st = EditorState(node_ref="i", value="hello")
    old_v, old_caret = st.value, st.caret  # caret=5, 比例 5/5=1.0
    st.sync_from_dom("world", keep_caret=True)
    ex = explain_caret_after_sync(st, old_v, old_caret)
    assert isinstance(ex, CaretExplanation)
    assert ex.caret == st.caret  # 同步后 caret（比例 1.0 * 5 = 5）
    assert ex.line == 0
    assert ex.col == 5
    assert abs(ex.ratio - 1.0) < 1e-9
    s = ex.summary()
    assert "caret @" in s and "ratio" in s


@_reg
def test_explain_caret_ratio_mid():
    """中间 caret（比例 0.5）→ 解释 ratio ≈ 0.5。"""
    st = EditorState(node_ref="i", value="abcd")
    st.select(2, 2)  # caret = 2（0..4 的中点）
    old_v, old_caret = st.value, st.caret  # 2/4 = 0.5
    st.sync_from_dom("abcdefgh", keep_caret=True)
    ex = explain_caret_after_sync(st, old_v, old_caret)
    assert abs(ex.ratio - 0.5) < 1e-9
    # caret 落在 0.5 * 8 = 4
    assert ex.caret == 4
    assert ex.line == 0 and ex.col == 4


@_reg
def test_explain_caret_empty_old_value():
    """旧值空（比例 0.0）：caret 0、ratio 0.0（无越界除零）。"""
    st = EditorState(node_ref="i", value="")
    old_v, old_caret = st.value, st.caret
    st.sync_from_dom("xyz", keep_caret=True)
    ex = explain_caret_after_sync(st, old_v, old_caret)
    assert ex.caret == 0
    assert ex.ratio == 0.0
    assert ex.line == 0 and ex.col == 0


@_reg
def test_explain_caret_multi_line():
    """多行同步：caret 定位到行 / 列（\\n 分行）。"""
    st = EditorState(node_ref="i", value="l1\nl2\nl3")
    st.select(5, 5)  # caret 5（"l1\nl2" 中 l2 的第 2 字符）
    old_v, old_caret = st.value, st.caret
    ex = explain_caret_after_sync(st, old_v, old_caret)
    assert ex.line == 1
    assert ex.col == 2  # l2 的 'l' 是 col0, '2' 是 col1, 后面是 col2? 验证
    # "l1\nl2\nl3"：pos 0-1=l1, pos2=\n, pos3-4=l2, pos5=\n, pos6-7=l3
    # pos 5 是 \n → line = count \n in [0,5) = 1（pos2 一个），
    # col = 5 - (last_nl+1=2+1... 实际 last_nl=2 → col=5-3=2)
    assert ex.caret == 5


# ---------------------------------------------------------------------------
# RepaintGuard（G03 验收 "失焦后重绘" 不还原 HTML 初始 value）
# ---------------------------------------------------------------------------

@_reg
def test_guard_editing_uses_dom():
    """控件被编辑过（dirty）→ 重绘用 DOM property，不还原 HTML。"""
    st = EditorState(node_ref="i", value="initial")
    # 模拟编辑（改 value → dirty）
    st.value = "edited"
    st.dirty = True
    d = RepaintGuard.guard(st, "initial")
    assert isinstance(d, RepaintDecision)
    assert d.value_source == "dom"
    assert d.restored is False
    assert "dom" in d.summary()


@_reg
def test_guard_committed_uses_dom():
    """已提交过（change 后 _committed_value 偏离 HTML 初始）→ DOM。"""
    st = EditorState(node_ref="i", value="initial")
    st.value = "submitted"
    st._committed_value = "submitted"  # change 触发后基线
    d = RepaintGuard.guard(st, "initial")
    assert d.value_source == "dom"
    assert d.restored is False


@_reg
def test_guard_untouched_restores_html():
    """控件从未编辑 / 提交（dirty False + 提交基线 == HTML 初始）
    → 还原 HTML 初始 value（restored True）。"""
    st = EditorState(node_ref="i", value="initial")
    # 不编辑、不提交
    assert st.dirty is False
    d = RepaintGuard.guard(st, "initial")
    assert d.value_source == "html"
    assert d.restored is True
    assert "html" in d.summary()


@_reg
def test_guard_sync_from_dom_marks_editing():
    """脚本改值后（sync_from_dom 让 dirty 取决于值偏离初始）→
    偏离初始则重绘用 DOM（不还原 HTML）。"""
    st = EditorState(node_ref="i", value="base")
    st.sync_from_dom("scripted", keep_caret=True)
    # 值 "scripted" != 初始 "base" → dirty True
    assert st.dirty is True
    d = RepaintGuard.guard(st, "base")
    assert d.value_source == "dom"
    assert d.restored is False


@_reg
def test_guard_sync_back_to_initial_still_html():
    """脚本把值改回 HTML 初始（dirty 复位 False + 提交基线 == 初始）
    → 还原 HTML（restored True，因为用户视觉上未改过）。"""
    st = EditorState(node_ref="i", value="abc")
    st.value = "xyz"; st.dirty = True
    # 脚本改回初始值
    st.sync_from_dom("abc", keep_caret=False)
    # 值 "abc" == 初始 → dirty False；但 _committed_value 仍 "abc"
    d = RepaintGuard.guard(st, "abc")
    assert d.value_source == "html"
    assert d.restored is True


# ---------------------------------------------------------------------------
# dom_property_value
# ---------------------------------------------------------------------------

@_reg
def test_dom_property_value_passthrough():
    """dom_property_value：返回 DOM property value（与 HTML attribute 区分）。"""
    st = EditorState(node_ref="i", value="a")
    assert dom_property_value("current-dom", st) == "current-dom"
    assert dom_property_value("", st) == ""


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
