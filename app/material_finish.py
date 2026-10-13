"""Explicitly scoped alpha finishing; no inferred garment masks or RGB repainting."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from .core import UserError
from .seam_review import polygon_mask


def coverage(size, polygon):
    # Reuse the normalized-coordinate validator, then supersample the contour.
    polygon_mask(size, [polygon])
    scale=4
    mask=Image.new('L',(size[0]*scale,size[1]*scale))
    ImageDraw.Draw(mask).polygon([(x*(size[0]-1)*scale,y*(size[1]-1)*scale) for x,y in polygon],fill=255)
    return np.asarray(mask.resize(size,Image.Resampling.LANCZOS),dtype=float)/255


def scalar(value, lo, hi, label):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
        raise UserError(label+'超出允许范围')
    return float(value)


def finish_material(image, data):
    """Preserve canvas/RGB/rig. All masks and material decisions come from review."""
    reason=data.get('reason')
    if not isinstance(reason,str) or not reason.strip():raise UserError('材质修整须记录实图检查原因')
    ops=data.get('operations')
    if not isinstance(ops,list) or not 1<=len(ops)<=16:raise UserError('材质修整须提供1–16个局部操作')
    original=np.array(image.convert('RGBA')); result=original.copy();alpha=result[:,:,3].astype(float)
    history=[]
    for op in ops:
        if not isinstance(op,dict):raise UserError('材质操作须为对象')
        selected=coverage(image.size,op.get('polygon'))
        kind=op.get('kind')
        if kind=='erase':target=np.zeros_like(alpha)
        elif kind=='remove-specks':
            limit=scalar(op.get('max_pixels',4),1,32,'残点面积')
            max_alpha=scalar(op.get('max_alpha',255),1,255,'残点最大不透明度')
            # Components are computed on the whole alpha image: clipping the ROI
            # must never turn a valid connected hair/garment edge into a speck.
            target=alpha.copy()
            region=polygon_mask(image.size,[op.get('polygon')])
            for ys,xs in alpha_components(alpha):
                if len(ys)<=limit and np.all(region[ys,xs]) and float(alpha[ys,xs].max())<=max_alpha:
                    target[ys,xs]=0;selected[ys,xs]=1
        elif kind=='edge-trim':
            amount=scalar(op.get('pixels',.5),.05,1,'轮廓收边宽度')
            # Bounded subpixel alpha erosion; no new RGB, global feather or holes.
            a=Image.fromarray(np.rint(alpha).astype('uint8'))
            inner=np.asarray(a.filter(ImageFilter.MinFilter(3)),dtype=float)
            target=alpha*(1-amount)+inner*amount
        elif kind=='opacity':
            # Visual matching is not alpha recovery from an opaque reference.
            if not str(op.get('reference_note','')).strip():raise UserError('透明材质须说明原图参考及调整依据')
            top=scalar(op.get('top',0),0,1,'渐变起点');bottom=scalar(op.get('bottom',1),0,1,'渐变终点')
            if bottom<=top:raise UserError('透明渐变终点须大于起点')
            start=scalar(op.get('start',1),0,1,'起始不透明度');end=scalar(op.get('end'),0,1,'终点不透明度')
            y=np.arange(image.height)/(max(1,image.height-1))
            t=np.clip((y-top)/(bottom-top),0,1);t=t*t*(3-2*t)
            target=alpha*(start+(end-start)*t[:,None])
        elif kind=='edge-cleanup':
            radius=scalar(op.get('radius',.6),.1,2,'边缘平滑半径')
            # Opening removes one-pixel spikes; smoothing changes alpha only.
            a=Image.fromarray(np.rint(alpha).astype('uint8'))
            opened=a.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
            target=np.array(opened.filter(ImageFilter.GaussianBlur(radius)),dtype=float)
            # Do not spread new pixels into empty background or increase opacity.
            target=np.minimum(target,alpha)
        else:raise UserError('不支持的材质操作：'+str(kind))
        alpha=alpha*(1-selected)+target*selected
        history.append(dict(op))
    result[:,:,3]=np.rint(alpha).clip(0,255).astype('uint8')
    changed=result[:,:,3]!=original[:,:,3]
    return Image.fromarray(result),{'reason':reason.strip()[:1000],'operations':history,'changed_pixels':int(changed.sum()),'rgb_preserved':True,'canvas_preserved':True,'alpha_before':alpha_summary(original),'alpha_after':alpha_summary(result)}


def alpha_summary(array):
    a=array[:,:,3]
    return {'opaque_pixels':int((a==255).sum()),'partial_pixels':int(((a>0)&(a<255)).sum()),'visible_pixels':int((a>0).sum())}


def alpha_components(alpha):
    """8-connected visible pixels; thin connected details are never detached specks."""
    mask=np.asarray(alpha)>0;seen=np.zeros(mask.shape,dtype=bool);h,w=mask.shape
    for y,x in zip(*np.where(mask)):
        if seen[y,x]:continue
        stack=[(int(y),int(x))];seen[y,x]=True;ys=[];xs=[]
        while stack:
            cy,cx=stack.pop();ys.append(cy);xs.append(cx)
            for ny in range(max(0,cy-1),min(h,cy+2)):
                for nx in range(max(0,cx-1),min(w,cx+2)):
                    if mask[ny,nx] and not seen[ny,nx]:
                        seen[ny,nx]=True;stack.append((ny,nx))
        yield np.array(ys),np.array(xs)


def inspect_alpha(image):
    if image.width*image.height>2_000_000:return {'semantic_verdict':'not_assessed','reason':'large_texture','note':'贴图过大，按局部范围检查，不能视为无残点'}
    a=np.array(image.convert('RGBA'));items=[]
    for ys,xs in alpha_components(a[:,:,3]):
        if len(ys)<=32:
            items.append({'pixels':len(ys),'box':[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)],'alpha_max':int(a[ys,xs,3].max())})
    return {'small_components':items[:128],'small_component_count':len(items),'visible_pixels':int((a[:,:,3]>0).sum()),'semantic_verdict':'requires_review','note':'孤立点可能是合法装饰；只在已确认范围清理，不自动删除连通发丝或透明衣料。'}


def clean_alpha_dust(image):
    """Remove only <=4-pixel, alpha<=3 islands unattached to meaningful artwork.

    Connected antialiasing, opaque details and all RGB are preserved. This is
    interpolation dust removal, not semantic hair/garment segmentation.
    """
    a=np.array(image.convert('RGBA'));alpha=a[:,:,3];weak=(alpha>0)&(alpha<=3)
    if not weak.any():return image,{'removed_pixels':0,'mode':'alpha-dust-v1'}
    strong=Image.fromarray(((alpha>3)*255).astype('uint8')).filter(ImageFilter.MaxFilter(3))
    touches=np.asarray(strong)>0;removed=0
    for ys,xs in alpha_components(weak):
        if len(ys)<=4 and not touches[ys,xs].any():
            a[ys,xs,3]=0;removed+=len(ys)
    return Image.fromarray(a),{'removed_pixels':removed,'mode':'alpha-dust-v1','rgb_preserved':True}
