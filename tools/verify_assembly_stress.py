"""Real orange-art runtime stress test; no formal rig, image call or approval."""
import sys,json,shutil,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.agent_tasks import AgentTasks
from app.assembly_stress import prepare
from app.core import atomic_json
storage=ROOT/'storage';source='88ef5aefbcb34e0c988fcd3780317f80';out=ROOT/'evidence/assembly-stress-20261021';out.mkdir(parents=True,exist_ok=True)
a=AgentTasks(storage,ROOT,{'character':lambda id:CharacterProject(storage/'characters'/id)},None)
original=(storage/'characters'/source/'character.json').read_bytes()
if (out/'copy.json').exists():record=json.loads((out/'copy.json').read_text())
else:
 record=a.clone({'kind':'character','id':source});(out/'copy.json').write_text(json.dumps(record))
c=a.loaders['character'](record['id']);meta=copy.deepcopy(c.meta);meta['stress_contract']='runtime-assembly-stress-v1';meta['name']='橙衣快递员 · 骨骼压力测试副本';atomic_json(c.directory/'character.json',meta);c=a.loaders['character'](c.id)
versions=copy.deepcopy(c.meta['versions']);report=prepare(a,c)
for p in report['panels']:shutil.copy2(c.directory/'stress-cache'/report['digest']/p['file'],out/(p['animation']+'-'+str(p['anchor'] or 'full')+'.png'))
report['character_id']=c.id;report['source_unchanged']=(storage/'characters'/source/'character.json').read_bytes()==original;report['no_production_version_created']=versions==c.meta['versions'];report['image_calls']=0
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='panels'},ensure_ascii=False))
