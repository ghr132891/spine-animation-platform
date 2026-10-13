/* Bone selection geometry and JSON bindings, shared by UI and offline tests. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.RigBones=api})(typeof window==='object'?window:globalThis,function(){
 function descendants(skeleton,name){const result=new Set([name]);let size=-1;while(size!==result.size){size=result.size;for(const b of skeleton.bones)if(result.has(b.parent))result.add(b.name)}return result}
 function skinMaps(s){return Array.isArray(s.skins)?s.skins.map(x=>[x.name,x.attachments||{}]):Object.entries(s.skins||{})}
 function affectedSlots(s,name){
  const family=descendants(s,name),indices=new Set(s.bones.map((b,i)=>family.has(b.name)?i:-1).filter(i=>i>=0)),slotBones=new Map((s.slots||[]).map(x=>[x.name,x.bone])),found=new Map(),maps=new Map(skinMaps(s));
  function weighted(a,skin,slot,visited=new Set()){
   if(a.parent&&(a.type==='mesh'||a.type==='linkedmesh')){const key=(a.skin||skin)+'/'+slot+'/'+a.parent;if(visited.has(key))return false;visited.add(key);const parent=maps.get(a.skin||skin)?.[slot]?.[a.parent];return parent?weighted(parent,a.skin||skin,slot,visited):false}
   const v=a.vertices,kind=a.type||'region',count=kind==='mesh'?(a.uvs||[]).length:(a.vertexCount||0)*2;
   if(!v||v.length===count)return family.has(slotBones.get(slot));
   let i=0;while(i<v.length){const n=v[i++];if(!Number.isInteger(n)||n<1||i+n*4>v.length)return false;for(let j=0;j<n;j++,i+=4)if(indices.has(v[i])&&v[i+3]>1e-6)return true}return false;
  }
  for(const [skin,slots] of maps)for(const [slot,items] of Object.entries(slots))for(const a of Object.values(items)){
   const kind=a.type||'region';if(!['region','mesh','linkedmesh'].includes(kind))continue;
   const affected=kind==='region'?family.has(slotBones.get(slot)):weighted(a,skin,slot);
   if(affected)found.set(slot,(kind==='region'?'跟随骨骼':'网格蒙皮'));
  }
  return [...found].map(([slot,kind])=>({slot,kind}));
 }
 function transform(matrix,x,y){const v=matrix.values||matrix,w=v[3]*x+v[7]*y+v[15];return {x:(v[0]*x+v[4]*y+v[12])/w,y:(v[1]*x+v[5]*y+v[13])/w}}
 function project(camera,x,y,width,height,mirrored=false){const p=transform(camera.projectionView,x,y);return {x:(mirrored?1-p.x:p.x+1)*width/2,y:(1-p.y)*height/2}}
 function unproject(camera,x,y,width,height,mirrored=false){if(mirrored)x=width-x;return transform(camera.inverseProjectionView,x/width*2-1,1-y/height*2)}
 function segmentDistance(p,a,b){const dx=b.x-a.x,dy=b.y-a.y,t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));return Math.hypot(p.x-a.x-dx*t,p.y-a.y-dy*t)}
 function hitTest(items,p,radius=14){const joints=items.map(item=>({...item,distance:Math.hypot(p.x-item.start.x,p.y-item.start.y)})).filter(x=>x.distance<=radius);const candidates=joints.length?joints:items.map(item=>({...item,distance:segmentDistance(p,item.start,item.end)})).filter(x=>x.distance<=radius);return candidates.sort((a,b)=>a.distance-b.distance)}
 return {descendants,affectedSlots,project,unproject,hitTest};
});
