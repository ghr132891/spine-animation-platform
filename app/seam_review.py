"""Local rendered seam evidence and bounded, visually scoped pixel reuse."""
import math
import numpy as np
from PIL import Image, ImageDraw
from .core import UserError
from .reviewed_parts import pixel_box


def polygon_mask(size, polygons):
    if not isinstance(polygons, list) or len(polygons) > 8:
        raise UserError('局部修复最多8个多边形')
    mask = Image.new('L', size)
    for polygon in polygons:
        if not isinstance(polygon, list) or not 3 <= len(polygon) <= 128:
            raise UserError('多边形须包含3–128个归一化顶点')
        points = []
        for pair in polygon:
            if not isinstance(pair, list) or len(pair) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in pair):
                raise UserError('多边形顶点须为0–1坐标')
            points.append((round(pair[0]*(size[0]-1)), round(pair[1]*(size[1]-1))))
        ImageDraw.Draw(mask).polygon(points, fill=255)
    return np.array(mask) > 0


def repair_texture(image, data, resolve_source):
    """Never infer missing art: clean selected matte or copy existing pixels."""
    if not isinstance(data.get('reason'), str) or not data['reason'].strip():
        raise UserError('局部修复须记录实际看图原因')
    original = np.array(image.convert('RGBA')); result = original.copy()
    regions = data.get('white_regions', [])
    selected = polygon_mask(image.size, regions)
    rgb = result[:, :, :3].astype(int)
    white = selected & (rgb.min(2) >= 222) & (rgb.max(2)-rgb.min(2) <= 20)
    result[white] = 0
    canvas = Image.fromarray(result, 'RGBA'); sources = []
    copies = data.get('copies', [])
    if not isinstance(copies, list) or len(copies) > 8:
        raise UserError('局部修复最多复用8个裁片')
    for item in copies:
        if not isinstance(item, dict):raise UserError('复用裁片须为对象')
        if bool(item.get('source_part'))==bool(item.get('source_material')):raise UserError('复用源须选source_part或source_material其中一个')
        source, digest = resolve_source(item)
        box = pixel_box(item.get('box'), source.size)
        dest = pixel_box(item.get('dest'), image.size)
        patch = source.crop(box).convert('RGBA')
        if 'crop_polygon' in item:
            keep = polygon_mask(patch.size, [item['crop_polygon']])
            a = np.array(patch); a[~keep] = 0; patch = Image.fromarray(a, 'RGBA')
        patch = patch.resize((dest[2]-dest[0],dest[3]-dest[1]), Image.Resampling.LANCZOS)
        canvas.alpha_composite(patch, tuple(dest[:2]))
        sources.append({'sha256': digest, 'box': box, 'dest': dest})
    # Reused opaque reference crops may bring their white matte with them.
    # The same explicitly reviewed target regions apply after composition.
    after = np.array(canvas);rgb=after[:,:,:3].astype(int)
    copied_white=selected & (rgb.min(2)>=222) & (rgb.max(2)-rgb.min(2)<=20)
    removed_white=int(((white & (original[:,:,3]>0)) | (copied_white & (after[:,:,3]>0))).sum())
    after[copied_white]=0;canvas=Image.fromarray(after,'RGBA');changed = np.any(after != original, axis=2)
    fraction = float(changed.mean())
    if fraction > .25:raise UserError('局部修复改变超过25%贴图，请改用独立完整补片并重新绑定')
    return canvas, {'changed_pixels': int(changed.sum()), 'changed_fraction': fraction,
                    'removed_white_pixels': removed_white,
                    'sources': sources, 'reason': data['reason'].strip()[:1000]}


