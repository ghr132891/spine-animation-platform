// Exercise browser interactions without submitting paid generation requests.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),crypto=require('crypto').webcrypto;
const {parseHTML}=require('../tests/dom/node_modules/linkedom');
const root=path.resolve(__dirname,'..'),base='http://172.16.7.76:8780';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let i=0;i<100;i++){if(fn())return;await sleep(5)}throw Error('DOM timeout')}
async function scenario(owner,openAccess=false,fresh=false){
 const {window,document}=parseHTML(fs.readFileSync(path.join(root,'web/auto.html'),'utf8'));
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return this.querySelector('option[selected]')?.value||this.querySelector('option')?.value||''},set(v){for(const o of this.querySelectorAll('option')){if(o.value===String(v))o.setAttribute('selected','');else o.removeAttribute('selected')}}});
 Object.defineProperty(window.HTMLInputElement.prototype,'checked',{configurable:true,get(){return this.hasAttribute('checked')},set(v){v?this.setAttribute('checked',''):this.removeAttribute('checked')}});
 const $=id=>document.getElementById(id),memory=new Map(),requests=[],messages=[],handlers={};let session={connected:owner||openAccess,owner,...(owner?{access_code:'sample-code'}:{})},list=[],record=null,rejectNext=false;
 if(fresh){record={id:'f'.repeat(32),state:'failed',requirements:'历史制作',created_at:1,stage:'本次未完成',step:0,error:'保留的历史错误',artifacts:[]};list=[record];memory.set('spine-auto-last-task',record.id)}
 const browserWindow={addEventListener:(type,handler)=>{handlers[type]=handler}};$('viewer').contentWindow={postMessage:data=>messages.push(data)};
 class FileReader{readAsDataURL(){this.result='data:image/png;base64,TEST';queueMicrotask(()=>this.onload())}}
 class Image{set src(value){this.naturalWidth=896;this.naturalHeight=1200;queueMicrotask(()=>this.onload())}}
 const fetch=async(url,options={})=>{
  const data=options.body?JSON.parse(options.body):null;requests.push({url,data});let status=200,value;
  if(url.endsWith('/session')){if(data){assert.equal(data.code,'sample-code');session={connected:true,owner:false}}value=session}
  else if(url.includes('/source?')){assert(owner);value={id:'a'.repeat(32),name:'机器人',image_url:'/api/characters/'+ 'a'.repeat(32)+'/image'}}
  else if(url.endsWith('/tasks')){if(data){assert.equal(options.headers['X-Spine-Local'],'1');if(rejectNext){rejectNext=false;throw Error('连接暂时断开')}record={id:data.nonce,state:'queued',requirements:data.requirements,created_at:1,stage:'排队等待',steps:[],step:0,artifacts:[]};list=[record];value=record}else value=list}
  else if(url.endsWith('/cancel')){record={...record,state:'cancelled',stage:'已停止'};list=[record];value=record}
  else value=record;
  return {ok:status===200,status,json:async()=>value};
 };
 const context=vm.createContext({window:browserWindow,document,location:{origin:base,search:(owner?'?character='+ 'a'.repeat(32):'')+(fresh?(owner?'&':'?')+'new=1':'')},localStorage:{getItem:k=>memory.get(k),setItem:(k,v)=>memory.set(k,v)},URLSearchParams,fetch,crypto,Image,FileReader,console,setInterval:()=>1,clearInterval:()=>{}});
 const run=code=>vm.runInContext(code,context),event={preventDefault(){}};
 vm.runInContext(fs.readFileSync(path.join(root,'web/auto.js'),'utf8'),context);
 await until(()=>requests.length>=(owner?3:1));await sleep(5);
 if(owner){assert(!$('invite').hidden);assert(!$('source-preview').hidden);assert.equal(run('state.character'),'a'.repeat(32))}
 else if(!openAccess){assert(!$('login').hidden);assert($('app').hidden);$('access-code').value='sample-code';await $('login-form').onsubmit(event);assert($('login').hidden);assert(!$('app').hidden);assert($('invite').hidden);await $('request-form').onsubmit(event);assert($('notice').textContent.includes('上传'))}
 assert.equal($('invite').closest('details').id,'generation-options');assert(!document.querySelector('.header-actions #invite'));assert.equal(document.querySelector('.auto-brand').getAttribute('href'),'auto.html?new=1');assert(document.querySelector('.header-actions a[href="result.html"]'));
 if(fresh){assert.equal(run('state.active'),null);assert($('progress').hidden);assert.equal(memory.get('spine-auto-last-task'),'f'.repeat(32));assert.equal($('history-count').textContent,'（1）');assert($('history-list').textContent.includes('历史制作'))}
 if(openAccess){assert($('login').hidden);assert(!$('app').hidden);assert($('invite').hidden);assert(!requests.some(r=>r.url.endsWith('/session')&&r.data))}
 assert(!$('generation-options').hasAttribute('open'));assert.equal($('generation-options').tagName,'DETAILS');assert.equal($('budget-note').textContent,'需要补图时最多生成 4 张，消耗亿创积分。');assert(!$('budget-note').hidden);assert.equal($('submit').textContent,'生成动画');assert.equal(document.querySelector('.history-panel').tagName,'DETAILS');assert(!document.querySelector('.history-panel').hasAttribute('open'));
 await run("choose({type:'image/png',name:'机器人.png',size:100})");assert(!$('source-preview').hidden);assert.equal(run('state.character'),null);
 rejectNext=true;await $('request-form').onsubmit(event);const nonce=requests.filter(r=>r.data?.nonce).at(-1).data.nonce;assert(!$('submit').disabled);
 await $('request-form').onsubmit(event);assert.equal(requests.filter(r=>r.data?.nonce).at(-1).data.nonce,nonce);assert($('submit').disabled);assert.equal($('stage').textContent,'排队等待');
 const posted=requests.filter(r=>r.data?.nonce).at(-1).data;assert.equal(posted.image,'TEST');assert.equal(posted.mode,'image');
 record={...record,state:'running',stage:'定位骨骼与关节',step:1};await $('refresh').onclick();assert.equal($('stage').textContent,'准备角色');assert.equal($('stage-detail').textContent,'定位骨骼与关节');assert(!$('process-detail').hasAttribute('open'));
 await $('cancel').onclick();assert.equal($('stage').textContent,'已停止');assert(!$('submit').disabled);
 record={...record,state:'failed',error:'遮挡部分无法补全',stage:'本次未完成'};await $('refresh').onclick();assert.equal($('result-message').textContent,record.error);assert($('results').hidden);
 record={...record,state:'completed',error:null,summary:'关节和动作检查通过',stage:'制作完成',requirements:'<img src=x onerror=alert(1)>',download_url:'/api/workflows/tasks/'+nonce+'/download',artifacts:[{rig_id:'b'.repeat(32),runtime:'4.2',preview_url:'/rig.html?rig='+ 'b'.repeat(32),animations:['idle','walk']}]};list=[record];await $('refresh').onclick();assert(!$('viewer').hidden);assert(!$('results').hidden);assert.equal($('animation').value,'walk');assert($('download').href.includes('/api/workflows/'));assert.equal($('history-list').querySelectorAll('img').length,0);assert($('history-list').textContent.includes('<img'));
 assert.equal($('open-editor').href,'/result.html?rig='+ 'b'.repeat(32));assert.equal($('open-editor').textContent,'全屏看动画');assert.equal($('advanced-editor').href,'/rig.html?rig='+ 'b'.repeat(32)+'&advanced=1');assert(!$('advanced-editor').hidden);assert.equal($('history-count').textContent,'（1）');assert($('process-detail').hidden);assert(!$('result-description').hasAttribute('open'));
 record={...record,artifacts:[...record.artifacts,{rig_id:'c'.repeat(32),runtime:'4.2',preview_url:'/rig.html?rig='+ 'c'.repeat(32),animations:['idle','walk']}]};list=[record];await $('refresh').onclick();assert(!$('artifact').hidden);$('artifact').value='1';$('artifact').onchange();assert($('open-editor').href.includes('c'.repeat(32)));await $('refresh').onclick();assert.equal($('artifact').value,'1');assert($('viewer').src.includes('c'.repeat(32)));
 handlers.message({origin:base,source:$('viewer').contentWindow,data:{type:'ready'}});assert.equal(messages.at(-1).animation,'walk');$('pause').onclick();assert(messages.at(-1).paused);
 const before=messages.length;handlers.message({origin:'http://evil.test',source:$('viewer').contentWindow,data:{type:'ready'}});assert.equal(messages.length,before);
 assert.equal(requests.find(r=>r.url.endsWith('/tasks')&&r.data)?.data.image_preparation,'auto');
 $('prepare-image').checked=false;$('prepare-image').onchange();assert.equal($('budget-note').textContent,'仅使用原图，不调用亿创补图。');await $('request-form').onsubmit({preventDefault(){}});assert.equal(requests.filter(r=>r.url.endsWith('/tasks')&&r.data).at(-1).data.image_preparation,'preserve');
 $('mode-generate').onclick();assert($('preparation-option').hidden);assert(!$('generation-note').hidden);assert($('upload-section').hidden);assert.equal(run('state.mode'),'generate');assert($('budget-note').hidden);assert($('generation-note').textContent.includes('最多 4 张'));assert($('generation-note').textContent.includes('消耗亿创积分'));$('mode-image').onclick();assert(!$('budget-note').hidden);assert($('generation-note').hidden);
}
(async()=>{await scenario(true);await scenario(false);await scenario(false,true);await scenario(false,true,true);console.log('Simple workflow DOM: novice layout, fresh creation preserving history, collapsed owner invitation, visible generation budget, owner source, remote login, token-free access, upload, safe retries, cancel, history, errors, playback, artifact selection, result/advanced links passed')})().catch(error=>{console.error(error);process.exitCode=1});
