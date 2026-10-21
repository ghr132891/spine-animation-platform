"""Reviewed connection envelopes and same-material color joins.

Works on actual registered pixels, never whole-limb scaling or alpha fading.
The caller identifies the garment connection; no guessed anatomy or material.
"""
import copy
import re
import numpy as np
from PIL import Image
from .core import UserError,png_bytes,read_image,sha


def finite(value,label):
    if type(value) not in (int,float) or not np.isfinite(value):raise UserError(label+'须为有限数值')
    return float(value)


def row_bounds(alpha,y):
    xs=np.flatnonzero(alpha[y]>128)
    if len(xs)<4:raise UserError('连接截面没有连续实色图面')
    if np.any(np.diff(xs)>3):raise UserError('连接截面包含分离轮廓，请重新限定部件')
    return float(xs[0]),float(xs[-1])


def fit_pixels(image,record,donor,donor_record,plan):
    if not isinstance(plan.get('reason'),str) or not plan['reason'].strip():raise UserError('连接修复须说明实图依据')
    ox,oy=record['canvas_offset'];dx,dy=donor_record['canvas_offset']
    original=np.array(image.convert('RGBA'));out=original.copy();height,width=original.shape[:2]
    start=finite(plan.get('join_y'),'连接高度')-oy;end=finite(plan.get('release_y'),'过渡结束高度')-oy
    if not (0<=start<end<height and end-start>=4):raise UserError('连接过渡范围无效')
    chain=record.get('chain');root='hip' if chain=='leg' else 'shoulder';distal='knee' if chain=='leg' else 'elbow'
    pins=record.get('canvas_pins',{})
    if chain not in ('arm','leg') or root not in pins or distal not in pins:raise UserError('连接拟合需要已校准的完整臂腿链')
    if end+oy>pins[distal][1]-2 or pins[distal][1]<=pins[root][1]:raise UserError('过渡必须结束于肘/膝之前，当前操作仅支持向下的连接段')
    target=plan.get('target_span')
    if not isinstance(target,list) or len(target)!=2:raise UserError('提供母版坐标连接宽度 target_span')
    left,right=[finite(v,'连接宽度')-ox for v in target]
    if not 0<=left<right<width or right-left<4:raise UserError('连接宽度越界')
    # Keep anatomical root position; mesh weighting/pose must not compensate for
    # a bad image fit. Knee, lower leg, hand/shoe and all pixels below release stay exact.
    pivot=pins[root][0]-ox
    if not left<pivot<right:raise UserError('连接截面须覆盖原关节，不能移动骨骼掩盖接缝')
    j=int(round(start));release=int(round(end));alpha=original[:,:,3]
    source_join=row_bounds(alpha,j)
    xs=np.arange(width,dtype=float)
    ratios=[]
    for y in range(release):
        if not (alpha[y]>128).any():continue
        lo,hi=source_join if y<j else row_bounds(alpha,y)
        t=np.clip((y-start)/(end-start),0,1);smooth=t*t*(3-2*t)
        # A smooth sleeve/trouser envelope bridges the reviewed opening and the
        # untouched distal contour, removing flared installation corners.
        desired=np.array([left,right])*(1-smooth)+np.array([lo,hi])*smooth
        ratio=(desired[1]-desired[0])/(hi-lo)
        if not .45<=ratio<=1.65:raise UserError('连接宽度差异过大，须换同尺度素材或补全，不能强行拉伸')
        ratios.append(float(ratio))
        # Piecewise mapping holds the semantic joint centerline fixed.
        axis=pivot+(pins[distal][0]-ox-pivot)*np.clip((y-(pins[root][1]-oy))/(pins[distal][1]-pins[root][1]),0,1)
        if not lo+1<axis<hi-1 or not desired[0]+1<axis<desired[1]-1:
            mapped=lo+(xs-desired[0])/ratio
        else:
            mapped=np.where(xs<axis,axis+(xs-axis)*(axis-lo)/(axis-desired[0]),axis+(xs-axis)*(hi-axis)/(desired[1]-axis))
        # Premultiplied interpolation prevents introducing new matte fringes.
        source=original[y].astype(float);a=source[:,3]/255;premul=source[:,:3]*a[:,None]
        new_alpha=np.interp(mapped,xs,a,left=0,right=0)
        rgb=np.stack([np.interp(mapped,xs,premul[:,c],left=0,right=0) for c in range(3)],axis=1)
        rgb=np.divide(rgb,new_alpha[:,None],out=np.zeros_like(rgb),where=new_alpha[:,None]>0)
        out[y,:,:3]=np.rint(rgb).clip(0,255).astype('uint8');out[y,:,3]=np.rint(new_alpha*255).astype('uint8')
    texture=plan.get('texture_join')
    color_report=None
    if texture is not None and not isinstance(texture,dict):raise UserError('连接纹理参数须为对象')
    if texture is not None and texture.get('mode','color') not in ('color','shared-pixels'):raise UserError('未知连接纹理模式')
    if texture is not None and texture.get('mode')=='shared-pixels':
        out,color_report=shared_pixels(out,donor,record,donor_record,texture,release)
    elif texture is not None:
        if not isinstance(texture.get('material_note'),str) or not texture['material_note'].strip():raise UserError('纹理接缝须确认同一材质，不能跨肤色、衣领或鞋口取色')
        donor_y=int(round(finite(texture.get('donor_y'),'同材质取样高度')-dy))
        sample_y=int(round(finite(texture.get('target_y'),'目标取样高度')-oy))
        fade_y=int(round(finite(texture.get('release_y'),'色差过渡高度')-oy))
        if not 2<=donor_y<donor.height-2 or not j<=sample_y<fade_y<=release:raise UserError('纹理取样或过渡范围无效')
        donor_arr=np.array(donor.convert('RGBA'));donor_x=np.arange(width)+ox-dx
        region=(xs>=left)&(xs<=right);columns=np.flatnonzero(region)
        if not len(columns) or np.any((donor_x[columns]<0)|(donor_x[columns]>=donor.width)):raise UserError('同材质来源不能覆盖连接宽度')
        source_strip=donor_arr[donor_y-2:donor_y+3,donor_x[columns]]
        target_strip=out[max(0,sample_y-2):sample_y+3,columns]
        valid=(source_strip[:,:,3].min(0)>200)&(target_strip[:,:,3].min(0)>200)
        if valid.sum()<max(4,len(columns)*.65):raise UserError('同材质实色取样不足，不能用背景补接缝')
        good=columns[valid]
        donor_mean=np.median(source_strip[:,:,:3],axis=0)[valid]
        target_mean=np.median(target_strip[:,:,:3],axis=0)[valid]
        delta=donor_mean-target_mean
        if np.median(np.abs(delta))>48:raise UserError('材质色差过大，需同源纹理或补图，不能大幅调色掩盖')
        # Do not propagate a dark outline or highlighted seam as a color cast.
        plausible=np.max(np.abs(delta),axis=1)<=48
        if plausible.sum()<max(4,len(columns)*.65):raise UserError('有效同材质颜色不足，先检查纹理归属')
        # Preserve existing texture variation; only the local low-frequency
        # color offset is matched. Alpha is never faded at a trouser seam.
        # A per-column correction would stretch waistband seams down the thigh
        # into stripes. Match the robust material cast, not individual creases.
        shift=np.broadcast_to(np.median(delta[plausible],axis=0),(width,3))
        before=float(np.abs(delta).mean())
        for y in range(fade_y):
            t=np.clip((y-sample_y)/(fade_y-sample_y),0,1);weight=1-t*t*(3-2*t)
            visible=out[y,:,3]>0
            out[y,visible,:3]=np.rint(out[y,visible,:3].astype(float)+shift[visible]*weight).clip(0,255).astype('uint8')
        after=float(np.abs(np.median(out[max(0,sample_y-2):sample_y+3,good,:3],axis=0)-donor_mean).mean())
        color_report={'before_rgb_mae':before,'after_rgb_mae':after,'donor_y':donor_y+dy,'target_y':sample_y+oy,'alpha_fade':False}
    if not np.array_equal(out[release:],original[release:]):raise AssertionError('Distal pixels changed')
    return Image.fromarray(out),{'changed_pixels':int(np.any(original!=out,axis=2).sum()),'source_span':[v+ox for v in source_join],
        'target_span':target,'release_y':release+oy,'scale_range':[min(ratios,default=1),max(ratios,default=1)],
        'distal_pixels_preserved':True,'pins_preserved':True,'texture_join':color_report}


