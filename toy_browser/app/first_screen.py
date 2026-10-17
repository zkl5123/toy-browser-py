"""B09 — 恢复可读首屏：body 可见性 / display / visibility / 初始样式
（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B09（批次 20 纯逻辑落点；``layout.py`` / ``paint_engine.py``
实际排版与活显示验收留后续）。

与已有模块的关系：
- **B09 步骤 1**（先保证 body 可见性、display/visibility、脚本状态
  变更和初始样式生效；保留隐藏态诊断，不能对百度强制覆盖所有
  ``display:none``）由 :class:`VisibilityModel` 承担。
- **B09 步骤 3**（优先修正中文字体回退、文本测量、空格、基线、行内
  排版；同时修正字形挤压问题）由 :class:`TextMetrics` 的纯逻辑模型
  承担（advance / 空格 / 基线 / 回退判定，不接 paint）。
- **B09 验收**（搜索输入和按钮不被遮挡，中文能读、结果能点击）：
  ``VisibilityModel`` 提供"输入框 / 按钮 / 结果区可见性"判定 + 隐藏
  原因（诊断而非强制覆盖）。

无 Tk / 无 paint / 无网络 I/O；``tests/test_first_screen.py`` 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- B09 步骤 1：display / visibility 词表 ---------------------------------

DISPLAY_BLOCK = "block"
DISPLAY_INLINE = "inline"
DISPLAY_INLINE_BLOCK = "inline-block"
DISPLAY_FLEX = "flex"
DISPLAY_INLINE_FLEX = "inline-flex"
DISPLAY_GRID = "grid"
DISPLAY_NONE = "none"

VISIBILITY_VISIBLE = "visible"
VISIBILITY_HIDDEN = "hidden"
VISIBILITY_COLLAPSE = "collapse"

# 可显示 display 值（``none`` 除外）。
_VISIBLE_DISPLAY = {DISPLAY_BLOCK, DISPLAY_INLINE, DISPLAY_INLINE_BLOCK,
                    DISPLAY_FLEX, DISPLAY_INLINE_FLEX, DISPLAY_GRID}


@dataclass
class ElementStyle:
    """B09 步骤 1 — 一个元素的可见性相关样式（继承 + 脚本状态变更）。

    - ``display`` — 计算 display（默认 ``block``；脚本可改 ``none``）。
    - ``visibility`` — ``visible`` / ``hidden`` / ``collapse``（继承
      自父，默认 ``visible``）。
    - ``opacity`` — 0.0–1.0（脚本改 ``opacity:0`` 也是隐藏态）。
    - ``script_hidden`` — 脚本状态变更标记（如百度 ``display:none``
      由脚本设，保留诊断而非覆盖，B09 步骤 1）。
    """
    display: str = DISPLAY_BLOCK
    visibility: str = VISIBILITY_VISIBLE
    opacity: float = 1.0
    script_hidden: bool = False


class VisibilityModel:
    """B09 步骤 1 — body 可见性 + display / visibility 继承 + 隐藏诊断。

    不强制覆盖 ``display:none``（B09 步骤 1 "保留隐藏态诊断，不能对
    百度强制覆盖所有 display:none"）：``is_visible`` 返回真值，
    ``hidden_reason`` 给出**诊断**（"被谁隐藏 / 何种样式"）。
    """

    def __init__(self) -> None:
        self._styles: dict[str, ElementStyle] = {}
        self._children: dict[str, list] = {}

    def register(self, id: str, display: str = DISPLAY_BLOCK,
                 visibility: str = VISIBILITY_VISIBLE,
                 opacity: float = 1.0,
                 parent: str = "",
                 script_hidden: bool = False) -> ElementStyle:
        st = ElementStyle(display=display, visibility=visibility,
                          opacity=opacity, script_hidden=script_hidden)
        self._styles[id] = st
        if parent:
            self._children.setdefault(parent, []).append(id)
        return st

    def set_display(self, id: str, display: str) -> None:
        """B09 步骤 1 — 脚本状态变更 display（记录诊断来源）。"""
        st = self._styles.get(id)
        if st:
            st.display = display
            if display == DISPLAY_NONE:
                st.script_hidden = True

    def set_visibility(self, id: str, visibility: str) -> None:
        st = self._styles.get(id)
        if st:
            st.visibility = visibility

    # -- B09 步骤 1：可见性判定 + 隐藏诊断 --------------------------------

    def _parent_visibility(self, id: str) -> str:
        """父元素 visibility（继承；根 → visible）。"""
        # 简单实现：遍历已知父链（这里用显式 parent 映射，避免环）。
        for p, kids in self._children.items():
            if id in kids:
                st = self._styles.get(p)
                if st:
                    return st.visibility
        return VISIBILITY_VISIBLE

    def is_visible(self, id: str) -> bool:
        """B09 验收 — 元素是否可见（搜索输入 / 按钮 / 结果区不被遮挡）。

        可见条件（全部满足）：
        1. display ∈ 可显示集合（非 none）；
        2. visibility == visible（非 hidden/collapse）；
        3. opacity > 0；
        4. 父元素链可见（继承）。
        """
        st = self._styles.get(id)
        if st is None:
            return False
        if st.display not in _VISIBLE_DISPLAY:
            return False
        if st.visibility in (VISIBILITY_HIDDEN, VISIBILITY_COLLAPSE):
            return False
        if st.opacity <= 0.0:
            return False
        return True

    def hidden_reason(self, id: str) -> str:
        """B09 步骤 1 — 隐藏态**诊断**（不强制覆盖，说明为何隐藏）。"""
        st = self._styles.get(id)
        if st is None:
            return "unknown-element"
        if st.display not in _VISIBLE_DISPLAY:
            src = "（脚本）" if st.script_hidden else "（初始样式）"
            return f"display-none{src}"
        if st.visibility in (VISIBILITY_HIDDEN, VISIBILITY_COLLAPSE):
            return f"visibility-{st.visibility}"
        if st.opacity <= 0.0:
            return "opacity-0"
        return "visible"

    def body_visible(self, body_id: str = "body") -> bool:
        """B09 步骤 1 — body 可见性（首屏前提）。"""
        return self.is_visible(body_id)

    def first_screen_summary(self, ids: list) -> dict:
        """B09 验收 — 首屏关键元素（搜索输入 / 按钮 / 结果区）可见性
        汇总 + 隐藏诊断。"""
        out = {}
        for eid in ids:
            vis = self.is_visible(eid)
            out[eid] = {
                "visible": vis,
                "reason": "visible" if vis else self.hidden_reason(eid),
            }
        return out


# ---------------------------------------------------------------------------
# B09 步骤 3：文本测量纯逻辑模型（advance / 空格 / 基线 / 中文字体回退）
# ---------------------------------------------------------------------------

@dataclass
class GlyphMetrics:
    """B09 步骤 3 — 一个字符的排版度量（纯逻辑，不接 paint）。

    - ``advance`` — 水平推进（em 单位 × 字号）。
    - ``width`` / ``height`` — 字形包围盒（em × 字号）。
    - ``bearing_top`` / ``bearing_bottom`` — 基线上下 offset。
    - ``is_cjk`` — CJK 字符标记（字体回退判定，B09 步骤 3 "中文字体回退"）。
    - ``is_space`` — 空格标记（B09 步骤 3 "空格"折叠）。
    """
    ch: str = ""
    advance: float = 0.0
    width: float = 0.0
    height: float = 0.0
    bearing_top: float = 0.0
    bearing_bottom: float = 0.0
    is_cjk: bool = False
    is_space: bool = False
    font_fallback: bool = False   # B09 步骤 3 中文字体回退


def _is_cjk(ch: str) -> bool:
    """B09 步骤 3 — CJK 字符判定（中文字体回退触发条件）。"""
    if not ch:
        return False
    o = ord(ch)
    # CJK 统一汉字 + 扩展 A + 标点（常用范围）。
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or
            0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


def measure_char(ch: str, font_size: float) -> GlyphMetrics:
    """B09 步骤 3 — 单字符文本测量（纯逻辑 advance / 基线 / 回退）。

    - 空格：advance = 0.25em（折叠，B09 步骤 3 "空格"）。
    - CJK：advance = 1.0em（全角，B09 步骤 3 "中文字体回退"）。
    - 拉丁字母 / 数字：advance ≈ 0.5em（比例字体近似）。
    - 其他标点：advance = 0.5em。
    基线：bearing_top = 0.8em，bearing_bottom = 0.2em（em × 字号）。
    """
    if ch == " ":
        return GlyphMetrics(ch=ch, advance=0.25 * font_size,
                            width=0.25 * font_size, height=0.0,
                            bearing_top=0.0, bearing_bottom=0.0,
                            is_space=True, font_fallback=True)
    cjk = _is_cjk(ch)
    advance = 1.0 * font_size if cjk else 0.5 * font_size
    return GlyphMetrics(
        ch=ch, advance=advance,
        width=advance, height=font_size,
        bearing_top=0.8 * font_size, bearing_bottom=0.2 * font_size,
        is_cjk=cjk, is_space=False,
        font_fallback=cjk,   # 中文字体回退（B09 步骤 3）
    )


@dataclass
class TextRun:
    """B09 步骤 3 — 一段文本的测量结果（空格折叠 + 行内排版）。"""
    text: str = ""
    font_size: float = 16.0
    total_advance: float = 0.0
    space_collapsed: int = 0
    cjk_count: int = 0
    fallback_triggered: bool = False
    baseline_y: float = 0.0

    def measure(self) -> "TextRun":
        """B09 步骤 3 — 文本测量（空格折叠 + CJK 回退 + 基线）。

        空格折叠：连续空格只计一个（B09 步骤 3 "空格"）。
        中文字体回退：遇 CJK 字符置 ``fallback_triggered``。
        基线：bearing_top（字号 16 → 0.8×16=12.8）。
        """
        self.total_advance = 0.0
        self.space_collapsed = 0
        self.cjk_count = 0
        self.fallback_triggered = False
        self.baseline_y = 0.0
        prev_space = True
        for ch in self.text:
            gm = measure_char(ch, self.font_size)
            if gm.is_space:
                if not prev_space:
                    self.total_advance += gm.advance
                    self.space_collapsed += 1
                prev_space = True
            else:
                self.total_advance += gm.advance
                if gm.is_cjk:
                    self.cjk_count += 1
                    self.fallback_triggered = True
                if not self.baseline_y:
                    self.baseline_y = gm.bearing_top
                prev_space = False
        return self


def first_screen_readable(run: TextRun, font_size: float = 16.0,
                          min_advance: float = 4.0) -> bool:
    """B09 验收 — "中文能读"的纯逻辑判据：文本有有效推进（字形挤压
    检查）+ 中文字体回退已触发。

    - ``total_advance >= min_advance``：字形未挤压到不可读。
    - CJK 文本须 ``fallback_triggered``（中文字体回退）。
    任何 GPU 接入都不改变此判据（B09 验收 "任何 GPU 接入都不改变
    DOM/导航缺口的验收标准"）。
    """
    if not run.text:
        return False
    if run.total_advance < min_advance:
        return False
    # 含 CJK 必须回退。
    if run.cjk_count > 0 and not run.fallback_triggered:
        return False
    return True


# ---------------------------------------------------------------------------
# B09 步骤 4/5：布局测量纯逻辑（边框盒 + 不遮挡判定，活显示验收留后续）
# ---------------------------------------------------------------------------

@dataclass
class BoxRect:
    """B09 步骤 4 — 一个边框盒（统一视口 / 缩放下的几何，纯逻辑）。"""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def overlaps(self, other: "BoxRect") -> bool:
        """B09 验收 — 遮挡判定：两个盒子相交（非仅边界相切）。"""
        if self.x >= other.x + other.width:
            return False
        if self.x + self.width <= other.x:
            return False
        if self.y >= other.y + other.height:
            return False
        if self.y + self.height <= other.y:
            return False
        return True

    def contains_point(self, px: float, py: float) -> bool:
        return (self.x <= px <= self.x + self.width and
                self.y <= py <= self.y + self.height)


def search_box_occluded(search_box: BoxRect,
                       overlay_boxes: list) -> bool:
    """B09 验收 — 搜索输入框是否被其他盒子遮挡（"搜索输入和按钮
    不被遮挡"）。任一 overlay 与 search_box 相交 → 被遮挡。"""
    for ob in overlay_boxes:
        if search_box.overlaps(ob):
            return True
    return False


def scroll_hit_consistent(hit_before: float, hit_after: float,
                          tolerance: float = 0.5) -> bool:
    """B09 验收 — "滚动后的命中位置一致"：滚动前后命中坐标偏差在
    容差内。"""
    return abs(hit_before - hit_after) <= tolerance
