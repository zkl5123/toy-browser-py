import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from toy_browser.app import browser; from toy_browser.layout import layout; from toy_browser.style import css; from toy_browser.document import dom

html = ('<html><head><style>*{box-sizing:border-box} body{margin:0} '
        '.box{width:240px;height:80px;background:#eef;border:2px solid #039;'
        'margin:12px;padding:8px} p{line-height:1.5}</style></head>'
        "<body><div class='box'>A box with text and <b>bold</b>, <i>italic</i> and "
        "<span style='color:#090'>green</span>.</div></body></html>")

tree = dom.parse(html)
author_css, ua_css = browser._full_css(tree, '')
media_rules = css.parse_media_queries(author_css)
supports_rules = css.parse_supports_blocks(author_css)
styled = css.apply(tree, author_css, ua_css=ua_css, media_rules=media_rules,
                  supports_rules=supports_rules, viewport_w=800.0, viewport_h=600.0)
root = layout.layout(styled, 800.0, 600.0)

# Find the .box div and dump the x-positions of every inline/text child in order.
def find_box(n, cls):
    if n is None:
        return None
    el = getattr(n, 'dom', None)
    if el is not None and getattr(el, 'attrs', None) and el.attrs.get('class') == cls:
        return n
    for c in (n.children or []):
        r = find_box(c, cls)
        if r is not None:
            return r
    return None

box = find_box(root, 'box')
print('box rect =', box.rect, 'border_rect =', box.border_rect())
print('--- inline run children (in order) ---')
for c in box.children:
    tag = getattr(getattr(c, 'dom', None), 'tag', None)
    tc = c.text_content or ''
    print(f"  type={c.box_type:6s} x={c.rect.x:8.2f} w={c.rect.w:7.2f} tag={tag!s:6s} text={tc[:20]!r}")
