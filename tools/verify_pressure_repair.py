"""Reuse retained orange-courier pixels and recheck via actual stress runtime."""
import sys,copy,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.agent_tasks import AgentTasks
from app.repair_pipeline import repair
from app.assembly_stress import prepare
from app.core import atomic_json
storage=ROOT/'storage';out=ROOT/'evidence/pressure-repair-1.11';out.mkdir(parents=True,exist_ok=True)
a=AgentTasks(storage,ROOT,{'character':lambda id:CharacterProject(storage/'characters'/id)},None)
record=a.clone({'kind':'character','id':'88ef5aefbcb34e0c988fcd3780317f80'});c=a.loaders['character'](record['id']);m=copy.deepcopy(c.meta);m['stress_contract']='runtime-assembly-stress-v1';m['name']='橙衣快递员 · 原裁片恢复与肩带分层修复';atomic_json(c.directory/'character.json',m);c=a.loaders['character'](c.id)
restore=[]
for name,source in [('l-upper-leg','c40c1c4c60ed65e8fc86cfdec8c00fd21a6410bf3bbbf0e5f2ce959592d6b245'),('r-upper-leg','359e4624fa3206519f42352e6523d34657506404b9cd82566e452cf1891ec6b6')]:
 r=c.meta['part_layers'][name]
 from PIL import Image
 im=Image.open(c.directory/'part-images'/(r['image_id']+'.png'))
 restore.append({'part':name,'source_sha256':source,'expected_sha256':r['image_id'],'box':[0,0,im.width,200],'reason':'压力图确认上腿外缘切口来自之前erase；恢复同片历史完整衣料，再匹配裤腰，保留下面鞋腿'})
plans=json.loads((ROOT/'evidence/connection-fit-20261021/shared-plans.json').read_text())
overlays=copy.deepcopy(c.meta['assembly_overlays'])
strap=next(x for x in overlays if x['name']=='near-strap')
points=[[55,49],[62,41],[81,32],[101,34],[118,45],[130,68],[135,95],[134,118],[113,118],[113,100],[109,78],[101,64],[93,55],[80,49],[67,47]]
strap['polygon']=[[x/269,y/426] for x,y in points];strap['reason']='肩带前景只保留肩端覆盖，胸前垂带留在torso随躯干并被内摆近臂遮挡；去除橙衣条，避免垂带横跨袖子'
collar=next(x for x in overlays if x['name']=='front-collar');collar['polygon']=collar['polygon'][:9]+[[.99,.33],[.25,.33],[.25,.15]];collar['reason']='前领前景只包含颈口附近，停止复制整个躯干和裤腰'
plan={'revision':c.meta['revision'],'restore_parts':restore,'assembly_plan':{'connections':plans,'overlays':overlays}}
(out/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2));repair(c,plan)
_,images=c.prebind_evidence()
for name,image in images.items():image.save(out/(name+'.png'))
report=prepare(a,c)
for p in report['panels']:(out/(p['key']+'.png')).write_bytes((c.directory/'stress-cache'/report['digest']/p['file']).read_bytes())
report['character_id']=c.id;(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'character':c.id,'digest':report['digest'],'panels':len(report['panels'])}))
