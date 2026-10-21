"""Content-bound material -> animation transitions shared by web and MCP tasks.

No model/image calls. Preparation never grants visual approval. Persist each
handoff before doing the next step so retries reuse the same rig and bundle.
"""
import copy,time,uuid
from .core import UserError

LABELS={'stress_pending':'正式绑定前：正在准备骨骼压力测试','awaiting_stress_review':'压力测试已准备，等待腰胯与肩带动态验收','stress_needs_repair':'压力测试未通过，先修复连接再正式绑定','awaiting_materials':'等待完整素材验收','awaiting_view':'等待当前素材视角与连接确认',
 'awaiting_turnaround':'等待三视图与当前组装确认','binding':'平台正在自动绑定','draft_pending':'正在关联动画草稿',
 'review_pending':'草稿已交付，等待准备动态复检','review_preparing':'草稿已交付，正在准备动态复检',
 'review_ready':'动态复检图已准备，等待视觉验收','retryable_error':'平台续作失败，保留草稿与具体错误','superseded':'资源已变化，等待当前版本重新验收'}

def enabled(task,entity):
 return (task.get('output_scope','animation')=='animation' and task.get('automatic_animation_enabled',True) is not False
         and len(task.get('sources',[]))==1 and task['sources'][0]['kind']=='character' and task['sources'][0]['id']==entity)

def save(agent,tid,cid,**changes):
 with agent.lock:
  t=agent.read(tid);records=copy.deepcopy(t.get('animation_transitions',{}));old=records.get(cid,{})
  new={**old,**changes,'character_id':cid,'updated_at':time.time()};new['message']=LABELS[new['phase']]
  history=old.get('history',[])
  if old and any(old.get(k)!=new.get(k) for k in ('phase','source_digest','rig_id','error')):
   history=[*history,{k:old.get(k) for k in ('phase','source_digest','rig_id','error','updated_at')}][-16:]
  new['history']=history;records[cid]=new;agent.update(tid,animation_transitions=records)
  return new

def assembly_seen(task,c):
 if not c.asset_status()['ready']:return False
 digest=task.get('assembly_digests',{}).get(c.id)
 return digest==c.prebind_evidence()[0]['digest'] if digest else task.get('assembly_checks',{}).get(c.id)==c.meta['revision']

