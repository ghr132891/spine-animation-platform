// Local official-runtime contact diagnostics. No generation, upload, network or mutations.
const fs=require('fs'),path=require('path'),crypto=require('crypto'),cp=require('child_process');
const {runtime,load,pose}=require('./verify_rig_runtime.cjs');
const finite=x=>typeof x==='number'&&Number.isFinite(x),mod=x=>((x%1)+1)%1;
const mean=a=>a.reduce((s,v)=>s+v,0)/a.length;
const median=a=>{const b=a.slice().sort((x,y)=>x-y);return b.length%2?b[(b.length-1)/2]:(b[b.length/2-1]+b[b.length/2])/2};
const spread=a=>Math.max(...a)-Math.min(...a);
class Unassessed extends Error{constructor(code){super(code);this.code=code}}
function safeFile(folder,name){
  const target=fs.realpathSync(path.resolve(folder,name));
  if(!target.startsWith(folder+path.sep))throw new Unassessed('resource-path-outside-export');
  return target;
}
function triangleIndex(g){
  const list=[];
  for(let i=0;i<g.tri.length;i+=3){
    const ids=g.tri.slice(i,i+3),[[ax,ay],[bx,by],[cx,cy]]=ids.map(id=>g.uv.slice(id*2,id*2+2));
    const denominator=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy);
    if(Math.abs(denominator)>1e-14)list.push({ids,ax,ay,bx,by,cx,cy,denominator,
      minX:Math.min(ax,bx,cx),maxX:Math.max(ax,bx,cx),minY:Math.min(ay,by,cy),maxY:Math.max(ay,by,cy)});
  }
  return list;
}
function locate([u,v],list){
  for(const t of list){
    if(u<t.minX-1e-7||u>t.maxX+1e-7||v<t.minY-1e-7||v>t.maxY+1e-7)continue;
    const a=((t.by-t.cy)*(u-t.cx)+(t.cx-t.bx)*(v-t.cy))/t.denominator;
    const b=((t.cy-t.ay)*(u-t.cx)+(t.ax-t.cx)*(v-t.cy))/t.denominator,c=1-a-b;
    if(Math.min(a,b,c)>=-1e-7)return {ids:t.ids,weights:[a,b,c]};
  }
  return null;
}
const world=(p,g)=>[0,1].map(axis=>p.ids.reduce((s,id,n)=>s+p.weights[n]*g.xy[2*id+axis],0));
function footWeights(spine,skeleton,slot,attachment,foot,count){
  if(!attachment.bones)return Array(count).fill(slot.bone.data.name===foot?1:0);
  let cursor=0,vertex=0;const weights=[],index=skeleton.findBone(foot).data.index;
  for(let i=0;i<count;i++){
    const n=attachment.bones[cursor++];let w=0,total=0;
    if(!Number.isInteger(n)||n<1)throw new Unassessed('unsupported-foot-weights');
    for(let j=0;j<n;j++){
      const bone=attachment.bones[cursor++];
      const weight=attachment.vertices[vertex+2];
      if(!finite(weight)||weight<0||weight>1+1e-5)throw new Unassessed('unsupported-foot-weights');
      total+=weight;if(bone===index)w+=weight;vertex+=3;
    }
    if(Math.abs(total-1)>1e-4)throw new Unassessed('unsupported-foot-weights');
    weights.push(w);
  }
  if(cursor!==attachment.bones.length||vertex!==attachment.vertices.length)throw new Unassessed('unsupported-foot-weights');
  return weights;
}
function contour(folder,atlasName,g,spine,skeleton,pair,options){
  const slot=skeleton.findSlot(pair.slot),attachment=slot?.getAttachment();
  if(!attachment||!finite(g.color[3])||g.color[3]<1-1e-6)throw new Unassessed('foot-attachment-not-opaque');
  const file=safeFile(folder,path.join(path.dirname(atlasName),g.page)),data=fs.readFileSync(file);
  const width=data.readUInt32BE(16),height=data.readUInt32BE(20),u=g.uv.filter((_,i)=>!(i%2)),v=g.uv.filter((_,i)=>i%2);
  const bounds=[Math.max(0,Math.floor(Math.min(...u)*width)),Math.max(0,Math.floor(Math.min(...v)*height)),
    Math.min(width,Math.ceil(Math.max(...u)*width)),Math.min(height,Math.ceil(Math.max(...v)*height))];
  const process=cp.spawnSync(options.python,[path.join(__dirname,'contact_alpha.py')],{
    input:JSON.stringify({page:file,bounds,alpha_threshold:options.alpha_threshold}),encoding:'utf8',timeout:5000,maxBuffer:3*1024*1024});
  if(process.status!==0)throw new Unassessed('alpha-boundary-unavailable');
  let pixels;try{pixels=JSON.parse(process.stdout).points}catch{throw new Unassessed('alpha-boundary-unavailable')}
  const list=triangleIndex(g),weights=footWeights(spine,skeleton,slot,attachment,pair.foot,g.uv.length/2),points=[];
  for(const pixel of pixels){
    const p=locate([pixel[0]/width,pixel[1]/height],list);if(!p)continue;
    const weight=p.ids.reduce((s,id,n)=>s+p.weights[n]*weights[id],0);
    if(weight<.98)continue;
    p.setup=world(p,g);p.pixel=pixel;points.push(p);
  }
  if(points.length<8)throw new Unassessed('insufficient-rigid-foot-pixels');
  if(points.length>4096)throw new Unassessed('foot-contour-limit');
  // World-space lower envelope for sole orientation; retain complete boundary for contact.
  const gx=Math.max(...g.xy.filter((_,i)=>!(i%2)))-Math.min(...g.xy.filter((_,i)=>!(i%2))),
    gy=Math.max(...g.xy.filter((_,i)=>i%2))-Math.min(...g.xy.filter((_,i)=>i%2));
  const pixelScale=Math.max(Math.min(gx/(bounds[2]-bounds[0]),gy/(bounds[3]-bounds[1])),1e-5);
  const bins=new Map();
  points.forEach((p,id)=>{const bin=Math.round(p.setup[0]/pixelScale),previous=bins.get(bin);if(previous===undefined||p.setup[1]<points[previous].setup[1])bins.set(bin,id)});
  let selected=[...bins.values()].sort((a,b)=>points[a].setup[0]-points[b].setup[0]);
  selected=selected.slice(Math.floor(selected.length*.1),Math.ceil(selected.length*.9));
  if(selected.length<8)throw new Unassessed('sole-line-not-readable');
  const slope=ids=>{
    const xy=ids.map(id=>points[id].setup),mx=mean(xy.map(p=>p[0])),my=mean(xy.map(p=>p[1]));
    return xy.reduce((s,p)=>s+(p[0]-mx)*(p[1]-my),0)/xy.reduce((s,p)=>s+(p[0]-mx)**2,0);
  };
  for(let k=0;k<3;k++){
    const m=slope(selected),offset=median(selected.map(id=>points[id].setup[1]-m*points[id].setup[0]));
    const residual=selected.map(id=>Math.abs(points[id].setup[1]-m*points[id].setup[0]-offset)),limit=Math.max(2*pixelScale,3*median(residual));
    const next=selected.filter((_,i)=>residual[i]<=limit);if(next.length<8)break;selected=next;
  }
  return {points,lineIds:selected,attachment,page:path.relative(folder,file),pixelScale};
}
function samplePoints(profile,g){
  const points=profile.points.map(p=>world(p,g)),xy=profile.lineIds.map(id=>points[id]);
  const mx=mean(xy.map(p=>p[0])),my=mean(xy.map(p=>p[1]));
  return {points,x:mean(points.map(p=>p[0])),y:Math.min(...points.map(p=>p[1])),
    angle:Math.atan2(xy.reduce((s,p)=>s+(p[0]-mx)*(p[1]-my),0),xy.reduce((s,p)=>s+(p[0]-mx)**2,0))*180/Math.PI};
}
function correlation(a,b){
  const am=mean(a),bm=mean(b),den=Math.sqrt(a.reduce((s,v)=>s+(v-am)**2,0)*b.reduce((s,v)=>s+(v-bm)**2,0));
  return den>1e-10?a.reduce((s,v,i)=>s+(v-am)*(b[i]-bm),0)/den:null;
}
function comparableFoot(skeleton,frame,c){
  const g=frame.geometry.find(g=>g.slot===c.pair.slot),slot=skeleton.findSlot(c.pair.slot);
  if(!g||slot.getAttachment()!==c.profile.attachment||g.path!==c.g.path||g.page!==c.g.page||g.xy.length!==c.g.xy.length||JSON.stringify(g.uv)!==JSON.stringify(c.g.uv)||JSON.stringify(g.tri)!==JSON.stringify(c.g.tri))throw new Unassessed('contact-attachment-or-topology-changes');
  // Keep the same opaque texel set. Dynamic transparency needs a separate effective-alpha tracker.
  if(!finite(g.color[3])||g.color[3]<1-1e-6)throw new Unassessed('foot-attachment-not-opaque');
  const foot=skeleton.findBone(c.pair.foot),determinant=foot.a*foot.d-foot.b*foot.c;
  if(!finite(determinant)||Math.abs(determinant)<1e-9)throw new Unassessed('degenerate-foot-transform');
  return g;
}
function run(folder,version,animation,options){
  const source=JSON.parse(fs.readFileSync(safeFile(folder,options.skeleton_name),'utf8'));
  if(version==='4.2')for(const timelines of Object.values(source.animations?.[animation]?.bones||{}))for(const f of timelines.rotate||[]){
    if(!Object.hasOwn(f,'value')&&finite(f.angle)&&Math.abs(f.angle)>1e-8)throw new Unassessed('rotate-field-version-mismatch');
  }
  const spine=runtime(version),skeleton=load(spine,version,folder,options.skeleton_name,options.atlas_name);
  if(options.skin){const skin=skeleton.data.findSkin(options.skin);if(!skin)throw new Unassessed('skin-not-found');skeleton.setSkin(skin)}
  const clip=skeleton.data.findAnimation(animation);if(!clip||!finite(clip.duration)||clip.duration<=0||clip.duration>600)throw new Unassessed('animation-not-found-or-no-duration');
  const setup=pose(spine,version,skeleton,'',0),pairs=options.pairs;
  const contexts=pairs.map(pair=>{
    const foot=skeleton.findBone(pair.foot),knee=skeleton.findBone(pair.knee),hip=skeleton.findBone(pair.hip),g=setup.geometry.find(g=>g.slot===pair.slot);
    if(!foot||!knee||!hip||knee.parent!==hip||foot.parent!==knee||!g)throw new Unassessed('mapping-is-not-a-continuous-leg');
    if(g.uv.length/2>8192||g.tri.length>24576)throw new Unassessed('mesh-complexity-limit');
    const length=Math.hypot(knee.worldX-hip.worldX,knee.worldY-hip.worldY)+Math.hypot(foot.worldX-knee.worldX,foot.worldY-knee.worldY);
    if(!finite(length)||length<1e-6)throw new Unassessed('degenerate-leg-chain');
    if(pair.arm&&!skeleton.findBone(pair.arm))throw new Unassessed('arm-mapping-not-found');
    const profile=contour(folder,options.atlas_name,g,spine,skeleton,pair,options);
    return {pair,profile,length,g,rows:[],previous:null,slipCount:0,slipMaximum:0,slipSquareSum:0,slipSpeedMaximum:0,arm:[],leg:[],supportContactCounts:[]};
  });
  let first,last;
  for(let i=0;i<=options.samples;i++){
    const t=clip.duration*i/options.samples,frame=pose(spine,version,skeleton,animation,t);
    if(i===0)first=frame;if(i===options.samples){for(const c of contexts)comparableFoot(skeleton,frame,c);last=frame;break;}
    for(const c of contexts){
      const g=comparableFoot(skeleton,frame,c);
      const p=samplePoints(c.profile,g),cycle=mod(i/options.samples+c.pair.phase),support=cycle<options.stance;
      const touching=p.points.flatMap((xy,id)=>Math.abs(xy[1]-options.ground_y)<=options.contact_tolerance?[id]:[]);
      const row={phase:i/options.samples,time:t,cycle,support,sole_y:p.y,sole_x:p.x,angle:p.angle,clearance:p.y-options.ground_y,touching};
      if(support&&c.previous?.row.support){
        const now=new Set(touching),ids=c.previous.row.touching.filter(id=>now.has(id));c.supportContactCounts.push(ids.length);
        for(const id of ids){const dt=t-c.previous.row.time,error=p.points[id][0]-c.previous.points[id][0]+options.scene_speed*dt;
          c.slipCount++;c.slipMaximum=Math.max(c.slipMaximum,Math.abs(error));c.slipSquareSum+=error*error;c.slipSpeedMaximum=Math.max(c.slipSpeedMaximum,Math.abs(error/dt));}
      }
      c.previous={row,points:p.points};c.rows.push(row);
      if(c.pair.arm){
        const arm=frame.bones.find(b=>b.name===c.pair.arm),ankle=frame.bones.find(b=>b.name===c.pair.foot),hip=frame.bones.find(b=>b.name===c.pair.hip);
        c.arm.push(arm.ex-hip.x);c.leg.push(ankle.x-hip.x);
      }
    }
  }
  // Bound work even when a clip contains many non-bone keyframes.
  const times=new Set([0]),pending=[source.animations[animation]];
  while(pending.length){const value=pending.pop();if(!value||typeof value!=='object')continue;if(finite(value.time)&&value.time>=0&&value.time<clip.duration-1e-6)times.add(value.time);for(const child of Object.values(value))if(child&&typeof child==='object')pending.push(child)}
  const allTimes=[...times].sort((a,b)=>a-b),selected=allTimes.length<=512?allTimes:Array.from({length:512},(_,i)=>allTimes[Math.round(i*(allTimes.length-1)/511)]);
  const epsilon=options.velocity_epsilon_phase,dt=epsilon*clip.duration;
  const feet=contexts.map(c=>{
    const support=c.rows.filter(r=>r.support),swing=c.rows.filter(r=>!r.support);if(support.length<4||!swing.length)throw new Unassessed('insufficient-support-or-swing-samples');
    const position=phase=>{
      const frame=pose(spine,version,skeleton,animation,mod(phase)*clip.duration),g=comparableFoot(skeleton,frame,c);
      return samplePoints(c.profile,g);
    };
    let maximum=0,worst=null;
    for(const time of selected){
      const phase=time/clip.duration,a=position(phase-epsilon),b=position(phase),d=position(phase+epsilon),before=[(b.x-a.x)/dt,(b.y-a.y)/dt],after=[(d.x-b.x)/dt,(d.y-b.y)/dt],jump=after.map((v,i)=>v-before[i]),size=Math.hypot(...jump);
      if(size>=maximum){maximum=size;worst={phase,velocity_before:before,velocity_after:after,difference:jump}}
    }
    const drift=spread(support.map(r=>r.sole_y)),slipRange=spread(support.map(r=>r.sole_x+options.scene_speed*r.cycle*clip.duration));
    const error=Math.max(...support.map(r=>Math.abs(r.clearance))),supportPlane=median(support.map(r=>r.sole_y));
    const contactPassed=error<=options.contact_tolerance&&drift<=options.contact_tolerance;
    const centerSpeedPassed=slipRange/c.length<=options.normalized_slip_limit;
    const persistentSpeedPassed=c.slipCount>0&&c.slipSpeedMaximum*clip.duration/c.length<=options.normalized_slip_limit;
    const speedPassed=centerSpeedPassed&&persistentSpeedPassed;
    return {slot:c.pair.slot,foot:c.pair.foot,hip:c.pair.hip,knee:c.pair.knee,phase:c.pair.phase,
      actual_chain_length:c.length,alpha_boundary_pixels:c.profile.points.length,sole_fit_pixels:c.profile.lineIds.length,
      support:{samples:support.length,sole_height_drift:drift,normalized_height_drift:drift/c.length,minimum_clearance:Math.min(...support.map(r=>r.clearance)),maximum_clearance:Math.max(...support.map(r=>r.clearance)),measured_plane:supportPlane,
        scene_speed:options.scene_speed,slip_range:slipRange,normalized_slip_range:slipRange/c.length,
        persistent_contact_pixels:{comparisons:c.slipCount,minimum_per_adjacent_pair:c.supportContactCounts.length?Math.min(...c.supportContactCounts):0,
          maximum_step_slip:c.slipCount?c.slipMaximum:null,rms_step_slip:c.slipCount?Math.sqrt(c.slipSquareSum/c.slipCount):null,maximum_velocity_error:c.slipCount?c.slipSpeedMaximum:null,
          normalized_maximum_velocity_error:c.slipCount?c.slipSpeedMaximum*clip.duration/c.length:null},
        contact_pixel_count_minimum:Math.min(...support.map(r=>r.touching.length)),contact_pixel_count_maximum:Math.max(...support.map(r=>r.touching.length))},
      sole_angle:{support_minimum:Math.min(...support.map(r=>r.angle)),support_maximum:Math.max(...support.map(r=>r.angle)),whole_cycle_range:spread(c.rows.map(r=>r.angle))},
      swing:{maximum_lift:Math.max(...swing.map(r=>r.clearance)),normalized_maximum_lift:Math.max(...swing.map(r=>r.clearance))/c.length},
      velocity_difference:{maximum,normalized_maximum:maximum/c.length,worst,keyframe_count:allTimes.length,checked_keyframes:selected.length,epsilon_phase:epsilon,
        coverage:allTimes.length<=512?'all-authored-keyframes':'uniformly-sampled-authored-keyframes'},
      arm_foot_correlation:c.pair.arm?correlation(c.arm,c.leg):null,
      checks:{ground_contact:contactPassed,shared_scene_speed:speedPassed,shoe_center_scene_speed:centerSpeedPassed,persistent_contact_speed:persistentSpeedPassed},
      ...(options.include_samples?{samples:c.rows.map(({touching,...r})=>({...r,contact_pixel_count:touching.length}))}:{})};
  });
  const layout=f=>JSON.stringify(f.geometry.map(g=>[g.slot,g.path,g.page,g.xy.length,g.uv,g.color]));
  const comparable=layout(first)===layout(last)&&first.flat.length===last.flat.length;
  const loopError=comparable?first.flat.reduce((e,v,i)=>Math.max(e,Math.abs(v-last.flat[i])),0):null;
  const loop={comparable,maximum_vertex_error:loopError,seamless:comparable&&loopError<1e-3};
  const files=[options.skeleton_name,options.atlas_name,...new Set(contexts.map(c=>c.profile.page))];
  return {status:'assessed',animation,version,skin:options.skin||skeleton.data.defaultSkin?.name||null,duration:clip.duration,samples:options.samples,
    phase_source:options.phase_source,ground_y:options.ground_y,scene_speed:options.scene_speed,stance:options.stance,feet,loop,
    support_plane_difference:spread(feet.map(f=>f.support.measured_plane)),
    shared_support_fraction:mean(Array.from({length:options.samples},(_,i)=>contexts.filter(c=>c.rows[i].support).length>=2?1:0)),
    checks:{ground_contact:feet.every(f=>f.checks.ground_contact),shared_scene_speed:feet.every(f=>f.checks.shared_scene_speed),loop:loop.seamless},
    numerical_checks_passed:feet.every(f=>f.checks.ground_contact&&f.checks.shared_scene_speed)&&loop.seamless,
    source_sha256:Object.fromEntries(files.map(name=>[name,crypto.createHash('sha256').update(fs.readFileSync(safeFile(folder,name))).digest('hex')])),
    limits:{alpha_threshold:options.alpha_threshold,minimum_foot_weight:.98,contact_tolerance:options.contact_tolerance,normalized_slip_limit:options.normalized_slip_limit,
      selected_skin_only:true,maximum_keyframes:512,maximum_foot_pixels:4096,
      velocity_difference:'One-sided finite differences include smooth acceleration at nonzero epsilon. Reduce epsilon to diagnose discontinuities; very small epsilon is limited by runtime precision.',
      contact_measurement:'Opaque texel-center boundary mapped through actual weighted UV triangles. Contact is selected-foot attachment geometry, not a physics collider or the final composited/occluded picture.',
      opaque_attachment_required:true,
      slip_measurement:'Positive scene_speed compensates negative foot world-X velocity for a rightward moving scene; both feet share this single speed. Checks require shoe-center range and persistent same-texel velocity residual, normalized by actual leg length and duration. Rolling contact needs separate pivot/contact-patch review.'}};
}
if(require.main===module){
  try{
    const [folder,version,animation]=process.argv.slice(2),options=JSON.parse(fs.readFileSync(0,'utf8'));
    console.log(JSON.stringify(run(fs.realpathSync(folder),version,animation,options)));
  }catch(error){
    if(error instanceof Unassessed)console.log(JSON.stringify({status:'not_assessed',reason_code:error.code}));
    else console.log(JSON.stringify({status:'failed',reason_code:'runtime-contact-diagnostic-failed'}));
  }
}
module.exports={run};
