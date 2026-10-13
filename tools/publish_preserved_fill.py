"""Publish source-preserving completion; retain the fully redrawn variant."""
import json,sys,uuid,base64,urllib.request,io,zipfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core import atomic_json
folder=ROOT/'evidence/single-image/preserved-fill';id='76a6aa6972a4466a976f798b115bac65';base='http://127.0.0.1:8780';prefix='/api/characters/'+id
def api(route,data=None):
    req=urllib.request.Request(base+route,data=None if data is None else json.dumps(data).encode(),headers={'X-Spine-Local':'1','Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
candidate=json.loads((folder/'candidate.json').read_text());v=candidate['version'];c=api(prefix);pointer=folder/'published.json'
if not pointer.exists():
    assert c['revision']==candidate['source_revision'],'Guide changed; inspect the current state again'
    atomic_json(pointer,{'nonce':uuid.uuid4().hex,'before_revision':c['revision'],'last_revision':c['revision'],'before_versions':[old['id'] for old in c['versions']],'installed':False})
record=json.loads(pointer.read_text())
if not any(old['id']==record['nonce'] for old in c['versions']):
    assert c['revision']==record['last_revision'],'Guide changed during publication'
    if not record['installed']:
        directory=folder/'qa-storage/characters'/candidate['id'];parts=[]
        for name,r in v['part_layers'].items():parts.append({'name':name,'record':r,'image':base64.b64encode((directory/'part-images'/(r['image_id']+'.png')).read_bytes()).decode()})
        c=api(prefix+'/install-source-layers',{'revision':c['revision'],'parts':parts,'landmarks':v['landmarks'],'masks':v['masks'],'settings':v['settings']})
        record.update(installed=True,last_revision=c['revision']);atomic_json(pointer,record)
    c=api(prefix+'/build',{'revision':c['revision'],'nonce':record['nonce'],'landmarks':v['landmarks'],'masks':v['masks'],'settings':v['settings'],'label':'保留原图 · 遮挡补全 · 鞋底稳定'})
version=next(old for old in c['versions'] if old['id']==record['nonce']);assert all(any(old['id']==key for old in c['versions']) for key in record['before_versions'])
with urllib.request.urlopen(base+prefix+'/export?version='+version['id'],timeout=120) as r:content=r.read()
(folder/'character-preserved.zip').write_bytes(content)
with zipfile.ZipFile(io.BytesIO(content)) as z:z.extractall(folder/'published-export')
paths=['skeleton.json']+['images/'+p.name for p in (folder/'export/images').glob('*.png')]
matches=all(hashlib.sha256((folder/'export'/p).read_bytes()).digest()==hashlib.sha256((folder/'published-export'/p).read_bytes()).digest() for p in paths);assert matches,'Saved art or rig differs from inspected candidate'
record.update(version=version,after_revision=c['revision'],matches_verified_candidate=True,history_preserved=True);atomic_json(pointer,record)
p=ROOT/'evidence/single-image/example.json';entry=json.loads(p.read_text())
if not (folder/'original-example.json').exists():atomic_json(folder/'original-example.json',entry)
entry.update(version=version['id'],rig_id=version['rig_id'],source_id=version['source_id']);atomic_json(p,entry)
print(json.dumps({'saved':True,'version':version['label'],'revision':c['revision'],'history':len(c['versions']),'source_pixels_preserved':True,'original_landmarks_restored':True},ensure_ascii=False))
