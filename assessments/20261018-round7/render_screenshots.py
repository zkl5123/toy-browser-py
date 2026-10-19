"""Current product PNGs from frozen historical fixtures; no reference browser run."""
from pathlib import Path
import json, sys, hashlib
D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parents[1]))
from toy_browser.app import browser
cases=json.loads((D/'render_cases.json').read_text(encoding='utf-8'))
out=[]
for name, html in cases.items():
    bmp=browser.render_document(html,'',800,600)
    p=D/f'toy_shot_{name}.png'
    browser.write_png(bmp,str(p))
    out.append({'case':name,'file':p.name,'width':800,'height':600,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(D/'screenshots.json').write_text(json.dumps({'scope':'Current CPU browser.render_document output. No fresh Edge screenshot or pixel comparison.','images':out},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False))
