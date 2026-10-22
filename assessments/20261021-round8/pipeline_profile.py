"""Measure complete samples; keep display-list emission separate from raster."""
import sys,json,time,statistics,cProfile,pstats,io
from pathlib import Path
D=Path(__file__).resolve().parent;sys.path.insert(0,str(D.parents[1]))
from toy_browser.document import dom; from toy_browser.style import css; from toy_browser.layout import layout; from toy_browser.rendering import paint_engine; from toy_browser.app import browser
def run(n):
 html='<html><body>'+''.join(f'<div style="width:100px;height:20px;margin:2px;background:#ccc">row {i}</div>'for i in range(n))+'</body></html>'
 sample={};t=time.perf_counter();tree=dom.parse(html);sample['parse_ms']=(time.perf_counter()-t)*1000
 t=time.perf_counter();author,ua=browser._full_css(tree,'');styled=css.apply(tree,author,ua_css=ua,viewport_w=800.,viewport_h=600.);sample['style_ms']=(time.perf_counter()-t)*1000
 t=time.perf_counter();root=layout.layout(styled,800.,600.);sample['layout_ms']=(time.perf_counter()-t)*1000
 t=time.perf_counter();dl=paint_engine.emit_commands(root);sample['emit_ms']=(time.perf_counter()-t)*1000
 t=time.perf_counter();bmp=paint_engine.rasterize(dl,800,600);sample['raster_ms']=(time.perf_counter()-t)*1000
 t=time.perf_counter();browser.write_png_bytes(bmp);sample['png_ms']=(time.perf_counter()-t)*1000
 sample['commands']=len(dl.commands);sample['render_total_ms']=sum(sample[k]for k in ['parse_ms','style_ms','layout_ms','emit_ms','raster_ms']);return sample
out={}
for n in [100,1000,5000]:
 samples=[run(n)for _ in range(3)];out[n]={'samples':samples,'median':{k:statistics.median(s[k]for s in samples)for k in samples[0]}}
prof=cProfile.Profile();prof.enable();run(1000);prof.disable();buf=io.StringIO();pstats.Stats(prof,stream=buf).strip_dirs().sort_stats('cumtime').print_stats(25);(D/'cpu_profile.txt').write_text(buf.getvalue(),encoding='utf8')
(D/'pipeline_profile.json').write_text(json.dumps(out,indent=2),encoding='utf8');print(json.dumps({n:v['median']for n,v in out.items()},indent=2))
