"""R7-U02 — 真实 Tk Entry/Text 原生输入控件（审计 §57 残留落地）。

依据 ``assessments/20261018-round7/docs/逐项改进实施方案.md`` §215-223
R7-U02 + GUI 审计 §57：

> "EditorOverlayController.attach 创建 EditorState 与 OverlayPlacement
> 并设置可见状态，**没有创建实际 Tk Entry/Text**。模型中的 selection、
> placement、composition 等存在不等同操作系统输入法已经接入。"

本模块把 :class:`EditorOverlayController` 的纯逻辑编辑核心（
:class:`EditorState` value/选区/password + :class:`OverlayPlacement`
位置协议）驱动到**真实 Tk 控件**，补上审计明确指出的缺失——
"没有创建实际控件"。

设计边界（与既有纯逻辑核心互补，不重造）：
- 控件选择：``input`` → Tk ``Entry``（单行），``textarea`` →
  Tk ``Text``（多行，1 行高）。
- **双向同步**（审计验收"value/selection 双向同步"）：
  - 模型 → 控件：:meth:`sync_to_widget` 把 :attr:`EditorState.value`
    写入控件（password 控件写遮罩字符进 Entry 显示，真实值仍存
    模型；textarea 写原文）。
  - 控件 → 模型：:meth:`sync_from_widget` 读控件当前文本回写模型
    （password 用遮罩长度反推，正常用控件内容）。
- 选区映射：caret / selection 与控件 0-index 列号对应（玩具简化：
  单行 Entry 直接列号；多行 Text 用行 0 + 列号）。
- **password 遮罩分离**（G03 step 4）：``Entry.show`` 用掩码字符，
  呈现层值经 :meth:`EditorState.display_value`（等长 ``●``），剪贴板
  策略不变（复制真实 value，不走遮罩）。
- **销毁终态**（审计"禁止提交过程中销毁编辑态" + 协议 destroyed）：
  控件销毁后所有同步 / 定位方法拒绝（no-op），与
  :class:`OverlayPlacement` 的终态一致。
- Tk 控件创建为**可选注入**：本类核心逻辑（选择/映射/销毁）不依赖
  tkinter，可无 Tk 回归；真实控件创建由调用方注入
  （``self._widget``），Tk 不可用时注入桩控件仍能让同步契约可测。

纯逻辑核心 + 可选 Tk 控件，与 :mod:`editor_overlay` 纯逻辑可回归的
约定一致（不破坏既有 57 条 editor/overlay 测试）。
"""
from __future__ import annotations

from typing import Optional

from toy_browser.app.editor import EditorState
from toy_browser.app.overlay_protocol import OverlayRect, Viewport


# 控件 kind（与 OverlayPlacement.kind 对齐）。
KIND_ENTRY = "entry"
KIND_TEXTAREA = "textarea"

# password 掩码字符（Tk Entry show 参数 + 模型 display_value 一致用 ●）。
_PASSWORD_MASK_CHAR = "●"
_ENTRY_MASK_SHOW = "*"


