"""批次 31 — G03 step 6 脚本改值同步可解释性 + 失焦重绘不还原 纯逻辑核心。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G03
step 6 + 验收：

> 6. DOM value 由脚本改变时，按控件是否处于编辑状态同步，保留可
>    解释的选区位置，避免下一次重绘还原成 HTML 初始 value。

G03 验收：

> 英文、中文拼音组合、粘贴含换行文本、选区替换、密码、
> disabled/readOnly、**脚本改值**、**失焦后重绘**。程序测试编辑
> 状态；另做真实键盘与 IME 人工验收。

批次 7/9（:mod:`toy_browser.app.editor`，G03 step 1–5）已落地
:class:`EditorState`（value / 选区 / caret / IME 组合区 / undo-redo /
dirty / password 呈现分离 / beforeinput-input-change / **
sync_from_dom**（脚本改值同步）；本批补 G03 step 6 的**可解释性
收尾**——"保留可解释的选区位置" + "避免下一次重绘还原成 HTML 初始
value"：

* :func:`explain_caret_after_sync` — 把一个 :class:`EditorState`
  脚本改值同步（:meth:`EditorState.sync_from_dom`）后的 caret 位置
  解释成人类可读串（"caret @ N = 行 L 列 C（old-len 比例 P）"），
  诊断区 / 日志可展示"为什么 caret 落在这里"（G03 step 6 "保留
  可解释的选区位置"）。
* :class:`RepaintGuard` — 失焦后重绘**不还原**成 HTML 初始 value
  的守卫（G03 验收"失焦后重绘"）：记录"已编辑 / 已提交"基线
  （``dirty`` / ``_committed_value``）+ 重绘呈现值来源（DOM
  property value vs HTML attribute 初始 value）；:meth:`guard` 在
  重绘前判断"控件被编辑过 / 已提交过 → 呈现 DOM property，不还原
  HTML 初始 value"（否则才还原），返回 :class:`RepaintDecision`
  （``value_source`` = ``"dom"`` / ``"html"`` + 是否还原）。
* :func:`dom_property_value` — 一个 DOM 节点当前 property value
  （活显示批次从真实 DOM 取；纯逻辑场景由调用方传入当前值）。

设计要点（与批次 7/9 `editor.py` **兼容不重叠**）：
- `EditorState.sync_from_dom` 已实现"脚本改值同步 + 保留可解释的
  caret（行内偏移比例）"；本模块只做两件事的**可测纯逻辑层**：
  ① 把 caret 解释串化（step 6 "可解释"）；② 把"重绘不还原 HTML
  初始 value"的判定固化成 :class:`RepaintGuard`（验收"失焦后重
  绘"）。
- 活显示批次：`RepaintGuard.guard` 在 Tk 重绘前调用，``value_source``
  决定用 DOM property 还是 HTML 初始 attribute；本批只固化判定
  逻辑，不接 Tk。
- 纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib + 批次 7/9
  `EditorState`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# EditorState 惰性导入（活显示批次 / 测试从 repo root import 正常；
# 直接 ``py -3 toy_browser/app/caret_explain.py`` 无 toy_browser 包
# 路径时只在 ``__main__`` 烟测里补 sys.path，模块本体不强制顶层
# import，与 layout_diff 同策略）。
EditorState = None  # type: ignore


# ----------------------------------------------------------------------
# 同步后 caret 可解释（G03 step 6 "保留可解释的选区位置"）
# ----------------------------------------------------------------------

@dataclass
class CaretExplanation:
    """G03 step 6 — 一次同步后 caret 位置的人类可读解释。

    * ``caret`` — 同步后 caret 绝对位置（0 起）。
    * ``line`` / ``col`` — caret 所在行号（0 起）/ 行内列（0 起，
      以 ``\\n`` 分行）。
    * ``ratio`` — 同步前 caret 在旧值中的行内偏移比例（0.0–1.0；
      旧值空时 0.0），即 :meth:`EditorState.sync_from_dom` 保留
      caret 用的"行内偏移比例"。
    * ``human`` — 一行诊断串（"caret @ 7 = line 0 col 7 (ratio 0.50)"）。
    """
    caret: int
    line: int
    col: int
    ratio: float
    human: str = ""

    def summary(self) -> str:
        return self.human


def line_col_of(text: str, pos: int) -> tuple[int, int]:
    """一个位置在字符串里的（行号，行内列）（以 ``\\n`` 分行，行 0 起）。

    越界 / 空串安全（clamp 到 0..len）。"""
    if not text:
        return (0, 0)
    pos = max(0, min(pos, len(text)))
    # 行号 = pos 之前有几个 \n
    line = text.count("\n", 0, pos)
    # 行内列 = pos - 该行起点
    last_nl = text.rfind("\n", 0, pos)
    col = pos - (last_nl + 1 if last_nl >= 0 else 0)
    return (line, col)


def explain_caret_after_sync(state: EditorState,
                             old_value: str,
                             old_caret: int) -> CaretExplanation:
    """G03 step 6 — 解释一次 :meth:`EditorState.sync_from_dom` 后
    caret 落在哪（为什么）。

    * *state* — 已同步后的 :class:`EditorState`（``state.caret`` 是
      同步后位置）。
    * *old_value* — 同步**前**的 DOM value（保留旧值 + 旧 caret 作
      "比例"参照，对应 :meth:`sync_from_dom` 的行内偏移比例算法）。
    * *old_caret* — 同步**前**的 caret 绝对位置。

    返回 :class:`CaretExplanation`（caret / line / col / 旧值行内
    偏移比例 + 诊断串）。这是 G03 step 6 "保留可解释的选区位置"
    的可测纯逻辑：诊断区可展示"caret 为什么在 line 0 col 7（旧值
    比例 0.5）"。"""
    cur = state.value
    caret = int(state.caret)
    line, col = line_col_of(cur, caret)
    # 旧值行内偏移比例（与 sync_from_dom 同款算法）
    if old_value:
        ratio = old_caret / len(old_value)
    else:
        ratio = 0.0
    # clamp 到 [0,1]
    ratio = max(0.0, min(1.0, ratio))
    human = (f"caret @ {caret} = line {line} col {col} "
             f"(old-len ratio {ratio:.2f}, old='{old_value}')")
    return CaretExplanation(caret=caret, line=line, col=col,
                            ratio=ratio, human=human)


