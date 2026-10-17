"""批次 34 — G03 step 2 原生覆盖层位置同步/撤销协议（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G03
step 2：

> Tk 过渡版给活动 input/textarea 放置原生 Entry/Text 覆盖层，位置
> 来自布局与视口转换。**滚动、缩放、失焦或节点销毁时同步或撤销
> 覆盖层**。

批次 7/9/18/31 已落地 ``EditorState``（value / 选区 / caret / IME /
undo-redo / dirty / password 呈现分离 / ``sync_from_dom``）+
``caret_explain.py``（可解释选区 + 失焦重绘不还原）+
``field_editing.py``（B06 字段编辑状态）。本批补 G03 step 2 的**覆盖
层位置同步 / 撤销协议**纯逻辑核心：

* :class:`OverlayPlacement` — 覆盖层的位置 / 可见性 / 生命周期协议
  状态机（*kind* = "entry" / "textarea"；*rect* = 控件 CSS 矩形；
  *state* = "hidden" / "visible" / "destroyed"；*reason* 审计日志）：

  * :meth:`show` — 显示覆盖层（hidden → visible；destroyed 终态拒）。
  * :meth:`reposition` — 位置同步（控件布局 / 视口变化 → 更新
    rect；visible 时保留可见，hidden 时只更新位置不显示）。
  * :meth:`sync_scroll` — 页面滚动：控件滚出视口 → 自动隐藏
    （"滚动…同步或撤销覆盖层"）；滚回视口 → 恢复可见。
  * :meth:`sync_zoom` — 视口缩放（page_zoom）：按缩放重算 rect
    （"缩放…同步或撤销覆盖层"）。
  * :meth:`hide` — 失焦 / 失活隐藏（"失焦…撤销覆盖层"）。
  * :meth:`destroy` — 节点销毁 → 覆盖层销毁（"节点销毁…撤销覆盖
    层"，**终态**：destroyed 后 show / reposition 全拒）。

* :func:`should_show_overlay` — 给定 :class:`EditorState` + 视口信息，
  判断该控件此刻是否应显示原生覆盖层（可编辑 + 未失焦 + 在视口内 +
  非 disabled / read-only）。

* :func:`overlay_caret_position` — 把 :class:`EditorState` 的 caret
  偏移换算成覆盖层内的显示坐标（行 / 列，供 Tk 侧把 caret 画对
  位置；纯逻辑，无 tkinter）。

* :func:`password_overlay_text` — G03 step 4 "password 显示与剪贴板
  策略明确分离"：覆盖层呈现用 :meth:`EditorState.display_value`
  （遮罩），剪贴板 / 提交用真实 :attr:`EditorState.value`；本函数
  返回 (overlay_text, clipboard_text) 对，保证两者一致可验证
  （遮罩等长、真实值等值）。

设计要点（与批次 7/9/18/31 **兼容不重叠**）：
- 批次 7/9/18/31 管编辑状态机 + 可解释选区 + 字段编辑；本模块管
  **覆盖层协议层**（位置 / 可见性 / 生命周期 + 滚动 / 缩放 / 失焦 /
  销毁四类同步触发），活显示批次 Tk 侧按本协议驱动原生
  Entry/Text 覆盖层的 ``place`` / ``configure`` / ``destroy``。
- 纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib + 批次 7/9
  ``EditorState``。
- 状态机审计：每次状态变更记录 (state, reason) 到 :attr:`log`，
  供诊断区 + 回归核对"滚动/缩放/失焦/销毁"四类触发是否按协议走。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# EditorState 惰性（活显示批次 / 测试从 repo root import 正常；直接
# 跑本文件无包路径时降级）。
try:
    from toy_browser.app.editor import EditorState
except Exception:  # pragma: no cover
    EditorState = None  # type: ignore


# 覆盖层状态
STATE_HIDDEN = "hidden"
STATE_VISIBLE = "visible"
STATE_DESTROYED = "destroyed"

# 触发原因（审计）
R_SHOWN = "shown"
R_REPOSITIONED = "repositioned"
R_SCROLLED_OUT = "scrolled_out"
R_SCROLLED_IN = "scrolled_in"
R_ZOOMED = "zoomed"
R_HIDDEN = "hidden"
R_DESTROYED = "destroyed"


# ----------------------------------------------------------------------
# 几何（纯逻辑：控件 CSS 矩形 + 视口）
# ----------------------------------------------------------------------

@dataclass
class OverlayRect:
    """覆盖层位置（CSS 像素，来自布局 + 视口转换）。"""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def contains(self, cx: float, cy: float) -> bool:
        return self.x <= cx < self.x + self.w and self.y <= cy < self.y + self.h


@dataclass
class Viewport:
    """视口（CSS 像素 + 页面缩放）。"""
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    width: float = 800.0
    height: float = 600.0
    page_zoom: float = 1.0


def _rect_visible_in_viewport(rect: OverlayRect, vp: Viewport) -> bool:
    """控件 CSS 矩形是否与视口可见区相交（G03 step 2 滚动同步）。"""
    vx0 = vp.scroll_x
    vy0 = vp.scroll_y
    vx1 = vp.scroll_x + vp.width
    vy1 = vp.scroll_y + vp.height
    rx1 = rect.x + rect.w
    ry1 = rect.y + rect.h
    return not (rx1 < vx0 or ry1 < vy0 or rect.x > vx1 or rect.y > vy1)


# ----------------------------------------------------------------------
# 覆盖层协议状态机
# ----------------------------------------------------------------------

class OverlayPlacement:
    """G03 step 2 — 原生覆盖层位置 / 可见性 / 生命周期协议。

    * ``kind`` — "entry" / "textarea"。
    * ``rect`` — 控件 CSS 矩形（布局 + 视口转换后）。
    * ``state`` — :data:`STATE_HIDDEN` / :data:`STATE_VISIBLE` /
      :data:`STATE_DESTROYED`（销毁是终态）。
    * ``log`` — 审计日志：(state, reason) 列表。

    四类同步触发（G03 step 2 "滚动、缩放、失焦或节点销毁"）：
    * :meth:`sync_scroll` — 滚动（滚出视口 → 隐藏，滚回 → 显示）；
    * :meth:`sync_zoom` — 缩放（重算 rect）；
    * :meth:`hide` — 失焦（隐藏）；
    * :meth:`destroy` — 节点销毁（终态）。
    """

    def __init__(self, kind: str = "entry",
                 rect: Optional[OverlayRect] = None) -> None:
        self.kind = kind
        self.rect = rect or OverlayRect()
        self.state = STATE_HIDDEN
        self.log: list[tuple[str, str]] = []

    # ---- 审计 ----
    def _set_state(self, state: str, reason: str) -> None:
        if self.state == STATE_DESTROYED:
            return  # 终态拒（销毁后任何操作无效）
        self.state = state
        self.log.append((state, reason))

    # ---- 显示 / 隐藏 ----
    def show(self) -> bool:
        """显示覆盖层（hidden → visible）。destroyed 拒。"""
        if self.state == STATE_DESTROYED:
            return False
        self._set_state(STATE_VISIBLE, R_SHOWN)
        return True

    def hide(self) -> bool:
        """隐藏覆盖层（失焦 / 失活，visible → hidden）。destroyed 拒。"""
        if self.state == STATE_DESTROYED:
            return False
        self._set_state(STATE_HIDDEN, R_HIDDEN)
        return True

    # ---- 位置同步 ----
    def reposition(self, rect: OverlayRect) -> None:
        """控件布局变化 → 更新位置（rect）。"""
        if self.state == STATE_DESTROYED:
            return
        self.rect = rect
        self.log.append((self.state, R_REPOSITIONED))

    def sync_scroll(self, vp: Viewport) -> None:
        """页面滚动：控件 CSS 矩形相对视口是否可见（G03 step 2
        "滚动…同步或撤销覆盖层"）。

        * visible 且滚出视口 → 隐藏；
        * hidden 且滚回视口 → 显示；
        * 其余只记审计。
        """
        if self.state == STATE_DESTROYED:
            return
        vis = _rect_visible_in_viewport(self.rect, vp)
        if self.state == STATE_VISIBLE and not vis:
            self._set_state(STATE_HIDDEN, R_SCROLLED_OUT)
        elif self.state == STATE_HIDDEN and vis:
            self._set_state(STATE_VISIBLE, R_SCROLLED_IN)
        else:
            self.log.append((self.state, R_REPOSITIONED))

    def sync_zoom(self, old_zoom: float, new_zoom: float) -> None:
        """视口缩放（G03 step 2 "缩放…同步"）：按缩放比重算 rect。

        控件 CSS 矩形随 page_zoom 缩放（UI 物理像素 = CSS × zoom）；
        本方法把 *rect* 从 old_zoom 标度换算到 new_zoom 标度（保持
        控件左上角在视口内相对位置不变，纯逻辑近似，活显示批次用
        真实布局重算替换）。
        """
        if self.state == STATE_DESTROYED or old_zoom <= 0:
            return
        ratio = new_zoom / old_zoom
        self.rect = OverlayRect(
            x=self.rect.x * ratio,
            y=self.rect.y * ratio,
            w=self.rect.w * ratio,
            h=self.rect.h * ratio,
        )
        self.log.append((self.state, R_ZOOMED))

    # ---- 销毁（终态）----
    def destroy(self) -> None:
        """节点销毁 → 覆盖层销毁（G03 step 2 "节点销毁…撤销覆盖层"）。

        销毁后 :attr:`state` 为 :data:`STATE_DESTROYED`，show /
        reposition / sync_scroll / sync_zoom / hide 全拒（协议终态）。
        """
        if self.state == STATE_DESTROYED:
            return
        self.state = STATE_DESTROYED
        self.log.append((STATE_DESTROYED, R_DESTROYED))

    # ---- 查询 ----
    def is_visible(self) -> bool:
        return self.state == STATE_VISIBLE

    def last_reason(self) -> Optional[str]:
        return self.log[-1][1] if self.log else None

    def count_reasons(self, reason: str) -> int:
        return sum(1 for _s, r in self.log if r == reason)


# ----------------------------------------------------------------------
# 是否应显示覆盖层（G03 step 2 协议 + G03 step 4 可编辑判定）
# ----------------------------------------------------------------------

def should_show_overlay(state: "EditorState", vp: Viewport,
                        rect: OverlayRect,
                        has_focus: bool = True) -> bool:
    """给定 :class:`EditorState` + 视口 + 控件矩形，判断此刻应否显示
    原生覆盖层。

    条件（全真才显示）：
    * *has_focus* — 控件持有焦点（失焦 → 不显示，G03 step 2 "失焦
      撤销覆盖层"）；
    * 可编辑 — ``not state.is_read_only``（read-only 控件无编辑
      覆盖层，C04 步骤 3）；
    * 在视口内 — 控件矩形与视口相交（滚动出视口 → 不显示）；
    * 非组合区破坏 — IME 组合进行中（``is_composing``）仍显示
      （组合区需覆盖层，不隐藏）。
    """
    if not has_focus:
        return False
    if state.is_read_only:
        return False
    if not _rect_visible_in_viewport(rect, vp):
        return False
    return True


# ----------------------------------------------------------------------
# 覆盖层内 caret 显示坐标（供 Tk 侧画 caret）
# ----------------------------------------------------------------------

def overlay_caret_position(state: "EditorState",
                           cpl: int = 8) -> tuple[int, int]:
    """把 :class:`EditorState` 的 caret 换算成覆盖层内的 (row, col)
    显示坐标。

    * *cpl* — 每行字符数（Tk Entry 单行 = len(value)；Text 多行由
      调用方按行宽传入）。caret 按 value 中 ``\n`` 分行定位。

    返回 (row, col)，row = caret 所在行号（0 起），col = 行内列号。
    越界 / 空值安全。活显示批次 Tk 侧据此把 caret / 选区画到覆盖层
    对应位置（"保留可解释的选区位置"，G03 step 6）。
    """
    val = state.value
    caret = state.caret
    if not val:
        return (0, 0)
    # 按 \n 分行，定位 caret 在行 / 列
    line_idx = 0
    consumed = 0
    for idx, ch in enumerate(val):
        if idx >= caret:
            break
        if ch == "\n":
            line_idx += 1
        consumed = idx + 1
    else:
        consumed = len(val)
    # caret 所在行的起始偏移
    row_start = 0
    for i in range(consumed):
        if val[i] == "\n":
            row_start = i + 1
    col = consumed - row_start
    # clamp
    max_col = cpl if cpl > 0 else (len(val) - row_start)
    col = max(0, min(col, max_col))
    return (line_idx, col)


# ----------------------------------------------------------------------
# password 呈现 / 剪贴板分离（G03 step 4）
# ----------------------------------------------------------------------

def password_overlay_text(state: "EditorState") -> tuple[str, str]:
    """G03 step 4 "password 显示与剪贴板策略明确分离"：返回
    ``(overlay_text, clipboard_text)``。

    * *overlay_text* — 覆盖层呈现值（:meth:`EditorState.display_value`：
      password 返回等长遮罩 ``●``，非 password 返回真实 value）；
    * *clipboard_text* — 剪贴板 / 提交值（真实 :attr:`EditorState.
      value`，**不含遮罩字符**——"不能把遮罩字符写回 DOM value"）。

    保证两者一致可验证：password 控件 ``len(overlay) == len(clip)``
    且 ``clip == state.value``；非 password 控件 ``overlay == clip ==
    value``。
    """
    overlay = state.display_value()
    clip = state.value
    return (overlay, clip)


# ----------------------------------------------------------------------
# 烟测
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    sys = __import__("sys")
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.app.editor import EditorState as _ES
    from toy_browser.app.caret_explain import line_col_of
    EditorState = _ES

    # 覆盖层协议
    ov = OverlayPlacement(kind="entry",
                          rect=OverlayRect(10, 20, 200, 30))
    vp = Viewport(scroll_x=0, scroll_y=0, width=800, height=600)
    ov.show()
    print("after show:", ov.state, "reason:", ov.last_reason())
    # 滚动出视口
    vp2 = Viewport(scroll_x=0, scroll_y=1000, width=800, height=600)
    ov.sync_scroll(vp2)
    print("scrolled out:", ov.state, "reason:", ov.last_reason())
    # 滚回
    vp3 = Viewport(scroll_x=0, scroll_y=0, width=800, height=600)
    ov.sync_scroll(vp3)
    print("scrolled in:", ov.state, "reason:", ov.last_reason())
    # 销毁终态
    ov.destroy()
    print("destroyed:", ov.state)
    print("show after destroy refused:", ov.show())

    # 是否应显示
    st = EditorState("n1", "hello")
    r = OverlayRect(10, 20, 200, 30)
    print("should show (focus, in-viewport):",
          should_show_overlay(st, vp3, r, has_focus=True))
    print("should show (no focus):",
          should_show_overlay(st, vp3, r, has_focus=False))

    # caret 位置
    st2 = EditorState("n2", "abc\ndef\nghi")
    st2.set_caret(5)  # 行 2 第 2 字符（"def" 的 d）
    print("caret pos:", overlay_caret_position(st2))

    # password 分离
    stp = EditorState("n3", "secret", is_password=True)
    o, c = password_overlay_text(stp)
    print("password overlay:", o, "clipboard:", c, "equal len:",
          len(o) == len(c))
