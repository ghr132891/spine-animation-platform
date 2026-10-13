/* SVG inspection layer over the official runtime; never edits stored data. */
(function(){
 const NS='http://www.w3.org/2000/svg',math=window.RigBones,fields=['x','y','rotation','length','scaleX','scaleY'];
 let players=[],version='',controls={},frame=0,drag=null,lastHit=null,baseline=null,lastInfo='',lastName='';
 const panes=[],svg=(name,attrs={})=>{const e=document.createElementNS(NS,name);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));return e};
 function post(data){parent.postMessage(data,location.origin)}
 function updateWorld(p){if(version==='4.2')p.skeleton.updateWorldTransform(spine.Physics.update);else p.skeleton.updateWorldTransform()}
 function refreshPose(p){p.skeleton.setToSetupPose();if(!controls.setup)p.animationState.apply(p.skeleton);updateWorld(p)}
 function applyDraft(){
  const p=players[1];if(!p)return;
  if(baseline){const data=p.skeleton.data.findBone(baseline.name);if(data)for(const k of fields)data[k]=baseline.values[k];baseline=null}
  const draft=controls.boneDraft;if(draft&&draft.bone===controls.selectedBone){const data=p.skeleton.data.findBone(draft.bone);if(data){baseline={name:draft.bone,values:Object.fromEntries(fields.map(k=>[k,data[k]]))};for(const k of fields)if(Number.isFinite(draft.values[k]))data[k]=draft.values[k]}}
  refreshPose(p);
 }
 function points(p,pane){const camera=p.sceneRenderer?.camera;if(!camera)return [];const width=pane.node.clientWidth,height=pane.node.clientHeight;return p.skeleton.bones.filter(b=>b.active!==false).map(b=>({name:b.data.name,bone:b,start:math.project(camera,b.worldX,b.worldY,width,height,controls.mirrored),end:math.project(camera,b.worldX+b.a*b.data.length,b.worldY+b.c*b.data.length,width,height,controls.mirrored)}))}
 function runtimeAffected(p,bone){const descendants=new Set([bone]);let size=-1;while(size!==descendants.size){size=descendants.size;for(const b of p.skeleton.bones)if(descendants.has(b.parent))descendants.add(b)}const indices=new Set([...descendants].map(b=>b.data.index));return p.skeleton.slots.filter(slot=>{const a=slot.getAttachment();if(!(a instanceof spine.RegionAttachment)&&!(a instanceof spine.MeshAttachment))return false;if(!a.bones)return descendants.has(slot.bone);let i=0,v=0;while(i<a.bones.length){const n=a.bones[i++];for(let j=0;j<n;j++,v+=3)if(indices.has(a.bones[i++])&&a.vertices[v+2]>1e-6)return true}return false})}
 function outline(p,slot,pane){const a=slot.getAttachment();let xy=[];
  if(a instanceof spine.RegionAttachment){xy=new Array(8).fill(0);a.computeWorldVertices(version==='3.8'?slot.bone:slot,xy,0,2)}
  else if(a instanceof spine.MeshAttachment){xy=new Array(a.worldVerticesLength).fill(0);a.computeWorldVertices(slot,0,a.worldVerticesLength,xy,0,2);if(a.hullLength)xy=xy.slice(0,a.hullLength)}else return null;
  if(!xy.length||xy.length>1024)return null;const camera=p.sceneRenderer.camera,w=pane.node.clientWidth,h=pane.node.clientHeight,projected=[];
  for(let i=0;i<xy.length;i+=2){const v=math.project(camera,xy[i],xy[i+1],w,h,controls.mirrored);if(!Number.isFinite(v.x)||!Number.isFinite(v.y))return null;projected.push(v.x+','+v.y)}
  return svg('polygon',{points:projected.join(' '),fill:'#315fbd','fill-opacity':'.07',stroke:'#315fbd','stroke-width':'1.4','stroke-opacity':'.55'});
 }
 function drawPane(p,pane,index){
  const width=pane.node.clientWidth,height=pane.node.clientHeight;pane.svg.setAttribute('viewBox',`0 0 ${width} ${height}`);pane.svg.replaceChildren();pane.points=points(p,pane);
  const selected=controls.selectedBone?p.skeleton.findBone(controls.selectedBone):null;pane.label.textContent=selected?'已选：'+controls.selectedBone:index===0&&controls.selectedBone?'创建时无同名骨骼':'点击右侧关节点选择骨骼';
  if(!controls.selectedBone||!selected)return;
  const impacted=runtimeAffected(p,selected);if(controls.showInfluence!==false)for(const slot of impacted){const shape=outline(p,slot,pane);if(shape)pane.svg.append(shape)}
  const family=new Set([selected]);let size=-1;while(size!==family.size){size=family.size;for(const b of p.skeleton.bones)if(family.has(b.parent))family.add(b)}
  for(const item of pane.points){if(!family.has(item.bone)||item.bone===selected)continue;if(item.bone.parent){const parentPoint=math.project(p.sceneRenderer.camera,item.bone.parent.worldX,item.bone.parent.worldY,width,height,controls.mirrored);pane.svg.append(svg('line',{x1:parentPoint.x,y1:parentPoint.y,x2:item.start.x,y2:item.start.y,stroke:'#c83768','stroke-width':'1.3','stroke-dasharray':'4 3','stroke-opacity':'.6'}))}}
  const item=pane.points.find(x=>x.bone===selected);if(!item)return;const a=item.start,b=item.end;
  pane.svg.append(svg('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'white','stroke-width':7}),svg('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#c83768','stroke-width':3}),svg('circle',{cx:b.x,cy:b.y,r:4,fill:'#c83768',stroke:'white','stroke-width':2}),svg('circle',{cx:a.x,cy:a.y,r:8,fill:'#c83768',stroke:'white','stroke-width':3}),svg('circle',{cx:a.x,cy:a.y,r:12,fill:'none',stroke:'#c83768','stroke-width':1.5}));
  const boxWidth=Math.min(width-16,Math.max(100,controls.selectedBone.length*7+22)),x=Math.max(8,Math.min(width-boxWidth-8,a.x+16)),y=Math.max(10,Math.min(height-30,a.y-30));pane.svg.append(svg('rect',{x,y,width:boxWidth,height:23,rx:4,fill:'white','fill-opacity':'.95'}));const text=svg('text',{x:x+8,y:y+16,fill:'#9d2751','font-size':12,'font-family':'Avenir Next, PingFang SC, sans-serif'});text.textContent=controls.selectedBone;pane.svg.append(text);
  if(index===1){const info={name:selected.data.name,parent:selected.parent?.data.name||'',slots:impacted.map(s=>s.data.name),children:p.skeleton.bones.filter(b=>b.parent===selected).map(b=>b.data.name),active:selected.active!==false};const key=JSON.stringify(info);if(key!==lastInfo){lastInfo=key;post({type:'bone-info',...info})}}
 }
 function localAt(event){const pane=panes[1],p=players[1],rect=pane.node.getBoundingClientRect();const world=math.unproject(p.sceneRenderer.camera,event.clientX-rect.left,event.clientY-rect.top,rect.width,rect.height,controls.mirrored);const b=p.skeleton.findBone(drag.name);if(b.parent)b.parent.worldToLocal(world);else{world.x-=p.skeleton.x;world.y-=p.skeleton.y}return world}
 function pointerDown(e){if(e.button!==0)return;const pane=panes[1],rect=pane.node.getBoundingClientRect(),point={x:e.clientX-rect.left,y:e.clientY-rect.top},hits=math.hitTest(pane.points||[],point);if(!hits.length)return;
  const best=hits[0].distance,overlaps=hits.filter(h=>h.distance<=best+3);let hit=hits[0];if(lastHit&&Math.hypot(lastHit.x-point.x,lastHit.y-point.y)<5&&overlaps.length>1){const previous=overlaps.findIndex(h=>h.name===lastHit.name);hit=overlaps[(previous+1)%overlaps.length]}
  // In setup pose the current joint takes priority, allowing overlapping control
  // bones to be dragged after choosing them from the list.
  if(controls.setup){const current=overlaps.find(h=>h.name===controls.selectedBone&&Math.hypot(h.start.x-point.x,h.start.y-point.y)<=14);if(current)hit=current}
  lastHit={...point,name:hit.name};e.preventDefault();e.stopPropagation();
  if(hit.name!==controls.selectedBone){post({type:'select-bone',name:hit.name});return}
  if(!controls.setup){post({type:'bone-hint',message:'已定位 '+hit.name+'。点击“初始姿态定位”后，可拖动关节点。'});return}
  if(Math.hypot(hit.start.x-point.x,hit.start.y-point.y)>16)return;
  drag={name:hit.name,pointer:e.pointerId};const local=localAt(e);drag.offsetX=hit.bone.x-local.x;drag.offsetY=hit.bone.y-local.y;pane.node.setPointerCapture?.(e.pointerId);post({type:'bone-drag-start',name:hit.name});
 }
 function pointerMove(e){if(!drag||e.pointerId!==drag.pointer)return;e.preventDefault();const p=localAt(e);post({type:'bone-drag',name:drag.name,x:p.x+drag.offsetX,y:p.y+drag.offsetY})}
 function pointerUp(e){if(!drag||e.pointerId!==drag.pointer)return;panes[1].node.releasePointerCapture?.(e.pointerId);drag=null;post({type:'bone-drag-end'})}
 function tick(){frame=requestAnimationFrame(tick);if(document.hidden)return;for(let i=0;i<players.length;i++)if(players[i])drawPane(players[i],panes[i],i)}
 window.BoneOverlay={start(items,runtime){players=items;version=runtime;for(const id of ['before','after']){const node=document.getElementById(id),layer=svg('svg',{'aria-hidden':'true'}),label=document.createElement('div');layer.classList.add('bone-overlay');label.className='bone-caption';node.append(layer,label);panes.push({node,svg:layer,label})}panes[1].node.addEventListener('pointerdown',pointerDown,true);panes[1].node.addEventListener('pointermove',pointerMove);panes[1].node.addEventListener('pointerup',pointerUp);panes[1].node.addEventListener('pointercancel',pointerUp);tick()},setControls(value){controls=value;if(lastName!==value.selectedBone){lastName=value.selectedBone;lastInfo=''}applyDraft()},stop(){cancelAnimationFrame(frame)}};
})();
