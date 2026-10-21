"""Single-image cutout rigging and procedural walk, independently implemented.

An editable humanoid guide, not an arbitrary-pose recognition model. The original
image is immutable; segmentation, landmarks and builds are revisioned together.
"""
from __future__ import annotations
import base64,copy,io,json,math,re,threading,time,uuid,zipfile
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFilter
from .core import UserError,atomic_json,png_bytes,read_image,sha,Project
from .rig import RigProject,pack,number,validate_state
from .part_layers import aligned_part,region_attachment,split_sheet,PINS,kind,preserve_visible
from .part_review import checked_review,apply_adjustment,component_stats,compose,DEFAULT
from .reviewed_parts import extract_parts,aligned_chain,chain_mesh,costume_mesh,CHAINS
from .gait import walk_phase,sole_target,solve_two_bone,spine_bezier_timeline
from .foot_contact import sole_profile
from .assembly_pipeline import atomic_assembly

JOINTS={'head':'头中心','neck':'颈部','hip':'腰部','l-shoulder':'左侧肩','l-elbow':'左侧肘','l-wrist':'左侧腕','l-hand':'左侧手尖',
        'r-shoulder':'右侧肩','r-elbow':'右侧肘','r-wrist':'右侧腕','r-hand':'右侧手尖',
        'l-hip':'左侧髋','l-knee':'左侧膝','l-ankle':'左侧踝','l-toe':'左侧脚尖',
        'r-hip':'右侧髋','r-knee':'右侧膝','r-ankle':'右侧踝','r-toe':'右侧脚尖'}
LABELS={'head':'头','torso':'身体','pelvis':'腰胯','back':'背部',**{f'{s}-{p}':f'{label}{part}' for s,label in [('l','左侧'),('r','右侧')] for p,part in [('upper-arm','上臂'),('lower-arm','前臂'),('hand','手'),('upper-leg','大腿'),('lower-leg','小腿'),('foot','脚')]}}

