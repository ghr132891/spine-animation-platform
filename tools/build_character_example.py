"""Build the actual Matrix-generated single-image example; preserve later edits."""
import json,sys,uuid,zipfile,io
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.core import atomic_json,png_bytes
storage=ROOT/'storage';folder=ROOT/'evidence/single-image';entry=json.loads((folder/'example.json').read_text());c=CharacterProject(storage/'characters'/entry['id'])
if not entry.get('version'):
    data=(c.directory/'jobs'/(entry['job_id']+'.png')).read_bytes();c.set_image(data,c.meta['revision'])
    coords={'head':(464,221),'neck':(464,376),'hip':(462,636),'l-shoulder':(349,431),'l-elbow':(271,559),'l-wrist':(259,708),'l-hand':(255,797),'r-shoulder':(560,436),'r-elbow':(589,573),'r-wrist':(641,705),'r-hand':(655,778),'l-hip':(401,659),'l-knee':(393,846),'l-ankle':(377,967),'l-toe':(482,1080),'r-hip':(528,679),'r-knee':(538,848),'r-ankle':(532,957),'r-toe':(632,1073),'back':(270,463)}
    landmarks={n:[x/896,y/1200] for n,(x,y) in coords.items()}
    masks={n:[[x/896,y/1200] for x,y in points] for n,points in json.loads((folder/'example-masks.json').read_text()).items()}
    c.update({'revision':c.meta['revision'],'landmarks':landmarks,'settings':c.meta['settings'],'masks':masks})
    nonce=uuid.uuid4().hex;c.build(storage,{'revision':c.meta['revision'],'nonce':nonce,'landmarks':landmarks,'settings':c.meta['settings'],'label':'亿创单图行走示例'})
    entry['version']=nonce;entry['rig_id']=c.meta['versions'][-1]['rig_id'];entry['source_id']=c.meta['versions'][-1]['source_id'];atomic_json(folder/'example.json',entry)
version=next(v for v in c.meta['versions'] if v['id']==entry['version']);target=folder/'export';target.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(c.export(storage,version['id']))) as z:z.extractall(target)
(folder/'character-walk.zip').write_bytes(c.export(storage,version['id']));(folder/'source.png').write_bytes(png_bytes(c.image()));(folder/'cutout.png').write_bytes(png_bytes(c.cutout()))
print(json.dumps({'character':c.id,'rig':entry['rig_id'],'parts':len(version['parts']),'revision':c.meta['revision']},ensure_ascii=False))
