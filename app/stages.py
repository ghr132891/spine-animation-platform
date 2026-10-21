"""Durable per-stage timing/checkpoints. Cache only explicitly validated outputs."""
import copy,time
from .core import UserError

def run_stage(agent,task_id,name,input_digest,operation,valid=None):
 started=time.time()
 with agent.lock:
  task=agent.read(task_id);records=copy.deepcopy(task.get('stage_checkpoints',{}));old=records.get(name,{})
  if old.get('status')=='completed' and old.get('input_digest')==input_digest and valid and valid(old.get('result')):
   old['cache_hits']=old.get('cache_hits',0)+1;records[name]=old;agent.update(task_id,stage_checkpoints=records);return copy.deepcopy(old['result'])
  entry={'name':name,'input_digest':input_digest,'status':'running','started_at':started,'attempts':old.get('attempts',0)+1,'total_seconds':old.get('total_seconds',0),'cache_hits':old.get('cache_hits',0)}
  records[name]=entry;agent.update(task_id,stage_checkpoints=records)
 try:result=operation()
 except Exception as e:
  with agent.lock:
   t=agent.read(task_id);records=copy.deepcopy(t.get('stage_checkpoints',{}));entry.update(status='failed',duration_seconds=round(time.time()-started,3),error=str(e)[:600] if isinstance(e,UserError) else type(e).__name__);entry['total_seconds']+=entry['duration_seconds'];records[name]=entry;agent.update(task_id,stage_checkpoints=records)
  raise
 with agent.lock:
  t=agent.read(task_id);records=copy.deepcopy(t.get('stage_checkpoints',{}));entry.update(status='completed',duration_seconds=round(time.time()-started,3),finished_at=time.time(),result=copy.deepcopy(result) if valid else None);entry['total_seconds']+=entry['duration_seconds'];records[name]=entry;agent.update(task_id,stage_checkpoints=records)
 return result

def public(task):
 return [{k:v for k,v in r.items() if k not in ('result','input_digest')} for r in task.get('stage_checkpoints',{}).values()]

def retry_stage(agent,tid,name):
 from .animation_transition import save,advance
 with agent.task_locks.for_task(tid):
  t=agent.read(tid)
  if t['state']=='cancelled':raise UserError('已取消任务不自动重试')
  if name not in ('stress-test','dynamic-review','bind','material-preparation'):raise UserError('此阶段需视觉判断或涉及生图，不能作为固定阶段重跑')
  characters=[s['id'] for s in t['sources'] if s['kind']=='character']
  if len(characters)!=1:raise UserError('阶段续作须对应一个角色副本')
  cid=characters[0];prior=t.get('animation_transitions',{}).get(cid,{})
  if name=='material-preparation':
   from .generation_flow import prepare
   return run_stage(agent,tid,name,str(agent.loaders['character'](cid).meta['revision']),lambda:prepare(agent,tid,cid))
  if name=='stress-test':
   if not agent.loaders['character'](cid).asset_status()['ready']:raise UserError('先补齐完整部件')
   return save(agent,tid,cid,phase='stress_pending',source_digest=agent.loaders['character'](cid).prebind_evidence()[0]['digest'],stress_attempts=0,retry_at=0,error=None)
  if name=='dynamic-review':
   if not prior.get('rig_id') or not prior.get('resource_sha256'):raise UserError('尚无已交付草稿可复检')
   return save(agent,tid,cid,phase='review_pending',review_attempts=0,retry_at=0,error=None)
  if not prior.get('version') or prior.get('error_step')!='binding_or_delivery':raise UserError('只能继续已启动且失败的绑定／交付阶段')
  return advance(agent,tid,cid,recover=True)
