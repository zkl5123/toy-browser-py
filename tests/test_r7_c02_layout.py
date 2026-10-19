"""R7-C02 — 格式化上下文与布局算法契约。

Round-7 评估（渲染与标准差距审计.md §5 / 逐项改进实施方案.md R7-C02）
列出的最小反例：

* Grid row-gap 重复累计（20 高 + 10 gap，下一行偏移 40 而非 30）；
* 显式 ``grid-column: 2`` 落在第 1 列（x=0 而非 50）；
* Flex wrap + grow 三项同为 300（100 宽容器应为 50/50/100）；
* table ``colspan=2`` 只占一列宽（50 而非 100）；
* InlineRun 混排字号被压成第一个 run 的 12/12（应为 12/24）；
* InlineRun 样式丢失（应为 {'color':'red'} / {'color':'blue'}）；
* 文本 DisplayCommand 不带 font_family（测量/绘制无法追踪 face）。

另含实施方案第 3/4 点的可测试步骤：grid span 宽度、flex 每行独立
grow、table 跨列列宽分摊、rowspan 占位、行内混排分行度量。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout as layout_mod
from toy_browser.rendering import paint_engine as pe
from toy_browser.rendering.inline_layout import (
    InlineLayout, InlineLayoutConfig, InlineRun, SimpleMetrics,
)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _boxes(html, sheet, w=300.0, h=200.0):
    """{id: rect dict} for every element carrying an id."""
    root = layout_mod.layout(css.apply(dom.parse(html), sheet), w, h)
    out = {}

    def walk(n):
        if n.dom is not None and getattr(n.dom, "is_element", None) \
                and n.dom.is_element():
            _id = n.dom.attr("id")
            if _id:
                out[_id] = dict(x=n.rect.x, y=n.rect.y,
                                w=n.rect.w, h=n.rect.h)
        for c in n.children:
            walk(c)
    walk(root)
    return out


def _near(a, b, tol=0.6):
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Grid：row-gap 只计一次
# ---------------------------------------------------------------------------
@_reg
def test_grid_row_gap_counted_once():
    """20 高 + 10 gap → 第二行偏移 30，不是 40。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div></div>',
               '#g{display:grid;width:100px;grid-template-columns:1fr;'
               'row-gap:10px}#a,#b{height:20px}')
    assert _near(g['b']['y'] - g['a']['y'], 30.0), g


@_reg
def test_grid_row_gap_three_rows():
    """三行：偏移为 0 / 30 / 60（gap 只在行间）。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div>'
               '<div id="c"></div></div>',
               '#g{display:grid;width:100px;grid-template-columns:1fr;'
               'row-gap:10px}#a,#b,#c{height:20px}')
    assert _near(g['a']['y'], 0.0), g
    assert _near(g['b']['y'], 30.0), g
    assert _near(g['c']['y'], 60.0), g
    # 容器高度 = 20*3 + 10*2 = 80
    assert _near(g['g']['h'], 80.0), g


@_reg
def test_grid_no_row_gap_stacks_tight():
    """无 row-gap 时行紧贴（20+20）。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div></div>',
               '#g{display:grid;width:100px;grid-template-columns:1fr}'
               '#a,#b{height:20px}')
    assert _near(g['b']['y'] - g['a']['y'], 20.0), g


# ---------------------------------------------------------------------------
# Grid：显式 grid-column / span
# ---------------------------------------------------------------------------
@_reg
def test_grid_explicit_column_line():
    """``grid-column: 2`` → x = 第 2 列起点（50）。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div></div>',
               '#g{display:grid;width:100px;grid-template-columns:1fr 1fr}'
               '#a{grid-column:2;height:20px}#b{height:20px}')
    assert _near(g['a']['x'] - g['g']['x'], 50.0), g


@_reg
def test_grid_column_span_width():
    """``grid-column: 1 / span 2`` → 宽度 = 两轨 + 中间 gap。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div></div>',
               '#g{display:grid;width:100px;grid-template-columns:1fr 1fr;'
               'column-gap:10px}#a{grid-column:1 / span 2;height:20px}'
               '#b{height:20px}')
    # 两轨各 45：(100-10)/2 = 45；跨两轨 = 45+10+45 = 100
    assert _near(g['a']['w'], 100.0), g
    assert _near(g['a']['x'], 0.0), g


