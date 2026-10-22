"""R8 loopback-only bounded network checks; no remote services."""
import sys,json,threading,gzip
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from toy_browser.network import net
from toy_browser.scripting import js
from toy_browser.document import dom
requests=[];results=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self):
        requests.append({'method':self.command,'path':self.path,'origin':self.headers.get('Origin')})
        n=int(self.headers.get('Content-Length','0')); self.rfile.read(min(n,4096))
        body=gzip.compress(b'x'*4096) if self.path=='/gzip' else (b'x'*4096 if self.path=='/large' else b'SYNTHETIC_R8')
        self.send_response(200)
        if self.command=='OPTIONS':
            self.send_header('Access-Control-Allow-Origin',self.headers.get('Origin',''))
            self.send_header('Access-Control-Allow-Methods','GET, POST, PUT')
            self.send_header('Access-Control-Allow-Headers','x-r8')
        if self.path=='/gzip': self.send_header('Content-Encoding','gzip')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    do_GET=respond;do_POST=respond;do_PUT=respond;do_OPTIONS=respond
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
try:
    for label,code in [('fetch_denied',f'fetch("{base}/simple").then(function(r){{console.log(r.type+":"+r.status);r.text().then(function(t){{console.log(t);}});}}).catch(function(e){{console.log("REJECT");}});'),('preflight_actual_missing_acao',f'fetch("{base}/custom",{{method:"PUT",headers:{{"x-r8":"1"}},body:"small"}}).then(function(r){{console.log(r.type+":"+r.status);r.text().then(function(t){{console.log(t);}});}}).catch(function(e){{console.log("REJECT");}});')]:
        logs=[];h=js.Host(dom.parse('<p>x'),logs.append);h.doc_url=f'http://localhost:{server.server_port}/page';h.doc_origin=net._origin_str(h.doc_url);c=net.HttpClient(timeout=2);h._fetch_transport=net.FetchTransport(c,h.doc_url);start=len(requests)
        try:h.execute(code);h.flush_microtasks();results.append({'id':label,'logs':logs,'requests':requests[start:]})
        finally:c.close_pool()
    h=js.Host(dom.parse('<p>x'),lambda s:None);h.doc_url=f'http://localhost:{server.server_port}/page';c=net.HttpClient(timeout=2);h._fetch_transport=net.FetchTransport(c,h.doc_url);start=len(requests)
    try:
        x=h._xhr_ctor()();x.open('POST',base+'/xhr');x.send('small');results.append({'id':'xhr_real_transport','status':x.status,'text':x.responseText,'requests':requests[start:],'scope':'Python XHR WebAPI with real loopback transport'})
    finally:c.close_pool()
    for path,method in [('/gzip','GET'),('/large','POST')]:
        c=net.HttpClient(timeout=2,max_response_bytes=128)
        try:
            r=c.get(base+path) if method=='GET' else c.post(base+path,data='small');results.append({'id':'cap_'+method,'bytes':len(r.body)})
        except Exception as e:results.append({'id':'cap_'+method,'exception':type(e).__name__})
        finally:c.close_pool()
finally:server.shutdown();server.server_close()
Path(__file__).with_name('network-probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(results,ensure_ascii=False,indent=2))
