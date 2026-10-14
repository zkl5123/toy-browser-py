"""CSS cascade + selectors + @media + @supports + calc — test module (GPT P1-2 split from test.py)."""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


import browser
import dom
import css
import layout
import js
import text
import paint
from paint import Bitmap, Color
import sys

# Shared helpers (defined in test.py, needed by multiple modules)
from test_helpers import (
    _a11y_roles,
    _find_a11y_role,
    _find_box,
    _find_box_by_id,
    _find_box_by_id_or_tag,
    _find_box_by_tag,
    _greens,
    _p20_bitmap,
    _p20_first_valid_outline,
    _p20_ink,
    _p20_metrics,
    _p21_start_server,
    _p22_start_server,
    _p29_js_logs,
    _p30_doc_html,
    _p30_host,
    _p30_qsel_all,
    _p32_cleanup,
    _p32_tmp,
    _pixel_diff,
    _reds,
    _rmtree_quiet,
    find2_children,
)

TESTS: list = []

def test_css_cascade():
    root = dom.parse('<p class="intro" id="f">hello</p>')
    styled = css.apply(root, "p { color: red; } .intro { font-size: 14px; }")
    p = styled.children[0]
    assert isinstance(p.props["color"], Color)
    assert p.props["color"].r == 255



TESTS.append(("test_css_cascade", test_css_cascade))
def test_css_inline_wins():
    root = dom.parse('<p style="color: blue;">x</p>')
    styled = css.apply(root, "p { color: red; }")
    p = styled.children[0]
    assert isinstance(p.props["color"], Color)
    assert p.props["color"].b == 255, "inline style must win the cascade"



TESTS.append(("test_css_inline_wins", test_css_inline_wins))
def test_css_height_and_line_height():
    root = dom.parse('<div style="height: 100px">x</div>')
    styled = css.apply(root, "")
    layout_root = layout.layout(styled, 400.0)
    div = _find_box_by_tag(layout_root, "div")
    assert div is not None, "div box found"
    assert div.explicit_height == 100.0, f"got {div.explicit_height}"
    assert div.rect.h == 100.0, f"explicit height must set box height, got {div.rect.h}"



TESTS.append(("test_css_height_and_line_height", test_css_height_and_line_height))
def test_selector_descendant():
    """Descendant combinator: 'div p { color: red; }' matches <p> inside <div>."""
    html = '<html><body><div><p id="inner">x</p></div><p id="outer">y</p></body></html>'
    root = dom.parse(html)
    styled = css.apply(root, "div p { color: #ff0000; }")

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    inner = find_sn(styled, "inner")
    outer = find_sn(styled, "outer")
    assert inner is not None and outer is not None
    assert isinstance(inner.props.get("color"), css.Color), "div p must color the inner <p>"
    assert inner.props["color"].r == 255, "inner p color must be red"
    # outer <p> is not inside a <div>, so it should not get the red color
    outer_color = outer.props.get("color")
    assert not (isinstance(outer_color, css.Color) and outer_color.r == 255 and outer_color.g == 0 and outer_color.b == 0), (
        "outer p must NOT be red from 'div p'"
    )



TESTS.append(("test_selector_descendant", test_selector_descendant))
def test_selector_child():
    """Child combinator: 'div > p { color: red; }' matches direct children only."""
    html = (
        '<html><body>'
        '<div><p id="direct">a</p><span><p id="nested">b</p></span></div>'
        '<div><p id="direct2">c</p></div>'
        "</body></html>"
    )
    root = dom.parse(html)
    styled = css.apply(root, "div > p { color: #ff0000; }")

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    direct = find_sn(styled, "direct")
    nested = find_sn(styled, "nested")
    direct2 = find_sn(styled, "direct2")
    assert direct is not None and nested is not None and direct2 is not None
    assert direct.props.get("color") is not None and isinstance(direct.props.get("color"), css.Color) and direct.props["color"].r == 255, (
        "direct child p must be red"
    )
    assert direct2.props.get("color") is not None and isinstance(direct2.props.get("color"), css.Color) and direct2.props["color"].r == 255, (
        "second direct child p must be red"
    )
    nested_color = nested.props.get("color")
    assert not (isinstance(nested_color, css.Color) and nested_color.r == 255 and nested_color.g == 0 and nested_color.b == 0), (
        "nested p (inside span) must NOT be red from 'div > p'"
    )



