"""Recompute image metrics from existing captures without rerendering."""
import json
from pathlib import Path
from PIL import Image
D=Path(__file__).resolve().parent
out={}
for name in json.loads((D/'render_cases.json').read_text(encoding='utf8')):
 with Image.open(D/f'edge_shot_{name}.png')as im:e=list(im.convert('RGB').getdata())
 with Image.open(D/f'toy_shot_{name}.png')as im:t=list(im.convert('RGB').getdata())
 out[name]={'diff_rate':round(100*sum(sum(abs(a-b)for a,b in zip(p,q))>30 for p,q in zip(e,t))/len(e),2),'edge_nonwhite_pct':round(100*sum(all(c<240 for c in p)for p in e)/len(e),2)}
(D/'render_diffs.json').write_text(json.dumps(out,indent=2),encoding='utf8')
print(out)
