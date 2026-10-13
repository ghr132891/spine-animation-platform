// Sample the actual AI-task ZIP using the official Spine runtime.
const fs=require('fs'),path=require('path'),assert=require('assert');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const root=path.resolve(__dirname,'..'),folder=path.join(root,'evidence/automation'),task=JSON.parse(fs.readFileSync(path.join(folder,'live-task.json')));
assert.equal(task.state,'completed');assert(task.validation.passed);
const source=task.plan.source,version=source.version.startsWith('3.8')?'3.8':'4.2',spine=runtime(version);
const beforeDir=path.join(root,'storage/projects',task.output_project_id,'original'),afterDir=path.join(folder,'export');
const before=load(spine,version,beforeDir,source.skeleton,source.atlas),after=load(spine,version,afterDir,source.skeleton,source.atlas);
let maxError=0;const animations=[];
for(const anim of after.data.animations){
 for(let i=0;i<=48;i++){
  const a=pose(spine,version,before,anim.name,anim.duration*i/48),b=pose(spine,version,after,anim.name,anim.duration*i/48);
  assert.equal(a.flat.length,b.flat.length);maxError=Math.max(maxError,...a.flat.map((v,j)=>Math.abs(v-b.flat[j])));
 }
 animations.push({name:anim.name,samples:49});
}
assert.equal(maxError,0);
const name=after.data.animations.find(a=>a.name.includes('walk'))?.name||after.data.animations[0]?.name;
fs.writeFileSync(path.join(folder,'pose.json'),JSON.stringify({before:pose(spine,version,before,name,.3),after:pose(spine,version,after,name,.3),beforeDir,afterDir}));
const result={actualMatrixTask:task.jobs[0].gen_id,task:task.id,runtime:version,animations,maxVertexDifference:maxError,skeletonUnchanged:task.validation.skeleton_unchanged,atlasUnchanged:task.validation.atlas_unchanged};
fs.writeFileSync(path.join(folder,'runtime-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
