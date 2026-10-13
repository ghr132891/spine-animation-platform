// Offline official-runtime + SVG/DOM unit test. No browser access or rendering.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const {parseHTML}=require('../tests/dom/node_modules/linkedom'),math=require('../web/rig-bones.js'),{runtime,load,pose}=require('./verify_rig_runtime.cjs');
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'evidence/rerig'),results=[];
// Runtime projection matrices use Float32; allow sub-millipixel roundoff.
const near=(a,b)=>assert(Math.abs(a-b)<1e-3,`${a} != ${b}`);
for(const fixture of JSON.parse(fs.readFileSync(path.join(evidence,'examples.json')))){
 const version=fixture.runtime,s=runtime(version),dir=path.join(evidence,fixture.kind,'after');
 const before=load(s,version,path.join(evidence,fixture.kind,'before'),fixture.before_skeleton,fixture.before_atlas),after=load(s,version,dir,'skeleton.json','skeleton.atlas');
 const Camera=version==='3.8'?s.webgl.OrthoCamera:s.OrthoCamera;
 const {window,document}=parseHTML(fs.readFileSync(path.join(root,'web/viewer.html'),'utf8'));window.RigBones=math;const messages=[],queue=[];
 const width=400,height=500;
 function player(skeleton){const camera=new Camera(width,height);camera.position.x=15;camera.position.y=170;camera.zoom=version==='3.8'?3:1;camera.update();const animationState=new s.AnimationState(new s.AnimationStateData(skeleton.data));return {skeleton,sceneRenderer:{camera,skeletonDebugRenderer:{}},animationState,config:{debug:{}},canvas:{style:{}},pause(){this.paused=true},play(){this.paused=false},setAnimation(name){animationState.setAnimation(0,name,true)}}}
 const players=[player(before),player(after)];for(const p of players){p.skeleton.setToSetupPose();version==='3.8'?p.skeleton.updateWorldTransform():p.skeleton.updateWorldTransform(s.Physics.update)}
 for(const pane of document.querySelectorAll('.pane')){Object.defineProperty(pane,'clientWidth',{value:width});Object.defineProperty(pane,'clientHeight',{value:height});pane.getBoundingClientRect=()=>({left:0,top:0,width,height});pane.setPointerCapture=()=>{};pane.releasePointerCapture=()=>{}}
 const context=vm.createContext({window,document,spine:s,parent:{postMessage:d=>messages.push(d)},location:{origin:'http://127.0.0.1:8780'},requestAnimationFrame:fn=>{queue.push(fn);return 1},cancelAnimationFrame(){},console});
 vm.runInContext(fs.readFileSync(path.join(root,'web/bone-overlay.js'),'utf8'),context);
 const selected=version==='3.8'?'bone8':'right-upper-arm',baseline=after.data.findBone(selected).x;
 const controls={selectedBone:selected,showInfluence:true,setup:true,mirrored:false};
 window.BoneOverlay.start(players,version);window.BoneOverlay.setControls(controls);queue.shift()();
 assert(document.querySelector('#after svg').textContent.includes(selected));assert(document.querySelector('#after svg circle'));assert(document.querySelector('#after svg polygon'));assert(messages.some(m=>m.type==='bone-info'&&m.name===selected&&m.slots.length));
 const skeletonJSON=JSON.parse(fs.readFileSync(path.join(dir,'skeleton.json'))),affected=math.affectedSlots(skeletonJSON,selected);assert(affected.length>0);if(version==='3.8')assert(affected.some(x=>x.kind==='网格蒙皮'));
 const camera=players[1].sceneRenderer.camera;for(const mirrored of [false,true])for(const point of [{x:21,y:33},{x:-180,y:222}]){const px=math.project(camera,point.x,point.y,width,height,mirrored),world=math.unproject(camera,px.x,px.y,width,height,mirrored);near(point.x,world.x);near(point.y,world.y)}
 window.BoneOverlay.setControls({...controls,boneDraft:{bone:selected,values:{x:baseline+20}}});near(after.data.findBone(selected).x,baseline+20);near(before.data.findBone(selected).x,baseline);queue.shift()();
 window.BoneOverlay.setControls(controls);near(after.data.findBone(selected).x,baseline);queue.shift()();
 // Inverse parent transform must convert a visual drag into matching local coords,
 // including the reflected parent in the actual mesh fixture.
 const bone=after.findBone(selected);const target={x:bone.x+13,y:bone.y-7},world={...target};bone.parent.localToWorld(world);const pixel=math.project(camera,world.x,world.y,width,height,true),back=math.unproject(camera,pixel.x,pixel.y,width,height,true);bone.parent.worldToLocal(back);near(target.x,back.x);near(target.y,back.y);
 function pointer(type,p,id=8){const event=new window.Event(type,{bubbles:true,cancelable:true});Object.assign(event,{button:0,clientX:p.x,clientY:p.y,pointerId:id});document.querySelector('#after').dispatchEvent(event)}
 // Choose a distant joint, then drag in mirrored setup pose.
 window.BoneOverlay.setControls({...controls,mirrored:true});queue.shift()();const start=math.project(camera,bone.worldX,bone.worldY,width,height,true);pointer('pointerdown',start);pointer('pointermove',{x:start.x+9,y:start.y-6});pointer('pointerup',{x:start.x+9,y:start.y-6});
 const drag=messages.findLast(m=>m.type==='bone-drag');assert(drag&&drag.name===selected);assert(Number.isFinite(drag.x)&&Number.isFinite(drag.y));assert.notEqual(drag.x,baseline);
 const controlsWithDraft={...controls,boneDraft:{bone:selected,values:{x:drag.x,y:drag.y}}};window.BoneOverlay.setControls(controlsWithDraft);queue.shift()();
 const output=document.querySelector('#after svg');output.setAttribute('width',width);output.setAttribute('height',height);fs.writeFileSync(path.join(evidence,fixture.kind,'bone-selection.svg'),output.toString());
 fs.writeFileSync(path.join(evidence,fixture.kind,'inspection-pose.json'),JSON.stringify({pose:pose(s,version,after,'__setup__',0),directory:dir,width,height,camera:{x:camera.position.x,y:camera.position.y,zoom:camera.zoom}}));
 // Cancel draft and ensure all bone data is restored.
 window.BoneOverlay.setControls(controls);near(after.data.findBone(selected).x,baseline);
 // Exercise the production viewer's controls -> overlay -> data path as well.
 context.BoneOverlay=window.BoneOverlay;context.players=players;context.previewReady=true;context.URLSearchParams=URLSearchParams;context.location.search='?rig='+fixture.id+'&version='+version;
 vm.runInContext(fs.readFileSync(path.join(root,'web/viewer.js'),'utf8').replace(/main\(\)\.catch\(.*\);\s*$/,''),context);context.previewReady=true;
 context.testControls={...controls,paused:true,bones:true,mirrored:true,boneDraft:{bone:selected,values:{x:baseline+7}}};vm.runInContext('controls=testControls;apply()',context);near(after.data.findBone(selected).x,baseline+7);near(before.data.findBone(selected).x,baseline);assert.equal(players[1].canvas.style.transform,'scaleX(-1)');assert(players[1].paused);
 context.testControls=controls;vm.runInContext('controls=testControls;apply()',context);near(after.data.findBone(selected).x,baseline);
 results.push({version,selected,affected,highlightSVG:true,meshInfluence:true,projectionAndMirror:true,draftRestored:true,parentLocalCoordinates:true,drag:true,viewerControlsIntegration:true});window.BoneOverlay.stop();
}
fs.writeFileSync(path.join(evidence,'bone-inspector-validation.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));
