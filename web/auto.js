const $=id=>document.getElementById(id);
const state={mode:'image',image:null,character:null,sourceUrl:null,active:null,artifacts:[],paused:false,frame:null,pending:null,poll:null,connected:false,owner:false,accessMode:'code',reconnecting:false,busy:false,refining:false,refinePending:null};
const terminal=new Set(['completed','failed','cancelled','interrupted']);
const store={get(k){try{return localStorage.getItem('spine-auto-'+k)}catch{return null}},set(k,v){try{localStorage.setItem('spine-auto-'+k,v)}catch{}}};
function notice(text,error=false){$('notice').textContent=text;$('notice').hidden=!text;$('notice').classList.toggle('error',error)}
async function api(path,data){
 const response=await fetch('/api/workflows/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Spine-Local':'1'},body:JSON.stringify(data)});const value=await response.json();
 if(!response.ok){
  if(response.status===401&&!path.startsWith('session')){
   state.connected=false;$('app').hidden=true;$('login').hidden=state.accessMode==='open';
   if(state.accessMode==='open'&&!state.reconnecting){
    state.reconnecting=true;try{await bootstrap()}finally{state.reconnecting=false}
    throw Error(state.connected?'连接已恢复，请重试刚才的操作':'连接已失效，请刷新页面后重试');
   }
  }
  throw Error(value.error||'请求失败');
 }
 return value;
}
function uuid(){const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);return [...bytes].map(v=>v.toString(16).padStart(2,'0')).join('')}
function fingerprint(data){const text=JSON.stringify(data);let hash=2166136261;for(let i=0;i<text.length;i++)hash=Math.imul(hash^text.charCodeAt(i),16777619);return text.length+':'+(hash>>>0).toString(16)}
function busy(value){state.busy=value;document.body.setAttribute('aria-busy',String(value));$('submit').disabled=value||!!state.active&&!terminal.has(state.active.state);$('submit').textContent=value?'正在提交…':state.active&&!terminal.has(state.active.state)?'正在制作…':'生成动画'}
function updateBudget(){const generating=state.mode==='generate';$('budget-note').hidden=generating;$('budget-note').textContent=$('prepare-image').checked?'首次三视图＋部件图另计，另有 8 张补图／修订额度，消耗亿创积分。':'仅使用原图，不调用亿创补图。'}
$('prepare-image').onchange=()=>{state.pending=null;updateBudget()};
function setMode(mode){state.mode=mode;for(const name of ['image','generate']){$('mode-'+name).classList.toggle('active',name===mode);$('mode-'+name).setAttribute('aria-pressed',String(name===mode))}$('upload-section').hidden=mode!=='image';$('generation-note').hidden=mode!=='generate';$('preparation-option').hidden=mode!=='image';$('request-help').textContent=mode==='image'?'例如：走慢一点、步幅小一些。':'请描述角色外观与动作，例如：蓝白色快递机器人，制作行走与待机。';$('request-help').hidden=mode==='image';$('requirements').placeholder=mode==='generate'?'描述角色外观和动作要求':'描述想要的动作';state.pending=null;updateBudget()}
$('mode-image').onclick=()=>setMode('image');$('mode-generate').onclick=()=>setMode('generate');
$('choose-image').onclick=()=>$('image-file').click();
async function choose(file){if(!file)return;try{if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('请选择 PNG、JPG 或 WebP 图片');if(file.size>12*1024*1024)throw Error('图片最大 12 MB');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('图片读取失败，请重新选择'));reader.readAsDataURL(file)});const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error('这张图片无法读取，请重新选择'));image.src=data});if(image.naturalWidth<128||image.naturalHeight<128||Math.max(image.naturalWidth,image.naturalHeight)>4096||image.naturalWidth*image.naturalHeight>8000000)throw Error('请使用 128–4096 像素且不超过 800 万像素的图片');state.image=data.split(',')[1];state.character=null;state.sourceUrl=data;state.pending=null;$('source-preview').src=data;$('source-preview').hidden=false;$('upload-label').textContent='点击更换图片';$('source-label').hidden=false;$('source-label').textContent=file.name+' · '+image.naturalWidth+' × '+image.naturalHeight;notice('')}catch(error){notice(error.message,true)}}
$('image-file').onchange=()=>choose($('image-file').files[0]);
for(const type of ['dragenter','dragover'])$('choose-image').addEventListener(type,event=>{event.preventDefault();$('choose-image').classList.add('drag-over')});
for(const type of ['dragleave','drop'])$('choose-image').addEventListener(type,event=>{event.preventDefault();$('choose-image').classList.remove('drag-over');if(type==='drop')choose(event.dataTransfer.files[0])});
function controls(){const animation=$('animation').value;return {type:'controls',animation,setup:!animation,paused:state.paused||!animation,speed:1,bones:false,mirrored:false,showInfluence:false,selectedBone:''}}
function postControls(){$('viewer').contentWindow?.postMessage(controls(),location.origin)}
$('animation').onchange=postControls;$('pause').onclick=()=>{state.paused=!state.paused;$('pause').textContent=state.paused?'播放':'暂停';postControls()};
window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==$('viewer').contentWindow)return;if(event.data?.type==='ready')postControls();if(event.data?.type==='error')notice(event.data.message||'预览暂时无法加载',true)});
function showArtifact(index){
 const artifact=state.artifacts[index];if(!artifact)return;
 const animations=Array.isArray(artifact.animations)?artifact.animations:[];
 state.paused=false;$('pause').textContent='暂停';
 $('open-editor').href=artifact.rig_id?'/result.html?rig='+encodeURIComponent(artifact.rig_id):artifact.preview_url;
 $('advanced-editor').href=artifact.rig_id?'/rig.html?rig='+encodeURIComponent(artifact.rig_id)+'&advanced=1':artifact.preview_url;
 $('advanced-editor').hidden=!artifact.rig_id&&!artifact.preview_url;
 $('animation').replaceChildren();
 for(const name of animations.length?animations:['']){const option=document.createElement('option');option.value=name;option.textContent=({idle:'待机',walk:'行走',walk_slow:'慢速行走'})[name]||name||'原始造型';$('animation').append(option)}
 if(animations.includes('walk'))$('animation').value='walk';
 $('playback').hidden=!artifact.rig_id;$('result-empty').hidden=!!artifact.rig_id;$('viewer').hidden=!artifact.rig_id;
 if(artifact.rig_id){const src='/character-viewer.html?rig='+encodeURIComponent(artifact.rig_id)+'&version='+encodeURIComponent(artifact.runtime||'4.2')+'&single=1&revision='+encodeURIComponent(artifact.revision??'');if(state.frame!==src){state.frame=src;$('viewer').src=src}else postControls()}
}
$('artifact').onchange=()=>showArtifact(+$('artifact').value);
function friendlyStage(task){
 if(task.delivery_status==='previous')return terminal.has(task.state)?'修改未完成 · 旧版可看':'修改中 · 旧版可看';
 if(task.delivery_status==='draft')return terminal.has(task.state)?task.is_refinement?'修改未完成 · 草稿可看':'已有修复草稿':'修复中 · 草稿可看';
 const completed={queued:'排队等待',completed:'制作完成',failed:'本次未完成',cancelled:'已停止',interrupted:'处理已中断'};
 return completed[task.state]||['准备图片','准备角色','制作动作','检查效果'][task.step]||'正在制作';
}
function render(task){
 const sameTask=state.active?.id===task.id,selected=sameTask?Number($('artifact').value)||0:0;
 const draft=!!task.delivery_status,hasResult=(task.artifacts||[]).length>0&&(task.state==='completed'||draft);
 state.active=task;store.set('last-task',task.id);$('progress').hidden=false;$('stage').textContent=friendlyStage(task);$('stage-detail').textContent=task.stage;
 $('current-request').hidden=!task.current_request;$('current-request').textContent=task.current_request?'本次修改：'+task.current_request:'';
 $('process-link').hidden=!task.process_url;if(task.process_url)$('process-link').href=task.process_url;
 const budget=task.generation_budget||{};if(budget.limit!=null)$('stage-detail').textContent+=' · 基础图 '+(budget.baseline_used_or_reserved||0)+'/'+(budget.baseline_limit||0)+'，补图／修订 '+(budget.used_or_reserved||0)+'/'+budget.limit;
 $('resume').hidden=!['failed','interrupted'].includes(task.state);
 $('attempt-details').hidden=!(task.attempt_count>1);$('attempt-list').replaceChildren();for(const attempt of task.attempts||[]){const li=document.createElement('li');li.textContent=new Date(attempt.created_at*1000).toLocaleString('zh-CN')+' · '+friendlyStage(attempt)+(attempt.error?'：'+attempt.error:'');$('attempt-list').append(li)}
 $('result-title').textContent=task.delivery_status==='previous'?'上次保存的版本':draft?'修复草稿 · 仍有待优化项':task.state==='completed'?'制作完成':'看效果';$('cancel').hidden=terminal.has(task.state);
 for(const [i,item]of [...$('steps').children].entries()){item.classList.toggle('done',task.state==='completed'||i<task.step);item.classList.toggle('current',!terminal.has(task.state)&&i===task.step)}
 $('result-message').textContent=(draft?task.delivery_summary+(task.is_refinement&&task.error&&task.delivery_task_id!==task.id?' 本次修改未完成：'+task.error:''):null)||task.error||(task.state==='completed'?'动画已准备好。看看效果，也可以让 Codex 继续调整。':task.state==='cancelled'?'制作已停止，可以调整需求后重新开始。':'正在后台处理，完成后会显示动画。');
 $('process-detail').hidden=terminal.has(task.state);$('result-description').hidden=!(task.summary||draft&&task.error);$('result-summary').textContent=draft&&task.error?'原自动任务未通过：'+task.error:task.summary||'';$('results').hidden=!hasResult;
 $('refine-form').hidden=!hasResult;$('refine-submit').disabled=state.refining||!terminal.has(task.state);
 if(!state.refining)$('refine-status').textContent=!terminal.has(task.state)?'正在处理，当前草稿仍可查看。':'';
 if(hasResult){
  $('download').href=task.download_url;state.artifacts=task.artifacts||[];$('artifact').replaceChildren();
  for(const [i,a]of state.artifacts.entries()){const option=document.createElement('option');option.value=i;option.textContent='效果 '+(i+1);$('artifact').append(option)}
  $('artifact').hidden=state.artifacts.length<2;const index=Math.min(selected,Math.max(0,state.artifacts.length-1));$('artifact').value=String(index);showArtifact(index);
 }else{
  $('playback').hidden=true;$('viewer').hidden=true;$('result-empty').hidden=false;state.frame=null;
  $('empty-title').textContent=terminal.has(task.state)?'这次制作未完成':'正在让角色动起来';
  $('empty-detail').textContent=terminal.has(task.state)?'查看下方原因，调整图片或需求后再制作。':'可以关闭页面，稍后从“最近制作”回来查看。';
 }
 busy(state.busy);
}
async function historyList(){if(!state.connected)return;const tasks=await api('tasks');$('history-list').replaceChildren();$('history-empty').hidden=tasks.length>0;$('history-count').textContent=tasks.length?'（'+tasks.length+'）':'';for(const task of tasks){const row=document.createElement('button');row.type='button';row.className='history-row';if(task.image_url){const img=document.createElement('img');img.src=task.image_url;img.alt='角色原图';row.append(img)}else{const box=document.createElement('span');box.className='history-placeholder';row.append(box)}const text=document.createElement('span');text.className='history-text';const title=document.createElement('strong');title.textContent=task.requirements;const date=document.createElement('span');date.textContent=new Date(task.created_at*1000).toLocaleString('zh-CN')+(task.delivery_updated_at?' · 草稿更新 '+new Date(task.delivery_updated_at*1000).toLocaleString('zh-CN'):'')+(task.attempt_count>1?' · 内部处理 '+task.attempt_count+' 次':'');text.append(title,date);const status=document.createElement('span');status.className='history-state '+(task.delivery_status==='draft'?'draft':task.state);status.textContent=friendlyStage(task);row.append(text,status);row.onclick=()=>{render(task);startPoll()};$('history-list').append(row)}return tasks}
async function poll(){if(!state.connected||!state.active||terminal.has(state.active.state))return;try{const task=await api('tasks/'+state.active.id);render(task);if(terminal.has(task.state)){clearInterval(state.poll);state.poll=null;await historyList()}}catch(error){notice(error.message,true)}}
function startPoll(){clearInterval(state.poll);state.poll=null;if(state.active&&!terminal.has(state.active.state))state.poll=setInterval(poll,4000)}
$('refresh').onclick=async()=>{try{await historyList();if(state.active)render(await api('tasks/'+state.active.id));startPoll();notice('')}catch(error){notice(error.message,true)}};
$('cancel').onclick=async()=>{if(!state.active)return;try{render(await api('tasks/'+state.active.id+'/cancel',{}));startPoll();await historyList()}catch(error){notice(error.message,true)}};
$('request-form').onsubmit=async event=>{event.preventDefault();if(state.busy||state.active&&!terminal.has(state.active.state))return;let accepted=false;try{const requirements=$('requirements').value.trim();if(requirements.length<5)throw Error('请描述希望制作的动作');const payload={mode:state.mode,requirements,image_preparation:state.mode==='image'&&!$('prepare-image').checked?'preserve':'asset-sheet'};if(state.mode==='image'){if(state.character)payload.character_id=state.character;else if(state.image)payload.image=state.image;else throw Error('请先上传一张角色图片')}const signature=fingerprint(payload);let saved=null;try{saved=JSON.parse(store.get('pending'))}catch{}const nonce=state.pending?.signature===signature?state.pending.nonce:saved?.signature===signature?saved.nonce:uuid();state.pending={signature,nonce};store.set('pending',JSON.stringify(state.pending));busy(true);notice('');const task=await api('tasks',{...payload,nonce});accepted=true;state.pending=null;store.set('pending','');render(task);startPoll();await historyList()}catch(error){notice(error.message,true)}finally{busy(false);if(accepted)$('request-form').setAttribute('data-submitted','true')}};
$('login-form').onsubmit=async event=>{event.preventDefault();const button=$('login-form').querySelector('button');button.disabled=true;try{const session=await api('session',{code:$('access-code').value.trim()});$('access-code').value='';await connected(session);notice('')}catch(error){notice(error.message,true)}finally{button.disabled=false}};
$('invite').onclick=()=>$('invite-dialog').showModal();$('close-invite').onclick=()=>$('invite-dialog').close();
async function connected(session){
 state.connected=session.connected;state.owner=session.owner;state.accessMode=session.access_mode==='open'||session.requires_code===false?'open':'code';
 const open=state.accessMode==='open';$('login').hidden=session.connected||open;$('app').hidden=!session.connected;$('invite').hidden=!session.connected||!(open||session.owner);
 $('invite').textContent=open?'分享页面':'邀请他人使用';$('invite-title').textContent=open?'分享动画工作台':'邀请他人制作动画';
 $('invite-url').value=location.origin+'/auto.html?new=1';$('invite-code-section').hidden=open;$('invite-code').value=!open&&session.access_code?session.access_code:'';
 $('invite-message').textContent=open?'把页面地址提供给同一局域网的同事，打开即可使用。':'把页面地址和访问码提供给同一局域网的同事。';
 $('invite-note').textContent=open?'同事可以直接上传图片、填写需求。':'同事首次输入一次，之后直接上传图片、填写需求即可。';
 if(!session.connected)return;const query=new URLSearchParams(location.search),source=query.get('character');if(source&&state.owner){try{const value=await api('source?character='+encodeURIComponent(source));if(value.image_url){state.character=value.id;state.sourceUrl=value.image_url;$('source-preview').src=value.image_url;$('source-preview').hidden=false;$('upload-label').textContent='点击更换图片';$('source-label').hidden=false;$('source-label').textContent=value.name}}catch(error){notice(error.message,true)}}const tasks=await historyList();const last=store.get('last-task');const requested=query.get('task');const previous=requested?tasks?.find(task=>task.id===requested||task.attempt_ids?.includes(requested)):query.get('new')==='1'?null:tasks?.find(task=>task.id===last||task.attempt_ids?.includes(last));if(previous){render(previous);startPoll()}
}
async function bootstrap(){try{await connected(await api('session'))}catch(error){notice(error.message,true);$('login').hidden=state.accessMode==='open'}}
document.addEventListener('visibilitychange',async()=>{if(document.hidden||!state.connected)return;try{await historyList();if(state.active)render(await api('tasks/'+state.active.id));startPoll()}catch(error){notice(error.message,true)}});
window.addEventListener('beforeunload',()=>clearInterval(state.poll));bootstrap();

