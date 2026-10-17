"""B06 — textarea/input 字段编辑纯逻辑核心（稳定 DOM 身份 + 键盘路由三分）。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B06（批次 18 纯逻辑落点；Tk 编辑覆盖层与活显示接线留后续批次）。

与批次 2 G03（``editor.py`` 的 :class:`EditorState`）的关系：
G03 是**单个活动控件**的编辑状态机（value / 选区 / caret / IME 组合 /
undo / 事件顺序）；B06 补上**页面级**的两件事：

- **B06 步骤 1**：字段编辑状态以**稳定 DOM 身份**关联（:class:`FieldRegistry`
  按 ``node_id`` 索引，不依赖可能变化的遍历下标）。``disabled`` /
  ``readonly`` / 禁用态在派发前拦截（只读 / 禁用控件不修改 value）。
- **B06 步骤 2**：键盘路由三分（地址栏 / 原生编辑层 / 页面）：
  :func:`route_key` 按焦点目标把按键归到 ``address`` / ``edit`` /
  ``page`` 三类，编辑层按键委托给当前焦点字段的
  :class:`~toy_browser.app.editor.EditorState`。
- **B06 步骤 3**：value 与 DOM 同步复用 ``EditorState.sync_from_dom``
  （脚本改值时按编辑 / 非编辑两态保留可解释 caret，批次 18 暴露
  ``sync_value`` 便捷入口）。
- **B06 步骤 4**：编辑变化只派发目标字段的事件 + 可合并的局部刷新
  （``FieldRegistry.dirty_field_ids`` 只报告被改的字段，不整页重建）。

``EditorState`` 已覆盖 B06 步骤 3 的 IME 三阶段（compositionstart /
update / commit + isComposing 语义）与步骤 4 的密码遮罩 / 剪贴板分离，
本模块不重复实现，仅做**注册 + 路由 + 批量局部刷新**的页面级调度。
"""

from __future__ import annotations

from typing import Optional

from toy_browser.app.editor import (
    EditorState, InputEvent,
    INSERT_TEXT, DEL_CARET_BACKWARD, DEL_CARET_FORWARD, INSERT_LINE_BREAK,
)

# --- 键盘路由目标（B06 步骤 2） --------------------------------------------

ROUTE_ADDRESS = "address"   # 地址栏（GUI 输入框）
ROUTE_EDIT = "edit"         # 原生编辑层（活动 input/textarea 字段）
ROUTE_PAGE = "page"         # 页面（无焦点字段 / 非编辑键）

# --- 编辑层按键 → InputEvent 的映射（B06 步骤 2） ---------------------------
# 仅文本编辑按键进编辑层；其余（Tab / Enter 激活 / 方向焦点等）归页面。
_KEY_EVENTS = {
    "Backspace": DEL_CARET_BACKWARD,
    "Delete": DEL_CARET_FORWARD,
    "Enter": INSERT_LINE_BREAK,
}


def key_to_input_event(key: str) -> Optional[InputEvent]:
    """把编辑层按键映射为 beforeinput 事件。

    - 可打印单字符（len==1、非控制键）→ ``insertText``；
    - ``Backspace`` / ``Delete`` / ``Enter`` → 对应 inputType；
    - 组合键 / 焦点键（Tab、方向键、Esc 等）→ 返回 ``None``（归页面）。
    """
    k = (key or "").strip()
    if not k:
        return None
    if k in _KEY_EVENTS:
        return InputEvent(_KEY_EVENTS[k])
    # Ctrl/Meta 组合键与功能键不归文本编辑层。
    if k in ("Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp",
             "ArrowDown", "Shift", "Control", "Alt", "Meta", "Home",
             "End", "PageUp", "PageDown"):
        return None
    if len(k) == 1 and k.isprintable():
        return InputEvent(INSERT_TEXT, k)
    return None