@_reg
def test_grid_auto_placement_does_not_overlap():
    """显式列后的自动项不覆盖已占单元（稀疏放置）。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div></div>',
               '#g{display:grid;width:100px;grid-template-columns:1fr 1fr}'
               '#a{grid-column:2;height:20px}#b{height:20px}')
    # a 在 (行0,列1)，b 的自动游标已越过 → 落到下一行，不与 a 重叠
    assert not (g['b']['y'] == g['a']['y'] and g['b']['x'] == g['a']['x']), g


@_reg
def test_grid_auto_flow_fills_row():
    """无显式定位时按列填满一行再换行（传统行为保持）。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div>'
               '<div id="c"></div></div>',
               '#g{display:grid;width:90px;grid-template-columns:1fr 1fr}'
               '#a,#b,#c{height:20px}')
    assert _near(g['a']['y'], 0.0) and _near(g['b']['y'], 0.0), g
    assert _near(g['a']['x'], 0.0) and _near(g['b']['x'], 45.0), g
    assert _near(g['c']['y'], 20.0), g


@_reg
def test_grid_row_span_placeholder_supported():
    """``grid-row: 1 / span 2`` 至少不丢单元（占两行）。"""
    g = _boxes('<div id="g"><div id="a"></div><div id="b"></div></div>',
               '#g{display:grid;width:100px;grid-template-columns:1fr 1fr}'
               '#a{grid-row:1 / span 2;height:20px}#b{height:20px}')
    assert g['a']['x'] is not None
    # b 被推到第 2 列（a 占用了第 1 列的两行）
    assert _near(g['b']['x'], 50.0), g


# ---------------------------------------------------------------------------
# Flex：wrap 时每行独立 grow
# ---------------------------------------------------------------------------
@_reg
def test_flex_wrap_grow_resolved_per_line():
    """100 宽容器、三项 basis 40 + grow 1 → 50 / 50 / 100。"""
    g = _boxes('<div id="f"><div id="a"></div><div id="b"></div>'
               '<div id="c"></div></div>',
               '#f{display:flex;flex-wrap:wrap;width:100px}'
               '#a,#b,#c{width:40px;height:20px;flex-grow:1}')
    assert _near(g['a']['w'], 50.0), g
    assert _near(g['b']['w'], 50.0), g
    assert _near(g['c']['w'], 100.0), g


@_reg
def test_flex_wrap_lines_stack():
    """wrap 的两行纵向堆叠（第二行在第一行下方）。"""
    g = _boxes('<div id="f"><div id="a"></div><div id="b"></div>'
               '<div id="c"></div></div>',
               '#f{display:flex;flex-wrap:wrap;width:100px}'
               '#a,#b,#c{width:40px;height:20px;flex-grow:1}')
    assert _near(g['a']['y'], 0.0) and _near(g['b']['y'], 0.0), g
    assert _near(g['c']['y'], 20.0), g


@_reg
def test_flex_nowrap_grow_unchanged():
    """nowrap 保持旧语义：剩余空间按 grow 分配（回归保护）。"""
    g = _boxes('<div id="f"><div id="a"></div><div id="b"></div></div>',
               '#f{display:flex;width:200px}'
               '#a{width:40px;height:20px}#b{width:40px;height:20px;'
               'flex-grow:1}')
    assert _near(g['a']['w'], 40.0), g
    assert g['b']['w'] > 40.0, g


