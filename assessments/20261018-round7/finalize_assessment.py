"""Validate R7 deliverables, retain source hashes, create downloadable bundle."""
from pathlib import Path
import ast, hashlib, json, re, zipfile
D=Path(__file__).resolve().parent
ROOT=D.parents[1]
OUT=ROOT/'outputs'
expected=['实测评估报告.md','逐项改进实施方案.md','评估方法与标准基线.md','网络脚本与安全审计.md','渲染与标准差距审计.md','GUI与工程质量审计.md']
missing=[x for x in expected if not (D/'docs'/x).is_file()]
broken=[]
for p in [D/'README.md',*(D/'docs').glob('*.md')]:
    for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
        if target.startswith(('http://','https://','#')): continue
        path=target.split('#')[0]
        if path and not (p.parent/path).exists(): broken.append({'file':p.relative_to(D).as_posix(),'target':target})
syntax=[]
for p in D.glob('*.py'):
    try: ast.parse(p.read_text(encoding='utf-8-sig'))
    except SyntaxError as e: syntax.append({'file':p.name,'error':str(e)})
changed=[]
for x in json.loads((D/'source-inventory.json').read_text(encoding='utf-8')):
    p=ROOT/x['path']
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=x['sha256']: changed.append(x['path'])
png_errors=[]
for case in json.loads((D/'render_cases.json').read_text(encoding='utf-8')):
    p=D/f'toy_shot_{case}.png'
    if not p.exists(): png_errors.append(p.name+': missing'); continue
    data=p.read_bytes()
    if data[:8]!=b'\x89PNG\r\n\x1a\n' or int.from_bytes(data[16:20],'big')!=800 or int.from_bytes(data[20:24],'big')!=600: png_errors.append(p.name+': invalid header/dimensions')
report={'expected_docs':len(expected),'missing_docs':missing,'broken_local_links':broken,'python_syntax_errors':syntax,'png_header_dimension_errors':png_errors,'product_test_tool_files_changed_since_inventory':changed,'scope':'Delivery integrity checks, not a browser conformance certificate'}
(D/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
if missing or broken or syntax or changed or png_errors: raise SystemExit(1)
OUT.mkdir(exist_ok=True)
manifest=[]
for p in sorted(D.rglob('*')):
    if p.is_file() and '__pycache__' not in p.parts and p.name!='manifest.json':
        data=p.read_bytes();manifest.append({'path':p.relative_to(D).as_posix(),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
(D/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
bundle=OUT/'toy-browser-py-第七轮评估-20261018.zip'
with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(D.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:z.write(p,Path('20261018-round7')/p.relative_to(D))
with zipfile.ZipFile(bundle) as z:
    corrupt=z.testzip()
    if corrupt: raise RuntimeError('ZIP integrity failure: '+corrupt)
print('BUNDLE',bundle,'FILES',len(manifest)+1,'BYTES',bundle.stat().st_size,'CRC_OK',True)
