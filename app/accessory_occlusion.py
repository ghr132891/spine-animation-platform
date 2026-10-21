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
  order=character.review_order(list(parts)+[o['name'] for o in meta.get('assembly_overlays',[])])
  if order.index(source)>order.index(near):raise UserError('先按实际前后关系把主体放在近侧手臂后方')
  records.append({**copy.deepcopy(item),'effect':'source-texture-retained;near-arm-occludes-fixed-accessory','removed_overlays':names})
 meta['assembly_overlays']=overlays;meta.setdefault('accessory_occlusion_history',[]).append(records)
 for name in ('prebind_review','stress_review','overlap_review','reconstruction_review'):meta.pop(name,None)
 character.save(meta)
 return records