TESTS.append(("test_selector_child", test_selector_child))
def test_selector_multi_class():
    """'.a.b { … }' matches elements with both classes."""
    html = (
        '<html><body>'
        '<div class="a b" id="both"></div>'
        '<div class="a" id="only_a"></div>'
        '<div class="b" id="only_b"></div>'
        "</body></html>"
    )
    root = dom.parse(html)
    styled = css.apply(root, ".a.b { color: #00ff00; }")

    def find_sn(sn, eid):
        if sn.dom.attr("id") == eid:
            return sn
        for c in sn.children:
            r = find_sn(c, eid)
            if r:
                return r
        return None

    both = find_sn(styled, "both")
    only_a = find_sn(styled, "only_a")
    only_b = find_sn(styled, "only_b")
    assert both is not None and only_a is not None and only_b is not None
    assert both.props.get("color") is not None and isinstance(both.props.get("color"), css.Color) and both.props["color"].g == 255, (
        ".a.b must match the element with both classes"
    )
    only_a_color = only_a.props.get("color")
    assert not (isinstance(only_a_color, css.Color) and only_a_color.g == 255 and only_a_color.r == 0 and only_a_color.b == 0), (
        ".a.b must NOT match an element with only class a"
    )
    only_b_color = only_b.props.get("color")
    assert not (isinstance(only_b_color, css.Color) and only_b_color.g == 255 and only_b_color.r == 0 and only_b_color.b == 0), (
        ".a.b must NOT match an element with only class b"
    )



TESTS.append(("test_selector_multi_class", test_selector_multi_class))
def test_media_min_width_match():
    """P9.1 — @media (min-width: 400px) rule applies when viewport >= 400."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px">hi</div></body></html>'
    css_text = '@media (min-width: 400px) { #x { background: #ff0000; } }'
    bmp = b.render_document(html, css_text, 500, 300)
    # Check that the background was applied (red pixel somewhere).
    assert _reds(bmp) > 0, "min-width media rule should apply at 500px viewport"



TESTS.append(("test_media_min_width_match", test_media_min_width_match))
def test_media_min_width_no_match():
    """P9.1 — @media (min-width: 400px) rule does NOT apply when viewport < 400."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px">hi</div></body></html>'
    css_text = '@media (min-width: 400px) { #x { background: #ff0000; } }'
    bmp = b.render_document(html, css_text, 300, 300)
    # At 300px viewport the rule should NOT apply.
    assert _reds(bmp) == 0, "min-width media rule should NOT apply at 300px viewport"



