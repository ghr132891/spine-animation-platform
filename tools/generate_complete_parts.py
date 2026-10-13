"""Three resumable reference-image jobs for unoccluded character parts."""
import sys,json,uuid,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core import atomic_json
from app.provider import Matrix,DEFAULT_MODEL
from app.jobs import JobHistory
id='76a6aa6972a4466a976f798b115bac65';folder=ROOT/'evidence/single-image/complete-parts';folder.mkdir(exist_ok=True)
source=json.loads((ROOT/'storage/characters'/id/'jobs/73b38cf0d8124c468d0b1680f0db6627.json').read_text())['image_urls'][0]
from app.part_prompts import part_prompt,SHEETS as specs
matrix=Matrix(ROOT/'storage');worker=JobHistory(ROOT/'storage',matrix,'characters')
for name,spec in specs.items():
    pointer=folder/(name+'.json')
    if not pointer.exists():
        job_id=uuid.uuid4().hex;job={'id':job_id,'status':'submission_unknown','created_at':time.time(),'prompt':part_prompt(name),'mode':'complete-parts','sheet':name,'parts':spec['parts'],'columns':spec['columns'],'rows':spec['rows'],'model':DEFAULT_MODEL}
        path=worker.path(id,job_id);atomic_json(pointer,{'job_id':job_id});atomic_json(path,job)
        try:
            gen_id=matrix.generate_part_sheet(name,source)
            job.update(gen_id=gen_id,status='pending');atomic_json(path,job)
        except Exception:
            print(json.dumps({'sheet':name,'status':'submission_unknown','message':'Submission recorded. Inspect this task before attempting another submission.'}));raise
    job=worker.read(id,json.loads(pointer.read_text())['job_id'])
    if sys.argv[-1]=='poll':job=worker.refresh(worker.path(id,job['id']),force=True)
    if job.get('candidate_ready'):(folder/(name+'.png')).write_bytes(worker.path(id,job['id']).with_suffix('.png').read_bytes())
    print(json.dumps({'sheet':name,'job':job['id'],'status':job['status'],'ready':bool(job.get('candidate_ready'))}))
