"""HTTP and CLI acceptance with a fake provider; does not submit billable images."""
import base64
import json
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_automation import FakeMatrix
from app import server
from app.automation import AutoReskin
from app.jobs import JobHistory


with tempfile.TemporaryDirectory(prefix='spine-agent-http-') as tmp:
    storage=Path(tmp);provider=FakeMatrix();jobs=JobHistory(storage,provider)
    server.STORAGE=storage;server.PROJECTS={};server.MATRIX=provider;server.JOBS=jobs;server.AUTOMATION=AutoReskin(storage,provider,jobs,server.get_project)
    http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);base='http://127.0.0.1:'+str(http.server_port);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    def api(route,data=None,header=True):
        req=urllib.request.Request(base+route,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json',**({'X-Spine-Local':'1'} if header else {})})
        with urllib.request.urlopen(req,timeout=10) as r:return r.status,r.read()
    def cli(*args):
        result=subprocess.run([sys.executable,str(ROOT/'tools/spine_agent.py'),*args,'--base',base],capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr;return json.loads(result.stdout)
    try:
        schema=json.loads(api('/api/automation/openapi.json')[1]);assert schema['openapi']=='3.1.0'
        plan=cli('plan','--input',str(ROOT/'demo'),'--style','深蓝银色科技装甲','--regions','r0','r1');assert plan['generation_count']==2;assert not provider.submissions
        state=storage/'client-task.json';task=cli('run','--input',str(ROOT/'demo'),'--style','深蓝银色科技装甲','--regions','r0','r1','--state',str(state))
        same=cli('run','--input',str(ROOT/'demo'),'--style','深蓝银色科技装甲','--regions','r0','r1','--state',str(state));assert task['id']==same['id'];assert not provider.submissions
        route=task['status_url'];engine=server.AUTOMATION;engine.step(task['id'])
        for job in engine.read(task['id'])['jobs']:jobs.refresh(jobs.path(task['output_project_id'],job['id']),force=True)
        engine.step(task['id']);done=cli('wait','--state',str(state),'--timeout','2');assert done['state']=='completed';assert done['validation']['passed']
        zip_path=storage/'result.zip';downloaded=cli('download','--state',str(state),'--output',str(zip_path));assert zip_path.read_bytes()==engine.download(task['id'])
        assert len(json.loads(api('/api/automation/reskin/tasks')[1]))==1
        try:api(route+'/cancel',{},header=False);raise AssertionError('mutation header missing')
        except urllib.error.HTTPError as e:assert e.code==403
        request={'project_id':task['output_project_id'],'style':'另一种风格','nonce':task['id']}
        try:api('/api/automation/reskin/tasks',request);raise AssertionError('nonce changed')
        except urllib.error.HTTPError as e:assert e.code==400
        result={'httpAPI':True,'openAPI':True,'cliPlanRunWaitDownload':True,'idempotentSubmission':True,'postGuard':True,'completedZIP':True,'skeletonAndAtlasUnchanged':done['validation']['skeleton_unchanged'] and done['validation']['atlas_unchanged'],'billableProviderCalls':0,'simulatedGenerations':len(provider.submissions),'errors':[]}
        folder=ROOT/'evidence/automation';folder.mkdir(exist_ok=True);(folder/'http-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    finally:http.shutdown();http.server_close()
