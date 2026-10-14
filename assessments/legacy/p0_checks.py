"""P0 regression checks: em-dash glyph crash + network hang + demo.html rendering."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import browser, dom, css, layout, paint, paint_engine

D = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(D, '..', '..')
results = {}

# 1) em-dash / rare char crash check (P0-1)
rare = "<html><body><p>text with \u2014 em-dash and \u2603 snowman and \u4e2d\u6587 here</p></body></html>"
try:
    t0 = time.time()
    bmp = browser.render_document(rare, '', 400, 200)
    results['rare_char_render'] = {'ok': True, 'nonwhite': sum(1 for p in bmp.pixels if p != 0xFFFFFF), 'ms': int((time.time()-t0)*1000)}
except Exception as e:
    results['rare_char_render'] = {'ok': False, 'err': f'{type(e).__name__}: {e}'}

# 2) demo.html (project's own)
demo = os.path.join(P, 'demo.html')
if os.path.exists(demo):
    try:
        t0 = time.time()
        with open(demo, encoding='utf-8', errors='replace') as f: html = f.read()
        bmp = browser.render_document(html, '', 800, 600)
        results['demo_html_render'] = {'ok': True, 'nonwhite': sum(1 for p in bmp.pixels if p != 0xFFFFFF), 'ms': int((time.time()-t0)*1000)}
    except Exception as e:
        results['demo_html_render'] = {'ok': False, 'err': f'{type(e).__name__}: {e}'}

# 3) network default timeout now finite (P0-3)
import inspect
import net
src = inspect.getsource(net.HttpClient.__init__)
results['network_default_timeout_finite'] = {'ok': ('DEFAULT_TIMEOUT' in src) or ('timeout is None' in src), 'snippet': [l.strip() for l in src.splitlines() if 'timeout' in l][:4]}

# 4) performance: 1000-element document
import time as _t
def build_doc(n):
    rows = ''.join(f'<div style="width:100px;height:20px;margin:2px;background:#ccc">row {i}</div>' for i in range(n))
    return f'<html><body>{rows}</body></html>'
for n in (1000, 5000):
    html = build_doc(n)
    t0 = _t.time()
    try:
        bmp = browser.render_document(html, '', 800, 600)
        results[f'perf_{n}'] = {'ok': True, 'ms': int((_t.time()-t0)*1000)}
    except Exception as e:
        results[f'perf_{n}'] = {'ok': False, 'err': repr(e)}

print(__import__('json').dumps(results, indent=1))
open(os.path.join(D, 'p0_results.json'), 'w', encoding='utf-8').write(__import__('json').dumps(results, indent=1))
