import sys,json,threading,subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
D=Path(__file__).resolve().parent;sys.path.insert(0,str(D.parents[1]));import browser
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  self.send_response(200)
  if self.path=='/data.json':
   self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'NETWORK_OK');return
  if self.path=='/external.js':
   self.send_header('Content-Type','application/javascript');self.end_headers();self.wfile.write(b"document.getElementById('probe').textContent='EXECUTED';");return
  if self.path=='/append.js':
   self.send_header('Content-Type','application/javascript');self.end_headers();self.wfile.write(b"document.getElementById('probe').textContent+='|EXTERNAL';");return
  if self.path=='/style.css':
   self.send_header('Content-Type','text/css')
   if self.server.cors:self.send_header('Access-Control-Allow-Origin','*')
   self.end_headers();self.wfile.write(b'#probe{background:#00ff00}');return
  self.send_header('Content-Type','text/html; charset=utf-8')
  if self.path=='/csp_self':self.send_header('Content-Security-Policy',"style-src 'self'")
  if self.path=='/csp_inline':self.send_header('Content-Security-Policy',"script-src 'none'")
  if self.path=='/csp_nonce':self.send_header('Content-Security-Policy',"script-src 'nonce-good'")
  self.end_headers()
  port=resource.server_port if self.path=='/cross_css' else cors_resource.server_port
  link=f'<link rel="stylesheet" href="http://127.0.0.1:{port}/style.css">' if self.path in ('/cross_css','/csp_self') else ''
  script="<script>document.getElementById('probe').textContent='EXECUTED'</script>" if self.path=='/csp_inline' else ''
  if self.path=='/external_script':script='<script src="/external.js"></script>'
  if self.path=='/script_order':script='<script src="/append.js"></script><script>document.getElementById("probe").textContent+="|INLINE";</script>'
  if self.path=='/repeat_external':script='<script src="/append.js"></script><script src="/append.js"></script>'
  if self.path=='/csp_nonce':script='<script nonce="good">document.getElementById("probe").textContent="EXECUTED";</script>'
  if self.path=='/fetch_real':script=f'<script>fetch("http://127.0.0.1:{self.server.server_port}/data.json").then(function(r){{document.getElementById("probe").textContent=r.status+":"+typeof r.text;}});</script>'
  if self.path=='/xhr_real':script='<script>var xhr=new XMLHttpRequest();xhr.open("GET","/data.json");xhr.onload=function(){document.getElementById("probe").textContent=xhr.status+":"+xhr.responseText;};xhr.send();</script>'
  if self.path=='/data_script':script='<script type="application/json">document.getElementById("probe").textContent="EXECUTED";</script>'
  html=f'<!DOCTYPE html><html><head>{link}</head><body><div id="probe" style="width:100px;height:100px">SAFE</div>{script}</body></html>'
  self.wfile.write(html.encode())
servers=[]
for cors in [False,False,True]:
 s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.cors=cors;threading.Thread(target=s.serve_forever,daemon=True).start();servers.append(s)
main,resource,cors_resource=servers;base=f'http://127.0.0.1:{main.server_port}'
out={}
try:
 subprocess.run(['node',str(D/'network_reference.cjs'),base],check=True,timeout=45)
 for name in ['cross_css','csp_self','csp_inline','external_script','script_order','csp_nonce','repeat_external','fetch_real','xhr_real','data_script']:
  import js
  from unittest.mock import patch
  captured=[]
  original=js.Host.execute_scripts_in_order
  def record(host,*args,**kwargs):
   result=original(host,*args,**kwargs)
   host.flush_timers()
   host.flush_microtasks()
   el=host.root.find_by_id('probe')
   if el is not None:captured.append(el.text_content())
   return result
  with patch.object(js.Host,'execute_scripts_in_order',record):
   bmp=browser.render_document_from_url(base+'/'+name,width=400,height=200)
  browser.write_png(bmp,str(D/f'toy_{name}.png'))
  import gui
  page=gui.PageInstance();page.load_url(base+'/'+name,w=400,h=200)
  sh=getattr(page,"_script_host",None)
  if sh is not None:sh.flush_timers();sh.flush_microtasks()
  node=getattr(page,'_dom_tree',None)
  probe=node.find_by_id('probe') if node is not None else None
  browser.write_png(page.bitmap,str(D/f'gui_{name}.png'))
  out[name]={'pixel_50_50':hex(bmp.pixels[50*400+50]),'cli_text':captured[-1] if captured else None,'gui_text':probe.text_content() if probe is not None else None,'gui_pixel_50_50':hex(page.bitmap.pixels[50*400+50])}
 (D/'toy_network_e2e.json').write_text(json.dumps(out,indent=2),encoding='utf8');print(out)
finally:
 for s in servers:s.shutdown();s.server_close()
