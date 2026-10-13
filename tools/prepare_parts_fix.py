"""Repair the sample's known outlines; preserve independently edited guides."""
import sys,json,uuid,io,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.core import png_bytes,atomic_json
folder=ROOT/'evidence/single-image/parts-fix';folder.mkdir(exist_ok=True)
source=CharacterProject(ROOT/'storage/characters/76a6aa6972a4466a976f798b115bac65')
original=folder/'before-guide.json'
if not original.exists():atomic_json(original,source.meta)
masks=source.meta['masks'].copy()
known=json.loads((ROOT/'evidence/single-image/adhesion-edge-fix/candidate.json').read_text())['version']['masks']
replacement={
 'head':[[300,70],[625,70],[625,350],[601,380],[475,390],[417,372],[382,356],[342,315],[300,285]],
 'back':[[203,334],[399,334],[399,592],[217,592]],
 'torso':[[384,360],[462,373],[521,372],[551,396],[574,439],[573,516],[554,553],[551,591],[389,591],[374,547],[354,488],[330,419],[348,395]],
 'l-upper-arm':[[333,381],[368,383],[394,400],[407,427],[404,448],[393,464],[373,473],[352,476],[330,516],[320,542],[263,522],[245,512],[274,467],[291,451],[300,413],[311,395]],
 'l-lower-arm':[[246,516],[286,516],[310,536],[329,548],[330,574],[307,592],[318,616],[316,697],[293,707],[265,716],[218,708],[207,686],[207,598],[220,573],[231,540]],
}
for name,points in replacement.items():
    if masks.get(name)==known.get(name):masks[name]=[[x/896,y/1200] for x,y in points]
storage=folder/'qa-storage';c=CharacterProject.create(storage,'拆分边界验证')
c.set_image(png_bytes(source.image()),0)
c.update({'revision':c.meta['revision'],'landmarks':source.meta['landmarks'],'masks':masks,'settings':source.meta['settings']})
c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':c.meta['landmarks'],'masks':masks,'settings':c.meta['settings']})
version=c.meta['versions'][-1]
with zipfile.ZipFile(io.BytesIO(c.export(storage,version['id']))) as z:z.extractall(folder/'export')
atomic_json(folder/'candidate.json',{'id':c.id,'source_revision':source.meta['revision'],'source_settings':source.meta['settings'],'version':version})
(folder/'cutout.png').write_bytes(png_bytes(c.cutout()))
print(json.dumps({'built':True,'source_revision':source.meta['revision'],'refined':[n for n in replacement if masks[n]!=source.meta['masks'][n]]}))
