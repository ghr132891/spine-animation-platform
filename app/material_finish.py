"""Scoped alpha finishing and opt-in edge color decontamination."""
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


def decontaminate_edge(rgb,alpha,selected,op):
    """Replace confirmed bright matte spill using nearby interior RGB only.

    Alpha/opaque artwork and sole contact geometry are never changed. This is
    an explicit local operation, not automatic whitening removal on clothes.
    """
    if not str(op.get('reference_note','')).strip():raise UserError('边缘去色须记录实际污染与保留材质的依据')
    radius=op.get('radius',3)
    if type(radius)!=int or not 1<=radius<=4:raise UserError('边缘取色半径须为1–4像素')
    threshold=scalar(op.get('min_lift',18),8,80,'边缘亮度差')
    strength=scalar(op.get('strength',1),.05,1,'边缘去色强度')
    a=Image.fromarray(np.rint(alpha).astype('uint8'))
    exterior=np.asarray(Image.fromarray(((alpha==0)*255).astype('uint8')).filter(ImageFilter.MaxFilter(2*radius+1)))>0
    seeds=(alpha>=240)&(np.asarray(a.filter(ImageFilter.MinFilter(3)))>=128)
    eligible=(alpha>0)&(alpha<255)&exterior&(selected>0)
    found=np.zeros(alpha.shape,dtype=bool);sample=rgb.astype(float).copy();h,w=alpha.shape
    for dy,dx in sorted(((y,x) for y in range(-radius,radius+1) for x in range(-radius,radius+1) if x or y),key=lambda v:v[0]**2+v[1]**2):
        if dx*dx+dy*dy>radius*radius:continue
        y0,y1=max(0,-dy),min(h,h-dy);x0,x1=max(0,-dx),min(w,w-dx)
        target=(slice(y0,y1),slice(x0,x1));source=(slice(y0+dy,y1+dy),slice(x0+dx,x1+dx))
        use=eligible[target]&~found[target]&seeds[source]
        sample[target][use]=rgb[source][use];found[target][use]=True
    delta=rgb.astype(float)-sample
    spill=eligible&found&(delta.mean(2)>=threshold)&(delta.min(2)>=-5)
    weight=(selected*strength*spill)[:,:,None]
    return np.rint(rgb*(1-weight)+sample*weight).clip(0,255).astype('uint8')


def finish_material(image, data):
    """Preserve canvas/rig. RGB changes require explicit edge decontamination."""
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
        if kind=='copy-pixel-color':
            points=op.get('points')
            if not str(op.get('reference_note','')).strip() or not isinstance(points,list) or not 1<=len(points)<=16:
                raise UserError('局部取色须提供1–16个明确像素和实图依据')
            region=polygon_mask(image.size,[op.get('polygon')]);seen=set()
            for point in points:
                if not isinstance(point,dict):raise UserError('取色像素格式无效')
                target=point.get('target');source=point.get('source');expected=point.get('expected_rgba')
                for xy in (target,source):
                    if not isinstance(xy,list) or len(xy)!=2 or any(type(v)!=int for v in xy) or not (0<=xy[0]<image.width and 0<=xy[1]<image.height):raise UserError('取色坐标须为贴图内整数像素')
                x,y=target;sx,sy=source
                if (x,y) in seen or not region[y,x] or not 0<(x-sx)**2+(y-sy)**2<=16 or alpha[sy,sx]<128:
                    raise UserError('取色须在确认范围内，来源为4像素内的真实可见图面')
                actual=[*map(int,result[y,x,:3]),int(round(alpha[y,x]))]
                if not isinstance(expected,list) or len(expected)!=4 or any(type(v)!=int or not 0<=v<=255 for v in expected) or expected!=actual:
                    raise UserError('待修像素已变化，请重新查看实图')
                seen.add((x,y));result[y,x,:3]=result[sy,sx,:3]
            history.append(dict(op));continue
        if kind=='edge-decontaminate':
            result[:,:,:3]=decontaminate_edge(result[:,:,:3],alpha,selected,op)
            history.append(dict(op));continue
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
    changed=np.any(result!=original,axis=2);rgb_changed=np.any(result[:,:,:3]!=original[:,:,:3],axis=2)
    return Image.fromarray(result),{'reason':reason.strip()[:1000],'operations':history,'changed_pixels':int(changed.sum()),'rgb_changed_pixels':int(rgb_changed.sum()),'rgb_preserved':not bool(rgb_changed.any()),'canvas_preserved':True,'alpha_before':alpha_summary(original),'alpha_after':alpha_summary(result)}


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
