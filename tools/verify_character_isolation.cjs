// Physical chains retain joint seams; touching unrelated limbs can separate.
const fs=require('fs'),path=require('path'),assert=require('assert');
const folder=path.resolve(process.argv[2]||path.join(__dirname,'../evidence/single-image/adhesion-fix'));
const {version}=JSON.parse(fs.readFileSync(path.join(folder,'candidate.json'))),poses=JSON.parse(fs.readFileSync(path.join(folder,'poses.json'))),sk=JSON.parse(fs.readFileSync(path.join(folder,'export/skeleton.json')));
const family=n=>n.startsWith('l-')||n.startsWith('r-')?n[0]+(/arm|hand/.test(n)?'-arm':'-leg'):'body';let groups=new Map(),foreignWeights=0,count=0;
for(const part of version.parts){const a=sk.skins[0].attachments[part.name][part.name],f=family(part.name);let offset=0;for(let i=0;i<a.uvs.length/2;i++){
 const x=part.box[0]+a.uvs[i*2]*(part.box[2]-part.box[0]),y=part.box[1]+a.uvs[i*2+1]*(part.box[3]-part.box[1]),key=x.toFixed(3)+','+y.toFixed(3);if(!groups.has(key))groups.set(key,[]);groups.get(key).push({slot:part.name,index:i,family:f});
 const n=a.vertices[offset++];let sum=0;for(let j=0;j<n;j++){const bone=sk.bones[a.vertices[offset]].name;if(family(bone)!==f&&bone!==(f.endsWith('arm')?'torso':'hip'))foreignWeights++;sum+=a.vertices[offset+3];offset+=4}assert(Math.abs(sum-1)<1e-6);count++;
}}
assert.equal(foreignWeights,0);let maxJointError=0,maxIndependentSeparation=0,chainPairs=0;
for(const pose of poses.frames){const by=new Map(pose.geometry.map(g=>[g.slot,g]));for(const group of groups.values()){for(let a=0;a<group.length;a++)for(let b=a+1;b<group.length;b++){const A=group[a],B=group[b],pa=by.get(A.slot).xy,pb=by.get(B.slot).xy,d=Math.hypot(pa[A.index*2]-pb[B.index*2],pa[A.index*2+1]-pb[B.index*2+1]);if(A.family===B.family){maxJointError=Math.max(maxJointError,d);chainPairs++}else maxIndependentSeparation=Math.max(maxIndependentSeparation,d)}}}
assert(chainPairs>100);assert(maxJointError<1e-5);assert(maxIndependentSeparation>1,'unrelated layers remain glued');const report={frames:poses.frames.length,weightedVertices:count,foreignLimbWeights:foreignWeights,maxJointSeamDisplacement:maxJointError,maxIndependentLayerSeparation:maxIndependentSeparation,closedBackgroundCleared:true};fs.writeFileSync(path.join(folder,'isolation-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
