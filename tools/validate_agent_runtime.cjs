// Match the production runtimes; report actual motion separately from valid geometry.
const fs=require('fs'),path=require('path');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const [folder,version,skeletonName='skeleton.json',atlasName='skeleton.atlas',previewClip,optionsText='{}']=process.argv.slice(2);

function rotationDiagnostics(source,version){
  if(version!=='4.2')return [];
  const issues=[];
  for(const [animation,clip]of Object.entries(source.animations||{}))for(const [bone,timelines]of Object.entries(clip.bones||{})){
    for(const [index,frame]of (timelines.rotate||[]).entries()){
      if(frame && Object.hasOwn(frame,'angle') && !Object.hasOwn(frame,'value') && Number.isFinite(Number(frame.angle)) && Math.abs(Number(frame.angle))>1e-8){
        issues.push({code:'rotate-field-version-mismatch',severity:'error',runtime:'4.2',animation,bone,keyframe:index,
          time:frame.time??0,providedField:'angle',expectedField:'value',providedValue:frame.angle,
          repair:'Use value for this Spine 4.2 rotate keyframe, then reload the official runtime and inspect actual motion.'});
      }
    }
  }
  return issues;
}

function samplingTimes(clip,source){
  const duration=clip.duration,times=Array.from({length:33},(_,i)=>duration*i/32),keys=new Set();
  const pending=[source];
  while(pending.length){
    const value=pending.pop();if(!value||typeof value!=='object')continue;
    if(typeof value.time==='number' && Number.isFinite(value.time) && value.time>=0 && value.time<=duration+1e-6)keys.add(Math.min(value.time,duration));
    for(const item of Object.values(value))if(item&&typeof item==='object')pending.push(item);
  }
  const actual=Array.from(keys).sort((a,b)=>a-b),limit=512;
  const selected=actual.length<=limit?actual:Array.from({length:limit},(_,i)=>actual[Math.round(i*(actual.length-1)/(limit-1))]);
  times.push(...selected);
  const merged=times.sort((a,b)=>a-b).filter((time,i,all)=>!i||time-all[i-1]>1e-7);
  if(!merged.length||duration-merged.at(-1)>0)merged.push(duration);
  return {times:merged,keyframeTimes:actual.length,includedKeyframeTimes:selected.length,
    coverage:actual.length<=limit?'uniform-and-keyframes':'uniform-and-sampled-keyframes'};
}

function capture(spine,version,skeleton,clip,time){
  const frame=pose(spine,version,skeleton,clip,time);
  if(frame.bones.some(b=>![b.x,b.y,b.ex,b.ey].every(Number.isFinite)))throw Error('Nonfinite bone transform');
  const directions=skeleton.bones.map(b=>Math.hypot(b.a,b.c)>1e-10?Math.atan2(b.c,b.a)*180/Math.PI:null);
  const relative=skeleton.bones.map((bone,i)=>{
    const p=bone.parent;if(!p)return null;
    const det=p.a*p.d-p.b*p.c;if(Math.abs(det)<1e-12)return null;
    const local=(x,y)=>[(p.d*x-p.b*y)/det,(p.a*y-p.c*x)/det];
    const origin=local(bone.worldX-p.worldX,bone.worldY-p.worldY);
    const end=local(frame.bones[i].ex-p.worldX,frame.bones[i].ey-p.worldY),axis=local(bone.a,bone.c);
    return {origin,end,direction:Math.hypot(...axis)>1e-10?Math.atan2(axis[1],axis[0])*180/Math.PI:null};
  });
  const root=skeleton.bones.find(bone=>!bone.parent),det=root?root.a*root.d-root.b*root.c:0;
  let rootRelativeGeometry=null;
  if(root&&Number.isFinite(det)&&Math.abs(det)>1e-12){
    rootRelativeGeometry=[];
    for(let i=0;i<frame.flat.length;i+=2){
      const x=frame.flat[i]-root.worldX,y=frame.flat[i+1]-root.worldY;
      rootRelativeGeometry.push((root.d*x-root.b*y)/det,(root.a*y-root.c*x)/det);
    }
    if(!rootRelativeGeometry.every(Number.isFinite))rootRelativeGeometry=null;
  }
  return {frame,directions,relative,rootRelativeGeometry,rootName:root?.data.name??null};
}

