"""Save one repaired version through the live API, retaining all user history."""
import json,uuid,urllib.request,io,zipfile,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core import atomic_json
folder=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'evidence/single-image/seam-fix';pointer=folder/'published.json';base='http://127.0.0.1:8780';id='76a6aa6972a4466a976f798b115bac65'
def api(path,data=None):
 req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={'X-Spine-Local':'1','Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)
c=api('/api/characters/'+id)
if not pointer.exists():atomic_json(pointer,{'character':id,'nonce':uuid.uuid4().hex,'before_revision':c['revision'],'before_versions':[v['id'] for v in c['versions']]})
record=json.loads(pointer.read_text());nonce=record['nonce']
guide=c
if len(sys.argv)>1:
    candidate=json.loads((folder/'candidate.json').read_text())
    if not any(v['id']==nonce for v in c['versions']):assert candidate['source_revision']==c['revision'],'用户参数已更新，请重新验证当前参数'
    guide=candidate['version']
result=api('/api/characters/'+id+'/build',{'revision':c['revision'],'nonce':nonce,'landmarks':guide['landmarks'],'masks':guide['masks'],'settings':guide['settings'],'label':'行走修正版 · 分肢蒙皮' if len(sys.argv)>1 else '行走修正版 · 连续蒙皮'})
version=next(v for v in result['versions'] if v['id']==nonce);assert all(any(v['id']==before for v in result['versions']) for before in record['before_versions'])
record.update(version=version,after_revision=result['revision'],history_preserved=True);atomic_json(pointer,record)
with urllib.request.urlopen(base+'/api/characters/'+id+'/export?version='+nonce,timeout=90) as r:data=r.read()
(folder/'character-walk-fixed.zip').write_bytes(data)
with zipfile.ZipFile(io.BytesIO(data)) as z:z.extractall(folder/'published-export')
qa=(folder/'export/skeleton.json').read_bytes();published=(folder/'published-export/skeleton.json').read_bytes();record['matches_verified_candidate']=hashlib.sha256(qa).digest()==hashlib.sha256(published).digest();atomic_json(pointer,record)
entrypath=ROOT/'evidence/single-image/example.json';entry=json.loads(entrypath.read_text());backup=folder/'original-example.json'
if not backup.exists():atomic_json(backup,entry)
entry.update(version=nonce,rig_id=version['rig_id'],source_id=version['source_id']);atomic_json(entrypath,entry)
print(json.dumps({'saved':True,'revision':result['revision'],'versions':len(result['versions']),'settings':version['settings'],'matches_verified_candidate':record['matches_verified_candidate'],'rig':version['rig_id']},ensure_ascii=False))
