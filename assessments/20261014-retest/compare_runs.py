import sys,json,hashlib
from pathlib import Path
D=Path(__file__).resolve().parent;old=D.parent/'20261014';root=D.parents[1]
def read(p):return json.loads(p.read_text(encoding='utf8'))
now=read(D/'summary.json');prev=read(old/'summary.json')
case_matches={k:all(v['max_delta_px']<=1 for v in items.values())for k,items in now['layout'].items()}
element_matches=sum(v['max_delta_px']<=1 for items in now['layout'].values()for v in items.values());element_total=sum(len(items)for items in now['layout'].values())
common_js={k:now['js'][k]['match']for k in prev['js']}
out={'js_current':{'match':now['js_match'],'total':now['js_total'],'both_error':now['js_both_error']},'js_same_39':{'previous':prev['js_match'],'current':sum(common_js.values())},'layout_current':{'matched_cases':sum(case_matches.values()),'cases':len(case_matches),'matched_elements':element_matches,'elements':element_total,'case_matches':case_matches},'layout_same_7':{'previous':sum(all(v['max_delta_px']<=1 for v in items.values())for items in prev['layout'].values()),'current':sum(case_matches[k]for k in prev['layout'])},'source_hashes':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()for p in sorted((root/'toy_browser').rglob('*.py'))}}
(D/'comparison.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in out.items()if k!='source_hashes'},ensure_ascii=False,indent=2))
