"""G11 — 可访问性键盘全流程（纯逻辑核心）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G11：

1. 复用现有 ``browser.collect_accessibility_tree`` + ``browser.tab_order``
   （focusable 元素 Tab 键顺序），建立可重复的"无鼠标任务"清单。
2. 焦点元素滚入可见区域（焦点滚动）：键盘 Tab 移动焦点时，视口
   自动滚动到目标元素的 ``scroll_top`` / ``scroll_bottom``（纯公式）。
3. 键盘全流程状态机：从 chrome（地址栏 / 工具栏）→ 文档 → 编辑控件
   → 对话框 的焦点路由，键盘（Tab / Shift+Tab / Enter / Escape /
   Alt+←/→）可独立完成导航 / 编辑 / 提交，不依赖鼠标。

本模块是**纯逻辑核心**（不依赖 tkinter / 活显示）：

* :class:`FocusViewport` — 焦点滚入可见区域的纯公式
  （``scroll_for_focus`` 返回应滚到的 offset，使焦点元素进入视口，
  不越界；元素已可见则不动）。
* :func:`mouseless_tasks` — 从 a11y 树 + tab_order 生成"无鼠标任务"
  清单（每个 focusable 元素一条：``{"id", "role", "name", "action"}``，
  供键盘全流程回归 / 无障碍审计）。
* :class:`KeyboardSession` — 键盘全流程状态机：chrome / document /
  editor / dialog 四个焦点域；``tab()`` / ``shift_tab()`` /
  ``enter()`` / ``escape()`` 推进焦点与动作，返回每个按键的
  结果（供"无鼠标完成导航 + 编辑 + 提交"的验收）。
* :func:`focus_action` — 焦点元素上 Enter / Space 的默认动作
  （link/button → activate；textbox → commit；dialog → 关闭 等），
  键盘可独立完成与鼠标点击等价的默认动作。

Tk 侧（真实 :class:`tk.Text` / Entry 覆盖层的键盘绑定、焦点可见性
的画布滚动）在 GUI 层消费这些纯函数；本批回归覆盖纯逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 焦点域（G11 step 3：chrome / document / editor / dialog）
FOCUS_CHROME = "chrome"
FOCUS_DOCUMENT = "document"
FOCUS_EDITOR = "editor"
FOCUS_DIALOG = "dialog"
_FOCUS_DOMAINS = (FOCUS_CHROME, FOCUS_DOCUMENT, FOCUS_EDITOR, FOCUS_DIALOG)


# ----------------------------------------------------------------------
# 焦点滚入可见区域（G11 step 2 纯公式）
# ----------------------------------------------------------------------
@dataclass
class FocusViewport:
    """焦点滚动公式：键盘 Tab 移动焦点时，把目标元素滚进视口。

    * ``scroll_offset`` — 当前视口滚动偏移（内容坐标）。
    * ``viewport_h`` — 视口高度。
    * 内容总高由调用方传入（``content_h``），滚动 clamp 到
      ``[0, max(0, content_h - viewport_h)]``。

    :meth:`scroll_for_focus` 给定焦点元素的 ``top`` / ``height``（内容
    坐标），返回应滚到的 offset：

    * 元素顶部在视口之上 → 滚到元素顶部（让顶部可见）；
    * 元素底部在视口之下 → 滚到让底部可见
      （``top + height - viewport_h``）；
    * 元素已完全可见 → 不滚动（返回当前 offset，避免无谓跳动）；
    * 始终 clamp 到 ``[0, scroll_max]``。
    """

    scroll_offset: int = 0
    viewport_h: int = 600
    content_h: int = 0

    @property
    def scroll_max(self) -> int:
        return max(0, self.content_h - self.viewport_h)

    def scroll_for_focus(self, top: int, height: int,
                         padding: int = 8) -> int:
        """返回使焦点元素（内容坐标 top/height）进入视口的 offset。"""
        smax = self.scroll_max
        cur = self.scroll_offset
        # 已完全可见 → 不动
        if top >= cur + padding and \
                top + height <= cur + self.viewport_h - padding:
            return cur
        # 顶部越上 → 滚到顶部对齐（留 padding）
        if top < cur + padding:
            target = max(0, top - padding)
        # 底部越下 → 滚到底部对齐（留 padding）
        elif top + height > cur + self.viewport_h - padding:
            target = top + height - self.viewport_h + padding
        else:
            target = cur
        return max(0, min(smax, target))

    def contains(self, top: int, height: int) -> bool:
        """元素（内容坐标）是否在当前视口内。"""
        return top >= self.scroll_offset and \
            top + height <= self.scroll_offset + self.viewport_h


# ----------------------------------------------------------------------
# 无鼠标任务清单（G11 step 1）
# ----------------------------------------------------------------------
def mouseless_tasks(a11y_tree: list, tab_order_ids: list) -> list[dict]:
    """从 a11y 树 + tab_order 生成"无鼠标任务"清单。

    每个 focusable 元素（按 *tab_order_ids* 顺序）生成一条
    ``{"id", "role", "name", "action"}``：

    * *role* — 元素在 a11y 树里的 role（供审计 / 回归匹配）；
    * *name* — 可访问名（aria-label > 文本 > img alt）；
    * *action* — 键盘默认动作（link/button → "activate"，
      textbox/combobox → "input"，heading/… → "browse"）。

    未带 id 的 focusable 元素被跳过（tab_order 只返回带 id 的元素）。
    """
    # id → (role, name) 索引（DFS 展平 a11y 树）
    info: dict[str, tuple] = {}
    for node in a11y_tree:
        _index_a11y(node, info)
    out: list[dict] = []
    for eid in tab_order_ids:
        role, name = info.get(eid, (None, None))
        action = _default_action(role)
        out.append({"id": eid, "role": role, "name": name,
                    "action": action})
    return out


def _index_a11y(node: dict, out: dict) -> None:
    eid = node.get("id")
    if eid:
        out[eid] = (node.get("role"), node.get("name"))
    for c in node.get("children", []) or []:
        _index_a11y(c, out)


def _default_action(role: Optional[str]) -> str:
    if role in ("link", "button"):
        return "activate"
    if role in ("textbox", "combobox"):
        return "input"
    if role == "image":
        return "browse"
    return "browse"


# ----------------------------------------------------------------------
# 键盘默认动作（G11 step 3：键盘可独立完成鼠标点击等价的默认动作）
# ----------------------------------------------------------------------
def focus_action(role: Optional[str], key: str) -> str:
    """焦点元素上 *key*（``"Enter"`` / ``"Space"`` / ``"Escape"``）
    的默认动作。

    * link / button：Enter / Space → ``"activate"``；
    * textbox / combobox：Enter → ``"commit"``（提交编辑）；
    * 其它：Enter / Space → ``"none"``（无默认动作，焦点保留）；
    * Escape：任何域 → ``"dismiss"``（关闭对话框 / 退出编辑，
      焦点回到上一级域）。
    """
    key = (key or "").lower()
    if key == "escape":
        return "dismiss"
    if role in ("link", "button"):
        if key in ("enter", "space"):
            return "activate"
        return "none"
    if role in ("textbox", "combobox"):
        if key == "enter":
            return "commit"
        return "none"
    return "none"


# ----------------------------------------------------------------------
# 键盘全流程状态机（G11 step 3）
# ----------------------------------------------------------------------
@dataclass
class KeyboardSession:
    """键盘全流程：chrome / document / editor / dialog 焦点路由。

    * ``domain`` — 当前焦点域（chrome / document / editor / dialog）。
    * ``cursor`` — 在 *focusable*（tab 顺序 id 列表）里的当前焦点
      位置（-1 表示 chrome 域未聚焦任何文档元素）。
    * ``focusable`` — tab 顺序（由 :func:`mouseless_tasks` 或
      ``browser.tab_order`` 提供）。

    按键 API（每个返回一个结果 dict，含 ``domain`` / ``cursor`` /
    ``action`` / ``focused_id``）：

    * :meth:`tab` / :meth:`shift_tab` — 在焦点域内循环移动；Tab 从
      chrome 进入 document 第一个 focusable；Shift+Tab 在首位时回到
      chrome（不跨出）。
    * :meth:`enter` — 在当前 focusable 上执行 :func:`focus_action`
      （activate / commit / none）。
    * :meth:`escape` — 若 dialog 域则关闭回 document；否则取消编辑
      回 chrome。
    """

    domain: str = FOCUS_DOCUMENT
    cursor: int = -1
    focusable: list = field(default_factory=list)

    def _res(self, action: str = "") -> dict:
        fid = self.focusable[self.cursor] if 0 <= self.cursor < \
            len(self.focusable) else None
        return {"domain": self.domain, "cursor": self.cursor,
                "focused_id": fid, "action": action}

    # ------------------------------------------------------------------
    def tab(self) -> dict:
        """Tab：前进一个 focusable；在 chrome 域时进入 document 首位。"""
        if self.domain == FOCUS_CHROME:
            self.domain = FOCUS_DOCUMENT
            self.cursor = 0 if self.focusable else -1
            return self._res("enter-document")
        if not self.focusable:
            return self._res()
        self.cursor = (self.cursor + 1) % len(self.focusable)
        return self._res()

    def shift_tab(self) -> dict:
        """Shift+Tab：回退一个；在 document 首位时回到 chrome。"""
        if not self.focusable:
            self.domain = FOCUS_CHROME
            self.cursor = -1
            return self._res("to-chrome")
        self.cursor -= 1
        if self.cursor < 0:
            self.domain = FOCUS_CHROME
            self.cursor = -1
            return self._res("to-chrome")
        return self._res()

    def enter(self) -> dict:
        """Enter：对当前 focusable 执行默认动作。"""
        if self.cursor < 0 or self.cursor >= len(self.focusable):
            return self._res("none")
        return self._res("activate")

    def escape(self) -> dict:
        """Escape：dialog → document；document/editor → chrome。"""
        if self.domain == FOCUS_DIALOG:
            self.domain = FOCUS_DOCUMENT
        else:
            self.domain = FOCUS_CHROME
            self.cursor = -1
        return self._res("dismiss")

    def enter_dialog(self) -> dict:
        """进入对话框域（键盘 Tab 到对话框时）。"""
        self.domain = FOCUS_DIALOG
        return self._res("enter-dialog")

    def enter_editor(self, eid: str) -> dict:
        """进入编辑控件域（textbox 获得焦点）。"""
        if eid in self.focusable:
            self.cursor = self.focusable.index(eid)
            self.domain = FOCUS_EDITOR
        return self._res()

    def report(self) -> dict:
        """当前键盘全流程状态快照（供无鼠标任务验收 / 审计）。"""
        return {
            "domain": self.domain,
            "cursor": self.cursor,
            "focused_id": self.focusable[self.cursor]
            if 0 <= self.cursor < len(self.focusable) else None,
            "n_focusable": len(self.focusable),
        }
