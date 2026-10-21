// Isolated DOM integration: no browser, model or production task is invoked.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const {parseHTML}=require('../tests/dom/node_modules/linkedom');
const root=path.resolve(__dirname,'..'),{window,document}=parseHTML(fs.readFileSync(path.join(root,'web/auto.html'),'utf8'));
const source='a'.repeat(32),target='b'.repeat(32),memory=new Map();
const original={id:source,workflow_id:source,state:'completed',output_scope:'materials',requirements:'已验收素材',created_at:1,step:3,stage:'素材完成',artifacts:[],
 material_delivery:{characters:[{character_id:'c'.repeat(32)}],download_url:'/api/workflows/tasks/'+source+'/download'},
 material_preparation:[{status:'reviewed',message:'素材已验收',panels:[{name:'edges',url:'/edge-proof.png'}]}]};
let record=original,fail=true;const requests=[];
const fetch=async(url,options={})=>{
 const data=options.body?JSON.parse(options.body):null;
 if(url.endsWith('/animate-materials')){requests.push({url,data});if(fail){fail=false;throw Error('temporary connection failure')}
  record={...original,id:target,state:'queued',output_scope:'animation',material_delivery:null,material_preparation:[],stage:'排队等待'};
 }
 const value=url.endsWith('/session')?{connected:true,owner:true,access_mode:'open'}:url.endsWith('/tasks')?[record]:record;
 return {ok:true,status:200,json:async()=>value};
};
const context=vm.createContext({window:{addEventListener(){}},document,location:{origin:'http://localhost',search:'?task='+source},localStorage:{getItem:k=>memory.get(k),setItem:(k,v)=>memory.set(k,v)},URLSearchParams,fetch,crypto:require('crypto').webcrypto,setInterval:()=>1,clearInterval(){},console});
vm.runInContext(fs.readFileSync(path.join(root,'web/auto.js'),'utf8'),context);
(async()=>{
 for(let i=0;i<40&&!document.getElementById('material-stages').querySelector('button');i++)await new Promise(r=>setTimeout(r,5));
 const container=document.getElementById('material-stages'),button=container.querySelector('button');
 assert(button);assert.equal(button.textContent,'用此素材生成待机和行走');assert(container.textContent.includes('边缘放大检查'));
 await button.onclick();assert(!button.disabled);await button.onclick();
 assert.equal(requests.length,2);assert.equal(requests[0].data.nonce,requests[1].data.nonce);
 assert.equal(requests[0].url,'/api/workflows/tasks/'+source+'/animate-materials');
 assert.equal(document.getElementById('stage').textContent,'排队等待');
 assert(!document.getElementById('material-stages').querySelector('button'));
 console.log('Material handoff DOM passed: edge panel, action, stable retry nonce and queued-state update.');
})().catch(error=>{console.error(error);process.exitCode=1});
