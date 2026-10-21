"""Exercise fixed-accessory ownership with retained source art, no generation."""
import sys,copy,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.agent_tasks import AgentTasks
from app.assembly_stress import prepare
from app.core import atomic_json,png_bytes,sha
out=ROOT/'evidence/github-occlusion-20261021/final';out.mkdir(parents=True,exist_ok=True)
a=AgentTasks(ROOT/'storage',ROOT,{'character':lambda id:CharacterProject(ROOT/'storage/characters'/id)},None)
clone=a.clone({'kind':'character','id':'5e32ae5e3a6a4fd9818f5f9e92d5a59c'});c=a.loaders['character'](clone['id']);before=copy.deepcopy(c.meta)
m=copy.deepcopy(c.meta);m['name']='橙衣快递员 · 固定肩带正确遮挡';atomic_json(c.directory/'character.json',m);c=a.loaders['character'](c.id)
p=next(p for p in c.base_parts() if p['name']=='torso');rule={'mode':'fixed-to-costume','source':'torso','near_part':'l-upper-arm','source_sha256':sha(png_bytes(p['image'])),'remove_foregrounds':['near-strap'],'reason':'对比原衣装与三种runtime试验：固定肩带已完整包含在torso；额外整条前景造成横跨近臂。近臂在主体前自然遮挡，不删除源图面、不裁断肩带、不切换绘制顺序。'}
c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[rule]})
assert before['part_layers']==c.meta['part_layers'] and before['landmarks']==c.meta['landmarks'] and before['settings']==c.meta['settings']
r,images=c.prebind_evidence()
for n,im in images.items():im.save(out/(n+'.png'))
stress=prepare(a,c)
for p in stress['panels']:(out/p['file']).write_bytes((c.directory/'stress-cache'/stress['digest']/p['file']).read_bytes())
result={'character_id':c.id,'prebind':r,'stress':stress,'source':'5e32ae5e3a6a4fd9818f5f9e92d5a59c','base_textures_and_pins_unchanged':True,'image_calls':0,'rule':rule}
(out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'character_id':c.id,'panels':len(stress['panels'])}))