class FieldRegistry:
    """B06 步骤 1 — 按**稳定 DOM 身份**（``node_id``）索引字段编辑状态。

    不依赖遍历下标（B06 步骤 1 "以稳定 DOM 身份关联，不能只依赖可能
    变化的遍历下标"）。每个字段持有一个 :class:`EditorState`（G03），
    注册表维护焦点字段、只读 / 禁用态与批量局部刷新清单。
    """

    def __init__(self) -> None:
        self._fields: dict[object, EditorState] = {}
        self._focus: Optional[object] = None
        self._local_refresh: list[object] = []   # 本批可合并的局部刷新
        self._refresh_flushed: bool = False

    # -- B06 步骤 1：稳定身份注册 / 查询 --------------------------------------

    def register(self, node_id: object, value: str = "",
                 is_password: bool = False, read_only: bool = False,
                 disabled: bool = False) -> EditorState:
        """按稳定 ``node_id`` 注册字段（幂等：已存在则更新元数据）。"""
        if node_id in self._fields:
            st = self._fields[node_id]
            st.is_read_only = read_only or disabled
            # disabled 在编辑器层等价只读（不修改 value）。
            return st
        st = EditorState(node_id, value, is_password, read_only or disabled)
        self._fields[node_id] = st
        return st

    def get(self, node_id: object) -> Optional[EditorState]:
        return self._fields.get(node_id)

    def untrack(self, node_id: object) -> None:
        self._fields.pop(node_id, None)
        if self._focus == node_id:
            self._focus = None

    def field_ids(self) -> list:
        return list(self._fields.keys())

    # -- 焦点（B06 步骤 1：字段焦点 / 失焦与值同步） -------------------------

    def focus(self, node_id: object) -> Optional[EditorState]:
        """焦点移到字段：失焦旧字段提交 change，激活新字段。"""
        if self._focus is not None and self._focus in self._fields:
            # 失焦 → 提交 change（G03 commit_change 语义）。
            self._fields[self._focus].commit_change()
        if node_id in self._fields:
            self._focus = node_id
            return self._fields[node_id]
        self._focus = None
        return None

    def focused_field(self) -> Optional[EditorState]:
        if self._focus is None:
            return None
        return self._fields.get(self._focus)

    # -- B06 步骤 2：键盘路由三分 --------------------------------------------

    def route_key(self, key: str, focus_in_edit: bool = True) -> str:
        """B06 步骤 2 — 把按键路由到 地址栏 / 编辑层 / 页面。

        - 焦点在编辑层（``focus_in_edit=True`` 且存在焦点字段）→ 文本编辑
          按键进 :meth:`apply_key`，返回 ``edit``；非编辑键返回 ``page``。
        - 焦点不在编辑层 → 返回 ``address``（地址栏吞下）或 ``page``。
        路由本身**不修改** value；修改由 :meth:`apply_key` 完成。
        """
        ev = key_to_input_event(key)
        st = self.focused_field() if focus_in_edit else None
        if ev is not None and st is not None:
            return ROUTE_EDIT
        if st is not None:
            # 焦点在字段但按键非编辑键（Tab / 方向等）→ 页面处理焦点 / 激活。
            return ROUTE_PAGE
        return ROUTE_ADDRESS

    def apply_key(self, key: str) -> bool:
        """编辑层按键落地到当前焦点字段（经 beforeinput → input）。

        只读 / 禁用控件返回 False（不修改 value，B06 步骤 1）。
        字段被修改时登记局部刷新（B06 步骤 4）。
        """
        st = self.focused_field()
        if st is None:
            return False
        ev = key_to_input_event(key)
        if ev is None:
            return False
        changed = st.apply_input(ev)
        if changed:
            self._mark_refresh(st.node_ref)
        return changed

    # -- B06 步骤 3：值与 DOM 同步 -------------------------------------------

    def set_value(self, node_id: object, value: str) -> None:
        """脚本 / DOM 直接设值：按编辑 / 非编辑两态同步（G03 step 6）。"""
        st = self._fields.get(node_id)
        if st is None:
            return
        st.sync_from_dom(value)
        self._mark_refresh(st.node_ref)

    def sync_value(self, node_id: object, value: str) -> None:
        """``set_value`` 的别名（B06 步骤 3 "value 与 DOM 同步"）。"""
        self.set_value(node_id, value)

    # -- B06 步骤 4：可合并的局部刷新 -----------------------------------------

    def _mark_refresh(self, node_id: object) -> None:
        if not self._refresh_flushed and node_id not in self._local_refresh:
            self._local_refresh.append(node_id)

    def dirty_field_ids(self) -> list:
        """本批被修改、需局部刷新的字段（可合并，不整页重建）。"""
        return list(self._local_refresh)

    def flush_refresh(self) -> list:
        """取走当前局部刷新清单并复位（下一批编辑重新累积）。"""
        ids = list(self._local_refresh)
        self._local_refresh.clear()
        self._refresh_flushed = True
        return ids

    def __len__(self) -> int:
        return len(self._fields)
