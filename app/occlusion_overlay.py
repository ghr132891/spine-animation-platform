"""Reviewed foreground extraction from existing artwork, preserving mesh weights.

No anatomy inference, new pixels or automatic art approval. The caller supplies
an observed foreground boundary in the source texture's normalized coordinates.
"""
import copy
import re
import numpy as np
from .core import UserError, png_bytes, read_image
from .material_finish import coverage


def apply_overlay(rig, state, data):
    from .rig import skins, path_for, attachment_kind
    reason=data.get('reason');name=data.get('name','');source=data.get('source_slot');subject=data.get('subject_slot')
    if not isinstance(reason,str) or not reason.strip():raise UserError('遮挡拆层须记录实际观察原因')
    if not isinstance(name,str) or not re.fullmatch('[a-zA-Z][a-zA-Z0-9_-]{0,79}',name):raise UserError('遮挡层名称无效')
    s=state['skeleton'];slots={x['name']:x for x in s.get('slots',[])}
    if source not in slots or subject not in slots or source==subject:raise UserError('须选择不同的实际后层和被遮挡主体')
    if name in slots or any(name in (p['name'],p['id']) for p in state['parts']):raise UserError('遮挡层名称已存在')
    for clip in s.get('animations',{}).values():
        if clip.get('drawOrder') or clip.get('draworder'):raise UserError('动作含动态绘制顺序，须先显式适配，不能只改静态层级')
        if clip.get('slots',{}).get(source):raise UserError('源插槽含独立动画，须先显式适配遮挡层')
        for section in ('deform','attachments'):
            if any(source in v for v in clip.get(section,{}).values()):raise UserError('源附件含变形动画，须先显式适配遮挡层')
    part=next((p for p in state['parts'] if p['id']==data.get('part')),None)
    if part is None:raise UserError('源贴图不存在')
    prepared=[]
    for skin,items,_ in skins(s):
        choices=items.get(source,{})
        if len(choices)!=1:raise UserError('遮挡拆层要求每个皮肤拥有唯一源附件')
        att_name,att=next(iter(choices.items()))
        if slots[source].get('attachment')!=att_name or attachment_kind(att) not in ('region','mesh') or att.get('sequence') or path_for(att_name,att)!=part['name']:
            raise UserError('遮挡层须使用当前实际源贴图与独立 region/mesh 附件')
        clone=copy.deepcopy(att);clone['path']=name;prepared.append((items,clone))
    if not prepared:raise UserError('没有可复制的皮肤附件')
    image=read_image(rig.blob(part)).convert('RGBA');mask=coverage(image.size,data.get('polygon'))
    pixels=np.array(image);pixels[:,:,3]=np.rint(pixels[:,:,3]*mask).astype('uint8')
    from PIL import Image
    overlay=Image.fromarray(pixels)
    if not overlay.getbbox():raise UserError('所选遮挡区域没有实际像素')
    digest=rig.put_blob(png_bytes(overlay))
    state['parts'].append({'id':name,'name':name,'sha':digest,'width':image.width,'height':image.height})
    for items,att in prepared:items[name]={name:att}
    # Source below subject; foreground directly above subject; other slots retain
    # their mutual ordering. Copy source color/blend/bone as well as geometry.
    order=[n for n in slots if n!=source];order.insert(order.index(subject),source)
    foreground=copy.deepcopy(slots[source]);foreground.update(name=name,attachment=name)
    slots[name]=foreground;order.insert(order.index(subject)+1,name);s['slots']=[slots[n] for n in order]
    s.setdefault('skeleton',{}).setdefault('occlusion_overlays',[]).append({
        'name':name,'source_slot':source,'subject_slot':subject,'source_sha256':part['sha'],
        'polygon':data['polygon'],'reason':reason.strip(),'visual_verdict':'not_reviewed'})
    return '拆分前景遮挡：'+name+'（须动态复检）'
