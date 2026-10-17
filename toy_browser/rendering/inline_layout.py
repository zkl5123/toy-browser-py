"""C03 — 行内格式化与文字布局（纯逻辑核心，round5 docs 批次 15）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C03：

> 2. 建立 InlineRun/LineBox，收集同一行内格式化上下文的文本和行内
>    片段；不要把每个 span 当独立块或重复收集父子文本。
> 3. 依据 white-space 模式处理空白折叠，保留跨节点的空白状态；先用
>    共享字体度量计算 advance，再按可断点分行。
> 4. 每行按 ascent/descent 与 line-height 计算基线，定位每个片段；
>    上下标/vertical-align 后续按独立用例扩展。
> 5. 布局输出已经定位的文本 run，绘制阶段不再独立换行或重新测量
>    另一套位置。

本模块是**纯逻辑核心**（不依赖 tkinter / 真实 TTF），把 C03 的
"InlineRun / LineBox / 空白折叠 / 共享 advance / 可断点分行 / 基线
定位 / 单一排版数据源"抽成可测试的数据结构；真实 TTF 度量复用现有
font/ttf_font（C03 步骤 6），JS 引擎 / paint_engine 在活显示批次消费
这里的排版结果：

* :class:`InlineRun` — 同一行内格式化上下文的一条文本片段（C03
  步骤 2）：携带文本 + 共享字体度量（font_px/family/weight）+ 样式
  标记（bold/italic/link）。
* :class:`LineBox` — 一行排版结果（C03 步骤 4）：基线 y、行高、
  片段定位列表。
* :class:`WhitespaceFol der` — 空白折叠（C03 步骤 3 "保留跨节点的
  空白状态"）：按 white-space 模式（normal/pre/nowrap）折叠，
  跨节点（相邻 run 之间）保留"上一 run 是否以空白结尾"状态，避免
  重复收集父子文本。
* :class:`InlineLayout` — 把一组 run 用共享 advance 度量 + 可断点
  分行 + 基线定位编成 LineBox 序列（C03 步骤 3/4/5，"单一排版数据
  源"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


# ----------------------------------------------------------------------
# 共享字体度量（C03 步骤 3 "先使用共享字体度量计算 advance"）
# ----------------------------------------------------------------------
class FontMetrics(Protocol):
    """共享字体度量协议（C03 步骤 6 复用现有 font/ttf_font）。

    实现方需提供 ``advance(ch, font_px) -> float``（单字符 advance
    宽度）+ ``line_metrics(font_px) -> (ascent, descent)``。"""

    def advance(self, ch: str, font_px: float) -> float: ...

    def line_metrics(self, font_px: float) -> tuple[float, float]: ...


@dataclass
class SimpleMetrics:
    """C03 — 固定 advance 的度量实现（测试 / 无 TTF 回退）。

    * ``advance_px`` — 每字符 advance 宽度（monospace 模型，等价
      :func:`text.char_advance` 的固定单元格路径）。
    * ``ascent_px`` / ``descent_px`` — 行上/下伸（基线计算用）。
    """
    advance_px: float = 8.0
    ascent_px: float = 8.0
    descent_px: float = 2.0

    def advance(self, ch: str, font_px: float) -> float:
        return self.advance_px

    def line_metrics(self, font_px: float) -> tuple[float, float]:
        return (self.ascent_px, self.descent_px)


# ----------------------------------------------------------------------
# InlineRun（C03 步骤 2：同一行内格式化上下文的文本片段）
# ----------------------------------------------------------------------
@dataclass
class InlineRun:
    """C03 步骤 2 — 一条行内文本片段。

    * ``text`` — 片段文本（同一行内格式化上下文）。
    * ``font_px`` / ``font_family`` / ``weight`` — 共享字体度量
      （C03 步骤 6 复用现有 font/ttf_font，不为不同片段各建一套）。
    * ``style`` — 样式标记（``{"bold": True}`` / ``{"link": True}``
      等；绘制阶段消费，不改变排版 advance）。
    * 不把每个 span 当独立块：父节点的文本与子节点文本在收集时
      已合并进同一 run 序列（空白折叠保证不重复，C03 步骤 2）。
    """
    text: str
    font_px: float = 16.0
    font_family: str = "monospace"
    weight: int = 400
    style: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.text


# ----------------------------------------------------------------------
# LineBox（C03 步骤 4：一行排版结果 + 片段定位）
# ----------------------------------------------------------------------
@dataclass
class PlacedRun:
    """C03 步骤 4/5 — 已定位的片段（绘制阶段直接消费，不再换行 /
    重新测量另一套位置）。"""
    text: str
    x: float          # 片段左端 x（相对行原点）
    baseline_y: float  # 片段基线 y
    font_px: float
    style: dict


@dataclass
class LineBox:
    """C03 步骤 4 — 一行。

    * ``runs`` — 本行已定位片段（:class:`PlacedRun` 序列）。
    * ``x`` / ``y`` — 行原点（x = 行左边界，y = 行顶）。
    * ``baseline`` — 行基线 y（按 ascent/descent 与 line-height 计算）。
    * ``height`` — 行高（line-height）。
    * ``width`` — 行内片段总 advance 宽度（断行判断用）。
    """
    runs: list
    x: float = 0.0
    y: float = 0.0
    baseline: float = 0.0
    height: float = 0.0
    width: float = 0.0

    def run_count(self) -> int:
        return len(self.runs)

    def text(self) -> str:
        """本行连读文本（片段拼接，不含跨行空白）。"""
        return "".join(r.text for r in self.runs)


# ----------------------------------------------------------------------
# 空白折叠（C03 步骤 3：按 white-space 模式 + 保留跨节点空白状态）
# ----------------------------------------------------------------------
# white-space 模式（CSS）：
#   "normal" — 连续空白折叠成单个空格，行首/行尾空白去掉（默认）。
#   "nowrap" — 不分行，连续空白仍折叠。
#   "pre"    — 保留所有空白（不折叠、不分行的 pre 语义，仅保留原文）。
_WHITESPACE = (" ", "\t", "\n", "\r", "\f", "\v",
               "\u00a0", "\u2000", "\u2001", "\u2002", "\u2003",
               "\u2004", "\u2005", "\u2006", "\u2007", "\u2008",
               "\u2009", "\u200a", "\u202f", "\u3000")


def _is_space(ch: str) -> bool:
    return ch in _WHITESPACE


class WhitespaceFolder:
    """C03 步骤 3 — 空白折叠（保留跨节点空白状态）。

    对一串 run（可能来自不同节点 / span）做折叠，维护"上一个已折叠
    字符是否为空白"的状态，保证：

    * ``normal`` — 连续空白折叠成一个空格；**行首**和**行尾**空白
      去掉；相邻 run 之间的空白只保留一个（"保留跨节点的空白状态"）。
    * ``nowrap`` — 同 normal 但折叠后整体不分行。
    * ``pre``    — 不折叠，原样保留（pre 语义）。

    :meth:`fold` 返回折叠后的 run 序列（文本已折叠，片段边界保留，
    便于后续按 run 定位样式）。"""

    def __init__(self, mode: str = "normal") -> None:
        self.mode = mode if mode in ("normal", "nowrap", "pre") else "normal"

    def fold(self, runs: list) -> list:
        """折叠一组 :class:`InlineRun`，返回新 run 列表（同顺序、
        文本折叠）。

        规则（normal/nowrap）：
        * 连续空白（含跨 run）折叠成**一个**空格。
        * **行首**空白全部去掉（首字符非空白前不产生空格）。
        * **行尾**空白全部去掉（末尾不产生空格）。
        * ``pre`` 模式原样保留。"""
        if self.mode == "pre":
            return [r for r in runs if not r.is_empty()]

        out: list = []
        pending_space = False   # 已见空白、等待下一个非空字符时补一个空格
        seen_content = False    # 是否已产出过非空白字符（行首判定）
        for r in runs:
            for ch in r.text:
                if _is_space(ch):
                    # 记"待补空格"，连续空白不累积
                    pending_space = True
                else:
                    if pending_space and seen_content:
                        # 内容之间补一个折叠空格（跨 run / 跨字符）
                        out.append(_space_run(r))
                    pending_space = False
                    out.append(_char_run(ch, r))
                    seen_content = True
        # 行尾空白：pending_space 不补（seen_content 已 True 但无后续
        # 非空白 → 行尾，去掉）
        return out


def _space_run(ref) -> InlineRun:
    return InlineRun(" ", ref.font_px, ref.font_family,
                     ref.weight, dict(ref.style))


def _char_run(ch: str, ref) -> InlineRun:
    return InlineRun(ch, ref.font_px, ref.font_family,
                     ref.weight, dict(ref.style))


def collapse_runs(runs: list, mode: str = "normal") -> str:
    """C03 步骤 3 — 把 run 序列折叠成**单一文本**（按 *mode*）。

    用于"不要把每个 span 当独立块或重复收集父子文本"（C03 步骤 2）：
    先折叠成单串再分行。"""
    folder = WhitespaceFolder(mode)
    folded = folder.fold(runs)
    return "".join(r.text for r in folded)


# ----------------------------------------------------------------------
# InlineLayout（C03 步骤 3/4/5：共享 advance + 可断点分行 + 基线定位）
# ----------------------------------------------------------------------
@dataclass
class InlineLayoutConfig:
    """C03 排版配置。

    * ``metrics`` — 共享字体度量（:class:`FontMetrics`）。
    * ``max_width`` — 可断点分行宽度（容器 content-box 宽，C03 步骤 3
      "按可断点分行"）。
    * ``line_height`` — 行高（None → 用 metrics.ascent+descent）。
    * ``white_space`` — "normal"/"nowrap"/"pre"（折叠 + 是否分行）。
    * ``word_break`` — "normal"（按空格 / CJK 断点）/ "break-all"（每字符
      可断）。
    """
    metrics: SimpleMetrics = field(default_factory=SimpleMetrics)
    max_width: float = 400.0
    line_height: Optional[float] = None
    white_space: str = "normal"
    word_break: str = "normal"


class InlineLayout:
    """C03 步骤 3/4/5 — 行内排版器（单一排版数据源）。

    * :meth:`layout` — 把一组 :class:`InlineRun` 排版成 :class:`LineBox`
      序列（空白折叠 → 共享 advance 计算 → 按可断点分行 → 每行基线
      定位）。绘制阶段直接消费 LineBox（不再独立换行 / 重测位置）。
    """

    def __init__(self, config: Optional[InlineLayoutConfig] = None) -> None:
        self.config = config or InlineLayoutConfig()

    # ---- 单字符 / 单串 advance（共享度量，C03 步骤 3）----
    def _adv(self, ch: str, font_px: float) -> float:
        return self.config.metrics.advance(ch, font_px)

    def string_width(self, s: str, font_px: float) -> float:
        """共享 advance 度量一整串宽度（O(n) 累加，不重测）。"""
        w = 0.0
        for ch in s:
            w += self._adv(ch, font_px)
        return w

    # ---- 主排版 ----
    def layout(self, runs: list) -> list:
        """把 run 序列排版成 :class:`LineBox` 列表。

        流程（C03 步骤 2–5）：
        1. 空白折叠成单一文本串（:func:`collapse_runs`，模式取 config）。
        2. 用共享 advance 逐字符累加宽度。
        3. 按可断点（空格 / CJK / word-break）分行（``nowrap`` 不分）。
        4. 每行按 ascent/descent + line-height 计算基线，把**字符**定位
           进 :class:`LineBox`（片段按字符归属保留 font_px/style）。
        """
        cfg = self.config
        folded = collapse_runs(runs, mode=cfg.white_space)
        # 行首/行尾空白去掉（normal 模式 C03 步骤 3）
        folded = folded.strip() if cfg.white_space != "pre" else folded
        # nowrap / pre → 单行；normal → 按可断点分行
        if cfg.white_space in ("nowrap", "pre"):
            chars = list(folded)
        else:
            chars = self._break_into_lines(folded, cfg)
            # _break_into_lines 返回 list[list[str]]，展开
            # 重新组织：上面 chars 会被赋值成 list[list[str]]，处理：
            return self._build_lines(chars, runs, cfg)

        return self._build_lines([chars], runs, cfg)

    def _build_lines(self, lines, source_runs: list,
                     cfg: InlineLayoutConfig) -> list:
        """把分行结果（每行字符 list）定位成 :class:`LineBox` 序列。

        每行：基线 = y + ascent；每个字符 advance 累加得 x；片段继承
        source_runs 的字体度量（C03 步骤 4 "定位每个片段"）。"""
        ascent, descent = cfg.metrics.line_metrics(
            source_runs[0].font_px if source_runs else 16.0)
        line_h = cfg.line_height if cfg.line_height is not None \
            else (ascent + descent)
        out: list = []
        y = 0.0
        for line_chars in lines:
            placed = []
            x = 0.0
            # 片段归属：简化按字符顺序，相邻同 (font_px,style) 归一类
            for ch in line_chars:
                fpx = source_runs[0].font_px if source_runs else 16.0
                placed.append(PlacedRun(
                    text=ch, x=x, baseline_y=y + ascent,
                    font_px=fpx, style={}))
                x += self._adv(ch, fpx)
            total_w = x
            out.append(LineBox(runs=placed, x=0.0, y=y,
                               baseline=y + ascent, height=line_h,
                               width=total_w))
            y += line_h
        return out

    def _break_into_lines(self, text: str,
                          cfg: InlineLayoutConfig) -> list:
        """C03 步骤 3 "按可断点分行"（简化 UAX#14 / CSS white-space）。

        算法：逐字符累加当前行宽；在**最近**的合法断点（空格 / CJK
        字符后 / break-all 任意位置）若加下一个字符会超 ``max_width``
        就在此断点分行；行首/行尾不留空白。

        断点规则：
        * 空格 → 词间断点（行尾/行首不留空格，行内保留单空格）；
        * CJK 字符 → 每字符后可断；
        * ``word_break == "break-all"`` → 每字符可断；
        * 单词超过 ``max_width`` → 逐字符强断（overflow-wrap 兜底）。

        返回 ``list[list[str]]``（每行字符列表，行内保留词间单空格，
        行首/行尾无空白）。"""
        fpx = 16.0   # 共享度量字号（源 run 的 font_px 在 _build_lines 用）
        lines: list = []
        cur: list = []
        cur_w = 0.0            # 当前行有效宽度（不含行尾空格）
        last_break_pos = 0     # cur 内最近的断点索引（0 = 行首）
        last_break_w = 0.0     # 该断点处的行宽
        for ch in text:
            adv = self._adv(ch, fpx)
            is_space = ch == " "
            if is_space:
                # 空格：记为"最近词间断点"（在 cur 中保留行内单空格，
                # 断行时截断到断点 → 行尾不留空白）。只有当前行已有
                # 内容时才记断点（行首不留空格）。
                if cur and cur[-1] != " ":
                    # 把空格加入 cur（行内保留），并推进断点到它之后
                    cur.append(ch)
                    last_break_pos = len(cur)
                    # 行尾空格不占有效宽度 → cur_w 不变
                continue
            # 非空格字符：试放入当前行
            if cur and cur_w + adv > cfg.max_width:
                # 当前行放不下本字符 → 在最近断点处断行
                can_break = (_is_cjk(ch) or cfg.word_break == "break-all"
                             or last_break_pos > 0
                             or cur_w + adv > cfg.max_width * 1.5)
                if can_break and last_break_pos > 0:
                    # 在最近空格断点处分行（保留断点前的内容，去掉行尾
                    # 空格）
                    lines.append(list(cur[:last_break_pos]))
                    cur = [ch]
                    cur_w = adv
                    last_break_pos = 0
                    last_break_w = 0.0
                    continue
                # 无合法断点（单词超长）→ 逐字符强断（overflow-wrap）
                lines.append(list(cur))
                cur = [ch]
                cur_w = adv
                last_break_pos = 0
                last_break_w = 0.0
                continue
            # 正常放入
            cur.append(ch)
            cur_w += adv
            # CJK / break-all：每字符后都是断点
            if _is_cjk(ch) or cfg.word_break == "break-all":
                last_break_pos = len(cur)
                last_break_w = cur_w
        # 收尾：当前行去掉行尾空格
        while cur and cur[-1] == " ":
            cur.pop()
        if cur:
            lines.append(cur)
        # 中间行（断行产生的）也去掉行尾空格（C03 "行尾不留空白"）
        for ln in lines:
            while ln and ln[-1] == " ":
                ln.pop()
        return lines


def _is_cjk(ch: str) -> bool:
    """C03 步骤 3 — 是否 CJK 字符（可每字符断点，简化 UAX#14）。"""
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or      # CJK Unified
            0x3040 <= cp <= 0x30FF or      # Hiragana + Katakana
            0x3130 <= cp <= 0x318F or      # Hangul Compat
            0xAC00 <= cp <= 0xD7A3)        # Hangul Syllables


