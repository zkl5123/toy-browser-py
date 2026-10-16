import json
from pathlib import Path
D=Path(__file__).resolve().parent;p=D/'js_cases.json';cases=json.loads(p.read_text(encoding='utf8'))
extra={
 'functional_queue_microtask':"console.log('sync');queueMicrotask(function(){console.log('micro')});console.log('end');",
 'functional_mutation_observer':"var observer=new MutationObserver(function(){console.log('mutated')});observer.observe(document.getElementById('t1'),{childList:true});document.getElementById('t1').textContent='changed';Promise.resolve().then(function(){console.log('checkpoint')});",
 'functional_resize_observer':"var observer=new ResizeObserver(function(){console.log('resized')});observer.observe(document.getElementById('t1'));",
 'functional_response_methods':"fetch('http://127.0.0.1:1/').then(function(r){console.log(typeof r.text,typeof r.json)});",
}
# A failed fetch rejects in Edge; an unhandled rejection produces no console.log.
cases=[c for c in cases if c['id']not in extra]+[{'id':k,'code':v}for k,v in extra.items()]
p.write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf8');print(len(cases),'JS cases')