@_reg
def test_flex_wrap_no_grow_keeps_basis():
    """wrap 且无 grow：宽度保持 basis，不填充整行。"""
    g = _boxes('<div id="f"><div id="a"></div><div id="b"></div>'
               '<div id="c"></div></div>',
               '#f{display:flex;flex-wrap:wrap;width:100px}'
               '#a,#b,#c{width:40px;height:20px}')
    assert _near(g['a']['w'], 40.0) and _near(g['c']['w'], 40.0), g


# ---------------------------------------------------------------------------
# table：colspan / rowspan
# ---------------------------------------------------------------------------
@_reg
def test_table_colspan_spans_two_columns():
    """``colspan=2`` 的单元格宽 = 两列之和（100 而非 50）。"""
    g = _boxes('<table id="t"><tr><td id="span" colspan="2"></td></tr>'
               '<tr><td id="a"></td><td id="b"></td></tr></table>',
               '#t{display:table;width:100px}tr{display:table-row}'
               'td{display:table-cell;height:20px}')
    assert _near(g['span']['w'], 100.0), g
    assert _near(g['a']['w'], 50.0) and _near(g['b']['w'], 50.0), g


@_reg
def test_table_colspan_does_not_shift_next_row():
    """跨列单元格不影响下一行的列起点。"""
    g = _boxes('<table id="t"><tr><td id="span" colspan="2"></td></tr>'
               '<tr><td id="a"></td><td id="b"></td></tr></table>',
               '#t{display:table;width:100px}tr{display:table-row}'
               'td{display:table-cell;height:20px}')
    assert _near(g['a']['x'], 0.0), g
    assert _near(g['b']['x'], 50.0), g


@_reg
def test_table_colspan_widens_auto_columns():
    """auto 宽度表格：跨列单元格的自然宽度分摊到所跨列。"""
    g = _boxes('<table id="t"><tr>'
               '<td id="span" colspan="2">abcdefghij</td></tr>'
               '<tr><td id="a">x</td><td id="b">y</td></tr></table>',
               '#t{display:table}tr{display:table-row}'
               'td{display:table-cell;height:20px}')
    assert g['span']['w'] >= g['a']['w'] + g['b']['w'] - 0.6, g


@_reg
def test_table_rowspan_reserves_column():
    """``rowspan=2`` 的单元格为下一行保留该列（后续单元右移）。"""
    g = _boxes('<table id="t"><tr><td id="r" rowspan="2"></td>'
               '<td id="a"></td></tr>'
               '<tr><td id="b"></td></tr></table>',
               '#t{display:table;width:100px}tr{display:table-row}'
               'td{display:table-cell;height:20px}')
    assert _near(g['r']['x'], 0.0), g
    # 第二行的 b 必须跳过 r 占据的第 1 列
    assert _near(g['b']['x'], 50.0), g


@_reg
def test_table_plain_two_by_two_unchanged():
    """无 span 的 2×2 表格：两列等宽（回归保护）。"""
    g = _boxes('<table id="t"><tr><td id="a"></td><td id="b"></td></tr>'
               '<tr><td id="c"></td><td id="d"></td></tr></table>',
               '#t{display:table;width:100px}tr{display:table-row}'
               'td{display:table-cell;height:20px}')
    for k in ('a', 'b', 'c', 'd'):
        assert _near(g[k]['w'], 50.0), g
    assert _near(g['c']['y'], 20.0), g


# ---------------------------------------------------------------------------
# InlineRun：混排字号 / 样式 / 字族
# ---------------------------------------------------------------------------
@_reg
def test_inline_mixed_font_size_preserved():
    """12px 与 24px 混排：每个片段保留自己的字号。"""
    line = InlineLayout().layout([
        InlineRun('A', font_px=12, style={'color': 'red'}),
        InlineRun('B', font_px=24, style={'color': 'blue'}),
    ])[0]
    assert [r.font_px for r in line.runs] == [12, 24]


@_reg
def test_inline_run_style_preserved():
    """每个片段保留自己的 style（不是空 dict）。"""
    line = InlineLayout().layout([
        InlineRun('A', font_px=12, style={'color': 'red'}),
        InlineRun('B', font_px=24, style={'color': 'blue'}),
    ])[0]
    assert [r.style for r in line.runs] == [{'color': 'red'},
                                            {'color': 'blue'}]


