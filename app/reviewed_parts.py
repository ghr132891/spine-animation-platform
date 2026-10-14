"""Reuse visually reviewed candidate parts without assuming their grid semantics.

Coordinates refer to actual candidate pixels. Codex supplies semantic names and
joint pins after looking at the image; this module does not infer anatomy.
"""
import math
import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageChops
from .core import UserError
from .rig import number
from .part_layers import PINS, kind

CHAINS = {'arm': ('shoulder', 'elbow', 'wrist', 'hand'),
          'leg': ('hip', 'knee', 'ankle', 'toe')}


def pixel_box(value, size):
    if not isinstance(value, list) or len(value) != 4:
        raise UserError('部件 box/dest 须为四个像素坐标')
    box = [round(number(v, 0, max(size))) for v in value]
    if not (0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1]):
        raise UserError('部件裁切范围越界或为空')
    return box


def remove_matte(image, tolerance):
    from .character import cut_background
    if np.any(np.array(image.convert('RGBA'))[:,:,3]<250):return image.convert('RGBA')
    result = cut_background(image, tolerance)
    a = np.array(result); bg = a[:, :, 3] == 0
    edge = (np.array(Image.fromarray(bg.astype('uint8') * 255).filter(ImageFilter.MaxFilter(3))) > 0) & ~bg
    # Only an outer edge next to known transparent background is unmatted.
    # Interior white cuffs and bandages remain untouched.
    opaque = (a[:, :, 3] == 255) & (a[:, :, :3].min(2) < 160)
    original = a[:, :, :3].astype(float); pending = edge.copy()
    for dx, dy in sorted([(x, y) for x in range(-3, 4) for y in range(-3, 4) if x or y], key=lambda p: p[0]**2 + p[1]**2):
        ys = slice(max(0, -dy), min(image.height, image.height-dy)); xs = slice(max(0, -dx), min(image.width, image.width-dx))
        yn = slice(max(0, dy), min(image.height, image.height+dy)); xn = slice(max(0, dx), min(image.width, image.width+dx))
        selected = pending[ys, xs] & opaque[yn, xn]
        fg = original[yn, xn]
        alpha = np.median(np.clip((255-original[ys, xs]) / np.maximum(255-fg, 1), 0, 1), axis=2)
        a[ys, xs, :3][selected] = fg[selected].astype('uint8')
        a[ys, xs, 3][selected] = np.minimum(a[ys, xs, 3][selected], np.rint(alpha[selected]*255).astype('uint8'))
        pending[ys, xs][selected] = False
    a[a[:, :, 3] == 0, :3] = 0
    return Image.fromarray(a, 'RGBA')


