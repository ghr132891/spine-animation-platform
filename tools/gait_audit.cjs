// Numeric diagnostics supplement visual review; they do not certify natural gait.
const {pose}=require('./verify_rig_runtime.cjs');
function correlation(a,b){
 const average=x=>x.reduce((s,v)=>s+v,0)/x.length,am=average(a),bm=average(b);
 const aa=a.map(v=>v-am),bb=b.map(v=>v-bm),den=Math.sqrt(aa.reduce((s,v)=>s+v*v,0)*bb.reduce((s,v)=>s+v*v,0));
 return den>1e-8?aa.reduce((s,v,i)=>s+v*bb[i],0)/den:null;
}
function audit(spine,version,skeleton,animation,options){
 const clip=skeleton.data.findAnimation(animation);if(!clip||!clip.duration)throw Error('Gait audit requires an existing nonzero-duration animation');
 const pairs=options.pairs,axis=options.axis||'x';
 if(!Array.isArray(pairs)||!pairs.length||pairs.length>8)throw Error('Gait pairs must contain 1–8 same-side arm/leg bone mappings');
 if(!['x','y'].includes(axis))throw Error('Gait axis must be x or y');
 const names=new Set(skeleton.bones.map(b=>b.data.name));
 if(!names.has(options.anchor))throw Error('Gait anchor bone not found');
 for(const p of pairs){
  if(!p||!names.has(p.arm)||!names.has(p.leg))throw Error('Gait arm/leg bone not found');
  for(const key of ['arm_endpoint','leg_endpoint'])if(p[key]!==undefined&&typeof p[key]!=='boolean')throw Error('Endpoint selectors must be boolean');
 }
 const frames=Array.from({length:32},(_,i)=>pose(spine,version,skeleton,animation,clip.duration*i/32));
 const value=(frame,name,endpoint)=>{const b=frame.bones.find(b=>b.name===name),anchor=frame.bones.find(b=>b.name===options.anchor);return b[endpoint?(axis==='x'?'ex':'ey'):axis]-anchor[axis]};
 const results=pairs.map(p=>{
  const arm=frames.map(f=>value(f,p.arm,p.arm_endpoint!==false)),leg=frames.map(f=>value(f,p.leg,p.leg_endpoint===true));
  const zero=correlation(arm,leg);let best=zero,offset=0;
  if(zero!==null)for(let i=1;i<32;i++){const r=correlation(arm.map((_,j)=>arm[(j+i)%32]),leg);if(r<best){best=r;offset=i}}
  return {arm:p.arm,leg:p.leg,axis,samples:32,correlation:zero,bestOppositionCorrelation:best,armShiftSamples:offset,
   armRange:Math.max(...arm)-Math.min(...arm),legRange:Math.max(...leg)-Math.min(...leg),
   assessment:zero===null?'insufficient-motion':zero<-.4?'opposed':zero>.4?'same-direction':'phase-offset-or-small-motion',
   suggestedArmPhaseAdvanceSeconds:offset*clip.duration/32};
 });
 const start=frames[0],end=pose(spine,version,skeleton,animation,clip.duration);
 const loopMaxVertexError=start.flat.length===end.flat.length?start.flat.reduce((error,v,i)=>Math.max(error,Math.abs(v-end.flat[i])),0):null;
 return {animation,duration:clip.duration,anchor:options.anchor,pairs:results,loopMaxVertexError,
  scope:'Sampled same-side bone displacement relative to anchor on the selected axis; confirm bone mapping, perspective, contacts and visual quality separately.'};
}
module.exports={audit,correlation};