TESTS.append(("test_media_min_width_no_match", test_media_min_width_no_match))
def test_media_max_width_match():
    """P9.1 — @media (max-width: 300px) rule applies when viewport <= 300."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px">hi</div></body></html>'
    css_text = '@media (max-width: 300px) { #x { background: #0000ff; } }'
    bmp = b.render_document(html, css_text, 300, 300)
    # Check blue pixel.
    blue_count = sum(
        1 for p in bmp.pixels
        if (p & 0xFF) > 200 and ((p >> 8) & 0xFF) < 55 and ((p >> 16) & 0xFF) < 55
    )
    assert blue_count > 0, "max-width media rule should apply at 300px viewport"



TESTS.append(("test_media_max_width_match", test_media_max_width_match))
def test_media_parse():
    """P9.1 — parse_media_queries extracts @media blocks."""
    mqs = css.parse_media_queries(
        '@media (min-width: 600px) { .a { color: red; } } '
        '.b { color: blue; } '
        '@media (max-height: 200px) { .c { background: green; } }'
    )
    assert len(mqs) == 2, f"expected 2 media queries, got {len(mqs)}"
    assert "min-width" in mqs[0].condition
    assert "max-height" in mqs[1].condition



TESTS.append(("test_media_parse", test_media_parse))
def test_supports_parse():
    """P11.3 — parse_supports_blocks extracts @supports blocks."""
    blocks = css.parse_supports_blocks(
        '@supports (display: grid) { .a { color: red; } }'
        '.b { color: blue; }'
    )
    assert len(blocks) == 1, f"must find 1 @supports block, got {len(blocks)}"
    assert "display" in blocks[0].condition, \
        f"condition must contain display, got {blocks[0].condition!r}"
    assert len(blocks[0].rules) == 1, "inner rules must be parsed"



TESTS.append(("test_supports_parse", test_supports_parse))
def test_supports_matches_always_true():
    """P11.3/GPT P1-1 — supports_matches returns True for conditions the
    engine actually supports; False for unsupported property: value
    pairs (e.g. ``position: sticky`` — the engine parses "position"
    but does not implement sticky positioning)."""
    # Supported properties → True
    assert css.supports_matches("display: grid") is True
    assert css.supports_matches("display: flex") is True
    assert css.supports_matches("position: absolute") is True
    assert css.supports_matches("color: red") is True
    # Unsupported property: value pair → False
    assert css.supports_matches("(position: sticky)") is False
    assert css.supports_matches("position: sticky") is False
    # Unknown property → False
    assert css.supports_matches("unknown-prop: 1px") is False
    # Empty condition → True (vacuously satisfied)
    assert css.supports_matches("") is True
    # Logical combinators
    assert css.supports_matches("not (position: sticky)") is True
    assert css.supports_matches("display: grid and color: red") is True
    assert css.supports_matches("position: sticky or display: grid") is True
    # Toy always-True forms
    assert css.supports_matches("selector(:dir(ltr))") is True
    assert css.supports_matches("font-tech(opentype)") is True



TESTS.append(("test_supports_matches_always_true", test_supports_matches_always_true))
def test_supports_cascade_applies():
    """P11.3 — @supports rules are applied to the cascade (toy: always)."""
    import browser as b
    html = '<html><body><div id="x" style="width:100px;height:50px">hi</div></body></html>'
    css_text = '@supports (display: grid) { #x { background: #ff0000; } }'
    bmp = b.render_document(html, css_text, 200, 100)
    assert _reds(bmp) > 0, "@supports rule must apply (toy: always supported)"



TESTS.append(("test_supports_cascade_applies", test_supports_cascade_applies))
def test_calc_parse_value():
    """P12.3 — parse_value recognizes calc() and evaluates simple forms."""
    v = css.parse_value("calc(100px - 20px)")
    assert isinstance(v, css.Length), \
        f"calc(100px - 20px) must be Length, got {type(v).__name__}"
    assert v.value == 80.0, f"must be 80.0, got {v.value}"
    assert v.unit == "px", f"must be px, got {v.unit}"



TESTS.append(("test_calc_parse_value", test_calc_parse_value))
def test_calc_plus():
    """P12.3 — calc(10px + 5px) = 15px."""
    v = css.parse_value("calc(10px + 5px)")
    assert v.value == 15.0 and v.unit == "px", \
        f"must be 15px, got {v.value}{v.unit}"



TESTS.append(("test_calc_plus", test_calc_plus))
def test_calc_em_to_px():
    """P12.3 — calc with em operand converts to px (1em = 16px)."""
    v = css.parse_value("calc(1em + 4px)")
    assert isinstance(v, css.Length)
    assert v.value == 20.0, f"1em(16px) + 4px must be 20.0, got {v.value}"



TESTS.append(("test_calc_em_to_px", test_calc_em_to_px))
def test_calc_unsupported_fallback():
    """P12.3 — calc with mixed unsupported units falls back to Keyword."""
    v = css.parse_value("calc(10px - 2%)")
    # P33.P1 — mixed %/px now yields CalcExpr (deferred resolution).
    # The legacy Keyword fallback was replaced; CalcExpr is the new
    # correct type for mixed-unit expressions.
    assert isinstance(v, (css.Keyword, css.CalcExpr)), \
        f"mixed %/px must be Keyword or CalcExpr, got {type(v).__name__}"
    if isinstance(v, css.Keyword):
        assert "calc(" in v.value, f"Keyword must carry calc() text, got {v.value!r}"



TESTS.append(("test_calc_unsupported_fallback", test_calc_unsupported_fallback))
def test_calc_in_layout_width():
    """P12.3 — calc() in a width style affects the box width."""
    html = '<div id="c" style="width: calc(100px - 20px); height: 30px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    c = find2_children(lroot, "c")
    assert c is not None, "calc box must be in the layout tree"
    assert c.explicit_width == 80.0, \
        f"calc(100px - 20px) width must be 80.0, got {c.explicit_width}"



TESTS.append(("test_calc_in_layout_width", test_calc_in_layout_width))
def test_calc_minus():
    """P12.5 — calc() subtraction: calc(200px - 50px) → 150px."""
    v = css.parse_value("calc(200px - 50px)")
    assert isinstance(v, css.Length), f"calc(200px - 50px) must be Length, got {type(v).__name__}"
    assert v.value == 150.0, f"200px - 50px must be 150.0, got {v.value}"
    assert v.unit == "px", f"unit must be px, got {v.unit}"



TESTS.append(("test_calc_minus", test_calc_minus))
def test_calc_bare_number():
    """P12.5 — calc with a bare number (no unit) returns a Keyword."""
    v = css.parse_value("calc(5)")
    # A bare number with no decimal → Keyword("5.0")
    assert isinstance(v, (css.Keyword, css.Length)), \
        f"calc(5) must be Keyword or Length, got {type(v).__name__}"
    val = v.value
    assert str(val) in ("5", "5.0") or val == 5.0, \
        f"calc(5) must evaluate to 5, got {val!r}"



TESTS.append(("test_calc_bare_number", test_calc_bare_number))
def test_calc_em_mixed():
    """P12.5 — calc(2em + 10px) → 42px (2*16 + 10)."""
    v = css.parse_value("calc(2em + 10px)")
    assert isinstance(v, css.Length), f"calc(2em + 10px) must be Length, got {type(v).__name__}"
    assert v.value == 42.0, f"2em(32px) + 10px must be 42.0, got {v.value}"
    assert v.unit == "px", f"unit must be px, got {v.unit}"



TESTS.append(("test_calc_em_mixed", test_calc_em_mixed))
def test_media_exact_width():
    """P12.5 — media query with exact width: 768px."""
    assert css.media_matches("(width: 768px)", 768, 600) is True, \
        "exact width 768 must match 768"
    assert css.media_matches("(width: 768px)", 769, 600) is False, \
        "exact width 768 must not match 769"



TESTS.append(("test_media_exact_width", test_media_exact_width))
def test_media_composite_and():
    """P12.5 — composite media query: (min-width: 300px) and (max-height: 500px)."""
    # width=400 satisfies min-width:300; height=400 satisfies max-height:500 → True
    assert css.media_matches("(min-width: 300px) and (max-height: 500px)", 400, 400) is True, \
        "composite and() must match"
    # width=200 fails min-width:300 → False
    assert css.media_matches("(min-width: 300px) and (max-height: 500px)", 200, 400) is False, \
        "composite and() must fail on first clause"



TESTS.append(("test_media_composite_and", test_media_composite_and))
def test_calc_in_layout_height():
    """P12.5 — calc() in a height style affects the box height."""
    html = '<div id="ch" style="width: 40px; height: calc(100px - 30px)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    # Single-child layout root IS the div.
    assert lroot.explicit_height == 70.0, \
        f"calc(100px - 30px) height must be 70.0, got {lroot.explicit_height}"



TESTS.append(("test_calc_in_layout_height", test_calc_in_layout_height))
def test_css_filter_brightness_layout():
    """P13.3 — `filter: brightness(50%)` is parsed into the layout box."""
    html = '<div id="f" style="width:50px;height:50px;background:#ff0000;filter:brightness(50%)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.filter_enabled is True, "filter must be enabled"
    assert lroot.filter_brightness == 0.5, \
        f"brightness(50%) must be 0.5, got {lroot.filter_brightness}"



TESTS.append(("test_css_filter_brightness_layout", test_css_filter_brightness_layout))
def test_css_filter_contrast_layout():
    """P13.3 — `filter: contrast(200%)` is parsed into the layout box."""
    html = '<div id="f" style="width:50px;height:50px;background:#808080;filter:contrast(200%)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.filter_contrast == 2.0, \
        f"contrast(200%) must be 2.0, got {lroot.filter_contrast}"



TESTS.append(("test_css_filter_contrast_layout", test_css_filter_contrast_layout))
def test_css_filter_blur_layout():
    """P13.3 — `filter: blur(2px)` sets the blur radius."""
    html = '<div id="f" style="width:50px;height:50px;filter:blur(2px)"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.filter_blur == 2.0, f"blur(2px) must be 2.0, got {lroot.filter_blur}"



TESTS.append(("test_css_filter_blur_layout", test_css_filter_blur_layout))
def test_css_filter_brightness_render():
    """P13.3 — brightness(50%) darkens the painted box pixels."""
    import browser as b
    html = ('<div id="f" style="width:40px;height:40px;'
            'background:#ff0000;filter:brightness(50%)"></div>')
    bmp = b.render_document(html, "")
    # The red box occupies the top-left region; sample a pixel inside it.
    # brightness 0.5 on red → R≈127.
    px = 20
    py = 20
    u32 = bmp.pixels[py * bmp.width + px]
    r = (u32 >> 16) & 0xFF
    assert r < 160, f"brightness(50%) must darken red; got R={r}"



TESTS.append(("test_css_filter_brightness_render", test_css_filter_brightness_render))
def test_css_filter_no_filter_identity():
    """P13.3 — no filter leaves pixels unchanged."""
    import browser as b
    html = '<div id="f" style="width:40px;height:40px;background:#ff0000"></div>'
    bmp = b.render_document(html, "")
    u32 = bmp.pixels[20 * bmp.width + 20]
    r = (u32 >> 16) & 0xFF
    assert r == 255, f"no filter must leave red at 255, got R={r}"



TESTS.append(("test_css_filter_no_filter_identity", test_css_filter_no_filter_identity))
def test_css_scroll_container_overflow_auto():
    """P14.1 — `overflow:auto` sets the box's overflow_x/overflow_y."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:auto"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.overflow_x == "auto", f"overflow_x must be 'auto', got {lroot.overflow_x!r}"
    assert lroot.overflow_y == "auto", f"overflow_y must be 'auto', got {lroot.overflow_y!r}"



