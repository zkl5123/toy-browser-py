"""第七轮安全探针：仅回环 HTTP 与内存对象，响应不超过 4 KiB。"""
from __future__ import annotations
import gzip
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from toy_browser.document import dom
from toy_browser.network import net
from toy_browser.network.cookie_store import CookieJar, parse_set_cookie
from toy_browser.scripting import js, web_api

RESULTS = []
REQUESTS = []

def record(name, expected, fn):
    try:
        actual = fn()
        RESULTS.append({'id': name, 'expected': expected, 'actual': actual})
    except BaseException as exc:
        RESULTS.append({'id': name, 'expected': expected, 'probe_exception': type(exc).__name__, 'message': str(exc)})

def host(html='<html><body></body></html>', url='https://a.example.test/index'):
    logs = []
    h = js.Host(dom.parse(html), logs.append)
    h.doc_url = url
    h.doc_origin = net._origin_str(url)
    h._js_step_budget = 20000
    return h, logs

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        REQUESTS.append({'method': 'GET', 'path': self.path, 'cookie': self.headers.get('Cookie'), 'origin': self.headers.get('Origin')})
        body = b'LOCAL_SYNTHETIC_DATA'
        self.send_response(200)
        if self.path == '/gzip':
            body = gzip.compress(b'x' * 4096)
            self.send_header('Content-Encoding', 'gzip')
        if self.path == '/cookies':
            self.send_header('Set-Cookie', 'first=1; Path=/')
            self.send_header('Set-Cookie', 'second=2; Path=/')
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        REQUESTS.append({'method': 'POST', 'path': self.path})
        body = b'p' * 4096
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Set-Cookie', 'posted=1; Path=/')
        self.end_headers()
        self.wfile.write(body)

