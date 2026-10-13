// Official runtime geometry, independently prepared data; no browser automation.
const fs=require('fs'),path=require('path'),assert=require('assert');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const folder=path.resolve(__dirname,'../evidence/assembly-review'),spine=runtime('4.2');
const a=load(spine,'4.2',path.join(folder,'baseline'),'skeleton.json','skeleton.atlas'),b=load(spine,'4.2',path.join(folder,'adjusted'),'skeleton.json','skeleton.atlas');
const edit=new Set(['back','l-upper-arm']);let unchangedMaxError=0,changedSlots=new Set(),loopError=0;
const report={runtime:'4.2',parts:b.slots.length,animations:[],sourceProjectMutated:false,aiSubmissions:0};
for(const anim of b.data.animations){let first,last;
 for(let i=0;i<=48;i++){const t=anim.duration*i/48,p=pose(spine,'4.2',a,anim.name,t),q=pose(spine,'4.2',b,anim.name,t);if(i===0)first=q;if(i===48)last=q;
  const before=new Map(p.geometry.map(g=>[g.slot,g]));for(const g of q.geometry){const old=before.get(g.slot);assert(old);
   if(edit.has(g.slot)){if(g.xy.length!==old.xy.length||g.xy.some((v,j)=>Math.abs(v-old.xy[j])>.001))changedSlots.add(g.slot)}
   else{assert.equal(g.xy.length,old.xy.length);unchangedMaxError=Math.max(unchangedMaxError,...g.xy.map((v,j)=>Math.abs(v-old.xy[j])))}}
  assert.deepEqual(q.bones,p.bones,'assembly adjustments must not move bones or alter animation curves');
 }
 assert.equal(first.flat.length,last.flat.length);loopError=Math.max(loopError,...first.flat.map((v,j)=>Math.abs(v-last.flat[j])));report.animations.push({name:anim.name,samples:49});
}
assert(unchangedMaxError<1e-6);assert.equal(changedSlots.size,2);assert(loopError<1e-3);
const expected=JSON.parse(fs.readFileSync(path.join(folder,'review.json'))).draw_order;assert.deepEqual(b.slots.map(s=>s.data.name),expected);
Object.assign(report,{unchangedPartMaxVertexError:unchangedMaxError,changedSlots:[...changedSlots],loopMaxError:loopError,drawOrderApplied:true,boneCurvesUnchanged:true});
const frames=Array.from({length:49},(_,i)=>pose(spine,'4.2',b,'walk',b.data.findAnimation('walk').duration*i/48));
fs.writeFileSync(path.join(folder,'poses.json'),JSON.stringify({setup:pose(spine,'4.2',b,'__setup__',0),frames,dir:path.join(folder,'adjusted')}));
fs.writeFileSync(path.join(folder,'runtime-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