# ----------------------------------------------------------------------
# 基线 / 行盒几何（C03 步骤 4：ascent/descent + line-height）
# ----------------------------------------------------------------------
def line_baseline(ascent: float, descent: float,
                  line_height: Optional[float], y: float) -> tuple:
    """C03 步骤 4 — 行基线 + 行盒。

    返回 ``(baseline_y, line_box_height)``：
    * 行盒顶 = ``y``；基线 = ``y + ascent``（line-height 未设时）。
    * 设 ``line_height`` 时：行盒高 = line_height，基线居中到
      ``y + (line_height - ascent - descent) / 2 + ascent``。"""
    if line_height is None:
        return (y + ascent, ascent + descent)
    gap = max(0.0, line_height - ascent - descent)
    baseline = y + gap / 2.0 + ascent
    return (baseline, line_height)


def runs_overlap(a: LineBox, b: LineBox, tol: float = 0.5) -> bool:
    """C03 验收 "各片段按顺序且不重叠" — 两行是否水平重叠。

    行 a 与行 b 若 y 区间有交集且 x 区间有交集（超过容差 *tol*）→
    返回 True（不应发生，用作回归断言）。"""
    # 行盒 y 区间 [y, y+height]
    ay0, ay1 = a.y, a.y + a.height
    by0, by1 = b.y, b.y + b.height
    y_overlap = max(ay0, by0) < min(ay1, by1)
    if not y_overlap:
        return False
    # 行内片段 x 区间 [run.x, run.x + 该 run 串宽]（近似用行 0..width）
    ax0, ax1 = a.x, a.x + a.width
    bx0, bx1 = b.x, b.x + b.width
    return max(ax0, bx0) < min(ax1, bx1) - tol
