// Compare the accepted character and its layer variant in the bundled Spine 4.2 runtime.
const fs=require('fs'),path=require('path'),assert=require('assert');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const folder=path.resolve(process.argv[2]||'evidence/foot-layer-fix'),spine=runtime('4.2');
const before=load(spine,'4.2',path.join(folder,'before'),'skeleton.json','skeleton.atlas');
const after=load(spine,'4.2',path.join(folder,'after'),'skeleton.json','skeleton.atlas');
let maximumVertexError=0,samples=0;const frames=[];
for(const animation of before.data.animations){
 for(let i=0;i<=48;i++){
  const time=animation.duration*i/48,a=pose(spine,'4.2',before,animation.name,time),b=pose(spine,'4.2',after,animation.name,time);
  assert.deepEqual(a.bones,b.bones,'layer adjustment changed joint motion');
  const geometry=Object.fromEntries(a.geometry.map(g=>[g.slot,g]));
  for(const g of b.geometry){assert.deepEqual(g.uv,geometry[g.slot].uv);assert.deepEqual(g.tri,geometry[g.slot].tri);maximumVertexError=Math.max(maximumVertexError,...g.xy.map((v,j)=>Math.abs(v-geometry[g.slot].xy[j])))}
  for(const side of ['l','r']){const order=b.geometry.map(g=>g.slot);assert.equal(order.indexOf(side+'-foot')+1,order.indexOf(side+'-lower-leg'))}
  if(animation.name==='walk')frames.push(b);samples++;
 }
}
assert(maximumVertexError<1e-6);
fs.writeFileSync(path.join(folder,'after-poses.json'),JSON.stringify({frames,dir:path.join(folder,'after')}));
const report={runtime:'4.2',samples,maximumVertexError,jointMotionPreserved:true,footBehindOwnShinEveryFrame:true};
fs.writeFileSync(path.join(folder,'layer-runtime-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
