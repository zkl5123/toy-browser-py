const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path');
(async()=>{const b=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});let out={};
const p=await b.newPage({viewport:{width:400,height:200}});
for(const name of ['cross_css','csp_self','csp_inline','external_script','script_order','csp_nonce','repeat_external']){await p.goto(process.argv[2]+'/'+name);await p.screenshot({path:path.join(__dirname,'edge_'+name+'.png')});out[name]=await p.locator('#probe').evaluate(el=>({text:el.textContent,color:getComputedStyle(el).backgroundColor}));}
fs.writeFileSync(path.join(__dirname,'edge_network_e2e.json'),JSON.stringify(out,null,2));await b.close()})().catch(e=>{console.error(e);process.exit(1)});
