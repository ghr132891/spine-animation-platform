"""Save the inspected complete sprites and rig as a new user history version."""
import json,uuid,urllib.request,io,zipfile,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core import atomic_json
folder=ROOT/'evidence/single-image/complete-parts';id='76a6aa6972a4466a976f798b115bac65';base='http://127.0.0.1:8780'
def api(route,data=None):
    request=urllib.request.Request(base+route,data=None if data is None else json.dumps(data).encode(),headers={'X-Spine-Local':'1','Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=90) as r:return json.load(r)
prefix='/api/characters/'+id;candidate=json.loads((folder/'candidate.json').read_text());guide=candidate['version'];pointer=folder/'published.json';c=api(prefix)
if not pointer.exists():
    assert c['revision']==candidate['source_revision'],'Current guide changed; inspect it again before publishing'
    atomic_json(pointer,{'nonce':uuid.uuid4().hex,'before_revision':c['revision'],'last_revision':c['revision'],'before_versions':[v['id'] for v in c['versions']],'applied_sheets':[]})
record=json.loads(pointer.read_text())
if not any(v['id']==record['nonce'] for v in c['versions']):
    assert c['revision']==record['last_revision'],'Current guide changed during publish; inspect it again'
    for sheet in ('body','arms','legs','forearms'):
        if sheet in record['applied_sheets']:continue
        job_id=json.loads((folder/(sheet+'.json')).read_text())['job_id'];c=api(prefix+'/apply-part-sheet',{'revision':c['revision'],'job_id':job_id})
        record['applied_sheets'].append(sheet);record['last_revision']=c['revision'];atomic_json(pointer,record)
    c=api(prefix+'/guide',{'revision':c['revision'],'landmarks':guide['landmarks'],'masks':guide['masks'],'settings':guide['settings']})
    record['last_revision']=c['revision'];atomic_json(pointer,record)
    c=api(prefix+'/build',{'revision':c['revision'],'nonce':record['nonce'],'landmarks':guide['landmarks'],'masks':guide['masks'],'settings':guide['settings'],'label':'亿创完整部件 · 遮挡补全行走'})
version=next(v for v in c['versions'] if v['id']==record['nonce']);assert all(any(v['id']==old for v in c['versions']) for old in record['before_versions'])
with urllib.request.urlopen(base+prefix+'/export?version='+version['id'],timeout=90) as r:content=r.read()
(folder/'character-complete.zip').write_bytes(content)
with zipfile.ZipFile(io.BytesIO(content)) as z:z.extractall(folder/'published-export')
paths=['skeleton.json']+['images/'+p.name for p in (folder/'export/images').glob('*.png')]
matches=all(hashlib.sha256((folder/'export'/p).read_bytes()).digest()==hashlib.sha256((folder/'published-export'/p).read_bytes()).digest() for p in paths);assert matches,'Published rig or part art differs from inspected candidate'
record.update(version=version,after_revision=c['revision'],history_preserved=True,matches_verified_candidate=matches);atomic_json(pointer,record)
entrypath=ROOT/'evidence/single-image/example.json';entry=json.loads(entrypath.read_text())
if not (folder/'original-example.json').exists():atomic_json(folder/'original-example.json',entry)
entry.update(version=version['id'],rig_id=version['rig_id'],source_id=version['source_id']);atomic_json(entrypath,entry)
print(json.dumps({'saved':True,'version':version['label'],'revision':c['revision'],'complete_parts':len(version['part_layers']),'history':len(c['versions']),'matches_verified_candidate':matches},ensure_ascii=False))