def advance(agent,tid,cid,recover=False):
 from .prebind import require
 from .image_preflight import require_side_camera,checked_connections
 from .asset_integrity import checked_material_review
 from .orientation_review import checked_orientation
 from .agent_tasks import resource_digest,ORIENTATION_CONTRACT,CONNECTION_CONTRACT,INTEGRITY_CONTRACT,GARMENT_CONTRACT
 with agent.task_locks.for_task(tid):
  task=agent.read(tid);agent.own(task,'character',cid)
  if not enabled(task,cid) or task['state']=='cancelled':return None
  prior=task.get('animation_transitions',{}).get(cid,{})
  resumable=recover and prior.get('phase') in ('binding','draft_pending','retryable_error') and prior.get('version')
  if task['state'] not in ('queued','running') and not resumable:return None
  c=agent.loaders['character'](cid)
  with c.lock:
   if not c.meta.get('image_id') or not c.asset_status()['ready']:
    return save(agent,tid,cid,phase='awaiting_materials',error=None)
   report,_=c.prebind_evidence();digest=report['digest']
   try:require(report,c.meta.get('prebind_review'))
   except UserError as e:return save(agent,tid,cid,phase='awaiting_materials',source_digest=digest,error=str(e))
   a=task.get('image_assessments',{}).get(cid,{})
   fresh=a.get('source_digest')==digest if a.get('source_digest') else a.get('revision')==c.meta['revision']
   if not fresh or a.get('image_id')!=c.meta['image_id'] or a.get('verdict')!='ready' or not a.get('parts_reviewed') or not assembly_seen(task,c):
    return save(agent,tid,cid,phase='awaiting_view',source_digest=digest,error=None)
   try:
    require_side_camera(a,task['requirements']);checked_material_review(a);checked_orientation(a)
    if not all(checked_connections(a.get('joint_review')).values()):raise UserError('连接仍需修复')
   except UserError as e:return save(agent,tid,cid,phase='awaiting_view',source_digest=digest,error=str(e))
   if task.get('turnaround_contract'):
    from .turnaround_review import require_review
    try:
     for phase in ('reference','assembly'):require_review(task,c,phase)
    except UserError as e:return save(agent,tid,cid,phase='awaiting_turnaround',source_digest=digest,error=str(e))
   if c.meta.get('stress_contract'):
    from .assembly_stress import require as require_stress,read_manifest
    try:require_stress(c)
    except UserError:
     try:
      stress=read_manifest(c);v=c.meta.get('stress_review',{})
      return save(agent,tid,cid,phase='stress_needs_repair' if v.get('digest')==stress['digest'] and v.get('verdict')=='needs_repair' else 'awaiting_stress_review',source_digest=digest,stress_digest=stress['digest'],stress_panels=stress['panels'],error=None)
     except UserError:return save(agent,tid,cid,phase='stress_pending',source_digest=digest,error=None,retry_at=0)
   previous=task.get('automatic_animation',{}).get(cid)
   if previous and previous['source_digest']==digest and prior.get('phase') in ('review_pending','review_preparing','review_ready'):
    return prior
   key=uuid.uuid5(uuid.NAMESPACE_URL,'material-animation:'+tid+':'+cid+':'+digest).hex
   current=agent.read(tid);attempts=current.get('automatic_build_attempts',[])
   if key not in attempts:
    if len(attempts)>=current['max_repairs']+1:return save(agent,tid,cid,phase='retryable_error',source_digest=digest,error='自动绑定已达到修复次数上限')
    attempts=[*attempts,key]
   agent.update(tid,automatic_build_attempts=attempts,material_animation=True,detail_contract='articulation-v1',orientation_contract=ORIENTATION_CONTRACT,connection_contract=CONNECTION_CONTRACT,integrity_contract=INTEGRITY_CONTRACT,garment_contract=GARMENT_CONTRACT)
   if previous and previous['source_digest']==digest and prior.get('error_step')=='dynamic_review':
    return save(agent,tid,cid,phase='review_pending',review_attempts=0,retry_at=0,error=None)
   save(agent,tid,cid,phase='binding',source_digest=digest,version=key,error=None,bundle_id=None)
   try:
    if task.get('turnaround_contract'):
     meta=copy.deepcopy(c.meta);meta['turnaround_reference']=copy.deepcopy(task['turnaround_checks'][cid]['assembly']);c.save(meta)
    from .stages import run_stage
    result=run_stage(agent,tid,'bind',digest,lambda:c.build(agent.storage,{'revision':c.meta['revision'],'nonce':key,'landmarks':c.meta['landmarks'],'settings':c.meta['settings'],'masks':c.meta.get('masks',{}),'label':'平台自动续作 · 待机与行走'}))
    version=next(v for v in result['versions'] if v['id']==key)
    with agent.lock:
     current=agent.read(tid);owned=copy.deepcopy(current['owned'])
     for kind,field in (('rig','rig_id'),('project','source_id')):
      if version[field] not in owned[kind]:owned[kind].append(version[field])
     agent.update(tid,owned=owned)
    save(agent,tid,cid,phase='draft_pending',rig_id=version['rig_id'])
    if agent.read(tid)['state']=='cancelled':return None
    agent.publish_draft(tid,version['rig_id'],'平台已根据已验收素材与视角确认自动生成待机和行走草稿，正在准备动态复检；尚未通过动态美术验收。')
    rig=agent.loaders['rig'](version['rig_id']);resource=resource_digest(rig.export())
    entry={'rig_id':rig.id,'character_id':cid,'source_digest':digest,'version':key,'visual_verdict':'not_reviewed'}
    with agent.lock:
     current=agent.read(tid);records=copy.deepcopy(current.get('automatic_animation',{}));records[cid]=entry;agent.update(tid,automatic_animation=records)
    return save(agent,tid,cid,phase='review_pending',resource_sha256=resource,review_attempts=0,retry_at=0,error_step=None,error=None)
   except Exception as e:
    return save(agent,tid,cid,phase='retryable_error',error=str(e)[:1000],error_step='binding_or_delivery',retry_count=prior.get('retry_count',0)+1,retry_at=time.time()+30)

def prepare_pending_review(agent,tid,cid):
 """Called by the platform coordinator, not by a model. Reusable after restart."""
 from .agent_tasks import resource_digest
 with agent.lock:
  t=agent.read(tid);r=copy.deepcopy(t.get('animation_transitions',{}).get(cid,{}))
  if t['state']=='cancelled' or r.get('phase') not in ('review_pending','review_preparing'):return None
  agent.own(t,'rig',r['rig_id']);rig=agent.loaders['rig'](r['rig_id'])
  if resource_digest(rig.export())!=r['resource_sha256']:return save(agent,tid,cid,phase='superseded',error='动画资源已修改，旧复检准备失效')
  save(agent,tid,cid,phase='review_preparing',review_attempts=r.get('review_attempts',0)+1)
 try:
  from .stages import run_stage
  bundle=run_stage(agent,tid,'dynamic-review',r['resource_sha256'],lambda:agent.review_bundle('rig',r['rig_id'],{}),valid=lambda value:bool(value and (agent.root/'storage/review-bundles'/value['bundle_id']/'manifest.json').is_file()))
  # Never acquire a character lock while holding agent.lock: binding takes
  # these in the reverse order. Evidence remains bound to explicit digests.
  current_source_digest=agent.loaders['character'](cid).prebind_evidence()[0]['digest']
  current_resource_digest=resource_digest(rig.export())
  with agent.lock:
   current=agent.read(tid);latest=current.get('animation_transitions',{}).get(cid,{})
   if current['state']=='cancelled' or latest.get('phase')!='review_preparing' or latest.get('source_digest')!=r['source_digest'] or latest.get('rig_id')!=r['rig_id']:return None
   if current_source_digest!=r['source_digest']:return save(agent,tid,cid,phase='superseded',error='复检期间素材已变化，保留上一版草稿')
   if bundle['resource_sha256']!=r['resource_sha256'] or current_resource_digest!=r['resource_sha256']:return save(agent,tid,cid,phase='superseded',error='复检期间资源已变化')
   return save(agent,tid,cid,phase='review_ready',bundle_id=bundle['bundle_id'],panel_count=len(bundle['panels']),error=None,visual_verdict='not_reviewed')
 except Exception as e:
  with agent.lock:
   current=agent.read(tid);latest=current.get('animation_transitions',{}).get(cid,{})
   if latest.get('phase')!='review_preparing' or latest.get('rig_id')!=r['rig_id'] or current['state']=='cancelled':return None
   attempts=latest.get('review_attempts',1)
   return save(agent,tid,cid,phase='review_pending' if attempts<3 else 'retryable_error',error=str(e)[:1000],error_step='dynamic_review',retry_at=time.time()+30)

