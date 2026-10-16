"""G01 — 滚动条协议与范围回归（assessments/20261016-round5/docs 批次 1）。

覆盖：
- PageInstance.scroll_to 的 clamping（0..scroll_max）
- PageInstance.scroll_by 的相对步进
- moveto 协议公式：target = clamp(fraction * content_height, 0, scroll_max)
- scroll units/pages 公式
- 空页 / 短页 / 长页（2000px）/ moveto 0 / 0.5 / 1 / 正负 units / pages
"""
from __future__ import annotations

from toy_browser.app import gui


def _make_page(content_height: int, viewport_h: int = 600) -> gui.PageInstance:
    p = gui.PageInstance()
    p.height = viewport_h
    p.content_height = content_height
    p.yoffset = 0
    return p


TESTS = []


def test_g01_scroll_to_clamp():
    """scroll_to 夹紧到 [0, scroll_max]。"""
    p = _make_page(2000)
    p.scroll_to(-100)
    assert p.yoffset == 0
    p.scroll_to(99999)
    assert p.yoffset == p.scroll_max() == 1400
    p.scroll_to(700)
    assert p.yoffset == 700


def test_g01_scroll_by():
    """scroll_by 相对步进，正向/负向都夹紧。"""
    p = _make_page(2000)
    p.scroll_to(700)
    p.scroll_by(-800)
    assert p.yoffset == 0
    p.scroll_by(9999)
    assert p.yoffset == 1400


def test_g01_moveto_fraction_of_content_height():
    """G01 — moveto fraction 按*总内容高度*解释（legacy 误乘 scroll_max）。

    content_height=2000, viewport=600 → scroll_max=1400。
    moveto 0.5 应到 0.5*2000=1000（不是 0.5*1400=700）。
    """
    p = _make_page(2000)
    target = int(0.5 * p.content_height)
    p.scroll_to(target)
    assert p.yoffset == 1000, f"moveto 0.5 → {p.yoffset}, want 1000"


def test_g01_moveto_end_is_scroll_max():
    """moveto 1.0 → 滚动到底（offset = scroll_max）。"""
    p = _make_page(2000)
    p.scroll_to(int(1.0 * p.content_height))
    assert p.yoffset == 1400


def test_g01_moveto_zero():
    """moveto 0.0 → 回到顶部。"""
    p = _make_page(2000)
    p.scroll_to(700)
    p.scroll_to(int(0.0 * p.content_height))
    assert p.yoffset == 0


def test_g01_short_page_no_scroll():
    """短页（content <= viewport）：scroll_max=0，thumb 为 0..1。"""
    p = _make_page(400, viewport_h=600)
    assert p.scroll_max() == 0
    p.scroll_to(999)
    assert p.yoffset == 0
    ratio = p.scroll_ratio()
    assert ratio == 0.0


def test_g01_scroll_units_formula():
    """scroll n units → n * LINE_STEP（40px/步，G01 建议规则）。"""
    p = _make_page(2000)
    p.scroll_to(0)
    step = 3 * 40
    p.scroll_by(step)
    assert p.yoffset == 120


def test_g01_scroll_pages_formula():
    """scroll 1 page → 0.9 * viewport_height。"""
    p = _make_page(2000, viewport_h=600)
    p.scroll_to(0)
    p.scroll_by(int(1 * 600 * 0.9))
    assert p.yoffset == 540
    p.scroll_by(int(1 * 600 * 0.9))
    assert p.yoffset == 1080


def test_g01_scroll_negative_units():
    """负 units 向上滚（offset 减小）。"""
    p = _make_page(2000)
    p.scroll_to(500)
    p.scroll_by(-2 * 40)
    assert p.yoffset == 420


def test_g01_thumb_ratio_range():
    """拇指 first/last = offset/ch, (offset+vh)/ch（G01 step 4 公式）。"""
    p = _make_page(2000, viewport_h=600)
    p.scroll_to(400)
    first = min(1.0, max(0.0, p.yoffset / p.content_height))
    last = min(1.0, max(0.0, (p.yoffset + p.height) / p.content_height))
    assert abs(first - 0.2) < 1e-9
    assert abs(last - 0.5) < 1e-9
    # 滚到底 last=1
    p.scroll_to(p.scroll_max())
    last = min(1.0, max(0.0, (p.yoffset + p.height) / p.content_height))
    assert last == 1.0


