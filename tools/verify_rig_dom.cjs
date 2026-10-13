// DOM + local HTTP integration. No browser tab, browser process, or UI automation.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const {parseHTML}=require('../tests/dom/node_modules/linkedom');
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:8780';
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function api(url,data){const r=await fetch(base+url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Spine-Local':'1'},body:JSON.stringify(data)});if(!r.ok)throw Error(await r.text());return r.json()}
async function wait(fn){for(let i=0;i<150;i++){if(fn())return;await delay(20)}throw Error('DOM integration timeout')}
(async()=>{
 const source=await api('/api/demo',{}),rig=await api('/api/rigs/create',{source_id:source.id,revision:0});
 const {window,document}=parseHTML(fs.readFileSync(path.join(root,'web/rig.html'),'utf8'));
 // LinkeDOM implements a read-only value on selects; emulate native form selection.
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return this.querySelector('option[selected]')?.value||this.querySelector('option')?.value||''},set(v){for(const o of this.querySelectorAll('option'))o.selected=o.value===String(v)}});
 for(const el of document.querySelectorAll('dialog')){el.showModal=function(){this.open=true};el.close=function(){this.open=false}}
 const location={href:base+'/rig.html?rig='+rig.id,search:'?rig='+rig.id,origin:base};const memory=new Map(),messages=[],downloads=[];
 const history={replaceState(_a,_b,url){location.href=String(url);location.search=new URL(url).search}};
 const iframe=document.querySelector('#viewer');iframe.contentWindow={postMessage(data){messages.push(data)}};
 class FileReader {readAsDataURL(file){this.result='data:image/png;base64,'+file.data;queueMicrotask(()=>this.onload())}}
 const FakeURL=URL;FakeURL.createObjectURL=blob=>{downloads.push(blob);return 'blob:qa'};FakeURL.revokeObjectURL=()=>{};
 const RigBones=require('../web/rig-bones.js');window.RigBones=RigBones;
 const context=vm.createContext({window,document,location,history,localStorage:{getItem:k=>memory.get(k),setItem:(k,v)=>memory.set(k,v)},URL:FakeURL,URLSearchParams,Blob,FileReader,console,CSS:{escape:value=>String(value)},RigBones,matchMedia:()=>({matches:false}),fetch:(url,options)=>fetch(new URL(url,base),options),setTimeout,clearTimeout,queueMicrotask});
 const errors=[];process.on('unhandledRejection',e=>errors.push(String(e)));
 vm.runInContext(fs.readFileSync(path.join(root,'web/rig.js'),'utf8'),context);await wait(()=>window.rigState?.().rig);
 const $=s=>document.querySelector(s),state=()=>window.rigState().rig,submit=id=>$(id).onsubmit({preventDefault(){}}),revision=()=>state().revision;
 assert.equal($('#project-name').textContent,rig.name);assert.equal($('#rig-workspace').hidden,false);assert.equal($('#item-list').querySelectorAll('[data-bone]').length,14);
 $('[data-bone="hip"]').onclick();assert.equal(messages.at(-1).selectedBone,'hip');assert($('#focus-impact').textContent.includes('torso'));$('#bone-scaleY').value='1.12';$('#bone-scaleY').oninput();assert.equal(messages.at(-1).boneDraft.values.scaleY,1.12);assert.equal(revision(),0);assert($('#bone-draft-state').textContent.includes('未保存'));$('#discard-bone-draft').onclick();assert.equal(messages.at(-1).boneDraft,null);assert.equal($('#bone-scaleY').value,'1');$('#bone-scaleY').value='1.12';$('#bone-scaleY').oninput();submit('#bone-form');await wait(()=>revision()===1);assert.equal(state().skeleton.bones.find(b=>b.name==='hip').scaleY,1.12);
 const changedSnapshot=state().history[0].id;
 $('#add-bone').onclick();$('#add-name').value='qa-controller';$('#add-parent').value='root';submit('#add-form');await wait(()=>revision()===2);assert(state().skeleton.bones.some(b=>b.name==='qa-controller'));
 $('[data-bone="hip"]').onclick();$('#bone-parent').value='qa-controller';submit('#bone-form');await wait(()=>revision()===3);assert.equal(state().skeleton.bones.find(b=>b.name==='hip').parent,'qa-controller');
 $('[data-list="parts"]').onclick();const head=state().parts.find(p=>p.name==='head');$('[data-part="'+head.id+'"]').onclick();assert.equal($('#part-editor').hidden,false);assert($('#attachment-select').querySelectorAll('option').length>0);
 // Replace with a real fixture, then edit a region attachment in the DOM.
 $('#part-file').files=[{data:fs.readFileSync(path.join(root,'demo/robot.png')).toString('base64')}];$('#resize-geometry').checked=true;await $('#part-file').onchange();await wait(()=>revision()===4);assert.equal(state().parts.find(p=>p.id===head.id).width,512);
 $('#attachment-values [data-key="x"]').value='12';submit('#attachment-form');await wait(()=>revision()===5);
 $('#save').onclick();$('#version-label').value='QA named snapshot';await submit('#save-form');assert.equal(state().history[0].label,'QA named snapshot');
 $('#versions').onclick();const row=$('#history-list [data-snapshot="'+changedSnapshot+'"]');assert(row);await row.querySelector('button').onclick();await wait(()=>revision()===7);assert(!state().skeleton.bones.some(b=>b.name==='qa-controller'));assert.equal(state().parts.find(p=>p.id===head.id).width,98);
 $('#validate').onclick();await wait(()=>$('#validation-dialog').open);assert($('#validation-body').textContent.includes('校验通过'));
 $('#download-skeleton').onclick();await $('#export').onclick();assert(downloads.length===2);
 $('#animation').value='__setup__';$('#animation').onchange();assert(messages.at(-1).setup);
 $('#animation').value='walk';$('#facing').value='mirrored';$('#facing').onchange();assert(messages.at(-1).mirrored&&!messages.at(-1).setup);
 $('#add-part').onclick();$('#add-name').value='qa-extra';$('#add-parent').value='head';$('#add-file').files=[{data:fs.readFileSync(path.join(root,'demo/robot.png')).toString('base64')}];await submit('#add-form');await wait(()=>revision()===8);assert(state().parts.some(p=>p.name==='qa-extra'));
 $('#delete-attachment').onclick();await wait(()=>revision()===9);assert(!state().parts.some(p=>p.name==='qa-extra'));
 $('#slot-select').value='qa-extra';$('#slot-select').onchange();$('#slot-bone').value='root';submit('#slot-form');await wait(()=>revision()===10);assert.equal(state().skeleton.slots.find(s=>s.name==='qa-extra').bone,'root');
 const reopened=await api('/api/rigs/'+rig.id);assert.equal(reopened.revision,10);assert.equal(reopened.history.length,11);
 // Also render the actual weighted project to cover IK forms, attachment labels and AI candidates.
 const meshId=JSON.parse(fs.readFileSync(path.join(root,'evidence/rerig/examples.json'))).find(x=>x.kind==='mesh38').id;
 await vm.runInContext('load("'+meshId+'")',context);assert.equal($('#ik-editor').hidden,false);assert.equal($('#ik-select').querySelectorAll('option').length,8);$('[data-list="parts"]').onclick();assert.equal($('#attachment-values').children.length,0);assert($('#attachment-note').textContent.includes('网格附件'));assert($('#ai-candidate').querySelectorAll('option').length>0);
 // Messages from the known viewer select and move a bone, without writing a version.
 const event=new window.Event('message');Object.assign(event,{origin:base,source:iframe.contentWindow,data:{type:'select-bone',name:'bone8'}});window.dispatchEvent(event);assert.equal(window.rigState().selection,'bone8');assert($('#bone-binding').textContent.includes('网格蒙皮'));$('#locate-setup').onclick();assert(messages.at(-1).setup);
 const meshRevision=state().revision;const drag=new window.Event('message');Object.assign(drag,{origin:base,source:iframe.contentWindow,data:{type:'bone-drag',name:'bone8',x:180,y:12}});window.dispatchEvent(drag);assert.equal($('#bone-x').value,'180.00');assert.equal(messages.at(-1).boneDraft.values.y,12);assert.equal(state().revision,meshRevision);
 const result={loaded:true,boneForms:true,hierarchy:true,texture:true,attachment:true,namedVersion:true,restored:true,reopened:true,export:true,setupAndMirror:true,addDeleteAttachment:true,slotBinding:true,meshAndIK:true,selectedBoneMessage:true,livePreviewNoSave:true,discardPreview:true,canvasSelectAndDragMessage:true,errors};
 fs.writeFileSync(path.join(root,'evidence/rerig/dom-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 // Keep QA resources recoverable outside the user's lists.
 const archive=path.join(root,'evidence/rerig/qa');fs.mkdirSync(archive,{recursive:true});fs.renameSync(path.join(root,'storage/projects',source.id),path.join(archive,'source-'+source.id));fs.renameSync(path.join(root,'storage/rigs',rig.id),path.join(archive,'rig-'+rig.id));
 if(errors.length)process.exit(1);
})().catch(e=>{console.error(e);process.exit(1)});
