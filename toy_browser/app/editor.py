"""G03 — 编辑状态机（编辑、选区与输入法的纯逻辑核心）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G03：

1. 引入 ``EditorState``：node 标识、value、selection_start/end、
   caret、composition_range（IME 组合区）、undo/redo、dirty 标记。
2. 普通编辑执行 beforeinput → 更新 value → input；被取消的
   beforeinput 不修改值。change 在提交 / 失焦时触发，不为每个
   按键重复触发。
3. 左右 / Home / End、删除、选择、复制粘贴、撤销；password 的
   显示与剪贴板策略分离（遮罩字符不写回 DOM value）。
4. IME 区分组合区更新与最终提交，维护 composition 与
   isComposing 语义。
5. DOM value 被脚本改变时，按控件是否处于编辑状态同步，保留
   可解释的选区位置。

本模块是纯逻辑（不依赖 tkinter），供原生 Entry/Text 覆盖层
（G03 step 2，Tk 过渡方案）驱动；事件顺序与验收对照 Edge。
"""

from __future__ import annotations

# ----------------------------------------------------------------------
# 事件类型（beforeinput 的 inputType 子集 + 自定义命令）
# ----------------------------------------------------------------------
INSERT_TEXT = "insertText"
DELETE_CONTENT = "deleteContent"
DELETE_BY_COMPOSITION = "deleteByComposition"
INSERT_FROM_DROP = "insertFromDrop"
INSERT_LINE_BREAK = "insertLineBreak"
DEL_CARET_BACKWARD = "deleteBackward"
DEL_CARET_FORWARD = "deleteForward"


class InputEvent:
    """beforeinput 事件（可取消）。

    ``input_type`` — 见模块常量；``data`` — 插入 / 删除的文本
    （delete 类为 None）。``cancelable`` 固定 True；调用方置
    ``default_prevented`` 后 :meth:`EditorState.apply_input` 不修改
    value。
    """

    __slots__ = ("input_type", "data", "default_prevented")

    def __init__(self, input_type: str, data: str | None = None) -> None:
        self.input_type = input_type
        self.data = data
        self.default_prevented = False


class ChangeEvent:
    """change 事件（不可取消，仅通知）。"""

    __slots__ = ("old_value", "new_value")

    def __init__(self, old_value: str, new_value: str) -> None:
        self.old_value = old_value
        self.new_value = new_value


