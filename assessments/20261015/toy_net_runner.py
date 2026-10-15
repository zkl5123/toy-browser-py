"""toy-browser network stack: redirect / loop / gzip / 404 / subresources / cache / CORS."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net
from net_server import start

httpd, PORT = start(8977)
base = f'http://127.0.0.1:{PORT}'
results = {}

# 1) redirect chain -> /two.html, with subresources (style.css)
try:
    t0 = time.time()
    client = net.HttpClient()
    html, resp = net.fetch_html(base + '/redirect', client=client, follow_redirects=True)
    results['redirect_follows'] = {'ok': True, 'final_url': resp.final_url, 'has_body': 'page two' in html, 'ms': int((time.time()-t0)*1000)}
except Exception as e:
    results['redirect_follows'] = {'ok': False, 'err': repr(e)}

# 2) redirect loop detection
try:
    client = net.HttpClient()
    net.fetch_html(base + '/redirect_loop', client=client, follow_redirects=True)
    results['redirect_loop_detected'] = {'ok': False, 'note': 'no exception raised'}
except net.RedirectLoop as e:
    results['redirect_loop_detected'] = {'ok': True, 'err': 'RedirectLoop'}
except Exception as e:
    results['redirect_loop_detected'] = {'ok': False, 'err': repr(e)}

# 3) gzip transparent decode
try:
    client = net.HttpClient()
    html, resp = net.fetch_html(base + '/gzip', client=client)
    results['gzip_decode'] = {'ok': 'Local' in html}
except Exception as e:
    results['gzip_decode'] = {'ok': False, 'err': repr(e)}

# 4) HTTP 404 -> FetchError
try:
    client = net.HttpClient()
    net.fetch_html(base + '/404', client=client)
    results['http_404_raises'] = {'ok': False, 'note': 'no exception'}
except net.FetchError as e:
    results['http_404_raises'] = {'ok': True, 'status': getattr(e, 'status', None)}
except Exception as e:
    results['http_404_raises'] = {'ok': False, 'err': repr(e)}

# 5) subresource stylesheet collection
try:
    client = net.HttpClient()
    html, resp = net.fetch_html(base + '/two.html', client=client)
    tree_html = html
    import dom
    tree = dom.parse(html)
    subs = net.collect_subresources(tree)
    srcs = [s['src'] for s in subs]
    results['subresource_collection'] = {'ok': any('style.css' in n for n in srcs), 'subs': srcs}
except Exception as e:
    results['subresource_collection'] = {'ok': False, 'err': repr(e)}

# 6) cache: ETag reuse path (plain 200 + cache store)
try:
    cache_dir = os.path.join(os.path.dirname(__file__), '_cache_test')
    os.makedirs(cache_dir, exist_ok=True)
    client = net.HttpClient(cache=net.CacheStore(cache_dir))
    html1, _ = net.fetch_html(base + '/index.html', client=client)
    html2, _ = net.fetch_html(base + '/index.html', client=client)
    results['cache_roundtrip'] = {'ok': html1 == html2 and 'Local' in html1}
except Exception as e:
    results['cache_roundtrip'] = {'ok': False, 'err': repr(e)}

# 7) CORS header parsing
import dataclasses
try:
    pol = net.CorsPolicy.from_headers({'Access-Control-Allow-Origin': 'http://example.com'})
    allowed = pol.permits('http://example.com')
    allowed_star = net.CorsPolicy.from_headers({'Access-Control-Allow-Origin': '*'}).permits('http://anything.example')
    denied = net.CorsPolicy.from_headers({'Access-Control-Allow-Origin': 'http://a.example'}).permits('http://b.example')
    results['cors_allow_origin'] = {'ok': allowed and allowed_star and not denied}
except Exception as e:
    results['cors_allow_origin'] = {'ok': False, 'err': repr(e)}

# 8) categorize_fetch_error
try:
    import socket
    try:
        net.fetch_html('http://nonexistent.invalid.domain', timeout=2.0)
        results['error_categorization'] = {'ok': False}
    except Exception as e:
        cat = net.categorize_fetch_error(e)
        results['error_categorization'] = {'ok': cat in ('dns', 'connect', 'timeout'), 'cat': cat}
except Exception as e:
    results['error_categorization'] = {'ok': False, 'err': repr(e)}

print(json_dump := open(os.path.join(os.path.dirname(__file__), 'toy_net_results.json'), 'w'))
import json
json.dump(results, json_dump, indent=1)
print(json.dumps(results, indent=1))
httpd.shutdown()
