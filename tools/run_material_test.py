"""Submit once / poll the same material generation, using the application API."""
import argparse,json,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('action',choices=['submit','poll','apply']);args=parser.parse_args()
record=ROOT/'evidence/material-test.json'
def api(path,data=None):
    headers={'X-Spine-Local':'1','Content-Type':'application/json'}
    req=urllib.request.Request('http://127.0.0.1:8780'+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
    with urllib.request.urlopen(req,timeout=65) as response:return json.load(response)
if args.action=='submit':
    if record.exists():print('Already submitted; use poll.');raise SystemExit()
    project=api('/api/demo',{});region=next(r for r in project['regions'] if r['name']=='torso')
    import uuid
    state={'project':project['id'],'job_id':str(uuid.uuid4())};record.write_text(json.dumps(state))
    job=api('/api/projects/'+project['id']+'/ai',{'id':region['id'],'prompt':'Deep midnight blue ceramic metal armor, elegant thin silver panel seams arranged in large clean geometric sections, occasional tiny amber accent details, subtle surface grain. Premium stylized sci-fi game artwork, dark blue dominant, not too busy.','mode':'material','revision':0,'nonce':state['job_id']})
    print(json.dumps(job,ensure_ascii=False))
elif args.action=='poll':
    state=json.loads(record.read_text());job=api('/api/projects/'+state['project']+'/jobs?id='+state['job_id']);(ROOT/'evidence/material-result.json').write_text(json.dumps(job,indent=2,ensure_ascii=False));print(json.dumps(job,ensure_ascii=False))
else:
    state=json.loads(record.read_text());p=api('/api/projects/'+state['project']);p=api('/api/projects/'+state['project']+'/apply-ai',{'job_id':state['job_id'],'revision':p['revision']});report=api('/api/projects/'+state['project']+'/validate');(ROOT/'evidence/material-validation.json').write_text(json.dumps(report,indent=2));print(json.dumps({'project':p['id'],'revision':p['revision'],'changed':p['changed'],'validation':report['passed']}))