class MemoryClient:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.progress = None
    def get(self, url, **kwargs):
        self.requests.append(url.to_string() if isinstance(url, net.URL) else str(url))
        return self.response

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f'http://127.0.0.1:{server.server_port}'
try:
    def host_read():
        h, logs = host()
        h.execute('console.log(location._host.doc_url); console.log(localStorage.__class__.__name__); console.log(fetch.__self__._js_step_budget);')
        return logs
    record('host_private_attributes', '页面不能读 Python 宿主私有属性或类型元数据', host_read)

    def fetch_read():
        h, logs = host(url=f'http://localhost:{server.server_port}/page')
        jar = CookieJar()
        jar.ingest_response(base, {'set-cookie': 'strict_marker=synthetic; SameSite=Strict'})
        client = net.HttpClient(cookies=jar, timeout=2)
        h._fetch_transport = net.FetchTransport(client, h.doc_url)
        try:
            h.execute(f'fetch("{base}/data").then(function(r){{console.log(r.status); console.log(r._text_value); r.text().then(function(t){{console.log("text-ok:"+t);}}).catch(function(e){{console.log("text-reject:"+e);}});}});')
            h.flush_microtasks()
            return {'logs': logs, 'request': REQUESTS[-1]}
        finally:
            client.close_pool()
    record('cross_origin_fetch_and_samesite', '无 ACAO 的跨源 fetch 不可读取响应，跨站子请求不发送 Strict Cookie', fetch_read)

    def xhr_read():
        h, logs = host(url='https://a.example.test/page')
        client = net.HttpClient(timeout=2)
        h._fetch_transport = net.FetchTransport(client, h.doc_url)
        try:
            h.execute(f'var x=new XMLHttpRequest(); x.open("POST","{base}/xhr"); x.onload=function(){{console.log(x.responseText);}}; x.send("synthetic");')
            return {'logs': logs, 'request': REQUESTS[-1]}
        finally:
            client.close_pool()
    record('cross_origin_xhr_method', '页面不能读取缺失 ACAO 的跨源 XHR，POST 不得变 GET；回环例外不用于论证混合内容', xhr_read)

    def connect_none():
        h, logs = host(f'<html><body><script>fetch("{base}/connect-none").then(function(r){{console.log(r.status);}});</script></body></html>')
        client = net.HttpClient(timeout=2)
        h._fetch_transport = net.FetchTransport(client, h.doc_url)
        try:
            h.execute_scripts_in_order({}, net.parse_csp_directives("script-src 'unsafe-inline'; connect-src 'none'"))
            return {'logs': logs, 'request': REQUESTS[-1]}
        finally:
            client.close_pool()
    record('csp_connect_none', 'connect-src none 必须在发请求前阻止连接', connect_none)

    def resource_case(html, csp, policy_origin='https://a.example.test'):
        tree = dom.parse(html)
        c = MemoryClient(net.HttpResponse(200, 'OK', {}, b'console.log(1)', 'https://b.example.test/a.js'))
        result = net.load_subresources(tree, 'https://a.example.test/page', client=c, policy=net.DocumentPolicy(policy_origin, net.parse_csp_directives(csp)))
        return {'requests': c.requests, 'allowed': {k: {u: r is not None for u, r in v.items()} for k, v in result.items()}}
    record('csp_denied_still_requested', 'img-src none 不发起资源请求', lambda: resource_case('<img src="https://b.example.test/pixel">', "img-src 'none'"))
    record('csp_self_same_origin_script', '同源 classic script 在 script-src self 下无需 ACAO', lambda: resource_case('<script src="/a.js"></script>', "script-src 'self'"))
    record('classic_cross_origin_script', '无 crossorigin 的 classic script 默认 no-cors，可执行跨源脚本', lambda: resource_case('<script src="https://b.example.test/a.js"></script>', ''))
    record('base_changes_self_origin', 'base URL 只能改变解析基址，不能改变 CSP self 的文档来源', lambda: resource_case('<base href="https://b.example.test/"><img src="/x">', "img-src 'self'"))
    record('csp_empty_directive', '显式空 script-src 应禁止所有脚本', lambda: net.DocumentPolicy('https://a.example.test', net.parse_csp_directives('script-src')).csp_allowed('script', 'https://b.example.test/x.js'))
    record('csp_duplicate_directive', '重复指令应保留首次指令', lambda: net.parse_csp_directives("script-src 'none'; script-src 'unsafe-inline'"))

    def inline_event():
        h, logs = host('<html><body><button id="b" onclick="console.log(123)">b</button></body></html>')
        h.execute_scripts_in_order({}, {'script-src': ["'none'"]})
        h.execute_event('b')
        return logs
    record('csp_inline_event', 'script-src none 也应阻止 HTML 内联事件处理器', inline_event)

    def cookie_checks():
        jar = CookieJar()
        jar.ingest_response('https://a.example.test/private/page', {'set-cookie': ['hostonly=1', 'super=1; Domain=test', 'scoped=1; Path=/private', 'bad=1; SameSite=invalid']})
        return {'subdomain': jar.cookie_header_for('https://sub.a.example.test/'), 'sibling': jar.cookie_header_for('https://b.example.test/'), 'cross_post': jar.cookie_header_for('https://a.example.test/', is_cross_site=True, method='POST', is_top_level=False), 'for_url_public': [c.name for c in jar.for_url('https://a.example.test/public')], 'http_secure_accepted': parse_set_cookie('__Secure-a=1; Secure', 'http://not-local.example.test/') is not None}
    record('cookie_scope_validation', 'host-only 不下发子域；公共后缀拒绝；for_url 尊重 Path；无效 SameSite 默认 Lax；HTTP 不可设置 Secure', cookie_checks)

    def limits():
        jar = CookieJar()
        client = net.HttpClient(timeout=2, cookies=jar, max_response_bytes=128)
        try:
            gz = client.get(base + '/gzip')
            post = client.post(base + '/post')
            client.get(base + '/cookies')
            return {'cap': 128, 'gzip_decoded_bytes': len(gz.body), 'post_bytes': len(post.body), 'cookie_names': [c.name for c in jar.all()]}
        finally:
            client.close_pool()
    record('response_limits_and_cookie_ingest', '解压及 POST 响应遵守 128 字节上限；POST 与多个 Set-Cookie 都应入库', limits)

    def bounded_read():
        calls = []
        class Reply:
            status = 200
            reason = 'OK'
            def getheader(self, name): return None
            def getheaders(self): return []
            def read(self, *args):
                calls.append(args)
                return b'x' * 129
        class Conn:
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Reply()
        client = net.HttpClient(max_response_bytes=128)
        with patch.object(client, '_acquire_conn', return_value=Conn()):
            try: client.get('http://synthetic.example.test/')
            except net.ResponseTooLarge: pass
        return {'read_arguments': calls}
    record('read_before_cap', '读取时应传剩余额度，不能先 read 全部再判断', bounded_read)

    def storage():
        h1, _ = host()
        h2, _ = host()
        h1.globals['localStorage'].setItem('synthetic', 'one')
        with patch.object(web_api, '_QUOTA_BYTES', 16):
            st = web_api.Storage()
            try: st.setItem('k', 'x' * 16)
            except Exception as exc: quota = type(exc).__name__
            else: quota = 'not blocked'
        return {'same_origin_new_host_value': h2.globals['localStorage'].getItem('synthetic'), 'quota_error': quota}
    record('storage_lifecycle_and_quota', '同源文档应共享 localStorage；setItem 超额应抛 QuotaExceededError', storage)

    def event_order():
        h, logs = host('<html><body><script>console.log("sync"); Promise.resolve().then(function(){console.log("micro");}); setTimeout(function(){console.log("timer");},0);</script><script>console.log("next-script");</script></body></html>')
        h.execute_scripts_in_order({})
        return logs
    record('event_loop_order', '脚本间有微任务检查点；同步脚本后微任务先于 timer', event_order)

    def b03():
        h, logs = host('<html><body><script>var broken=,;</script><script>console.log("later-runs");</script></body></html>')
        h.execute_scripts_in_order({})
        return {'logs': logs, 'script_errors': h.script_errors}
    record('b03_parse_boundary', '语法错误隔离，后续脚本继续执行并结构化记录错误', b03)
    def b03_runtime():
        h, logs = host('<html><body><script>throw new Error("synthetic");</script><script>console.log("later-runs");</script></body></html>')
        h.execute_scripts_in_order({})
        return {'logs': logs, 'script_errors': h.script_errors}
    record('b03_runtime_observability', '运行时失败也应进入结构化 script_errors，后续脚本继续执行', b03_runtime)
    def disabled_subresources():
        from toy_browser.app import browser
        logs = []
        original = js.Host
        def captured(root, unused_log):
            return original(root, logs.append)
        c = MemoryClient(net.HttpResponse(200, 'OK', {'content-type': 'text/html', 'content-security-policy': "script-src 'none'"}, b'<html><body><script>console.log("INLINE_RAN");</script></body></html>', 'https://a.example.test/page'))
        with patch.object(js, 'Host', side_effect=captured):
            browser.render_document_from_url('https://a.example.test/page', width=20, height=20, load_subresources=False, client=c)
        return logs
    record('disable_subresources_disables_inline_csp', '关闭子资源下载不得关闭文档的内联 CSP', disabled_subresources)
    def b05():
        h, logs = host()
        h.execute('location.replace("/next");')
        return [{'target': r.target, 'replace': getattr(r, 'replace', None), 'kind': r.kind()} for r in h._pending_nav]
    record('b05_location_queue', 'location.replace 生成真实导航请求而非仅修改 dict', b05)
    def budget():
        h, logs = host()
        h._js_step_budget = 100
        h.execute('var s=0; for(var i=0;i<1000;i++){s+=i;}')
        return logs
    record('bounded_js_budget', '有限循环在低预算下被取消，不阻塞宿主', budget)
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

out = Path(__file__).with_name('security-probes.json')
out.write_text(json.dumps({'commit': '551da74', 'safety': '仅回环 HTTP、4KiB 以下响应、宿主仅无害属性读取，无个人数据与破坏载荷', 'results': RESULTS}, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(json.dumps({'output': str(out), 'count': len(RESULTS), 'probe_exceptions': [r['id'] for r in RESULTS if 'probe_exception' in r]}, ensure_ascii=False))