def shared_pixels(out,donor,record,donor_record,spec,release):
    """Use a real co-registered material patch, with RGB-only interior blending.

    Hidden overlap keeps the target alpha. This cannot fill missing silhouettes.
    Source pixels must cover the whole requested opaque connection band.
    """
    if not isinstance(spec.get('material_note'),str) or not spec['material_note'].strip():raise UserError('共享纹理须确认同一衣料及实际覆盖区域')
    ox,oy=record['canvas_offset'];dx,dy=donor_record['canvas_offset']
    start=int(round(finite(spec.get('start_y'),'补片起点')-oy))
    solid=int(round(finite(spec.get('solid_until_y'),'共享纹理终点')-oy))
    end=int(round(finite(spec.get('release_y'),'补片过渡终点')-oy))
    if not 0<=start<solid<end<=release:raise UserError('共享纹理须限定在关节前的局部范围')
    _,w=out.shape[:2];source=np.array(donor.convert('RGBA'));xx=np.arange(w)+ox-dx
    changed=0
    for y in range(start,end):
        yy=y+oy-dy;visible=out[y,:,3]>0
        if not visible.any():continue
        inside=(xx>=0)&(xx<source.shape[1])
        if not 0<=yy<source.shape[0] or np.any(visible&~inside):raise UserError('共享母版没有完整覆盖连接区，须补全实图')
        cols=np.flatnonzero(visible);patch=source[yy,xx[cols]]
        valid=patch[:,3]>=200
        good=np.flatnonzero(valid)
        # Permit only a narrow outer antialias fringe. Never bridge an interior
        # missing patch or silently accept substantial donor under-coverage.
        if not len(good) or np.any(~valid[good[0]:good[-1]+1]) or (~valid).sum()>3 or valid.mean()<.95:raise UserError(f'共享母版包含透明缺口：y={y+oy}，缺少{int((~valid).sum())}像素；不能把背景复制到腿片')
        cols=cols[valid];patch=patch[valid]
        t=np.clip((y-solid)/(end-solid),0,1);weight=1-t*t*(3-2*t)
        old=out[y,cols,:3].astype(float);new=np.rint(old*(1-weight)+patch[:,:3]*weight).clip(0,255).astype('uint8')
        changed+=int(np.any(new!=old,axis=1).sum());out[y,cols,:3]=new
    return out,{'mode':'shared-pixels','source_pixels_copied':changed,'solid_until_y':solid+oy,'release_y':end+oy,'alpha_fade':False,'limits':'纹理过渡仍须检查；不补缺失轮廓，不代替动态验收'}


