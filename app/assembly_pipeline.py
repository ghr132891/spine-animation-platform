"""Executable preparation shared by import, preview and runtime export.

Complete silhouettes remain independent. Canonicalization registers pixels once;
it never intersects alpha with the original visible segmentation.
"""
import copy
from functools import wraps
import re
import numpy as np
from PIL import Image
from .core import UserError, png_bytes, sha, read_image
from .material_finish import coverage


def atomic_assembly(method):
    """Publish one revision only after the entire import/repair validates.

    Content-addressed blobs may be written during preparation; failed operations
    never switch the live character to those blobs or lose its accepted state.
    """
    @wraps(method)
    def run(character,*args,**kwargs):
        with character.lock:
            staged=copy.copy(character);staged.meta=copy.deepcopy(character.meta);staged.cache={}
            def save(meta):
                staged.meta=copy.deepcopy(meta);staged.meta['revision']+=1
            staged.save=save
            method(staged,*args,**kwargs)
            character.save(staged.meta);character.cache={}
            return character.summary()
    return run


def reconcile_overlays(character):
    """Retain unaffected foreground art; expose invalidated recipes explicitly."""
    meta=copy.deepcopy(character.meta);items=meta.get('assembly_overlays',[])
    if not items:return
    parts=character.layered_parts(character.image(),meta['landmarks'],meta.get('masks',{}),adjust=False,overlays=False)
    retained=[];invalid=[]
    for item in items:
        try:validate_overlays(parts,[item]);retained.append(item)
        except UserError as exc:invalid.append({'name':item['name'],'source':item['source'],'reason':str(exc)})
    if invalid:
        meta['assembly_overlays']=retained
        meta.setdefault('assembly_invalidations',[]).extend(invalid)
        for key in ('prebind_review','overlap_review','reconstruction_review'):meta.pop(key,None)
        character.save(meta)