def extract_parts(image, items, labels, tolerance=32):
    if not isinstance(items, list) or not 1 <= len(items) <= 16:
        raise UserError('显式布局须提供 1–16 个部件')
    cut = remove_matte(image, tolerance); result = []; seen = set(); covered = set()
    for item in items:
        if not isinstance(item, dict): raise UserError('部件布局须为对象')
        name = item.get('name')
        if name not in labels or name in seen: raise UserError('部件名称无效或重复')
        seen.add(name); chain = item.get('chain'); group = item.get('group')
        if 'group' in item and (group != 'costume' or name != 'torso' or chain is not None):
            raise UserError('完整衣装 group 只支持 torso 的 costume，不能同时设置 chain')
        if chain is not None and (chain not in CHAINS or name not in ('l-upper-'+chain, 'r-upper-'+chain)):
            raise UserError('连续链只支持 l/r-upper-arm 或 l/r-upper-leg')
        coverage = {'torso', 'pelvis'} if group == 'costume' else {name} if chain is None else {name[:2]+p for p in (('upper-arm','lower-arm','hand') if chain=='arm' else ('upper-leg','lower-leg','foot'))}
        if covered & coverage: raise UserError('完整衣装／连续链与单独部件覆盖冲突')
        covered |= coverage
        if 'pieces' in item:
            size = item.get('canvas')
            if not isinstance(size, list) or len(size)!=2 or any(type(v)!=int or not 16<=v<=2048 for v in size):
                raise UserError('组合部件 canvas 须为 16–2048 像素宽高')
            pieces = item['pieces']
            if not isinstance(pieces, list) or not 1<=len(pieces)<=8: raise UserError('组合部件须为 1–8 个已生成裁片')
            sprite = Image.new('RGBA', tuple(size))
            for piece in pieces:
                if not isinstance(piece,dict): raise UserError('组合裁片格式无效')
                box = pixel_box(piece.get('box'), image.size); dest = pixel_box(piece.get('dest'), size)
                patch = cut.crop(box).resize((dest[2]-dest[0],dest[3]-dest[1]),Image.Resampling.LANCZOS)
                sprite.alpha_composite(patch, tuple(dest[:2]))
        else:
            box = pixel_box(item.get('box'), image.size); sprite = cut.crop(box)
        # Crop only existing pixels. Explicit masks remove a duplicated sleeve
        # or neighbouring fragment without redrawing the retained material.
        if 'crop_polygon' in item or 'exclude_polygons' in item:
            polygons=item.get('exclude_polygons',[])
            if not isinstance(polygons,list) or len(polygons)>8:raise UserError('exclude_polygons 须为最多8个多边形')
            mask=Image.new('L',sprite.size,0 if 'crop_polygon' in item else 255);draw=ImageDraw.Draw(mask)
            def polygon(value):
                if not isinstance(value,list) or not 3<=len(value)<=128:raise UserError('裁片多边形须为3–128个归一化坐标点')
                return [(round(point(p)[0]*(sprite.width-1)),round(point(p)[1]*(sprite.height-1))) for p in value]
            if 'crop_polygon' in item:draw.polygon(polygon(item['crop_polygon']),fill=255)
            for value in polygons:draw.polygon(polygon(value),fill=0)
            sprite.putalpha(ImageChops.multiply(sprite.getchannel('A'),mask))
        rotation=number(item.get('rotation',0),-180,180)
        if rotation:
            sprite=sprite.rotate(rotation,expand=True,resample=Image.Resampling.BICUBIC)
        if max(sprite.size)>4096:raise UserError('旋转后部件尺寸超过 4096 像素')
        if not sprite.getbbox(): raise UserError('裁片没有前景像素')
        # Pins belong to the final rotated sprite, including its expanded canvas.
        record = {'layout': {'mode':'reviewed-pixels','rotation':rotation,'sprite_size':list(sprite.size),
                            **({key:item[key] for key in ('crop_polygon','exclude_polygons') if key in item})}, 'width_scale':1}
        if chain is not None:
            pins = item.get('pins')
            if not isinstance(pins,dict) or set(pins)!=set(CHAINS[chain]): raise UserError('连续链须提供四个实际关节 pins')
            record.update(chain=chain, pins={k:point(v) for k,v in pins.items()}, coverage=sorted(coverage))
            if np.linalg.norm(np.array(record['pins'][CHAINS[chain][-1]])-record['pins'][CHAINS[chain][0]]) < .05:
                raise UserError('连续链首尾关节太近')
            points=[np.array(record['pins'][k])*sprite.size for k in CHAINS[chain]]
            if any(np.linalg.norm(b-a)<2 for a,b in zip(points,points[1:])):
                raise UserError('连续链相邻关节太近，请检查实际 pins')
        else:
            if group == 'costume':
                if 'start_pin' not in item or 'end_pin' not in item:
                    raise UserError('完整衣装须显式提供最终贴图的腰部 start_pin 与颈部 end_pin')
                record.update(group='costume', coverage=['torso', 'pelvis'])
            for key,default in zip(('start_pin','end_pin'),PINS[kind(name)]):record[key]=point(item[key]) if key in item else default.copy()
            if np.linalg.norm(np.array(record['start_pin'])-record['end_pin']) < .01: raise UserError('部件关节太近')
        result.append((name,sprite,record))
    return result


def point(value):
    if not isinstance(value,list) or len(value)!=2: raise UserError('关节 pin 须为两个 0–1 坐标')
    return [number(v,0,1) for v in value]


