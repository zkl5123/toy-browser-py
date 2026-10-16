"""C04 步骤 1/2/3 — 表格列宽/行高求解 + 表单控件统一呈现模型
回归（round5 docs 批次 10）。

覆盖 toy_browser/app/form_control.py：
- ControlModel.from_dom：input(各 type)/select/button/textarea 的初始
  值/勾选/选中项读取，checkbox/radio 默认 "on"，select 初始 selected
  与当前选中区分
- display_text：text/password 掩码/checkbox 勾选标记/file 文件名/
  select 当前项/button label/placeholder 占位（读当前值不读初始值）
- paint_geometry：统一控件模型几何（内容盒 + padding + border + 显示
  文本），各控件同框（与 layout.py P28 占位盒一致）
- solve_table_widths：显式列固定、auto 列 max-content、超出按比例缩、
  border-collapse 共享边框减半（C04 步骤 1）
- solve_row_heights / table_total_height：行高 = 最大单元格 border-box、
  collapse 行间共享边框各一次（C04 步骤 2）
"""
from __future__ import annotations


class _FakeNode:
    """toy 层 DOM 元素的最小替身（tag/attr/children/text）。"""

    def __init__(self, tag="", attrs=None, text="", children=None):
        self.tag = tag
        self._attrs = dict(attrs or {})
        self.text = text
        self.children = children or []

    def attr(self, name):
        v = self._attrs.get(name)
        return v if v != "" or name in self._attrs else None

    def is_element(self):
        return True


def _input(tag="input", **attrs):
    return _FakeNode(tag, attrs)


def _opt(v="", selected=False, text=""):
    a = {"value": v} if v else {}
    if selected:
        a["selected"] = ""
    return _FakeNode("option", a, text=text or v)


from toy_browser.app.form_control import (
    ControlModel, display_text,
    CTRL_TEXT, CTRL_PASSWORD, CTRL_CHECKBOX, CTRL_RADIO, CTRL_FILE,
    CTRL_SELECT, CTRL_TEXTAREA, CTRL_BUTTON,
    solve_table_widths, solve_row_heights, table_total_height,
    TableColumn,
)


TESTS = []


# ----------------------------------------------------------------------
# ControlModel.from_dom — 各控件初始值读取
# ----------------------------------------------------------------------
def test_fc_input_text_value():
    m = ControlModel.from_dom(_input(value="hello", placeholder="ph",
                                     name="u"))
    assert m.control_type == CTRL_TEXT
    assert m.value == "hello" and m.initial_value == "hello"
    assert m.placeholder == "ph" and m.name == "u"


def test_fc_input_password_masked_display():
    m = ControlModel.from_dom(_input(type="password", value="s3cr3t"))
    assert m.control_type == CTRL_PASSWORD
    assert display_text(m) == "•" * 6      # 掩码（G03 显示分离）


def test_fc_checkbox_default_on():
    """HTML 规范：无 value 属性的 checkbox 默认 "on"。"""
    m = ControlModel.from_dom(_input(type="checkbox", checked=""))
    assert m.checked and m.initial_checked
    assert m.value == "on"                  # 默认 on


def test_fc_radio_unchecked_empty_mark():
    m = ControlModel.from_dom(_input(type="radio", name="g", value="a"))
    assert not m.checked
    assert display_text(m) == "○ a"


def test_fc_selected_current_vs_initial():
    """C04 步骤 3：初始 selected 与当前 DOM property 区分。"""
    m = ControlModel.from_dom(
        _FakeNode("select", children=[
            _opt("a"), _opt("b", selected=True), _opt("c")]))
    assert m.options == ["a", "b", "c"]
    assert m.initial_selected == 1         # 初始 selected 属性
    assert m.selected_index == 1          # 当前选中项
    assert m.value == "b"
    # 交互后当前项变了（用户切到 c）：呈现读当前值
    m.selected_index = 2
    m.value = "c"
    assert display_text(m) == "c"
    assert m.initial_selected == 1        # 初始值不变


def test_fc_button_label():
    m = ControlModel.from_dom(_FakeNode("button", children=[],
                                        text="Go"))
    assert m.control_type == CTRL_BUTTON
    assert m.label == "Go" and display_text(m) == "Go"


def test_fc_textarea_value():
    m = ControlModel.from_dom(_FakeNode("textarea", text="line1\nline2"))
    assert m.control_type == CTRL_TEXTAREA
    assert m.value == "line1\nline2"
    assert display_text(m) == "line1\nline2"


def test_fc_file_name():
    m = ControlModel.from_dom(_input(type="file", value="a.pdf"))
    assert m.control_type == CTRL_FILE
    assert m.file_name == "a.pdf"
    assert display_text(m) == "a.pdf"
    m2 = ControlModel.from_dom(_input(type="file"))
    assert display_text(m2) == "选择文件..."


def test_fc_text_empty_shows_placeholder():
    m = ControlModel.from_dom(_input(value="", placeholder="name"))
    assert display_text(m) == "name"       # 空值占位


# ----------------------------------------------------------------------
# paint_geometry — 统一控件模型几何
# ----------------------------------------------------------------------
def test_fc_paint_geometry_unified():
    m = ControlModel.from_dom(_input(value="x"))
    g = m.paint_geometry()
    # 统一模型：内容盒 + padding + border，各控件同框
    assert g["width"] > g["height"] > 0
    assert g["text_x"] > 0 and g["text_y"] > 0
    assert g["text"] == "x"
    assert g["focused"] is False and g["disabled"] is False


