"""R7 reproducible audit orchestration. Never writes earlier rounds or product code."""
from pathlib import Path
import ast, datetime, hashlib, importlib.util, json, os, platform, shutil, subprocess, sys, time
D = Path(__file__).resolve().parent
ROOT = D.parents[1]
OLD = ROOT / 'assessments' / '20261016-round5'

def save(name, obj):
    (D / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def run(label, args, timeout=900):
    start = time.perf_counter()
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=timeout, env={**os.environ, 'PYTHONIOENCODING':'utf-8'})
        out, err = p.stdout, p.stderr
        code = p.returncode
    except subprocess.TimeoutExpired as e:
        out, err, code = e.stdout or b'', e.stderr or b'', 'TIMEOUT'
    (D / (label + '-stdout.txt')).write_bytes(out)
    (D / (label + '-stderr.txt')).write_bytes(err)
    result = {'command':args, 'returncode':code, 'wall_seconds':time.perf_counter()-start}
    save(label + '-run.json', result)
    print(json.dumps(result), flush=True)
    return result

def inventory():
    files = []
    for base in ['toy_browser','tests','tools']:
        for p in sorted((ROOT/base).rglob('*.py')):
            data = p.read_bytes()
            text = data.decode('utf-8-sig')
            tree = ast.parse(text)
            files.append({'path':p.relative_to(ROOT).as_posix(),'lines':len(text.splitlines()),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'functions':sum(isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) for x in ast.walk(tree)),'classes':sum(isinstance(x,ast.ClassDef) for x in ast.walk(tree))})
    save('source-inventory.json', files)
    save('environment.json', {'at':datetime.datetime.now().astimezone().isoformat(),'python':sys.version,'executable':sys.executable,'platform':platform.platform(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'optional_modules':{m:bool(importlib.util.find_spec(m)) for m in ['tkinter','wgpu','playwright','PIL']},'source_files':len(files),'scope':'Managed runtime; no real GUI/GPU certification; reference browser outputs are frozen R5 evidence, NOT fresh browser runs.'})

if __name__ == '__main__':
    inventory()
    if '--full' in sys.argv:
        run('tests-full', [sys.executable, str(ROOT/'test.py'),'--full','--report'])
    if '--legacy' in sys.argv:
        names = ['js_cases.json','layout_cases.json','render_cases.json','edge_js_results.json','edge_layout_results.json','toy_js_runner.py','toy_layout_runner.py','pipeline_profile.py']
        hashes = {}
        for name in names:
            src = OLD/name
            shutil.copyfile(src,D/name)
            hashes[name] = {'source':src.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(src.read_bytes()).hexdigest()}
        save('historical-fixture-provenance.json',hashes)
        run('legacy-js',[sys.executable,str(D/'toy_js_runner.py')],120)
        run('legacy-layout',[sys.executable,str(D/'toy_layout_runner.py')],120)
    if '--profile' in sys.argv:
        run('pipeline-profile',[sys.executable,str(D/'pipeline_profile.py')],300)