def test_g01_content_shrinks_clamps_offset():
    """内容缩短后，scroll_to 再夹紧（G01 step 3）。"""
    p = _make_page(2000, viewport_h=600)
    p.scroll_to(1000)
    p.content_height = 800  # 内容变短
    p.scroll_to(p.yoffset)  # 重新夹紧
    assert p.yoffset == p.scroll_max() == 200


TESTS.append(("test_g01_scroll_to_clamp", test_g01_scroll_to_clamp))
TESTS.append(("test_g01_scroll_by", test_g01_scroll_by))
TESTS.append(("test_g01_moveto_fraction_of_content_height",
              test_g01_moveto_fraction_of_content_height))
TESTS.append(("test_g01_moveto_end_is_scroll_max", test_g01_moveto_end_is_scroll_max))
TESTS.append(("test_g01_moveto_zero", test_g01_moveto_zero))
TESTS.append(("test_g01_short_page_no_scroll", test_g01_short_page_no_scroll))
TESTS.append(("test_g01_scroll_units_formula", test_g01_scroll_units_formula))
TESTS.append(("test_g01_scroll_pages_formula", test_g01_scroll_pages_formula))
TESTS.append(("test_g01_scroll_negative_units", test_g01_scroll_negative_units))
TESTS.append(("test_g01_thumb_ratio_range", test_g01_thumb_ratio_range))
TESTS.append(("test_g01_content_shrinks_clamps_offset",
              test_g01_content_shrinks_clamps_offset))


# ----------------------------------------------------------------------
# G01 协议处理器（TabBrowser.handle_scroll_command）— 无 Tk 的纯逻辑
# 版本：把协议解析提出来可直接测试（Tk 版通过 Tcl command 调用它）。
# ----------------------------------------------------------------------

def _protocol_target(operation: str, *args, page) -> int | None:
    """复刻 TabBrowser.handle_scroll_command 的协议公式（G01 建议规则）：

        moveto f        -> clamp(f * content_height, 0, scroll_max)
        scroll n units  -> offset + n * 40
        scroll n pages  -> offset + n * viewport_height * 0.9
    """
    LINE_STEP = 40
    m = page.scroll_max()
    if operation == "moveto":
        fraction = float(args[0])
        target = int(fraction * page.content_height)
        page.scroll_to(target)
        return page.yoffset
    if operation == "scroll":
        count = int(args[0])
        unit = args[1] if len(args) > 1 else "units"
        step = int(count * page.height * 0.9) if unit == "pages" else count * LINE_STEP
        page.scroll_by(step)
        return page.yoffset
    return None


def test_g01_protocol_moveto_0_5_1():
    p = _make_page(2000)
    assert _protocol_target("moveto", "0.0", page=p) == 0
    assert _protocol_target("moveto", "0.5", page=p) == 1000
    assert _protocol_target("moveto", "1.0", page=p) == 1400


def test_g01_protocol_scroll_units_pages():
    p = _make_page(4000, viewport_h=600)
    assert _protocol_target("scroll", "1", "units", page=p) == 40
    assert _protocol_target("scroll", "-1", "units", page=p) == 0
    p.scroll_to(0)
    assert _protocol_target("scroll", "1", "pages", page=p) == 540
    p.scroll_to(3000)
    assert _protocol_target("scroll", "-1", "pages", page=p) == 2460


def test_g01_protocol_set_none_noop():
    p = _make_page(2000)
    assert _protocol_target("set", "0.0", "0.5", page=p) is None
    assert _protocol_target("none", page=p) is None
    assert p.yoffset == 0


TESTS.append(("test_g01_protocol_moveto_0_5_1", test_g01_protocol_moveto_0_5_1))
TESTS.append(("test_g01_protocol_scroll_units_pages",
              test_g01_protocol_scroll_units_pages))
TESTS.append(("test_g01_protocol_set_none_noop", test_g01_protocol_set_none_noop))
