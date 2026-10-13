"""Independent, reversible assembly corrections in source-image coordinates.

No source pixels are changed at zero correction. Diagnostics flag review points;
they do not infer anatomy or delete disconnected costume details.
"""
import math
import numpy as np
from PIL import Image
from .core import UserError
from .rig import number

DEFAULT={'dx':0.,'dy':0.,'rotation':0.,'scale':1.}

def checked_review(value,names,size,default_order):
    if not isinstance(value,dict):raise UserError('组装校准格式无效')
    adjustments=value.get('adjustments',{})
    if not isinstance(adjustments,dict) or set(adjustments)-set(names):raise UserError('组装校准包含未知部件')
    result={};limit=max(size)/2
    for name,record in adjustments.items():
        if not isinstance(record,dict) or set(record)-set(DEFAULT):raise UserError('部件校准字段无效')
        result[name]={'dx':number(record.get('dx',0),-limit,limit),'dy':number(record.get('dy',0),-limit,limit),
                      'rotation':number(record.get('rotation',0),-90,90),'scale':number(record.get('scale',1),.5,2)}
    order=value.get('draw_order',[n for n in default_order if n in names])
    if not isinstance(order,list) or any(not isinstance(n,str) for n in order) or len(order)!=len(names) or set(order)!=set(names):
        raise UserError('绘制顺序必须包含每个部件一次，顺序从后到前')
    shoe_cuffs=value.get('shoe_cuffs',False)
    if not isinstance(shoe_cuffs,bool):raise UserError('鞋口接合选项无效')
    if shoe_cuffs:
        from .ankle_join import joined_order
        order=joined_order(order)
    return {'adjustments':result,'draw_order':order,**({'shoe_cuffs':True} if shoe_cuffs else {})}

def apply_adjustment(part,record,pivot):
    if all(record.get(k,v)==v for k,v in DEFAULT.items()):return part
    image=part['image'];x0,y0,x1,y1=part['box'];pivot=np.array(pivot);angle=math.radians(record.get('rotation',0))
    c,s=math.cos(angle),math.sin(angle);matrix=np.array([[c,-s],[s,c]])*record.get('scale',1)
    offset=pivot+np.array([record.get('dx',0),record.get('dy',0)])-matrix@pivot
    corners=np.array([[x0,y0],[x1,y0],[x0,y1],[x1,y1]])@matrix.T+offset
    lo=np.floor(corners.min(0)).astype(int);hi=np.ceil(corners.max(0)).astype(int)
    inverse=np.linalg.inv(matrix);shift=inverse@(lo-offset)-[x0,y0]
    transformed=image.transform(tuple(hi-lo),Image.Transform.AFFINE,(*inverse[0],shift[0],*inverse[1],shift[1]),resample=Image.Resampling.BICUBIC)
    crop=transformed.getbbox()
    if not crop:raise UserError('部件校准后没有可见像素')
    result={**part,'image':transformed.crop(crop),'box':[int(lo[0]+crop[0]),int(lo[1]+crop[1]),int(lo[0]+crop[2]),int(lo[1]+crop[3])],
            'review_transform':{'matrix':matrix.tolist(),'offset':offset.tolist()}}
    return result

def component_stats(image):
    """8-connected runs: linear in scanline runs, not repeated full-image scans."""
    alpha=np.array(image)[:,:,3]>16;parents=[];areas=[];boxes=[];previous=[]
    def root(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    def join(a,b):
        a,b=root(a),root(b)
        if a!=b:parents[b]=a
    for y,row in enumerate(alpha):
        edges=np.flatnonzero(np.diff(np.r_[False,row,False]));runs=[];j=0
        for x0,x1 in edges.reshape(-1,2):
            i=len(parents);parents.append(i);areas.append(int(x1-x0));boxes.append([int(x0),y,int(x1),y+1]);runs.append((x0,x1,i))
            while j<len(previous) and previous[j][1]<x0:j+=1
            k=j
            while k<len(previous) and previous[k][0]<=x1:join(i,previous[k][2]);k+=1
        previous=runs
    groups={}
    for i,area in enumerate(areas):
        r=root(i);b=boxes[i]
        if r not in groups:groups[r]={'area':0,'box':b.copy()}
        item=groups[r];item['area']+=area;old=item['box'];item['box']=[min(old[0],b[0]),min(old[1],b[1]),max(old[2],b[2]),max(old[3],b[3])]
    total=int(alpha.sum());threshold=max(16,total*.01)
    significant=sorted((g for g in groups.values() if g['area']>=threshold),key=lambda g:-g['area'])
    return {'visible_pixels':total,'component_count':len(significant),'components':significant[:8],
            'minor_pixels':sum(g['area'] for g in groups.values() if g['area']<threshold)}

def compose(parts,size,order):
    canvas=Image.new('RGBA',size);by={p['name']:p for p in parts}
    for name in order:
        p=by[name];canvas.alpha_composite(p['image'],tuple(p['box'][:2]))
    return canvas
