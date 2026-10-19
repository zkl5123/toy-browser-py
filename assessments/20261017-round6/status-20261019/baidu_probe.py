"""Live Baidu probes. Run each mode in a bounded subprocess; no engine patches."""
from pathlib import Path
import sys, time, json, hashlib, traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from toy_browser.network import net
from toy_browser.app import browser, gui

OUT = Path(__file__).resolve().parent
MODE = sys.argv[1]
URL = sys.argv[2] if len(sys.argv) > 2 else 'https://www.baidu.com/'
records = []

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

class Client(net.HttpClient):
    def get(self, url, *args, **kwargs):
        if MODE.endswith('-ua'):
            kwargs['headers'] = dict(kwargs.get('headers') or {}, **{'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36'})
        t = time.perf_counter()
        entry = {'url': str(url)}
        try:
            response = super().get(url, *args, **kwargs)
            entry.update(status=response.status, final_url=response.final_url,
                         bytes=len(response.body), headers=dict(response.headers),
                         sha256=hashlib.sha256(response.body).hexdigest())
            if 'text/html' in str(response.headers).lower() and not records:
                (OUT / f'{MODE}-response.html').write_bytes(response.body)
            return response
        except Exception as exc:
            entry['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            entry['elapsed_ms'] = round((time.perf_counter()-t)*1000, 2)
            records.append(entry)
            save(f'{MODE}-requests.json', records)

result = {'mode': MODE, 'url': URL, 'viewport': [800, 600], 'time': time.strftime('%Y-%m-%d %H:%M:%S')}
t = time.perf_counter()
client = Client(timeout=8)
try:
    if MODE.startswith('fetch'):
        html, response = net.fetch_html(URL, client=client)
        result.update(status=response.status, final_url=response.final_url, characters=len(html),
                      html_prefix=html[:300])
    elif MODE.startswith('cli'):
        bitmap = browser.render_document_from_url(URL, width=800, height=600, client=client)
        browser.write_png(bitmap, str(OUT/f'{MODE}.png'))
        result.update(rendered=True, size=[bitmap.width, bitmap.height])
    else:
        page = gui.PageInstance()
        page.load_url(URL, w=800, h=600, client=client)
        if page.bitmap:
            browser.write_png(page.bitmap, str(OUT/f'{MODE}.png'))
        nodes=[]
        def walk(node):
            if node.is_element():
                tag=(node.tag or '').lower()
                if tag in ('form','input','textarea','button','script','link','img','title'):
                    nodes.append({'tag':tag,'id':node.attr('id'),'name':node.attr('name'),
                                  'type':node.attr('type'),'src':node.attr('src'),
                                  'href':node.attr('href'),'action':node.attr('action'),
                                  'text':node.text_content()[:120] if tag in ('title','button') else ''})
            for child in node.children:walk(child)
        if page._dom_tree:walk(page._dom_tree)
        result.update(page_url=page.url,title=page.title,content_height=page.content_height,
                      rendered=page.bitmap is not None,nodes=nodes,
                      text=page._dom_tree.text_content()[:2000] if page._dom_tree else '',
                      source_characters=len(page._last_html),focus_chain=page.focus_chain)
    result['outcome']='completed'
except Exception as exc:
    result.update(outcome='error',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
finally:
    result['elapsed_ms']=round((time.perf_counter()-t)*1000,2)
    result['request_count']=len(records)
    save(f'{MODE}-result.json',result)
    client.close_pool()
    print(json.dumps(result,ensure_ascii=False))
