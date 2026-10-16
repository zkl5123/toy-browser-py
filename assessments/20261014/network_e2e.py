import sys,json,threading,subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
D=Path(__file__).resolve().parent;sys.path.insert(0,str(D.parents[1]));from toy_browser.app import browser
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  self.send_response(200)
  if self.path=='/style.css':
   self.send_header('Content-Type','text/css')
   if self.server.cors:self.send_header('Access-Control-Allow-Origin','*')
   self.end_headers();self.wfile.write(b'#probe{background:#00ff00}');return
  self.send_header('Content-Type','text/html; charset=utf-8')
  if self.path=='/csp_self':self.send_header('Content-Security-Policy',"style-src 'self'")
  if self.path=='/csp_inline':self.send_header('Content-Security-Policy',"script-src 'none'")
  self.end_headers()
  port=resource.server_port if self.path=='/cross_css' else cors_resource.server_port
  link=f'<link rel="stylesheet" href="http://127.0.0.1:{port}/style.css">' if self.path!='/csp_inline' else ''
  script="<script>document.getElementById('probe').textContent='EXECUTED'</script>" if self.path=='/csp_inline' else ''
  html=f'<!DOCTYPE html><html><head>{link}</head><body><div id="probe" style="width:100px;height:100px">SAFE</div>{script}</body></html>'
  self.wfile.write(html.encode())
servers=[]
for cors in [False,False,True]:
 s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.cors=cors;threading.Thread(target=s.serve_forever,daemon=True).start();servers.append(s)
main,resource,cors_resource=servers;base=f'http://127.0.0.1:{main.server_port}'
out={}
try:
 subprocess.run(['node',str(D/'network_reference.cjs'),base],check=True,timeout=45)
 for name in ['cross_css','csp_self','csp_inline']:
  bmp=browser.render_document_from_url(base+'/'+name,width=400,height=200);browser.write_png(bmp,str(D/f'toy_{name}.png'))
  out[name]={'pixel_50_50':hex(bmp.pixels[50*400+50])}
 (D/'toy_network_e2e.json').write_text(json.dumps(out,indent=2),encoding='utf8');print(out)
finally:
 for s in servers:s.shutdown();s.server_close()
