"""Complete, independently generated sprites aligned by their own joint pins."""
import math
import numpy as np
from PIL import Image,ImageOps,ImageDraw
from .core import UserError

PINS={
 'head':([.5,.95],[.5,.43]),'torso':([.5,1.10],[.5,.09]),
 'pelvis':([.25,.55],[.75,.55]),'back':([.34,.5],[.34,1.0]),
 'upper-arm':([.5,.18],[.5,.91]),'lower-arm':([.5,.16],[.5,.92]),
 'hand':([.45,.12],[.45,.65]),'upper-leg':([.5,.14],[.5,.91]),
 'lower-leg':([.5,.18],[.5,.90]),'foot':([.30,.18],[.86,.77]),
}

def kind(name):return name[2:] if name.startswith(('l-','r-')) else name

def divider_edges(image,axis,count):
    rgb=np.array(image.convert('RGB'));dark=np.max(rgb,axis=2)<75
    lines=np.flatnonzero(dark.mean(axis=0 if axis==0 else 1)>.88)
    size=image.width if axis==0 else image.height
    groups=[]
    for i in lines:
        if not groups or i>groups[-1][-1]+1:groups.append([int(i)])
        else:groups[-1].append(int(i))
    inner=[round(sum(g)/len(g)) for g in groups if size*.02<sum(g)/len(g)<size*.98]
    if inner:
        edges=[0]+inner+[size]
        if len(edges)-1>=count and (len(edges)-1)%count==0 and max(np.diff(edges))/min(np.diff(edges))<1.15:return edges
    return [round(size*i/count) for i in range(count+1)]

