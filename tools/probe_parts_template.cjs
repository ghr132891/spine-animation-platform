// Static material probes only: no clips authored and no rig added to the platform.
const fs=require('fs'),path=require('path');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const folder=path.resolve(process.argv[2]);
const spec=JSON.parse(fs.readFileSync(path.join(folder,'template.json')));
const source=JSON.parse(fs.readFileSync(path.join(folder,'template-skeleton.json')));
const version=source.skeleton.spine.startsWith('3.8')?'3.8':'4.2';
const spine=runtime(version),maps=spec.probe_bones;
if(!maps||maps.arms?.length!==2||maps.legs?.length!==2)throw Error('Review both arm and leg root bones for this template first');
const scenes=[['assembly',[]],['arms-open',maps.arms.map((n,i)=>[n,i?25:-25])],
 ['arms-reverse',maps.arms.map((n,i)=>[n,i?-25:25])],
 ['legs-open',maps.legs.map((n,i)=>[n,i?18:-18])],['legs-reverse',maps.legs.map((n,i)=>[n,i?-18:18])]];
const output=[];
for(const [name,rotations]of scenes){
 const s=JSON.parse(JSON.stringify(source));s.animations={};
 for(const ik of s.ik||[])ik.mix=0;
 for(const [n,angle]of rotations){const b=s.bones.find(b=>b.name===n);if(!b)throw Error('Missing probe bone '+n);b.rotation=(b.rotation||0)+angle;}
 fs.writeFileSync(path.join(folder,'probe/skeleton.json'),JSON.stringify(s));
 const skeleton=load(spine,version,path.join(folder,'probe'),'skeleton.json','skeleton.atlas');
 const frame=pose(spine,version,skeleton,null,0);delete frame.flat;output.push({name,...frame});
}
fs.writeFileSync(path.join(folder,'probe/poses.json'),JSON.stringify(output));
