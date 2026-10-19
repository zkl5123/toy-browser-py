"""Current text rasterization specimen; no product changes or UI automation."""
from pathlib import Path
import sys, json, hashlib

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from toy_browser.typography import font, text, ttf_font
from toy_browser.rendering.paint import Bitmap, Color
from toy_browser.app import browser

OUT = Path(__file__).resolve().parent
face = ttf_font.load_font_for_family('arial', 16.0, False, [str(ROOT/'fonts')])
assert face is not None
# Same known bundled face throughout: isolate shape rendering from fallback.
font._real_font_metrics['serif'] = face
font._real_font_metrics['monospace'] = face
text.clear_metrics_memo()
bmp = Bitmap(1000, 500)
rows = [
    (12, '12px  OQ B8 eago @%  AVATAR To fi ffi  0123456789'),
    (16, '16px  OQ B8 eago @%  AVATAR To fi ffi  0123456789'),
    (24, '24px  OQ B8 eago @%  AVATAR To fi ffi  0123456789'),
    (36, '36px  OQ B8 eago @%  AVATAR To fi ffi'),
    (48, '48px  OQ B8 eago @%  AVATAR To'),
]
y = 24
for size, sample in rows:
    text.draw_text(bmp, 24.25, y, sample, float(size), Color(20,20,20), 950,
                   font_family='serif')
    y += max(65, size*1.6)
browser.write_png(bmp, str(OUT/'current-glyphs.png'))
glyphs={}
for ch in 'OB8gjpq@':
    outline=face.glyph_outline(ch)
    mask=text._get_glyph_mask(ch,face,24.0,1.0)
    glyphs[ch]={'outline_bbox':outline.bbox if outline else None,
                'outline_points':len(outline.points) if outline else 0,
                'has_contour_boundaries':any(hasattr(outline,key) for key in ('contours','end_pts','end_points')),
                'mask_dimensions':[mask.w,mask.h] if mask else None,
                'mask_has_bearing':any(hasattr(mask,key) for key in ('left','top','bearing_x','bearing_y')) if mask else False}
data={'font':'bundled fonts/arial.ttf, explicitly installed into specimen process registry',
      'font_sha256':hashlib.sha256((ROOT/'fonts/arial.ttf').read_bytes()).hexdigest(),
      'sizes':[x[0] for x in rows],'glyphs':glyphs,
      'scope':'Current rasterization only, not browser comparison, no code fixes.'}
(OUT/'observations.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
print('Saved current-glyphs.png and observations.json')