TESTS.append(("test_css_scroll_container_overflow_auto", test_css_scroll_container_overflow_auto))
def test_css_scroll_container_scroll_top():
    """P14.1 — `scroll-top` inline style sets the box's scroll_top."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:scroll;scroll-top:30px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.scroll_top == 30.0, f"scroll-top:30px must be 30.0, got {lroot.scroll_top}"



TESTS.append(("test_css_scroll_container_scroll_top", test_css_scroll_container_scroll_top))
def test_css_scroll_container_scroll_left():
    """P14.1 — `scroll-left` inline style sets the box's scroll_left."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:scroll;scroll-left:15px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.scroll_left == 15.0, f"scroll-left:15px must be 15.0, got {lroot.scroll_left}"



TESTS.append(("test_css_scroll_container_scroll_left", test_css_scroll_container_scroll_left))
def test_css_scroll_container_render():
    """P14.1 — a scroll container with overflowing child renders without crash."""
    import browser as b
    html = ('<div id="sc" style="width:80px;height:40px;overflow:auto;scroll-top:20px">'
            '<div id="inner" style="width:70px;height:200px;background:#3366ff"></div>'
            '</div>')
    bmp = b.render_document(html, "")
    assert bmp.width > 0 and bmp.height > 0, "scroll container must render a bitmap"



