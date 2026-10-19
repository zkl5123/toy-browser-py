"""Fresh pure-ECMAScript differential probes; Node is NOT a browser oracle."""
import json, subprocess, sys
from pathlib import Path
D=Path(__file__).resolve().parent
sys.path.insert(0,str(D.parents[1]))
from toy_browser.document import dom
from toy_browser.scripting import js
NODE=r'C:/Users/zklzk/.workbuddy/binaries/node/versions/22.22.2-6/node.exe'
CASES={
 'null_undefined':'console.log(null === undefined);',
 'strict_bool_number':'console.log(true === 1);',
 'object_identity':'console.log(({}) === ({}));',
 'array_identity':'console.log([] === []);',
 'nan_equality':'console.log(NaN === NaN);',
 'negative_zero':'console.log(Object.is(-0,0));',
 'utf16_length':'console.log("😀".length);',
 'utf16_charcode':'console.log("😀".charCodeAt(0));',
 'default_sort':'console.log([2,10,1].sort().join(","));',
 'number_null':'console.log(Number(null));',
 'number_undefined':'console.log(Number(undefined));',
 'parseint_radix':'console.log(parseInt("10",16));',
 'truthy_array':'console.log(Boolean([]));',
 'truthy_object':'console.log(Boolean({}));',
 'nullish':'console.log(0 ?? 7);',
 'optional_chain':'var x=null; console.log(x?.a);',
 'multi_declaration':'var a=1,b=2; console.log(a+b);',
 'object_keys_numeric':'console.log(Object.keys({"10":"a","2":"b","x":"c"}).join(","));',
 'array_hole':'console.log(0 in [,]);',
 'property_absence':'console.log("x" in {x:undefined});',
 'try_finally':'function f(){try{return 1;}finally{return 2;}} console.log(f());',
 'template_literal':'var n=3; console.log(`v${n}`);',
 'promise_order':'Promise.resolve().then(function(){console.log("p");}); console.log("s");',
 'async_function':'async function f(){return 3;} f().then(function(x){console.log(x);});',
}
BOOT='const vm=require("node:vm"); const code=JSON.parse(process.argv[1]); const logs=[]; const ctx={console:{log:(...a)=>logs.push(a.map(String).join(" "))}};let error=null;try{vm.runInNewContext(code,ctx,{timeout:1000});}catch(e){error=e.name;}setImmediate(()=>process.stdout.write(JSON.stringify({logs,error})));'
results=[]
for name,code in CASES.items():
    p=subprocess.run([NODE,'-e',BOOT,json.dumps(code)],capture_output=True,text=True,encoding='utf-8',timeout=5)
    ref=json.loads(p.stdout)
    logs=[]; error=None
    h=js.Host(dom.parse('<html><body></body></html>'),log=logs.append)
    try:
        h.execute(code)
        h.flush_microtasks()
    except Exception as e:
        error=type(e).__name__+': '+str(e)
    actual={'logs':logs,'error':error}
    match=ref['logs']==logs and (ref['error'] is None)==(error is None)
    results.append({'id':name,'code':code,'reference':ref,'actual':actual,'match':match})
obj={'scope':'24 selected pure ECMAScript probes, fresh Node/V8 vs project; not WPT/Test262 or browser compatibility percentage','node_version':subprocess.check_output([NODE,'--version'],text=True).strip(),'cases':results,'matched':sum(x['match'] for x in results),'total':len(results)}
(D/'semantic-probes.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(obj,ensure_ascii=False,indent=2))