def aligned_chain(name,image,record,landmarks,size):
    chain=record['chain']; keys=CHAINS[chain]; side=name[:2]; W,H=size
    a=np.array(record['pins'][keys[0]])*image.size; b=np.array(record['pins'][keys[-1]])*image.size
    A=np.array(landmarks[side+keys[0]])*[W,H]; B=np.array(landmarks[side+keys[-1]])*[W,H]
    if min(np.linalg.norm(b-a),np.linalg.norm(B-A))<1:raise UserError('连续链的两个关节太近')
    angle=math.atan2(*(B-A)[::-1])-math.atan2(*(b-a)[::-1]); c,s=math.cos(angle),math.sin(angle)
    M=np.array([[c,-s],[s,c]])*(np.linalg.norm(B-A)/np.linalg.norm(b-a)); offset=A-M@a
    corners=np.array([[0,0],[image.width,0],[0,image.height],[image.width,image.height]])@M.T+offset
    lo=np.floor(corners.min(0)).astype(int); hi=np.ceil(corners.max(0)).astype(int); inv=np.linalg.inv(M); shift=inv@(lo-offset)
    if np.any(hi-lo>4096):raise UserError('连续部件尺寸过大，请检查关节比例')
    transformed=image.transform(tuple(hi-lo),Image.Transform.AFFINE,(*inv[0],shift[0],*inv[1],shift[1]),resample=Image.Resampling.BICUBIC)
    pins={side+k:(M@(np.array(record['pins'][k])*image.size)+offset).tolist() for k in keys}
    joints=list(pins.values())
    if any(np.linalg.norm(np.array(b)-a)<4 for a,b in zip(joints,joints[1:])):
        raise UserError('连续链缩放后关节太近，请先校准 guide')
    return {'name':name,'image':transformed,'box':[*lo.tolist(),*hi.tolist()],'start':side+keys[0],'end':side+keys[-1],
            'complete':True,'chain':chain,'chain_points':pins,'pixel_count':int((np.array(transformed)[:,:,3]>0).sum())}


def chain_mesh(part,bones,absposes,scale,origin,H):
    """One continuous mesh across three bones; no independently cut joint seam."""
    side=part['name'][:2]; keys=CHAINS[part['chain']]; kinds=('upper-arm','lower-arm','hand') if part['chain']=='arm' else ('upper-leg','lower-leg','foot')
    names=[b['name'] for b in bones]; bind=[side+k for k in kinds]; pts=[np.array(part['chain_points'][side+k]) for k in keys]
    x0,y0,x1,y1=part['box']; nx,ny=12,32
    transform=part.get('review_transform'); inverse=np.linalg.inv(transform['matrix']) if transform else None
    # The hull is first, followed by interior vertices; triangulation is remapped.
    border=[(x,0) for x in range(nx+1)]+[(nx,y) for y in range(1,ny+1)]+[(x,ny) for x in range(nx-1,-1,-1)]+[(0,y) for y in range(ny-1,0,-1)]
    grid=border+[(x,y) for y in range(1,ny) for x in range(1,nx)]; ids={p:i for i,p in enumerate(grid)}
    vertices=[];uv=[];tri=[]
    def blend(p,joint,previous,width):
        d=joint-previous; d=d/max(np.linalg.norm(d),1); t=np.clip(.5+np.dot(p-joint,d)/(2*width),0,1)
        return t*t*(3-2*t)
    width1=max(3,np.linalg.norm(pts[1]-pts[0])*.14);width2=max(2,np.linalg.norm(pts[2]-pts[1])*(.08 if part['chain']=='leg' else .12))
    for col,row in grid:
        u,v=col/nx,row/ny;uv.extend([u,v]);p=np.array([x0+u*(x1-x0),y0+v*(y1-y0)])
        sample=inverse@(p-transform['offset']) if transform else p
        a=blend(sample,pts[1],pts[0],width1);b=blend(sample,pts[2],pts[1],width2);weights=[1-a,a*(1-b),a*b]
        active=[(bone,w) for bone,w in zip(bind,weights) if w>1e-7]
        if part['chain']=='leg' and 'hip' in names:
            # Waist overlap belongs to the pelvis. Blend into thigh motion below
            # the reviewed hip pin, preserving every setup world position.
            axis=pts[1]-pts[0];length2=max(float(axis@axis),1.)
            t=float(np.clip(((sample-pts[0])@axis)/length2/.28,0,1))
            thigh=t*t*(3-2*t);active=[(bone,w*thigh) for bone,w in active if w*thigh>1e-7]
            if 1-thigh>1e-7:active.append(('hip',1-thigh))
        vertices.append(len(active))
        q=np.array([(p[0]-origin[0])*scale,(H-p[1])*scale])
        for bone,w in active:
            bp,angle=absposes[bone];delta=q-bp;r=math.radians(-angle);c,s=math.cos(r),math.sin(r)
            vertices.extend([names.index(bone),float(c*delta[0]-s*delta[1]),float(s*delta[0]+c*delta[1]),float(w)])
    for y in range(ny):
        for x in range(nx):
            a,b,c,d=[ids[p] for p in ((x,y),(x+1,y),(x,y+1),(x+1,y+1))];tri.extend([a,c,b,b,c,d])
    return {'type':'mesh','path':part['name'],'uvs':uv,'triangles':tri,'vertices':vertices,'hull':len(border),
            'width':part['image'].width*scale,'height':part['image'].height*scale}