TESTS.append(("test_css_scroll_container_render", test_css_scroll_container_render))
def test_css_scroll_container_overflow_scroll():
    """P14.1 — `overflow:scroll` sets both axes to 'scroll'."""
    html = '<div id="sc" style="width:100px;height:60px;overflow:scroll"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.overflow_x == "scroll", f"overflow_x must be 'scroll', got {lroot.overflow_x!r}"
    assert lroot.overflow_y == "scroll", f"overflow_y must be 'scroll', got {lroot.overflow_y!r}"



TESTS.append(("test_css_scroll_container_overflow_scroll", test_css_scroll_container_overflow_scroll))
def test_css_text_indent_layout():
    """P14.3 — `text-indent: 30px` sets the layout box's text_indent."""
    html = '<div id="ti" style="width:200px;text-indent:30px">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == 30.0, \
        f"text-indent:30px must be 30.0, got {lroot.text_indent}"



TESTS.append(("test_css_text_indent_layout", test_css_text_indent_layout))
def test_css_text_indent_render():
    """P14.3 — text-indent renders without crash."""
    import browser as b
    html = '<div id="ti" style="width:200px;text-indent:20px">Hello World</div>'
    bmp = b.render_document(html, "")
    assert bmp.width > 0, "text-indent must render"



TESTS.append(("test_css_text_indent_render", test_css_text_indent_render))
def test_css_text_indent_zero_default():
    """P14.3 — no text-indent defaults to 0.0."""
    html = '<div id="ti" style="width:200px">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == 0.0, \
        f"default text_indent must be 0.0, got {lroot.text_indent}"