def replace_from_sources(character,items):
    """Reuse complete source sprites with explicit pixel anchors, no AI call."""
    from .character import CharacterProject, LABELS
    from .semantic_completion import register_sprite
    if not isinstance(items,list) or not 1<=len(items)<=16:raise UserError('补全来源须为1–16项')
    meta=copy.deepcopy(character.meta);seen=set();prepared=[];history=[]
    for item in items:
        if not isinstance(item,dict):raise UserError('补全来源须为对象')
        name=item.get('name');cid=item.get('character_id','');source=item.get('part',name)
        if name not in LABELS or name in seen or not re.fullmatch('[a-f0-9]{32}',cid):raise UserError('补全部件或来源编号无效')
        seen.add(name);c=CharacterProject(character.directory.parent/cid)
        p=next((p for p in c.layered_parts(c.image(),c.meta['landmarks'],c.meta.get('masks',{}),adjust=False,overlays=False) if p['name']==source),None)
        if p is None or not p.get('complete'):raise UserError('来源不是完整部件：'+str(source))
        # Work from the stored crop, not a screenshot or an occluded composite.
        r=c.meta['part_layers'][source];image=read_image((c.directory/'part-images'/(r['image_id']+'.png')).read_bytes())
        if r.get('chain') and name not in ('l-upper-'+r['chain'],'r-upper-'+r['chain']):raise UserError('补全来源与目标肢体类型不同')
        if r.get('group')=='costume' and name!='torso':raise UserError('完整衣装只能补入torso')
        if not r.get('chain') and source!=name:raise UserError('主体部件补全须使用相同类型')
        automatic='source_pair' not in item and 'target_pair' not in item
        pins=item.get('pins',{})
        source_pair=item.get('source_pair');target_pair=item.get('target_pair')
        if automatic:
            # Use calibrated, unadjusted complete pixels and matching semantic
            # joints. Never estimate coordinates from grid cells or a silhouette.
            image=p['image'];offset=np.array(p['box'][:2]);target_size=np.array(character.image().size)
            if r.get('chain'):
                from .reviewed_parts import CHAINS
                joints=CHAINS[r['chain']]
                pins={j:(np.array(p['chain_points'][source[:2]+j])-offset).tolist() for j in joints}
                source_pair=[pins[joints[0]],pins[joints[-1]]]
                target_pair=[(np.array(meta['landmarks'][name[:2]+j])*target_size).tolist() for j in (joints[0],joints[-1])]
            else:
                source_size=np.array(c.image().size)
                source_pair=[(np.array(c.meta['landmarks'][j])*source_size-offset).tolist() for j in (p['start'],p['end'])]
                target_pair=[(np.array(meta['landmarks'][j])*target_size).tolist() for j in (p['start'],p['end'])]
            if 'pins' in item:raise UserError('自动配准不接受混用手工pins；需要手动时提供两对锚点')
        if not isinstance(pins,dict):raise UserError('pins须为关节点对象')
        if r.get('chain'):
            from .reviewed_parts import CHAINS
            if set(pins)!=set(CHAINS[r['chain']]):raise UserError('补全连续肢体须给四个源贴图像素关节')
        for xy in pins.values():
            if not isinstance(xy,list) or len(xy)!=2 or any(type(v) not in (int,float) or not np.isfinite(v) for v in xy):raise UserError('源关节点必须是有限像素坐标')
        canvas,mapped,registration=register_sprite(image,source_pair,target_pair,character.image().size,pins)
        if any(not (0<=xy[0]<=canvas.width and 0<=xy[1]<=canvas.height) for xy in mapped.values()):raise UserError('补全关节点超出画布')
        box=canvas.getbbox()
        if not box or box[0]==0 or box[1]==0 or box[2]==canvas.width or box[3]==canvas.height:raise UserError('完整素材触及画布边缘，先留出足够空间')
        blob=png_bytes(canvas.crop(box));digest=sha(blob);prepared.append((digest,blob))
        record={'canvas_offset':list(box[:2]),'image_id':digest,'source_image_id':meta['image_id'],'owns':[name],'complete':True,'mode':'canvas-layer','coverage':[name]}
        if r.get('chain'):
            chain=r['chain'];record.update(chain=chain,canvas_pins=mapped,coverage=[name[:2]+x for x in (('upper-arm','lower-arm','hand') if chain=='arm' else ('upper-leg','lower-leg','foot'))])
            for joint,xy in mapped.items():meta['landmarks'][name[:2]+joint]=[xy[0]/canvas.width,xy[1]/canvas.height]
        if r.get('group')=='costume':record.update(group='costume',coverage=['torso','pelvis'])
        for old in list(meta['part_layers']):
            if set(meta['part_layers'][old].get('coverage',[old]))&set(record['coverage']):meta['part_layers'].pop(old)
        record['completion_source']={'character_id':cid,'part':source,'sha256':sha(png_bytes(image)),'registration':registration,'anchor_mode':'landmarks' if automatic else 'explicit'}
        meta['part_layers'][name]=record;history.append({'part':name,**record['completion_source']})
    meta.setdefault('assembly_completion_history',[]).append(history)
    inputs=meta.get('assembly_inputs',{})
    for name in seen:inputs.pop(name,None)
    for key in ('prebind_review','overlap_review','reconstruction_review'):meta.pop(key,None)
    meta.setdefault('part_review',{}).setdefault('adjustments',{})
    for name in seen:meta['part_review']['adjustments'].pop(name,None)
    meta['canvas_owners']={claim:n for n,r in meta['part_layers'].items() for claim in r.get('owns',[n])}
    for digest,blob in prepared:(character.directory/'part-images'/(digest+'.png')).write_bytes(blob)
    character.save(meta)
    reconcile_overlays(character)


