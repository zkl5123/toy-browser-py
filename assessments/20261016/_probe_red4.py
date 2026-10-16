import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
import browser, layout

lc = json.load(open(Path(__file__).resolve().parent / 'layout_cases.json', encoding='utf-8'))

def red_bounds(bmp):
    w, h = bmp.width, bmp.height
    px = bmp.pixels
    minx = miny = maxx = maxy = None
    for y in range(h):
        base = y * w
        for x in range(w):
            v = px[base + x]
            if ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF) == (255, 0, 0):
                if minx is None or x < minx: minx = x
                if maxx is None or x > maxx: maxx = x
                if miny is None or y < miny: miny = y
                if maxy is None or y > maxy: maxy = y
    if minx is None:
        return None
    return [minx, miny, maxx + 1, maxy + 1]

def geom(name, ids):
    """Border-box geometry of the probe ids (what getBoundingClientRect
    reports in the browser)."""
    import dom, css
    html = lc[name]
    tree = dom.parse(html)
    author_css, ua_css = browser._full_css(tree, '')
    media_rules = css.parse_media_queries(author_css)
    supports_rules = css.parse_supports_blocks(author_css)
    styled = css.apply(tree, author_css, ua_css=ua_css, media_rules=media_rules,
                       supports_rules=supports_rules, viewport_w=800.0, viewport_h=600.0)
    root = layout.layout(styled, 800.0, 600.0)
    out = {}
    def find(n, id_):
        if n is None:
            return None
        el = getattr(n, 'dom', None)
        if el is not None and getattr(el, 'attrs', None) and el.attrs.get('id') == id_:
            return n
        for c in (n.children or []):
            r = find(c, id_)
            if r is not None:
                return r
        return None
    for id_ in ids:
        bx = find(root, id_)
        if bx is None:
            out[id_] = None
            continue
        br = bx.border_rect()
        out[id_] = (round(br.x, 4), round(br.y, 4), round(br.w, 4), round(br.h, 4))
    return out

# R4 stage 5 verification — the 4 painted color-block cases from the
# assessment plan, plus the 17-case geometry probe.
#
# Reference (Edge GPU screenshots, edge_geom_*.png):
#   flex_padding_border: visible red group [63,6,237,36] — red fill +
#   5px padding + 2px blue border, centered in a 300x150 flex container.
#   flex_reverse_center / flex_gap / grid_unequal: bare 40x20 red cells
#   (4px margins, no padding/border) — red bounds = cell border-box.
#
# Acceptance (assessment plan, task 5): the painted color-block
# boundaries sit within 1px of the Edge reference (the toy's raster
# border ring is 1px where CSS says 2px, so exactly 1px of inset
# difference is expected and tolerated).

print('--- painted red-pixel bounds (vs Edge reference) ---')
for name in ('flex_reverse_center', 'flex_gap', 'grid_unequal', 'flex_padding_border'):
    b = red_bounds(browser.render_document(lc[name], '', 800, 600))
    print(name, 'toy red_bounds =', b)

print()
print('--- geometry probe: 17 layout cases, border-box of probed ids ---')
edge = json.load(open(Path(__file__).resolve().parent / 'edge_layout_results.json', encoding='utf-8'))

# (name, probed ids) for all 17 cases.
probe_ids = {
    'layout_blocks': ['a', 'b'],
    'layout_margin_collapse': ['t', 'b'],
    'layout_flex': ['row', 'c1', 'c2', 'c3'],
    'layout_grid': ['g', 'g1', 'g2', 'g3', 'g4', 'g5', 'g6'],
    'layout_table': ['tab'],
    'layout_abs': ['p', 'c'],
    'layout_text_wrap': ['w'],
    'flex_reverse': ['row', 'c1', 'c2', 'c3'],
    'flex_reverse_center': ['row', 'c1', 'c2', 'c3'],
    'flex_reverse_end': ['row', 'c1', 'c2', 'c3'],
    'flex_gap': ['row', 'c1', 'c2', 'c3'],
    'flex_single_around': ['row', 'c1'],
    'flex_single_evenly': ['row', 'c1'],
    'flex_padding_border': ['row', 'c1', 'c2', 'c3'],
    'flex_column': ['row', 'c1', 'c2', 'c3'],
    'grid_unequal': ['g', 'g1', 'g2'],
    'percent_height': ['p', 'c'],
}

match = 0
total = 0
for name in list(lc.keys()):
    ids = probe_ids.get(name, [])
    now = geom(name, ids)
    for id_ in ids:
        ref = edge.get(name, {}).get(id_)
        if ref is None:
            continue
        total += 1
        cur = now.get(id_)
        ref4 = (ref['x'], ref['y'], ref['w'], ref['h'])
        ok = cur is not None and all(abs(cur[i] - ref4[i]) < 1e-3 for i in range(4))
        if ok:
            match += 1
        print(f"  {name:24s} {id_:4s} toy={cur} edge={ref4} {'OK' if ok else 'DIFF'}")

print()
print(f'geometry: {match}/{total} values within 1e-3 of Edge')
