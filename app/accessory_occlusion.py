"""Explicit ownership for fixed accessories already painted on the costume.

Remove only reviewed duplicate foregrounds. Retain real costume pixels, base
mesh weights, pins and animation; never treat an independent prop as clothing.
"""
import copy
from .core import UserError,png_bytes,sha

def apply(character,items):
 if not isinstance(items,list) or not 1<=len(items)<=4:raise UserError('配件遮挡须为1–4个明确方案')
 parts={p['name']:p for p in character.base_parts()};meta=copy.deepcopy(character.meta);overlays=copy.deepcopy(meta.get('assembly_overlays',[]));records=[];seen=set()
 for item in items:
  if not isinstance(item,dict) or item.get('mode')!='fixed-to-costume':raise UserError('当前支持固定穿戴配件归主衣装')
  source=item.get('source');near=item.get('near_part');names=item.get('remove_foregrounds')
  if not isinstance(source,str) or source not in parts or near not in ('l-upper-arm','r-upper-arm') or near not in parts:raise UserError('指定实际衣装来源与近侧完整手臂')
  if source!='torso' or parts[source].get('group')!='costume':raise UserError('固定穿戴方案只适用于实际完整主衣装')
  if item.get('source_sha256')!=sha(png_bytes(parts[source]['image'])):raise UserError('配件衣装图面已变化，须重新检查')
  if not isinstance(item.get('reason'),str) or not item['reason'].strip():raise UserError('须说明配件固定穿戴及真实图面依据')
  if not isinstance(names,list) or not names or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names):raise UserError('指定需移除的重复前景层')
  for name in names:
   if name in seen:raise UserError('重复前景层不能重复处理')
   selected=next((o for o in overlays if o['name']==name),None)
   if not selected or selected.get('source')!=source or selected.get('subject')!=near:raise UserError('不能移除不属于当前衣装和近臂关系的遮挡层')
   seen.add(name);overlays.remove(selected)
   rule={'basis':source_basis(character,parts,source,near),'overlay_signature':overlay_signature(selected),'source':source,'near_part':near,'name':name,'reason':item['reason']}
   rules=meta.setdefault('accessory_occlusion_rules',[])
   if not any(r['basis']==rule['basis'] and r['overlay_signature']==rule['overlay_signature'] for r in rules):rules.append(rule)
  order=character.review_order(list(parts)+[o['name'] for o in meta.get('assembly_overlays',[])])
  if order.index(source)>order.index(near):raise UserError('先按实际前后关系把主体放在近侧手臂后方')
  records.append({**copy.deepcopy(item),'effect':'source-texture-retained;near-arm-occludes-fixed-accessory','removed_overlays':names})
 meta['assembly_overlays']=overlays;meta.setdefault('accessory_occlusion_history',[]).append(records)
 for name in ('prebind_review','stress_review','overlap_review','reconstruction_review'):meta.pop(name,None)
 character.save(meta)
 return records


def overlay_signature(item):
 import json
 return sha(json.dumps({k:item.get(k) for k in ('name','source','subject','source_sha256','polygon')},sort_keys=True).encode())


def source_basis(character,parts,source,near):
 import json
 return sha(json.dumps({'image_id':character.meta['image_id'],'landmarks':character.meta['landmarks'],
  'parts':[(n,sha(png_bytes(parts[n]['image'])),parts[n]['box']) for n in (source,near)]},sort_keys=True).encode())


def inspect(character):
 """Detect risky duplicate structure, never infer accessory semantics from name."""
 parts={p['name']:p for p in character.base_parts()};rules=character.meta.get('accessory_occlusion_rules',[]);candidates=[]
 for item in character.meta.get('assembly_overlays',[]):
  source=item['source'];near=item['subject']
  if source!='torso' or near not in ('l-upper-arm','r-upper-arm') or source not in parts or near not in parts or parts[source].get('group')!='costume':continue
  signature=overlay_signature(item);basis=source_basis(character,parts,source,near)
  approved=any(r.get('basis')==basis and r.get('overlay_signature')==signature for r in rules)
  candidates.append({'name':item['name'],'source':source,'near_part':near,'source_sha256':sha(png_bytes(parts[source]['image'])),
   'overlay_signature':signature,'basis':basis,'status':'approved_repair' if approved else 'needs_visual_review',
   'reason':'主衣装图面重复置于近臂前方；先核实固定配件归属，不按名称自动删除'})
 return {'version':'accessory-ownership-default-v1','candidates':candidates,'approved_repairs':[c['name'] for c in candidates if c['status']=='approved_repair'],
  'status':'repair_pending' if any(c['status']=='approved_repair' for c in candidates) else 'needs_visual_review' if candidates else 'no_duplicate_foreground'}


def execute(character):
 """Shared default hook for imports, assembly and reused task preparation."""
 report=inspect(character);approved=set(report['approved_repairs'])
 if approved:
  meta=copy.deepcopy(character.meta);meta['assembly_overlays']=[x for x in meta.get('assembly_overlays',[]) if x['name'] not in approved]
  meta.setdefault('accessory_auto_repairs',[]).append({'removed':sorted(approved),'evidence':[x for x in report['candidates'] if x['name'] in approved]})
  for name in ('prebind_review','stress_review','overlap_review','reconstruction_review'):meta.pop(name,None)
  character.save(meta)
  report=inspect(character);report['automatically_repaired']=sorted(approved)
 if character.meta.get('accessory_preparation')!=report:
  meta=copy.deepcopy(character.meta);meta['accessory_preparation']=report;character.save(meta)
 return report
