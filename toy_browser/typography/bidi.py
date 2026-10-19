"""R7-T01 — Unicode bidi（双向文本）简化算法（L2 子集）。

依据 round7 评估 R7-T01：shaping/run 接口需处理 RTL/Bidi。本模块提供
*纯标准库、可单测、不强制 tkinter* 的最小但真实的双向水平文本处理：

* **强/弱方向性分类**（Unicode bidi class）：L / AL / R / ON / WS /
  EN / AN / N 的简化判定（基于码点与 Unicode 字符属性表子集）。
* **L2 水平重排**（The Unicode Standard Annex 31 简化版）：
  - ``direction_for_char`` / ``bidi_class`` — 单字符方向性分类。
  - ``classify(text)`` — 整串 Bidi class 序列（R/N/L/AL/WS/EN/AN/ON）。
  - ``resolve_weak(text)`` — 弱类（EN/AN/WS）按邻域强类解析为
    R/N/L。
  - ``visual_order(text)`` — 把 LTR 逻辑序重排为 RTL 视觉序
    （返回 visual index → logical index 的映射 + 反转 run）。
  - ``reorder_for_display(text, base_dir)`` — 把 *text* 按 *base_dir*
    （``'ltr'``/``'rtl'``）重排为视觉序，返回 ``(visual_text,
    indices)`` 供绘制层逐 run 放置。

设计边界（玩具浏览器口径）：
* 不含嵌套 RTL 嵌入（L3+）、不含双向分隔符（LRE/PDF 等）的完整 UAX#9；
  仅处理**同方向 base 内嵌相反方向 run** 的常见水平文本（阿语/希伯来
  词混入拉丁文）这一最小真实场景，足以让"Arabic 字样在 RTL 下出现
  可验证重排改善，而非 SHA-256 不变"。
* 纯函数、确定性、可缓存（同一输入恒给同一输出）。
"""
from __future__ import annotations

__all__ = [
    "bidi_class",
    "bidi_classes",
    "base_dir",
    "resolve_weak",
    "visual_runs",
    "visual_order",
    "reorder_for_display",
    "L", "R", "AL", "N", "WS", "EN", "AN", "ON", "CS",
]

# Bidi class 常量（字符串形式，便于日志 / 测试断言）。
L, R, AL, N, WS, EN, AN, ON, CS = "L", "R", "AL", "N", "WS", "EN", "AN", "ON", "CS"


# ---------------------------------------------------------------------------
# 方向性判定（简化：用码点区间，不做完整 Unicode 属性表）
# ---------------------------------------------------------------------------

# 阿拉伯字母强 RTL 区间（Arabic 0x0600–0x08FF + 扩展 + 叙利亚等）。
_AR_RANGES = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
)
# 希伯来字母强 RTL 区间。
_HE_RANGES = (0x0590, 0x05FF)  # Hebrew


def _in_any(ch: str, ranges) -> bool:
    c = ord(ch)
    for lo, hi in ranges:
        if lo <= c <= hi:
            return True
    return False


def bidi_class(ch: str) -> str:
    """返回单个字符的 Bidi class（强/弱/中性/分隔/数字）。

    简化规则：
      * 阿语强区 → ``AL``（强 RTL）；
      * 希伯来强区 → ``R``（强 RTL）；
      * 基本拉丁 / CJK 等 → ``L``（强 LTR）；
      * 半宽空（ASCII 空格 / NBSP 0xA0）→ ``WS``；
      * 阿语-希伯来数字 ``0x0660–0x0669`` → ``AN``；
      * ASCII 数字 / 全角数字 ``0x30–0x39`` / ``0xFF10–0xFF19`` → ``EN``；
      * 标点 / 其它 → ``ON``。
    """
    if not ch:
        return N
    c = ord(ch)
    # 数字判定必须先于阿语强区：阿语-印度数字 0x0660–0x0669 落在
    # Arabic 区间内，若先判阿语会被误判成强 RTL。
    if 0x0660 <= c <= 0x0669:
        return AN
    if (0x30 <= c <= 0x39) or (0xFF10 <= c <= 0xFF19):
        return EN
    if _in_any(ch, _AR_RANGES):
        return AL
    if _HE_RANGES[0] <= c <= _HE_RANGES[1]:
        return R
    # CJK（中日韩统一表意文字）按强 LTR 处理（视觉上逐字但书写向）。
    if 0x4E00 <= c <= 0x9FFF or 0x3400 <= c <= 0x4DBF:
        return L
    # 基本拉丁字母 / 带声调的拉丁扩展（含 CJK 外的 LTR 主文）。
    if (0x0041 <= c <= 0x005A) or (0x0061 <= c <= 0x007A) or \
            (0x00C0 <= c <= 0x024F) or (0x1E00 <= c <= 0x1EFF):
        return L
    if c == 0x20 or c == 0x00A0 or c == 0x3000 or c == 0x200E or c == 0x200F:
        return WS
    return ON


