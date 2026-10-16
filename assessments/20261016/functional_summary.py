import json
from pathlib import Path
D=Path(__file__).resolve().parent
read=lambda n:json.loads((D/(n+'.json')).read_text(encoding='utf8'))
edge,toy=read('edge_js_results'),read('toy_js_results');old=read('comparison')['js_same_47']
baseline=set(json.loads((D.parent/'20261015/js_cases.json').read_text(encoding='utf8'))[i]['id']for i in range(47))
def match(k,strip=False):
 a,b=edge[k],toy[k];logs=lambda v:[s.strip() for s in v['logs']]if strip else v['logs']
 return (a['status']=='OK')==(b['status']=='OK')and logs(a)==logs(b)and not b.get('drain_error')
out={'original_47_raw_matches':sum(match(k)for k in baseline),'original_47_trimmed_matches':sum(match(k,True)for k in baseline),'all_raw_matches':sum(match(k)for k in edge),'all_trimmed_matches':sum(match(k,True)for k in edge),'total':len(edge),'added_functional':{k:{'match':match(k),'edge':edge[k],'toy':toy[k]}for k in edge if k not in baseline},'drain_errors':{k:v['drain_error']for k,v in toy.items()if v.get('drain_error')}}
now=read('toy_network_e2e');ref=read('edge_network_e2e');cases={}
for k in ref:
 expected=ref[k]['text'];ok=now[k]['cli_text']==expected
 guiok=now[k]['gui_text']==expected
 if k in ('cross_css','csp_self'):
  expected_pixel='0xff00'if k=='cross_css'else'0xffffff'
  ok=ok and now[k]['pixel_50_50']==expected_pixel;guiok=guiok and now[k]['gui_pixel_50_50']==expected_pixel
 cases[k]={'cli_match':ok,'gui_match':guiok,'edge':ref[k],'toy':now[k]}
out['http_cases']=cases;out['http_cli_matches']=sum(v['cli_match']for v in cases.values());out['http_gui_matches']=sum(v['gui_match']for v in cases.values())
(D/'functional_summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in out.items()if k not in ('added_functional','http_cases')},ensure_ascii=False,indent=2))
