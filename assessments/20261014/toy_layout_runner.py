"""toy-browser layout probes: mirror edge_layout.js via the full pipeline internals."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import dom as dom_mod, css as css_mod, layout as layout_mod, browser

CASES = json.load(open(os.path.join(os.path.dirname(__file__), 'layout_cases.json'), encoding='utf-8'))
PROBE = {
  'layout_blocks': ['a', 'b'],
  'layout_margin_collapse': ['t', 'b'],
  'layout_flex': ['row', 'c1', 'c2', 'c3'],
  'layout_grid': ['g', 'g1', 'g4'],
  'layout_table': ['tab'],
  'layout_abs': ['p', 'c'],
  'layout_text_wrap': ['w'],
}
W, H = 800, 600

def _find_box(root, id_):
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx is None:
            continue
        el = getattr(bx, 'dom', None)
        idv = el.attrs.get('id') if (el is not None and getattr(el, 'attrs', None)) else None
        if idv == id_:
            return bx
        for ch in (getattr(bx, 'children', None) or []):
            stack.append(ch)
    return None

out = {}
for name, html in CASES.items():
    tree = dom_mod.parse(html)
    author_css, ua_css = browser._full_css(tree, '')
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
        media_rules=media_rules, supports_rules=supports_rules,
        viewport_w=float(W), viewport_h=float(H),
    )
    root = layout_mod.layout(styled, float(W), float(H))
    out[name] = {}
    for id_ in PROBE[name]:
        bx = _find_box(root, id_)
        if bx is None:
            out[name][id_] = None
        else:
            r = bx.rect
            out[name][id_] = {'x': round(r.x, 2), 'y': round(r.y, 2),
                               'w': round(r.w, 2), 'h': round(r.h, 2)}

with open(os.path.join(os.path.dirname(__file__), 'toy_layout_results.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=1)
print('toy_layout_results.json written,', len(out), 'cases')
for name in out:
    for id_, v in out[name].items():
        print(f"  {name}.{id_} = {v}")
