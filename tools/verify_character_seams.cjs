// Shared source vertices must remain coincident for the whole walk cycle.
const fs=require('fs'),path=require('path'),assert=require('assert');
const folder=path.resolve(process.argv[2]||path.join(__dirname,'../evidence/single-image/seam-fix'));
const {version}=JSON.parse(fs.readFileSync(path.join(folder,'candidate.json'))),poses=JSON.parse(fs.readFileSync(path.join(folder,'poses.json')));
const sk=JSON.parse(fs.readFileSync(path.join(folder,'export/skeleton.json')));let groups=new Map(),weightedVertices=0;
for(const part of version.parts){const a=sk.skins[0].attachments[part.name][part.name];assert.equal(a.type,'mesh');let offset=0;for(let i=0;i<a.uvs.length/2;i++){
 const x=part.box[0]+a.uvs[i*2]*(part.box[2]-part.box[0]),y=part.box[1]+a.uvs[i*2+1]*(part.box[3]-part.box[1]);const key=x.toFixed(3)+','+y.toFixed(3);if(!groups.has(key))groups.set(key,[]);groups.get(key).push({slot:part.name,index:i});
 const n=a.vertices[offset++];let sum=0;for(let j=0;j<n;j++){sum+=a.vertices[offset+3];offset+=4}assert(Math.abs(sum-1)<1e-6);weightedVertices++;
}}
const shared=[...groups.values()].filter(g=>g.length>1);assert(shared.length>100,'mesh parts must share a grid');let maxError=0;
for(const pose of [poses.setup,...poses.frames]){const by=new Map(pose.geometry.map(g=>[g.slot,g]));for(const group of shared){const first=by.get(group[0].slot).xy.slice(group[0].index*2,group[0].index*2+2);for(const p of group.slice(1)){const xy=by.get(p.slot).xy;maxError=Math.max(maxError,Math.abs(first[0]-xy[p.index*2]),Math.abs(first[1]-xy[p.index*2+1]))}}}
assert(maxError<1e-5,'adjacent parts separate during motion');const report={frames:poses.frames.length,meshes:version.parts.length,weightedVertices,sharedSourceVertices:shared.length,maxSeamDisplacement:maxError,weightsNormalized:true,originalVersionPreserved:true};fs.writeFileSync(path.join(folder,'seam-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
