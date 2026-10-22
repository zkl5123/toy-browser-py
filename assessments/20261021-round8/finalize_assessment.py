"""Validate and bundle R8 deliverables; does not alter product code."""
from pathlib import Path
import json,hashlib,ast,re,zipfile,struct
D=Path(__file__).resolve().parent;ROOT=D.parents[1]
def save(n,v):(D/n).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
expected=['实测评估报告.md','逐项改进实施方案.md','评估方法与标准基线.md','网络脚本与安全审计.md','渲染与标准差距审计.md','GUI与工程质量审计.md']
missing=[n for n in expected if not (D/'docs'/n).is_file()]
broken=[]
for p in [D/'README.md',*(D/'docs').glob('*.md')]:
    for link in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
        if '://' not in link and not link.startswith('#') and not (p.parent/link.split('#')[0]).exists():broken.append([p.name,link])
syntax=[]
for p in D.glob('*.py'):
    try:ast.parse(p.read_text(encoding='utf-8-sig'))
    except Exception as e:syntax.append([p.name,str(e)])
changed=[]
for f in json.loads((D/'source-inventory.json').read_text(encoding='utf-8')):
    p=ROOT/f['path']
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256']:changed.append(f['path'])
png=[]
for p in D.glob('toy_shot_*.png'):
    b=p.read_bytes();png.append({'file':p.name,'valid_signature':b[:8]==b'\x89PNG\r\n\x1a\n','dimensions':struct.unpack('>II',b[16:24]),'bytes':len(b)})
plan=(D/'docs'/'逐项改进实施方案.md').read_text(encoding='utf-8')
items=len(re.findall(r'^## \d+ — R8-',plan,re.M))
report={'documents':len(expected),'missing':missing,'broken_links':broken,'python_syntax_errors':syntax,'source_changes':changed,'implementation_items':items,'screenshots':png,'scope':'Delivery integrity only; not a browser conformance certificate. Test failures retained.'}
save('delivery-check.json',report)
if missing or broken or syntax or changed or items!=18 or len(png)!=4:raise SystemExit(json.dumps(report,ensure_ascii=False))
save('summary.json',{'baseline':'551da747862b6d35384e8c97e6f6a4e86b389a57','full_regression':{'passed':2871,'failed':3,'total':2874,'exit_code':1},'node_semantics':{'matched':24,'total':24},'historical_layout':{'within_1px':48,'total':48},'raw_render_checks':{'matched':29,'total':32,'caveat':'Two nonmatches have obsolete GPU probe preconditions; one is document-entry contract mismatch.'},'documents':6,'implementation_items':18,'not_verified':['full WPT/Test262','fresh multi-engine browser references','screenshot visual review','real IME/UIA','GPU surface/device-lost']})
files=[p for p in D.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='manifest.json']
manifest=[{'path':p.relative_to(D).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)]
save('manifest.json',manifest);files.append(D/'manifest.json')
out=ROOT/'outputs'/'toy-browser-py-第八轮评估-20261021.zip'
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for p in files:z.write(p,arcname=D.name+'/'+p.relative_to(D).as_posix())
with zipfile.ZipFile(out) as z:
    bad=z.testzip()
    if bad:raise RuntimeError(bad)
print(json.dumps(report,ensure_ascii=False,indent=2));print('ZIP',str(out),'FILES',len(files),'BYTES',out.stat().st_size,'CRC_OK')
