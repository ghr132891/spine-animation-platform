"""Submit exactly one durable Matrix character job; subsequent runs only query it."""
import json,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core import atomic_json
from app.provider import Matrix
from app.jobs import JobHistory
storage=ROOT/'storage';pointer=ROOT/'evidence/single-image/example.json'
provider=Matrix(storage)
if not pointer.exists():
    id=uuid.uuid4().hex;job_id=uuid.uuid4().hex;directory=storage/'characters'/id
    directory.mkdir(parents=True);(directory/'jobs').mkdir()
    prompt='可爱的快递机器人，橙黄色外壳，蓝绿色关节和护甲，深蓝面罩，一只大眼睛，圆润的大头与短身体，双腿长度适中，厚实的运动靴，精致的二维手游角色立绘。'
    meta={'id':id,'name':'亿创橙色快递机器人','revision':0,'image_id':None,'landmarks':None,'settings':{'background':32,'period':1,'stride':.22,'lift':.1,'facing':'right'},'versions':[],'created_at':time.time()}
    atomic_json(directory/'character.json',meta);atomic_json(pointer,{'id':id,'job_id':job_id})
    job={'id':job_id,'status':'submission_unknown','prompt':prompt,'mode':'character','created_at':time.time()}
    path=directory/'jobs'/(job_id+'.json');atomic_json(path,job)
    gen_id=provider.generate_character(prompt)
    job.update(gen_id=gen_id,status='pending');atomic_json(path,job)
else:
    entry=json.loads(pointer.read_text());id=entry['id'];job_id=entry['job_id']
worker=JobHistory(storage,provider,'characters');job=worker.refresh(worker.path(id,job_id))
print(json.dumps({'character':id,'job':job_id,'status':job['status'],'ready':job.get('candidate_ready',False)},ensure_ascii=False))
