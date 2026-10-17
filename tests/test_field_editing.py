"""B06 — textarea/input 字段编辑纯逻辑核心回归。

依据 ``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B06。被测：``toy_browser/app/field_editing.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.field_editing import (
    FieldRegistry, key_to_input_event,
    ROUTE_ADDRESS, ROUTE_EDIT, ROUTE_PAGE,
)
from toy_browser.app.editor import INSERT_TEXT

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@_reg
def test_b06_stable_identity_not_index():
    """B06 步骤 1 — 字段按稳定 node_id 关联（不依赖遍历下标）。"""
    reg = FieldRegistry()
    reg.register("id-search", value="")
    reg.register("id-other", value="x")
    st = reg.get("id-search")
    assert st is not None and st.node_ref == "id-search"
    # 下标会随注册顺序变，但 node_id 是长期身份。
    assert reg.field_ids() == ["id-search", "id-other"]


@_reg
def test_b06_readonly_disabled_block_edit():
    """B06 步骤 1 — 只读 / 禁用控件不修改 value。"""
    reg = FieldRegistry()
    reg.register("ro", value="init", read_only=True)
    reg.focus("ro")
    assert reg.apply_key("a") is False, "readonly must not edit"
    assert reg.get("ro").value == "init"
    reg.register("dis", value="init", disabled=True)
    reg.focus("dis")
    assert reg.apply_key("b") is False, "disabled must not edit"
    assert reg.get("dis").value == "init"


@_reg
def test_b06_typing_updates_value():
    """B06 步骤 3 — 输入更新 value（经 beforeinput → input）。"""
    reg = FieldRegistry()
    reg.register("f", value="")
    reg.focus("f")
    assert reg.apply_key("h") is True
    assert reg.apply_key("i") is True
    assert reg.get("f").value == "hi"


@_reg
def test_b06_keyboard_routing_three_way():
    """B06 步骤 2 — 键盘路由三分（地址栏 / 编辑层 / 页面）。"""
    reg = FieldRegistry()
    reg.register("f", value="")
    # 无焦点字段 → 地址栏吞下。
    assert reg.route_key("h") == ROUTE_ADDRESS
    reg.focus("f")
    # 焦点在字段 + 文本编辑键 → 编辑层。
    assert reg.route_key("h") == ROUTE_EDIT
    # 焦点在字段 + 非编辑键（Tab）→ 页面处理焦点 / 激活。
    assert reg.route_key("Tab") == ROUTE_PAGE


@_reg
def test_b06_ime_chinese_composition():
    """B06 步骤 3 — 中文组合（IME 三阶段，复用 G03 composition）。"""
    reg = FieldRegistry()
    reg.register("f", value="")
    st = reg.focus("f")
    st.ime_composition_start(0)
    st.ime_composition_update("zhong", 0)
    assert st.is_composing and st.value == "zhong"
    st.ime_composition_commit("中")
    assert st.value == "中" and not st.is_composing
    # 组合一次性写 undo（不是每拼音字母）。
    assert st.count_events("input") == 1


@_reg
def test_b06_sync_value_dom():
    """B06 步骤 3 — value 与 DOM 同步（脚本改值保留可解释 caret）。"""
    reg = FieldRegistry()
    reg.register("f", value="")
    reg.focus("f")
    reg.apply_key("a")
    reg.apply_key("b")
    assert reg.get("f").value == "ab"
    reg.set_value("f", "xyz")
    # 非编辑中整体替换；caret 按比例保留。
    assert reg.get("f").value == "xyz"


@_reg
def test_b06_local_refresh_only_changed_fields():
    """B06 步骤 4 — 编辑只触发被改字段的局部刷新（不整页重建）。"""
    reg = FieldRegistry()
    reg.register("f1", value="")
    reg.register("f2", value="")
    reg.focus("f1")
    reg.apply_key("a")
    dirty = reg.dirty_field_ids()
    assert "f1" in dirty and "f2" not in dirty, f"only changed field refreshes: {dirty}"
    flushed = reg.flush_refresh()
    assert flushed == ["f1"]
    assert reg.dirty_field_ids() == [], "refresh list flushed"


@_reg
def test_b06_focus_blur_commits_change():
    """B06 步骤 1 — 失焦提交 change（值偏离已提交基线时）。"""
    reg = FieldRegistry()
    reg.register("f", value="init")
    reg.focus("f")
    reg.apply_key("x")
    fired = []
    reg.focus("f")   # 重新聚焦自身 → 旧失焦已提交；再失焦到别的字段
    reg.register("g", value="")
    reg.focus("g")   # 失焦 f → 提交 f 的 change
    f = reg.get("f")
    # f 的 change 已提交（committed_value 更新）。
    assert f._committed_value == "initx", f"blur should commit change: {f._committed_value!r}"


@_reg
def test_b06_key_to_input_event_mapping():
    """B06 步骤 2 — 按键 → InputEvent 映射（可打印 insertText，功能键归 None）。"""
    ev = key_to_input_event("k")
    assert ev is not None and ev.input_type == INSERT_TEXT and ev.data == "k"
    assert key_to_input_event("Tab") is None, "Tab is not text editing"
    assert key_to_input_event("ArrowLeft") is None, "arrows are not text editing"
    be = key_to_input_event("Backspace")
    assert be is not None and be.input_type == "deleteBackward"


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
