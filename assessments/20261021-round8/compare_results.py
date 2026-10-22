"""Summarize current replays versus explicitly historical Edge baselines."""
from pathlib import Path
import json
D=Path(__file__).resolve().parent
load=lambda n:json.loads((D/n).read_text(encoding='utf-8'))
a,b=load('toy_js_results.json'),load('edge_js_results.json')
js=[]
for k,v in a.items():
    r=b[k]
    js.append({'id':k,'logs_raw_match':v['logs']==r['logs'],'logs_trim_match':[s.strip() for s in v['logs']]==[s.strip() for s in r['logs']],'status_exact_match':v['status']==r['status'],'status_class_match':v['status'].split(':')[0]==r['status'].split(':')[0],'drain_error':v.get('drain_error')})
a,b=load('toy_layout_results.json'),load('edge_layout_results.json')
boxes=[]
for case,vs in a.items():
    for id_,v in vs.items():
        ref=b[case].get(id_)
        errors={k:abs(v[k]-ref[k]) for k in ['x','y','w','h']} if v and ref else None
        boxes.append({'case':case,'id':id_,'actual':v,'reference':ref,'errors':errors,'within_1px':errors is not None and max(errors.values())<=1})
sem=load('semantic-probes.json')
inv=load('source-inventory.json')
summary={'baseline_warning':'Edge measurements were produced in R5 (2026-10-16), not freshly run in R8. Current project results re-executed. Pure-JS Node reference is fresh.','js':{'total':len(js),'raw_logs_match':sum(x['logs_raw_match'] for x in js),'trim_logs_match':sum(x['logs_trim_match'] for x in js),'status_exact_match':sum(x['status_exact_match'] for x in js),'status_class_match':sum(x['status_class_match'] for x in js),'cases':js},'layout':{'cases':len(a),'boxes':len(boxes),'within_1px':sum(x['within_1px'] for x in boxes),'details':boxes},'fresh_ecmascript':{'total':sem['total'],'matched':sem['matched'],'mismatches':[x for x in sem['cases'] if not x['match']]},'inventory':{base:{'files':sum(x['path'].startswith(base+'/') for x in inv),'lines':sum(x['lines'] for x in inv if x['path'].startswith(base+'/'))} for base in ['toy_browser','tests','tools']}}
(D/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:({p:q for p,q in v.items() if p not in ['cases','details','mismatches']} if isinstance(v,dict) else v) for k,v in summary.items()},ensure_ascii=False,indent=2))
print('JS mismatches:',[(x['id'],x['reference'],x['actual']) for x in sem['cases'] if not x['match']])
print('Layout mismatches:',[x for x in boxes if not x['within_1px']])
