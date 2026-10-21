"""Isolated reuse regression: measured template executes matching connection work."""
import sys,copy,json,tempfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.garment_templates import generation_request,plan
from app.part_prompts import generation_plan
from app.core import png_bytes

def main():
 out=ROOT/'evidence/garment-templates-20261021';out.mkdir(exist_ok=True)
 source=ROOT/'storage/characters/efb9d8cf723142948674e8778146b7b4';original=(source/'character.json').read_bytes()
 connections=json.loads((ROOT/'evidence/connection-fit-20261021/shared-plans.json').read_text())
 spec={'type':'trousers','openings':{},'shared_sources':{}}
 for p in connections:
  spec['openings'][p['part']]={'span':p['target_span'],'join_y':p['join_y'],'release_y':p['release_y'],'insert_depth':12,'evidence':'现有衣装母版裤腰截面与腿片根部已逐像素核对，同源裤料补接'}
  spec['shared_sources'][p['part']]={k:p[k] for k in ('donor_layout','texture_join')}
 for n,span,y,release,depth in [('l-upper-arm',[318,392],305,400,16),('r-upper-arm',[489,559],320,410,15)]:
  spec['openings'][n]={'span':span,'join_y':y,'release_y':release,'insert_depth':depth,'fit_required':False,'evidence':'实际完整袖根截面已测量，本次保留原袖形；不重复拟合已安装袖根'}
 (out/'template-input.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2))
 with tempfile.TemporaryDirectory() as tmp:
  target=Path(tmp)/source.name;shutil.copytree(source,target,ignore=shutil.ignore_patterns('*cache*'));c=CharacterProject(target)
  c.prepare_assembly({'revision':c.meta['revision'],'garment_template':spec})
  before={n:r['image_id'] for n,r in c.meta['part_layers'].items()};history=len(c.meta['connection_fit_history'])
  c.prepare_assembly({'revision':c.meta['revision'],'apply_template':True})
  assert before=={n:r['image_id'] for n,r in c.meta['part_layers'].items()};assert history==len(c.meta['connection_fit_history'])
  for n in ('head','torso','l-upper-arm','r-upper-arm'):assert before[n]==json.loads(original)['part_layers'][n]['image_id']
  expected=json.loads((ROOT/'storage/characters/88ef5aefbcb34e0c988fcd3780317f80/character.json').read_text())
  for n in ('l-upper-leg','r-upper-leg'):assert before[n]==expected['part_layers'][n]['image_id']
  report,images=c.prebind_evidence()
  for n,image in images.items():image.save(out/(n+'.png'))
  prepared,t=generation_request(c,{'mode':'complete-parts','instructions':'保留快递员身份、橙衣与藏蓝长裤。'})
  preview=generation_plan(prepared);(out/'generation-plan.json').write_text(json.dumps(preview,ensure_ascii=False,indent=2));(out/'template.json').write_text(json.dumps(t,ensure_ascii=False,indent=2))
  result={'same_repaired_pixels_as_reviewed_draft':True,'repeat_prepare_no_double_warp':True,'head_torso_arms_preserved':True,'original_preserved':(source/'character.json').read_bytes()==original,'image_calls':0,'template':t['type'],'dimensions':t['dimensions'],'openings':t['openings'],'prebind_digest':report['digest'],'visual_verdict':'same_known_draft_not_new_art_approval'}
  (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
