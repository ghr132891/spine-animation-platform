// Official Spine 4.2 runtime loaded in Node; no browser automation.
const fs=require('fs'),path=require('path'),assert=require('assert');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const root=path.resolve(__dirname,'..'),folder=process.argv[2]?path.resolve(process.argv[2]):path.join(root,'evidence/single-image'),dir=path.join(folder,'export');
const spine=runtime('4.2'),skeleton=load(spine,'4.2',dir,'skeleton.json','skeleton.atlas'),walk=skeleton.data.findAnimation('walk');
const times=Array.from({length:49},(_,i)=>i*walk.duration/48),poses=times.map(t=>pose(spine,'4.2',skeleton,'walk',t));
const setup=pose(spine,'4.2',skeleton,'__setup__',0);let loopError=Math.max(...poses[0].flat.map((v,i)=>Math.abs(v-poses.at(-1).flat[i])));
assert(loopError<1e-3,'walk must loop seamlessly');
const report={runtime:'4.2',bones:skeleton.bones.length,parts:skeleton.slots.length,duration:walk.duration,loopMaxError:loopError,animations:[],feet:[]};
for(const anim of skeleton.data.animations){for(let i=0;i<=48;i++)pose(spine,'4.2',skeleton,anim.name,anim.duration*i/48);report.animations.push({name:anim.name,samples:49})}
for(const [side,phase]of [['l',0],['r',.5]]){
 const world=p=>p.bones.find(b=>b.name===side+'-foot');let ground=[];for(let i=0;i<poses.length;i++){const cycle=(i/48+phase)%1;if(cycle<.5)ground.push(world(poses[i]).y)}
 const groundDrift=Math.max(...ground)-Math.min(...ground);assert(groundDrift<.12,'support foot drifts vertically');
 const angles=poses.map(p=>{const b=world(p);return Math.atan2(b.ey-b.y,b.ex-b.x)*180/Math.PI});const range=Math.max(...angles)-Math.min(...angles);assert(range<=6.02,'foot sole orientation is unstable');
 const start=world(setup),lift=Math.max(...poses.map(p=>world(p).y))-Math.min(...ground);assert(lift>5,'foot must lift');
 report.feet.push({side,groundVerticalDrift:groundDrift,footAngleRangeDegrees:range,lift});
}
fs.writeFileSync(path.join(folder,'poses.json'),JSON.stringify({setup,frames:poses,dir}));fs.writeFileSync(path.join(folder,'runtime-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