$('resume').onclick=async()=>{if(!state.active||state.busy)return;const button=$('resume');button.disabled=true;busy(true);try{const task=await api('tasks/'+state.active.id+'/resume',{nonce:uuid()});render(task);startPoll();await historyList();notice('已从保留的素材继续，生成额度不会重置。')}catch(error){notice(error.message,true)}finally{button.disabled=false;busy(false)}};

$('refine-form').onsubmit=async event=>{
 event.preventDefault();if(state.refining||!state.active||!terminal.has(state.active.state))return;
 const payload={task_id:state.active.id,artifact_index:Number($('artifact').value)||0,requirements:$('refine-request').value.trim()};
 if(payload.requirements.length<5){$('refine-status').textContent='请具体描述希望修改的地方（至少 5 个字）。';return}
 const signature=fingerprint(payload);let saved=null;try{saved=JSON.parse(store.get('refine-pending'))}catch{}
 const nonce=saved?.signature===signature?saved.nonce:uuid();store.set('refine-pending',JSON.stringify({signature,nonce}));
 state.refining=true;$('refine-submit').disabled=true;$('refine-status').textContent='正在提交修改…';
 try{const task=await api('refine',{...payload,nonce});store.set('refine-pending','');render(task);startPoll();await historyList();$('refine-request').value='';notice('修改已提交，旧版本保留。这里会显示处理进度和新结果。')}
 catch(error){$('refine-status').textContent=error.message}
 finally{state.refining=false;$('refine-submit').disabled=!!state.active&&!terminal.has(state.active.state)}
};