@_reg
def test_inline_run_font_family_preserved():
    """字族随片段保留（测量/绘制能追踪具体 face）。"""
    line = InlineLayout().layout([
        InlineRun('A', font_family='serif'),
        InlineRun('B', font_family='monospace'),
    ])[0]
    assert [r.font_family for r in line.runs] == ['serif', 'monospace']


@_reg
def test_inline_mixed_size_advance_per_run():
    """混排时每个字符用它自己字号的 advance（不是全局 16px）。"""
    metrics = SimpleMetrics(advance_px=10.0, ascent_px=8.0, descent_px=2.0)

    class _BySize:
        """advance = font_px * 0.5（字号越大越宽）。"""

        def advance(self, ch, font_px):
            return font_px * 0.5

        def line_metrics(self, font_px):
            return (font_px * 0.8, font_px * 0.2)

    lay = InlineLayout(InlineLayoutConfig(metrics=_BySize(), max_width=1000.0))
    line = lay.layout([InlineRun('A', font_px=10), InlineRun('B', font_px=30)])[0]
    assert _near(line.runs[0].x, 0.0)
    assert _near(line.runs[1].x, 5.0)      # A 的 advance = 10*0.5
    assert _near(line.width, 20.0)         # 5 + 15
    assert metrics.advance_px == 10.0      # 共享度量对象未被修改


@_reg
def test_inline_break_uses_run_font_size():
    """分行按各自字号累加（大字号先换行）。"""
    class _BySize:
        def advance(self, ch, font_px):
            return font_px

        def line_metrics(self, font_px):
            return (font_px, 0.0)

    lay = InlineLayout(InlineLayoutConfig(metrics=_BySize(), max_width=25.0,
                                          white_space="normal"))
    # A(20) + 空格(20?) ... 用两个无空格 run：A=20, B=20 → 第二个放不下
    lines = lay.layout([InlineRun('A', font_px=20), InlineRun('B', font_px=20)])
    assert len(lines) == 2, [l.text() for l in lines]
    assert lines[0].text().startswith('A')


@_reg
def test_inline_whitespace_folding_unchanged():
    """normal 模式仍折叠连续空白（回归保护）。"""
    lay = InlineLayout(InlineLayoutConfig(metrics=SimpleMetrics(advance_px=8.0),
                                          max_width=400.0))
    lines = lay.layout([InlineRun('a   b'), InlineRun('c')])
    assert lines[0].text() == 'a bc', lines[0].text()


@_reg
def test_inline_pre_mode_preserved():
    """pre 模式保留全部空白（回归保护）。"""
    lay = InlineLayout(InlineLayoutConfig(metrics=SimpleMetrics(advance_px=8.0),
                                          white_space="pre", max_width=400.0))
    lines = lay.layout([InlineRun('  a  ')])
    assert lines[0].text() == '  a  ', repr(lines[0].text())


@_reg
def test_inline_empty_runs_returns_no_lines():
    """空 run 序列不产生行盒（不是崩溃/幽灵行）。"""
    assert InlineLayout().layout([]) == []


# ---------------------------------------------------------------------------
# 文本 DisplayCommand 携带 font_family
# ---------------------------------------------------------------------------
def _text_commands(html, sheet=''):
    tree = layout_mod.layout(css.apply(dom.parse(html), sheet), 100.0, 40.0)
    return [c for c in pe.emit_commands(tree).commands if c.kind == 'text']


@_reg
def test_display_command_carries_font_family():
    """文本命令携带 face：测量与绘制使用同一字体。"""
    cmds = _text_commands('<div style="font-family:serif">A</div>')
    assert cmds, "no text command emitted"
    assert all(getattr(c, 'font_family', None) == 'serif' for c in cmds), \
        [c.font_family for c in cmds]