const spread=values=>{const good=values.filter(Number.isFinite);return good.length?Math.max(...good)-Math.min(...good):null};
function directionSpread(values){
  const angles=[];let previous=null;
  for(let angle of values){
    if(!Number.isFinite(angle))continue;
    if(previous!==null){while(angle-previous>180)angle-=360;while(angle-previous< -180)angle+=360;}
    angles.push(angle);previous=angle;
  }
  return spread(angles);
}
const geometryLayout=frame=>JSON.stringify(frame.geometry.map(g=>[g.slot,g.path,g.xy.length]));
const appearance=frame=>JSON.stringify(frame.geometry.map(g=>[g.slot,g.path,g.page,g.color,g.uv]));

function summarizeMotion(skeleton,clip,skin,captures,sampling){
  const epsilon=1e-4,first=captures[0].frame,last=captures.at(-1).frame,layout=geometryLayout(first),look=appearance(first);
  const firstRootGeometry=captures[0].rootRelativeGeometry;
  let geometryMaxDisplacement=0,geometryComparable=true,appearanceChanges=false,
    rootRelativeGeometryMaxDisplacement=0,rootRelativeGeometryComparable=!!firstRootGeometry?.length;
  for(const {frame,rootRelativeGeometry}of captures){
    appearanceChanges ||= appearance(frame)!==look;
    if(geometryLayout(frame)!==layout||frame.flat.length!==first.flat.length){geometryComparable=false;rootRelativeGeometryComparable=false;continue;}
    for(let i=0;i<first.flat.length;i++)geometryMaxDisplacement=Math.max(geometryMaxDisplacement,Math.abs(frame.flat[i]-first.flat[i]));
    if(!firstRootGeometry||!rootRelativeGeometry||rootRelativeGeometry.length!==firstRootGeometry.length){rootRelativeGeometryComparable=false;continue;}
    for(let i=0;i<firstRootGeometry.length;i++)rootRelativeGeometryMaxDisplacement=Math.max(rootRelativeGeometryMaxDisplacement,Math.abs(rootRelativeGeometry[i]-firstRootGeometry[i]));
  }
  const bones=skeleton.bones.map((bone,i)=>{
    const poses=captures.map(c=>c.frame.bones[i]),locals=captures.map(c=>c.relative[i]);
    return {name:bone.data.name,parent:bone.parent?.data.name??null,length:bone.data.length,
      originRangeX:spread(poses.map(b=>b.x)),originRangeY:spread(poses.map(b=>b.y)),
      endRangeX:spread(poses.map(b=>b.ex)),endRangeY:spread(poses.map(b=>b.ey)),
      worldDirectionRangeDegrees:directionSpread(captures.map(c=>c.directions[i])),
      parentRelativeOriginRangeX:spread(locals.map(b=>b?.origin[0])),parentRelativeOriginRangeY:spread(locals.map(b=>b?.origin[1])),
      parentRelativeEndRangeX:spread(locals.map(b=>b?.end[0])),parentRelativeEndRangeY:spread(locals.map(b=>b?.end[1])),
      parentRelativeDirectionRangeDegrees:directionSpread(locals.map(b=>b?.direction))};
  });
  const changing=(bone,keys)=>keys.some(key=>Number.isFinite(bone[key])&&bone[key]>epsilon);
  const skeletalMotionObserved=bones.some(b=>changing(b,['originRangeX','originRangeY','endRangeX','endRangeY','worldDirectionRangeDegrees']));
  const articulatedMotionObserved=bones.some(b=>b.parent&&changing(b,['parentRelativeOriginRangeX','parentRelativeOriginRangeY','parentRelativeEndRangeX','parentRelativeEndRangeY','parentRelativeDirectionRangeDegrees']));
  const geometryMotionObserved=geometryMaxDisplacement>epsilon,visibleMotionObserved=geometryMotionObserved||appearanceChanges;
  const rootRelativeGeometryMotionObserved=rootRelativeGeometryMaxDisplacement>epsilon;
  const moving=geometryMotionObserved||skeletalMotionObserved;
  const sameGeometryLayout=geometryLayout(last)===layout&&last.flat.length===first.flat.length;
  const maximumVertexError=sameGeometryLayout?first.flat.reduce((error,value,i)=>Math.max(error,Math.abs(value-last.flat[i])),0):null;
  const maximumBoneError=first.bones.reduce((error,bone,i)=>Math.max(error,...['x','y','ex','ey'].map(key=>Math.abs(bone[key]-last.bones[i][key]))),0);
  const maximumBoneDirectionErrorDegrees=captures[0].directions.reduce((error,angle,i)=>{
    const other=captures.at(-1).directions[i];if(!Number.isFinite(angle)||!Number.isFinite(other))return error;
    return Math.max(error,Math.abs(((other-angle+540)%360)-180));
  },0);
  const sameDrawOrder=JSON.stringify(first.geometry.map(g=>g.slot))===JSON.stringify(last.geometry.map(g=>g.slot));
  const sameAppearance=appearance(last)===look;
  const rootOnlyMotion=moving&&skeletalMotionObserved&&!articulatedMotionObserved;
  return {skin:skin?.name??null,animation:clip.name,duration:clip.duration,samples:captures.length,
    sampling:{uniformIntervals:32,keyframeTimes:sampling.keyframeTimes,includedKeyframeTimes:sampling.includedKeyframeTimes,coverage:sampling.coverage},
    moving,geometryMotionObserved,visibleMotionObserved,skeletalMotionObserved,articulatedMotionObserved,rootOnlyMotion,appearanceChanges,geometryComparable,geometryMaxDisplacement,
    rootBone:captures[0].rootName,rootRelativeGeometryMotionObserved,rootRelativeGeometryComparable,rootRelativeGeometryMaxDisplacement,
    diagnostic:!moving?(appearanceChanges?'appearance-only-runtime-clip':'static-runtime-clip'):!visibleMotionObserved?'skeletal-motion-with-static-render':rootOnlyMotion?'root-only-runtime-motion':articulatedMotionObserved&&geometryMotionObserved&&rootRelativeGeometryComparable&&!rootRelativeGeometryMotionObserved?'articulation-with-root-only-render-motion':'runtime-motion-observed',
    bones,loop:{sameGeometryLayout,sameDrawOrder,sameAppearance,maximumVertexError,maximumBoneError,maximumBoneDirectionErrorDegrees,
      seamless:sameDrawOrder&&sameAppearance&&maximumVertexError!==null&&maximumVertexError<1e-3&&maximumBoneError<1e-3&&maximumBoneDirectionErrorDegrees<1e-3}};
}