def split_sheet(image,job,cut_background):
    names=job['parts'];columns=job['columns'];rows=job['rows']
    xs=divider_edges(image,0,columns);ys=divider_edges(image,1,rows)
    result={};layout={'columns':len(xs)-1,'rows':len(ys)-1,'selected_columns':columns}
    for i,name in enumerate(names):
        row,col=divmod(i,columns);margin=max(4,round(min(np.diff(xs).min(),np.diff(ys).min())*.025))
        box=(xs[col]+margin,ys[row]+margin,xs[col+1]-margin,ys[row+1]-margin)
        cell=image.crop(box).convert('RGBA')
        # The first arms sheet includes a repeated proximal shaft above each
        # actual forearm. Keep the lower forearm guard and its elbow pivot.
        trim=job.get('trim_top',{}).get(name,0)
        if trim:cell=cell.crop((0,round(cell.height*trim),cell.width,cell.height))
        sprite=cut_background(cell,32);bbox=sprite.getbbox()
        if not bbox or (bbox[2]-bbox[0])<12 or (bbox[3]-bbox[1])<12:raise UserError('完整部件图中有空白格，请检查生成结果')
        sprite=sprite.crop(bbox)
        if name=='r-foot':
            # Normalize paired boots to the source's same walking direction.
            # The shaft occupies the heel side in the upper half of the sprite.
            alpha=np.array(sprite)[:,:,3];upper=alpha[:max(1,sprite.height//3)]>0
            _,xx=np.nonzero(upper)
            if len(xx) and xx.mean()>sprite.width*.5:sprite=ImageOps.mirror(sprite)
        result[name]=sprite
    return result,layout

def aligned_part(name,image,record,landmarks,size,segments):
    W,H=size;start,end=next((a,b) for n,a,b in segments(landmarks) if n==name)
    if name=='head':start,end='neck','head'
    if name=='torso':start,end='hip','neck'
    A=np.array(landmarks[start])*[W,H];B=np.array(landmarks[end])*[W,H]
    if name=='back':B=A+np.array([0,np.linalg.norm((np.array(landmarks['hip'])-landmarks['neck'])*[W,H])*.5])
    source_space='source_box' in record
    if source_space:
        box=np.rint(np.array(record['source_box'])*[W,H,W,H]).astype(int).tolist()
        a=np.array(record['source_start'])*[W,H]-box[:2];b=np.array(record['source_end'])*[W,H]-box[:2]
        if np.allclose(A,a+box[:2],atol=1e-7) and np.allclose(B,b+box[:2],atol=1e-7):
            return {'name':name,'box':box,'image':image.copy(),'start':start,'end':end,'pixel_count':int((np.array(image)[:,:,3]>0).sum()),'complete':True,'preserve_mesh':not record.get('rigid',False),'placement':{'matrix':[[1,0],[0,1]],'offset':box[:2]}}
    else:
        a=np.array(record.get('start_pin',PINS[kind(name)][0]))*[image.width,image.height]
        b=np.array(record.get('end_pin',PINS[kind(name)][1]))*[image.width,image.height]
    if np.linalg.norm(b-a)<1 or np.linalg.norm(B-A)<1:raise UserError('完整部件关节锚点太近')
    scale=np.linalg.norm(B-A)/np.linalg.norm(b-a);angle=math.atan2(*(B-A)[::-1])-math.atan2(*(b-a)[::-1]);c,s=math.cos(angle),math.sin(angle)
    widen=record.get('width_scale',1 if source_space else 1.35 if kind(name)=='upper-arm' else 1.25 if kind(name)=='lower-leg' else 1)
    matrix=np.array([[c,-s],[s,c]])@np.diag([scale*widen,scale])
    # Shoes use the same similarity transform as other rigid artwork.
    # Per-axis division magnifies near-horizontal pins and can even collapse
    # or mirror a shoe. Sole contact is solved from pixels during animation.
    offset=A-matrix@a
    corners=np.array([[0,0],[image.width,0],[0,image.height],[image.width,image.height]])@matrix.T+offset
    lo=np.floor(corners.min(0)).astype(int);hi=np.ceil(corners.max(0)).astype(int)
    inverse=np.linalg.inv(matrix);shift=inverse@(lo-offset)
    transformed=image.transform(tuple(hi-lo),Image.Transform.AFFINE,(*inverse[0],shift[0],*inverse[1],shift[1]),resample=Image.Resampling.BICUBIC)
    crop=transformed.getbbox()
    if not crop:raise UserError('完整部件没有可见像素')
    box=[int(lo[0]+crop[0]),int(lo[1]+crop[1]),int(lo[0]+crop[2]),int(lo[1]+crop[3])]
    return {'name':name,'box':box,'image':transformed.crop(crop),'start':start,'end':end,'pixel_count':int((np.array(transformed)[:,:,3]>0).sum()),'complete':True,'preserve_mesh':source_space and not record.get('rigid',False),'placement':{'matrix':matrix.tolist(),'offset':offset.tolist()}}

def region_attachment(part,absposes,bone,scale,origin,H):
    x0,y0,x1,y1=part['box'];world=np.array([((x0+x1)/2-origin[0])*scale,(H-(y0+y1)/2)*scale])
    p,angle=absposes[bone];v=world-p;r=math.radians(-angle);c,s=math.cos(r),math.sin(r)
    return {'type':'region','path':part['name'],'x':float(c*v[0]-s*v[1]),'y':float(s*v[0]+c*v[1]),'rotation':-angle,'width':part['image'].width*scale,'height':part['image'].height*scale}

def preserve_visible(part,generated,image,outline):
    """Inpaint only unavailable part pixels; original visible pixels are exact."""
    canvas=Image.new('RGBA',image.size);canvas.paste(part['image'],part['box'][:2]);original=np.array(canvas);W,H=image.size
    polygon=Image.new('L',image.size)
    if outline:ImageDraw.Draw(polygon).polygon([(x*W,y*H) for x,y in outline],fill=255)
    else:ImageDraw.Draw(polygon).rectangle(part['box'],fill=255)
    bbox=polygon.getbbox();art=Image.new('RGBA',image.size);art.paste(generated.resize((bbox[2]-bbox[0],bbox[3]-bbox[1]),Image.Resampling.LANCZOS),bbox[:2]);new=np.array(art)
    visible=original[:,:,3]>0;hidden=(np.array(polygon)>0)&(np.array(image)[:,:,3]>0)&~visible&(new[:,:,3]>0)
    combined=original.copy();combined[hidden]=new[hidden]
    assert np.array_equal(combined[visible],original[visible])
    result=Image.fromarray(combined,'RGBA');box=result.getbbox()
    return result.crop(box),box,int(visible.sum()),int(hidden.sum())
