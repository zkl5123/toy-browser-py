"""Check painted color boundaries independently of internal box conventions."""
import sys,json
from pathlib import Path
from PIL import Image
D=Path(__file__).resolve().parent;sys.path.insert(0,str(D.parents[1]));import browser
cases=json.loads((D/'layout_cases.json').read_text(encoding='utf8'));out={}
for name in ['flex_reverse_center','flex_gap','grid_unequal','flex_padding_border']:
 browser.write_png(browser.render_document(cases[name],'',800,600),str(D/f'toy_geom_{name}.png'))
 out[name]={}
 for side in ['edge','toy']:
  with Image.open(D/f'{side}_geom_{name}.png')as im:
   rgb=im.convert('RGB');w,h=rgb.size;pts=[(x,y)for y in range(180)for x in range(320)if rgb.getpixel((x,y))==(255,0,0)]
   out[name][side]={'red_bounds':[min(x for x,y in pts),min(y for x,y in pts),max(x for x,y in pts)+1,max(y for x,y in pts)+1] if pts else None}
(D/'painted_geometry.json').write_text(json.dumps(out,indent=2),encoding='utf8');print(json.dumps(out,indent=2))