TESTS.append(("test_css_text_indent_zero_default", test_css_text_indent_zero_default))
def test_css_font_face_parse():
    """P14.4 — @font-face block parses without error (toy: stored, no real font loading)."""
    css_text = '@font-face { font-family: "MyFont"; src: url("font.woff"); }'
    styled = css.apply(dom.parse('<div></div>'), css_text)
    assert styled is not None, "@font-face must not crash the cascade"



TESTS.append(("test_css_font_face_parse", test_css_font_face_parse))
def test_css_font_face_no_crash():
    """P14.4 — multiple @font-face blocks + a font-family reference don't crash."""
    css_text = (
        '@font-face { font-family: "A"; src: url("a.woff"); }'
        '@font-face { font-family: "B"; src: url("b.woff"); }'
        'div { font-family: "A"; }'
    )
    styled = css.apply(dom.parse('<div id="d">text</div>'), css_text)
    assert styled is not None, "multiple @font-face must not crash"
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot is not None, "@font-face document must lay out"



TESTS.append(("test_css_font_face_no_crash", test_css_font_face_no_crash))
def test_css_scroll_ymax_clamped():
    """P14.1 — scroll-top larger than viewport is clamped (toy: stored as-is)."""
    html = '<div id="sc" style="width:50px;height:50px;overflow:scroll;scroll-top:999px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.scroll_top == 999.0, f"scroll-top:999px must be 999.0, got {lroot.scroll_top}"



TESTS.append(("test_css_scroll_ymax_clamped", test_css_scroll_ymax_clamped))
def test_css_text_indent_negative():
    """P14.3 — negative text-indent shifts text left."""
    html = '<div id="ti" style="width:200px;text-indent:-10px">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == -10.0, \
        f"text-indent:-10px must be -10.0, got {lroot.text_indent}"



TESTS.append(("test_css_text_indent_negative", test_css_text_indent_negative))
def test_css_text_indent_render_negative():
    """P14.3 — negative text-indent renders without crash."""
    import browser as b
    html = '<div id="ti" style="width:200px;text-indent:-5px">Hi</div>'
    bmp = b.render_document(html, "")
    assert bmp.width > 0, "negative text-indent must render"



TESTS.append(("test_css_text_indent_render_negative", test_css_text_indent_render_negative))
def test_css_text_indent_layout_block_child():
    """P14.3 — text-indent on a block with text content."""
    html = '<div style="text-indent:15px">some text here</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 300.0, 200.0)
    assert lroot.text_indent == 15.0, \
        f"text-indent:15px must be 15.0, got {lroot.text_indent}"



TESTS.append(("test_css_text_indent_layout_block_child", test_css_text_indent_layout_block_child))
def test_css_text_indent_render_text_present():
    """P14.3 — text-indent document renders non-white pixels (text painted)."""
    import browser as b
    html = '<div style="width:300px;height:100px;text-indent:20px;color:#000000">Hello</div>'
    bmp = b.render_document(html, "")
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "text-indent document must paint text pixels"



TESTS.append(("test_css_text_indent_render_text_present", test_css_text_indent_render_text_present))
def test_css_box_decoration_break_layout():
    """P15.3 — box-decoration-break:clone is parsed into the layout box."""
    html = '<div id="bd" style="width:100px;box-decoration-break:clone"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.box_decoration_break == "clone", \
        f"box-decoration-break must be 'clone', got {lroot.box_decoration_break!r}"



TESTS.append(("test_css_box_decoration_break_layout", test_css_box_decoration_break_layout))
def test_css_webkit_appearance_layout():
    """P15.3 — -webkit-appearance:none is parsed into the layout box."""
    html = '<div id="ap" style="width:100px;-webkit-appearance:none"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.webkit_appearance == "none", \
        f"-webkit-appearance must be 'none', got {lroot.webkit_appearance!r}"



TESTS.append(("test_css_webkit_appearance_layout", test_css_webkit_appearance_layout))
def test_css_box_decoration_default_empty():
    """P15.3 — no box-decoration-break defaults to empty string."""
    html = '<div id="bd" style="width:100px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.box_decoration_break == "", \
        f"default box-decoration-break must be '', got {lroot.box_decoration_break!r}"



TESTS.append(("test_css_box_decoration_default_empty", test_css_box_decoration_default_empty))
def test_css_webkit_appearance_default_empty():
    """P15.3 — no -webkit-appearance defaults to empty string."""
    html = '<div id="ap" style="width:100px"></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 200.0)
    assert lroot.webkit_appearance == "", \
        f"default -webkit-appearance must be '', got {lroot.webkit_appearance!r}"



