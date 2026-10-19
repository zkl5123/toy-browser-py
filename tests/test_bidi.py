"""R7-T01 — Unicode bidi（双向文本）简化算法回归。

被测：``toy_browser/typography/bidi.py``
（bidi_class / bidi_classes / resolve_weak / visual_runs / visual_order /
base_dir / reorder_for_display）。

设计：纯标准库 + 无 tkinter 的确定性逻辑回归。验证"RTL 字样出现可验证
重排改善，而非 SHA-256 不变"——锁定阿语/希伯来/数字/中文混排下
强/弱方向性分类与 L2 视觉重排的关键契约。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from toy_browser.typography import bidi

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 单字符方向性分类
# ---------------------------------------------------------------------------

@_reg
def test_class_latin_ltr():
    """基本拉丁字母 / 带声调扩展 → 强 LTR。"""
    assert bidi.bidi_class("a") == bidi.L
    assert bidi.bidi_class("A") == bidi.L
    assert bidi.bidi_class("é") == bidi.L  # U+00E9 带声调拉丁


@_reg
def test_class_cjk_ltr():
    """CJK 统一表意文字 → 强 LTR（视觉逐字但书写向 LTR）。"""
    assert bidi.bidi_class("中") == bidi.L
    assert bidi.bidi_class("日") == bidi.L


@_reg
def test_class_arabic_strong_rtl():
    """阿语字母 → AL（强 RTL）；希伯来 → R。"""
    assert bidi.bidi_class("ن") == bidi.AL
    assert bidi.bidi_class("ا") == bidi.AL
    assert bidi.bidi_class("א") == bidi.R


@_reg
def test_class_digits():
    """ASCII/全角数字 → EN；阿语-印度数字 0x0660–0x0669 → AN。

    关键契约：阿语数字必须先于阿语强区判定（落在 0x0600–0x06FF 内，
    否则会误判成 AL）。
    """
    assert bidi.bidi_class("1") == bidi.EN
    assert bidi.bidi_class("9") == bidi.EN
    assert bidi.bidi_class("٠") == bidi.AN  # U+0660
    assert bidi.bidi_class("٥") == bidi.AN  # U+0665


@_reg
def test_class_ws_on_empty():
    """空格 → WS；标点 → ON；空串 → N。"""
    assert bidi.bidi_class(" ") == bidi.WS
    assert bidi.bidi_class("!") == bidi.ON
    assert bidi.bidi_class("") == bidi.N


@_reg
def test_classes_sequence_length():
    """bidi_classes 返回与输入等长的序列。"""
    s = "Hi مرحبا 2"
    assert len(bidi.bidi_classes(s)) == len(s)


# ---------------------------------------------------------------------------
# 弱类解析
# ---------------------------------------------------------------------------

@_reg
def test_resolve_weak_digits_follow_ltr():
    """弱类数字邻域 LTR → 解析为 L。"""
    cls = [bidi.L, bidi.EN, bidi.L]  # L 1 L
    out = bidi.resolve_weak(cls)
    assert out[1] == bidi.L, "digit between L must resolve to L"


@_reg
def test_resolve_weak_digits_follow_rtl():
    """弱类数字邻域 RTL → 解析为 N（RTL 向）。"""
    cls = [bidi.AL, bidi.EN, bidi.AL]  # ن 1 ن
    out = bidi.resolve_weak(cls)
    assert out[1] == bidi.N, "digit between AL must resolve to N (RTL)"


# ---------------------------------------------------------------------------
# base_dir
# ---------------------------------------------------------------------------

@_reg
def test_base_dir_latin():
    assert bidi.base_dir("Arabic") == "ltr"
    assert bidi.base_dir("123") == "ltr"


@_reg
def test_base_dir_arabic():
    assert bidi.base_dir("مرحبا") == "rtl"
    assert bidi.base_dir("אבג") == "rtl"  # Hebrew strong R


@_reg
def test_base_dir_no_strong_default_ltr():
    """无强类（纯数字/标点）→ 默认 ltr。"""
    assert bidi.base_dir("!?") == "ltr"
    assert bidi.base_dir("") == "ltr"


# ---------------------------------------------------------------------------
# L2 视觉重排
# ---------------------------------------------------------------------------

@_reg
def test_ltr_base_embedded_arabic_run_reversed():
    """LTR base 内嵌阿语 run 在视觉序中内部反向。

    'Hi مرحبا there'：阿语词 5 个字母在逻辑序 [3..7]，视觉阅读时
    从右到左 → 该 run 在视觉序里应倒序，而拉丁词与空格正向。
    """
    s = "Hi مرحبا there"
    vis, idx = bidi.reorder_for_display(s, "ltr")
    assert len(vis) == len(s)
    # 前缀 'Hi ' 逻辑序不变；阿语词 5 字母在 idx 中应倒序出现；
    # 验证：阿语 run 的逻辑索引在视觉序里相对原文是反的。
    ar_run = [i for i in range(len(s)) if bidi.bidi_class(s[i]) == bidi.AL]
    # 视觉序中阿语 run 的索引相对原文倒序。
    vis_ar = [i for i in idx if i in set(ar_run)]
    assert vis_ar == list(reversed(sorted(ar_run))), (
        f"Arabic run must be reversed in LTR visual order: {vis_ar} "
        f"vs reversed(sorted)={list(reversed(sorted(ar_run)))}")


@_reg
def test_rtl_base_latin_run_kept_forward():
    """RTL base 内嵌拉丁 run 在视觉序中保持正向。

    'مرحبا Hello'（RTL 文档）：阿语词在右（视觉先读），拉丁 'Hello'
    在视觉上正向书写（H 在 e 左边）。视觉序 = 逻辑整体倒序后，
    内嵌 LTR run 恢复正向。
    """
    s = "مرحبا Hello"
    vis, idx = bidi.reorder_for_display(s, "rtl")
    assert vis.endswith("Hello") or "Hello" in vis, (
        f"'Hello' must appear forward in RTL visual order: {vis!r}")
    # 关键契约：'Hello' 在视觉序里保持字符正向（H 先于 o）。
    assert "Hello" in vis, f"forward 'Hello' missing in {vis!r}"
    assert "olleH" not in vis, "latin run must NOT be reversed in RTL base"


@_reg
def test_pure_ltr_identity():
    """纯 LTR 文本视觉序 = 逻辑序（恒等，不重排）。"""
    s = "hello world"
    vis, idx = bidi.reorder_for_display(s, "ltr")
    assert vis == s
    assert idx == list(range(len(s)))


@_reg
def test_pure_rtl_full_reverse():
    """纯 RTL 文本在 RTL base 下视觉序 = 逻辑序倒序。"""
    s = "مرحبا"
    vis, idx = bidi.reorder_for_display(s, "rtl")
    assert idx == list(range(len(s) - 1, -1, -1))
    assert vis == s[::-1]


@_reg
def test_empty_input():
    """空输入 → 空输出（不崩）。"""
    vis, idx = bidi.reorder_for_display("", "ltr")
    assert vis == "" and idx == []


@_reg
def test_deterministic():
    """同一输入恒给同一输出（可缓存契约）。"""
    s = "Arabic مرحبا 123 done"
    a1, i1 = bidi.reorder_for_display(s, "rtl")
    a2, i2 = bidi.reorder_for_display(s, "rtl")
    assert a1 == a2 and i1 == i2


if __name__ == "__main__":
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(TESTS)} tests, {failed} failed")
    sys.exit(1 if failed else 0)
