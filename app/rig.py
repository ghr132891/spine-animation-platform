"""Independent editable Spine JSON projects. No upstream application code.

The state and history pointer are committed together. Image blobs are immutable.
Bone order is topologically rebuilt, with weighted vertex indices remapped.
"""
from __future__ import annotations
import base64
import copy
import io
import json
import math
import re
import threading
import time
import uuid
import zipfile
from collections import Counter
from pathlib import Path
import numpy as np
from PIL import Image, ImageEnhance
from app.core import UserError, atomic_json, png_bytes, read_image, safe_name, sha, parse_atlas, MAX_PIXELS


def skins(s):
    value=s.get('skins',{})
    if isinstance(value,dict):return [(name,attachments,None) for name,attachments in value.items()]
    return [(skin['name'],skin.setdefault('attachments',{}),skin) for skin in value]


def attachments(s):
    for skin,slots,_ in skins(s):
        for slot,items in slots.items():
            for name,a in items.items():yield skin,slot,name,a


def path_for(name,a):return a.get('path',a.get('name',name))


def attachment_kind(a):
    kind=a.get('type','region')
    return 'linkedmesh' if kind=='mesh' and a.get('parent') else kind


def number(value,minimum=-100000,maximum=100000):
    if isinstance(value,bool):raise UserError('数值参数无效')
    try:value=float(value)
    except (TypeError,ValueError):raise UserError('请输入有效数值')
    if not math.isfinite(value) or not minimum<=value<=maximum:raise UserError('数值超出允许范围')
    return value


def name_value(value):
    value=str(value).strip()
    if not value or len(value)>100 or any(c in value for c in '\n\r\x00'):raise UserError('名称须为 1–100 个字符')
    return value


def weighted_indices(a):
    """Yield index offsets; unweighted and linked meshes have none."""
    vertices=a.get('vertices',[])
    count=len(a.get('uvs',[])) if a.get('type')=='mesh' else int(a.get('vertexCount',0))*2
    if not vertices or len(vertices)==count:return
    i=0;points=0
    while i<len(vertices):
        n=vertices[i]
        if not isinstance(n,int) or n<1 or i+1+n*4>len(vertices):raise UserError('加权顶点数据不合法')
        i+=1
        for _ in range(n):yield i;i+=4
        points+=1
    if points*2!=count:raise UserError('加权顶点数量与 UV / vertexCount 不一致')


def reorder_bones(s):
    old=s['bones'];by={b['name']:b for b in old}
    if len(by)!=len(old):raise UserError('骨骼名称不能重复')
    done=set();result=[]
    while len(result)<len(old):
        remaining=[b for b in old if b['name'] not in done and (not b.get('parent') or b['parent'] in done)]
        if not remaining:raise UserError('骨骼层级存在循环或父骨骼不存在')
        for b in remaining:result.append(b);done.add(b['name'])
    mapping={i:next(j for j,b in enumerate(result) if b['name']==bone['name']) for i,bone in enumerate(old)}
    for _,_,_,a in attachments(s):
        for pos in weighted_indices(a):
            old_index=a['vertices'][pos]
            if old_index not in mapping:raise UserError('蒙皮引用的骨骼索引不存在')
            a['vertices'][pos]=mapping[old_index]
    s['bones']=result


def rename_bone(s,old,new):
    if old==new:return
    if any(b['name']==new for b in s['bones']):raise UserError('骨骼名称已存在')
    for b in s['bones']:
        if b['name']==old:b['name']=new
        if b.get('parent')==old:b['parent']=new
    for slot in s.get('slots',[]):
        if slot['bone']==old:slot['bone']=new
    for kind in ('ik','transform','path','physics'):
        for c in s.get(kind,[]):
            if c.get('bone')==old:c['bone']=new
            if kind!='path' and c.get('target')==old:c['target']=new
            if 'bones' in c:c['bones']=[new if n==old else n for n in c['bones']]
    for _,_,skin in skins(s):
        if skin and 'bones' in skin:skin['bones']=[new if n==old else n for n in skin['bones']]
    for animation in s.get('animations',{}).values():
        timelines=animation.get('bones',{})
        if old in timelines:timelines[new]=timelines.pop(old)