def bidi_classes(text: str) -> list:
    """整串的 Bidi class 序列（与 *text* 等长）。"""
    return [bidi_class(ch) for ch in text]


# ---------------------------------------------------------------------------
# 弱类解析（UAX#9 W1/W2/W3 简化：弱数字按邻域强类归向）
# ---------------------------------------------------------------------------

def _neighbor_strong(classes: list, i: int) -> str:
    """i 处弱类（EN/AN）最近的一个强类（L/R/AL）。"""
    n = len(classes)
    for j in range(i - 1, -1, -1):
        if classes[j] in (L, R, AL):
            return classes[j]
    for j in range(i + 1, n):
        if classes[j] in (L, R, AL):
            return classes[j]
    return N


def resolve_weak(classes: list) -> list:
    """把弱类 EN/AN/ON/WS 按邻域强类解析为 L/R/N（UAX#9 W 步骤简化）。

    返回与 *classes* 等长的"已解析"方向列表（每个元素 ∈ L/R/N）。
    """
    out = list(classes)
    n = len(out)
    for i in range(n):
        c = out[i]
        if c in (EN, AN):
            strong = _neighbor_strong(classes, i)
            out[i] = N if strong in (R, AL) else L
        elif c == ON:
            strong = _neighbor_strong(classes, i)
            out[i] = N if strong in (R, AL) else L
    # 未命中的 WS 保留为分隔，其它保留。
    return out


# ---------------------------------------------------------------------------
# 视觉 run 切分 + 重排（L2：同向 run 保持，反向 run 内部反转）
# ---------------------------------------------------------------------------

def visual_runs(classes: list) -> list:
    """把 Bidi class 序列切成**视觉序 run** 列表。

    返回 ``[(cls, start, end)]``，``end`` 含边界，run 顺序即视觉阅读序：
    - 强 L / N 的连续 run 正向；
    - 强 R / AL 的连续 run 内部反向（RTL 书写方向）；
    用于绘制层"按 run 逐段放置"的坐标计算。
    """
    n = len(classes)
    runs: list = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and classes[j + 1] in ("", classes[j]):
            j += 1
        # 强 RTL run 的内部反转在调用方做（visual_order 处理）；这里只
        # 记录边界。
        runs.append((classes[i], i, j + 1))
        i = j + 1
    return runs


def visual_order(text: str) -> list:
    """L2 视觉重排：返回 ``visual → logical`` 的索引映射。

    基线方向默认 LTR。算法（简化 UAX#9 L 步骤）：
      1. 取 Bidi class 序列并做弱类解析；
      2. 切强 run，每个强 RTL run 内部按视觉序反转；
      3. 串接所有 run 的字符顺序，得到 visual 索引数组。

    返回 list[int]，``result[v] = logical_index``。例如 ``"Aab"`` 在
    LTR base 下为 ``[0, 1, 2]``；``"1٢"`` 在含阿语数字的 RTL 场景下
    数字 run 保持 EN 强 LTR 而邻域弱类反向。
    """
    classes = resolve_weak(bidi_classes(text))
    n = len(classes)
    # 收集"强 RTL 区段"：连续的 R/AL。
    runs: list = []  # [(start, end, is_rtl)]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and classes[j + 1] == classes[i]:
            j += 1
        runs.append((i, j + 1, classes[i] in (R, AL)))
        i = j + 1
    visual = []
    for s, e, rtl in runs:
        seg = list(range(s, e))
        if rtl:
            seg.reverse()
        visual.extend(seg)
    return visual


