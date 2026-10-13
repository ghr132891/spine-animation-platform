"""Fit inspected Matrix sheets and build an isolated animation candidate."""
import sys,json,io,uuid,zipfile
import numpy as np
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.core import png_bytes,read_image,atomic_json
from app.part_layers import aligned_part
from app.character import segments
folder=ROOT/'evidence/single-image/complete-parts';source=CharacterProject(ROOT/'storage/characters/76a6aa6972a4466a976f798b115bac65')
if not (folder/'before-guide.json').exists():atomic_json(folder/'before-guide.json',source.meta)
storage=folder/'qa-storage';c=CharacterProject.create(storage,'完整部件装配验证');c.set_image(png_bytes(source.image()),0)
guide=json.loads((ROOT/'evidence/single-image/parts-fix/candidate.json').read_text())['version']
c.update({'revision':c.meta['revision'],'landmarks':source.meta['landmarks'],'masks':guide['masks'],'settings':source.meta['settings']})
for sheet in ('body','arms','legs','forearms'):
    jobid=json.loads((folder/(sheet+'.json')).read_text())['job_id'];job=json.loads((source.directory/'jobs'/(jobid+'.json')).read_text())
    c.apply_part_sheet({'revision':c.meta['revision'],'mode':'redraw'},{**job,'source_image_id':c.meta['image_id']},read_image((folder/(sheet+'.png')).read_bytes()))
# The completed chest's shoulder socket is now visible. Match its center rather
# than reusing the old shoulder on the partly hidden source chest.
record=c.meta['part_layers']['torso'];sprite=read_image((c.directory/'part-images'/(record['image_id']+'.png')).read_bytes())
placed=aligned_part('torso',sprite,record,c.meta['landmarks'],c.image().size,segments)
mount=np.array(placed['placement']['matrix'])@([sprite.width*.205,sprite.height*.315])+placed['placement']['offset']
landmarks=c.meta['landmarks'].copy();landmarks['l-shoulder']=(mount/np.array(c.image().size)).tolist()
c.update({'revision':c.meta['revision'],'landmarks':landmarks,'masks':c.meta['masks'],'settings':c.meta['settings']})
c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':c.meta['landmarks'],'masks':c.meta['masks'],'settings':c.meta['settings'],'label':'亿创完整部件 · 行走'})
version=c.meta['versions'][-1]
with zipfile.ZipFile(io.BytesIO(c.export(storage,version['id']))) as z:z.extractall(folder/'export')
atomic_json(folder/'candidate.json',{'id':c.id,'source_revision':source.meta['revision'],'source_settings':source.meta['settings'],'version':version})
print(json.dumps({'built':True,'complete_parts':len(version['part_layers']),'source_revision':source.meta['revision']}))
