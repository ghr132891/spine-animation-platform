"""Empty task -> late real accepted assets -> bind/draft/full dynamic evidence.

Isolated storage, no model or image calls, no new visual approval. The reused
source must exactly match the recorded, previously accepted material digest.
"""
import copy,json,shutil,sys,tempfile,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import Project,png_bytes,atomic_json
from app.rig import RigProject
from app.web_workflows import WebWorkflows
from app.animation_transition import prepare_pending_review
from app.asset_integrity import ASSEMBLY_CHECKS
from app.orientation_review import ASSEMBLY_CHECKS as ORIENTATION_CHECKS

def main():
 out=ROOT/'evidence/late-material-transition-20261021';out.mkdir(parents=True,exist_ok=True)
 source=ROOT/'storage/characters/5512f5ff7dd141bea6b34238bfb40161';original=(source/'character.json').read_bytes();accepted=json.loads(original)
 reference=json.loads((ROOT/'storage/agent-tasks/9a81467cd68648739549487d5837ae63/task.json').read_text());assert reference['state']=='completed'
 ref_source=next(s['id'] for s in reference['sources'] if s.get('original_id')==source.name)
 with tempfile.TemporaryDirectory() as tmp:
  storage=Path(tmp);loaders={'character':lambda id:CharacterProject(storage/'characters'/id),'rig':lambda id:RigProject(storage/'rigs'/id),'project':lambda id:Project(storage/'projects'/id)}
  agent=AgentTasks(storage,ROOT,loaders,None);web=WebWorkflows(agent);owner,_=web.new_session(True)
  task=agent.create({'nonce':uuid.uuid4().hex,'requirements':'回归验证：空任务后续收到已验收素材，自动完成待机行走续作','image_preparation':'auto','max_generations':0,'_web_session_id':owner['id']});tid=task['id'];cid=task['sources'][0]['id'];c=loaders['character'](cid)
  assert not c.meta['image_id'] and not task.get('material_animation');states=['empty_task_without_material_animation']
  for folder in ('images','part-images'):shutil.copytree(source/folder,c.directory/folder)
  meta=copy.deepcopy(accepted);meta.update(id=cid,revision=1,versions=[]);meta.pop('prebind_review',None);atomic_json(c.directory/'character.json',meta);c=loaders['character'](cid)
  report,images=c.prebind_evidence();assert report['digest']==accepted['prebind_review']['digest']
  def api(route,data=None,binary=False):
   c=loaders['character'](cid);action=route.rsplit('/',1)[-1]
   if binary and action=='image':return png_bytes(c.image())
   if binary and action=='assembly':return png_bytes(c.assembly())
   if binary and action.startswith('prebind-'):return png_bytes(c.prebind_evidence()[1][action.split('-',1)[1]])
   if action=='review-prebind':return c.review_prebind(data)
   raise AssertionError(route)
  agent.api=api
  for view in ('image','assembly','prebind-parts','prebind-assembly','prebind-spread'):agent.tool(tid,'spine_image',{'kind':'character','id':cid,'view':view})
  result=agent.tool(tid,'spine_character',{'id':cid,'action':'review-prebind','data':{'revision':c.meta['revision'],**accepted['prebind_review']}});states.append(result['animation_transition']['phase'])
  # These fixture expectations were previously checked on the unchanged source;
  # the assertions above prohibit applying them to newly generated artwork.
  args={**copy.deepcopy(reference['image_assessments'][ref_source]),'id':cid,'material_checks':dict.fromkeys(ASSEMBLY_CHECKS,True),'orientation_checks':dict.fromkeys(ORIENTATION_CHECKS,True)}
  result=agent.tool(tid,'spine_assess_image',args);built=result['automatic_animation'];states.append(result['animation_transition']['phase'])
  task_after=agent.read(tid);assert task_after['draft_artifacts'][0]['id']==built['rig_id'];assert len(loaders['character'](cid).meta['versions'])==1
  agent.tool(tid,'spine_assess_image',args);assert len(loaders['character'](cid).meta['versions'])==1
  # Recreate coordinator object to verify the queue is persisted, not an in-memory callback.
  restarted=AgentTasks(storage,ROOT,loaders,None);ready=prepare_pending_review(restarted,tid,cid);assert ready['phase']=='review_ready',ready;states.append(ready['phase'])
  bundle=json.loads((ROOT/'storage/review-bundles'/ready['bundle_id']/'manifest.json').read_text());assert bundle['visual_verdict']=='not_reviewed'
  for p in bundle['panels']:
   if p['anchor'] in (None,'__hip__'):
    shutil.copy2(ROOT/'storage/review-bundles'/bundle['bundle_id']/p['file'],out/(p['animation']+('-hip' if p['anchor'] else '-full')+'.png'))
  (out/'review-bundle.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2))
  public=web.public(restarted.read(tid));assert public['animation_transitions'][0]['phase']=='review_ready'
  summary={'mode':'isolated regression with unchanged previously accepted assets; not a fresh AI generation or new art approval','states':states,'initial_auto_bind_flag_absent':True,'late_source_digest_matches_accepted':True,'duplicate_assessment_one_rig':True,'draft_delivered':True,'queue_recovered_with_new_coordinator':True,'web_and_mcp_same_state':True,'dynamic_panels':len(bundle['panels']),'sampling':[(p['animation'],p['frames']) for p in bundle['panels'] if p['anchor'] is None],'visual_verdict':bundle['visual_verdict'],'image_calls':0,'model_calls':0,'source_unchanged':(source/'character.json').read_bytes()==original}
  (out/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
