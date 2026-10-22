"""R8 bounded probes. No external network, OS GUI, or product writes."""
import sys,json,subprocess,importlib.util
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from toy_browser.document import dom
from toy_browser.network import net, request_context as rc
from toy_browser.scripting import js,web_api
D=Path(__file__).parent
results=[]
def record(name,fn):
    try: results.append({'id':name,'actual':fn()})
    except Exception as e: results.append({'id':name,'probe_error':repr(e)})
def host(url='http://a.example.test/'):
    logs=[]; h=js.Host(dom.parse('<html><body></body></html>'),logs.append); h.doc_url=url; h.doc_origin=net._origin_str(url); return h,logs
class Client:
    def __init__(self): self.calls=[]
    def get(self,url,**kw): self.calls.append({'method':'GET','kwargs':str(kw)}); return net.HttpResponse(200,'OK',{'x-private':'synthetic'},b'synthetic',str(url))
    def post(self,url,**kw): self.calls.append({'method':'POST','kwargs':str(kw)}); return net.HttpResponse(200,'OK',{},b'synthetic',str(url))
def cors():
    ctx=rc.context_for_destination('http://a.example.test','http://b.example.test/x',rc.DEST_FETCH)
    denied=rc.validate_preflight_response(ctx,{})
    allowed=rc.validate_preflight_response(ctx,{'access-control-allow-origin':'http://a.example.test','x-private':'synthetic'})
    return {'denied_type':denied.resolved_type,'denied_allowed':denied.allowed,'allowed_visible_headers':allowed.visible_headers}
record('cors_response_contract',cors)
def fetch_contract():
    h,logs=host(); c=Client(); h._fetch_transport=net.FetchTransport(c,h.doc_url)
    h.execute('fetch("http://b.example.test/x").then(function(r){console.log("resolved:"+r.type+":"+r.status);}).catch(function(e){console.log("rejected");});'); h.flush_microtasks()
    return {'logs':logs,'calls':c.calls}
record('fetch_missing_acao',fetch_contract)
def xhr_contract():
    h,logs=host(); c=Client(); h._fetch_transport=net.FetchTransport(c,h.doc_url)
    x=h._xhr_ctor()(); x.open('POST','http://b.example.test/x'); x.send('body')
    return {'status':x.status,'text':x.responseText,'calls':c.calls,'note':'Python WebAPI boundary with injected raw-response client, not full HttpClient network test'}
record('xhr_boundary',xhr_contract)
def storage():
    a,_=host(); b,_=host(); a.globals['localStorage'].setItem('r8-key','value')
    before=b.globals['localStorage'].getItem('r8-key')
    web_api.register_web_api(a); web_api.register_web_api(b)
    a.globals['localStorage'].setItem('r8-key','bound')
    return {'assigned_origin_only':before,'after_reinstall':b.globals['localStorage'].getItem('r8-key')}
record('storage_registration_lifecycle',storage)
def timeout_path():
    spec=importlib.util.spec_from_file_location('r8_runner',ROOT/'tests/run.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    with patch('platform.system',return_value='Windows'), patch.object(m.subprocess,'run',side_effect=subprocess.TimeoutExpired('synthetic',1)):
        try: return m._run_tcl_isolated(['synthetic'])
        except Exception as e: return {'exception':type(e).__name__,'message':str(e)}
record('runner_timeout_path',timeout_path)
def parse_modes():
    return {str(mode):[n.tag for n in dom.parse('<p>x',document_mode=mode).children] for mode in [None,True,False]}
record('parse_modes',parse_modes)
if '--gui' in sys.argv:
    from toy_browser.app.gui import PageInstance
    def reload_policy():
        p=PageInstance(); p.width=80;p.height=40
        html='<html><body><p id="marker">before</p><script>document.getElementById("marker").textContent="after";</script></body></html>'
        response=net.HttpResponse(200,'OK',{'content-security-policy':"script-src 'none'"},html.encode(),'https://untrusted.example.test/')
        with patch.object(net,'fetch_html',return_value=(html,response)):
            p._load_no_history('https://untrusted.example.test/',80,40)
        def walk(n):
            yield n
            for child in n.children: yield from walk(child)
        marker=next(n for n in walk(p._dom_tree) if n.is_element() and n.attr('id')=='marker')
        return {'text':marker.text_content(),'host_url':getattr(p._script_host,'doc_url',None),'trust':str(p.last_script_trust_decision)}
    record('gui_history_reload_policy',reload_policy)
    def history():
        p=PageInstance(); p.load_file('demo.html',100,60); original=p.url; p.back_stack.append(p.url); p.fwd_stack.clear(); p.load_url('about:blank',100,60); p.go_back(); back=p.url;p.go_forward()
        return {'initial':original,'back':back,'forward':p.url,'note':'Replay of legacy test that manually mutates history projection'}
    record('legacy_history_failure',history)
(D/'current-probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
