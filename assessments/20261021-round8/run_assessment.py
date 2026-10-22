"""R8 assessment: read-only product audit, outputs restricted to this round."""
from pathlib import Path
import ast, datetime, hashlib, importlib.util, json, os, platform, shutil, subprocess, sys, time
D = Path(__file__).resolve().parent
ROOT = D.parents[1]
OLD = ROOT / 'assessments' / '20261018-round7'
MANAGED = r'C:/Users/zklzk/.workbuddy/binaries/python/versions/3.13.12/python.exe'
NATIVE = r'C:/Users/zklzk/AppData/Local/Python/pythoncore-3.11-64/python.exe'
def save(name, obj):
    (D/name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
def run(label, args, timeout=1800):
    start=time.perf_counter()
    try:
        p=subprocess.run(args,cwd=ROOT,capture_output=True,timeout=timeout,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        out,err,code=p.stdout,p.stderr,p.returncode
    except subprocess.TimeoutExpired as e:
        out,err,code=e.stdout or b'',e.stderr or b'','TIMEOUT'
    (D/(label+'-stdout.txt')).write_bytes(out)
    (D/(label+'-stderr.txt')).write_bytes(err)
    obj={'command':args,'returncode':code,'wall_seconds':round(time.perf_counter()-start,3)}
    save(label+'-run.json',obj)
    print(json.dumps(obj),flush=True)
    return obj
def inventory():
    files=[]
    for base in ['toy_browser','tests','tools']:
        for p in sorted((ROOT/base).rglob('*.py')):
            data=p.read_bytes(); text=data.decode('utf-8-sig'); ast.parse(text)
            files.append({'path':p.relative_to(ROOT).as_posix(),'lines':len(text.splitlines()),'sha256':hashlib.sha256(data).hexdigest()})
    save('source-inventory.json',files)
    save('environment.json',{'at':datetime.datetime.now().astimezone().isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'python':sys.version,'platform':platform.platform(),'executable':sys.executable,'optional_modules':{m:bool(importlib.util.find_spec(m)) for m in ['tkinter','wgpu','PIL']}})
def prepare():
    names=['semantic_probe.py','render_probe.py','security_probe.py','render_screenshots.py','render_cases.json','js_cases.json','layout_cases.json','edge_js_results.json','edge_layout_results.json','toy_js_runner.py','toy_layout_runner.py','compare_results.py','pipeline_profile.py']
    prov={}
    for name in names:
        src=OLD/name
        shutil.copyfile(src,D/name)
        prov[name]={'source':src.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'note':'Historical probe/fixture copied; embedded evidence line numbers refer to old round, not current code. Edge results are frozen, not a fresh browser run.'}
    # Correct immutable run metadata only; preserve case semantics for initial replay.
    p=D/'security_probe.py'; s=p.read_text(encoding='utf-8'); s=s.replace("'commit': '2d1bdd3'", "'commit': '551da74'"); p.write_text(s,encoding='utf-8')
    save('fixture-provenance.json',prov)
if __name__=='__main__':
    if '--prepare' in sys.argv: inventory(); prepare()
    if '--full' in sys.argv: run('native-full',[NATIVE,str(ROOT/'test.py'),'--full','--report','--json',str(D/'native-full.json'),'--junit',str(D/'native-full.xml')])
    if '--probes' in sys.argv:
        for label,file in [('semantic','semantic_probe.py'),('render-legacy','render_probe.py'),('security-legacy','security_probe.py'),('legacy-js','toy_js_runner.py'),('legacy-layout','toy_layout_runner.py')]:
            run(label,[MANAGED,str(D/file)],180)