def validate_state(state):
    s=state['skeleton'];errors=[];warnings=[]
    def check(ok,message):
        if not ok:errors.append(message)
    def finite(data):
        if isinstance(data,float) and not math.isfinite(data):errors.append('存在非有限数值')
        elif isinstance(data,dict):
            for v in data.values():finite(v)
        elif isinstance(data,list):
            for v in data:finite(v)
    finite(s)
    bones=s.get('bones',[]);names=[b.get('name') for b in bones];by={b.get('name'):b for b in bones}
    check(bool(bones) and len(bones)<=1000,'骨骼数量须为 1–1000')
    check(len(set(names))==len(names) and all(isinstance(n,str) and n for n in names),'骨骼名称不合法或重复')
    seen=set()
    for b in bones:
        check(not b.get('parent') or b['parent'] in seen,'父骨骼必须在子骨骼之前：'+str(b.get('name')))
        seen.add(b.get('name'))
        for k in ('scaleX','scaleY'):check(abs(b.get(k,1))>1e-6,'骨骼缩放不能为零：'+str(b.get('name')))
        check(b.get('length',0)>=0,'骨骼长度不能为负数')
    slots=s.get('slots',[]);slot_by={slot['name']:slot for slot in slots}
    check(len(slot_by)==len(slots),'插槽名称重复')
    for slot in slots:check(slot.get('bone') in by,'插槽绑定的骨骼不存在：'+slot['name'])
    paths={p['name'] for p in state['parts']}
    for p in state['parts']:
        try:
            safe_name(p['name'])
            check(not any(c in p['name'] for c in '\n\r:'),'图集区域名称含非法字符')
        except UserError as e:errors.append(str(e))
    check(len(paths)==len(state['parts']),'图集区域名称重复；序列图集请先在 Spine 中拆分')
    types=Counter()
    skin_maps={n:items for n,items,_ in skins(s)}
    for skin,slot,name,a in attachments(s):
        kind=attachment_kind(a);types[kind]+=1
        check(slot in slot_by,'附件引用不存在的插槽：'+slot)
        if kind in ('region','mesh','linkedmesh'):
            check(path_for(name,a) in paths,'缺少附件贴图：'+path_for(name,a))
            check(not a.get('sequence'),'暂不支持序列附件；请先在 Spine 中转换为普通附件')
        if kind=='region':check(a.get('width',0)>0 and a.get('height',0)>0,'图片附件尺寸无效：'+name)
        if kind=='linkedmesh':
            parent=skin_maps.get(a.get('skin',skin),{}).get(slot,{}).get(a.get('parent'))
            check(parent is not None and parent.get('type') in ('mesh','linkedmesh'),'linkedmesh 父附件不存在：'+name)
        if a.get('vertices'):
            try:
                for pos in weighted_indices(a):
                    index=a['vertices'][pos]
                    check(isinstance(index,int) and 0<=index<len(bones),'蒙皮骨骼索引越界：'+name)
            except UserError as e:errors.append(str(e))
        if kind=='mesh':
            uvs=a.get('uvs',[]);tri=a.get('triangles',[])
            check(bool(a.get('vertices')),'mesh 缺少顶点：'+name)
            check(len(uvs)>0 and len(uvs)%2==0,'mesh UV 数量无效：'+name)
            check(len(tri)%3==0 and all(isinstance(i,int) and 0<=i<len(uvs)//2 for i in tri),'mesh 三角形索引无效：'+name)
        if kind=='clipping' and a.get('end'):check(a['end'] in slot_by,'裁切附件引用不存在的结束插槽：'+name)
    for kind in ('ik','transform','path','physics'):
        for c in s.get(kind,[]):
            ns=c.get('bones',[c['bone']] if 'bone' in c else [])
            check(all(n in by for n in ns),'约束引用不存在的骨骼：'+c['name'])
            if 'target' in c:check(c['target'] in (slot_by if kind=='path' else by),'约束目标不存在：'+c['name'])
            if kind=='ik' and len(ns)==2:check(by.get(ns[1],{}).get('parent')==ns[0],'双骨 IK 必须是连续父子骨骼：'+c['name'])
    for _,_,skin in skins(s):
        if skin:
            check(all(n in by for n in skin.get('bones',[])),'皮肤引用的骨骼不存在')
            for kind in ('ik','transform','path','physics'):
                known={c['name'] for c in s.get(kind,[])}
                check(all(n in known for n in skin.get(kind,[])),'皮肤引用的约束不存在')
    for label,anim in s.get('animations',{}).items():
        check(all(n in by for n in anim.get('bones',{})),'动画骨骼引用不存在：'+label)
        check(all(n in slot_by for n in anim.get('slots',{})),'动画插槽引用不存在：'+label)
        for kind in ('ik','transform','path','paths','physics'):
            known={c['name'] for c in s.get('path' if kind=='paths' else kind,[])}
            check(all(n in known for n in anim.get(kind,{})),'动画约束引用不存在：'+label)
        for section in ('deform','attachments'):
            for skin,slots_data in anim.get(section,{}).items():
                for slot,items in slots_data.items():
                    for name in items:check(name in skin_maps.get(skin,{}).get(slot,{}),'变形动画附件引用不存在：'+label+'/'+name)
        for key in ('drawOrder','draworder'):
            for frame in anim.get(key,[]):check(all(x.get('slot') in slot_by for x in frame.get('offsets',[])),'动画绘制顺序引用不存在：'+label)
    if s.get('ik'):warnings.append('IK 会控制链条旋转。调整肢体后请同时检查目标骨骼和全部动作。')
    if types['mesh'] or types['linkedmesh']:warnings.append('网格附件保留权重、UV 和变形动画；新轮廓受现有网格范围约束。可新增普通图片附件扩展外形。')
    warnings.append('动作保留原局部曲线；改变父子关系和比例会改变世界空间运动，请检查全部动作。')
    return {'passed':not errors,'errors':list(dict.fromkeys(errors)),'warnings':warnings,'bones':len(bones),'slots':len(slots),'attachment_types':dict(types),'constraints':{k:len(s.get(k,[])) for k in ('ik','transform','path','physics')},'animations':list(s.get('animations',{}))}


def pack(parts):
    """Deterministic multi-page shelf pack, untrimmed with extruded 2px gutter."""
    pad=2;limit=2048;placements=[];pages=[];x=y=pad;row=0;current=[]
    if any(max(im.size)+pad*2>4096 for _,im in parts):raise UserError('单个部件最多 4092×4092 像素')
    if sum((im.width+4)*(im.height+4) for _,im in parts)>MAX_PIXELS:raise UserError('部件总画布面积过大')
    limit=max(limit,max(max(im.size)+pad*2 for _,im in parts)) if parts else limit
    def flush():
        nonlocal current,x,y,row
        if current:
            w=max(v['x']+v['width']+pad for v in current);h=max(v['y']+v['height']+pad for v in current)
            canvas=Image.new('RGBA',(w,h))
            for v in current:
                im=v['image'];px=v['x'];py=v['y'];iw=im.width;ih=im.height
                canvas.paste(im,(px,py))
                canvas.paste(im.crop((0,0,iw,1)).resize((iw,pad)),(px,py-pad))
                canvas.paste(im.crop((0,ih-1,iw,ih)).resize((iw,pad)),(px,py+ih))
                canvas.paste(im.crop((0,0,1,ih)).resize((pad,ih)),(px-pad,py))
                canvas.paste(im.crop((iw-1,0,iw,ih)).resize((pad,ih)),(px+iw,py))
                for cx,cy,tx,ty in [(0,0,px-pad,py-pad),(iw-1,0,px+iw,py-pad),(0,ih-1,px-pad,py+ih),(iw-1,ih-1,px+iw,py+ih)]:
                    canvas.paste(Image.new('RGBA',(pad,pad),im.getpixel((cx,cy))),(tx,ty))
                v['page']=len(pages);placements.append({k:val for k,val in v.items() if k!='image'})
            pages.append(canvas)
        current=[];x=y=pad;row=0
    for part,im in sorted(parts,key=lambda pair:(-pair[1].height,pair[0]['name'])):
        if x+im.width+pad>limit:x=pad;y+=row+pad*2;row=0
        if y+im.height+pad>limit:flush()
        current.append({'name':part['name'],'x':x,'y':y,'width':im.width,'height':im.height,'image':im})
        x+=im.width+pad*2;row=max(row,im.height)
    flush();lines=[];files={}
    for i,im in enumerate(pages):
        page=f'rig-page-{i}.png';files[page]=png_bytes(im)
        if i:lines.append('')
        # 3.8's older atlas reader cannot consume a pma header. Both runtimes
        # default to straight alpha, which is also explicitly set in preview.
        lines.extend([page,f'size: {im.width},{im.height}','format: RGBA8888','filter: Linear,Linear','repeat: none'])
        for p in placements:
            if p['page']!=i:continue
            lines.extend([p['name'],'  rotate: false',f"  xy: {p['x']}, {p['y']}",f"  size: {p['width']}, {p['height']}",f"  orig: {p['width']}, {p['height']}",'  offset: 0, 0','  index: -1'])
    files['skeleton.atlas']=('\n'.join(lines)+'\n').encode()
    return files,placements


class RigProject:
    def __init__(self,directory):
        self.directory=Path(directory);self.id=self.directory.name;self.lock=threading.RLock()
        self.meta=json.loads((self.directory/'rig.json').read_text());self.cache=None

    @classmethod
    def create(cls,directory,source):
        if source.skeleton_data is None:raise UserError('骨骼编辑需要 JSON，请在 Spine 中把 SKEL 导出为 JSON 后导入')
        if source.runtime not in ('3.8','4.2'):raise UserError('第二步暂支持可预览的 Spine 3.8 / 4.2 JSON')
        directory=Path(directory);directory.mkdir(parents=True)
        rig=object.__new__(cls);rig.directory=directory;rig.id=directory.name;rig.lock=threading.RLock();rig.cache=None
        state={'skeleton':copy.deepcopy(source.skeleton_data),'parts':[],'source_id':source.id,'source_revision':source.revision}
        for r in source.regions:
            if r['index']!=-1:raise UserError('序列图集暂不支持骨骼编辑，请先在 Spine 中拆分序列')
            blob=rig.put_blob(png_bytes(source.part(r['id'])))
            state['parts'].append({'id':r['id'],'name':r['name'],'sha':blob,'width':r['original_width'],'height':r['original_height']})
        report=validate_state(state)
        if not report['passed']:raise UserError('无法建立骨骼项目：'+'；'.join(report['errors'][:5]))
        base=source.raw_data(False)
        atomic_json(directory/'source-preview.json',base)
        rig.meta={'id':rig.id,'name':source.label+' · 骨骼与贴图','revision':0,'state':state,'history':[],'source_skeleton_sha':sha(source.files[source.skeleton])}
        rig.commit(state,'从当前换皮创建',initial=True)
        return rig

    @property
    def state(self):return self.meta['state']

    def put_blob(self,data):
        im=read_image(data)
        if max(im.size)>4092:raise UserError('单个部件最多 4092×4092 像素')
        data=png_bytes(im);digest=sha(data);path=self.directory/'blobs'/(digest+'.png')
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_bytes(data);tmp.replace(path)
        return digest

    def blob(self,part):
        digest=part['sha']
        if not re.fullmatch('[a-f0-9]{64}',digest):raise UserError('贴图摘要无效')
        data=(self.directory/'blobs'/(digest+'.png')).read_bytes()
        if sha(data)!=digest:raise UserError('贴图摘要校验失败')
        return data

    def check_revision(self,value):
        if value!=self.meta['revision']:raise UserError('项目已更新，请刷新页面后重试')

    def commit(self,state,label,initial=False):
        report=validate_state(state)
        if not report['passed']:raise UserError('；'.join(report['errors'][:6]))
        if len(state['parts'])>1000:raise UserError('最多支持 1000 个图集部件')
        if sum(p['width']*p['height'] for p in state['parts'])>MAX_PIXELS:raise UserError('部件总面积超过限制')
        next_meta=copy.deepcopy(self.meta);next_meta['state']=state
        if not initial:next_meta['revision']+=1
        next_meta['history'].append({'id':uuid.uuid4().hex,'label':str(label)[:100],'created_at':time.time(),'revision':next_meta['revision'],'state':copy.deepcopy(state)})
        atomic_json(self.directory/'rig.json',next_meta);self.meta=next_meta;self.cache=None

    def summary(self):
        state=self.state;s=state['skeleton'];version=s.get('skeleton',{}).get('spine','')
        return {'id':self.id,'name':self.meta['name'],'revision':self.meta['revision'],'source_id':state['source_id'],'source_revision':state['source_revision'],'version':version,'runtime':version[:3],
                'skeleton':copy.deepcopy(s),'parts':copy.deepcopy(state['parts']),'material_sources':copy.deepcopy(self.meta.get('material_sources',[])),'history':[{k:v for k,v in h.items() if k!='state'} for h in reversed(self.meta['history'])],'validation':validate_state(state)}

    def part(self,id):return next(p for p in self.state['parts'] if p['id']==id)

    def mutate(self,action,data):
        with self.lock:
            self.check_revision(data['revision']);state=copy.deepcopy(self.state);s=state['skeleton'];label=''
            if action=='bone':
                old=data['bone'];b=next(b for b in s['bones'] if b['name']==old);new=name_value(data.get('name',old))
                for key in ('x','y','rotation','scaleX','scaleY','length','shearX','shearY'):
                    if key in data:b[key]=number(data[key],0 if key=='length' else -100000,100000)
                parent=data.get('parent',b.get('parent',''))
                if parent:b['parent']=parent
                else:b.pop('parent',None)
                rename_bone(s,old,new);reorder_bones(s);label='修改骨骼：'+new
            elif action=='add-bone':
                name=name_value(data['name'])
                if any(b['name']==name for b in s['bones']):raise UserError('骨骼名称已存在')
                b={'name':name,'parent':data['parent'],'x':number(data.get('x',0)),'y':number(data.get('y',0)),'length':number(data.get('length',30),0)}
                s['bones'].append(b);reorder_bones(s);label='新增骨骼：'+name
            elif action=='delete-bone':
                name=data['bone'];idx=next(i for i,b in enumerate(s['bones']) if b['name']==name)
                if any(b.get('parent')==name for b in s['bones']):raise UserError('请先重新绑定子骨骼，再删除此骨骼')
                if any(slot['bone']==name for slot in s.get('slots',[])):raise UserError('此骨骼仍绑定插槽，请先调整附件绑定')
                if any(name in c.get('bones',[]) or c.get('bone')==name or (kind!='path' and c.get('target')==name) for kind in ('ik','transform','path','physics') for c in s.get(kind,[])):raise UserError('此骨骼被约束引用，不能直接删除')
                for _,_,_,a in attachments(s):
                    if any(a['vertices'][pos]==idx for pos in weighted_indices(a)):raise UserError('此骨骼仍有网格权重，请先在 Spine 中重新蒙皮')
                for _,_,skin in skins(s):
                    if skin and name in skin.get('bones',[]):raise UserError('此骨骼被皮肤引用，不能直接删除')
                # Remap indices before removing, since deletion changes the index space.
                for _,_,_,a in attachments(s):
                    for pos in weighted_indices(a):
                        if a['vertices'][pos]>idx:a['vertices'][pos]-=1
                s['bones'].pop(idx)
                for anim in s.get('animations',{}).values():anim.get('bones',{}).pop(name,None)
                label='删除未绑定骨骼：'+name
            elif action in ('texture','recolor','source-ai'):
                p=next(p for p in state['parts'] if p['id']==data['part'])
                if action=='recolor':
                    im=read_image(self.blob(p));alpha=im.getchannel('A');hue=number(data.get('hue',0),-180,180)
                    hsv=np.array(im.convert('RGB').convert('HSV'));hsv[...,0]=(hsv[...,0].astype(np.int16)+round(hue*255/360))%256
                    im=Image.fromarray(hsv,'HSV').convert('RGB');im=ImageEnhance.Color(im).enhance(number(data.get('saturation',1),0,2));im=ImageEnhance.Brightness(im).enhance(number(data.get('brightness',1),.2,2)).convert('RGBA');im.putalpha(alpha)
                else:im=read_image(base64.b64decode(data['image'],validate=True))
                old_size=(p['width'],p['height']);p.update(sha=self.put_blob(png_bytes(im)),width=im.width,height=im.height)
                if data.get('resize_geometry'):
                    for _,_,n,a in attachments(s):
                        if a.get('type','region')=='region' and path_for(n,a)==p['name']:
                            a['width']=a.get('width',old_size[0])*im.width/old_size[0];a['height']=a.get('height',old_size[1])*im.height/old_size[1]
                label='替换贴图：'+p['name'] if action!='recolor' else '调色：'+p['name']
            elif action=='texture-repair':
                from .seam_review import repair_texture
                p=self.part(data['part'])
                def resolve(item):
                    if item.get('source_part'):
                        record=next((r for r in self.state['parts'] if r['id']==item['source_part']),None)
                        if record is None:raise UserError('原像素贴图不属于当前骨架')
                    else:
                        record=next((r for r in self.meta.get('material_sources',[]) if r['id']==item.get('source_material')),None)
                        if record is None:raise UserError('原像素参考不属于当前骨架')
                    return read_image(self.blob(record)),record['sha']
                im,repair=repair_texture(read_image(self.blob(p)),data,resolve)
                target=next(x for x in state['parts'] if x['id']==p['id'])
                target['sha']=self.put_blob(png_bytes(im))
                state.setdefault('texture_repairs',[]).append({'part':p['id'],'before_sha':p['sha'],'after_sha':target['sha'],**repair})
                label='修复局部白底／连接：'+p['name']
            elif action=='material-finish':
                from .material_finish import finish_material
                p=self.part(data['part'])
                im,repair=finish_material(read_image(self.blob(p)),data)
                target=next(x for x in state['parts'] if x['id']==p['id'])
                target['sha']=self.put_blob(png_bytes(im))
                state.setdefault('material_repairs',[]).append({'part':p['id'],'before_sha':p['sha'],'after_sha':target['sha'],**repair})
                label='修整局部轮廓／透明材质：'+p['name']
            elif action=='slot':
                slot=next(slot for slot in s['slots'] if slot['name']==data['slot'])
                slot['bone']=data['bone'];label='重新绑定插槽：'+slot['name']
            elif action=='attachment':
                slot=next(slot for slot in s['slots'] if slot['name']==data['slot'])
                skin_map=next(items for name,items,_ in skins(s) if name==data['skin']);a=skin_map[data['slot']][data['attachment']]
                if data.get('bone'):slot['bone']=data['bone']
                if a.get('type','region')=='region':
                    for k in ('x','y','rotation','scaleX','scaleY','width','height'):
                        if k in data:a[k]=number(data[k],.001 if k in ('width','height') else -100000)
                elif any(k in data for k in ('x','y','rotation','scaleX','scaleY','width','height')):raise UserError('网格位置由骨骼和权重控制，请编辑其骨骼')
                label='调整附件：'+data['attachment']
            elif action=='delete-attachment':
                skin_map=next(items for name,items,_ in skins(s) if name==data['skin']);slot=data['slot'];name=data['attachment']
                a=skin_map[slot][name];path=path_for(name,a)
                if any(attachment_kind(other)=='linkedmesh' and other.get('parent')==name and other_slot==slot and other.get('skin',other_skin)==data['skin'] for other_skin,other_slot,_,other in attachments(s)):
                    raise UserError('此附件被 linkedmesh 引用，请先处理关联附件')
                del skin_map[slot][name]
                if not skin_map[slot]:del skin_map[slot]
                available=any(other_slot==slot and other_name==name for _,other_slot,other_name,_ in attachments(s))
                if not available:
                    for item in s['slots']:
                        if item['name']==slot and item.get('attachment')==name:item.pop('attachment',None)
                for anim in s.get('animations',{}).values():
                    for section in ('deform','attachments'):anim.get(section,{}).get(data['skin'],{}).get(slot,{}).pop(name,None)
                    if not available:
                        for frame in anim.get('slots',{}).get(slot,{}).get('attachment',[]):
                            if frame.get('name')==name:frame['name']=None
                if not any(other.get('type','region') in ('region','mesh','linkedmesh') and path_for(other_name,other)==path for _,_,other_name,other in attachments(s)):
                    state['parts']=[p for p in state['parts'] if p['name']!=path]
                if not state['parts']:raise UserError('请至少保留一个图片附件')
                label='删除附件：'+name
            elif action=='ik':
                c=next(c for c in s.get('ik',[]) if c['name']==data['name'])
                c['target']=data['target'];c['bones']=data['bones'];c['mix']=number(data.get('mix',1),0,1)
                c['softness']=number(data.get('softness',0),0);c['bendPositive']=bool(data.get('bendPositive',True))
                label='调整 IK：'+c['name']
            elif action=='add-part':
                name=safe_name(name_value(data['name']));im=read_image(base64.b64decode(data['image'],validate=True));bone=data['bone']
                if name in {p['name'] for p in state['parts']} or name in {sl['name'] for sl in s['slots']}:raise UserError('部件或插槽名称已存在')
                p={'id':'p'+uuid.uuid4().hex,'name':name,'sha':self.put_blob(png_bytes(im)),'width':im.width,'height':im.height};state['parts'].append(p)
                s['slots'].append({'name':name,'bone':bone,'attachment':name})
                for _,items,_ in skins(s):items[name]={name:{'width':im.width,'height':im.height,'x':number(data.get('x',0)),'y':number(data.get('y',0))}}
                label='新增附件：'+name
            elif action=='skeleton':
                value=data['skeleton']
                if not isinstance(value,dict) or not isinstance(value.get('bones'),list):raise UserError('请上传有效的 Spine JSON 骨架')
                old_version=s.get('skeleton',{}).get('spine','')[:3]
                if value.get('skeleton',{}).get('spine','')[:3]!=old_version:raise UserError('新骨架须使用相同 Spine 版本，不能直接改版本号转换格式')
                # JSON contains its own bone index space; never remap via the old skeleton.
                s=copy.deepcopy(value);state['skeleton']=s;label='导入替换骨架 JSON'
            elif action=='restore':
                entry=next((h for h in self.meta['history'] if h['id']==data['snapshot_id']),None)
                if entry is None:raise UserError('历史版本不存在')
                state=copy.deepcopy(entry['state'])
                for p in state['parts']:self.blob(p)
                label='恢复：'+entry['label']
            elif action=='save':label=str(data.get('label','')).strip() or '手动保存版本'
            else:raise UserError('未知骨骼操作')
            self.commit(state,label);return self.summary()

    def files(self):
        if self.cache is not None:return self.cache
        parts=[(p,read_image(self.blob(p))) for p in self.state['parts']]
        files,placements=pack(parts);s=copy.deepcopy(self.state['skeleton']);meta=s.setdefault('skeleton',{})
        meta.pop('hash',None);meta['images']='./images/';meta.pop('audio',None)
        files['skeleton.json']=(json.dumps(s,ensure_ascii=False,indent=2)+'\n').encode()
        self.cache=(files,placements);return self.cache

    def raw_data(self,original=False):
        if original:return json.loads((self.directory/'source-preview.json').read_text())
        files,_=self.files()
        return {'skeleton':'skeleton.json','atlas':'skeleton.atlas','pma':False,'rawDataURIs':{name:'data:'+('image/png' if name.endswith('.png') else 'application/json' if name.endswith('.json') else 'application/octet-stream')+';base64,'+base64.b64encode(data).decode() for name,data in files.items()}}

    def validate(self):
        report=validate_state(self.state)
        try:
            files,placements=self.files();pages,regions=parse_atlas(files['skeleton.atlas'].decode())
            for p in self.state['parts']:
                im=read_image(self.blob(p));r=next(r for r in regions if r['name']==p['name']);page=read_image(files[pages[r['page']]['name']]);patch=page.crop((r['x'],r['y'],r['x']+r['width'],r['y']+r['height']))
                if im.size!=(p['width'],p['height']) or png_bytes(patch)!=png_bytes(im):raise UserError('打包后部件像素不一致：'+p['name'])
            report.update(pages=len(pages),parts=len(regions),texture_pixels_preserved=True)
        except UserError as e:report['passed']=False;report['errors'].append(str(e))
        report.update(revision=self.meta['revision'],source_id=self.state['source_id'],source_revision=self.state['source_revision'],scope='骨架结构、引用与打包像素校验；仍须在匹配版本 runtime 检查全部动作')
        return report

    def export(self):
        report=self.validate()
        if not report['passed']:raise UserError('骨架校验失败，已阻止导出')
        files,placements=self.files();out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in files.items():z.writestr(name,data)
            for p in self.state['parts']:z.writestr('images/'+safe_name(p['name'])+'.png',self.blob(p))
            z.writestr('_rig-validation.json',json.dumps(report,ensure_ascii=False,indent=2))
            z.writestr('_rig-manifest.json',json.dumps({'source_skeleton_sha':self.meta['source_skeleton_sha'],'source_id':self.state['source_id'],'source_revision':self.state['source_revision'],'revision':self.meta['revision'],'regions':placements},ensure_ascii=False,indent=2))
            z.writestr('README.txt','Spine 骨骼与贴图项目\n导入 skeleton.json，图片路径 ./images/。运行时使用 skeleton.json + skeleton.atlas + rig-page-*.png。\n需使用与 skeleton.spine 对应的 Spine 编辑器 / runtime；本包不包含 .spine 原生工程或 SKEL 转换。\n')
        return out.getvalue()