def white_candidates(image, roi):
    """Neutral opaque white is a suspect, never a semantic failure/pass."""
    a = np.array(image.convert('RGB')); x0,y0,x1,y1 = roi
    rgb = a[y0:y1,x0:x1].astype(int)
    selected = (rgb.min(2) >= 242) & (rgb.max(2)-rgb.min(2) <= 15)
    seen = np.zeros(selected.shape, dtype=bool); components = []
    for y,x in zip(*np.where(selected)):
        if seen[y,x]:continue
        pending = [(y,x)]; seen[y,x]=True; points=[]
        while pending:
            cy,cx=pending.pop();points.append((cx,cy))
            for ny,nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                if 0<=ny<selected.shape[0] and 0<=nx<selected.shape[1] and selected[ny,nx] and not seen[ny,nx]:
                    seen[ny,nx]=True;pending.append((ny,nx))
        if len(points)>=6:
            p=np.array(points);lo=p.min(0);hi=p.max(0)+1
            components.append({'pixels':len(points),'box':[int(lo[0]+x0),int(lo[1]+y0),int(hi[0]+x0),int(hi[1]+y0)],
                               'rectangle_fill':round(len(points)/int(np.prod(hi-lo)),3)})
    return components


def render_closeups(frames, directory, anchor, render, isolated=False):
    if isolated:
        slots={anchor}
        if '-upper-' in anchor:
            side,kind=anchor.split('-upper-');slots.update([side+'-lower-'+kind,side+('-foot' if kind=='leg' else '-hand')])
        if anchor=='__hair__':slots={'head','back'}
        if anchor=='__hip__':slots={'torso','pelvis','l-upper-leg','r-upper-leg'}
        if anchor=='__back__':slots={'back','torso'}
        filtered=[{**f,'geometry':[g for g in f['geometry'] if g['slot'] in slots]} for f in frames]
        if any(not f['geometry'] for f in filtered):raise UserError('此 anchor 没有可隔离的同名附件，请使用组合图诊断')
        frames=filtered
    rows=(len(frames)+3)//4
    contrast=anchor in ('__hip__','__back__','torso','l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg')
    sheet=Image.new('RGB',(1600,400*(rows+int(contrast))),'#e5ebf3');draw=ImageDraw.Draw(sheet)
    evidence=[]
    for i,frame in enumerate(frames):
        bone_name={'__hair__':'head','__hip__':'hip','__back__':'torso'}.get(anchor,anchor)
        bone=next((b for b in frame['bones'] if b['name']==bone_name),None)
        if bone is None:raise UserError('找不到局部参考骨骼，请按真实骨架提供 anchor')
        length=math.hypot(bone['ex']-bone['x'],bone['ey']-bone['y'])
        x,y=bone['x'],bone['y'];view=[x-length*.65,y-length*.38,x+length*.65,y+length*.92]
        extent_source='bone-length'
        if length<1 and anchor!='__hip__':
            geometry=[g for g in frame['geometry'] if g['slot']==('head' if anchor=='__hair__' else anchor) or g.get('path')==anchor]
            if not geometry:raise UserError('零长度参考骨没有对应附件，请选择实际头颈骨骼')
            xy=np.array([v for g in geometry for v in g['xy']]).reshape(-1,2);lo=xy.min(0);hi=xy.max(0)
            length=float(max(hi[1]-lo[1],hi[0]-lo[0])*.55)
            if length<1 and anchor!='__hip__':raise UserError('头颈附件无可渲染范围')
            x=float((lo[0]+hi[0])/2);y=float(lo[1]+(hi[1]-lo[1])*.12)
            view=[x-length*.65,y-length*.38,x+length*.65,y+length*.92];extent_source='actual-attachment-bounds'
        local_view=detail_view(frame,anchor)
        if local_view is not None:view=local_view;extent_source='animated-chain-or-artwork-bounds'
        tile=render(frame,directory,view,(400,400),0)
        # This zone is below the face and above the collar. White costume/hair
        # can still occur here; Codex must decide from the actual image.
        roi=[46,145,354,295];suspects=white_candidates(tile,roi)
        evidence.append({'frame':i,'anchor_world':[x,y],'view':view,'extent_source':extent_source,'white_candidates':suspects})
        sheet.paste(tile,(i%4*400,i//4*400));draw.text((i%4*400+8,i//4*400+5),str(i),fill='#325080')
    if contrast:
        # Two actual poses on light/dark grounds reveal holes and sheer overlap.
        for index,(frame_id,bg) in enumerate([(0,(250,250,250,255)),(0,(36,43,55,255)),(len(frames)//4,(250,250,250,255)),(len(frames)//4,(36,43,55,255))]):
            frame=frames[frame_id];view=evidence[frame_id]['view']
            tile=render(frame,directory,view,(400,400),0,background=bg)
            sheet.paste(tile,(index*400,rows*400))
            draw.text((index*400+8,rows*400+5),f'{frame_id} / '+('light' if index%2==0 else 'dark'),fill='#5588cc')
    return sheet, {'isolated':isolated,'material_contrast':{'included':contrast,'poses':[0,len(frames)//4] if contrast else [],'checks':['edge-halo','alpha-holes','sheer-material','layer-overlap'] if contrast else []},'anchor':anchor,'frames':len(frames),'white_candidate_frames':sum(bool(f['white_candidates']) for f in evidence),
                   'samples':evidence,'scope':'白色区域仅为疑似残底；结合三视图检查关节定位、弯曲方向、头发/衣裙遮挡与接缝，不能数值自动验收或自动删除白色衣饰。浅/深底对照检查肩缘残片、裙缝连续性、透明层叠加和原图材质；不把纯白候选为零视为通过。'}


def required_detail_anchors(skeleton):
    names={b['name'] for b in skeleton.get('bones',[])}
    return [n for n in ('head','torso','l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg') if n in names]+(['__hair__'] if 'head' in names else [])


def require_detail_reviews(review,digest,clips,anchors):
    if review.get('sha256')!=digest:raise UserError('肩臂、腿膝及头发局部检查已失效，须重看当前资源')
    missing=[clip+':'+anchor for clip in clips for anchor in anchors
             if review.get('regions',{}).get(clip,{}).get(anchor,{}).get('review',{}).get('verdict')!='ready']
    if missing:raise UserError('仍需放大比对并验收：'+', '.join(missing))


def detail_view(frame,anchor):
    """Use actual animated geometry, including chain endpoints, not fixed screen boxes."""
    if anchor=='__hip__':
        bones={b['name']:b for b in frame['bones']};hip=bones.get('hip')
        if not hip:raise UserError('找不到髋部参考骨骼')
        thighs=[b for n,b in bones.items() if n in ('l-upper-leg','r-upper-leg')]
        radius=max([math.hypot(b['ex']-b['x'],b['ey']-b['y'])*.6 for b in thighs]+[12.])
        return [hip['x']-radius,hip['y']-radius*.75,hip['x']+radius,hip['y']+radius*.4]
    if anchor=='__back__':
        geometry=[g for g in frame['geometry'] if g['slot']=='back']
        # A declared integrated backpack uses the complete costume geometry.
        # Connection anchors schedule this only for characters carrying that art.
        if not geometry:geometry=[g for g in frame['geometry'] if g['slot']=='torso']
        if not geometry:raise UserError('找不到背部配件或其完整衣装图层')
        xy=np.array([v for g in geometry for v in g['xy']]).reshape(-1,2)
        lo=xy.min(0);hi=xy.max(0);margin=max(float(max(hi-lo))*.25,5)
        return [float(lo[0]-margin),float(lo[1]-margin),float(hi[0]+margin),float(hi[1]+margin)]
    if anchor=='__hair__':
        geometry=[g for g in frame['geometry'] if g['slot'] in ('head','back') or 'hair' in g.get('path','')]
        if not geometry:raise UserError('找不到头发相关图面')
        xy=np.array([v for g in geometry for v in g['xy']]).reshape(-1,2)
    else:
        if anchor=='torso':names={'torso','l-upper-arm','r-upper-arm','head'}
        elif '-upper-' in anchor:
            prefix,kind=anchor.split('-upper-');names={anchor,prefix+'-lower-'+kind,prefix+('-hand' if kind=='arm' else '-foot')}
        else:return None
        bones=[b for b in frame['bones'] if b['name'] in names]
        if not any(b['name']==anchor for b in bones):raise UserError('找不到局部参考骨骼')
        xy=np.array([[b['x'],b['y']] for b in bones]+[[b['ex'],b['ey']] for b in bones])
    lo=xy.min(0);hi=xy.max(0);extent=max(float(max(hi-lo)),1);margin=extent*.18
    return [float(lo[0]-margin),float(lo[1]-margin),float(hi[0]+margin),float(hi[1]+margin)]
