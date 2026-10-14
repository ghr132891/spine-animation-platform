"""Common-canvas assets and reconstruction evidence. Never infer hidden pixels."""
import hashlib
import json
import numpy as np
from PIL import Image
from .core import UserError, png_bytes

CONTRACT='canvas-layers-v1'
CHECKS=('ownership_correct','camera_consistent','identity_preserved','connections_complete')

def ownership(items):
    owners={}
    for item in items:
        claims=item.get('owns')
        if not isinstance(claims,list) or not claims or any(not isinstance(v,str) or not v or len(v)>80 for v in claims):
            raise UserError('每个同坐标部件须声明非空 owns 素材归属')
        for claim in claims:
            if claim in owners:raise UserError('可见素材重复归属：'+claim)
            owners[claim]=item['name']
    return owners

def placement(name,image,record,landmarks,size,segments):
    """Integer source-canvas offset only: importing never rescales artwork."""
    xy=record['canvas_offset'];x,y=xy;w,h=image.size
    if any(type(v)!=int for v in xy) or not (0<=x and 0<=y and x+w<=size[0] and y+h<=size[1]):
        raise UserError('同坐标部件超出母版画布')
    start,end=next((a,b) for n,a,b in segments(landmarks) if n==name)
    part={'name':name,'image':image,'box':[x,y,x+w,y+h],'start':start,'end':end,'complete':record['complete'],
          'pixel_count':int((np.asarray(image)[:,:,3]>0).sum()),'canvas_anchors':{key:landmarks[key] for key in (start,end)}}
    if record.get('chain'):
        from .reviewed_parts import CHAINS
        part.update(chain=record['chain'],chain_points={name[:2]+k:record['canvas_pins'][k] for k in CHAINS[record['chain']]})
        part['end']=name[:2]+CHAINS[record['chain']][-1]
        for key,point in part['chain_points'].items():
            if np.linalg.norm(np.array(landmarks[key])*size-point)>.5:raise UserError('同坐标关节点已改变，重新安装图层以同步 pins')
    if record.get('group'):part['group']=record['group']
    return part

def reconstruction(reference,parts,order,owners):
    from .part_review import compose
    if len(order)!=len(parts) or set(order)!={p['name'] for p in parts}:raise UserError('拼回验收须完整且唯一的绘制顺序')
    assembled=compose(parts,reference.size,order)
    a=np.asarray(reference.convert('RGBA'),dtype=float)/255;b=np.asarray(assembled,dtype=float)/255
    # Premultiplied RGBA avoids counting invisible RGB and keeps sheer fabric.
    pa=a[:,:,:3]*a[:,:,3:];pb=b[:,:,:3]*b[:,:,3:]
    mask=(a[:,:,3]>.05)|(b[:,:,3]>.05)
    if not mask.any():raise UserError('拼回验收缺少可见素材')
    error=np.max(np.abs(pa-pb),axis=2);alpha=np.abs(a[:,:,3]-b[:,:,3])
    i=(a[:,:,3]>.1)&(b[:,:,3]>.1);u=(a[:,:,3]>.1)|(b[:,:,3]>.1)
    payload={'reference':hashlib.sha256(png_bytes(reference)).hexdigest(),'parts':[(p['name'],p['box'],p.get('chain_points'),p.get('canvas_anchors'),hashlib.sha256(png_bytes(p['image'])).hexdigest()) for p in parts],'order':order,'owners':owners}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    metrics={'silhouette_iou':float(i.sum()/max(1,u.sum())),'visible_rgb_mae':float(error[mask].mean()),'alpha_mae':float(alpha[mask].mean()),'changed_visible_fraction':float(((error>.12)|(alpha>.12))[mask].mean())}
    # Conservative triage thresholds, not a semantic/artistic pass.
    numerical=metrics['silhouette_iou']>=.97 and metrics['visible_rgb_mae']<=.04 and metrics['changed_visible_fraction']<=.08
    heat=np.zeros((*error.shape,4),dtype=np.uint8);heat[:,:,0]=255;heat[:,:,3]=np.rint(np.clip(np.maximum(error,alpha)*3,0,1)*255).astype('uint8')
    return {'contract':CONTRACT,'digest':digest,'metrics':metrics,'numerical_checks_passed':numerical,'visual_verdict':'not_reviewed','owners':owners,'limits':'数值仅用于筛查；相机、遮挡补全、身份和素材归属仍须实看。'},assembled,Image.fromarray(heat)

