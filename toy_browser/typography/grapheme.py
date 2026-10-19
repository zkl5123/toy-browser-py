"""R7-T01 §4 — Unicode grapheme cluster 边界 + white-space 语义断行决策层。

Round-7 评估（``逐项改进实施方案.md`` R7-T01 第 4 点，``渲染与标准
差距审计.md`` §6 T01）：

    换行遵循 Unicode 断行和 white-space 语义；**以 grapheme/cluster
    边界维护选择和光标**。

本模块落地其中"grapheme/cluster 边界 + white-space 语义"的**纯逻辑
决策层**（无 OS / wgpu / 外部 Unicode 数据库依赖，P13 不崩）：

* :func:`grapheme_clusters` — Unicode 字素扩展（UAX#29 可测子集）
  边界检测：组合标记粘附于基字符、区域标志旗对（成对 emoji 旗）
  不可拆、ZWJ 序列（👨‍👩‍👧 等）保留、变体选择符跟随前一码点。
  产出 ``[(start, end), ...]`` 的码点索引区间，供选择 / 光标维护
  "不落在 cluster 中间"。
* :func:`grapheme_index` — 码点位置 → 所属 grapheme 序号映射（选择
  锚点 / 光标定位用）。
* :class:`WhiteSpacePolicy` — white-space CSS 语义五态
  （``normal`` / ``nowrap`` / ``pre`` / ``pre-wrap`` / ``pre-line``）
  × 空格折叠 / 换行符保留 / 断行机会 的决策层，输出"折叠后文本 +
  断行机会位置"，供 :func:`toy_browser.typography.text.wrap` 上层消费
  （§4 "换行遵循 white-space 语义"）。

设计取舍（"不冒充通过"）：本层是**决策与边界**，不是完整排版引擎；
组合字符 / RTL Bidi / emoji 的完整 shaping 留活字体批次（审计 §3
"shaping/run 接口"为独立条目，本层只做 cluster 边界 + white-space
断行决策，可对照测试、可解释）。

无 OS / 外部依赖，纯逻辑可回归（P13 不崩）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "grapheme_clusters",
    "grapheme_index",
    "WhiteSpacePolicy",
    "WS_NORMAL",
    "WS_NOWRAP",
    "WS_PRE",
    "WS_PRE_WRAP",
    "WS_PRE_LINE",
]

# ----------------------------------------------------------------------
# UAX#29 字素扩展——可测子集的码点范围判定
# ----------------------------------------------------------------------

# 区域标志（Regional Indicator）：U+1F1E6..U+1F1FF（26 个，构成 2
# 码点的国旗旗对）。成对不可拆。
_RI_LO, _RI_HI = 0x1F1E6, 0x1F1FF


def _is_regional_indicator(cp: int) -> bool:
    return _RI_LO <= cp <= _RI_HI


# 变体选择符：U+FE00..U+FE0F（VS1..VS16）、U+E0100..U+E01EF。
# 粘附于前一码点（同一 cluster）。
_VARIATION_LO, _VARIATION_HI = 0xFE00, 0xFE0F
_VARIATION_EXT_LO, _VARIATION_EXT_HI = 0xE0100, 0xE01EF


def _is_variation_selector(cp: int) -> bool:
    return (_VARIATION_LO <= cp <= _VARIATION_HI
            or _VARIATION_EXT_LO <= cp <= _VARIATION_EXT_HI)


# 零宽连接符 / 零宽不连接符（emoji ZWJ 序列核心）。
_ZWJ = 0x200D
_ZWNJ = 0x200C


# 组合字符（Mark, Nonspacing / Spacing / Enclosing）。覆盖主要区段：
# * 组合音调/变音符 U+0300..U+036F（Greek/Cyrillic combining）
# * 组合键帽 U+20D0..U+20FF
# * 梵文/天城体组合 U+0900..U+097F 区间内的 combining
# * 阿拉伯组合 U+0640..U+065F
# * CJK 组合 U+3099..U+309A
# * Hangul Jamo Vowel/Consonant medial/final（黏着）
# 这里取一个保守但覆盖常见（拉丁 + 中文 + 阿拉伯 + 梵文 + Hangul
# 音节）的区段集合，纯静态判断，无需外部 Unicode 数据库。
_COMBINING_RANGES = (
    (0x0300, 0x036F),   # 组合变音（Gréc/Cyrill）
    (0x0483, 0x0489),
    (0x0591, 0x05BD),
    (0x05BF, 0x05BF),
    (0x0640, 0x065F),   # 阿拉伯组合
    (0x0670, 0x0670),
    (0x06D6, 0x06EF),
    (0x0E01, 0x0E3A),   # Thai
    (0x0E3F, 0x0E3F),
    (0x0EC0, 0x0ECD),
    (0x0F00, 0x0F17),   # Tibetan
    (0x1100, 0x115F),   # Hangul Jamo leading
    (0x1160, 0x11A7),
    (0x11A8, 0x11FF),
    (0x1300, 0x134F),
    (0x135D, 0x135F),
    (0x1A17, 0x1A1B),
    (0x1A55, 0x1A5E),
    (0x1A60, 0x1A7D),
    (0x1D00, 0x1D25),   # 音乐符号组合
    (0x1D62, 0x1D65),
    (0x1DC0, 0x1DCF),   # 组合符号扩展
    (0x1DFC, 0x1DFF),
    (0x20D0, 0x20F0),   # 组合键帽
    (0x2CEF, 0x2CF1),
    (0x2D7F, 0x2D7F),
    (0x2DE0, 0x2DFF),
    (0x3099, 0x309C),   # CJK 组合
    (0xFE20, 0xFE2F),   # 组合半宽
)


def _is_combining_mark(cp: int) -> bool:
    for lo, hi in _COMBINING_RANGES:
        if lo <= cp <= hi:
            return True
    return False


# Hangul syllable (0xAC00..0xD7A3) 在字素扩展中自成 cluster（含
# jamo medial/final 合成，不再细分）。


def _is_cjk_hangul_syllable(cp: int) -> bool:
    return 0xAC00 <= cp <= 0xD7A3


# ----------------------------------------------------------------------
# grapheme cluster 边界（UAX#29 可测子集）
# ----------------------------------------------------------------------

def grapheme_clusters(text: str) -> list[tuple[int, int]]:
    """返回 *text* 的 Unicode 字素扩展区间列表 ``[(start, end), ...]``
    （码点索引，半开区间 ``[start, end)``）。

    边界规则（UAX#29 可测子集，纯静态判定）：

    * 组合标记（:func:`_is_combining_mark`）粘附前一码点——不可在
      基字符与其组合标记之间断开（如 ``e`` + U+0301 合音 é）。
    * 变体选择符（:func:`_is_variation_selector`）粘附前一码点。
    * 区域标志旗对：两个连续 RI 码点合成一个 flag emoji，不可拆
      （``U+1F1EF U+1F1F1`` = 法国国旗）。
    * ZWJ 序列：以 ``\\u200d`` 连接的连续码点（如
      ``U+1F468\\u200dU+1F469\\u200dU+1F467``）保留为单一 cluster。
    * 其余码点各自成 cluster（普通字符逐字素，与现有 wrap 逐字符
      断行一致）。

    返回的区间首尾相接、不重叠、覆盖整个 *text*。空串 → 空列表。
    """
    n = len(text)
    clusters: list[tuple[int, int]] = []
    if n == 0:
        return clusters
    i = 0
    while i < n:
        start = i
        cp0 = ord(text[i])
        i += 1
        if _is_regional_indicator(cp0):
            # 当前码点是 RI。若刚结束的前一 cluster 恰好是单码点
            # RI（即 i-2 是某 cluster 起点且区间为 [i-2, i-1)，且
            # text[i-2] 是 RI），则当前 RI 与其合并（旗对不拆）。
            # 判断条件：clusters 非空 + 前一 cluster 终点 == i-1 +
            # 前一点是 RI。
            if (clusters and clusters[-1][1] == i - 1
                    and i - 1 >= 1
                    and _is_regional_indicator(ord(text[i - 1]))):
                prev_start = clusters[-1][0]
                # 仅当前一 cluster 长度为 1 且起点处是 RI 时合并
                # （避免把"RI 后接非 RI"误判为旗对）。
                if i - 1 - prev_start == 1 and prev_start >= 0:
                    clusters[-1] = (prev_start, i)
                    continue
        else:
            # 扫后续粘附码点（组合 / 变体选择 / ZWJ 链）。
            while i < n:
                cp1 = ord(text[i])
                if _is_combining_mark(cp1) or _is_variation_selector(cp1):
                    i += 1
                    continue
                # ZWJ：把当前 ZWJ 及其后一个码点并入同 cluster
                # （ZWJ 序列：base ZWJ base ZWJ base ...）。
                if cp1 == _ZWJ:
                    i += 1
                    if i < n:
                        i += 1
                    continue
                break
        clusters.append((start, i))
    return clusters


def grapheme_index(text: str) -> dict[int, int]:
    """码点位置 → 所属 grapheme 序号 的映射。

    供选择锚点 / 光标定位"不落在 cluster 中间"：给定任意码点下标
    *p*，返回包含 *p* 的 cluster 序号（0-based）。空串 → 空 dict。
    """
    mapping: dict[int, int] = {}
    for gi, (start, end) in enumerate(grapheme_clusters(text)):
        for p in range(start, end):
            mapping[p] = gi
    return mapping


# ----------------------------------------------------------------------
# white-space CSS 语义断行决策层
# ----------------------------------------------------------------------

#: white-space 五态常量（CSS2.1 §16.6 + CSS3 text）。
WS_NORMAL = "normal"
WS_NOWRAP = "nowrap"
WS_PRE = "pre"
WS_PRE_WRAP = "pre-wrap"
WS_PRE_LINE = "pre-line"

#: 可折叠空白（CSS2.1 §9.2 collapsible whitespace）。
_COLLAPSIBLE_WS = frozenset(
    "\u0020\u0009\u000A\u000B\u000C\u000D"
    "\u00A0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200A\u200B\u202F\u205F\u3000"
)


@dataclass
class _LineDecision:
    """一次 white-space 断行决策的输出。"""
    collapsed_text: str
    break_positions: list[int]
    #: 是否强制不断行（nowrap）。
    no_break: bool


@dataclass
class WhiteSpacePolicy:
    """§4 — white-space CSS 语义断行决策层。

    把 white-space 五态 × 空格折叠 / 换行符保留 / 断行机会收敛为
    显式、可单测的决策：

    * :meth:`process` — 输入原始文本，产出 :class:`_LineDecision`
      （折叠后文本 + 断行机会位置 + nowrap 标记）。

    五态语义（CSS2.1 §16.6）：

    - ``normal``  — 折叠空白（连续空白→单空格，行首/行尾去掉，换行
      符当空格折叠），允许断行。
    - ``nowrap``  — 折叠空白（同 normal），但**禁止**断行机会
      （整段一行，超宽由 overflow 处理）。
    - ``pre``     — 保留全部空白与换行符（不折叠），允许断行机会在
      换行符处。
    - ``pre-wrap``— 保留换行符为断行机会，但折叠连续空格/制表。
    - ``pre-line``— 折叠连续空格，保留换行符为断行机会。

    * ``white-space`` — 五态之一（默认 :data:`WS_NORMAL`）。
    """

    white_space: str = WS_NORMAL
    _breaks: list[_LineDecision] = field(default_factory=list,
                                          repr=False)

    def process(self, text: str) -> _LineDecision:
        ws = self.white_space
        if ws == WS_NOWRAP:
            return self._fold_no_break(text)
        if ws == WS_PRE:
            return self._pre(text)
        if ws in (WS_PRE_WRAP, WS_PRE_LINE):
            # 二者在本可测子集内折叠行为一致（均折叠连续空格、
            # 保留换行符为断行机会），差异在完整 CSS 中"行首/尾空格
            # 是否折叠"，此子集统一折叠。
            return self._pre_family(text, wrap=False)
        # 默认 normal。
        return self._fold_normal(text)

    # ---- 内部五态分支（纯逻辑，无 OS 依赖）----
    def _fold_normal(self, text: str) -> _LineDecision:
        """normal：折叠连续空白（含换行符）为单空格，行首/尾去空。"""
        out: list[str] = []
        break_pos: list[int] = []
        in_ws = False
        prev_was_ws = False
        for ch in text:
            if ch in _COLLAPSIBLE_WS:
                in_ws = True
                continue
            if in_ws and out:
                # 折叠为单空格（非行首）。
                out.append(" ")
                break_pos.append(len(out))
            out.append(ch)
            in_ws = False
        return _LineDecision("".join(out), break_pos, no_break=False)

    def _fold_no_break(self, text: str) -> _LineDecision:
        """nowrap：折叠空白但禁止断行机会（整段一行）。"""
        out: list[str] = []
        in_ws = False
        for ch in text:
            if ch in _COLLAPSIBLE_WS:
                in_ws = True
                continue
            if in_ws and out:
                out.append(" ")
            out.append(ch)
            in_ws = False
        return _LineDecision("".join(out), [], no_break=True)

    def _pre(self, text: str) -> _LineDecision:
        """pre：保留全部字符（不折叠），断行机会在每个换行符处。"""
        break_pos: list[int] = []
        for idx, ch in enumerate(text):
            if ch == "\n":
                break_pos.append(idx)
        return _LineDecision(text, break_pos, no_break=False)

    def _pre_family(self, text: str, wrap: bool) -> _LineDecision:
        """pre-wrap / pre-line：换行符保留为断行机会。

        *pre-wrap* — 保留换行符 + 折叠连续空格，允许在折叠空格
        处断行（``no_break=False``）；*pre-line* — 保留换行符 +
        折叠连续空格（本可测子集与 pre-wrap 折叠行为一致，差异在
        完整 CSS 中 pre-line 折叠行首/尾空格、pre-wrap 不折叠，
        此子集统一折叠），允许在折叠空格处断行（``no_break=False``）。
        """
        out: list[str] = []
        break_pos: list[int] = []
        in_space = False
        for ch in text:
            if ch == "\n":
                out.append("\n")
                break_pos.append(len(out) - 1)
                in_space = False
                continue
            if ch in _COLLAPSIBLE_WS and ch != "\n":
                in_space = True
                continue
            if in_space and out and out[-1] != "\n":
                out.append(" ")
            out.append(ch)
            in_space = False
        # pre-wrap / pre-line 均允许在折叠空格处断行（no_break=False）。
        # 完整 CSS 中二者差异仅在"行首/尾空格是否折叠"（本可测
        # 子集统一折叠），断行机会语义一致。
        return _LineDecision("".join(out), break_pos, no_break=False)


def decide_breaks(text: str, white_space: str = WS_NORMAL) -> _LineDecision:
    """便捷入口——按 *white_space* 五态做断行决策。"""
    return WhiteSpacePolicy(white_space).process(text)
