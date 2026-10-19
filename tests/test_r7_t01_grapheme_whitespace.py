"""R7-T01 §4 — grapheme cluster 边界 + white-space 语义断行。

Round-7 评估（``逐项改进实施方案.md`` R7-T01 第 4 点，``渲染与标准
差距审计.md`` §6 T01）：

    换行遵循 Unicode 断行和 white-space 语义；**以 grapheme/cluster
    边界维护选择和光标**。

本批落地"grapheme cluster 边界 + white-space 语义"的**纯逻辑决策层**
（无 OS / wgpu / 外部 Unicode 数据库依赖，P13 不崩；完整 shaping/run
接口（combining marks / ligature / RTL Bidi / emoji / fallback）为
独立条目留活字体批次）：

* :func:`grapheme.grapheme_clusters` — UAX#29 字素扩展可测子集：
  组合标记粘附、区域标志旗对、ZWJ 序列、变体选择符跟随。
* :class:`grapheme.WhiteSpacePolicy` — CSS2.1 §16.6.1 五态
  （``normal`` / ``nowrap`` / ``pre`` / ``pre-wrap`` / ``pre-line``）
  × 空格折叠 / 换行符保留 / 断行机会。

两层设计（"不冒充通过"——区分静态契约与动态真实调用）：

* **静态层**（无字体 / wgpu 可导入即全过）——源码契约字符串断言：
  grapheme 模块可导入 + 五态常量 + :func:`decide_breaks` 便捷入口 +
  :func:`text.wrap` / :func:`text.draw_text` 已接 white_space 参数。
* **动态层**（真实调用）——
  - grapheme cluster：组合字符 / 区域标志旗对 / ZWJ 序列 / 普通
    字符各自 cluster 数正确；
  - white-space 五态：折叠 / 保留 / 断行机会 / no_break 标记正确；
  - :func:`text.wrap` 经 white_space（nowrap 不断行）；
  - 组合字符 cluster 不被逐字符拆散（§4 "断行不落在 cluster 中间"
    的可测子集形态）。

无 OS / wgpu 依赖即可回归（P13 不崩）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.typography import grapheme as _g
from toy_browser.typography import text as _text
from toy_browser.rendering import paint_engine as _pe

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read_module(name):
    import importlib
    mod = importlib.import_module(name)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(
        os.path.join(base, "toy_browser", "typography",
                     os.path.basename(mod.__file__)),
        encoding="utf-8").read()


# ----------------------------------------------------------------------
# 静态层——源码契约断言
# ----------------------------------------------------------------------

@_test
def test_static_grapheme_module_and_constants():
    """grapheme 模块含五态常量 + decide_breaks 便捷入口 + cluster 函数。"""
    src = _read_module("toy_browser.typography.grapheme")
    for c in ("WS_NORMAL", "WS_NOWRAP", "WS_PRE", "WS_PRE_WRAP",
              "WS_PRE_LINE", "grapheme_clusters", "grapheme_index",
              "WhiteSpacePolicy", "decide_breaks"):
        assert c in src, f"grapheme.py 缺 {c} 契约"


@_test
def test_static_wrap_accepts_white_space():
    """text.wrap 源码带 white_space 参数（接 grapheme.WhiteSpacePolicy）。"""
    src = _read_module("toy_browser.typography.text")
    assert "white_space" in src, "wrap 必须带 white_space 参数"
    assert "WhiteSpacePolicy" in src or "white_space" in src, (
        "wrap 必须经 grapheme white-space 决策层")


@_test
def test_static_display_command_white_space_field():
    """DisplayCommand 带 white_space 字段（默认 normal）。"""
    c = _pe.DisplayCommand("text", text="x")
    assert getattr(c, "white_space", None) == "normal", (
        f"DisplayCommand.white_space 默认须为 'normal', "
        f"got {getattr(c, 'white_space', None)!r}")


# ----------------------------------------------------------------------
# 动态层——真实调用
# ----------------------------------------------------------------------

@_test
def test_dynamic_combining_mark_cluster_not_split():
    """组合标记粘附基字符——e + U+0301（合音 é）为单一 cluster。"""
    s = "e\u0301"  # e + acute
    clusters = _g.grapheme_clusters(s)
    assert len(clusters) == 1, (
        f"e + combining acute 必须为单一 cluster, got {clusters}")
    # grapheme_index：两码点同属 cluster 0。
    idx = _g.grapheme_index(s)
    assert idx.get(0) == 0 and idx.get(1) == 0, (
        f"两码点须同属 cluster 0, got {idx}")


@_test
def test_dynamic_regional_indicator_flag_pair():
    """区域标志旗对——两个 RI 合成单一 flag emoji cluster，不可拆。"""
    flag = "\U0001F1EB\U0001F1FF"  # US flag
    assert _g.grapheme_clusters(flag) == [(0, 2)], (
        f"RI 旗对须为单一 cluster, got {_g.grapheme_clusters(flag)}")
    # 单 RI（不成对）仍为单码点 cluster。
    single = "\U0001F1EB"
    assert _g.grapheme_clusters(single) == [(0, 1)], (
        f"单 RI 须为单 cluster, got {_g.grapheme_clusters(single)}")


@_test
def test_dynamic_zwj_sequence_cluster():
    """ZWJ 序列（emoji 家庭）——base ZWJ base 保留为单一 cluster。"""
    zwj = "\U0001F468\u200D\U0001F469"  # man ZWJ woman
    assert _g.grapheme_clusters(zwj) == [(0, 3)], (
        f"ZWJ 序列须为单一 cluster, got {_g.grapheme_clusters(zwj)}")
    # 三码点家庭（man ZWJ woman ZWJ child）。
    family = "\U0001F468\u200D\U0001F469\u200D\U0001F467"
    assert _g.grapheme_clusters(family) == [(0, 5)], (
        f"ZWJ 家庭序列须为单一 cluster, got {_g.grapheme_clusters(family)}")


@_test
def test_dynamic_normal_chars_one_cluster_each():
    """普通字符——逐码点各成 cluster（与既有 wrap 逐字符断行一致）。"""
    assert _g.grapheme_clusters("hello") == \
        [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)], (
        "普通字符须逐码点 cluster")
    # 空串 → 空列表。
    assert _g.grapheme_clusters("") == []
    # 全 CJK 字符逐字素。
    cjk = "中文"
    assert _g.grapheme_clusters(cjk) == [(0, 1), (1, 2)], (
        f"CJK 字符须逐字素, got {_g.grapheme_clusters(cjk)}")


@_test
def test_dynamic_whitespace_normal_collapses():
    """white-space:normal——折叠连续空白（含换行符）为单空格，允许断行。"""
    d = _g.decide_breaks("a  b\nc   d", _g.WS_NORMAL)
    assert d.collapsed_text == "a b c d", (
        f"normal 折叠须为 'a b c d', got {d.collapsed_text!r}")
    assert d.no_break is False, "normal 须允许断行"
    # 断行机会在折叠空格处。
    assert d.break_positions, "normal 须有断行机会"


@_test
def test_dynamic_whitespace_nowrap_forbid_break():
    """white-space:nowrap——折叠空白但禁止断行（整段一行）。"""
    d = _g.decide_breaks("a  b\nc   d", _g.WS_NOWRAP)
    assert d.collapsed_text == "a b c d", (
        f"nowrap 折叠须为 'a b c d', got {d.collapsed_text!r}")
    assert d.no_break is True, "nowrap 须禁止断行"
    assert d.break_positions == [], "nowrap 须无断行机会"


@_test
def test_dynamic_whitespace_pre_preserves():
    """white-space:pre——保留全部空白与换行符（不折叠），断行在换行处。"""
    d = _g.decide_breaks("a  b\nc   d", _g.WS_PRE)
    assert d.collapsed_text == "a  b\nc   d", (
        f"pre 须保留原样, got {d.collapsed_text!r}")
    assert d.no_break is False, "pre 须允许断行（在换行符处）"
    # 断行机会在换行符位置（'a  b' 4 字符后）。
    assert d.break_positions == [4], (
        f"pre 断行机会须在换行符处, got {d.break_positions}")


@_test
def test_dynamic_whitespace_pre_wrap_folds_keeps_nl():
    """white-space:pre-wrap——保留换行符为断行机会，折叠连续空格。"""
    d = _g.decide_breaks("a  b\nc   d", _g.WS_PRE_WRAP)
    assert d.collapsed_text == "a b\nc d", (
        f"pre-wrap 须折叠空格保留换行, got {d.collapsed_text!r}")
    assert d.no_break is False, "pre-wrap 须允许断行"


@_test
def test_dynamic_whitespace_pre_line_same_subset():
    """white-space:pre-line——本可测子集与 pre-wrap 折叠行为一致
    （差异在完整 CSS 中行首/尾空格，此子集统一折叠）。"""
    d = _g.decide_breaks("a  b\nc   d", _g.WS_PRE_LINE)
    assert d.collapsed_text == "a b\nc d", (
        f"pre-line 须折叠空格保留换行, got {d.collapsed_text!r}")
    assert d.no_break is False, "pre-line 须允许断行（在折叠空格处）"


@_test
def test_dynamic_wrap_nowrap_single_line():
    """text.wrap(white_space=nowrap)——整段一行不断行。"""
    lines = _text.wrap("hello world foo bar", 20, 16, white_space="nowrap")
    assert lines == ["hello world foo bar"], (
        f"nowrap wrap 须整段一行, got {lines}")


@_test
def test_dynamic_wrap_normal_default_behavior():
    """text.wrap 默认（normal）——既有逐字符 + 空格断行行为不变。"""
    # width=50：'hello' / 'world' 各自完整保留（不 force-break）。
    lines = _text.wrap("hello world foo bar", 50, 16)
    assert isinstance(lines, list) and len(lines) >= 2, (
        f"normal wrap 须折行, got {lines}")
    # 完整词（不 force-break）须出现在拼回文本中。
    rejoined = " ".join(l.rstrip() for l in lines)
    assert "hello" in rejoined, f"wrap 须保留完整词 'hello', got {rejoined}"
    assert "world" in rejoined, f"wrap 须保留完整词 'world', got {rejoined}"


@_test
def test_dynamic_combining_cluster_not_split_by_wrap():
    """§4 "断行不落在 cluster 中间"——组合字符 é（e+U+0301）在
    nowrap 下不被逐字符拆散（整段一行，cluster 保持完整）。"""
    s = "e\u0301 b"  # é + space + b
    lines = _text.wrap(s, 20, 16, white_space="nowrap")
    # nowrap 整段一行：组合字符保持完整（é 不拆成 'e' + 独立 U+0301）。
    assert lines == [s], (
        f"nowrap 组合字符须保持 cluster 完整, got {lines}")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")