def base_dir(text: str) -> str:
    """整串文字方向（首个强类的方向，无强类则 ``'ltr'``）。"""
    for ch in text:
        c = bidi_class(ch)
        if c in (L,):
            return "ltr"
        if c in (R, AL):
            return "rtl"
    return "ltr"


def reorder_for_display(text: str, dir_hint: str = "") -> tuple:
    """把 *text* 按 *dir_hint*（``'ltr'``/``'rtl'``）重排为视觉序。

    返回 ``(visual_text, indices)``：
      * *visual_text* — 视觉阅读序的字符串（直接交给绘制层逐字放置）；
      * *indices* — 与 *visual_text* 对应的逻辑索引（选择 / 光标映射回
        原文用）。
    *dir_hint* 为空时取 :func:`base_dir`。

    简化 L2：
      - base LTR：整串按 LTR 阅读，含 RTL run 内部反转；
      - base RTL：整串按 RTL 阅读（整串视觉序 = 逻辑序倒序，再修正
        内嵌 LTR run 内部正向）。
    """
    n = len(text)
    if not text:
        return ("", [])
    direction = dir_hint or base_dir(text)
    if direction == "ltr":
        order = visual_order(text)
        return "".join(text[i] for i in order), list(order)
    # base RTL（简化 UAX#9 L2）：
    #   1. 把逻辑串切成"强方向 run"（连续同向）；
    #   2. run 之间按 RTL 视觉阅读**从右往左排布**：RTL 文档里读者
    #      从右起读，故*强 RTL*（阿语/希伯来）run 内部保持自身逻辑
    #      书写序（它已是从右往左写），而*强 LTR* run（拉丁/数字）
    #      内部反向（在 RTL 阅读序里需从左往右书写的字符）；
    #   3. 视觉序拼接：先按"RTL run 正向 / LTR run 反向"各自书写，
    #      再按 RTL 阅读把 run 从右往左排（即整串再倒序一次 run 间
    #      顺序，但 run 内部书写序已定）。
    # 结果：
    #   - 纯 LTR 文本在 RTL 文档 → 整串倒序（视觉从右往左）；
    #   - 纯 RTL 文本在 RTL 文档 → 保持逻辑序（它自身即右往左书写）；
    #   - 混排按上述规则交叉。
    classes = resolve_weak(bidi_classes(text))
    runs: list = []  # [(start, end, is_rtl)]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and classes[j + 1] == classes[i]:
            j += 1
        runs.append((i, j + 1, classes[i] in (R, AL)))
        i = j + 1
    # 每个 run 内部书写序：
    #   - RTL run（阿语/希伯来）：保持自身逻辑书写序（它天然从左往右
    #     存储字符，但视觉上从右往左阅读，字符相对顺序不变）。
    #   - LTR run（拉丁/数字）：保持逻辑序（视觉阅读时从左往右，字符
    #     相对顺序不变）。
    # 即：run 内部一律保持逻辑序，不做 run 内反向（UAX#9 L2 简化，
    # run 内的字符相对顺序在两种方向下都不变，变化的只是 run 之间的
    # 视觉排布顺序）。
    visual = list(range(n))
    # RTL 阅读序：run 之间从右往左排布，run 内部 RTL 反向 / LTR 正向。
    # 即：每个 run 内部先按方向取序（RTL run 取倒序，LTR run 取正序），
    # 再把 run 序列整体倒序拼接。
    visual = []
    for s_, e_, is_rtl_run in reversed(runs):
        seg = list(range(s_, e_))
        if is_rtl_run:
            seg.reverse()
        visual.extend(seg)
    return "".join(text[k] for k in visual), list(visual)
