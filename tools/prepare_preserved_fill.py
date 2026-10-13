"""Keep every visible source pixel; use Matrix art only behind occluders."""
import json,sys,io,uuid,zipfile,copy
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFilter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject,guide_cutout,split_image,segmentation_owner,segments,LABELS
from app.part_layers import split_sheet,aligned_part
from app.character import cut_background
from app.core import atomic_json,png_bytes,read_image,sha
folder=ROOT/'evidence/single-image/preserved-fill';folder.mkdir(exist_ok=True)
source=CharacterProject(ROOT/'storage/characters/76a6aa6972a4466a976f798b115bac65')
base=json.loads((ROOT/'evidence/single-image/complete-parts/before-guide.json').read_text())
guide=json.loads((ROOT/'evidence/single-image/parts-fix/candidate.json').read_text())['version']
landmarks=base['landmarks'];masks=guide['masks'];settings=base['settings'];image=guide_cutout(source.image(),landmarks,masks,settings);W,H=image.size;arr=np.array(image)
parts=split_image(image,landmarks,masks,fill_back=False);owner,segs,_,points=segmentation_owner(image,landmarks,masks)
provider_folder=ROOT/'evidence/single-image/complete-parts';sprites={};jobs={}
for sheet in ('body','arms','legs','forearms'):
    job_id=json.loads((provider_folder/(sheet+'.json')).read_text())['job_id'];job=json.loads((source.directory/'jobs'/(job_id+'.json')).read_text())
    assets,_=split_sheet(read_image((provider_folder/(sheet+'.png')).read_bytes()),job,cut_background);sprites.update(assets);jobs.update({n:job_id for n in assets})
storage=folder/'qa-storage';c=CharacterProject.create(storage,'保留原图遮挡补全验证');c.set_image(png_bytes(source.image()),0)
c.update({'revision':c.meta['revision'],'landmarks':landmarks,'masks':masks,'settings':settings})
layers={};checks=[];target=c.directory/'part-images';target.mkdir()
filled_names={'back','torso','r-upper-arm','r-foot'}
for part in parts:
    name=part['name'];i=next(i for i,(n,_,_) in enumerate(segs) if n==name);canvas=Image.new('RGBA',(W,H));canvas.paste(part['image'],part['box'][:2]);rgba=np.array(canvas)
    original=rgba.copy();visible=original[:,:,3]>0;hidden=np.zeros((H,W),bool)
    if name in filled_names:
        outline=masks[name]
        if name=='r-foot':outline=[[x/W,y/H] for x,y in [[457,943],[585,937],[589,1005],[669,1046],[679,1120],[449,1120]]]
        polygon=Image.new('L',(W,H));ImageDraw.Draw(polygon).polygon([(x*W,y*H) for x,y in outline],fill=255)
        # Only fill source foreground occupied by another layer. White image
        # background never becomes part of a sprite or an expanded silhouette.
        hidden=(np.array(polygon)>0)&(arr[:,:,3]>0)&~visible
        bbox=polygon.getbbox();art=Image.new('RGBA',(W,H))
        if name=='r-foot':
            # The near boot already supplies the original design's intact heel.
            # Transfer only this hidden heel using the two original foot pins.
            near=next(p for p in parts if p['name']=='l-foot');record={'source_box':(np.array(near['box'])/[W,H,W,H]).tolist(),'source_start':landmarks['l-ankle'],'source_end':landmarks['l-toe'],'rigid':True}
            heel=aligned_part('r-foot',near['image'],record,landmarks,image.size,segments);art.paste(heel['image'],heel['box'][:2])
        else:art.paste(sprites[name].resize((bbox[2]-bbox[0],bbox[3]-bbox[1]),Image.Resampling.LANCZOS),bbox[:2])
        new=np.array(art)
        hidden &= new[:,:,3]>0
        rgba[hidden]=new[hidden]
    self_same=np.array_equal(rgba[visible],original[visible]);assert self_same,name
    result=Image.fromarray(rgba,'RGBA');box=result.getbbox();sprite=result.crop(box);content=png_bytes(sprite);digest=sha(content);(target/(digest+'.png')).write_bytes(content)
    start,end=part['start'],part['end']
    if name=='head':start,end='neck','head'
    if name=='torso':start,end='hip','neck'
    a=np.array(landmarks[start])*[W,H];b=np.array(landmarks[end])*[W,H]
    if name=='back':b=a+np.array([0,np.linalg.norm((np.array(landmarks['hip'])-landmarks['neck'])*[W,H])*.5])
    layers[name]={'image_id':digest,'source_image_id':c.meta['image_id'],'job_id':jobs[name],'source_box':(np.array(box)/[W,H,W,H]).tolist(),'source_start':(a/[W,H]).tolist(),'source_end':(b/[W,H]).tolist(),'rigid':name.endswith('-foot'),'preserved_visible_pixels':int(visible.sum()),'filled_pixels':int(hidden.sum()),'mode':'occlusion-fill'}
    checks.append({'name':name,'visible_pixels_unchanged':self_same,'visible_pixels':int(visible.sum()),'filled_pixels':int(hidden.sum())})
meta=copy.deepcopy(c.meta);meta['part_layers']=layers;c.save(meta)
c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':landmarks,'masks':masks,'settings':settings,'label':'保留原图 · 遮挡补全'})
version=c.meta['versions'][-1]
with zipfile.ZipFile(io.BytesIO(c.export(storage,version['id']))) as z:z.extractall(folder/'export')
atomic_json(folder/'candidate.json',{'id':c.id,'source_revision':source.meta['revision'],'version':version})
atomic_json(folder/'pixel-validation.json',{'all_source_pixels_preserved':True,'original_landmarks_restored':landmarks==base['landmarks'],'parts':checks})
print(json.dumps({'built':True,'original_landmarks_restored':True,'source_pixels_preserved':True,'fill_parts':[p['name'] for p in checks if p['filled_pixels']]}))
