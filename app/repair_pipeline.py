"""Atomic, evidence-bound repair batches from retained same-part pixels."""
import copy,re
import numpy as np
from PIL import Image
from .core import UserError,read_image,png_bytes,sha
from .assembly_pipeline import atomic_assembly,reconcile_overlays

@atomic_assembly
def repair(character,data):
 character.check(data['revision']);meta=copy.deepcopy(character.meta);history=[];seen=set()
 items=data.get('restore_parts',[])
 if not isinstance(items,list) or len(items)>4:raise UserError('最多恢复4个已确认部件')
 for item in items:
  name=item.get('part');layers=meta.get('part_layers',{})
  if name not in layers or name in seen:raise UserError('恢复部件无效或重复')
  seen.add(name);r=layers[name];digest=item.get('source_sha256','')
  if item.get('expected_sha256')!=r['image_id']:raise UserError('待修素材已变化，请重新检查')
  known={v[k] for batch in meta.get('part_finish_history',[]) for v in batch if v['part']==name for k in ('before','after')}
  if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or digest not in known:raise UserError('仅可恢复本部件已有修整历史中的实际像素')
  if not isinstance(item.get('reason'),str) or not item['reason'].strip():raise UserError('须说明恢复范围与压力图依据')
  folder=character.directory/'part-images';before=read_image((folder/(r['image_id']+'.png')).read_bytes()).convert('RGBA');source=read_image((folder/(digest+'.png')).read_bytes()).convert('RGBA')
  if before.size!=source.size:raise UserError('历史素材尺寸不同，必须重新配准，不能直接覆盖')
  box=item.get('box')
  from .reviewed_parts import pixel_box
  box=pixel_box(box,before.size);result=before.copy();result.paste(source.crop(box),tuple(box[:2]))
  blob=png_bytes(result);after=sha(blob);(folder/(after+'.png')).write_bytes(blob)
  history.append({'part':name,'before':r['image_id'],'after':after,'source_sha256':digest,'box':box,'reason':item['reason']})
  r['image_id']=after;r.pop('garment_fit',None);meta.get('assembly_inputs',{}).pop(name,None)
 meta.setdefault('connection_repair_history',[]).append(history)
 for key in ('prebind_review','stress_review','overlap_review','reconstruction_review'):meta.pop(key,None)
 character.save(meta);reconcile_overlays(character)
 if data.get('edits'):character.finish_parts({'revision':character.meta['revision'],'edits':data['edits']})
 plan=copy.deepcopy(data.get('assembly_plan',{}))
 if not isinstance(plan,dict):raise UserError('装配修复方案须为对象')
 # A restored part's exact new source is deterministic; bind it here, not by an
 # extra model round trip. Other sources still use the explicitly supplied hash.
 for p in plan.get('connections',[]):
  if p.get('part') in seen:p['source_sha256']=character.meta['part_layers'][p['part']]['image_id']
 if plan:character.prepare_assembly({**plan,'revision':character.meta['revision']})
 return character.summary()
