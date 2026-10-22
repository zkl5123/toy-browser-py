"""本轮只读源码行为探针；不启动 GUI、网络或真实 GPU，不改产品代码。"""
from pathlib import Path
import json
import sys
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout as layout_mod
from toy_browser.rendering import gpu, paint_engine as pe

results = []
def check(name, observed, expected, evidence):
    results.append(dict(name=name, observed=observed, expected=expected,
                        passes=observed == expected, evidence=evidence))

def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)

def element(root, tag=None, id_=None):
    return next(n for n in walk(root) if n.is_element() and
                (tag is None or n.tag == tag) and
                (id_ is None or n.attr('id') == id_))

def text(node):
    return ''.join(n.text for n in walk(node) if n.node_type == n.TEXT)

def colors(html, sheet):
    styled = css.apply(dom.parse(html), sheet)
    return {n.dom.attr('id'): str(n.props.get('color'))
            for n in walk(styled) if n.dom.attr('id')}

check('html_quoted_gt', element(dom.parse('<div title="a>b">x</div>'), 'div').attr('title'), 'a>b', 'tokenize.py:235')
check('html_duplicate_attr_first_wins', element(dom.parse('<div id="first" id="last">x</div>'), 'div').attr('id'), 'first', 'tokenize.py:283')
check('html_rcdata_entity', text(element(dom.parse('<textarea>&amp;</textarea>'), 'textarea')), '&', 'tokenize.py:285-299')
check('html_whitespace_text_preserved', text(dom.parse('<span>a</span> <span>b</span>')), 'a b', 'tokenize.py:232;build.py:129')
check('html_implied_document_element', [n.tag for n in dom.parse('<p>x').children], ['html'], 'build.py:121-181')
check('html_table_implied_tbody', [n.tag for n in element(dom.parse('<table><tr><td>x</td></tr></table>'), 'table').children], ['tbody'], 'build.py:168-178')
node = element(dom.parse('<div id="x" class="a b"></div>'), 'div')
check('selector_compound_specificity', css.Selector.parse('div#x.a').specificity, (0, 1, 1, 1), 'css.py:638-657')
check('selector_unknown_pseudo_rejected', css.Selector.parse('div:madeup').matches(node), False, 'css.py:608-621')
check('selector_not_semantics', css.Selector.parse('div:not(.a)').matches(node), False, 'css.py:608-621')
check('selector_attribute', css.Selector.parse('[id="x"]').matches(node), True, 'css.py:463-473')
mixed = element(dom.parse('<main><section><div><p id="x"></p></div></section></main>'), id_='x')
check('selector_mixed_combinators', css.Selector.parse('main div > p').matches(mixed), True, 'css.py:595-602;400-409')
red = str(css.parse_color('red'))
blue = str(css.parse_color('blue'))
check('cascade_id_beats_two_classes', colors('<div id="x" class="a b"></div>', '#x{color:red}.a.b{color:blue}')['x'], red, 'css.py:2242-2251')
check('cascade_structural_cache', colors('<div id="x"></div><div id="y"></div>', 'div:first-child{color:red}'), {'x': red, 'y': 'None'}, 'css.py:2184-2186;2333-2346')
check('cascade_compound_id_cache', colors('<div id="x"></div><div id="y"></div>', 'div#x{color:red}'), {'x': red, 'y': 'None'}, 'css.py:2174-2176;2333-2346')

# 用无设备缓存检验真实的 prepare 调用，避免把驱动可用性与逻辑正确性混淆。
backend = gpu.GPUBackend.__new__(gpu.GPUBackend)
backend._img_cache = gpu.ImageTextureCache(None)
# R8 fixture adaptation: __new__ bypasses constructor; initialize new present gate.
from toy_browser.rendering.gui_backend_boundary import SurfacePresentGate
backend._gate = SurfacePresentGate()
backend._device = None
scene = pe.DisplayList()
scene.commands = [pe.DisplayCommand('img', x=0, y=0, w=1, h=1, img_pixels=[0xFF0000], img_src_w=1, img_src_h=1),
                  pe.DisplayCommand('img', x=1, y=0, w=1, h=1, img_pixels=[0x0000FF], img_src_w=1, img_src_h=1)]
backend._prepare_image_textures(scene)
check('p05_distinct_content_resident', backend.image_cache.resident(), 2, 'gpu.py:909-912')
results.append(dict(name='p05_cache_observation', stats=backend.image_cache.stats(),
                    entries={k: list(v['rgba']) for k,v in backend.image_cache._entries.items()}))
canvas = pe.rasterize_to_canvas(scene, 2, 1)
check('cpu_red_blue_frame_independent_of_p05', list(canvas.img.buffer), [255, 0, 0, 0, 0, 255], 'gpu.py:794-829;paint_engine.py:841-842')
class UploadRecorder:
    device = object()
    def __init__(self): self.calls = []
    def upload_rgba8(self, w, h, rgba, label=''):
        self.calls.append((w,h,bytes(rgba),label))
        return object()