def cut_background(image,tolerance=32,holes=()):
    """Remove only border-connected flat background; preserve interior whites."""
    image=image.convert('RGBA');arr=np.array(image);h,w=arr.shape[:2]
    # Existing transparency is authoritative. Never erase white costume details.
    if np.any(arr[:,:,3]<250):return image
    samples=np.concatenate([arr[:max(2,h//30),:max(2,w//30),:3].reshape(-1,3),arr[:max(2,h//30),-max(2,w//30):,:3].reshape(-1,3),arr[-max(2,h//30):,:max(2,w//30),:3].reshape(-1,3),arr[-max(2,h//30):,-max(2,w//30):,:3].reshape(-1,3)])
    bg=np.median(samples,axis=0)
    candidate=np.max(np.abs(arr[:,:,:3].astype(float)-bg),axis=2)<=tolerance
    mask=Image.fromarray(np.where(candidate,0,255).astype('uint8'),'L').copy()
    # PIL's iterative flood fill handles connected regions without a large queue.
    pixels=mask.load()
    for x,y in [(x,0) for x in range(w)]+[(x,h-1) for x in range(w)]+[(0,y) for y in range(h)]+[(w-1,y) for y in range(h)]:
        if pixels[x,y]==0:ImageDraw.floodfill(mask,(x,y),128)
    for x,y in holes:
        x,y=round(x),round(y)
        if 0<=x<w and 0<=y<h and pixels[x,y]==0:ImageDraw.floodfill(mask,(x,y),128)
    removed=np.array(mask)==128;arr[removed,3]=0;arr[removed,:3]=0
    result=Image.fromarray(arr,'RGBA')
    if not result.getbbox():raise UserError('没有找到角色，请调低去背景容差，或上传透明 PNG')
    return result

def default_landmarks(image):
    box=image.getbbox()
    if not box:raise UserError('图片没有可见角色')
    x0,y0,x1,y1=box;w=x1-x0;h=y1-y0;W,H=image.size
    points={'head':(.52,.14),'neck':(.52,.29),'hip':(.52,.54),
            'l-shoulder':(.31,.33),'l-elbow':(.17,.47),'l-wrist':(.15,.61),'l-hand':(.15,.69),
            'r-shoulder':(.70,.34),'r-elbow':(.78,.48),'r-wrist':(.88,.61),'r-hand':(.91,.68),
            'l-hip':(.40,.56),'l-knee':(.39,.74),'l-ankle':(.36,.89),'l-toe':(.54,.98),
            'r-hip':(.63,.57),'r-knee':(.65,.74),'r-ankle':(.64,.89),'r-toe':(.86,.97)}
    return {n:[round((x0+x*w)/W,6),round((y0+y*h)/H,6)] for n,(x,y) in points.items()}

def checked_landmarks(value):
    if not isinstance(value,dict) or set(value)-set(JOINTS)-{'back'}:raise UserError('关节点名称无效')
    if set(JOINTS)-set(value):raise UserError('请完整设置头部、腰部及四肢关节点')
    result={}
    for name,point in value.items():
        if not isinstance(point,list) or len(point)!=2:raise UserError('关节点坐标无效')
        result[name]=[number(v,0,1) for v in point]
    return result

def checked_settings(value):
    return {'background':number(value.get('background',32),0,150),'period':number(value.get('period',1),.5,3),
            'stride':number(value.get('stride',.22),.04,.5),'lift':number(value.get('lift',.035),.02,.25),
            'foot_scale':number(value.get('foot_scale',.9),.65,1.2),
            'facing':value.get('facing','right') if value.get('facing','right') in ('right','left') else 'right',
            'clear_gaps':value.get('clear_gaps',True) is not False,
            'background_seeds':[[number(v,0,1) for v in p] for p in value.get('background_seeds',[]) if isinstance(p,list) and len(p)==2][:32]}


def guide_cutout(image,landmarks,masks,settings):
    """Seed enclosed background between separated legs, outside drawn parts."""
    W,H=image.size;holes=[(p[0]*W,p[1]*H) for p in settings.get('background_seeds',[])]
    if landmarks and settings.get('clear_gaps',True):
        outlines=Image.new('L',image.size);draw=ImageDraw.Draw(outlines)
        for points in (masks or {}).values():draw.polygon([(p[0]*W,p[1]*H) for p in points],fill=255)
        for joint in ('knee','ankle'):
            a=np.array(landmarks['l-'+joint])*[W,H];b=np.array(landmarks['r-'+joint])*[W,H];p=(a+b)/2
            # Do not remove white costume details when the legs overlap or a
            # user-drawn part explicitly covers the proposed background seed.
            if abs(a[0]-b[0])>H*.09 and not outlines.getpixel(tuple(np.clip(np.rint(p).astype(int),[0,0],[W-1,H-1]))):holes.append(p)
        # With a complete set of user outlines, enclosed near-background areas
        # outside every part are also explicit gaps (e.g. under an arm).
        expected={name for name,_,_ in segments(landmarks)}
        if expected.issubset(masks or {}):
            rgba=np.array(image.convert('RGBA'));background=np.median(np.concatenate([rgba[0,:,:3],rgba[-1,:,:3],rgba[:,0,:3],rgba[:,-1,:3]]),axis=0)
            candidate=np.max(np.abs(rgba[:,:,:3].astype(float)-background),axis=2)<=settings['background']
            points=np.argwhere(candidate&(np.array(outlines)==0))
            holes.extend((x,y) for y,x in points[::3])
    return cut_background(image,settings['background'],holes)

def segments(points):
    result=[('back','back','back')] if 'back' in points else []
    result += [('head','head','neck'),('torso','neck','hip'),('pelvis','l-hip','r-hip')]
    for s in ('l','r'):
        for part,a,b in [('upper-arm','shoulder','elbow'),('lower-arm','elbow','wrist'),('hand','wrist','hand'),('upper-leg','hip','knee'),('lower-leg','knee','ankle'),('foot','ankle','toe')]:
            result.append((s+'-'+part,s+'-'+a,s+'-'+b))
    return result

def checked_masks(value):
    if not isinstance(value,dict) or set(value)-set(LABELS):raise UserError('部件边界名称无效')
    result={}
    for name,points in value.items():
        if not isinstance(points,list) or not 3<=len(points)<=100:raise UserError('每个部件边界须有 3–100 个点')
        result[name]=[]
        for p in points:
            if not isinstance(p,list) or len(p)!=2:raise UserError('部件边界坐标无效')
            result[name].append([number(v,0,1) for v in p])
    return result

def segmentation_owner(image,landmarks,masks=None):
    W,H=image.size;points={n:np.array([p[0]*W,p[1]*H]) for n,p in landmarks.items()};segs=segments(points)
    yy,xx=np.mgrid[:H,:W];lengths={};best=np.full((H,W),np.inf);owner=np.zeros((H,W),dtype=np.int16)
    for name,start,end in segs:
        a=points[start];b=points[end];d=b-a;length=float(np.linalg.norm(d));lengths[name]=length
        if length<4 and name!='back':raise UserError(LABELS[name]+'的两个关节太近，请移动关节点')
        if name=='back':
            # A separate optional backpack seed stays attached to the torso.
            a=a-np.array([0,H*.09]);b=b+np.array([0,H*.09]);d=b-a
        t=np.clip(((xx-a[0])*d[0]+(yy-a[1])*d[1])/max(float(d@d),1),0,1)
        score=(xx-a[0]-t*d[0])**2+(yy-a[1]-t*d[1])**2;near=score<best;owner[near]=len(lengths)-1;best[near]=score[near]
    # User outlines take precedence in anatomical draw order. A polygon need
    # only delimit neighboring parts: the original alpha still cuts its edge.
    override_order=['back','pelvis','torso','head','r-upper-arm','r-lower-arm','r-hand','r-upper-leg','r-lower-leg','r-foot','l-upper-arm','l-lower-arm','l-hand','l-upper-leg','l-lower-leg','l-foot']
    for name in override_order:
        if name not in (masks or {}):continue
        index=next((i for i,(n,_,_) in enumerate(segs) if n==name),None)
        if index is None:continue
        mask=Image.new('L',(W,H));ImageDraw.Draw(mask).polygon([(p[0]*W,p[1]*H) for p in masks[name]],fill=255);owner[np.array(mask)>0]=index
    return owner,segs,lengths,points


def split_image(image,landmarks,masks=None,fill_back=True):
    W,H=image.size;owner,segs,lengths,points=segmentation_owner(image,landmarks,masks)
    yy,xx=np.mgrid[:H,:W];arr=np.array(image);foreground=arr[:,:,3]>0;parts=[]
    for i,(name,start,end) in enumerate(segs):
        mask=(owner==i)&foreground
        near_owned=np.array(Image.fromarray((mask*255).astype('uint8')).filter(ImageFilter.MaxFilter(5)))>0
        # Overlap only a small cap at a real shared joint. A broad circular
        # overlap copies fingers, clothing and neighboring silhouettes into
        # otherwise independent parts.
        joint_area=np.zeros((H,W),dtype=bool)
        for endpoint in (start,end):
            adjacent=[j for j,(_,a,b) in enumerate(segs) if j!=i and endpoint in (a,b)]
            if not adjacent:continue
            cap=.012 if name=='head' or any(segs[j][0]=='head' for j in adjacent) or name.endswith('-foot') or any(segs[j][0].endswith('-foot') for j in adjacent) else .04
            radius=min([lengths[name]]+[lengths[segs[j][0]] for j in adjacent])*.38
            radius=max(4,min(radius,H*cap));p=points[endpoint]
            joint=(xx-p[0])**2+(yy-p[1])**2<=radius**2
            joint_area |= joint & np.isin(owner,[i]+adjacent)
            mask |= joint_area&foreground
        # A two-pixel overlap removes alpha-filter cracks at cut boundaries.
        # Do not expand into a different part merely because its pixels touch.
        mask=(np.array(Image.fromarray((mask*255).astype('uint8')).filter(ImageFilter.MaxFilter(5)))>0)&foreground&((owner==i)|joint_area)
        rgba=arr.copy();rgba[~mask]=0
        if fill_back and name=='back' and name in (masks or {}):
            # A backpack is a rear plate with an explicitly drawn silhouette.
            # Complete its occluded interior using only its own visible art,
            # never the elbow/torso pixels occupying that location in the photo.
            outline=Image.new('L',(W,H));ImageDraw.Draw(outline).polygon([(p[0]*W,p[1]*H) for p in masks[name]],fill=255)
            later=[j for j,(n,_,_) in enumerate(segs) if PART_ORDER.index(n)>PART_ORDER.index(name)]
            hidden=(np.array(outline)>0)&foreground&np.isin(owner,later)&~mask
            fill_occlusion(rgba,arr,(owner==i)&foreground,hidden,limit=False)
        im=Image.fromarray(rgba,'RGBA');box=im.getbbox()
        if not box:raise UserError(LABELS[name]+'没有分到像素，请把关节移到对应身体部件上')
        x0,y0,x1,y1=box;parts.append({'name':name,'label':LABELS[name],'box':list(box),'image':im.crop(box),'start':start,'end':end,'pixel_count':int(mask.sum())})
    return parts

def norm_angle(value):return (value+180)%360-180

MESH_STEP=16
PART_ORDER=['back','r-upper-arm','r-lower-arm','r-hand','r-upper-leg','r-lower-leg','r-foot','pelvis','torso','head','l-upper-leg','l-lower-leg','l-foot','l-upper-arm','l-lower-arm','l-hand']
# Keep source segmentation ownership unchanged; presentation has its own order.
DRAW_ORDER=['back','r-upper-arm','r-lower-arm','r-hand','r-upper-leg','r-foot','r-lower-leg','pelvis','torso','head','l-upper-leg','l-foot','l-lower-leg','l-upper-arm','l-lower-arm','l-hand']
COSTUME_DRAW_ORDER=['back','r-upper-arm','r-lower-arm','r-hand','r-upper-leg','r-foot','r-lower-leg','l-upper-leg','l-foot','l-lower-leg','torso','l-upper-arm','l-lower-arm','l-hand','head']


def fill_occlusion(target,source,visible,hidden,limit=True):
    """Extend only a user-outlined rear layer under an overlapping front layer.

    Clone interior material along source rows; the front layer covers it in the
    original pose. No pixels outside the original silhouette are synthesized.
    """
    # An outline may include a large front limb because its hidden rear edge
    # cannot be seen in the source. Only complete a narrow band next to real
    # rear-layer material; cloning the entire polygon creates phantom limbs.
    if limit:
        radius=max(2,round(source.shape[0]*.008))
        nearby=np.array(Image.fromarray((visible*255).astype('uint8')).filter(ImageFilter.MaxFilter(radius*2+1)))>0
        hidden=hidden&nearby
    if not hidden.any():return
    interior=np.array(Image.fromarray((visible*255).astype('uint8')).filter(ImageFilter.MinFilter(7)))>0
    rows=np.flatnonzero(interior.any(axis=1))
    if not len(rows):interior=visible;rows=np.flatnonzero(visible.any(axis=1))
    if not len(rows):return
    for row in np.flatnonzero(hidden.any(axis=1)):
        valid_row=row if interior[row].any() else rows[np.argmin(np.abs(rows-row))]
        columns=np.flatnonzero(interior[valid_row]);xs=np.flatnonzero(hidden[row]);pos=np.searchsorted(columns,xs);left=columns[np.clip(pos-1,0,len(columns)-1)];right=columns[np.clip(pos,0,len(columns)-1)]
        nearest=np.where(abs(xs-left)<=abs(xs-right),left,right);target[row,xs]=source[valid_row,nearest]


def skin_field(image,landmarks,masks,bones):
    """A single source-space weight field for every part, including overlaps."""
    owner,segs,_,_=segmentation_owner(image,landmarks,masks);alpha=np.array(image)[:,:,3]/255
    names=[b['name'] for b in bones];mapped={n:('hip' if n=='pelvis' else 'torso' if n=='back' else n) for n,_,_ in segs}
    fields=[];indices=[];radius=max(4,image.height*.018)
    for name in names:
        labels=[i for i,(n,_,_) in enumerate(segs) if mapped[n]==name]
        if not labels:continue
        mask=np.isin(owner,labels)*alpha
        field=np.array(Image.fromarray(np.rint(mask*255).astype('uint8')).filter(ImageFilter.GaussianBlur(radius)),dtype=np.float32)/255
        fields.append(field);indices.append(names.index(name))
    return np.stack(fields),indices,owner,segs,mapped


def skin_family(name):
    if name.startswith(('l-','r-')):
        side=name[:1];kind='arm' if name.endswith(('upper-arm','lower-arm','hand')) else 'leg'
        return side+'-'+kind
    return 'body'


def family_weights(values,indices,names,family,x,y,landmarks,W,H,rigid_feet=False):
    allowed={'body':['hip','torso','head']}
    for side in ('l','r'):
        allowed[side+'-arm']=[side+'-'+p for p in ('upper-arm','lower-arm','hand')]
        allowed[side+'-leg']=[side+'-'+p for p in ('upper-leg','lower-leg','foot')]
    result=values.copy()
    for i,index in enumerate(indices):
        if names[index] not in allowed[family]:result[i]=0
    # A limb follows its parent only at its anatomical shoulder/hip attachment,
    # never where its hand or foot happens to touch another body part.
    if family!='body':
        side=family[:1];arm=family.endswith('arm');root='torso' if arm else 'hip';joint=landmarks[side+('-shoulder' if arm else '-hip')]
        distance=math.hypot(x-joint[0]*W,y-joint[1]*H);radius=H*.03;blend=np.clip((radius-distance)/(radius*.67),0,1)
        if sum(result)>0:result=result/sum(result)*(1-blend)
        result[indices.index(names.index(root))]=blend
    if rigid_feet and family.endswith('-leg'):
        side=family[:1];ankle=landmarks[side+'-ankle'][1]*H
        blend=np.clip((y-(ankle-H*.06))/(H*.03),0,1)
        if sum(result)>0:result=result/sum(result)*(1-blend)
        result[indices.index(names.index(side+'-foot'))]+=blend
    return result


def mesh_attachment(part,bones,absposes,field,scale,origin,H,landmarks,W,rigid_feet=False):
    weights,indices,owner,segs,mapped=field
    x0,y0,x1,y1=part['box'];step=MESH_STEP
    # Identical global grid and diagonal in all attachments. Two triangles from
    # adjacent parts therefore interpolate the exact same source-space map.
    box=[math.floor(x0/step)*step,math.floor(y0/step)*step,math.ceil(x1/step)*step,math.ceil(y1/step)*step]
    image=Image.new('RGBA',(box[2]-box[0],box[3]-box[1]));image.paste(part['image'],(x0-box[0],y0-box[1]));part.update(image=image,box=box)
    xs=list(range(box[0],box[2]+1,step));ys=list(range(box[1],box[3]+1,step));uv=[];vertices=[];tri=[];ny,nx=len(ys),len(xs)
    names=[b['name'] for b in bones]
    family=skin_family(part['name'])
    transform=part.get('review_transform');inverse=np.linalg.inv(transform['matrix']) if transform else None
    for y in ys:
        for x in xs:
            uv.extend([(x-box[0])/(box[2]-box[0]),(y-box[1])/(box[3]-box[1])]);sx,sy=inverse@(np.array([x,y])-transform['offset']) if transform else (x,y)
            px=min(max(round(sx),0),weights.shape[2]-1);py=min(max(round(sy),0),weights.shape[1]-1)
            values=family_weights(weights[:,py,px],indices,names,family,sx,sy,landmarks,W,H,rigid_feet);active=np.argsort(values)[-3:];active=[i for i in active if values[i]>.002]
            if not active:
                candidates=[(i,names[index]) for i,index in enumerate(indices) if skin_family(names[index])==family]
                if family=='body':candidates=[(i,names[index]) for i,index in enumerate(indices) if names[index] in ('hip','torso','head')]
                def distance(item):
                    p,angle=absposes[item[1]];return np.linalg.norm(np.array([(x-origin[0])*scale,(H-y)*scale])-p)
                if part.get('preserve_mesh'):
                    bound='hip' if part['name']=='pelvis' else 'torso' if part['name']=='back' else part['name']
                    active=[indices.index(names.index(bound))]
                else:active=[min(candidates,key=distance)[0]]
                values=values.copy();values[active[0]]=1
            total=sum(values[i] for i in active);vertices.append(len(active))
            point=np.array([(x-origin[0])*scale,(H-y)*scale])
            for i in active:
                index=indices[i];p,angle=absposes[names[index]];v=point-p;r=math.radians(-angle);c,s=math.cos(r),math.sin(r)
                vertices.extend([index,float(c*v[0]-s*v[1]),float(s*v[0]+c*v[1]),float(values[i]/total)])
    for row in range(ny-1):
        for col in range(nx-1):
            a=row*nx+col;tri.extend([a,a+nx,a+1,a+1,a+nx,a+nx+1])
    return {'type':'mesh','path':part['name'],'uvs':uv,'triangles':tri,'vertices':vertices,'width':image.width*scale,'height':image.height*scale}


def make_skeleton(image,landmarks,parts,settings,masks=None,draw_order=None):
    box=image.getbbox();W,H=image.size;scale=420/(box[3]-box[1]);origin=np.array([landmarks['hip'][0]*W,H])
    def world(name):
        x,y=landmarks[name];return np.array([(x*W-origin[0])*scale,(H-y*H)*scale])
    points={n:world(n) for n in landmarks};bones=[];absposes={}
    def bone(name,parent,start,end=None):
        p=points[start] if isinstance(start,str) else np.array(start);q=points[end] if end else p+np.array([1,0])
        angle=math.degrees(math.atan2(*(q-p)[::-1])) if end else 0
        if parent:
            pp,pa=absposes[parent];delta=p-pp;rad=math.radians(-pa);c,s=math.cos(rad),math.sin(rad);local=np.array([c*delta[0]-s*delta[1],s*delta[0]+c*delta[1]])
        else:local=p;pa=0
        entry={'name':name,'x':round(float(local[0]),5),'y':round(float(local[1]),5),'rotation':round(norm_angle(angle-pa),5),'length':round(float(np.linalg.norm(q-p)),5) if end else 0}
        if parent:entry['parent']=parent
        bones.append(entry);absposes[name]=(p,angle)
    bone('root',None,[0,0]);bone('hip','root','hip');bone('torso','hip','hip','neck');bone('head','torso','neck','head')
    for side in ('l','r'):
        bone(side+'-upper-arm','torso',side+'-shoulder',side+'-elbow');bone(side+'-lower-arm',side+'-upper-arm',side+'-elbow',side+'-wrist');bone(side+'-hand',side+'-lower-arm',side+'-wrist',side+'-hand')
        bone(side+'-upper-leg','hip',side+'-hip',side+'-knee');bone(side+'-lower-leg',side+'-upper-leg',side+'-knee',side+'-ankle');bone(side+'-foot',side+'-lower-leg',side+'-ankle',side+'-toe')
    slots=[];atts={};by={p['name']:p for p in parts};field=skin_field(image,landmarks,masks or {},bones)
    # The uppermost limb in the source image remains uppermost while walking.
    order=draw_order or (COSTUME_DRAW_ORDER if any(p.get('group')=='costume' for p in parts) else DRAW_ORDER);rigid_feet=any(p['name'].endswith('-foot') and p.get('complete') and not p.get('preserve_mesh') for p in parts)
    for name in order:
        if name not in by:continue
        part=by[name]
        if part.get('overlay_of'):
            source=part['overlay_of'];slot=next(s for s in slots if s['name']==source)
            att=copy.deepcopy(atts[source][source]);att['path']=name
            slots.append({**slot,'name':name,'attachment':name});atts[name]={name:att}
            continue
        bound='hip' if name=='pelvis' else 'torso' if name=='back' else name
        att=chain_mesh(part,bones,absposes,scale,origin,H) if part.get('chain') else costume_mesh(part,bones,absposes,scale,origin,H,landmarks,W) if part.get('group')=='costume' else region_attachment(part,absposes,bound,scale,origin,H) if part.get('complete') and not part.get('preserve_mesh') else mesh_attachment(part,bones,absposes,field,scale,origin,H,landmarks,W,rigid_feet)
        slots.append({'name':name,'bone':bound,'attachment':name});atts[name]={name:att}
    # Scale footwear uniformly about the ankle; blended chain weights keep
    # the collar continuous. Preserve texture bytes and the leg bone lengths.
    foot_scale=settings.get('foot_scale',.9)
    for bone in bones:
        if bone['name'] in ('l-foot','r-foot'):bone.update(scaleX=foot_scale,scaleY=foot_scale)
    period=settings['period'];frames=256;stance=.6
    times=sorted(set([period*i/frames for i in range(frames+1)]+[period*.1,period*.6]));walk={};idle={}
    bybone={b['name']:b for b in bones};leglengths=[bybone[s+'-upper-leg']['length']+bybone[s+'-lower-leg']['length'] for s in ('l','r')];leg=sum(leglengths)/2
    # Both planted feet must cancel the same scene velocity, even when the
    # visually reviewed left/right chains have different lengths.
    stride=min(leglengths)*settings['stride']
    facing=1 if settings['facing']=='right' else -1
    profiles={};foot_slots={};actual_slots={slot['name'] for slot in slots}
    for side in ('l','r'):
        part=by.get(side+'-foot') or next((p for p in parts if p.get('chain')=='leg' and p['name'].startswith(side+'-')),None)
        profiles[side]=sole_profile(part,np.array(landmarks[side+'-ankle'])*[W,H],absposes[side+'-foot'][1],scale,origin,H)
        if profiles[side]:
            profiles[side]['points']=(np.array(profiles[side]['points'])*foot_scale).tolist()
        if profiles[side] and part['name'] in actual_slots:foot_slots[side]=part['name']
    pixel_profiles=[p for p in profiles.values() if p]
    ground=min(p['setup_ground_y'] for p in pixel_profiles) if len(pixel_profiles)==2 else None
    plans={};side_offsets={};max_hip_offset=float('inf');bob_peak=leg*.006
    # Natural walking is a low-clearance motion, not a high-knee step.
    requested_lift=leg*settings['lift'];lift=min(requested_lift,leg*.04)
    def planned(side,t):
        phase=0 if side=='l' else .5
        sample=walk_phase(t/period+phase,stance)
        profile=profiles[side];ankle=points[side+'-ankle'].copy();basefoot=absposes[side+'-foot'][1]
        # A posed source often puts both ankles behind the hips. Center the
        # step under its own hip instead of turning that lean into a crouch.
        ankle[0]=points[side+'-hip'][0]
        angle=(profile['flat_angle'] if profile else basefoot)+sample['pitch_deg']*facing
        if profile:
            target=sole_target(ankle,profile['points'],angle,sample,stride,lift,facing,ground if ground is not None else profile['setup_ground_y'])['ankle']
        else:
            target=ankle+np.array([sample['dx']*stride*facing,sample['clearance']*lift])
        return sample,target,angle
    for side,phase in [('l',0),('r',.5)]:
        upper=side+'-upper-leg';lower=side+'-lower-leg';foot=side+'-foot'
        L1,L2=bybone[upper]['length'],bybone[lower]['length'];base_hip=points[side+'-hip'];side_offsets[side]=float('inf')
        for t in times:
            _,target,_=planned(side,t)
            # Keep the leg below full extension for every sampled target,
            # including Hermite swing overshoot. Allow a crouched source hip
            # to rise: an initial cap of zero silently preserved deep flexion.
            reach=(L1+L2)*.995;dx=target[0]-base_hip[0]
            allowed=math.sqrt(max(0,reach*reach-dx*dx))
            side_offsets[side]=min(side_offsets[side],target[1]+allowed-base_hip[1]-bob_peak)
            max_hip_offset=min(max_hip_offset,side_offsets[side])
        plans[side]=(L1,L2,base_hip)
    # Small perspective asymmetry is absorbed beneath the reviewed waist overlap.
    hip_correction={s:min(leg*.08 if any(p.get('canvas_anchors') and p.get('group')=='costume' for p in parts) else 0,max(0,v-max_hip_offset)) for s,v in side_offsets.items()}
    def bob(t):return max_hip_offset+leg*.003*(1-math.cos(4*math.pi*t/period))
    walk['hip']={'translate':spine_bezier_timeline(lambda t:{'x':0,'y':bob(t)},times,('x','y'))}
    clamped=0
    leg_cache={}
    def leg_pose(side,t):
        key=(side,t)
        if key not in leg_cache:
            L1,L2,base_hip=plans[side];_,target,angle=planned(side,t)
            solved=solve_two_bone(base_hip+np.array([0,bob(t)+hip_correction[side]]),target,L1,L2,bend=-facing)
            a=solved['upper_angle'];b=solved['lower_angle']
            upper=side+'-upper-leg';lower=side+'-lower-leg';foot=side+'-foot'
            base1=absposes[upper][1];base2=absposes[lower][1]
            leg_cache[key]=([norm_angle(a-base1),norm_angle(b-a-(base2-base1)),norm_angle(angle-b-bybone[foot]['rotation'])],solved['clamped'],abs(solved['bend_angle']))
        return leg_cache[key]
    for side,phase in [('l',0),('r',.5)]:
        names=[side+'-'+part for part in ('upper-leg','lower-leg','foot')]
        clamped+=sum(int(leg_pose(side,t)[1]) for t in times)
        for index,name in enumerate(names):
            walk[name]={'rotate':spine_bezier_timeline(lambda t,s=side,i=index:{'value':leg_pose(s,t)[0][i]},times,angular=True)}
        walk[side+'-upper-leg']['translate']=[{'time':0,'x':0,'y':hip_correction[side]}]
        # Use the actual shoulder-to-hand vertical direction, not the bone's
        # local axis, so projected hand movement opposes the same-side foot.
        hand_delta=points[side+'-hand'][1]-points[side+'-shoulder'][1]
        arm_direction=facing*(-1 if hand_delta<=0 else 1)
        response=2*math.radians(12*abs(hand_delta)+5*abs(points[side+'-hand'][1]-points[side+'-elbow'][1])+2*abs(points[side+'-hand'][1]-points[side+'-wrist'][1]))
        arm_gain=float(np.clip(.9*stride/max(response,1),.15,1))
        for part,amount in [('upper-arm',12),('lower-arm',5),('hand',2)]:
            walk[side+'-'+part]={'rotate':spine_bezier_timeline(lambda t,d=arm_direction,a=amount,g=arm_gain,p=phase:{'value':d*a*g*math.cos(2*math.pi*(t/period+p))},times,angular=True)}
    walk['torso']={'rotate':spine_bezier_timeline(lambda t:{'value':-2*facing+1.3*math.sin(4*math.pi*t/period)},times,angular=True)}
    idle['torso']={'rotate':[{'time':0,'value':-1},{'time':1,'value':1},{'time':2,'value':-1}]}
    # Re-solve the standing legs as the body breathes: moving the entire hip
    # with fixed leg rotations makes both resized soles float in idle.
    idle_targets={};idle_side_offsets={};idle_offset=float('inf');idle_times=[2*i/64 for i in range(65)]
    for side in ('l','r'):
        profile=profiles[side];ankle=points[side+'-ankle'].copy();ankle[0]=points[side+'-hip'][0]
        angle=profile['flat_angle'] if profile else absposes[side+'-foot'][1]
        target=sole_target(ankle,profile['points'],angle,{'stance':True,'dx':0,'clearance':0},0,0,facing,
                           ground if ground is not None else profile['setup_ground_y'])['ankle'] if profile else ankle
        L1,L2,base_hip=plans[side];idle_targets[side]=(target,angle)
        idle_side_offsets[side]=target[1]+(L1+L2)*.99-base_hip[1]-leg*.004
        idle_offset=min(idle_offset,idle_side_offsets[side])
    idle_correction={s:min(leg*.08 if any(p.get('canvas_anchors') and p.get('group')=='costume' for p in parts) else 0,max(0,v-idle_offset)) for s,v in idle_side_offsets.items()}
    def idle_bob(t):return idle_offset+leg*.002*(1-math.cos(math.pi*t))
    idle['hip']={'translate':spine_bezier_timeline(lambda t:{'x':0,'y':idle_bob(t)},idle_times,('x','y'))}
    def standing(side,t,index):
        L1,L2,base_hip=plans[side];target,angle=idle_targets[side]
        pose=solve_two_bone(base_hip+np.array([0,idle_bob(t)+idle_correction[side]]),target,L1,L2,bend=-facing)
        a,b=pose['upper_angle'],pose['lower_angle'];base1=absposes[side+'-upper-leg'][1];base2=absposes[side+'-lower-leg'][1]
        return {'value':[norm_angle(a-base1),norm_angle(b-a-(base2-base1)),norm_angle(angle-b-bybone[side+'-foot']['rotation'])][index]}
    for side in ('l','r'):
        for i,part in enumerate(('upper-leg','lower-leg','foot')):
            idle[side+'-'+part]={'rotate':spine_bezier_timeline(lambda t,s=side,index=i:standing(s,t,index),idle_times,angular=True)}
        idle[side+'-upper-leg']['translate']=[{'time':0,'x':0,'y':idle_correction[side]}]
    knee_report={}
    for side in ('l','r'):
        samples=[(planned(side,t)[0]['stance'],leg_pose(side,t)[2]) for t in times]
        support=max(v for planted,v in samples if planted);swing=max(v for planted,v in samples if not planted)
        knee_report[side]={'stance_max_deg':support,'swing_max_deg':swing,
                           'passed':support<=35 and swing<=50}
    contact={'version':'sole-contact-v1','stance':stance,'stride_length':float(stride),'scene_speed':float(facing*stride/(period*stance)),'facing':settings['facing'],
             'ground_y':ground,'hip_offset':float(max_hip_offset),'unreachable_samples':clamped,
             'foot_scale':foot_scale,'posture_policy':'natural-walk-v2','requested_lift':requested_lift,'effective_lift':lift,
             'knee_flexion':knee_report,'requires_posture_review':not all(r['passed'] for r in knee_report.values()),
             'motion_curve':'bezier-hermite-v1','curve_samples':frames,
             'feet':{s:({**{k:v for k,v in p.items() if k!='points'},**({'slot':foot_slots[s]} if s in foot_slots else {})} if p else {'source':'ankle-fallback','requires_visual_review':True}) for s,p in profiles.items()}}
    result={'skeleton':{'spine':'4.2.0','x':round((box[0]-origin[0])*scale,4),'y':round((H-box[3])*scale,4),'width':round((box[2]-box[0])*scale,4),'height':420,'images':'./images/','gait':contact},'bones':bones,'slots':slots,'skins':[{'name':'default','attachments':atts}],'animations':{'idle':{'bones':idle},'walk':{'bones':walk}}}

    if any(p.get('group')=='costume' for p in parts):
        from .garment_motion import add_long_hem_motion
        add_long_hem_motion(result,period,facing)
    return result

class CharacterProject:
    def __init__(self,directory):
        self.directory=Path(directory);self.id=self.directory.name;self.lock=threading.RLock();self.meta=json.loads((self.directory/'character.json').read_text());self.cache={}

    @classmethod
    def create(cls,storage,name):
        directory=Path(storage)/'characters'/uuid.uuid4().hex;directory.mkdir(parents=True)
        atomic_json(directory/'character.json',{'id':directory.name,'name':str(name).strip()[:80] or '单图角色','revision':0,'image_id':None,'landmarks':None,'masks':{},'settings':checked_settings({}),'versions':[],'created_at':time.time()})
        return cls(directory)

    def save(self,meta):
        meta=copy.deepcopy(meta);meta['revision']=self.meta['revision']+1;meta['updated_at']=time.time();atomic_json(self.directory/'character.json',meta);self.meta=meta

    def check(self,revision):
        if revision!=self.meta['revision']:raise UserError('单图项目已更新，请重新载入')

    def image(self):
        digest=self.meta.get('image_id')
        if not digest:raise UserError('请先上传图片，或应用完成的亿创候选图')
        if not re.fullmatch('[a-f0-9]{64}',digest):raise UserError('图片编号无效')
        return read_image((self.directory/'images'/(digest+'.png')).read_bytes())

    def cutout(self):
        key=(self.meta['image_id'],json.dumps([self.meta['settings'],self.meta['landmarks'],self.meta.get('masks',{})],sort_keys=True))
        if self.cache.get('image_key')!=key:
            self.cache={'image_key':key,'cutout':guide_cutout(self.image(),self.meta['landmarks'],self.meta.get('masks',{}),self.meta['settings'])}
        return self.cache['cutout']

    def summary(self):
        value=copy.deepcopy(self.meta);value.setdefault('masks',{});value['joints']=JOINTS;value['labels']=LABELS
        value.setdefault('part_review',{'adjustments':{},'draw_order':[]})
        value['jobs']=[json.loads(p.read_text()) for p in sorted((self.directory/'jobs').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)]
        if value['image_id']:
            im=self.image();value['width'],value['height']=im.size
            value['asset_status']=self.asset_status()
        return value

    def set_image(self,data,revision):
        self.check(revision);image=read_image(data)
        if image.width<128 or image.height<128 or max(image.size)>4096:raise UserError('图片尺寸须在 128–4096 像素之间')
        if image.width*image.height>8_000_000:raise UserError('图片最多 800 万像素，请先缩小')
        image.thumbnail((1200,1600));data=png_bytes(image);digest=sha(data);folder=self.directory/'images';folder.mkdir(exist_ok=True)
        path=folder/(digest+'.png')
        if not path.exists():path.write_bytes(data)
        meta=copy.deepcopy(self.meta);meta['source_images']=list(dict.fromkeys([*meta.get('source_images',[]),*([meta['image_id']] if meta.get('image_id') else []),digest]))[-4:];meta['image_id']=digest;meta['masks']={};meta['part_layers']={};meta['part_review']={'adjustments':{},'draw_order':[]};meta['landmarks']=default_landmarks(cut_background(image,meta['settings']['background']));[meta.pop(k,None) for k in ('canvas_contract','canvas_owners','reconstruction_review','overlap_review','assembly_inputs','assembly_overlays','assembly_pipeline','assembly_completion_history','assembly_invalidations','back_accessory_part','prebind_review')];meta['settings'].pop('background_seeds',None);self.save(meta)
        return self.summary()

    def update(self,data):
        self.check(data['revision']);meta=copy.deepcopy(self.meta)
        if data['landmarks']!=meta['landmarks'] and meta.get('assembly_inputs'):
            meta['part_layers'].update(copy.deepcopy(meta.pop('assembly_inputs')))
        meta['landmarks']=checked_landmarks(data['landmarks']);meta['settings']=checked_settings(data['settings']);meta['masks']=checked_masks(data.get('masks',self.meta.get('masks',{})))
        names={n for n,_,_ in segments(meta['landmarks'])}
        layers=meta.get('part_layers',{})
        names-={n for r in layers.values() for n in r.get('coverage',[])}
        names.update(layers)
        names.update(r['name'] for r in meta.get('assembly_overlays',[]))
        review=meta.get('part_review',{});order=self.review_order(names)
        meta['part_review']={'adjustments':{n:r for n,r in review.get('adjustments',{}).items() if n in names},'draw_order':order,**({'shoe_cuffs':True} if review.get('shoe_cuffs') else {})}
        self.save(meta)
        from .assembly_pipeline import canonicalize
        canonicalize(self)
        from .assembly_pipeline import reconcile_overlays
        reconcile_overlays(self)
        return self.summary()

    def parts(self):
        image=self.image() if self.asset_status()['ready'] else self.cutout();key=json.dumps([self.meta['image_id'],self.meta['settings'],self.meta['landmarks'],self.meta.get('masks',{}),self.meta.get('part_layers',{}),self.meta.get('part_review',{}),self.meta.get('assembly_overlays',[])],sort_keys=True)
        if self.cache.get('parts_key')!=key:
            self.cache['parts']=self.layered_parts(image,self.meta['landmarks'],self.meta.get('masks',{}));self.cache['parts_key']=key
        return self.cache['parts']

    def base_parts(self):
        image=self.image() if self.asset_status()['ready'] else self.cutout();key=json.dumps([self.meta['image_id'],self.meta['settings'],self.meta['landmarks'],self.meta.get('masks',{}),self.meta.get('part_layers',{}),self.meta.get('assembly_overlays',[])],sort_keys=True)
        if self.cache.get('base_parts_key')!=key:
            self.cache['base_parts']=self.layered_parts(image,self.meta['landmarks'],self.meta.get('masks',{}),adjust=False);self.cache['base_parts_key']=key
        return self.cache['base_parts']

    def layered_parts(self,image,landmarks,masks,adjust=True,overlays=True):
        expected={n for n,_,_ in segments(landmarks)}
        layers=self.meta.get('part_layers',{})
        self.require_layer_anchors(layers,landmarks)
        supplied={p for name,r in layers.items() for p in r.get('coverage',[name])}
        # A complete source montage is assembled only from its reviewed crops.
        # Never run the portrait segmenter over unrelated atlas cell positions.
        originals={p['name']:p for p in split_image(image,landmarks,masks) if p['name'] not in supplied} if expected-supplied else {}
        covered=set()
        for name,record in layers.items():
            if name in covered:continue
            digest=record.get('image_id','')
            if not re.fullmatch('[a-f0-9]{64}',digest):raise UserError('完整部件图片编号无效')
            if record.get('source_image_id')!=self.meta['image_id']:raise UserError('完整部件不属于当前角色图')
            sprite=read_image((self.directory/'part-images'/(digest+'.png')).read_bytes())
            if 'canvas_offset' in record:
                from .canvas_layers import placement
                part=placement(name,sprite,record,landmarks,image.size,segments)
            else:
                part=aligned_chain(name,sprite,record,landmarks,image.size) if record.get('chain') else aligned_part(name,sprite,record,landmarks,image.size,segments)
            part['label']=(('左侧' if name.startswith('l-') else '右侧')+('完整手臂' if record['chain']=='arm' else '完整腿与鞋')) if record.get('chain') else LABELS[name]
            if record.get('group')=='costume':part['group']='costume';part['label']='完整衣装'
            originals[name]=part
            for child in record.get('coverage',[name]):
                if child!=name:originals.pop(child,None);covered.add(child)
        parts=list(originals.values())
        if overlays and self.meta.get('assembly_overlays'):
            from .assembly_pipeline import overlay_parts
            parts=overlay_parts(parts,self.meta['assembly_overlays'])
        if adjust:
            W,H=image.size
            from .assembly_pipeline import adjust_parts
            parts=adjust_parts(parts,self.meta.get('part_review',{}).get('adjustments',{}),landmarks,[W,H])
        return parts

    def review_order(self,names):
        order=self.meta.get('part_review',{}).get('draw_order',[])
        fallback=COSTUME_DRAW_ORDER if self.meta.get('part_layers',{}).get('torso',{}).get('group')=='costume' else DRAW_ORDER
        from .assembly_pipeline import overlay_order
        items=self.meta.get('assembly_overlays',[]);base=set(names)-{x['name'] for x in items}
        order=[n for n in order if n in base]
        order=order if set(order)==base and len(order)==len(base) else [n for n in fallback if n in base]
        return overlay_order(order,items)

    def garment_template(self,data):
        from .garment_templates import contract,plan
        self.check(data['revision'])
        if data.get('template') is not None:
            meta=copy.deepcopy(self.meta);meta['garment_template']=contract(self,data['template'])
            for key in ('prebind_review','overlap_review','reconstruction_review'):meta.pop(key,None)
            self.save(meta)
        return {**self.summary(),'garment_plan':plan(self)}

    @atomic_assembly
    def prepare_assembly(self,data):
        """Execute shared-canvas registration and reviewed foreground extraction."""
        from .assembly_pipeline import canonicalize,validate_overlays,replace_from_sources
        self.check(data['revision'])
        if 'garment_template' in data:
            from .garment_templates import contract
            meta=copy.deepcopy(self.meta);meta['garment_template']=contract(self,data['garment_template']);self.save(meta)
        if data.get('replacements'):replace_from_sources(self,data['replacements'])
        canonicalize(self)
        if not self.asset_status()['ready']:raise UserError('先补齐独立部件，不能用可见碎片替代补全')
        if data.get('connections'):
            from .connection_fit import apply
            apply(self,data['connections'])
        meta=copy.deepcopy(self.meta)
        parts=self.layered_parts(self.image(),meta['landmarks'],meta.get('masks',{}),adjust=False,overlays=False)
        meta['assembly_overlays']=validate_overlays(parts,data.get('overlays',meta.get('assembly_overlays',[])))
        from .assembly_pipeline import overlay_order
        overlay_order([p['name'] for p in parts],meta['assembly_overlays'])
        if 'back_accessory_part' in data:
            name=data['back_accessory_part']
            if name is not None and (name not in ('torso','back') or name not in meta['part_layers']):raise UserError('背包归属须为实际完整部件')
            meta['back_accessory_part']=name
        meta['prebind_contract']='complete-assets-v1'
        meta['assembly_pipeline']={'version':'assembly-execution-v2','steps':['complete-silhouettes','register-common-canvas','preserve-overlap','compose-occlusion'],'automatic':True}
        for key in ('prebind_review','overlap_review','reconstruction_review'):meta.pop(key,None)
        self.save(meta);return self.summary()

    def review(self):
        image=self.cutout();parts=self.base_parts();W,H=image.size
        key=json.dumps([self.meta['image_id'],self.meta['landmarks'],self.meta.get('masks',{}),self.meta.get('part_layers',{}),self.meta.get('assembly_overlays',[])],sort_keys=True)
        if self.cache.get('review_key')!=key:
            self.cache['quality']={p['name']:component_stats(p['image']) for p in parts};self.cache['review_key']=key
        items=[]
        for p in parts:
            record=self.meta.get('part_layers',{}).get(p['name'],{});quality=copy.deepcopy(self.cache['quality'][p['name']]);warnings=[]
            if quality['component_count']>1:warnings.append('存在多个分离像素块，请检查是否混入邻近部件或缺少连接')
            if record.get('group')=='costume' and quality['component_count']>1:warnings.append('完整衣装包含断开图块：检查腰线到前后裙底是否有真实连续图面；删掉旧腿不会补出被遮挡布料，透明度调整不能修补缺片')
            if not p.get('complete'):warnings.append('来自原图可见区域，遮挡部分尚未完整恢复')
            filled=record.get('filled_pixels',0);visible=record.get('preserved_visible_pixels',quality['visible_pixels'])
            if filled/(filled+visible or 1)>.2:warnings.append('补全区域超过 20%，请对照原图检查形状和颜色')
            items.append({'name':p['name'],'label':p['label'],'box':p['box'],'pivot':(np.array(self.meta['landmarks'][p['start']])*[W,H]).tolist(),
                          'adjustment':{**DEFAULT,**self.meta.get('part_review',{}).get('adjustments',{}).get(p['name'],{})},
                          'quality':{**quality,'filled_pixels':filled,'warnings':warnings}})
        return {'schema':'spine-part-review-v1','coordinate_system':'image-pixels-y-down-clockwise','revision':self.meta['revision'],
                'image_id':self.meta['image_id'],'width':W,'height':H,'draw_order':self.review_order([p['name'] for p in parts]),'parts':items,'shoe_cuffs':bool(self.meta.get('part_review',{}).get('shoe_cuffs'))}

    def update_review(self,data):
        self.check(data['revision']);names=[p['name'] for p in self.base_parts()]
        if set(data.get('part_review',{}).get('adjustments',{})) & {r['name'] for r in self.meta.get('assembly_overlays',[])}:raise UserError('前景遮挡层跟随源部件，请调整源部件')
        if data.get('part_review',{}).get('shoe_cuffs') and any(r.get('chain')=='leg' for r in self.meta.get('part_layers',{}).values()):raise UserError('连续腿鞋部件已有鞋口，不需要额外鞋沿图层')
        meta=copy.deepcopy(self.meta);meta['part_review']=checked_review(data.get('part_review'),names,self.image().size,DRAW_ORDER)
        self.save(meta);return self.summary()

    def partition_canvas(self,data):
        from .canvas_layers import partition
        self.check(data['revision']);items=copy.deepcopy(data.get('layers',[]))
        for item in items:
            for field in ('fill_image','mask','completed_image'):
                if item.get(field):item[field]=read_image(base64.b64decode(item[field],validate=True))
        layers,_=partition(self.cutout(),items,data.get('draw_order',[]));spec={x['name']:x for x in items}
        for item in layers:
            item['image']=base64.b64encode(png_bytes(item['image'])).decode()
            item.update({k:spec[item['name']][k] for k in ('chain','pins','group','complete') if k in spec[item['name']]})
        return self.install_canvas_layers({'revision':data['revision'],'layers':layers,'draw_order':data['draw_order']})

    @atomic_assembly
    def install_canvas_layers(self,data):
        from .canvas_layers import ownership, placement
        self.check(data['revision']);items=data.get('layers')
        if not isinstance(items,list) or not 1<=len(items)<=16:raise UserError('同坐标图层须为1–16项')
        owners=ownership(items);meta=copy.deepcopy(self.meta);layers={};prepared=[]
        for item in items:
            name=item.get('name')
            if name not in LABELS or name in layers:raise UserError('同坐标部件名称无效或重复')
            sprite=read_image(base64.b64decode(item['image'],validate=True)).convert('RGBA')
            offset=item.get('offset');claims=item['owns'];complete=item.get('complete')
            if not isinstance(offset,list) or len(offset)!=2 or type(complete)!=bool:raise UserError('须提供整数offset和complete布尔值')
            record={'canvas_offset':offset,'owns':claims,'complete':complete,'coverage':[name],'source_image_id':meta['image_id'],'mode':'canvas-layer'}
            if item.get('chain'):
                chain=item['chain']
                if chain not in CHAINS or name not in ('l-upper-'+chain,'r-upper-'+chain):raise UserError('同坐标链名称无效')
                pins=item.get('pins',{})
                if set(pins)!=set(CHAINS[chain]):raise UserError('同坐标链须提供四个母版像素关节点')
                W,H=self.image().size
                for key,point in pins.items():
                    if not isinstance(point,list) or len(point)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in point) or not (0<=point[0]<=W and 0<=point[1]<=H):raise UserError('母版关节点无效')
                    meta['landmarks'][name[:2]+key]=[point[0]/W,point[1]/H]
                record.update(chain=chain,canvas_pins=pins,coverage=[name[:2]+v for v in (('upper-arm','lower-arm','hand') if chain=='arm' else ('upper-leg','lower-leg','foot'))])
            if item.get('group'):
                if item['group']!='costume' or name!='torso' or item.get('chain'):raise UserError('完整衣装分组无效')
                record.update(group='costume',coverage=['torso','pelvis'])
            if any(set(record['coverage'])&set(r['coverage']) for r in layers.values()):raise UserError('同坐标图层覆盖冲突')
            self.require_layer_anchors({name:record},meta['landmarks'])
            placement(name,sprite,record,meta['landmarks'],self.image().size,segments)
            blob=png_bytes(sprite);digest=sha(blob);record['image_id']=digest;layers[name]=record;prepared.append((digest,blob))
        expected={n for n,_,_ in segments(meta['landmarks'])};covered={n for r in layers.values() for n in r['coverage']}
        if expected-covered:raise UserError('同坐标图层缺少部件：'+', '.join(sorted(expected-covered)))
        order=data.get('draw_order')
        if not isinstance(order,list) or len(order)!=len(layers) or set(order)!=set(layers):raise UserError('同坐标图层必须提供完整draw_order')
        meta['part_layers']=layers;meta['part_review']={'adjustments':{},'draw_order':order};meta['canvas_contract']='canvas-layers-v1';meta['canvas_owners']=owners;meta.pop('reconstruction_review',None);meta.pop('overlap_review',None)
        for key in ('assembly_inputs','assembly_overlays','prebind_review'):meta.pop(key,None)
        meta['assembly_pipeline']={'version':'assembly-execution-v2','steps':['complete-silhouettes','register-common-canvas','preserve-overlap','compose-occlusion'],'automatic':True}
        folder=self.directory/'part-images';folder.mkdir(exist_ok=True)
        for digest,blob in prepared:(folder/(digest+'.png')).write_bytes(blob)
        self.save(meta);return self.summary()

    def reconstruction(self):
        from .canvas_layers import reconstruction
        if self.meta.get('canvas_contract')!='canvas-layers-v1':raise UserError('先导入同坐标图层')
        parts=self.parts()
        return reconstruction(self.cutout(),parts,self.review_order([p['name'] for p in parts]),self.meta['canvas_owners'])

    def layer_overlap(self):
        from .layer_overlap import inspect
        parts=self.parts()
        return inspect(self.cutout(),parts,self.review_order([p['name'] for p in parts]),self.meta['landmarks'])

    def finish_parts(self,data):
        """Scoped, reversible finishing on explicit imported sprites, before rigging."""
        from .material_finish import finish_material
        self.check(data['revision']);edits=data.get('edits')
        if not isinstance(edits,list) or not 1<=len(edits)<=16:raise UserError('提供1–16项部件修整')
        meta=copy.deepcopy(self.meta);prepared=[];history=[];seen=set()
        for edit in edits:
            name=edit.get('part')
            if name in seen or name not in meta.get('part_layers',{}):raise UserError('部件无效或重复')
            seen.add(name);record=meta['part_layers'][name];old=record['image_id']
            image=read_image((self.directory/'part-images'/(old+'.png')).read_bytes())
            result,report=finish_material(image,edit);blob=png_bytes(result);digest=sha(blob)
            record['image_id']=digest;prepared.append((digest,blob));history.append({'part':name,'before':old,'after':digest,'report':report})
        for digest,blob in prepared:(self.directory/'part-images'/(digest+'.png')).write_bytes(blob)
        meta.setdefault('part_finish_history',[]).append(history)
        meta.pop('assembly_inputs',None)
        meta['assembly_overlays']=[r for r in meta.get('assembly_overlays',[]) if r['source'] not in seen]
        for key in ('prebind_review','reconstruction_review','overlap_review'):meta.pop(key,None)
        self.save(meta);return self.summary()

    def prebind_evidence(self):
        from .prebind import cached_evidence
        return cached_evidence(self)

    def edge_evidence(self):
        from .edge_review import prepare
        return prepare(self)

    def review_prebind(self,data):
        from .prebind import validate_review
        self.check(data['revision']);report,_=self.prebind_evidence()
        review=validate_review(report,data)
        meta=copy.deepcopy(self.meta);meta['prebind_review']=review;self.save(meta)
        return self.summary()

    def review_overlap(self,data):
        from .layer_overlap import CHECKS,require
        self.check(data['revision']);report,_=self.layer_overlap();review={k:data.get(k) for k in ('digest','checks','verdict','reason')}
        if review['digest']!=report['digest'] or not isinstance(review['reason'],str) or not review['reason'].strip():raise UserError('请记录当前摆开图的实际观察')
        if not isinstance(review['checks'],dict) or set(review['checks'])!=set(CHECKS) or any(type(v)!=bool for v in review['checks'].values()):raise UserError('摆开检查须有四项布尔结论')
        if review['verdict'] not in ('ready','needs_repair'):raise UserError('摆开验收结论无效')
        if review['verdict']=='ready':require(report,review)
        meta=copy.deepcopy(self.meta);meta['overlap_review']=review;self.save(meta);return self.summary()

    def review_reconstruction(self,data):
        from .canvas_layers import require,CHECKS
        self.check(data['revision']);report,_,_=self.reconstruction();review={k:data.get(k) for k in ('digest','checks','verdict','reason')}
        if not isinstance(review['reason'],str) or not review['reason'].strip():raise UserError('请记录拼回原图的实际观察')
        if not isinstance(review['checks'],dict) or set(review['checks'])!=set(CHECKS) or any(type(v)!=bool for v in review['checks'].values()):raise UserError('须记录四项布尔视觉结论')
        if review['verdict'] not in ('ready','needs_repair'):raise UserError('拼回验收结论无效')
        if review['verdict']=='ready':require(report,review)
        elif review['digest']!=report['digest']:raise UserError('拼回资源已变化')
        meta=copy.deepcopy(self.meta);meta['reconstruction_review']=review;self.save(meta);return self.summary()

    def apply_part_layout(self,data,job,image):
        """Use explicit visually reviewed crops, including connected limb chains."""
        self.check(data['revision'])
        if job.get('sheet')=='turnaround':raise UserError('三视图只供比对验收，不能当作部件图接入')
        if not job.get('candidate_ready') or job.get('mode')!='complete-parts':raise UserError('须选择已完成的部件候选图')
        if job.get('source_image_id')!=self.meta['image_id']:raise UserError('部件候选不属于当前角色图片')
        return self._apply_reviewed_layout(data,image,{'job_id':job['id'],'sheet':job.get('sheet'),'mode':'reviewed-layout'})

    def apply_source_layout(self,data):
        """Import reviewed crops from the immutable source, with no AI job."""
        self.check(data['revision'])
        return self._apply_reviewed_layout(data,self.image(),{'sheet':'source-assets','mode':'source-layout'})

    def require_layer_anchors(self,layers,landmarks):
        missing=sorted(set(layers)-{n for n,_,_ in segments(landmarks)})
        if missing:raise UserError('导入部件缺少定位点，不能静默丢弃：'+', '.join(missing)+'；请先通过 guide 标注对应位置（后发或背部配件使用 back）')

    @atomic_assembly
    def _apply_reviewed_layout(self,data,image,provenance):
        items=extract_parts(image,data.get('parts'),LABELS,self.meta['settings']['background'])
        meta=copy.deepcopy(self.meta)
        previous_group=meta.get('part_layers',{}).get('torso',{}).get('group')
        layers=meta.setdefault('part_layers',{});W,H=self.image().size;prepared=[]
        self.require_layer_anchors({**layers,**{name:record for name,_,record in items}},meta['landmarks'])
        for name,sprite,record in items:
            if provenance.get('mode')=='reviewed-layout':
                from .material_finish import clean_alpha_dust
                sprite,dust=clean_alpha_dust(sprite);record['alpha_cleanup']=dust
            record.update(source_image_id=meta['image_id'],**provenance)
            if record.get('chain'):
                # The supplied pins calibrate the hidden interior pivots. The
                # current guide's first/last joint determines size and pose.
                placed=aligned_chain(name,sprite,record,meta['landmarks'],(W,H))
                for joint,p in placed['chain_points'].items():
                    values=(np.array(p)/[W,H]).tolist()
                    if any(v<0 or v>1 for v in values):raise UserError('部件关节超出角色画布，请先校准 guide')
                    meta['landmarks'][joint]=values
                for child in record['coverage']:layers.pop(child,None)
            else:
                for child in record.get('coverage',[name]):layers.pop(child,None)
                for old,r in list(layers.items()):
                    if set(record.get('coverage',[name])) & set(r.get('coverage',[old])):layers.pop(old)
            blob=png_bytes(sprite);digest=sha(blob);record['image_id']=digest;layers[name]=record;prepared.append((digest,blob))
        # Validate all items before persisting blobs or a new revision.
        meta['landmarks']=checked_landmarks(meta['landmarks'])
        folder=self.directory/'part-images';folder.mkdir(exist_ok=True)
        for digest,blob in prepared:
            path=folder/(digest+'.png')
            if not path.exists():path.write_bytes(blob)
        # Preserve unrelated registered pixels, offsets and foreground recipes.
        changed={name for name,_,_ in items};available=set(layers)
        review=meta.setdefault('part_review',{'adjustments':{},'draw_order':[]})
        review['adjustments']={n:v for n,v in review.get('adjustments',{}).items() if n in available and n not in changed}
        review['draw_order']=[n for n in review.get('draw_order',[]) if n in available]
        if previous_group!=layers.get('torso',{}).get('group'):review['draw_order']=[]
        meta['assembly_inputs']={n:v for n,v in meta.get('assembly_inputs',{}).items() if n in available and n not in changed}
        for key in ('prebind_review','overlap_review','reconstruction_review'):meta.pop(key,None)
        self.save(meta)
        from .assembly_pipeline import canonicalize
        canonicalize(self)
        from .assembly_pipeline import reconcile_overlays
        reconcile_overlays(self)
        if data.get('assembly_plan'):
            self.prepare_assembly({**data['assembly_plan'],'revision':self.meta['revision']})
        return self.summary()

    def asset_status(self):
        """Structural completeness only; actual assembly still needs visual review."""
        landmarks=self.meta.get('landmarks') or {}
        required={'head','torso','pelvis',*[s+'-'+p for s in ('l','r') for p in ('upper-arm','lower-arm','hand','upper-leg','lower-leg','foot')]}
        if 'back' in landmarks:required.add('back')
        covered=set();chains=set()
        for name,record in self.meta.get('part_layers',{}).items():
            digest=record.get('image_id','')
            if record.get('source_image_id')!=self.meta['image_id'] or not re.fullmatch('[a-f0-9]{64}',digest):continue
            if not (self.directory/'part-images'/(digest+'.png')).is_file():continue
            covered.update(record.get('coverage',[name]))
            if record.get('chain') in ('arm','leg'):chains.add(name)
        expected={s+'-upper-'+p for s in ('l','r') for p in ('arm','leg')}
        missing=sorted(required-covered);missing_chains=sorted(expected-chains)
        unbound=sorted(set(self.meta.get('part_layers',{}))-{n for n,_,_ in segments(landmarks)})
        return {'ready':not missing and not missing_chains and not unbound,'missing_parts':missing,'missing_chains':missing_chains,'unbound_parts':unbound,
                'requires_visual_review':True}

    def cuff_textures(self,parts,base=None,landmarks=None):
        from .ankle_join import cuff_image
        base=base if base is not None else self.base_parts();landmarks=landmarks or self.meta['landmarks'];W,H=self.image().size
        textures={};by={p['name']:p for p in parts}
        for p in base:
            if p['name'] not in ('l-foot','r-foot'):continue
            cuff={**p,'image':cuff_image(p['image'])}
            cuff=apply_adjustment(cuff,self.meta.get('part_review',{}).get('adjustments',{}).get(p['name'],{}),np.array(landmarks[p['start']])*[W,H])
            target=by[p['name']];canvas=Image.new('RGBA',target['image'].size)
            canvas.paste(cuff['image'],tuple(np.array(cuff['box'][:2])-target['box'][:2]));textures[p['name']]=canvas
        return textures

    def assembly(self):
        parts=self.parts();order=self.review_order([p['name'] for p in parts])
        if self.meta.get('part_review',{}).get('shoe_cuffs'):
            from .ankle_join import joined_order
            order=joined_order(order);textures=self.cuff_textures(parts)
            for side in ('l','r'):
                foot=next(p for p in parts if p['name']==side+'-foot');name=side+'-foot-cuff'
                parts=parts+[{**foot,'name':name,'image':textures[foot['name']]}];order.insert(order.index(side+'-lower-leg')+1,name)
        return compose(parts,self.image().size,order)

    def apply_part_sheet(self,data,job,image):
        self.check(data['revision'])
        if job.get('sheet')=='turnaround':raise UserError('三视图只供比对验收，不能当作部件图接入')
        if not job.get('candidate_ready') or job.get('mode')!='complete-parts':raise UserError('完整部件候选图尚未完成')
        if job.get('sheet')=='turnaround':raise UserError('三视图只供比对验收，不能当作部件图接入')
        if job.get('requires_reviewed_layout') or job.get('sheet') in ('limbs','character-assets'):raise UserError('完整部件图请由 Codex 检查实际裁片与关节，并使用 apply-part-layout 接入连续骨链')
        if job.get('source_image_id')!=self.meta['image_id']:raise UserError('此完整部件基于另一张角色图，不能应用')
        names=job.get('parts',[])
        if not names or set(names)-set(LABELS) or len(names)!=job['rows']*job['columns']:raise UserError('完整部件图的排列信息无效')
        sprites,layout=split_sheet(image,job,cut_background);meta=copy.deepcopy(self.meta);layers=meta.setdefault('part_layers',{});folder=self.directory/'part-images';folder.mkdir(exist_ok=True)
        preserve=data.get('mode','preserve')!='redraw';source=self.cutout();W,H=source.size
        originals={p['name']:p for p in split_image(source,meta['landmarks'],meta.get('masks',{}),fill_back=False)} if preserve else {}
        for name,sprite in sprites.items():
            if preserve and name not in originals:continue
            for old,record in list(layers.items()):
                if name in record.get('coverage',[]):layers.pop(old)
            if preserve:
                part=originals[name];sprite,box,visible,filled=preserve_visible(part,sprite,source,meta.get('masks',{}).get(name))
                start,end=part['start'],part['end']
                if name=='head':start,end='neck','head'
                if name=='torso':start,end='hip','neck'
                a=np.array(meta['landmarks'][start])*[W,H];b=np.array(meta['landmarks'][end])*[W,H]
                if name=='back':b=a+np.array([0,np.linalg.norm((np.array(meta['landmarks']['hip'])-meta['landmarks']['neck'])*[W,H])*.5])
            content=png_bytes(sprite);digest=sha(content);path=folder/(digest+'.png')
            if not path.exists():path.write_bytes(content)
            layers[name]={'image_id':digest,'source_image_id':self.meta['image_id'],'job_id':job['id'],'sheet':job['sheet'],'start_pin':PINS[kind(name)][0],'end_pin':PINS[kind(name)][1],'layout':layout}
            if preserve:layers[name].update(source_box=(np.array(box)/[W,H,W,H]).tolist(),source_start=(a/[W,H]).tolist(),source_end=(b/[W,H]).tolist(),rigid=name.endswith('-foot'),preserved_visible_pixels=visible,filled_pixels=filled,mode='occlusion-fill')
        if any(r.get('chain') for r in self.meta.get('part_layers',{}).values()):meta['part_review']={'adjustments':{},'draw_order':[]}
        self.save(meta);return self.summary()

    def reset_part(self,data):
        self.check(data['revision']);name=data.get('name')
        if name not in LABELS:raise UserError('部件名称无效')
        meta=copy.deepcopy(self.meta);layers=meta.setdefault('part_layers',{});removed=layers.pop(name,{})
        meta.pop('assembly_inputs',None)
        meta['assembly_overlays']=[r for r in meta.get('assembly_overlays',[]) if name not in (r['source'],r['subject'])]
        reset_chain=bool(removed.get('chain'))
        for old,record in list(layers.items()):
            if name in record.get('coverage',[]):layers.pop(old);reset_chain=True
        if reset_chain:meta['part_review']={'adjustments':{},'draw_order':[]}
        else:meta.get('part_review',{}).get('adjustments',{}).pop(name,None)
        self.save(meta);return self.summary()

    def install_source_layers(self,data):
        """Install reviewed source-space completions without changing the source."""
        self.check(data['revision']);meta=copy.deepcopy(self.meta)
        meta['landmarks']=checked_landmarks(data['landmarks']);meta['masks']=checked_masks(data['masks']);meta['settings']=checked_settings(data['settings'])
        prepared=[];layers={};W,H=self.image().size
        if not isinstance(data.get('parts'),list) or len(data['parts'])>len(LABELS):raise UserError('补全部件列表无效')
        for item in data['parts']:
            name=item.get('name');record=copy.deepcopy(item.get('record',{}))
            if name not in LABELS or name in layers or record.get('source_image_id')!=meta['image_id']:raise UserError('补全部件不属于当前角色')
            for key,count in [('source_box',4),('source_start',2),('source_end',2)]:
                values=record.get(key)
                if not isinstance(values,list) or len(values)!=count:raise UserError('补全部件的原图锚点无效')
                record[key]=[number(v,-1,2) for v in values]
            sprite=read_image(base64.b64decode(item['image'],validate=True));box=np.rint(np.array(record['source_box'])*[W,H,W,H]).astype(int)
            if tuple(box[2:]-box[:2])!=sprite.size:raise UserError('补全贴图尺寸与原图范围不符')
            content=png_bytes(sprite);digest=sha(content);record.update(image_id=digest,mode='occlusion-fill');layers[name]=record;prepared.append((digest,content))
        folder=self.directory/'part-images';folder.mkdir(exist_ok=True)
        for digest,content in prepared:
            path=folder/(digest+'.png')
            if not path.exists():path.write_bytes(content)
        meta['part_layers']=layers;self.save(meta);return self.summary()

    def build(self,storage,data):
        nonce=str(data.get('nonce',''))
        if not re.fullmatch('[a-f0-9-]{16,64}',nonce):raise UserError('生成版本编号无效')
        # Repeating the same request never creates another rig or extra history.
        existing=next((v for v in self.meta['versions'] if v['id']==nonce),None)
        if existing:return self.summary()
        self.check(data['revision']);landmarks=checked_landmarks(data['landmarks']);settings=checked_settings(data['settings']);masks=checked_masks(data.get('masks',self.meta.get('masks',{})));im=guide_cutout(self.image(),landmarks,masks,settings);parts=self.layered_parts(im,landmarks,masks)
        if self.meta.get('canvas_contract')=='canvas-layers-v1' and self.meta.get('prebind_contract')!='complete-assets-v1':
            from .canvas_layers import reconstruction,require
            report,_,_=reconstruction(self.cutout(),parts,self.review_order([p['name'] for p in parts]),self.meta['canvas_owners'])
            review_record=self.meta.get('reconstruction_review',{})
            if data.get('draft') is True:
                if review_record.get('digest')!=report['digest']:raise UserError('草稿也须先记录当前拼回验收')
            else:
                require(report,review_record)
                from .layer_overlap import require as require_overlap
                require_overlap(self.layer_overlap()[0],self.meta.get('overlap_review'))
                if any(not p.get('complete') for p in parts):raise UserError('仍有不完整图层，只能交付草稿')
        if self.meta.get('prebind_contract')=='complete-assets-v1':
            if landmarks!=checked_landmarks(self.meta['landmarks']) or settings!=checked_settings(self.meta['settings']) or masks!=checked_masks(self.meta.get('masks',{})):raise UserError('先保存当前定位与设置，再重新验收三项素材图')
            from .prebind import require
            require(self.prebind_evidence()[0],self.meta.get('prebind_review'))
        if self.meta.get('stress_contract'):
            from .assembly_stress import require as require_stress
            require_stress(self)
        review=checked_review({'adjustments':self.meta.get('part_review',{}).get('adjustments',{}),'draw_order':self.review_order([p['name'] for p in parts])},[p['name'] for p in parts],im.size,DRAW_ORDER)
        if self.meta.get('part_review',{}).get('shoe_cuffs'):review['shoe_cuffs']=True
        skeleton=make_skeleton(im,landmarks,parts,settings,masks,review['draw_order'])
        if self.meta.get('back_accessory_part'):
            skeleton['skeleton']['assembly_features']={'back_accessory_part':self.meta['back_accessory_part']}
        render_parts=parts
        if review.get('shoe_cuffs'):
            from .ankle_join import add_cuffs
            skeleton,render_parts=add_cuffs(skeleton,parts,self.cuff_textures(parts,self.layered_parts(im,landmarks,masks,adjust=False),landmarks))
        state={'skeleton':skeleton,'parts':[{'name':p['name']} for p in render_parts]};report=validate_state(state)
        if not report['passed']:raise UserError('生成骨架未通过结构校验：'+'；'.join(report['errors'][:4]))
        files,_=pack([({'name':p['name']},p['image']) for p in render_parts]);files['skeleton.json']=json.dumps(skeleton).encode()
        source_id=uuid.uuid4().hex;source_dir=Path(storage)/'projects'/source_id;rig_dir=Path(storage)/'rigs'/uuid.uuid4().hex
        try:
            source=Project(source_dir,files,self.meta['name']+' · 单图行走');rig=RigProject.create(rig_dir,source)
            if not rig.validate()['passed']:raise UserError('生成图集未通过校验')
            materials=[]
            for digest in list(dict.fromkeys([*self.meta.get('source_images',[]),self.meta['image_id']]))[-4:]:
                path=self.directory/'images'/(digest+'.png')
                if path.is_file():
                    reference=read_image(path.read_bytes());materials.append({'id':'source-'+str(len(materials)+1),'name':'角色准备原图 '+str(len(materials)+1),'sha':rig.put_blob(path.read_bytes()),'width':reference.width,'height':reference.height})
            turnaround=self.meta.get('turnaround_reference')
            if turnaround:
                path=self.directory/'jobs'/(turnaround['job_id']+'.png')
                if not path.is_file() or sha(path.read_bytes())!=turnaround['sha256']:raise UserError('三视图来源已变化，请重新验收')
                reference=read_image(path.read_bytes());materials.append({'id':'turnaround','name':'已审查的正侧背三视图','sha':rig.put_blob(path.read_bytes()),'width':reference.width,'height':reference.height})
                rig.meta['turnaround_reference']=copy.deepcopy(turnaround)
            if self.meta.get('canvas_contract'):
                rig.meta['canvas_layers']={'contract':self.meta['canvas_contract'],'character_id':self.id,'source_image_id':self.meta['image_id'],'owners':copy.deepcopy(self.meta['canvas_owners']),'canvas_size':list(im.size),'layers':copy.deepcopy(self.meta['part_layers']),'assembly_overlays':copy.deepcopy(self.meta.get('assembly_overlays',[])),'assembly_pipeline':copy.deepcopy(self.meta.get('assembly_pipeline')),'overlap_review':copy.deepcopy(self.meta.get('overlap_review')),'review':copy.deepcopy(self.meta.get('reconstruction_review')),'draft':data.get('draft') is True}
            from .assembly_source import snapshot
            rig.meta['assembly_source']=snapshot(self.id,self.meta,landmarks,settings,masks,review,render_parts)
            rig.meta['material_sources']=materials;atomic_json(rig.directory/'rig.json',rig.meta)
            meta=copy.deepcopy(self.meta);meta.update(landmarks=landmarks,settings=settings,masks=masks)
            generator='reviewed-chain-v1' if any(r.get('chain') for r in meta.get('part_layers',{}).values()) else 'occlusion-fill-v1' if any(r.get('mode')=='occlusion-fill' for r in meta.get('part_layers',{}).values()) else 'complete-parts-v1' if meta.get('part_layers') else 'chain-mesh-v2'
            meta['part_review']=review
            meta['versions'].append({'id':nonce,'created_at':time.time(),'label':str(data.get('label','')).strip()[:80] or '行走版本 '+str(len(meta['versions'])+1),'image_id':meta['image_id'],'landmarks':landmarks,'masks':masks,'settings':settings,'part_layers':copy.deepcopy(meta.get('part_layers',{})),'part_review':copy.deepcopy(review),'render_parts':len(render_parts),'rig_id':rig.id,'source_id':source.id,'generator':generator,'parts':[{'name':p['name'],'box':p['box'],'pixel_count':p['pixel_count'],'complete':p.get('complete',False)} for p in parts]})
            self.save(meta)
        except Exception:
            # Failed builds are never presented as user projects.
            import shutil
            shutil.rmtree(source_dir,ignore_errors=True);shutil.rmtree(rig_dir,ignore_errors=True);raise
        return self.summary()

    def foot_layers(self,storage,data):
        """Derive ordering/cuff variants without regenerating accepted art or motion."""
        nonce=str(data.get('nonce',''));policy=data.get('policy','behind')
        if not re.fullmatch('[a-f0-9-]{16,64}',nonce):raise UserError('生成版本编号无效')
        if policy not in ('behind','front','cuff'):raise UserError('脚腿遮挡选项无效')
        existing=next((v for v in self.meta['versions'] if v['id']==nonce),None)
        if existing:
            if existing.get('derived_from_version')!=data.get('version') or existing.get('foot_layer_policy')!=policy:raise UserError('版本编号已被其他操作使用')
            return self.summary()
        self.check(data['revision'])
        v=next((v for v in self.meta['versions'] if v['id']==data.get('version')),None)
        if not v:raise UserError('请选择已有行走版本')
        original=RigProject(Path(storage)/'rigs'/v['rig_id'])
        with original.lock:
            skeleton=copy.deepcopy(original.state['skeleton'])
            original_parts=[{'name':p['name'],'image':read_image(original.blob(p))} for p in original.state['parts']]
            if v.get('foot_layer_policy')=='cuff' or v.get('part_review',{}).get('shoe_cuffs'):
                from .rig import skins
                helpers={s+'-foot-cuff' for s in ('l','r')}
                skeleton['slots']=[s for s in skeleton['slots'] if s['name'] not in helpers]
                original_parts=[p for p in original_parts if p['name'] not in helpers]
                for _,attachments,_ in skins(skeleton):
                    for name in helpers:attachments.pop(name,None)
            if any(a.get('drawOrder') or a.get('draworder') for a in skeleton.get('animations',{}).values()):
                raise UserError('此版本包含随动作变化的层级，请在骨骼编辑器中调整脚腿遮挡')
            slots=skeleton['slots'];names=[s['name'] for s in slots]
            if not all(side+'-'+part in names for side in ('l','r') for part in ('foot','lower-leg')):raise UserError('此骨架没有可识别的双脚与小腿')
            for side in ('l','r'):
                foot=next(s for s in slots if s['name']==side+'-foot');slots.remove(foot)
                index=next(i for i,s in enumerate(slots) if s['name']==side+'-lower-leg')
                slots.insert(index+(policy=='front'),foot)
            logical_order=[s['name'] for s in slots];render_parts=original_parts
            if policy=='cuff':
                from .ankle_join import add_cuffs
                skeleton,render_parts=add_cuffs(skeleton,original_parts)
            files,_=pack([({'name':p['name']},p['image']) for p in render_parts]);files['skeleton.json']=json.dumps(skeleton).encode()
        source_dir=Path(storage)/'projects'/uuid.uuid4().hex;rig_dir=Path(storage)/'rigs'/uuid.uuid4().hex
        try:
            source=Project(source_dir,files,self.meta['name']+' · 脚腿遮挡调整');rig=RigProject.create(rig_dir,source)
            if not rig.validate()['passed']:raise UserError('脚腿遮挡版本未通过校验')
            version=copy.deepcopy(v);order=[s['name'] for s in slots]
            version.update(id=nonce,created_at=time.time(),label='腿鞋接合 · 鞋口包住小腿' if policy=='cuff' else '脚在小腿后方 · 保留原版关节' if policy=='behind' else '鞋口在小腿前方 · 保留原版关节',
                           rig_id=rig.id,source_id=source.id,derived_from_version=v['id'],foot_layer_policy=policy)
            version.setdefault('part_review',{'adjustments':{},'draw_order':[]})['draw_order']=logical_order
            version['part_review'].pop('shoe_cuffs',None)
            if policy=='cuff':version['part_review']['shoe_cuffs']=True
            version['render_parts']=len(render_parts)
            meta=copy.deepcopy(self.meta);meta['versions'].append(version);self.save(meta)
        except Exception:
            import shutil
            shutil.rmtree(source_dir,ignore_errors=True);shutil.rmtree(rig_dir,ignore_errors=True);raise
        return self.summary()

    def restore(self,data):
        self.check(data['revision']);v=next((v for v in self.meta['versions'] if v['id']==data['version']),None)
        if not v:raise UserError('历史版本不存在')
        meta=copy.deepcopy(self.meta)
        for key in ('image_id','landmarks','settings'):meta[key]=copy.deepcopy(v[key])
        meta['masks']=copy.deepcopy(v.get('masks',{}))
        meta['part_layers']=copy.deepcopy(v.get('part_layers',{}))
        meta['part_review']=copy.deepcopy(v.get('part_review',{'adjustments':{},'draw_order':[]}))
        self.save(meta);return self.summary()

    def export(self,storage,version):
        v=next((v for v in self.meta['versions'] if v['id']==version),None)
        if not v:raise UserError('请先生成行走版本')
        rig=RigProject(Path(storage)/'rigs'/v['rig_id']);out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(rig.export())) as original,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
            for item in original.infolist():target.writestr(item.filename,original.read(item.filename))
            target.writestr('single-image/source.png',(self.directory/'images'/(v['image_id']+'.png')).read_bytes())
            target.writestr('single-image/guide.json',json.dumps(v,ensure_ascii=False,indent=2))
            target.writestr('single-image/assembly-review.json',json.dumps({'schema':'spine-part-review-v1','coordinate_system':'image-pixels-y-down-clockwise','image_id':v['image_id'],**v.get('part_review',{'adjustments':{},'draw_order':[]})},ensure_ascii=False,indent=2))
            for name,record in v.get('part_layers',{}).items():target.writestr('single-image/complete-parts/'+name+'.png',(self.directory/'part-images'/(record['image_id']+'.png')).read_bytes())
        return out.getvalue()
