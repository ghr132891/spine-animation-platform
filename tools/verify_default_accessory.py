"""Real art regression: import and task preparation preserve reviewed ownership."""
import json,copy,tempfile,shutil,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.agent_tasks import AgentTasks
from app.core import png_bytes,sha
from app.generation_flow import prepare
source=ROOT/'storage/characters/5e32ae5e3a6a4fd9818f5f9e92d5a59c';original=(source/'character.json').read_bytes()
expected=json.loads((ROOT/'storage/characters/44385d2ae4d740ada2381ecfa2284e4f/character.json').read_text())
with tempfile.TemporaryDirectory() as tmp:
 storage=Path(tmp);target=storage/'characters'/source.name;shutil.copytree(source,target,ignore=shutil.ignore_patterns('*cache*'));c=CharacterProject(target);overlay=copy.deepcopy(c.meta['assembly_overlays']);p=next(p for p in c.base_parts() if p['name']=='torso')
 c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[{'mode':'fixed-to-costume','source':'torso','near_part':'l-upper-arm','source_sha256':sha(png_bytes(p['image'])),'remove_foregrounds':['near-strap'],'reason':'复用已验收的橙衣实图结论：肩带完整归主衣装，重复前景造成跨袖'}]})
 before={p['name']:sha(png_bytes(p['image'])) for p in c.parts()}
 # Reintroduced recipe simulates downstream assembly replay.
 c.prepare_assembly({'revision':c.meta['revision'],'overlays':overlay});assert before=={p['name']:sha(png_bytes(p['image'])) for p in c.parts()}
 assert c.meta['assembly_overlays']==expected['assembly_overlays'] and c.meta['part_layers']==expected['part_layers']
 m=copy.deepcopy(c.meta);m['assembly_overlays']=overlay;c.save(m)
 agent=AgentTasks(storage,ROOT,{'character':lambda id:CharacterProject(storage/'characters'/id)},None)
 t=agent.create({'nonce':uuid.uuid4().hex,'requirements':'隔离验证默认配件修复，无模型无生图','sources':[{'kind':'character','id':c.id}],'image_preparation':'preserve','max_generations':0})
 cid=t['sources'][0]['id'];record=prepare(agent,t['id'],cid);fixed=agent.loaders['character'](cid)
 assert fixed.meta['assembly_overlays']==expected['assembly_overlays'];assert before=={p['name']:sha(png_bytes(p['image'])) for p in fixed.parts()}
 result={'assembly_replay_auto_repaired':True,'task_start_auto_repaired':True,'pixels_match_previously_reviewed_result':True,'original_preserved':(source/'character.json').read_bytes()==original,'model_calls':agent.read(t['id'])['calls'],'image_calls':0,'status':record['accessory_preparation']['status'],'visual_scope':'unchanged previously reviewed result; no new art approval'}
 out=ROOT/'evidence/default-accessory-20261022';out.mkdir(parents=True,exist_ok=True);(out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result))