recorder = UploadRecorder()
atlas = gpu.GlyphAtlas(recorder)
atlas.get_or_rasterize('A')
alpha = recorder.calls[-1][2][3::4]
check('p06_atlas_has_coverage', any(alpha), True, 'gpu.py:477-494')
results.append(dict(name='p06_atlas_upload', size=list(recorder.calls[-1][:2]), bytes=len(recorder.calls[-1][2]), nonzero_alpha=sum(a != 0 for a in alpha), uv=atlas.uv('A')))
check('p06_family_affects_glyph', gpu.GlyphAtlas._default_glyph_rgba('A','serif',400,16) != gpu.GlyphAtlas._default_glyph_rgba('A','monospace',400,16), True, 'gpu.py:504')
check('p06_unicode_not_ascii_alias', gpu.GlyphAtlas._default_glyph_rgba('中','monospace',400,16) != gpu.GlyphAtlas._default_glyph_rgba('-','monospace',400,16), True, 'gpu.py:504')
check('p06_fractional_size_distinct', gpu.GlyphAtlas._default_glyph_rgba('A','monospace',400,17) != gpu.GlyphAtlas._default_glyph_rgba('A','monospace',400,16), True, 'gpu.py:505-506')
check('gpu_present_needs_surface', backend.present(object(), None), False, 'gpu.py:942-946')
try:
    gpu.GPURasterizer(None).rasterize_and_verify(pe.DisplayList(), 1, 1)
    verify_result = 'no_error'
except Exception as exc:
    verify_result = type(exc).__name__ + ': ' + str(exc)
check('gpu_verify_helper_rgb_conversion', verify_result, 'no_error', 'gpu.py:149-152')

# 小型几何探针：固定高度、无需文字测量；只调用产品公开解析/样式/布局链。
def boxes(html, sheet):
    root = layout_mod.layout(css.apply(dom.parse(html), sheet), 300, 200)
    return {n.dom.attr('id'): dict(x=n.rect.x,y=n.rect.y,w=n.rect.w,h=n.rect.h)
            for n in walk(root) if n.dom and n.dom.attr('id')}
try:
    geom = boxes('<div id="g"><div id="a"></div><div id="b"></div></div>', '#g{display:grid;width:100px;grid-template-columns:1fr;row-gap:10px}#a,#b{height:20px}')
    check('grid_row_gap_once', geom['b']['y']-geom['a']['y'], 30.0, 'layout.py:3505-3517')
    results.append(dict(name='grid_geometry', boxes=geom))
except Exception as exc:
    results.append(dict(name='grid_geometry', error=repr(exc)))

# 正向对照与已存在但未接通的接口边界。
check('control_void_siblings', [n.tag for n in dom.parse('<img><p>x</p>').children], ['img','p'], 'build.py:179')
check('control_script_rawtext', text(element(dom.parse('<script>a<b && c>d</script>'), 'script')), 'a<b && c>d', 'tokenize.py:285-302')
check('control_author_important_over_inline', colors('<div id="x" style="color:blue"></div>', '#x{color:red!important}')['x'], red, 'css.py:2401-2408')
from toy_browser.rendering.inline_layout import InlineLayout, InlineRun
line = InlineLayout().layout([InlineRun('A', font_px=12, style={'color':'red'}), InlineRun('B', font_px=24, style={'color':'blue'})])[0]
check('inline_mixed_run_font_preserved', [r.font_px for r in line.runs], [12,24], 'inline_layout.py:309-313')
check('inline_run_style_preserved', [r.style for r in line.runs], [{'color':'red'},{'color':'blue'}], 'inline_layout.py:313')
font_tree = layout_mod.layout(css.apply(dom.parse('<div style="font-family:serif">A</div>'), ''), 100, 40)
font_commands = [c for c in pe.emit_commands(font_tree).commands if c.kind == 'text']
check('display_command_carries_family', bool(font_commands) and all(getattr(c,'font_family',None) == 'serif' for c in font_commands), True, 'paint_engine.py:17-91;828-840')
try:
    geom = boxes('<div id="g"><div id="a"></div><div id="b"></div></div>', '#g{display:grid;width:100px;grid-template-columns:1fr 1fr}#a{grid-column:2;height:20px}#b{height:20px}')
    check('grid_explicit_column', geom['a']['x']-geom['g']['x'], 50.0, 'layout.py:3456-3458;3510-3512')
except Exception as exc:
    results.append(dict(name='grid_explicit_column', error=repr(exc)))

try:
    geom = boxes('<div id="f"><div id="a"></div><div id="b"></div><div id="c"></div></div>', '#f{display:flex;flex-wrap:wrap;width:100px}#a,#b,#c{width:40px;height:20px;flex-grow:1}')
    check('flex_growth_resolved_per_line', [geom[k]['w'] for k in ('a','b','c')], [50.0,50.0,100.0], 'layout.py:2850-2903')
    results.append(dict(name='flex_geometry', boxes=geom))
except Exception as exc:
    results.append(dict(name='flex_geometry', error=repr(exc)))
try:
    geom = boxes('<table id="t"><tr><td id="span" colspan="2"></td></tr><tr><td id="a"></td><td id="b"></td></tr></table>', '#t{display:table;width:100px}tr{display:table-row}td{display:table-cell;height:20px}')
    check('table_colspan_width', geom['span']['w'], 100.0, 'layout.py:3571-3575;3707-3714')
    results.append(dict(name='table_geometry', boxes=geom))
except Exception as exc:
    results.append(dict(name='table_geometry', error=repr(exc)))

out = dict(scope='本地只读逻辑与最小 CPU 帧；无网络、GUI、真实 GPU 驱动测试',
           python=sys.version, checks=sum('passes' in r for r in results),
           passed=sum(r.get('passes') is True for r in results),
           failed=sum(r.get('passes') is False for r in results), results=results)
output = Path(__file__).with_name('render-probes.json')
output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(out, ensure_ascii=False, indent=2))
