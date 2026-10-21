"""Content-bound magnified edge evidence; candidates never change artwork."""
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFilter
from .core import atomic_json,png_bytes,read_image,sha
from .material_finish import decontaminate_edge


def candidates(image):
    a=np.asarray(image.convert('RGBA'));alpha=a[:,:,3];rgb=a[:,:,:3].astype(float)
    if alpha.size>1_000_000:return {'status':'not_assessed','reason':'large_texture','points':[]}
    trial=decontaminate_edge(a[:,:,:3],alpha,np.ones(alpha.shape),
        {'radius':3,'min_lift':18,'reference_note':'diagnostic only'})
    score=np.maximum(0,(rgb-trial).mean(2))*alpha/255
    h,w=alpha.shape;summed=np.zeros_like(rgb);count=np.zeros((h,w))
    for dy,dx in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
        y0,y1=max(0,-dy),min(h,h-dy);x0,x1=max(0,-dx),min(w,w-dx)
        target=(slice(y0,y1),slice(x0,x1));source=(slice(y0+dy,y1+dy),slice(x0+dx,x1+dx))
        visible=alpha[source]>=200
        summed[target]+=rgb[source]*visible[:,:,None];count[target]+=visible
    mean=summed/np.maximum(count,1)[:,:,None]
    exterior=np.asarray(Image.fromarray(((alpha==0)*255).astype('uint8')).filter(ImageFilter.MaxFilter(7)))>0
    opaque=(alpha==255)&exterior&(count>=4)&(rgb.min(2)>190)&(rgb.max(2)-rgb.min(2)<30)&(mean.max(2)<150)&((rgb-mean).min(2)>65)
    score[opaque]+=300
    points=[]
    for _ in range(3):
        y,x=np.unravel_index(int(score.argmax()),score.shape)
        if score[y,x]<8:break
        points.append({'pixel':[int(x),int(y)],'rgba':a[y,x].tolist(),
                       'kind':'opaque_light_spot' if opaque[y,x] else 'partial_alpha_fringe'})
        score[max(0,y-8):y+9,max(0,x-8):x+9]=0
    return {'status':'candidates' if points else 'no_candidates','points':points,
            'opaque_candidate_pixels':int(opaque.sum()),'limits':'亮边可能是原有高光；须看实图，不自动去白。'}


def prepare(character):
    with character.lock:
        records=character.meta.get('part_layers',{})
        key=sha(json.dumps([sha(Path(__file__).read_bytes()),sha(Path(__file__).with_name('material_finish.py').read_bytes()),
                           {name:r['image_id'] for name,r in records.items()}],sort_keys=True).encode())
        folder=character.directory/'edge-review-cache'/key
        try:
            saved=json.loads((folder/'manifest.json').read_text());blob=(folder/'edges.png').read_bytes()
            if saved['png_sha256']==sha(blob):return saved['report'],read_image(blob).convert('RGB')
        except (OSError,ValueError,KeyError):pass
        parts=[];patches=[]
        for name,record in records.items():
            blob=(character.directory/'part-images'/(record['image_id']+'.png')).read_bytes();image=read_image(blob)
            result={'part':name,'source_sha256':sha(blob),**candidates(image)};parts.append(result)
            for point in result['points']:
                x,y=point['pixel'];crop=image.crop((x-14,y-14,x+14,y+14)).resize((112,112),Image.Resampling.NEAREST)
                tile=Image.new('RGB',(270,160),'white');tile.paste((35,43,55),(135,0,270,160))
                tile.paste(crop,(10,35),crop);tile.paste(crop,(145,35),crop)
                draw=ImageDraw.Draw(tile);draw.text((5,5),name,fill=(30,50,80));draw.text((140,5),f'{x},{y} / 4x',fill='white')
                # Mark the inspected pixel without painting over it.
                for shift in (10,145):draw.rectangle((shift+54,89,shift+59,94),outline=(230,90,50))
                patches.append(tile)
        sheet=Image.new('RGB',(810,max(160,160*((len(patches)+2)//3))),(228,235,243))
        for i,tile in enumerate(patches):sheet.paste(tile,((i%3)*270,(i//3)*160))
        fully_assessed=all(p['status']!='not_assessed' for p in parts)
        if not patches:ImageDraw.Draw(sheet).text((15,25),'No high-contrast candidates. Visual review is still required.' if fully_assessed else 'Some textures need manual inspection; automatic scan is incomplete.',fill=(30,50,80))
        report={'contract':'edge-evidence-v1','digest':key,'parts':parts,'candidate_count':len(patches),
                'fully_assessed':fully_assessed,'visual_verdict':'not_reviewed','pixels_modified':False,'coordinate_system':'stored-part-pixels'}
        folder.mkdir(parents=True,exist_ok=True);blob=png_bytes(sheet);temporary=folder/'edges.png.tmp';temporary.write_bytes(blob);temporary.replace(folder/'edges.png')
        atomic_json(folder/'manifest.json',{'report':report,'png_sha256':sha(blob)})
        return report,sheet
