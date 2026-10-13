"""Persist and finish already-submitted jobs independently of browser selection.

This worker only queries statuses and downloads outputs. It never generates.
"""
import concurrent.futures
import json
import re
import threading
import time
from pathlib import Path
from .core import UserError,atomic_json
from .provider import LEGACY_MODEL,classify_provider_failure,failure_details


class JobHistory:
    def __init__(self,storage,provider,collection='projects'):
        self.root=Path(storage)/collection
        self.provider=provider
        self.guard=threading.Lock()
        self.locks={}
        self.stop=threading.Event()
        self.wake=threading.Event()

    def path(self,project_id,job_id):
        if not re.fullmatch(r'[a-f0-9]{32}',project_id) or not re.fullmatch(r'[a-f0-9-]{16,64}',job_id):
            raise UserError('任务编号无效')
        return self.root/project_id/'jobs'/(job_id+'.json')

    def read(self,project_id,job_id):
        path=self.path(project_id,job_id)
        if not path.exists():raise UserError('任务记录不存在')
        return json.loads(path.read_text())

    def due(self,path,job,force=False):
        if not job.get('gen_id') or job.get('status') in ('failed','submission_unknown'):return False
        if job.get('candidate_ready') and path.with_suffix('.png').exists():return False
        if not force and (job.get('poll_failures',0)>=8 or job.get('poll_count',0)>=225 or job.get('next_check_at',0)>time.time()):return False
        return True

    def refresh(self,path,force=False):
        path=Path(path)
        with self.guard:lock=self.locks.setdefault(str(path),threading.Lock())
        with lock:
            job=json.loads(path.read_text())
            if not self.due(path,job,force):return job
            if force:job['poll_count']=0
            job['poll_count']=job.get('poll_count',0)+1
            try:
                result=self.provider.poll(job['gen_id'], model=job.get('model',LEGACY_MODEL));data=result.get('data') or {}
                job['status']=data.get('status','pending')
                job.setdefault('prompt',str((data.get('input') or {}).get('prompt',''))[:2000])
                job.setdefault('mode',job.get('layout',{}).get('mode','reference'))
                if job['status']=='completed':
                    urls=[]
                    for output in data.get('outputs',[]):
                        if output.get('item_status')=='failed':continue
                        item=output.get('data') or {}
                        if item.get('image_url'):urls.append(item['image_url'])
                        urls.extend(item.get('image_urls') or [])
                    if urls:
                        # Do not mark a candidate ready until the download is durable.
                        image=self.provider.download_result(urls[0])
                        target=path.with_suffix('.png');temp=target.with_suffix('.png.tmp')
                        temp.write_bytes(image);temp.replace(target)
                        job.update(image_urls=urls,candidate_ready=True)
                        job.pop('failure',None)
                    else:
                        failure=classify_provider_failure(result,category='empty_output')
                        job.update(status='failed',failure=failure,error=failure['message']+' '+failure['next_action'])
                elif job['status']=='failed':
                    failure=classify_provider_failure(result,category='generation_failed')
                    job.update(failure=failure,error=failure['message']+' '+failure['next_action'])
                job.pop('poll_error',None)
                job['poll_failures']=0
                job['next_check_at']=time.time()+8
                if job['poll_count']>=225 and not job.get('candidate_ready') and job['status']!='failed':
                    job['poll_error']='已达到本次查询上限，任务记录仍保留；点击刷新结果可继续查询'
            except Exception as error:
                # Keep job identity and state so transient outages can be resumed.
                failure=failure_details(error)
                failures=job.get('poll_failures',0)+1
                job.update(poll_failures=failures,poll_error=failure['message']+' '+failure['next_action'],failure=failure,next_check_at=time.time()+min(120,5*2**min(failures,5)))
                if failure['stop_generation']:job.update(status='failed',error=job.pop('poll_error'))
            job['checked_at']=time.time()
            atomic_json(path,job)
            return job

    def request_refresh(self,project_id):
        if not re.fullmatch(r'[a-f0-9]{32}',project_id):raise UserError('项目编号无效')
        def run():
            for path in (self.root/project_id/'jobs').glob('*.json'):
                self.refresh(path,force=True)
        threading.Thread(target=run,daemon=True).start()

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            while not self.stop.is_set():
                paths=[]
                for path in self.root.glob('*/jobs/*.json'):
                    try:
                        if self.due(path,json.loads(path.read_text())):paths.append(path)
                    except (OSError,ValueError):continue
                # Only read already-created jobs; no resubmission and no billing calls.
                for result in concurrent.futures.as_completed([pool.submit(self.refresh,p) for p in paths]):
                    try:result.result()
                    except (OSError,ValueError):pass
                self.wake.wait(5);self.wake.clear()