def notify(agent,character):
    # HTTP callbacks must not acquire tool_lock: a worker may be waiting on this
    # HTTP request while holding that lock. Persist an event for the coordinator.
    from .core import atomic_json
    if character.directory.parent.parent.resolve()!=agent.storage.resolve():return
    atomic_json(agent.storage/'animation-events'/(character.id+'.json'),{'character_id':character.id,'revision':character.meta['revision'],'time':time.time()})

def process_events(agent):
    for path in (agent.storage/'animation-events').glob('*.json'):
        raw=path.read_bytes();cid=path.stem
        for taskpath in agent.tasks.glob('*/task.json'):
            task=agent.read(taskpath.parent.name)
            if task['state'] in ('queued','running') and enabled(task,cid):advance(agent,task['id'],cid)
        if path.exists() and path.read_bytes()==raw:path.unlink()

def run(agent):
 """Durable low-frequency queue, independent of the Codex process lifecycle."""
 while not agent.stop.is_set():
  try:process_events(agent)
  except (UserError,OSError,ValueError):pass
  for path in sorted(agent.tasks.glob('*/task.json')):
   if agent.stop.is_set():break
   try:
    task=agent.read(path.parent.name)
    for cid,state in task.get('animation_transitions',{}).items():
     if task['state']=='cancelled' or state.get('retry_at',0)>time.time():continue
     if state.get('phase') in ('binding','draft_pending') or (state.get('phase')=='retryable_error' and state.get('error_step')=='binding_or_delivery' and state.get('retry_count',0)<3):
      advance(agent,task['id'],cid,recover=True)
   except (UserError,OSError,ValueError):continue
  agent.stop.wait(2)


def run_reviews(agent):
 """Keep slow rendering off the event/binding coordinator. One bounded worker."""
 while not agent.stop.is_set():
  for path in sorted(agent.tasks.glob('*/task.json')):
   if agent.stop.is_set():break
   try:
    task=agent.read(path.parent.name)
    for cid,state in task.get('animation_transitions',{}).items():
     if task['state']=='cancelled' or state.get('retry_at',0)>time.time():continue
     if state.get('phase')=='stress_pending':prepare_stress(agent,task['id'],cid)
     elif state.get('phase') in ('review_pending','review_preparing'):prepare_pending_review(agent,task['id'],cid)
   except (UserError,OSError,ValueError):continue
  agent.stop.wait(2)


def prepare_stress(agent,tid,cid):
 from .assembly_stress import prepare
 t=agent.read(tid);initial=t.get('animation_transitions',{}).get(cid,{})
 if t['state']=='cancelled' or initial.get('phase')!='stress_pending':return
 try:
  from .stages import run_stage
  r=run_stage(agent,tid,'stress-test',initial.get('source_digest',''),lambda:prepare(agent,agent.loaders['character'](cid)))
  with agent.lock:
   current=agent.read(tid);latest=current.get('animation_transitions',{}).get(cid,{})
   if current['state']=='cancelled' or latest.get('phase')!='stress_pending' or latest.get('source_digest')!=initial.get('source_digest'):return
   save(agent,tid,cid,phase='awaiting_stress_review',stress_digest=r['digest'],stress_panels=r['panels'],error=None)
 except Exception as e:
  with agent.lock:
   latest=agent.read(tid)
   if latest['state']=='cancelled' or latest.get('animation_transitions',{}).get(cid,{}).get('phase')!='stress_pending':return
   attempts=initial.get('stress_attempts',0)+1
   save(agent,tid,cid,phase='stress_pending' if attempts<3 else 'retryable_error',stress_attempts=attempts,error_step='stress',error=str(e)[:1000],retry_at=time.time()+30)
