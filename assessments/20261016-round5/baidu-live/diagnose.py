"""Offline minimization of the failures observed during the Baidu visit."""
from pathlib import Path
import json, sys, re, collections

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from toy_browser.document import dom
from toy_browser.scripting import js
from toy_browser.scripting.web_api import make_location

OUT = Path(__file__).resolve().parent
tree = dom.parse((OUT/'gui-ua-response.html').read_text(encoding='utf-8', errors='replace'))
nodes = []
def walk(node):
    if node.is_element():
        nodes.append(node)
    for child in node.children:
        walk(child)
walk(tree)
errors = []
for index, node in enumerate(n for n in nodes if n.tag == 'script'):
    if node.attr('src') or not node.text_content().strip():
        continue
    code = node.text_content()
    try:
        js.parse_program(code)
    except Exception as exc:
        match = re.search(r'pos (\d+)', str(exc))
        position = int(match[1]) if match else 0
        errors.append({'script_index': index, 'error': str(exc), 'characters': len(code),
                       'position': position, 'context': code[max(0, position-100):position+100]})
        (OUT/'first-failing-inline.js').write_text(code, encoding='utf-8')
        break
minimal = []
for code in ('var a=1,b=2;', 'let a=1,b=2;', 'const a=1,b=2;', 'var a=1; var b=2;', '(1,2);'):
    try:
        js.parse_program(code)
        minimal.append({'code': code, 'parsed': True})
    except Exception as exc:
        minimal.append({'code': code, 'parsed': False, 'error': str(exc)})
data = {
    'elements': len(nodes), 'tags': dict(collections.Counter(n.tag for n in nodes)),
    'form_controls': [{k: n.attr(k) for k in ('id','name','type','action','method')} | {'tag':n.tag}
                      for n in nodes if n.tag in ('form','input','textarea','button')],
    'first_inline_parse_error': errors, 'minimal_parser_cases': minimal,
    'location_keys': list(make_location('https://www.baidu.com/')),
    'location_replace_callable': callable(make_location('https://www.baidu.com/').get('replace')),
    'scope': 'Parser-only local checks; not a second online visit or an interaction completion.'
}
(OUT/'page-analysis.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'minimal_parser_cases':minimal,'location_replace_callable':data['location_replace_callable']},ensure_ascii=False))
