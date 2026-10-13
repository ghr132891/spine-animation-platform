'use strict';
const $=id=>document.getElementById(id),query=new URLSearchParams(location.search);
const state={rig:null,paused:matchMedia('(prefers-reduced-motion: reduce)').matches,mirrored:false,frame:null,loadSequence:0,ready:false};
const validId=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);
function displayName(value){const internal=new Set(['单图行走','骨骼与贴图','自动化','Agent 源副本']);return String(value||'角色动画').split(' · ').filter(part=>part&&!internal.has(part)).map(part=>part.length>85?part.slice(0,82)+'…':part).join(' · ')||'角色动画'}
function notice(text){$('notice').textContent=text||'';$('notice').hidden=!text}
async function api(path){const response=await fetch(path);const value=await response.json();if(!response.ok)throw Error(value.error||'暂时无法打开动画，请刷新页面');return value}
function option(value,text){const item=document.createElement('option');item.value=value;item.textContent=text;return item}
function controls(){const animation=$('animation').value;return {type:'controls',animation:animation==='__setup__'?'':animation,setup:animation==='__setup__',skin:$('skin').value,speed:Number($('speed').value),paused:state.paused||animation==='__setup__',bones:false,mirrored:state.mirrored,showInfluence:false,selectedBone:''}}
function sendControls(){if(state.ready)$('viewer').contentWindow?.postMessage(controls(),location.origin);$('play').textContent=state.paused?'播放':'暂停';$('play').disabled=$('animation').value==='__setup__';$('direction').setAttribute('aria-pressed',String(state.mirrored));$('direction').textContent=state.mirrored?'恢复朝向':'左右翻转'}
function saveAnimation(){if(!state.rig)return;const url=new URL(location.href);url.searchParams.set('rig',state.rig.id);url.searchParams.set('animation',$('animation').value);if($('skin').value)url.searchParams.set('skin',$('skin').value);url.searchParams.delete('advanced');history.replaceState(null,'',url);$('edit').href='rig.html?rig='+state.rig.id+'&animation='+encodeURIComponent($('animation').value)+'&skin='+encodeURIComponent($('skin').value)+'&advanced=1'}
function showEmpty(title,help){$('refine-form').hidden=true;$('empty-title').textContent=title;$('empty-help').textContent=help;$('empty').hidden=false;$('viewer').hidden=true;$('controls').hidden=true;$('more').hidden=true;$('download').hidden=true;$('edit').hidden=true}
async function load(id){
 const sequence=++state.loadSequence;state.ready=false;
 if(!validId(id)){state.rig=null;document.body.setAttribute('aria-busy','false');notice('这个动画链接无效，请从下方已保存动画中选择。');showEmpty('没有找到这套动画','可以选择已保存动画，或上传图片制作新动画。');return}
 notice('');document.body.setAttribute('aria-busy','true');showEmpty('正在打开动画','稍等一下，动画马上出现。');
 try{
  const rig=await api('/api/rigs/'+id);if(sequence!==state.loadSequence)return;if(!validId(rig.id)||!['3.8','4.2'].includes(rig.runtime))throw Error('这个动画版本暂时无法预览，请打开“调整细节”检查');
  state.rig=rig;state.mirrored=false;$('name').textContent=displayName(rig.name);$('rig-select').value=id;
  const animations=Object.keys(rig.skeleton.animations||{}),desired=new URLSearchParams(location.search).get('animation');
  $('animation').replaceChildren(...animations.map(name=>option(name,({idle:'待机',walk:'行走',walk_slow:'慢速行走'})[name]||name)),option('__setup__','静态展示'));
  $('animation').value=animations.includes(desired)||desired==='__setup__'?desired:animations.includes('walk')?'walk':animations[0]||'__setup__';
  const skins=Array.isArray(rig.skeleton.skins)?rig.skeleton.skins.map(skin=>skin.name):Object.keys(rig.skeleton.skins||{});
  $('skin').replaceChildren(...skins.map(name=>option(name,name==='default'?'默认外观':name)));const desiredSkin=new URLSearchParams(location.search).get('skin');if(skins.includes(desiredSkin))$('skin').value=desiredSkin;$('skin-label').hidden=skins.length<2;
  $('download').href='/api/rigs/'+id+'/export';$('download').hidden=false;$('edit').hidden=false;$('saved').textContent='已保存';$('controls').hidden=false;$('more').hidden=false;
  $('refine-form').hidden=false;saveAnimation();try{localStorage.setItem('spine-last-result',id)}catch{}
  const frame='character-viewer.html?rig='+id+'&version='+rig.runtime+'&single=1&revision='+rig.revision;state.frame=frame;$('viewer').src=frame;$('viewer').hidden=false;$('empty').hidden=true;sendControls();
 }catch(error){if(sequence!==state.loadSequence)return;state.rig=null;notice(error.message);$('name').textContent='请选择其他动画';showEmpty('暂时无法打开这套动画','下方可以查看其他成果，或返回制作页。')}
 finally{if(sequence===state.loadSequence)document.body.setAttribute('aria-busy','false')}
}
$('play').onclick=()=>{state.paused=!state.paused;sendControls()};$('direction').onclick=()=>{state.mirrored=!state.mirrored;sendControls()};$('animation').onchange=()=>{saveAnimation();sendControls()};$('speed').onchange=sendControls;$('skin').onchange=()=>{saveAnimation();sendControls()};$('rig-select').onchange=()=>load($('rig-select').value);
window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==$('viewer').contentWindow)return;if(event.data?.type==='ready'){state.ready=true;sendControls()}if(event.data?.type==='error'){notice(event.data.message||'动画预览暂时无法加载，请刷新页面')}});
async function init(){
 let rigs=[];try{rigs=await api('/api/rigs');if(!Array.isArray(rigs))throw Error('动画记录暂时无法读取');rigs=rigs.filter(rig=>validId(rig.id));$('rig-select').replaceChildren(...rigs.map(rig=>option(rig.id,displayName(rig.name)+(Number.isFinite(rig.updated_at)?' · '+new Date(rig.updated_at*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):''))))}catch(error){notice(error.message)}
 $('library-empty').hidden=rigs.length>0;$('rig-select').hidden=!rigs.length;
 let previous;try{previous=localStorage.getItem('spine-last-result')}catch{}
 const explicit=query.get('rig'),id=explicit||(rigs.some(rig=>rig.id===previous)?previous:rigs[0]?.id);
 if(id)await load(id);else{$('name').textContent='上传角色图片，就可以开始制作';showEmpty('这里展示你的角色动画','完成制作后，可以切换动作、播放和下载。');$('library').open=true}
}
init();