# ----------------------------------------------------------------------
# 失焦后重绘不还原（G03 验收 "失焦后重绘"）
# ----------------------------------------------------------------------

@dataclass
class RepaintDecision:
    """G03 验收 — 一次重绘的呈现值来源判定。

    * ``value_source`` — 呈现值来源：
      - ``"dom"`` — 用 DOM **property** value（控件被编辑过 / 已
        提交过，不还原 HTML 初始 value）；
      - ``"html"`` — 用 HTML **attribute** 初始 value（控件从未被
        编辑、从未提交过，还原成初始 value 是正确的）。
    * ``restored`` — 本次重绘是否把呈现值还原成了 HTML 初始 value
      （``value_source == "html"`` 时 True）。
    * ``reason`` — 一句话原因（诊断）。
    """
    value_source: str
    restored: bool
    reason: str = ""

    def summary(self) -> str:
        return (f"source={self.value_source} restored={self.restored} "
                f"({self.reason})")


class RepaintGuard:
    """G03 验收 "失焦后重绘" — 重绘前判定呈现值来源。

    规则（G03 step 6 "避免下一次重绘还原成 HTML 初始 value"）：

    * 控件被**编辑过**（``state.dirty`` True）或已**提交过**
      （change 触发，``state._committed_value != HTML 初始 value``
      —— 即 ``_committed_value`` 偏离初始 value）→ 重绘用 **DOM
      property** value（``value_source="dom"``、``restored=False``），
      **不还原** HTML 初始 value。
    * 控件从未编辑、从未提交（``dirty`` False 且
      ``_committed_value == HTML 初始 value``）→ 重绘用 **HTML 初始**
      value（``value_source="html"``、``restored=True``，还原是正确
      的——用户没改过，画初始值）。

    活显示批次在 Tk 重绘前调 :meth:`guard`，据 ``value_source``
    决定读 DOM property 还是 HTML attribute；本批只固化判定逻辑。
    """

    @staticmethod
    def guard(state: EditorState, html_initial_value: str) -> RepaintDecision:
        """重绘前判定：呈现值用 DOM property 还是 HTML 初始 value。"""
        committed = state._committed_value
        edited = state.dirty  # value 偏离初始 value（被编辑过）
        committed_diverged = (committed != html_initial_value)
        if edited or committed_diverged:
            return RepaintDecision(
                value_source="dom",
                restored=False,
                reason=("控件被编辑过 / 已提交，重绘用 DOM property，"
                        "不还原 HTML 初始 value"))
        return RepaintDecision(
            value_source="html",
            restored=True,
            reason=("控件从未编辑 / 提交，重绘还原 HTML 初始 value"))


# ----------------------------------------------------------------------
# DOM property value（活显示批次从真实 DOM 取；纯逻辑场景由调用方传）
# ----------------------------------------------------------------------

def dom_property_value(current_dom_value: str,
                       state: EditorState) -> str:
    """返回 DOM **property** value（呈现 / 编辑用），与 HTML 初始
    attribute 区分。

    活显示批次从真实 DOM 节点取当前 property value 传入；纯逻辑
    场景（测试）直接传当前值。本函数语义上对应"控件实际当前 value"
    （可能已被脚本改写 / 用户编辑过），与 :class:`RepaintGuard`
    的 ``value_source`` 判定配套。"""
    return current_dom_value


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.app.editor import EditorState as _ES
    st = _ES(node_ref="i1", value="hello")
    old_v, old_caret = st.value, st.caret
    st.sync_from_dom("world long", keep_caret=True)
    expl = explain_caret_after_sync(st, old_v, old_caret)
    print("explain:", expl.summary())
    g = RepaintGuard.guard(st, "hello")
    print("guard(编辑过):", g.summary())
    st2 = _ES(node_ref="i2", value="abc")
    print("guard(未编辑):", RepaintGuard.guard(st2, "abc").summary())
