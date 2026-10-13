// Load and sample official runtime data in Node; no browser or UI automation.
const fs=require('fs'),path=require('path'),vm=require('vm');
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'evidence/rerig');
function runtime(version){const context=vm.createContext({console,TextDecoder,TextEncoder,Float32Array,Uint16Array,Uint32Array,Int16Array,Int32Array,Uint8Array,ArrayBuffer,Math,setTimeout,clearTimeout});vm.runInContext(fs.readFileSync(path.join(root,'web/runtime',version,'spine-player.js'),'utf8'),context);return context.spine;}
function load(spine,version,dir,skeletonName,atlasName){
 const atlasText=fs.readFileSync(path.join(dir,atlasName),'utf8');
 class Texture {constructor(name,w,h){this.name=name;this._image={width:w,height:h}}getImage(){return this._image}setFilters(){}setWraps(){}dispose(){}}
 let atlas;
 if(version==='3.8')atlas=new spine.TextureAtlas(atlasText,name=>{const png=fs.readFileSync(path.join(dir,path.dirname(atlasName),name));return new Texture(name,png.readUInt32BE(16),png.readUInt32BE(20))});
 else{atlas=new spine.TextureAtlas(atlasText);for(const page of atlas.pages)page.setTexture(new Texture(page.name,page.width,page.height))}
 const reader=new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)),data=reader.readSkeletonData(JSON.parse(fs.readFileSync(path.join(dir,skeletonName),'utf8')));
 return new spine.Skeleton(data);
}
function pose(spine,version,skeleton,animation,time){
 skeleton.setToSetupPose();const a=animation?skeleton.data.findAnimation(animation):null;if(a)a.apply(skeleton,-1,time,false,[],1,spine.MixBlend.replace,spine.MixDirection.mixIn);
 version==='4.2'?skeleton.updateWorldTransform(spine.Physics.update):skeleton.updateWorldTransform();
 const geometry=[],all=[];
 for(const slot of skeleton.drawOrder){const a=slot.getAttachment();if(!a)continue;let xy=[],uv=[],tri=[];
  if(a instanceof spine.RegionAttachment){xy=new Array(8).fill(0);a.computeWorldVertices(version==='3.8'?slot.bone:slot,xy,0,2);uv=Array.from(a.uvs);tri=[0,1,2,2,3,0]}
  else if(a instanceof spine.MeshAttachment){xy=new Array(a.worldVerticesLength).fill(0);a.computeWorldVertices(slot,0,a.worldVerticesLength,xy,0,2);uv=Array.from(a.uvs);tri=Array.from(a.triangles)}else continue;
  if(!xy.every(Number.isFinite))throw Error('nonfinite vertices: '+slot.data.name);
  all.push(...xy);const region=a.region,texture=region?.texture||region?.page?.texture;
  geometry.push({slot:slot.data.name,path:a.path,xy,uv,tri,page:texture?.name||region?.page?.name,color:[slot.color.r*a.color.r,slot.color.g*a.color.g,slot.color.b*a.color.b,slot.color.a*a.color.a]});
 }
 return {geometry,bones:skeleton.bones.map(b=>({name:b.data.name,parent:b.parent?.data.name,x:b.worldX,y:b.worldY,ex:b.worldX+b.a*b.data.length,ey:b.worldY+b.c*b.data.length})),flat:all};
}
if(require.main===module){
const results=[];
for(const item of JSON.parse(fs.readFileSync(path.join(evidence,'examples.json')))){
 const version=item.runtime,spine=runtime(version),beforeDir=path.join(evidence,item.kind,'before'),afterDir=path.join(evidence,item.kind,'after');
 const before=load(spine,version,beforeDir,item.before_skeleton,item.before_atlas),after=load(spine,version,afterDir,'skeleton.json','skeleton.atlas');
 const samples=[];for(const anim of after.data.animations){for(let i=0;i<=32;i++){const p=pose(spine,version,after,anim.name,anim.duration*i/32);if(!p.geometry.length)throw Error('no renderable attachments');}samples.push({animation:anim.name,duration:anim.duration,samples:33})}
 const anim=after.data.animations.find(a=>['walk','run','run2'].includes(a.name))||after.data.animations[0];const time=(anim?.duration||0)*.25;
 const poses={before:pose(spine,version,before,anim?.name,time),after:pose(spine,version,after,anim?.name,time)};
 for(const p of Object.values(poses))delete p.flat;
 fs.writeFileSync(path.join(evidence,item.kind,'pose.json'),JSON.stringify({...poses,animation:anim?.name,time,beforeDir,afterDir}));
 results.push({kind:item.kind,version,bonesBefore:before.bones.length,bonesAfter:after.bones.length,slotsBefore:before.slots.length,slotsAfter:after.slots.length,ikBefore:before.ikConstraints.length,ikAfter:after.ikConstraints.length,samples,sourceUnchanged:item.source_unchanged});
 // Geometry at creation must survive repack, including trimmed weighted meshes.
 const original=JSON.parse(fs.readFileSync(path.join(root,'storage/rigs',item.id,'rig.json'),'utf8')).history[0].state.skeleton;
 const temp=path.join(evidence,item.kind,'creation.json');fs.writeFileSync(temp,JSON.stringify(original));
 const creation=load(spine,version,path.join(evidence,item.kind),'creation.json','after/skeleton.atlas');
 // New parts don't affect old region dimensions; before original bones/weights unchanged.
 let maxError=0;for(const a of before.data.animations){for(let i=0;i<=16;i++){const aPose=pose(spine,version,before,a.name,a.duration*i/16),bPose=pose(spine,version,creation,a.name,a.duration*i/16);if(aPose.flat.length!==bPose.flat.length)throw Error('creation vertex count changed');maxError=Math.max(maxError,...aPose.flat.map((v,j)=>Math.abs(v-bPose.flat[j])))}}
 if(maxError>1e-3)throw Error('repack changed geometry: '+maxError);results.at(-1).creationMaxVertexError=maxError;
}
const spine38=runtime('3.8'),region38=load(spine38,'3.8',path.join(evidence,'region38-rig'),'skeleton.json','skeleton.atlas');for(const a of region38.data.animations)pose(spine38,'3.8',region38,a.name,.2);results.push({kind:'region38',version:'3.8',animations:region38.data.animations.map(a=>a.name),loaded:true});
fs.writeFileSync(path.join(evidence,'runtime-validation.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));
}
module.exports={runtime,load,pose};