def require(report,review):
    if not isinstance(review,dict) or review.get('digest')!=report['digest']:raise UserError('先查看并验收当前同坐标拼回图；旧资源结论失效')
    checks=review.get('checks',{})
    if set(checks)!=set(CHECKS) or any(type(v)!=bool for v in checks.values()):raise UserError('拼回验收须记录四项真实视觉结论')
    if review.get('verdict')!='ready' or not all(checks.values()) or not report['numerical_checks_passed']:
        raise UserError('拼回原图未通过，请保留草稿并修复差异后再正式构建')

def partition(reference,items,order):
    """Partition existing visible pixels, preserving coordinates and color.

    Optional full-canvas fill artwork is accepted only behind later layers and
    inside their opaque visible area. No claim that inferred artwork is correct.
    """
    from PIL import ImageDraw
    owners=ownership(items);by={item['name']:item for item in items}
    if len(by)!=len(items) or len(order)!=len(items) or set(order)!=set(by):raise UserError('图层顺序或名称无效')
    w,h=reference.size;labels=np.full((h,w),-1,dtype=np.int16);arr=np.array(reference.convert('RGBA'))
    for item in items:
        name=item['name'];index=order.index(name)
        poly=by[name].get('polygon')
        if not isinstance(poly,list) or not 3<=len(poly)<=128:raise UserError('每层需母版像素polygon')
        if any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not np.isfinite(v) for v in p) or not (0<=p[0]<=w and 0<=p[1]<=h) for p in poly):raise UserError('图层轮廓超出母版')
        mask=Image.new('L',(w,h));ImageDraw.Draw(mask).polygon([tuple(p) for p in poly],fill=255)
        if item.get('mask') is not None:
            if item['mask'].size!=reference.size:raise UserError('像素蒙版须与母版同尺寸')
            mask=item['mask'].convert('L')
        labels[np.asarray(mask)>0]=index
    if np.any((arr[:,:,3]>3)&(labels<0)):raise UserError('存在未归属的可见原图像素，先补齐图层轮廓')
    layers=[]
    for index,name in enumerate(order):
        pixels=arr.copy();pixels[labels!=index]=0
        image=Image.fromarray(pixels);completed=by[name].get('completed_image');fill=by[name].get('fill_image')
        if completed is not None:
            if completed.size!=reference.size:raise UserError('完整部件须与母版同尺寸')
            # Complete silhouettes may overlap other layers. Visible ownership
            # identifies source pixels to preserve, not an alpha clipping mask.
            full=np.array(completed.convert('RGBA'));visible=(labels==index)&(arr[:,:,3]>0);full[visible]=arr[visible];image=Image.fromarray(full)
            if fill is not None:raise UserError('完整部件与旧式遮挡补片不能同时传入')
        if fill is not None:
            if fill.size!=reference.size:raise UserError('补片必须使用同一母版画布')
            f=np.array(fill.convert('RGBA'));allowed=(labels>index)&(arr[:,:,3]==255)
            if np.any((f[:,:,3]>0)&~allowed):raise UserError('补片越过遮挡区；可见原像素不能被替换')
            background=Image.fromarray(f);background.alpha_composite(image);image=background
        box=image.getbbox()
        if box is None:raise UserError('图层没有像素：'+name)
        layers.append({'name':name,'image':image.crop(box),'offset':list(box[:2]),'owns':by[name]['owns']})
    return layers,owners
