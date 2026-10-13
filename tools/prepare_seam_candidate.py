"""Build a repair from the current guide without altering the user's project."""
import sys,json,uuid,io,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.core import png_bytes,atomic_json
folder=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'evidence/single-image/seam-fix';folder.mkdir(exist_ok=True)
source=CharacterProject(ROOT/'storage/characters/76a6aa6972a4466a976f798b115bac65')
storage=folder/'qa-storage';c=CharacterProject.create(storage,'关节连续蒙皮验证')
c.set_image(png_bytes(source.image()),0);c.update({'revision':c.meta['revision'],'landmarks':source.meta['landmarks'],'masks':source.meta['masks'],'settings':source.meta['settings']})
if len(sys.argv)>1:
    old=[[x/896,y/1200] for x,y in [[313,955],[439,944],[444,1006],[517,1057],[521,1127],[299,1127]]]
    if c.meta['masks'].get('l-foot')==old:
        masks=c.meta['masks'].copy();masks['l-foot']=[[x/896,y/1200] for x,y in json.loads((ROOT/'evidence/single-image/example-masks.json').read_text())['l-foot']]
        c.update({'revision':c.meta['revision'],'landmarks':c.meta['landmarks'],'masks':masks,'settings':c.meta['settings']})
    old_right=[[x/896,y/1200] for x,y in [[457,943],[585,937],[589,1005],[669,1046],[679,1120],[449,1120]]]
    if c.meta['masks'].get('r-foot')==old_right:
        masks=c.meta['masks'].copy()
        masks['r-foot']=[[x/896,y/1200] for x,y in [[457,943],[585,937],[589,1005],[669,1046],[679,1120],[565,1120],[518,1095],[478,1070],[449,1054]]]
        c.update({'revision':c.meta['revision'],'landmarks':c.meta['landmarks'],'masks':masks,'settings':c.meta['settings']})
c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':c.meta['landmarks'],'masks':c.meta['masks'],'settings':c.meta['settings']})
version=c.meta['versions'][-1]
with zipfile.ZipFile(io.BytesIO(c.export(storage,version['id']))) as z:z.extractall(folder/'export')
atomic_json(folder/'candidate.json',{'id':c.id,'source_revision':source.meta['revision'],'source_settings':source.meta['settings'],'version':version})
(folder/'cutout.png').write_bytes(png_bytes(c.cutout()))
print(json.dumps({'built':True,'source_revision':source.meta['revision'],'settings':source.meta['settings']},ensure_ascii=False))