def registered_donor(character,spec):
    """Register a reviewed crop of this character's own existing candidate."""
    from .reviewed_parts import extract_parts
    from .part_layers import aligned_part
    from .character import LABELS,segments
    if not isinstance(spec,dict) or not isinstance(spec.get('job_id'),str) or not re.fullmatch('[a-f0-9]{32}',spec['job_id']):raise UserError('共享母版来源须为本角色已有候选')
    path=character.directory/'jobs'/(spec['job_id']+'.png')
    if not path.is_file():raise UserError('共享母版候选不存在')
    blob=path.read_bytes()
    if sha(blob)!=spec.get('sha256'):raise UserError('共享母版候选像素已变化')
    layout=spec.get('layout')
    if not isinstance(layout,dict) or layout.get('name')!='torso' or layout.get('group')!='costume':raise UserError('共享母版须为已标注锚点的完整衣装')
    name,sprite,record=extract_parts(read_image(blob),[layout],LABELS,character.meta.get('settings',{}).get('background',32))[0]
    source_length=np.linalg.norm((np.array(record['start_pin'])-record['end_pin'])*sprite.size)
    guide=character.meta['landmarks'];target_length=np.linalg.norm((np.array(guide['hip'])-guide['neck'])*character.image().size)
    if source_length<1 or max(sprite.size)*target_length/source_length*2**.5>4096:raise UserError('共享母版配准尺寸过大，请校准实际锚点')
    part=aligned_part(name,sprite,record,character.meta['landmarks'],character.image().size,segments)
    return part['image'],{'canvas_offset':part['box'][:2]}


def apply(character,plans):
    if not isinstance(plans,list) or not 1<=len(plans)<=4:raise UserError('连接拟合须为1–4个已确认连接')
    meta=copy.deepcopy(character.meta);seen=set();prepared=[];history=[]
    targets=[p.get('part') for p in plans if isinstance(p,dict)]
    for plan in plans:
        if not isinstance(plan,dict):raise UserError('连接拟合参数无效')
        name=plan.get('part');donor_name=plan.get('donor','torso');layers=meta.get('part_layers',{})
        if not isinstance(name,str) or name in seen or name not in layers or not isinstance(donor_name,str) or donor_name not in layers or name==donor_name:raise UserError('连接部件无效或重复')
        if donor_name in targets:raise UserError('同批连接来源不能同时被修改')
        seen.add(name);record=layers[name];donor_record=layers[donor_name]
        if 'canvas_offset' not in record or 'canvas_offset' not in donor_record:raise UserError('先安装同坐标完整图层')
        if plan.get('source_sha256')!=record['image_id'] or plan.get('donor_sha256')!=donor_record['image_id']:raise UserError('连接源像素已变化，请重看实图后提交')
        if any(meta.get('part_review',{}).get('adjustments',{}).get(n) for n in (name,donor_name)):raise UserError('连接拟合前须将目标校准落实为同坐标图层')
        folder=character.directory/'part-images';source=read_image((folder/(record['image_id']+'.png')).read_bytes());donor=read_image((folder/(donor_record['image_id']+'.png')).read_bytes())
        if 'donor_layout' in plan:donor,donor_record=registered_donor(character,plan['donor_layout'])
        image,report=fit_pixels(source,record,donor,donor_record,plan);blob=png_bytes(image);digest=sha(blob)
        history.append({'part':name,'before_sha256':record['image_id'],'after_sha256':digest,'plan':copy.deepcopy(plan),'report':report})
        record['image_id']=digest;record['connection_fit']={'version':'connection-fit-v1','source_sha256':plan['source_sha256'],'donor_sha256':plan['donor_sha256']}
        prepared.append((digest,blob));meta.get('assembly_inputs',{}).pop(name,None)
    for key in ('prebind_review','overlap_review','reconstruction_review'):meta.pop(key,None)
    meta.setdefault('connection_fit_history',[]).append(history)
    for digest,blob in prepared:(character.directory/'part-images'/(digest+'.png')).write_bytes(blob)
    character.save(meta)
    from .assembly_pipeline import reconcile_overlays
    reconcile_overlays(character)
