// Read-only novice result-page checks with mocked fetch; no service or generation calls.
'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
const {parseHTML}=require('../tests/dom/node_modules/linkedom');
const root=path.resolve(__dirname,'..'),base='http://172.16.7.76:8780';
const files=Object.fromEntries(['result.html','result.js','result.css','style.css'].map(name=>[name,fs.readFileSync(path.join(root,'web',name),'utf8')]));
const ids={a:'a'.repeat(32),b:'b'.repeat(32),c:'c'.repeat(32)};
const fixture=(id=ids.a,fields={})=>({id,name:'蓝衣角色',runtime:'4.2',revision:7,skeleton:{animations:{idle:{},walk:{}},skins:[{name:'default'},{name:'winter'}]},...fields});
const response=(value,status=200)=>({ok:status>=200&&status<300,status,json:async()=>structuredClone(value)});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness({search='',rigs=[fixture()],records={},reduced=false,storage={},handler}={}){
 const {window,document}=parseHTML(files['result.html']);
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return this.querySelector('option[selected]')?.value||this.querySelector('option')?.value||''},set(value){for(const item of this.querySelectorAll('option')){if(item.value===String(value))item.setAttribute('selected','');else item.removeAttribute('selected')}}});
 // LinkeDOM re-encodes percent-escaped href components on access; keep the
 // exact assigned URL attribute and let native URL perform URL resolution.
 Object.defineProperty(window.HTMLAnchorElement.prototype,'href',{configurable:true,get(){return this.getAttribute('href')||''},set(value){this.setAttribute('href',String(value))}});
 const $=id=>document.getElementById(id),requests=[],messages=[],handlers={},memory=new Map(Object.entries(storage));
 const location={origin:base,search,href:base+'/result.html'+search};
 const history={replaceState(_state,_title,url){location.href=String(url);location.search=new URL(url).search}};
 const frameWindow={postMessage(data,origin){messages.push({data:structuredClone(data),origin})}};$('viewer').contentWindow=frameWindow;
 const fetch=async(url,options)=>{assert(!options?.method||options.method==='GET','Result page must be read-only');requests.push({url,options:options||null});if(handler){const result=await handler(url);if(result!==undefined)return result}if(url==='/api/rigs')return response(rigs);const match=/^\/api\/rigs\/([a-f0-9]{32})$/.exec(url);if(match){const rig=records[match[1]]||rigs.find(item=>item.id===match[1]);return rig?response(rig):response({error:'没有找到该动画'},404)}throw Error('Unexpected mocked request: '+url)};
 const context=vm.createContext({window:{addEventListener(type,fn){handlers[type]=fn}},document,location,history,URL,URLSearchParams,localStorage:{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value)},matchMedia:()=>({matches:reduced}),fetch,console});
 const run=code=>vm.runInContext(code,context),script=files['result.js'].replace(/\binit\(\);\s*$/,'');vm.runInContext(script,context);
 return {$,document,requests,messages,location,memory,run,frameWindow,init:()=>run('init()'),message:(data,origin=base,source=frameWindow)=>handlers.message({data,origin,source}),state:()=>run('state')};
}
const results=[];
async function check(name,run){try{await run();results.push({name,passed:true})}catch(error){results.push({name,passed:false,error:error.message});console.error(name+': '+error.message)}}
(async()=>{
 await check('Default walk, hidden optional controls and read-only viewer controls',async()=>{
  const h=harness();await h.init();assert.equal(h.$('animation').value,'walk');assert.equal(h.$('viewer').hidden,false);assert.equal(h.$('controls').hidden,false);assert(!h.$('more').hasAttribute('open'));assert(!h.$('library').hasAttribute('open'));assert.equal(h.messages.length,0);h.message({type:'ready'});
  const data=h.messages.at(-1).data;assert.equal(data.animation,'walk');assert.equal(data.bones,false);assert.equal(data.showInfluence,false);assert.equal(data.selectedBone,'');assert.equal(data.paused,false);assert.equal(data.speed,1);assert.equal(data.skin,'default');assert(!('boneDraft'in data));assert(!('mutations'in data));assert.equal(h.messages.at(-1).origin,base);assert.equal(h.document.querySelectorAll('canvas').length,0);
 });
 await check('Explicit animation and skin query are respected',async()=>{
  const h=harness({search:'?rig='+ids.a+'&animation=idle&skin=winter'});await h.init();assert.equal(h.$('animation').value,'idle');assert.equal(h.$('skin').value,'winter');h.message({type:'ready'});assert.equal(h.messages.at(-1).data.skin,'winter');assert.equal(new URL(h.location.href).searchParams.get('animation'),'idle');assert.equal(new URL(h.location.href).searchParams.get('skin'),'winter');
 });
 await check('No animations produce paused static display',async()=>{
  const h=harness({rigs:[fixture(ids.a,{skeleton:{animations:{},skins:{default:{}}}})]});await h.init();assert.equal(h.$('animation').value,'__setup__');assert(h.$('play').disabled);h.message({type:'ready'});const data=h.messages.at(-1).data;assert.equal(data.animation,'');assert(data.setup);assert(data.paused);assert(h.$('skin-label').hidden);
 });
 await check('Explicit static display and invalid animation fallback',async()=>{
  const h=harness({search:'?rig='+ids.a+'&animation=__setup__'});await h.init();h.message({type:'ready'});assert(h.messages.at(-1).data.setup);assert(h.$('play').disabled);const other=harness({search:'?rig='+ids.a+'&animation=missing'});await other.init();assert.equal(other.$('animation').value,'walk');
 });
 await check('Flip, pause, speed and skin controls only change playback',async()=>{
  const h=harness();await h.init();h.message({type:'ready'});const before=h.requests.length;h.$('play').onclick();assert(h.messages.at(-1).data.paused);assert.equal(h.$('play').textContent,'播放');h.$('play').onclick();assert(!h.messages.at(-1).data.paused);h.$('direction').onclick();assert(h.messages.at(-1).data.mirrored);assert.equal(h.$('direction').getAttribute('aria-pressed'),'true');h.$('direction').onclick();assert(!h.messages.at(-1).data.mirrored);h.$('speed').value='1.5';h.$('speed').onchange();assert.equal(h.messages.at(-1).data.speed,1.5);h.$('skin').value='winter';h.$('skin').onchange();assert.equal(h.messages.at(-1).data.skin,'winter');assert.equal(h.requests.length,before);assert.equal(h.state().rig.revision,7);
 });
 await check('Reduced motion starts paused',async()=>{const h=harness({reduced:true});await h.init();h.message({type:'ready'});assert(h.messages.at(-1).data.paused);assert.equal(h.$('play').textContent,'播放')});
 await check('Foreign origin and foreign window messages are ignored',async()=>{
  const h=harness();await h.init();h.message({type:'ready'},'https://evil.example');h.message({type:'ready'},base,{});assert.equal(h.messages.length,0);h.message({type:'error',message:'malicious'},'https://evil.example');h.message({type:'error',message:'malicious'},base,{});assert(h.$('notice').hidden);h.message({type:'ready'});assert.equal(h.messages.length,1);
 });
 await check('Download and advanced links use current rig and selected animation',async()=>{
  const name='run & jump';const h=harness({rigs:[fixture(ids.a,{skeleton:{animations:{[name]:{}},skins:[{name:'default'}]}})],search:'?rig='+ids.a+'&animation='+encodeURIComponent(name)+'&advanced=1'});await h.init();assert.equal(h.$('download').href,'/api/rigs/'+ids.a+'/export');const editor=new URL(h.$('edit').href,base+'/');assert.equal(editor.pathname,'/rig.html');assert.equal(editor.searchParams.get('rig'),ids.a);assert.equal(editor.searchParams.get('animation'),name);assert.equal(editor.searchParams.get('advanced'),'1');assert(!new URL(h.location.href).searchParams.has('advanced'));assert.equal(h.memory.get('spine-last-result'),ids.a);h.$('animation').value='__setup__';h.$('animation').onchange();assert.equal(new URL(h.$('edit').href,base+'/').searchParams.get('animation'),'__setup__');
 });
 await check('Result styles override inherited editor main and iframe layout',async()=>{
  const h=harness();const links=[...h.document.querySelectorAll('link[rel=stylesheet]')].map(item=>item.getAttribute('href'));assert.deepEqual(links,['style.css','result.css']);assert(/main\{display:grid;/.test(files['style.css']));const main=/\.result-workspace\{([^}]+)\}/.exec(files['result.css'])?.[1]||'';assert(/(?:^|;)display:block(?:;|$)/.test(main));assert(/(?:^|;)min-height:0(?:;|$)/.test(main));assert(/(?:^|;)max-width:1060px(?:;|$)/.test(main));const frame=/\.animation-stage iframe\{([^}]+)\}/.exec(files['result.css'])?.[1]||'';assert(/(?:^|;)height:100%(?:;|$)/.test(frame));assert(/(?:^|;)width:100%(?:;|$)/.test(frame));assert.equal(h.document.querySelector('main').className,'result-workspace');
 });
 await check('Slow previous valid selection cannot overwrite newer selection',async()=>{
  const waits=new Map();const h=harness({rigs:[fixture(ids.a),fixture(ids.b)],handler:url=>waits.get(url)?.promise});await h.init();const old=deferred(),newer=deferred();waits.set('/api/rigs/'+ids.a,old);waits.set('/api/rigs/'+ids.b,newer);const pOld=h.run('load("'+ids.a+'")'),pNew=h.run('load("'+ids.b+'")');await tick();newer.resolve(response(fixture(ids.b,{name:'新成果'})));await pNew;old.resolve(response(fixture(ids.a,{name:'旧成果'})));await pOld;assert.equal(h.state().rig.id,ids.b);assert.equal(h.$('name').textContent,'新成果');assert(h.$('viewer').src.includes(ids.b));assert.equal(h.document.body.getAttribute('aria-busy'),'false');
 });
 await check('Invalid selection cancels a pending valid load',async()=>{
  const old=deferred();let hold=false;const h=harness({handler:url=>hold&&url==='/api/rigs/'+ids.b?old.promise:undefined,records:{[ids.b]:fixture(ids.b)}});await h.init();hold=true;const pending=h.run('load("'+ids.b+'")');await tick();await h.run('load("not-an-id")');old.resolve(response(fixture(ids.b,{name:'旧请求返回'})));await pending;assert(h.$('viewer').hidden);assert(h.$('notice').textContent.includes('无效'));assert.equal(h.$('download').hidden,true);
 });
 await check('Empty library and failed list request show usable empty state',async()=>{
  const empty=harness({rigs:[]});await empty.init();assert(empty.$('viewer').hidden);assert(empty.$('download').hidden);assert(!empty.$('library-empty').hidden);assert(empty.$('library').open);assert(empty.$('rig-select').hidden);const failed=harness({handler:url=>url==='/api/rigs'?response({error:'记录读取失败'},503):undefined});await failed.init();assert(failed.$('viewer').hidden);assert(failed.$('notice').textContent.includes('记录读取失败'));assert(failed.$('download').hidden);
 });
 await check('Missing rig, unsupported runtime and malformed skeleton fail cleanly',async()=>{
  const missing=harness({search:'?rig='+ids.c});await missing.init();assert(missing.$('viewer').hidden);assert(missing.$('notice').textContent.includes('没有找到'));assert.equal(missing.document.body.getAttribute('aria-busy'),'false');const unsupported=harness({rigs:[fixture(ids.a,{runtime:'4.1'})]});await unsupported.init();assert(unsupported.$('viewer').hidden);assert(unsupported.$('notice').textContent.includes('暂时无法预览'));const malformed=harness({rigs:[fixture(ids.a,{skeleton:null})]});await malformed.init();assert(malformed.$('viewer').hidden);assert(malformed.$('download').hidden);
 });
 await check('Library, names, animation, skin and error strings cannot inject DOM',async()=>{
  const attack='<img src=x onerror="alert(1)">';const h=harness({rigs:[fixture(ids.a,{name:attack,skeleton:{animations:{[attack]:{}},skins:[{name:attack}]}})]});await h.init();assert.equal(h.$('name').textContent,attack);assert.equal(h.$('rig-select').querySelectorAll('img').length,0);assert.equal(h.$('animation').querySelectorAll('img').length,0);assert.equal(h.$('skin').querySelectorAll('img').length,0);h.message({type:'error',message:attack});assert.equal(h.$('notice').textContent,attack);assert.equal(h.$('notice').querySelectorAll('img').length,0);assert.equal(h.document.querySelectorAll('[onerror]').length,0);assert.equal(new URL(h.$('edit').href,base+'/').searchParams.get('animation'),attack);
 });
 const failed=results.filter(item=>!item.passed),evidence={status:failed.length?'failed':'passed',checked_at:new Date().toISOString(),suite:'result-page-dom-v1',tests:results.length,passed:results.length-failed.length,failed:failed.length,real_network_calls:0,new_generation_calls:0,new_resource_writes:0,limitations:['LinkeDOM emulates select values and exact assigned URL attributes; iframe renderer and visual responsive layout require browser validation.','CSS check verifies cascade selectors and load order, not pixel layout.'],source_sha256:Object.fromEntries(Object.entries(files).map(([name,data])=>[name,crypto.createHash('sha256').update(data).digest('hex')])),results};
 const output=path.resolve(process.argv[2]||path.join(root,'evidence/simple-workbench-v1.9/test-result-dom.json'));fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(evidence,null,2)+'\n');console.log(JSON.stringify({status:evidence.status,tests:evidence.tests,passed:evidence.passed,failed:evidence.failed,evidence:output}));if(failed.length)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1});