def costume_mesh(part,bones,absposes,scale,origin,H,landmarks,W):
    """One continuous costume weighted along the actual placed hip-to-neck axis."""
    names=[bone['name'] for bone in bones];bind=('hip','torso')
    if any(name not in names or name not in absposes for name in bind):
        raise UserError('完整衣装须有 hip 与 torso 骨骼')
    hip=np.array(landmarks['hip'])*[W,H];neck=np.array(landmarks['neck'])*[W,H];axis=neck-hip
    length2=float(axis@axis)
    if length2<1:raise UserError('完整衣装的腰颈关节太近')
    x0,y0,x1,y1=part['box'];nx,ny=12,24
    # Boundary vertices come first for Spine 4.2's hull contract. All rows stay
    # in one triangulated surface, including the waist and hanging costume hem.
    border=[(x,0) for x in range(nx+1)]+[(nx,y) for y in range(1,ny+1)]+[(x,ny) for x in range(nx-1,-1,-1)]+[(0,y) for y in range(ny-1,0,-1)]
    grid=border+[(x,y) for y in range(1,ny) for x in range(1,nx)];ids={p:i for i,p in enumerate(grid)}
    vertices=[];uv=[];tri=[]
    for col,row in grid:
        u,v=col/nx,row/ny;uv.extend([u,v]);p=np.array([x0+u*(x1-x0),y0+v*(y1-y0)])
        # p is already in the final adjusted box. Do not inverse-transform it
        # or sample the source atlas: neither describes the setup skeleton.
        t=float(np.clip((p-hip)@axis/length2,0,1));upper=t*t*(3-2*t)
        active=[(bone,weight) for bone,weight in zip(bind,(1-upper,upper)) if weight>0]
        vertices.append(len(active));world=np.array([(p[0]-origin[0])*scale,(H-p[1])*scale])
        for bone,weight in active:
            position,angle=absposes[bone];delta=world-position;r=math.radians(-angle);c,s=math.cos(r),math.sin(r)
            vertices.extend([names.index(bone),float(c*delta[0]-s*delta[1]),float(s*delta[0]+c*delta[1]),float(weight)])
    for y in range(ny):
        for x in range(nx):
            a,b,c,d=[ids[p] for p in ((x,y),(x+1,y),(x,y+1),(x+1,y+1))];tri.extend([a,c,b,b,c,d])
    return {'type':'mesh','path':part['name'],'uvs':uv,'triangles':tri,'vertices':vertices,'hull':len(border),
            'width':part['image'].width*scale,'height':part['image'].height*scale}
