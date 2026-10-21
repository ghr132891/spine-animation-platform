"""Verify reviewed connection plans on independent copies of existing materials.

No image/model calls, no automatic visual approval. Optional persistence keeps
before/after resources available for visual review without replacing originals.
"""
import argparse,copy,json,sys,tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.core import Project,sha,atomic_json
from app.rig import RigProject
from app.agent_tasks import AgentTasks

def main():
 p=argparse.ArgumentParser();p.add_argument('--character',required=True);p.add_argument('--rig',required=True);p.add_argument('--plan',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 storage=ROOT/'storage';loaders={'character':lambda id:CharacterProject(storage/'characters'/id),'rig':lambda id:RigProject(storage/'rigs'/id),'project':lambda id:Project(storage/'projects'/id)}
 agent=AgentTasks(storage,ROOT,loaders,None);source=loaders['character'](a.character);old=loaders['rig'](a.rig)
 original_character=(source.directory/'character.json').read_bytes();original_rig=(old.directory/'rig.json').read_bytes();plans=json.loads(a.plan.read_text());a.output.mkdir(parents=True,exist_ok=True)
 copied=agent.clone({'kind':'character','id':source.id});c=loaders['character'](copied['id'])
 c.prepare_assembly({'revision':c.meta['revision'],'connections':plans});report,images=c.prebind_evidence()
 for name,image in images.items():image.save(a.output/(name+'-after.png'))
 source.assembly().save(a.output/'assembly-before.png')
 repaired=agent.clone({'kind':'rig','id':old.id});rig=loaders['rig'](repaired['id']);state=copy.deepcopy(rig.state);changed=[]
 for plan in plans:
  name=plan['part'];part=next(p for p in state['parts'] if p['name']==name)
  if part['sha']!=plan['source_sha256']:raise RuntimeError('Rig and character pixels differ; require reviewed rebind')
  r=c.meta['part_layers'][name];blob=(c.directory/'part-images'/(r['image_id']+'.png')).read_bytes();part['sha']=rig.put_blob(blob);changed.append(name)
 assert state['skeleton']==old.state['skeleton']
 rig.commit(state,'连接局部比例与共享母版裤料修复；待视觉验收')
 rig.meta['assembly_source']={'character_id':c.id,'character_revision':c.meta['revision']};rig.meta['connection_review_status']='awaiting_visual_review';atomic_json(rig.directory/'rig.json',rig.meta)
 zipblob=rig.export();(a.output/'connection-repair-draft.zip').write_bytes(zipblob)
 checks={}
 for animation,count in [('idle',8),('walk',32)]:
  for region in (None,'__hip__'):
   name=animation+('-hip' if region else '-full');result,png=agent.runtime_check(zipblob,animation,count,closeup=region)
   (a.output/(name+'.png')).write_bytes(png);checks[name]={'passed':result['passed'],'frames':count}
 before_report,before_png=agent.runtime_check(old.export(),'walk',32,closeup='__hip__');(a.output/'walk-hip-before.png').write_bytes(before_png)
 result={'character_id':c.id,'rig_id':rig.id,'source_character_id':source.id,'source_rig_id':old.id,'changed_parts':changed,'skeleton_and_animations_preserved':state['skeleton']==old.state['skeleton'],
 'source_character_preserved':(source.directory/'character.json').read_bytes()==original_character,'source_rig_preserved':(old.directory/'rig.json').read_bytes()==original_rig,
 'image_calls':0,'runtime':checks,'visual_verdict':'awaiting_review','connection_history':c.meta['connection_fit_history'][-1]}
 (a.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='connection_history'},ensure_ascii=False))
if __name__=='__main__':main()