try {
  const options=JSON.parse(optionsText),frameCount=options.frame_count??8;
  if(!Number.isInteger(frameCount)||frameCount<4||frameCount>32)throw Error('frame_count must be an integer from 4 to 32');
  const source=JSON.parse(fs.readFileSync(path.join(folder,skeletonName),'utf8'));
  const diagnostics=rotationDiagnostics(source,version);
  if(diagnostics.length){
    const first=diagnostics[0],error=Error(`Spine 4.2 ignores nonzero rotate.angle without value: animation ${first.animation}, bone ${first.bone}, keyframe ${first.keyframe}. Replace angle with value and recheck motion.`);
    error.diagnostics=diagnostics;throw error;
  }
  const spine=runtime(version),skeleton=load(spine,version,folder,skeletonName,atlasName);
  let samples=0;const motionClips=[];
  const skins=skeleton.data.skins.length?skeleton.data.skins:[null];
  if(skins.length>64 || skeleton.data.animations.length>128)throw Error('Runtime validation limit exceeded');
  for(const skin of skins){
    if(skin)skeleton.setSkin(skin);
    pose(spine,version,skeleton,'',0);samples++;
    for(const clip of skeleton.data.animations){
      if(!Number.isFinite(clip.duration)||clip.duration>600)throw Error('Invalid clip duration');
      const sampling=samplingTimes(clip,source.animations?.[clip.name]),captures=[];
      for(const time of sampling.times){captures.push(capture(spine,version,skeleton,clip.name,time));samples++;}
      motionClips.push(summarizeMotion(skeleton,clip,skin,captures,sampling));
    }
  }
  let frames=[];
  if(previewClip){
    const clip=skeleton.data.findAnimation(previewClip);
    if(!clip && previewClip!=='__setup__')throw Error('Preview clip not found');
    const skin=skeleton.data.defaultSkin||skins[0];if(skin)skeleton.setSkin(skin);
    if(!options.gait)for(let i=0;i<frameCount;i++)frames.push(pose(spine,version,skeleton,clip?.name||'',(clip?.duration||0)*i/frameCount));
  }
  const gait=options.gait?require('./gait_audit.cjs').audit(spine,version,skeleton,previewClip,options.gait):undefined;
  console.log(JSON.stringify({passed:true,samples,skins:skins.length,animations:skeleton.data.animations.map(a=>a.name),frames,gait,
    motion:{epsilon:1e-4,clips:motionClips,scope:'Actual runtime motion, including parent-relative changes. Static pose clips are allowed. Moving or seamless clips still require visual identity, occlusion, gait and contact review.'},
    scope:'Runtime loading, finite pose geometry and sampled motion diagnostics; passed does not certify an effective walking animation or natural gait.'}));
}catch(e){console.log(JSON.stringify({passed:false,error:String(e.message),...(e.diagnostics?{diagnostics:e.diagnostics}:{})}));process.exit(1)}