@_reg
def test_font_family_inherits_into_text_boxes():
    """font-family 继承到文本盒（子元素不再是 monospace）。"""
    cmds = _text_commands('<div style="font-family:serif">A</div>')
    fams = {c.font_family for c in cmds}
    assert fams == {'serif'}, fams


@_reg
def test_display_command_default_family():
    """未指定 font-family 时回退 monospace（回归保护）。"""
    cmds = _text_commands('<div>A</div>')
    assert cmds
    assert all(c.font_family == 'monospace' for c in cmds)


# ---------------------------------------------------------------------------
# 选择器遗留项（本轮随 C02 一起修的 C01 反例）
# ---------------------------------------------------------------------------
def _element(html, tag=None, id_=None):
    root = dom.parse(html)

    def walk(n):
        yield n
        for c in n.children:
            yield from walk(c)
    return next(n for n in walk(root)
                if n.is_element()
                and (tag is None or n.tag == tag)
                and (id_ is None or n.attr('id') == id_))


@_reg
def test_unknown_pseudo_class_rejected():
    """未知伪类不能静默当匹配（``div:madeup`` 不匹配）。"""
    node = _element('<div id="x" class="a b"></div>', 'div')
    assert css.Selector.parse('div:madeup').matches(node) is False


@_reg
def test_mixed_combinators_match():
    """``main div > p``：后代 + 子代混合组合符。"""
    node = _element('<main><section><div><p id="x"></p></div></section></main>',
                    id_='x')
    assert css.Selector.parse('main div > p').matches(node) is True


@_reg
def test_combinators_recorded_per_step():
    """组合符按步记录（' ' 与 '>' 各就各位）。"""
    sel = css.Selector.parse('main div > p')
    assert sel.combinators == (' ', '>'), sel.combinators


@_reg
def test_compound_id_selector_does_not_leak_to_sibling():
    """``div#x`` 只染 #x，不染同 tag 的兄弟 #y。"""
    sn = css.apply(dom.parse('<div id="x"></div><div id="y"></div>'),
                   'div#x{color:red}')
    out = {}
    def walk(n):
        if n.dom is not None and n.dom.is_element() and n.dom.attr('id'):
            out[n.dom.attr('id')] = n.props.get('color')
        for c in n.children:
            walk(c)
    walk(sn)
    assert str(out['x']) == str(css.parse_color('red')), out
    assert out['y'] is None, out


@_reg
def test_position_pseudo_classes_implemented():
    """结构伪类真实判定（不再静默匹配）。"""
    root = dom.parse('<div id="a"></div><div id="b"></div><div id="c"></div>')

    def walk(n):
        yield n
        for c in n.children:
            yield from walk(c)
    divs = [n for n in walk(root) if n.is_element() and n.tag == 'div']
    assert css.Selector.parse('div:last-child').matches(divs[-1]) is True
    assert css.Selector.parse('div:last-child').matches(divs[0]) is False
    assert css.Selector.parse('div:only-child').matches(divs[0]) is False
    assert css.Selector.parse('div:nth-child(2)').matches(divs[1]) is True
    assert css.Selector.parse('div:nth-child(2)').matches(divs[0]) is False
    assert css.Selector.parse('div:nth-last-child(1)').matches(divs[-1]) is True


@_reg
def test_type_based_pseudo_classes():
    """:first-of-type / :last-of-type 按同 tag 兄弟判定。"""
    root = dom.parse('<p id="p1"></p><div id="d1"></div><div id="d2"></div>')

    def walk(n):
        yield n
        for c in n.children:
            yield from walk(c)
    nodes = {n.attr('id'): n for n in walk(root) if n.is_element()}
    assert css.Selector.parse('div:first-of-type').matches(nodes['d1']) is True
    assert css.Selector.parse('div:first-of-type').matches(nodes['d2']) is False
    assert css.Selector.parse('div:last-of-type').matches(nodes['d2']) is True
    assert css.Selector.parse('p:only-of-type').matches(nodes['p1']) is True