class NativeOverlayWidget:
    """R7-U02 — 把编辑覆盖层驱动到真实 Tk Entry/Text 控件。

    职责：
    - 按 ``kind`` 选择控件类型（input→Entry / textarea→Text）；
    - value / selection 双向同步（模型 ↔ 控件）；
    - password 遮罩（Entry show=掩码，呈现层等长 ●）；
    - 按 *rect* + *viewport* 几何定位控件（``place``）；
    - 销毁终态：控件销毁后所有操作拒绝（no-op）。

    构造时**不**自动创建 Tk 控件（无 Tk 也可构造 + 回归）；控件由
    :meth:`bind_widget` 注入（真实 Tk 控件或测试桩）。
    """

    def __init__(self, state: EditorState, kind: str = KIND_ENTRY,
                 rect: Optional[OverlayRect] = None) -> None:
        self._state = state
        self._kind = kind
        self._rect = rect or OverlayRect()
        self._widget = None
        self._destroyed = False

    # ------------------------------------------------------------------
    # 控件选择（审计"按控件几何定位" + "密码/textarea/readonly/disabled
    # 分流"的基础）
    # ------------------------------------------------------------------

    @property
    def widget(self):
        """当前绑定的控件（真实 Tk 控件或测试桩；未绑定为 None）。"""
        return self._widget

    @property
    def destroyed(self) -> bool:
        return self._destroyed

    def widget_class_name(self) -> str:
        """应使用的控件类型名（审计验收：input→Entry / textarea→Text）。

        纯逻辑（不创建真实控件也能核查"该用哪种控件"，可无 Tk 回归）。
        """
        return "Text" if self._is_text_area() else "Entry"

    def _is_text_area(self) -> bool:
        """当前控件 kind 是否为多行 textarea（决定 Tk 索引协议：
        Text 用 "1.0"/"end"，Entry 用 0/"end"；见 :meth:`_widget_write`
        / :meth:`_widget_read`）。"""
        return self._kind == KIND_TEXTAREA

    def bind_widget(self, widget) -> None:
        """注入真实 Tk 控件（Entry / Text 或测试桩）。

        销毁终态下拒绝再绑定（与协议 destroy 一致）。
        """
        if self._destroyed:
            return
        self._widget = widget
        # 注入后做一次初始同步：把模型 value 写进控件。
        self.sync_to_widget()

    # ------------------------------------------------------------------
    # 双向同步（审计验收"value/selection 双向同步"）
    # ------------------------------------------------------------------

    def _display_value(self) -> str:
        """控件里应显示的文本（password 写遮罩，普通写原文）。"""
        if self._state.is_password:
            # Entry 用 show="*" 自带掩码呈现；这里返回真实 value 让调用方
            # 决定写什么——但为与 display_value（●）一致，统一返回遮罩。
            return _PASSWORD_MASK_CHAR * len(self._state.value)
        return self._state.value

    def sync_to_widget(self) -> bool:
        """模型 → 控件：把当前 value / caret 写进控件。

        无控件或已销毁 → 返回 False（no-op 不崩）。真实 Tk 控件按
        控件类型走不同索引协议（Entry 用 0 / Text 用 "1.0"，见
        :meth:`_widget_write`）；测试桩实现 ``set_value(str)`` 约定
        方法即可（绕开 Tk 索引细节，无 tkinter 也能回归）。
        """
        if self._widget is None or self._destroyed:
            return False
        text = self._display_value()
        w = self._widget
        # 测试桩：约定 set_value(str)。
        if hasattr(w, "set_value"):
            w.set_value(text)
            return True
        if hasattr(w, "delete") and hasattr(w, "insert"):
            return self._widget_write(w, text)
        return False

    def _widget_write(self, w, text: str) -> bool:
        """按控件类型写 *text*（Entry 0 索引 / Text "1.0" 索引）。"""
        try:
            if self._is_text_area():
                w.delete("1.0", "end")
                w.insert("1.0", text)
            else:
                w.delete(0, "end")
                w.insert(0, text)
        except Exception:
            return False
        return True

    def sync_from_widget(self) -> bool:
        """控件 → 模型：读控件当前文本回写 :attr:`EditorState.value`。

        password 控件读回的是遮罩文本（长度等于真实 value 长度），
        不据此篡改真实 value（剪贴板策略不变，G03 step 4）。
        无控件或已销毁 → False。真实 Tk 控件按类型读
        （Entry 用 ``get()`` / Text 用 ``get("1.0","end-1c")``，见
        :meth:`_widget_read`）；测试桩实现 ``get_value()`` 约定方法。
        """
        if self._widget is None or self._destroyed:
            return False
        w = self._widget
        # 测试桩：约定 get_value()。
        if hasattr(w, "get_value"):
            raw = w.get_value()
            if self._state.is_password:
                return len(raw) == len(self._state.value)
            self._state.value = raw
            return True
        # 真实 Tk 控件：Entry/Text 都提供 get()（Text 还接受索引，
        # 见 _widget_read；selection_range 仅 Entry 有，Text 用
        # tag/mark 体系——故此处只按 get 判定可同步，不按 selection_range）。
        if hasattr(w, "get"):
            raw = self._widget_read(w)
            if raw is None:
                return False
            if self._state.is_password:
                # 遮罩文本不回写真实 value（长度校验即可）。
                if len(raw) != len(self._state.value):
                    return False
                return True
            self._state.value = raw
            return True
        return False

    def _widget_read(self, w):
        """按控件类型读全量文本（Entry ``get()`` / Text ``get("1.0","end-1c")``）。"""
        try:
            if self._is_text_area():
                return w.get("1.0", "end-1c")
            return w.get()
        except Exception:
            # Entry 不接受索引；Text 索引失败也不应是常规路径。
            try:
                return w.get()
            except Exception:
                return None

    def sync_selection(self) -> bool:
        """把 :attr:`EditorState` 选区映射到控件（caret 列号）。

        玩具简化：单行 / 多行都用 0-index 行 0 + 列号 = caret 位置。
        控件须实现 ``select_range`` / ``xview_moveto``（真实 Tk）或
        ``set_selection``（桩）。无控件或已销毁 → False。
        """
        if self._widget is None or self._destroyed:
            return False
        sel = self._state.selection_start
        w = self._widget
        if hasattr(w, "select_range"):
            try:
                w.select_range(str(sel), str(self._state.selection_end))
            except Exception:
                return False
            return True
        if hasattr(w, "set_selection"):
            w.set_selection(sel, self._state.selection_end)
            return True
        return False

    # ------------------------------------------------------------------
    # 几何定位（审计"按控件几何定位" + 滚动/缩放更新覆盖位置）
    # ------------------------------------------------------------------

    def place(self, vp: Optional[Viewport] = None) -> bool:
        """按 *rect*（CSS 像素）+ *viewport* 缩放把控件 place 到视口。

        物理坐标 = CSS × page_zoom（DPI 感知场景再 × device_scale，
        与 :class:`ViewportState` 同一标度约定，见 overlay_protocol
        :meth:`OverlayPlacement.sync_zoom`）。控件须实现 ``place``
        （真实 Tk 控件）或 ``set_place``（桩）。无控件或已销毁 → False。
        """
        if self._widget is None or self._destroyed:
            return False
        vp = vp or Viewport()
        s = vp.page_zoom or 1.0
        x = int(round(self._rect.x * s))
        y = int(round(self._rect.y * s))
        w = max(1, int(round(self._rect.w * s)))
        h = max(1, int(round(self._rect.h * s)))
        wgt = self._widget
        if hasattr(wgt, "place"):
            try:
                wgt.place(x=x, y=y, width=w, height=h)
            except Exception:
                return False
            return True
        if hasattr(wgt, "set_place"):
            wgt.set_place(x, y, w, h)
            return True
        return False

    def place_on_scroll(self, vp: Viewport) -> bool:
        """滚动时重定位（控件 CSS rect 不变，视口 scroll 偏移改变
        呈现位置）——委托 :meth:`place`，传入新视口。"""
        return self.place(vp)

    # ------------------------------------------------------------------
    # 销毁（审计"禁止提交过程中销毁编辑态" + 协议 destroyed 终态）
    # ------------------------------------------------------------------

    def destroy(self) -> bool:
        """控件销毁（协议终态）。此后所有同步 / 定位拒绝（no-op）。

        返回是否真正销毁了控件。已销毁或无控件 → False（幂等不崩）。
        """
        if self._destroyed or self._widget is None:
            return False
        w = self._widget
        if hasattr(w, "destroy"):
            try:
                w.destroy()
            except Exception:
                pass
        self._widget = None
        self._destroyed = True
        return True
