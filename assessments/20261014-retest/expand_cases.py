import json
from pathlib import Path
D=Path(__file__).resolve().parent
p=D/'layout_cases.json';cases=json.loads(p.read_text(encoding='utf8'))
for name,extra,count in [
 ('flex_reverse','flex-direction:row-reverse;',3),
 ('flex_reverse_center','flex-direction:row-reverse;justify-content:center;',3),
 ('flex_reverse_end','flex-direction:row-reverse;justify-content:flex-end;',3),
 ('flex_gap','gap:10px;',3),
 ('flex_single_around','justify-content:space-around;',1),
 ('flex_single_evenly','justify-content:space-evenly;',1),
 ('flex_padding_border','justify-content:center;',3),
 ('flex_column','flex-direction:column;justify-content:center;',3),
]:
 item='width:40px;height:20px;margin:4px;background:#f00;'
 if name=='flex_padding_border':item+='padding:5px;border:2px solid blue;'
 cases[name]='<!DOCTYPE html><html><head><style>body{margin:0}#row{display:flex;width:300px;height:150px;'+extra+'}#row>div{'+item+'}</style></head><body><div id="row">'+''.join(f'<div id="c{i}"></div>' for i in range(1,count+1))+'</div></body></html>'
cases['grid_unequal']='<!DOCTYPE html><html><head><style>body{margin:0}#g{display:grid;width:300px;grid-template-columns:1fr 2fr;gap:10px}#g>div{height:20px;background:red}</style></head><body><div id="g"><div id="g1"></div><div id="g2"></div></div></body></html>'
cases['percent_height']='<!DOCTYPE html><html><body style="margin:0"><div id="p" style="height:200px;width:200px"><div id="c" style="height:50%;width:50%;background:red"></div></div></body></html>'
p.write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf8')
p=D/'js_cases.json';cases=json.loads(p.read_text(encoding='utf8'));extra={
 'probe_const_write':"try{const a=1;a=2;console.log('changed',a)}catch(e){console.log('caught')}",
 'probe_tdz':"try{console.log(x);let x=1}catch(e){console.log('caught')}",
 'probe_reference_error':"try{console.log(noSuchBinding)}catch(e){console.log('caught')}",
 'probe_array_json':"console.log(JSON.stringify([1,2,3].map(function(v){return v*2})));",
 'probe_object_identity':"var a={};var b={};console.log(a===a,a===b);",
 'probe_remove_dom':"var el=document.getElementById('t1');el.parentNode.removeChild(el);console.log(document.getElementById('t1')===null);",
 'probe_unsupported_css':"console.log(typeof CSS,typeof CSS.supports);",
 'probe_string_timer':"setTimeout(\"console.log('timer')\",0);console.log('sync');",
}
cases=[c for c in cases if c['id'] not in extra]+[{'id':k,'code':v}for k,v in extra.items()]
p.write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf8')
print(len(cases),'JS cases;',len(json.loads((D/'layout_cases.json').read_text(encoding='utf8'))),'layout cases')