def test_fc_paint_geometry_focus_flag():
    m = ControlModel.from_dom(_input(value="x", focus=""))
    g = m.paint_geometry()
    assert g["focused"] is True


# ----------------------------------------------------------------------
# solve_table_widths — 列宽求解（C04 步骤 1）
# ----------------------------------------------------------------------
def test_solve_explicit_fixed():
    cols = [
        TableColumn(explicit=100.0),
        TableColumn(max_content=50.0),
    ]
    solve_table_widths(cols, available=800.0)
    assert cols[0].resolved == 100.0       # 显式列固定
    assert cols[1].resolved == 50.0        # auto 列 max-content（不拉伸填满）


def test_solve_shrink_when_over():
    cols = [
        TableColumn(min_content=20.0, max_content=400.0),
        TableColumn(min_content=20.0, max_content=400.0),
    ]
    solve_table_widths(cols, available=400.0)
    # 800 > 400 → 缩到 400（各 200）
    assert abs(cols[0].resolved + cols[1].resolved - 400.0) < 1e-6
    assert cols[0].resolved == cols[1].resolved


def test_solve_shrink_floors_at_min():
    cols = [
        TableColumn(min_content=150.0, max_content=400.0),
        TableColumn(min_content=150.0, max_content=400.0),
    ]
    solve_table_widths(cols, available=100.0)
    # 空间不足时缩到 min_content 下限
    assert cols[0].resolved >= 150.0 - 1e-6
    assert cols[1].resolved >= 150.0 - 1e-6


def test_solve_collapse_half_border():
    cols = [
        TableColumn(max_content=100.0),
        TableColumn(max_content=100.0),
    ]
    solve_table_widths(cols, available=800.0,
                       border_collapse=True, shared_border=2.0)
    # 每个 auto 列减掉共享边框的一半（1.0）
    assert abs(cols[0].resolved - 99.0) < 1e-6
    assert abs(cols[1].resolved - 99.0) < 1e-6


def test_solve_mixed_explicit_and_auto_shrink():
    cols = [
        TableColumn(explicit=200.0),
        TableColumn(min_content=10.0, max_content=300.0),
    ]
    solve_table_widths(cols, available=400.0)
    assert cols[0].resolved == 200.0       # 显式不缩
    assert abs(cols[1].resolved - 200.0) < 1e-6   # auto 填满剩余


# ----------------------------------------------------------------------
# solve_row_heights / table_total_height — 行高求解（C04 步骤 2）
# ----------------------------------------------------------------------
def test_row_height_max_cell_borderbox():
    rows = [[{"content_h": 20.0, "padding_v": 8.0,
              "border_t": 1.0, "border_b": 1.0}]]
    hs = solve_row_heights(rows)
    assert abs(hs[0] - 30.0) < 1e-6        # 20+8+1+1


def test_row_height_multi_cell_picks_max():
    rows = [[
        {"content_h": 10.0, "padding_v": 0.0},
        {"content_h": 40.0, "padding_v": 4.0, "border_t": 1.0,
         "border_b": 1.0},
    ]]
    hs = solve_row_heights(rows)
    assert abs(hs[0] - 46.0) < 1e-6        # 取最大单元格：40+4+1+1


def test_total_separate():
    hs = [30.0, 30.0]
    assert abs(table_total_height(hs, border_collapse=False,
                                 outer_border_pair=2.0) - 62.0) < 1e-6


def test_total_collapse_shared_once():
    hs = [29.2, 29.2]
    total = table_total_height(hs, shared_row_border=1.0,
                               border_collapse=True,
                               outer_border_pair=1.0)
    # 29.2*2 + (2-1)*1.0 + 1.0 = 60.4
    assert abs(total - 60.4) < 1e-6


TESTS.append(("test_fc_input_text_value", test_fc_input_text_value))
TESTS.append(("test_fc_input_password_masked_display",
              test_fc_input_password_masked_display))
TESTS.append(("test_fc_checkbox_default_on", test_fc_checkbox_default_on))
TESTS.append(("test_fc_radio_unchecked_empty_mark",
              test_fc_radio_unchecked_empty_mark))
TESTS.append(("test_fc_selected_current_vs_initial",
              test_fc_selected_current_vs_initial))
TESTS.append(("test_fc_button_label", test_fc_button_label))
TESTS.append(("test_fc_textarea_value", test_fc_textarea_value))
TESTS.append(("test_fc_file_name", test_fc_file_name))
TESTS.append(("test_fc_text_empty_shows_placeholder",
              test_fc_text_empty_shows_placeholder))
TESTS.append(("test_fc_paint_geometry_unified",
              test_fc_paint_geometry_unified))
TESTS.append(("test_fc_paint_geometry_focus_flag",
              test_fc_paint_geometry_focus_flag))
TESTS.append(("test_solve_explicit_fixed", test_solve_explicit_fixed))
TESTS.append(("test_solve_shrink_when_over", test_solve_shrink_when_over))
TESTS.append(("test_solve_shrink_floors_at_min",
              test_solve_shrink_floors_at_min))
TESTS.append(("test_solve_collapse_half_border",
              test_solve_collapse_half_border))
TESTS.append(("test_solve_mixed_explicit_and_auto_shrink",
              test_solve_mixed_explicit_and_auto_shrink))
TESTS.append(("test_row_height_max_cell_borderbox",
              test_row_height_max_cell_borderbox))
TESTS.append(("test_row_height_multi_cell_picks_max",
              test_row_height_multi_cell_picks_max))
TESTS.append(("test_total_separate", test_total_separate))
TESTS.append(("test_total_collapse_shared_once",
              test_total_collapse_shared_once))
