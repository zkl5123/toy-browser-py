"""C04 步骤 3 — 表单控件统一呈现模型（纯逻辑核心，批次 10）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C04 步骤 3：

> input/select/button 的文本、边框、内边距和状态由统一控件模型绘制，
> 初始 value、selected、checked 与当前 DOM property 区分。

以及 C04 步骤 4：

> 编辑状态使用 G03 的 value 和 selection；提交复用现有
> serialize/validate/form_submit，不复制序列化规则。

本模块把"控件呈现"从 layout/paint 的散点逻辑里抽成一个**统一、可
独立测试**的纯逻辑模型：

* :class:`ControlModel` — 一个表单控件（input/select/button/textarea）
  的呈现描述：控件类型、当前值、文本框几何（内容盒 + padding +
  border）、是否聚焦 / 禁用 / 只读 / 必填、select 的选项列表与当前
  选中项、checkbox/radio 的勾选态。
* **初始值 vs 当前 DOM property 的区分**（C04 步骤 3 验收）：
  - ``initial_value`` — DOM 解析时读到的 ``value`` 属性（初始值）。
  - ``value`` — 当前编辑后的值（G03 编辑状态机维护；脚本改值按
    编辑/非编辑两态同步）。
  - ``selected``（select）— 当前选中项索引；与初始 ``selected``
    属性区分。
  - ``checked``（checkbox/radio）— 当前勾选态；与初始 ``checked``
    属性区分。
  呈现层读 *当前值*（不是初始值）；G03 的 value/selection 与这里
  共用同一真值来源（DOM property）。
* :func:`display_text` — 控件当前应显示的文本（text/password 显示
  value；password 掩码；file 显示文件名；select 显示当前选中项文本；
  button 显示 label；checkbox/radio 显示勾选标记）。
* :func:`paint_geometry` — 统一控件模型绘制的几何参数（内容盒、
  padding、border、占位文本），供 paint 阶段消费，不在各控件分支
  重复。

与 G03（编辑状态机）的关系：本模型只描述"呈现"，编辑（打字、选区、
undo/redo）由 G03 的 :class:`EditorState` 维护；这里读取编辑后的
*当前值* 做呈现。提交（serialize/validate）复用 browser.py 现有
实现，不在这里复制序列化规则（C04 步骤 4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 控件类型（DOM <input type> / <select> / <button> / <textarea>）
CTRL_TEXT = "text"
CTRL_PASSWORD = "password"
CTRL_CHECKBOX = "checkbox"
CTRL_RADIO = "radio"
CTRL_FILE = "file"
CTRL_BUTTON = "button"
CTRL_SUBMIT = "submit"
CTRL_SELECT = "select"
CTRL_TEXTAREA = "textarea"


# 几何常量（与 layout.py 的 P28 占位盒一致，保证呈现模型与布局同框）
_CTRL_BOX_H = 24.0
_CTRL_BOX_W = 160.0
_CTRL_PAD_X = 4.0
_CTRL_PAD_Y = 2.0
_CTRL_BORDER = 1.0
_TEXTAREA_W = 20.0 * 8.0     # 20 cols × 8px（layout.py P28 默认）
_TEXTAREA_H = 4.0 * 20.0     # 4 rows × 20px（layout.py P28 默认）
_SELECT_W = 160.0
_FILE_W = 220.0


@dataclass
class ControlModel:
    """C04 步骤 3 — 一个表单控件的统一呈现模型（纯逻辑）。

    * ``tag`` — DOM 标签（input/select/button/textarea）。
    * ``control_type`` — 控件类型（text/password/checkbox/radio/file/
      button/submit 等；select 固定为 CTRL_SELECT）。
    * ``name`` — DOM name 属性（表单序列化键；提交层用，不在这里）。
    * ``initial_value`` — DOM 解析时读到的初始 value 属性。
    * ``value`` — **当前**值（G03 编辑后 / 脚本改值后的 DOM property）；
      呈现读这里，不读初始值。
    * ``checked`` / ``initial_checked`` — checkbox/radio 当前勾选态与
      初始勾选属性（区分初始属性与交互后状态）。
    * ``options`` / ``selected_index`` / ``initial_selected`` —
      select 选项值列表 + 当前选中项索引 + 初始选中属性索引。
    * ``label`` — button 的文本（<button> 内容 或 <input type=button>
      的 value）。
    * ``file_name`` — file input 当前选中的文件名（DOM property）。
    * ``disabled`` / ``readonly`` / ``required`` — 状态位。
    * ``focused`` — 当前是否聚焦（呈现高亮边框用；焦点本身由 G04
      焦点管理维护，这里只读呈现）。
    * ``placeholder`` — text input 空值时的占位文本。
    """
    tag: str
    control_type: str = ""
    name: str = ""
    initial_value: str = ""
    value: str = ""
    checked: bool = False
    initial_checked: bool = False
    options: list = field(default_factory=list)
    selected_index: int = 0
    initial_selected: int = 0
    label: str = ""
    file_name: str = ""
    disabled: bool = False
    readonly: bool = False
    required: bool = False
    focused: bool = False
    placeholder: str = ""

    # ------------------------------------------------------------------
    # 从 DOM 节点构建（与 layout.py P28 读取逻辑一致，但纯逻辑可测）
    # ------------------------------------------------------------------
    @classmethod
    def from_dom(cls, node) -> "ControlModel":
        """C04 — 从 DOM 节点构建控件呈现模型（读取 DOM *属性* 作为
        初始值；G03 编辑后由调用方更新 ``value``/``checked``/
        ``selected_index`` 等当前 DOM property）。

        * ``node`` — toy 层 DOM 元素（带 ``tag`` / ``attr`` /
          ``children`` / ``text``）。
        """
        tag = (getattr(node, "tag", "") or "").lower()
        m = cls(tag=tag)
        m.name = node.attr("name") or ""
        m.required = node.attr("required") is not None
        m.disabled = node.attr("disabled") is not None
        m.readonly = node.attr("readonly") is not None
        m.focused = node.attr("focus") is not None

        if tag == "input":
            ctype = (node.attr("type") or "text").lower()
            m.control_type = ctype
            m.initial_value = node.attr("value") or ""
            m.value = m.initial_value
            m.initial_checked = node.attr("checked") is not None
            m.checked = m.initial_checked
            m.placeholder = node.attr("placeholder") or ""
            if ctype in (CTRL_CHECKBOX, CTRL_RADIO) and m.value == "":
                # HTML 规范：无 value 属性的 checkbox/radio 默认 "on"
                m.value = "on"
            if ctype == CTRL_FILE:
                m.file_name = m.value
        elif tag == "select":
            m.control_type = CTRL_SELECT
            m.initial_selected = 0
            for opt in getattr(node, "children", []) or []:
                if (getattr(opt, "tag", "") or "").lower() == "option":
                    m.options.append(opt.attr("value") or (opt.text or ""))
                    if opt.attr("selected") is not None:
                        m.initial_selected = len(m.options) - 1
                        m.selected_index = m.initial_selected
            if m.options and m.value == "":
                m.value = m.options[m.selected_index]
            m.initial_value = m.value
        elif tag == "button":
            m.control_type = CTRL_BUTTON
            m.label = (node.text or node.attr("value") or "").strip()
            m.value = m.label
            m.initial_value = m.label
        elif tag == "textarea":
            m.control_type = CTRL_TEXTAREA
            m.initial_value = (node.text or "")
            m.value = m.initial_value
        return m

    # ------------------------------------------------------------------
    # 几何（统一控件模型；与 layout.py P28 占位盒同框）
    # ------------------------------------------------------------------
    def box_geometry(self, font_px: float = 16.0) -> tuple[float, float]:
        """C04 — 控件内容盒的 (width, height)（不含 padding/border）。

        select/textarea/file 用各自默认；其余文本类用通用占位盒。"""
        if self.control_type == CTRL_TEXTAREA:
            return _TEXTAREA_W, _TEXTAREA_H
        if self.control_type == CTRL_SELECT:
            # 宽度取最长选项文本（toy：固定 _SELECT_W 作占位）
            return _SELECT_W, _CTRL_BOX_H
        if self.control_type == CTRL_FILE:
            return _FILE_W, _CTRL_BOX_H
        return _CTRL_BOX_W, _CTRL_BOX_H

    def paint_geometry(self, font_px: float = 16.0) -> dict:
        """C04 步骤 3 — 统一控件模型绘制的几何参数（供 paint 消费）：
        内容盒尺寸 + padding + border + 显示文本。各控件分支不再重复
        这些计算（单一呈现数据源）。"""
        w, h = self.box_geometry(font_px)
        text = display_text(self)
        return {
            "width": w + 2 * _CTRL_PAD_X + 2 * _CTRL_BORDER,
            "height": h + 2 * _CTRL_PAD_Y + 2 * _CTRL_BORDER,
            "padding_x": _CTRL_PAD_X,
            "padding_y": _CTRL_PAD_Y,
            "border": _CTRL_BORDER,
            "text": text,
            "text_x": _CTRL_PAD_X + _CTRL_BORDER,
            "text_y": _CTRL_PAD_Y + _CTRL_BORDER,
            "focused": self.focused,
            "disabled": self.disabled,
        }


def display_text(m: ControlModel) -> str:
    """C04 步骤 3 — 控件当前应显示的文本（读 *当前值*，不读初始值）。

    * text/number 等 — 显示 ``value``（空时显示 placeholder 占位）。
    * password — 显示掩码（与 value 等长的 ``•``，G03 显示分离）。
    * checkbox/radio — 勾选显示 ``☑`` / ``○``（未勾选），带 value 前缀。
    * file — 显示 ``file_name``（无则占位 "选择文件..."）。
    * select — 显示当前选中项文本。
    * button — 显示 label。
    * textarea — 显示 value（多行）。
    """
    ct = m.control_type
    if ct == CTRL_PASSWORD:
        return "•" * len(m.value)
    if ct in (CTRL_CHECKBOX, CTRL_RADIO):
        mark = "☑" if m.checked else ("⚬" if ct == CTRL_CHECKBOX else "○")
        return f"{mark} {m.value or m.name}".strip()
    if ct == CTRL_FILE:
        return m.file_name or "选择文件..."
    if ct == CTRL_SELECT:
        if 0 <= m.selected_index < len(m.options):
            return str(m.options[m.selected_index])
        return ""
    if ct == CTRL_BUTTON:
        return m.label
    if ct == CTRL_TEXTAREA:
        return m.value
    # 通用文本类
    if m.value == "" and m.placeholder:
        return m.placeholder
    return m.value


# ----------------------------------------------------------------------
# 表格列宽 / 行高纯逻辑求解（C04 步骤 1/2）
# ----------------------------------------------------------------------
@dataclass
class TableColumn:
    """一个表格列的求解结果。

    * ``min_content`` — 该列单元格的最小内容宽（换行后的最小值）。
    * ``max_content`` — 该列单元格的最大内容宽（不换行）。
    * ``explicit`` — 该列显式宽度（colgroup/col style）；None = auto。
    * ``resolved`` — 最终列宽（explicit 优先，否则按规则求解）。
    """
    min_content: float = 0.0
    max_content: float = 0.0
    explicit: Optional[float] = None
    resolved: float = 0.0


def solve_table_widths(columns: list[TableColumn],
                       available: float,
                       border_collapse: bool = False,
                       shared_border: float = 0.0) -> list[TableColumn]:
    """C04 步骤 1/2 — 表格列宽分配的纯逻辑求解（与 ``layout.py``
    ``_layout_table`` 同规则，独立可测）。

    规则（对齐 CSS2.1 §17.4 + Edge 行为）：

    1. 有显式宽度（``col.explicit``）的列固定为该宽；无显式的列
       用 **max-content**（不换行内容宽）作为 auto 求解起点
       （与 layout.py P0 一致：``width:auto`` 表格 shrink-to-fit）。
    2. 若所有列 max-content 之和 + 外边框 > ``available``，把超出
       量按各列 max-content 比例缩回 auto 列（显式列不缩）。
    3. ``border_collapse`` 时相邻共享边框只画一次：每个 auto 列
       的列宽减掉共享边框的一半（与 layout.py 的 ``_bw_lr`` 规则
       一致），表格总宽加外边框一次。

    返回就地修改后的 *columns*（每列 ``resolved`` 已填充）。"""
    # auto 列（无显式宽）
    auto_cols = [c for c in columns if c.explicit is None]
    fixed_cols = [c for c in columns if c.explicit is not None]

    for c in fixed_cols:
        c.resolved = c.explicit

    if not auto_cols:
        return columns

    # 起点：max-content（auto 表格 shrink-to-fit）
    for c in auto_cols:
        c.resolved = c.max_content

    # 收缩：超出 available 时按比例缩回 auto 列
    fixed_total = sum(c.resolved for c in fixed_cols)
    auto_total = sum(c.resolved for c in auto_cols)
    # 外边框（collapse 时加 shared 的一半一次，separate 时不加到列）
    extra = 0.0
    if border_collapse:
        # 相邻共享边框：每个 auto 列减掉共享边框的一半
        for c in auto_cols:
            c.resolved = max(1.0, c.resolved - shared_border / 2.0)
        auto_total = sum(c.resolved for c in auto_cols)
        extra = shared_border / 2.0  # 外边框一半（一次）
    overage = fixed_total + auto_total + extra - available
    if overage > 0 and auto_total > 0:
        # 按 max-content 比例分配超出量（min_content 是下限）
        scale = (auto_total - overage) / auto_total
        for c in auto_cols:
            c.resolved = max(c.min_content, c.resolved * scale)
    else:
        # 空间富余：auto 列不变（shrink-to-fit，不拉伸填满父块，
        # 与 layout.py P1 一致）
        pass
    return columns


def solve_row_heights(rows: list[list[dict]],
                      shared_row_border: float = 0.0,
                      border_collapse: bool = False) -> list[float]:
    """C04 步骤 2 — 表格行高的纯逻辑求解（与 ``layout.py``
    ``_layout_table`` 的行 pitch 规则一致，独立可测）。

    * *rows* — 每行是 ``[{"content_h", "padding_v", "border_t",
      "border_b"}, ...]`` 的单元格列表。
    * 行高 = 该行的最大单元格 border-box 高（content + padding +
      border）；collapse 模式下相邻行的共享行边框（*shared_row_border*，
      取上下两行对应边框的最大值）在行间只加一次。

    返回每行的高度列表（不含行间共享边框，共享边框由调用方在行间
    另加，与 layout.py 的 ``child_y += _shared`` 一致）。"""
    out: list[float] = []
    for cells in rows:
        row_h = 0.0
        for c in cells:
            bh = (c.get("content_h", 0.0) + c.get("padding_v", 0.0)
                  + c.get("border_t", 0.0) + c.get("border_b", 0.0))
            row_h = max(row_h, bh)
        out.append(row_h)
    return out


def table_total_height(row_heights: list[float],
                       shared_row_border: float = 0.0,
                       border_collapse: bool = False,
                       outer_border_pair: float = 0.0) -> float:
    """C04 步骤 2 — 表格 border-box 总高的纯公式（与 layout.py 一致）：

    * separate 模式：``sum(行高) + outer_border_pair``（外边框整对）。
    * collapse 模式：``sum(行高) + (n-1)*shared_row_border``（行间共享
      边框各一次）+ ``outer_border_pair``（外边框的两半：最上行顶边
      一半 + 最下行底边一半，与 layout.py 的 border_rect 规则一致）。"""
    total = sum(row_heights)
    if border_collapse:
        total += max(0, len(row_heights) - 1) * shared_row_border
        total += outer_border_pair
    else:
        total += outer_border_pair
    return total
