import sys,json,time,platform,statistics
from pathlib import Path
D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parents[1]))
import browser,dom,js,css,layout
cases=json.loads((D/'js_cases.json').read_text(encoding='utf8'))
extra={
 'probe_microtasks':"console.log('start'); Promise.resolve().then(function(){console.log('promise')}); console.log('end');",
 'probe_timer_order':"setTimeout(function(){console.log('late')},30);setTimeout(function(){console.log('early')},0);console.log('sync');",
 'probe_event_bubble':"document.body.addEventListener('click',function(){console.log('body')});document.getElementById('btn').addEventListener('click',function(){console.log('button')});document.getElementById('btn').click();",
 'probe_dom_live':"var el=document.getElementById('t1');el.innerHTML='<span id=child>new</span>';console.log(document.getElementById('child').textContent);",
 'probe_json_numbers':"console.log(JSON.stringify([NaN,Infinity,undefined,-0]));",
 'probe_equality':"console.log(JSON.stringify([[]==false,[]==0,null==undefined,'0'==false,{}=== {}]));",
 'probe_api_types':"console.log(typeof fetch,typeof XMLHttpRequest,typeof MutationObserver,typeof queueMicrotask,typeof ResizeObserver);",
 'probe_fetch_contract':"console.log(typeof fetch('http://127.0.0.1:1/').then);",
 'probe_async':"async function f(){return 42} f().then(function(v){console.log(v)});",
 'probe_dom_create':"var el=document.createElement('div');el.id='added';el.textContent='hello';document.body.appendChild(el);console.log(document.getElementById('added').textContent);",
}
cases = [c for c in cases if c['id'] not in extra] + [{'id':k,'code':v} for k,v in extra.items()]
(D/'js_cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf8')
out={'python':sys.version,'platform':platform.platform(),'perf':{}}
for n in [100,1000,5000]:
 html='<html><body>'+''.join(f'<div style="width:100px;height:20px;margin:2px;background:#ccc">row {i}</div>' for i in range(n))+'</body></html>'
 samples=[]
 for k in range(3):
  t=time.perf_counter();bmp=browser.render_document(html,'',800,600);render=(time.perf_counter()-t)*1000
  browser.write_png(bmp,str(D/f'perf_{n}.png'));samples.append({'renderMs':render,'totalWithPngMs':(time.perf_counter()-t)*1000})
 out['perf'][n]=samples
try:
 import gui
 page=gui.PageInstance();page.load_html('<html><body><div style="height:2000px">long</div></body></html>',w=400,h=200)
 page.scroll_to(99999)
 out['gui_page_scroll']={'content_height':page.content_height,'max':page.scroll_max(),'actual':page.yoffset,'bitmap':[page.bitmap.width,page.bitmap.height]}
 try:
  import tkinter as tk
  root=tk.Tk();root.withdraw();app=gui.TabBrowser(root,str(D.parents[1]/'demo.html'));root.update();root.destroy();out['gui_start']='OK (hidden window initialized and updated)'
 except Exception as e:out['gui_start']=repr(e)
except Exception as e:out['gui_page_scroll']=repr(e)
(D/'toy_extra.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(out,ensure_ascii=False,indent=2))
