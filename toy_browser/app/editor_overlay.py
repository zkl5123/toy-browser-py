"""批次 39 — G03 step 2 原生覆盖层 Tk 接线（纯逻辑核心 + 可选 Tk）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G03
step 2：

> Tk 过渡版给活动 input/textarea 放置原生 Entry/Text 覆盖层，位置
> 来自布局与视口转换。**滚动、缩放、失焦或节点销毁时同步或撤销
> 覆盖层**。

与已有模块的关系（本模块**不重复**实现，只做覆盖层接线）：
- **批次 7/9/18/31** ``EditorState``（value / 选区 / caret / IME /
  undo-redo / dirty / password 呈现分离 / ``sync_from_dom``）已落；
  本模块用 ``EditorState`` 承载覆盖层的编辑逻辑（beforeinput → 更新
  value → input 事件顺序），不重写编辑状态机。
- **批次 34** ``OverlayPlacement``（位置 / 可见性 / 生命周期协议 +
  滚动 / 缩放 / 失焦 / 销毁四类同步触发 + 审计日志）已落；本模块
  把该协议**驱动** Tk 原生 Entry/Text 覆盖层的 ``place`` /
  ``configure`` / ``destroy``。
- **G11 接线留口**：本批把 :meth:`_g11_get_router` 的
  ``focus_in_edit`` / ``has_active_field`` 从硬编码 False 改为
  反映当前覆盖层状态（有活动覆盖层 → editor 域），编辑层按键据此
  路由到覆盖层。

纯逻辑核心：:class:`EditorOverlayController` 不依赖 tkinter 也能
回归（协议 + 编辑 + DOM 写回）；Tk 控件创建 / 放置是可选扩展
（tkinter 可用时），活显示批次再接真实 GUI。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.editor import (
    EditorState, InputEvent, INSERT_TEXT, DELETE_CONTENT,
    DEL_CARET_BACKWARD, DEL_CARET_FORWARD,
)
from toy_browser.app.overlay_protocol import (
    OverlayPlacement, OverlayRect, Viewport,
    should_show_overlay, overlay_caret_position,
)


# ---------------------------------------------------------------------------
# 覆盖层编辑命令（从键盘 / 事件驱动的纯逻辑入口）
# ---------------------------------------------------------------------------

#: 编辑命令 → EditorState 事件类型映射（G03 step 3/4 的键语义）。
_CMD_INSERT_TEXT = "insert-text"
_CMD_DELETE_BACKWARD = "delete-backward"   # Backspace
_CMD_DELETE_FORWARD = "delete-forward"     # Delete
_CMD_MOVE_LEFT = "move-left"
_CMD_MOVE_RIGHT = "move-right"
_CMD_MOVE_HOME = "move-home"
_CMD_MOVE_END = "move-end"
_CMD_IME_START = "ime-start"
_CMD_IME_UPDATE = "ime-update"
_CMD_IME_COMMIT = "ime-commit"
_CMD_PASTE = "paste"
_CMD_COPY = "copy"

VALID_OVERLAY_COMMANDS = (
    _CMD_INSERT_TEXT, _CMD_DELETE_BACKWARD, _CMD_DELETE_FORWARD,
    _CMD_MOVE_LEFT, _CMD_MOVE_RIGHT, _CMD_MOVE_HOME, _CMD_MOVE_END,
    _CMD_IME_START, _CMD_IME_UPDATE, _CMD_IME_COMMIT,
    _CMD_PASTE, _CMD_COPY,
)


@dataclass
class OverlayControlInfo:
    """一个表单控件的定位 / 呈现信息（纯逻辑，来自布局 + DOM）。

    * ``node_ref`` — 控件稳定标识（node_id / 索引）。
    * ``tag`` — "input" / "textarea"。
    * ``value`` — DOM property value（password 也存真实值）。
    * ``is_password`` — type=password（呈现层做遮罩，剪贴板用真值）。
    * ``is_read_only`` — readonly 属性（无编辑覆盖层）。
    * ``rect`` — 控件 CSS 矩形（布局 + 视口转换）。
    """
    node_ref: object
    tag: str
    value: str = ""
    is_password: bool = False
    is_read_only: bool = False
    rect: OverlayRect = field(default_factory=OverlayRect)


# ---------------------------------------------------------------------------
# 覆盖层控制器（驱动 OverlayPlacement 协议 + EditorState 编辑）
# ---------------------------------------------------------------------------

class EditorOverlayController:
    """G03 step 2 — 原生覆盖层控制器（纯逻辑核心）。

    职责：
    - 管理**一个**活动控件的原生覆盖层（协议经
      :class:`OverlayPlacement`，编辑经 :class:`EditorState`）。
    - :meth:`attach` — 给 *info* 描述的控件建覆盖层（可编辑才显示）。
    - :meth:`on_scroll` / :meth:`on_zoom` / :meth:`on_blur` /
      :meth:`on_node_destroy` — 四类同步触发（G03 step 2 "滚动、
      缩放、失焦或节点销毁时同步或撤销覆盖层"），全部经协议状态机。
    - :meth:`dispatch` — 把键盘 / 编辑命令喂给 :class:`EditorState`
      （G03 step 3 beforeinput → value → input 事件顺序）。
    - :meth:`commit` — 失焦 / Enter 提交：change 事件 + 写回 DOM
      value（:meth:`write_back`，password 遮罩字符不写回，G03
      step 4）。

    Tk 控件创建 / 放置（``place`` / ``configure`` / ``destroy``）
    是可选扩展（活显示批次接真实 GUI）；本核心纯逻辑可回归。
    """

    def __init__(self) -> None:
        self._placement: Optional[OverlayPlacement] = None
        self._state: Optional[EditorState] = None
        self._info: Optional[OverlayControlInfo] = None
        self._attached: bool = False

    # -- 查询 -------------------------------------------------------------

    @property
    def attached(self) -> bool:
        return self._attached

    @property
    def state(self) -> Optional[EditorState]:
        return self._state

    @property
    def placement(self) -> Optional[OverlayPlacement]:
        return self._placement

    @property
    def active(self) -> bool:
        """覆盖层当前可见（活动编辑中）。"""
        return self._attached and self._placement is not None \
            and self._placement.is_visible()

    # -- 附着 -------------------------------------------------------------

    def attach(self, info: OverlayControlInfo,
               vp: Optional[Viewport] = None,
               has_focus: bool = True) -> bool:
        """给 *info* 描述的控件建覆盖层。

        可编辑（非 read-only）+ 持有焦点 + 在视口内 → 显示；否则
        隐藏（G03 step 2 "失焦撤销覆盖层"）。返回是否显示。
        """
        self._info = info
        self._state = EditorState(
            node_ref=info.node_ref, value=info.value,
            is_password=info.is_password,
            read_only=info.is_read_only)
        self._placement = OverlayPlacement(
            kind="textarea" if info.tag == "textarea" else "entry",
            rect=info.rect)
        self._attached = True
        # G03 step 2 + step 4：应否显示（可编辑 + 未失焦 + 在视口内）
        vp = vp or Viewport()
        show = should_show_overlay(self._state, vp, info.rect,
                                   has_focus=has_focus)
        if show:
            self._placement.show()
        # read-only 控件无编辑覆盖层（协议显示 False）
        return show

    # -- 四类同步触发（G03 step 2）----------------------------------------

    def on_scroll(self, vp: Viewport) -> None:
        """页面滚动 → 协议 :meth:`OverlayPlacement.sync_scroll`。"""
        if self._placement is not None:
            self._placement.sync_scroll(vp)

    def on_zoom(self, old_zoom: float, new_zoom: float) -> None:
        """视口缩放 → 协议 :meth:`OverlayPlacement.sync_zoom`。"""
        if self._placement is not None:
            self._placement.sync_zoom(old_zoom, new_zoom)

    def on_blur(self) -> None:
        """失焦 → 协议 :meth:`OverlayPlacement.hide`（撤销覆盖层）。"""
        if self._placement is not None:
            self._placement.hide()

    def on_node_destroy(self) -> None:
        """节点销毁 → 协议 :meth:`OverlayPlacement.destroy`（终态）。"""
        if self._placement is not None:
            self._placement.destroy()
        self._attached = False

    # -- 编辑命令分派（G03 step 3/4/5）-----------------------------------

    def dispatch(self, command: str, data: str = "",
                 ime_mark: int = 0) -> bool:
        """把一条编辑命令喂给 :class:`EditorState`。返回是否生效。

        命令见 :data:`VALID_OVERLAY_COMMANDS`：insert-text / delete /
        caret-move / IME / paste / copy。read-only 或已销毁 → False。
        """
        if not self._attached or self._state is None:
            return False
        st = self._state
        if st.is_read_only:
            return False
        if command == _CMD_INSERT_TEXT:
            return st.apply_input(InputEvent(INSERT_TEXT, data))
        if command == _CMD_DELETE_BACKWARD:
            return st.apply_input(InputEvent(DEL_CARET_BACKWARD))
        if command == _CMD_DELETE_FORWARD:
            return st.apply_input(InputEvent(DEL_CARET_FORWARD))
        if command == _CMD_MOVE_LEFT:
            st.move_left(); return True
        if command == _CMD_MOVE_RIGHT:
            st.move_right(); return True
        if command == _CMD_MOVE_HOME:
            st.move_home(); return True
        if command == _CMD_MOVE_END:
            st.move_end(); return True
        if command == _CMD_IME_START:
            st.ime_composition_start(ime_mark or st.caret); return True
        if command == _CMD_IME_UPDATE:
            st.ime_composition_update(data, ime_mark); return True
        if command == _CMD_IME_COMMIT:
            st.ime_composition_commit(data); return True
        if command == _CMD_PASTE:
            st.paste(data); return True
        if command == _CMD_COPY:
            st.copy_selection(); return True
        return False

    # -- 提交 / 写回（G03 step 4 password 分离 + step 6）-----------------

    def commit(self, on_change=None) -> bool:
        """失焦 / Enter 提交：change 事件（value 偏离已提交基线时）。"""
        if self._state is None:
            return False
        return self._state.commit_change(on_change)

    def write_back(self, dom: Optional[dict] = None) -> str:
        """G03 step 4 — 把编辑值写回 DOM：返回**真实 value**
        （password 也写真实值，遮罩字符不写回 DOM）。*dom* 可选的
        节点属性字典，非 None 时直接更新其 "value"。"""
        if self._state is None:
            return ""
        val = self._state.value  # 真实值（非遮罩）
        if dom is not None:
            dom["value"] = val
        return val

    # -- 呈现 / 诊断 ------------------------------------------------------

    def display_text(self) -> str:
        """覆盖层呈现文本（password 遮罩，G03 step 4）。"""
        return self._state.display_value() if self._state else ""

    def caret_display(self) -> tuple[int, int]:
        """caret 在覆盖层内的 (row, col) 显示坐标（批次 34 协议）。"""
        if self._state is None:
            return (0, 0)
        return overlay_caret_position(self._state)

    def stats(self) -> dict:
        """诊断：附着 / 活动 / 协议审计。"""
        out = {"attached": self._attached,
               "active": self.active,
               "value_len": len(self._state.value) if self._state else 0,
               "state": self._placement.state if self._placement else None}
        if self._placement is not None:
            out["last_reason"] = self._placement.last_reason()
        return out


# ---------------------------------------------------------------------------
# 烟测
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    info = OverlayControlInfo(node_ref=1, tag="input",
                              value="hello", is_password=False,
                              rect=OverlayRect(10, 20, 200, 30))
    c = EditorOverlayController()
    c.attach(info, Viewport(0, 0, 800, 600))
    print("active:", c.active)
    c.dispatch(_CMD_MOVE_END)
    c.dispatch(_CMD_INSERT_TEXT, " world")
    print("value:", c.state.value)
    vp_out = Viewport(0, 1000, 800, 600)
    c.on_scroll(vp_out)
    print("scrolled out:", c.active, c.stats()["last_reason"])
    c.on_blur()
    print("blur:", c.stats())
    print("write back:", c.write_back())