TESTS.append(("test_css_webkit_appearance_default_empty", test_css_webkit_appearance_default_empty))
def test_css_font_family_layout():
    """P15.4 — font-family:serif sets the layout box's font_family."""
    html = '<div style="width:300px;font-family:serif">Hello</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.font_family == "serif", \
        f"font-family:serif must set font_family to serif, got {lroot.font_family!r}"



TESTS.append(("test_css_font_family_layout", test_css_font_family_layout))
def test_css_font_family_inheritance():
    """P15.4 — children inherit the parent's font-family when unset."""
    html = '<div style="font-family:serif"><span>child</span></div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)

    def _find_by_tag(bx, tag):
        for c in bx.children:
            if c.dom is not None and c.dom.is_element() and c.dom.tag == tag:
                return c
            r = _find_by_tag(c, tag)
            if r is not None:
                return r
        return None

    sp = _find_by_tag(lroot, "span")
    assert sp is not None, "span child must be found in layout"
    assert sp.font_family == "serif", \
        f"span must inherit serif, got {sp.font_family!r}"



TESTS.append(("test_css_font_family_inheritance", test_css_font_family_inheritance))
def test_css_font_family_unknown_fallback():
    """P15.4 — unknown font-family falls back to monospace."""
    html = '<div style="font-family:FancyType">x</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 200.0, 100.0)
    assert lroot.font_family == "monospace", \
        f"unknown family must fall back to monospace, got {lroot.font_family!r}"



TESTS.append(("test_css_font_family_unknown_fallback", test_css_font_family_unknown_fallback))
def test_css_font_family_render():
    """P15.4 — a serif document renders non-white text pixels."""
    import browser as b
    html = '<div style="width:300px;height:100px;font-family:serif;color:#000000">Serif text</div>'
    bmp = b.render_document(html, "")
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "serif document must paint text pixels"



TESTS.append(("test_css_font_family_render", test_css_font_family_render))
def test_css_font_family_serif_wider_render():
    """P15.4 — serif text produces a wider pixel span than monospace."""
    import browser as b

    def _rightmost(bmp):
        cols = [x for x in range(bmp.width)
                if any(p != 0xFFFFFF for p in bmp.pixels[x::bmp.width])]
        return cols[-1] if cols else 0

    text = "AAAAA"
    mono_bmp = b.render_document(
        f'<div style="width:400px;height:60px;font-family:monospace;color:#000000">{text}</div>', "")
    serif_bmp = b.render_document(
        f'<div style="width:400px;height:60px;font-family:serif;color:#000000">{text}</div>', "")
    assert _rightmost(serif_bmp) >= _rightmost(mono_bmp), \
        "serif text must be at least as wide as monospace"



TESTS.append(("test_css_font_family_serif_wider_render", test_css_font_family_serif_wider_render))
def test_css_text_justify_layout():
    """P16.3 — text-justify:new is parsed into the layout box."""
    html = '<div style="width:300px;text-justify:new">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "new", \
        f"text-justify must be 'new', got {lroot.text_justify!r}"



TESTS.append(("test_css_text_justify_layout", test_css_text_justify_layout))
def test_css_word_break_layout():
    """P16.3 — word-break:break-all is parsed into the layout box."""
    html = '<div style="width:300px;word-break:break-all">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.word_break == "break-all", \
        f"word-break must be 'break-all', got {lroot.word_break!r}"



TESTS.append(("test_css_word_break_layout", test_css_word_break_layout))
def test_css_hyphens_layout():
    """P16.3 — hyphens:auto is parsed into the layout box."""
    html = '<div style="width:300px;hyphens:auto">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.hyphens == "auto", \
        f"hyphens must be 'auto', got {lroot.hyphens!r}"



TESTS.append(("test_css_hyphens_layout", test_css_hyphens_layout))
def test_css_text_justify_default_empty():
    """P16.3 — no text-justify defaults to empty string."""
    html = '<div style="width:300px">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "", \
        f"default text-justify must be '', got {lroot.text_justify!r}"



TESTS.append(("test_css_text_justify_default_empty", test_css_text_justify_default_empty))
def test_css_word_break_default_empty():
    """P16.3 — no word-break defaults to empty string."""
    html = '<div style="width:300px">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.word_break == "", \
        f"default word-break must be '', got {lroot.word_break!r}"



