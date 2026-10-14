const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path');
const read=n=>JSON.parse(fs.readFileSync(path.join(__dirname,n+'.json'),'utf8'));
const write=(n,v)=>fs.writeFileSync(path.join(__dirname,n+'.json'),JSON.stringify(v,null,2));
(async()=>{
 const b=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 write('environment',{browser:await b.version(),node:process.version,viewport:[800,600],date:new Date().toISOString()});
 let out={};
 for(const c of read('js_cases')){
  const p=await b.newPage({viewport:{width:800,height:600}}),logs=[];
  await p.setContent('<!DOCTYPE html><html><body><div id="t1">orig</div><button id="btn">B</button></body></html>');
  p.on('console',m=>{if(m.type()==='log')logs.push(m.text())});
  const status=await p.evaluate(code=>{try{new Function(code)();return 'OK'}catch(e){return 'ERROR: '+e.name+': '+e.message}},c.code);
  await p.waitForTimeout(80);out[c.id]={status,logs};await p.close();
 }write('edge_js_results',out);
 const probes={layout_blocks:['a','b'],layout_margin_collapse:['t','b'],layout_flex:['row','c1','c2','c3'],layout_grid:['g','g1','g4'],layout_table:['tab'],layout_abs:['p','c'],layout_text_wrap:['w']};
 const p=await b.newPage({viewport:{width:800,height:600},deviceScaleFactor:1});out={};
 for(const [name,html] of Object.entries(read('layout_cases'))){
  await p.setContent('<!DOCTYPE html>'+html);out[name]={};
  for(const id of probes[name])out[name][id]=await p.evaluate(id=>{const r=document.getElementById(id).getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}},id);
 }write('edge_layout_results',out);
 for(const [name,html] of Object.entries(read('render_cases'))){await p.setContent('<!DOCTYPE html>'+html);await p.evaluate(()=>document.fonts.ready);await p.screenshot({path:path.join(__dirname,'edge_shot_'+name+'.png')});}
 out={};for(const n of [100,1000,5000]){
  const html='<html><body>'+Array.from({length:n},(_,i)=>`<div style="width:100px;height:20px;margin:2px;background:#ccc">row ${i}</div>`).join('')+'</body></html>';
  const samples=[];for(let k=0;k<3;k++){let t=performance.now();await p.setContent('<!DOCTYPE html>'+html);await p.evaluate(()=>document.body.offsetHeight);const layoutMs=performance.now()-t;await p.screenshot();samples.push({layoutMs,totalWithScreenshotMs:performance.now()-t});}out[n]=samples;
 }write('edge_perf',out);await b.close();console.log('Reference complete');
})().catch(e=>{console.error(e);process.exit(1)});
