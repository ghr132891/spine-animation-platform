const fs=require('fs'),path=require('path'),assert=require('assert');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const folder=path.resolve('evidence/ankle-seam-fix'),beforeDir=path.resolve('evidence/foot-layer-fix/before'),afterDir=path.join(folder,'after'),spine=runtime('4.2');
const before=load(spine,'4.2',beforeDir,'skeleton.json','skeleton.atlas'),after=load(spine,'4.2',afterDir,'skeleton.json','skeleton.atlas');
let samples=0,maximumVertexError=0;const frames=[];
for(const animation of before.data.animations)for(let i=0;i<=48;i++){
 const t=animation.duration*i/48,a=pose(spine,'4.2',before,animation.name,t),b=pose(spine,'4.2',after,animation.name,t);
 assert.deepEqual(a.bones,b.bones);const original=Object.fromEntries(a.geometry.map(g=>[g.slot,g]));
 for(const g of b.geometry){
  const foot=g.slot.endsWith('-foot-cuff')?g.slot.slice(0,-5):null,source=original[foot||g.slot];
  assert(source);assert.deepEqual(g.tri,source.tri);maximumVertexError=Math.max(maximumVertexError,...g.xy.map((v,j)=>Math.abs(v-source.xy[j])));
  if(foot){const shoe=b.geometry.find(g=>g.slot===foot);assert.deepEqual(g.xy,shoe.xy);assert.equal(after.findSlot(g.slot).bone,after.findSlot(foot).bone);}
 }
 const order=b.geometry.map(g=>g.slot);
 for(const side of ['l','r']){assert(order.indexOf(side+'-foot')<order.indexOf(side+'-lower-leg'));assert.equal(order.indexOf(side+'-lower-leg')+1,order.indexOf(side+'-foot-cuff'))}
 if(animation.name==='walk')frames.push(b);samples++;
}
assert(maximumVertexError<1e-6);assert.equal(after.slots.length,18);
const report={runtime:'4.2',samples,maximumVertexError,jointMotionPreserved:true,frontRimsFollowFeet:true,frontRimOrderPreservedEveryFrame:true,parts:after.slots.length};
fs.writeFileSync(path.join(folder,'runtime-validation.json'),JSON.stringify(report,null,2));
fs.writeFileSync(path.join(folder,'poses.json'),JSON.stringify({dir:afterDir,frames}));console.log(JSON.stringify(report,null,2));
