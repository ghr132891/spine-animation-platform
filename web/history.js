let historyTab='projects',historyRender=0,historyTimer=null;
const historyDialog=$('#history-dialog');
function historyTime(value){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value*1000))}
function historyButton(label,callback){const b=document.createElement('button');b.className='secondary';b.textContent=label;b.onclick=()=>callback().catch(e=>message(e.message,'error'));return b}
function historyRow(title,subtitle,image){const row=document.createElement('article');row.className='history-row';if(image){const img=document.createElement('img');img.src=image;img.alt='历史贴图';img.loading='lazy';row.append(img)}const copy=document.createElement('div');copy.className='history-copy';const h=document.createElement('strong');h.textContent=title;const p=document.createElement('p');p.className='small';p.textContent=subtitle;copy.append(h,p);const actions=document.createElement('div');actions.className='history-actions';row.append(copy,actions);return {row,copy,actions}}
async function renderHistory(){
 clearTimeout(historyTimer);const ticket=++historyRender,tab=historyTab,owner=project?.id;
 document.querySelectorAll('[data-history]').forEach(b=>b.classList.toggle('active',b.dataset.history===tab));
 $('#history-project-name').textContent=project?'当前项目：'+project.name:'尚未打开项目';
 $('#history-hint').textContent=tab==='projects'?'所有项目均保存在本机。':tab==='versions'?'每次修改自动保存；恢复历史会另外保存一个新版本。':'所有生成任务都保留。查询和查看历史不会重新生成或再次提交任务。';
 const list=$('#history-list');list.textContent='读取历史…';
 if(tab!=='projects'&&!owner){list.textContent='请先打开一个项目';return}
 const base='/api/projects/'+owner;
 const items=await api(tab==='projects'?'/api/projects':base+(tab==='versions'?'/history':'/jobs'));
 if(ticket!==historyRender||!historyDialog.open||project?.id!==owner)return;
 list.replaceChildren();
 for(const item of items){
  if(tab==='projects'){
   const {row,actions}=historyRow(item.name,historyTime(item.updated_at)+' · '+item.revision+' 次编辑 · '+item.job_count+' 次 AI 生成');row.dataset.project=item.id;row.dataset.current=item.id===owner;
   actions.append(historyButton(item.id===owner?'当前项目':'打开项目',async()=>{const p=await api('/api/projects/'+item.id);selected.clear();focus=null;showProject(p);historyDialog.close()}));list.append(row);
  }else if(tab==='versions'){
   const {row,actions}=historyRow(item.label,historyTime(item.created_at)+' · 第 '+item.revision+' 次编辑',base+'/history-thumbnail?id='+item.id);row.dataset.snapshot=item.id;
   actions.append(historyButton('恢复此版本',async()=>{historyDialog.close();await mutate('restore-version',{snapshot_id:item.id},'恢复历史版本…')}));list.append(row);
  }else{
   const name=project.regions.find(r=>r.id===item.region)?.name||item.region,labels={completed:'已完成',failed:'失败',pending:'排队中',processing:'生成中',submitted:'已提交',submission_unknown:'提交结果待确认'};
   const {row,copy,actions}=historyRow(name,historyTime(item.created_at)+' · '+(labels[item.status]||item.status),item.candidate_ready?base+'/candidate?id='+item.id:null);row.dataset.job=item.id;
   if(item.prompt){const p=document.createElement('p');p.className='history-prompt';p.textContent=item.prompt;copy.append(p)}
   if(item.poll_error||item.error){const p=document.createElement('p');p.className='small';p.textContent=item.poll_error||item.error;copy.append(p)}
   if(item.candidate_ready){actions.append(historyButton('查看候选图',async()=>{focus=item.region;selected=new Set([focus]);selectedJobs.set(owner+':'+focus,item.id);cacheJob(item);renderParts();renderSelection();document.querySelector('[data-edit="ai"]').click();historyDialog.close()}));const a=document.createElement('a');a.href=base+'/candidate?id='+item.id+'&download=1';a.textContent='下载 PNG';actions.append(a)}
   list.append(row);
  }
 }
 if(!items.length){const p=document.createElement('p');p.className='history-empty';p.textContent=tab==='jobs'?'还没有 AI 生成记录。':'还没有历史记录。';list.append(p)}
 if(tab==='jobs'&&items.some(j=>!['completed','failed','submission_unknown'].includes(j.status)))historyTimer=setTimeout(()=>renderHistory().catch(e=>message(e.message,'error')),5000);
}
$('#history').onclick=()=>{historyTab=project?'versions':'projects';historyDialog.showModal();renderHistory().catch(e=>message(e.message,'error'))};
$('#close-history').onclick=()=>historyDialog.close();historyDialog.addEventListener('close',()=>{historyRender++;clearTimeout(historyTimer)});
document.querySelectorAll('[data-history]').forEach(b=>b.onclick=()=>{historyTab=b.dataset.history;renderHistory().catch(e=>message(e.message,'error'))});
$('#refresh-history').onclick=async()=>{try{if(project&&historyTab==='jobs')await api(projectAPI('refresh-jobs'),{});await renderHistory()}catch(e){message(e.message,'error')}};
$('#save-version').onclick=()=>{$('#version-name').value='';$('#save-dialog').showModal();$('#version-name').focus()};$('#close-save').onclick=()=>$('#save-dialog').close();
$('#save-version-form').onsubmit=async e=>{e.preventDefault();const label=$('#version-name').value.trim();$('#save-dialog').close();await busy('保存版本到本机…',async()=>{const p=await api(projectAPI('save-version'),{label,revision:project.revision});showProject(p,false);message('版本已保存，可从“历史记录 → 编辑版本”找回。','good')})};