let refining=false;
$('refine-form').onsubmit=async event=>{
 event.preventDefault();if(refining||!state.rig)return;
 const payload={rig_id:state.rig.id,revision:state.rig.revision,requirements:$('refine-request').value.trim()};
 if(payload.requirements.length<5){$('refine-status').textContent='请具体描述希望修改的地方（至少 5 个字）。';return}
 const signature=JSON.stringify(payload);let saved=null;try{saved=JSON.parse(localStorage.getItem('spine-result-refine'))}catch{}
 const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);const nonce=saved?.signature===signature?saved.nonce:[...bytes].map(v=>v.toString(16).padStart(2,'0')).join('');
 try{localStorage.setItem('spine-result-refine',JSON.stringify({signature,nonce}))}catch{}
 refining=true;$('refine-submit').disabled=true;$('refine-status').textContent='正在提交修改…';
 try{
  const session=await api('/api/workflows/session');if(!session.connected)throw Error('请先到制作页连接工作台，再回来修改。');
  const response=await fetch('/api/workflows/refine',{method:'POST',headers:{'Content-Type':'application/json','X-Spine-Local':'1'},body:JSON.stringify({...payload,nonce})});const task=await response.json();if(!response.ok)throw Error(task.error||'修改提交失败');
  try{localStorage.removeItem('spine-result-refine')}catch{}
  location.href='/auto.html?task='+encodeURIComponent(task.id);
 }catch(error){$('refine-status').textContent=error.message;refining=false;$('refine-submit').disabled=false}
};