def canonicalize(character):
    if not character.asset_status()['ready']:
        return False
    meta=copy.deepcopy(character.meta)
    if all('canvas_offset' in r for r in meta['part_layers'].values()):
        return False
    parts=character.layered_parts(character.image(),meta['landmarks'],meta.get('masks',{}),adjust=False,overlays=False)
    if any(not p.get('complete') for p in parts):
        raise UserError('补齐独立部件后才能登记同坐标装配')
    originals=copy.deepcopy(meta['part_layers']);inputs=copy.deepcopy(meta.get('assembly_inputs',{}));layers={};owners={};prepared=[]
    W,H=character.image().size
    for p in parts:
        n=p['name'];r=copy.deepcopy(originals[n]);x,y,x1,y1=p['box']
        if 'canvas_offset' not in r:inputs[n]=copy.deepcopy(r)
        if min(x,y)<0 or x1>W or y1>H:
            raise UserError('完整部件超出画布：'+n+'；调整锚点，不能裁掉肢体')
        blob=png_bytes(p['image']);digest=sha(blob);prepared.append((digest,blob))
        r.update(image_id=digest,canvas_offset=[x,y],owns=r.get('owns',[n]),complete=True,mode='canvas-layer')
        if p.get('chain'):
            r['canvas_pins']={k[2:]:v for k,v in p['chain_points'].items()}
            for joint,xy in p['chain_points'].items():meta['landmarks'][joint]=[xy[0]/W,xy[1]/H]
        layers[n]=r
        for claim in r['owns']:
            if claim in owners:raise UserError('素材归属重复：'+claim)
            owners[claim]=n
    meta.update(part_layers=layers,canvas_contract='canvas-layers-v1',canvas_owners=owners,prebind_contract='complete-assets-v1',
        assembly_inputs=inputs,assembly_pipeline={'version':'assembly-execution-v2','steps':['complete-silhouettes','register-common-canvas','preserve-overlap','compose-occlusion'], 'automatic':True})
    for key in ('prebind_review','overlap_review','reconstruction_review'):meta.pop(key,None)
    for digest,blob in prepared:(character.directory/'part-images'/(digest+'.png')).write_bytes(blob)
    character.save(meta)
    return True


def validate_overlays(parts,items):
    if not isinstance(items,list) or len(items)>8:raise UserError('前景遮挡层须为0–8项')
    by={p['name']:p for p in parts};names=set(by);result=[]
    for item in items:
        n=item.get('name','');source=item.get('source');subject=item.get('subject')
        if not re.fullmatch('[a-zA-Z][a-zA-Z0-9_-]{0,79}',n) or n in names:raise UserError('前景遮挡层名称无效或重复')
        if source not in by or subject not in by or source==subject:raise UserError('遮挡层源部件和被遮挡部件无效')
        if not isinstance(item.get('reason'),str) or not item['reason'].strip():raise UserError('记录前后遮挡原因')
        p=by[source]
        if item.get('source_sha256')!=sha(png_bytes(p['image'])):raise UserError('遮挡轮廓与源贴图不匹配，请重新标注')
        mask=coverage(p['image'].size,item.get('polygon'))
        if not np.any(mask*np.asarray(p['image'])[:,:,3]):raise UserError('前景遮挡区没有像素')
        names.add(n);result.append(copy.deepcopy(item))
    return result


def overlay_parts(parts,items):
    validate_overlays(parts,items)
    by={p['name']:p for p in parts};result=list(parts)
    for item in items:
        src=by[item['source']];pixels=np.array(src['image']);pixels[:,:,3]=np.rint(pixels[:,:,3]*coverage(src['image'].size,item['polygon'])).astype('uint8')
        p=copy.copy(src);p.update(name=item['name'],label=item['name'],image=Image.fromarray(pixels),overlay_of=src['name'],complete=True)
        p['pixel_count']=int((pixels[:,:,3]>0).sum());result.append(p)
    return result


def overlay_order(order,items):
    order=[n for n in order if n not in {x['name'] for x in items}]
    for item in items:
        source,subject,name=item['source'],item['subject'],item['name']
        if order.index(source)>order.index(subject):
            order.remove(source);order.insert(order.index(subject),source)
        order.insert(order.index(subject)+1,name)
    return order


def adjust_parts(parts,adjustments,landmarks,size):
    from .part_review import apply_adjustment
    by={p['name']:apply_adjustment(p,adjustments.get(p['name'],{}),np.array(landmarks[p['start']])*size) for p in parts if not p.get('overlay_of')}
    result=[]
    for p in parts:
        if not p.get('overlay_of'):result.append(by[p['name']]);continue
        source=by[p['overlay_of']];transform=source.get('review_transform')
        if not transform:result.append(p);continue
        # Keep the full source-sized canvas. Cropping the foreground independently
        # would change its UVs while export intentionally copies source geometry.
        inverse=np.linalg.inv(transform['matrix']);lo=np.array(source['box'][:2]);shift=inverse@(lo-transform['offset'])-p['box'][:2]
        im=p['image'].transform(source['image'].size,Image.Transform.AFFINE,(*inverse[0],shift[0],*inverse[1],shift[1]),resample=Image.Resampling.BICUBIC)
        result.append({**p,'image':im,'box':source['box'],'review_transform':transform,'pixel_count':int((np.asarray(im)[:,:,3]>0).sum())})
    return result