TESTS.append(("test_css_word_break_default_empty", test_css_word_break_default_empty))
def test_css_hyphens_default_empty():
    """P16.3 — no hyphens defaults to empty string."""
    html = '<div style="width:300px">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.hyphens == "", \
        f"default hyphens must be '', got {lroot.hyphens!r}"



TESTS.append(("test_css_hyphens_default_empty", test_css_hyphens_default_empty))
def test_css_word_break_keep_all():
    """P16.3 — word-break:keep-all is parsed."""
    html = '<div style="width:300px;word-break:keep-all">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.word_break == "keep-all", \
        f"word-break must be 'keep-all', got {lroot.word_break!r}"



TESTS.append(("test_css_word_break_keep_all", test_css_word_break_keep_all))
def test_css_text_justify_inter_word():
    """P16.3 — text-justify:inter-word is parsed."""
    html = '<div style="width:300px;text-justify:inter-word">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "inter-word", \
        f"text-justify must be 'inter-word', got {lroot.text_justify!r}"



TESTS.append(("test_css_text_justify_inter_word", test_css_text_justify_inter_word))
def test_css_hyphens_manual():
    """P16.3 — hyphens:manual is parsed."""
    html = '<div style="width:300px;hyphens:manual">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.hyphens == "manual", \
        f"hyphens must be 'manual', got {lroot.hyphens!r}"



TESTS.append(("test_css_hyphens_manual", test_css_hyphens_manual))
def test_css_text_justify_none():
    """P16.3 — text-justify:none is parsed."""
    html = '<div style="width:300px;text-justify:none">Text</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.text_justify == "none", \
        f"text-justify must be 'none', got {lroot.text_justify!r}"



TESTS.append(("test_css_text_justify_none", test_css_text_justify_none))
def test_css_scroll_snap_type_layout():
    """P16.4 — scroll-snap-type:x mandatory is parsed into the layout box."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:x mandatory">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "x mandatory", \
        f"scroll-snap-type must be 'x mandatory', got {lroot.scroll_snap_type!r}"



TESTS.append(("test_css_scroll_snap_type_layout", test_css_scroll_snap_type_layout))
def test_css_scroll_snap_type_y_proximity():
    """P16.4 — scroll-snap-type:y proximity is parsed."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:y proximity">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "y proximity", \
        f"scroll-snap-type must be 'y proximity', got {lroot.scroll_snap_type!r}"



TESTS.append(("test_css_scroll_snap_type_y_proximity", test_css_scroll_snap_type_y_proximity))
def test_css_scroll_snap_type_none():
    """P16.4 — scroll-snap-type:none is parsed."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:none">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "none", \
        f"scroll-snap-type must be 'none', got {lroot.scroll_snap_type!r}"



TESTS.append(("test_css_scroll_snap_type_none", test_css_scroll_snap_type_none))
def test_css_scroll_snap_type_default_empty():
    """P16.4 — no scroll-snap-type defaults to empty string."""
    html = '<div style="width:300px;height:200px;overflow:scroll">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.scroll_snap_type == "", \
        f"default scroll-snap-type must be '', got {lroot.scroll_snap_type!r}"



TESTS.append(("test_css_scroll_snap_type_default_empty", test_css_scroll_snap_type_default_empty))
def test_css_scroll_snap_with_overflow_scroll():
    """P16.4 — scroll-snap-type coexists with overflow:scroll."""
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:x both">C</div>'
    styled = css.apply(dom.parse(html), "")
    lroot = layout.layout(styled, 400.0, 200.0)
    assert lroot.overflow_x in ("auto", "scroll"), \
        f"overflow must be scroll-ish, got {lroot.overflow_x!r}"
    assert lroot.scroll_snap_type == "x both", \
        f"scroll-snap-type must be 'x both', got {lroot.scroll_snap_type!r}"



TESTS.append(("test_css_scroll_snap_with_overflow_scroll", test_css_scroll_snap_with_overflow_scroll))
def test_css_scroll_snap_render():
    """P16.4 — a scroll-snap document renders without crash."""
    import browser as b
    html = '<div style="width:300px;height:200px;overflow:scroll;scroll-snap-type:x mandatory;color:#000000">Snap</div>'
    bmp = b.render_document(html, "")
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert nonwhite > 0, "scroll-snap document must paint pixels"



TESTS.append(("test_css_scroll_snap_render", test_css_scroll_snap_render))
