import json,statistics
from pathlib import Path
from PIL import Image,ImageChops,ImageDraw
D=Path(__file__).resolve().parent
read=lambda n:json.loads((D/(n+'.json')).read_text(encoding='utf8'))
e,t=read('edge_js_results'),read('toy_js_results')
js={};errors=[]
for k in e:
 a,b=e[k],t[k];same=(a['status']=='OK')==(b['status']=='OK') and a['logs']==b['logs']
 js[k]={'match':same,'edge':a,'toy':b}
 if a['status']!='OK' and b['status']!='OK':errors.append(k)
out={'js_match':sum(v['match'] for v in js.values()),'js_total':len(js),'js_both_error':errors,'js':js,'layout':{},'render':{},'perf':{}}
e,t=read('edge_layout_results'),read('toy_layout_results')
for k in e:
 out['layout'][k]={i:{'edge':e[k][i],'toy':t[k][i],'max_delta_px':max(abs(e[k][i][v]-t[k][i][v]) for v in ['x','y','w','h'])} for i in e[k]}
for k in read('render_cases'):
 e=Image.open(D/('edge_shot_'+k+'.png')).convert('RGB');t=Image.open(D/('toy_shot_'+k+'.png')).convert('RGB')
 different=union=diff_union=0
 for a,b in zip(e.getdata(),t.getdata()):
  changed=sum(abs(x-y) for x,y in zip(a,b))>30
  ink=min(a)<240 or min(b)<240
  different+=changed;union+=ink;diff_union+=changed and ink
 out['render'][k]={'whole_diff_pct':100*different/(800*600),'foreground_union_diff_pct':100*diff_union/max(1,union),'foreground_union_pixels':union}
 pair=Image.new('RGB',(1600,640),'#eeeeee');pair.paste(e,(0,40));pair.paste(t,(800,40));draw=ImageDraw.Draw(pair);draw.text((10,10),'Edge reference',fill='black');draw.text((810,10),'toy-browser-py',fill='black');pair.save(D/(k+'_comparison.png'))
e,t=read('edge_perf'),read('toy_extra')['perf']
for n in t:out['perf'][n]={'toy_render_median_ms':statistics.median(v['renderMs'] for v in t[n]),'toy_png_total_median_ms':statistics.median(v['totalWithPngMs'] for v in t[n]),'edge_layout_median_ms':statistics.median(v['layoutMs'] for v in e[n]),'edge_screenshot_total_median_ms':statistics.median(v['totalWithScreenshotMs'] for v in e[n])}
(D/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in out.items() if k!='js'},ensure_ascii=False,indent=2))