class EditorState:
    """一个活动输入控件的编辑状态（G03 step 1）。

    * ``node_ref`` — 控件的稳定标识（``node_id``；批次 2 过渡用 DOM
      元素索引也可，但不作长期身份）。
    * ``value`` — 当前 DOM property value（password 也存真实值，
      遮罩只在呈现层做）。
    * ``selection_start`` / ``selection_end`` — 选区（相等时即 caret）。
    * ``composition_range`` — 非 None 时 IME 组合进行中
      （``isComposing`` 语义）。
    * ``undo_stack`` / ``redo_stack`` — value + 选区快照。
    * ``dirty`` — value 偏离初始 value（change 触发依据）。
    * ``password`` — 呈现层用；剪贴板策略与显示分离。
    """

    def __init__(self, node_ref: object, value: str = "",
                 is_password: bool = False,
                 read_only: bool = False) -> None:
        self.node_ref = node_ref
        self.value = value
        self._initial_value = value
        self.selection_start = len(value)
        self.selection_end = len(value)
        self.composition_range: tuple[int, int] | None = None
        self.is_composing = False
        self.is_password = is_password
        self.is_read_only = read_only
        self.dirty = False
        self._undo_stack: list[tuple[str, int, int]] = []
        self._redo_stack: list[tuple[str, int, int]] = []
        # 已提交（change 后）的 value 基线。
        self._committed_value = value
        # 事件日志（供测试与诊断）：("beforeinput", ev) /
        # ("input", ev) / ("change", ev)。
        self.event_log: list = []

    # ------------------------------------------------------------------
    # 选区 / caret
    # ------------------------------------------------------------------
    @property
    def caret(self) -> int:
        """G03 — caret = selection_start（无选区时 start == end）。"""
        return self.selection_start

    def set_caret(self, pos: int) -> None:
        pos = max(0, min(pos, len(self.value)))
        self.selection_start = pos
        self.selection_end = pos

    def select(self, start: int, end: int) -> None:
        n = len(self.value)
        start = max(0, min(start, n))
        end = max(0, min(end, n))
        if start > end:
            start, end = end, start
        self.selection_start = start
        self.selection_end = end

    # ------------------------------------------------------------------
    # 光标移动命令（G03 step 4：左右 / Home / End）
    # ------------------------------------------------------------------
    def move_left(self, count: int = 1) -> None:
        self.set_caret(self.caret - count)

    def move_right(self, count: int = 1) -> None:
        self.set_caret(self.caret + count)

    def move_home(self) -> None:
        self.set_caret(0)

    def move_end(self) -> None:
        self.set_caret(len(self.value))

    # ------------------------------------------------------------------
    # beforeinput / input（G03 step 3）
    # ------------------------------------------------------------------
    def apply_input(self, ev: InputEvent) -> bool:
        """派发 beforeinput，然后更新 value 并派发 input。

        返回值是否被修改（被取消的 beforeinput / 只读控件返回 False）。
        组合进行中（``is_composing``）的 insertText 更新组合区，
        不写 undo 栈（组合结束后一次性写）。
        """
        if self.is_read_only:
            return False
        # beforeinput（可取消）
        self.event_log.append(("beforeinput", ev))
        if ev.default_prevented:
            return False
        _old = self.value
        if ev.input_type in (INSERT_TEXT, INSERT_FROM_DROP,
                             INSERT_LINE_BREAK):
            # insertLineBreak 无 data 时插入 "\n"（G03 step 4 textarea
            # Enter 语义）
            data = ev.data if ev.data else (
                "\n" if ev.input_type == INSERT_LINE_BREAK else "")
            sel = self._selected_text()
            lo, hi = self.selection_start, self.selection_end
            self.value = self.value[:lo] + data + self.value[hi:]
            pos = lo + len(data)
            self.set_caret(pos)
            if self.is_composing:
                # 组合区扩大覆盖新插入内容
                c_lo, c_hi = self.composition_range or (0, 0)
                self.composition_range = (c_lo, pos)
            else:
                self._push_undo(_old, lo, hi)
        elif ev.input_type in (DELETE_CONTENT, DEL_CARET_BACKWARD,
                               DEL_CARET_FORWARD):
            if ev.input_type == DEL_CARET_FORWARD:
                # 删除 caret 之后的字符（Delete 键语义）；有选区时删选区
                lo, hi = self.selection_start, self.selection_end
                if lo == hi:
                    lo, hi = lo, min(lo + 1, len(self.value))
            elif ev.input_type == DEL_CARET_BACKWARD:
                # Backspace：有选区删选区，否则删 caret 前一个字符
                lo, hi = self.selection_start, self.selection_end
                if lo == hi and lo > 0:
                    lo, hi = lo - 1, lo
            else:
                # DELETE_CONTENT：删除当前选区（无选区则无操作）
                lo, hi = self.selection_start, self.selection_end
            if lo < hi:
                self.value = self.value[:lo] + self.value[hi:]
                self.set_caret(lo)
                self._push_undo(_old, lo, hi)
        elif ev.input_type == DELETE_BY_COMPOSITION:
            # 组合中取消整段组合文本
            if self.composition_range:
                lo, hi = self.composition_range
                self.value = self.value[:lo] + self.value[hi:]
                self.set_caret(lo)
                self._push_undo(_old, lo, hi)
            self.composition_range = None
            self.is_composing = False
        else:
            # 未知 inputType — 不修改值
            self.event_log.append(("input", ev))
            return False
        self.dirty = self.value != self._initial_value
        self.event_log.append(("input", ev))
        return True

    def _selected_text(self) -> str:
        return self.value[self.selection_start:self.selection_end]

    def _push_undo(self, old_value: str, lo: int, hi: int) -> None:
        self._undo_stack.append((old_value, lo, min(lo, len(old_value))))
        self._redo_stack.clear()
        # 栈上限（G03 内存治理）
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    # ------------------------------------------------------------------
    # 选区编辑命令
    # ------------------------------------------------------------------
    def replace_selection(self, text: str) -> None:
        """复制粘贴 / 选区替换：以 *text* 替换当前选区。"""
        self.apply_input(InputEvent(INSERT_FROM_DROP, text))

    def copy_selection(self) -> str:
        """复制选区文本；password 控件复制的是真实 value（剪贴板
        策略与显示分离——呈现层才做遮罩，G03 step 4）。"""
        return self._selected_text()

    def paste(self, text: str) -> None:
        self.replace_selection(text)

    # ------------------------------------------------------------------
    # undo / redo
    # ------------------------------------------------------------------
    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        val, sel_lo, sel_hi = self._undo_stack.pop()
        self._redo_stack.append((self.value, self.selection_start,
                                 self.selection_end))
        self.value = val
        self.selection_start = sel_lo
        self.selection_end = sel_hi
        self.dirty = self.value != self._initial_value
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        val, sel_lo, sel_hi = self._redo_stack.pop()
        self._undo_stack.append((self.value, self.selection_start,
                                 self.selection_end))
        self.value = val
        self.selection_start = sel_lo
        self.selection_end = sel_hi
        self.dirty = self.value != self._initial_value
        return True

    # ------------------------------------------------------------------
    # change（G03 step 3：提交 / 失焦时触发一次）
    # ------------------------------------------------------------------
    def commit_change(self, on_change=None) -> bool:
        """失焦 / Enter / 显式提交：value != 已提交基线时触发 change。

        返回是否实际触发了 change。*on_change* 可选回调。
        """
        if self.value == self._committed_value:
            return False
        ev = ChangeEvent(self._committed_value, self.value)
        self.event_log.append(("change", ev))
        self._committed_value = self.value
        if on_change:
            on_change(ev)
        return True

    # ------------------------------------------------------------------
    # IME 组合输入（G03 step 5）
    # ------------------------------------------------------------------
    def ime_composition_start(self, mark: int) -> None:
        """组合开始（候选框出现）：caret 处标记组合区起点。"""
        self.is_composing = True
        self.composition_range = (mark, mark)

    def ime_composition_update(self, text: str, mark: int) -> None:
        """组合区文本更新（拼音中间态）：组合区内替换，
        不派发 input（浏览器在 compositionend 前不派发 input）。"""
        if not self.is_composing:
            return
        lo, hi = self.composition_range
        self.value = self.value[:lo] + text + self.value[hi:]
        self.composition_range = (lo, lo + len(text))
        self.set_caret(lo + len(text))

    def ime_composition_commit(self, final_text: str) -> None:
        """组合提交（最终汉字替换拼音）：一次性写入并派发 input。"""
        if not self.is_composing:
            return
        lo, hi = self.composition_range
        _old = self.value
        self.value = self.value[:lo] + final_text + self.value[hi:]
        pos = lo + len(final_text)
        self.set_caret(pos)
        self._push_undo(_old, lo, hi)
        self.is_composing = False
        self.composition_range = None
        self.dirty = self.value != self._initial_value
        self.event_log.append(
            ("input", InputEvent(INSERT_TEXT, final_text)))

    # ------------------------------------------------------------------
    # 脚本改值同步（G03 step 6）
    # ------------------------------------------------------------------
    def sync_from_dom(self, new_value: str, keep_caret: bool = True) -> None:
        """DOM value 被脚本改写时同步编辑状态。

        编辑进行中（is_composing）只更新组合区外的值；非编辑时整体
        替换，caret 按可解释位置（行内偏移比例）保留。
        """
        old = self.value
        if self.is_composing and self.composition_range:
            lo, hi = self.composition_range
            prefix, suffix = old[:lo], old[hi:]
            self.value = prefix + new_value + suffix
            self.composition_range = (lo, lo + len(new_value))
            self.set_caret(lo + len(new_value))
        else:
            # 保留 caret 在旧值中的相对行内位置
            ratio = (self.caret / len(old)) if old else 0.0
            self.value = new_value
            self.set_caret(int(ratio * len(new_value)))
        self.dirty = self.value != self._initial_value

    # ------------------------------------------------------------------
    # 呈现
    # ------------------------------------------------------------------
    def display_value(self) -> str:
        """呈现层值：password 控件返回等长遮罩（●），真实值只存于
        :attr:`value`（剪贴板策略不变，G03 step 4）。"""
        if self.is_password:
            return "●" * len(self.value)
        return self.value

    # ------------------------------------------------------------------
    # 事件查询（测试 / 诊断）
    # ------------------------------------------------------------------
    def last_event_kind(self) -> str | None:
        return self.event_log[-1][0] if self.event_log else None

    def count_events(self, kind: str) -> int:
        return sum(1 for k, _ in self.event_log if k == kind)
