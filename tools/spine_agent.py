"""Small dependency-free client for AI agents using the local reskin API."""
import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

def sha(content):return hashlib.sha256(content).hexdigest()


def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:tmp.write_text(json.dumps(data,ensure_ascii=False));tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan','run','status','wait','download','cancel','resume'])
    parser.add_argument('--base',default='http://127.0.0.1:8780')
    source=parser.add_mutually_exclusive_group();source.add_argument('--project');source.add_argument('--input',type=Path,help='Spine ZIP 或资源目录')
    parser.add_argument('--style');parser.add_argument('--regions',nargs='+');parser.add_argument('--mode',choices=['material','reference'],default='material')
    parser.add_argument('--references',type=Path,help='region_id 到参考画布 HTTPS URL 的 JSON 映射')
    parser.add_argument('--max-generations',type=int,default=32);parser.add_argument('--revision',type=int)
    parser.add_argument('--nonce');parser.add_argument('--task');parser.add_argument('--state',type=Path,help='保存或读取任务编号；run 默认 spine-agent-task.json')
    parser.add_argument('--wait',action='store_true');parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--output',type=Path);parser.add_argument('--generation-ids',type=Path,help='待核实 job_id 到已存在 gen_id 的 JSON 映射')
    args=parser.parse_args()
    if not 1<=args.timeout<=86400:parser.error('--timeout 须为 1–86400 秒')
    if args.action=='run' and args.output:args.wait=True

    def api(route,data=None,binary=False):
        req=urllib.request.Request(args.base.rstrip('/')+route,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Spine-Local':'1'})
        try:
            with urllib.request.urlopen(req,timeout=90) as r:content=r.read()
        except urllib.error.HTTPError as e:
            try:message=json.load(e).get('error','接口请求失败')
            except ValueError:message='接口请求失败'
            raise RuntimeError(message) from None
        return content if binary else json.loads(content)

    def output(task):
        if args.output:
            if task['state']!='completed':raise RuntimeError('任务未完成；未写入输出文件')
            content=api(task['download_url'],binary=True)
            if sha(content)!=task['result_sha256']:raise RuntimeError('下载结果摘要不一致')
            args.output.parent.mkdir(parents=True,exist_ok=True);tmp=args.output.with_name(args.output.name+'.tmp');tmp.write_bytes(content);tmp.replace(args.output)
            task['output_file']=str(args.output.resolve())
        return task

    def wait(task):
        deadline=time.monotonic()+args.timeout;progress=None
        while task['state'] in ('queued','generating','assembling'):
            value=(task['state'],task['progress']['ready'])
            if value!=progress:print(json.dumps({'task':task['id'],'state':task['state'],'progress':task['progress']},ensure_ascii=False),file=sys.stderr);progress=value
            if time.monotonic()>=deadline:task['client_wait_timed_out']=True;break
            time.sleep(min(5,max(0,deadline-time.monotonic())));task=api(task['status_url'])
        return task

    if args.action in ('plan','run'):
        if not args.style or not (args.project or args.input):parser.error('plan/run 需要 --style 及 --project 或 --input')
        data={'style':args.style,'mode':args.mode,'max_generations':args.max_generations}
        if args.project:data['project_id']=args.project
        else:
            path=args.input.resolve()
            files=sorted(p for p in path.rglob('*') if p.is_file() and not any(n.startswith('.') for n in p.relative_to(path).parts)) if path.is_dir() else [path]
            data['files']=[{'name':str(p.relative_to(path)) if path.is_dir() else p.name,'data':base64.b64encode(p.read_bytes()).decode()} for p in files]
            data['name']=path.stem
        if args.regions:data['region_ids']=args.regions
        if args.references:data['reference_urls']=json.loads(args.references.read_text())
        if args.revision is not None:data['revision']=args.revision
        if args.action=='plan':result=api('/api/automation/reskin/plan',data)
        else:
            state=args.state or Path('spine-agent-task.json');digest=sha(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
            if state.exists():
                record=json.loads(state.read_text())
                if record['request_sha256']!=digest or record.get('base')!=args.base or (args.nonce and args.nonce!=record['id']):raise RuntimeError('state 文件对应另一个请求或服务，请指定新的 --state 文件')
                nonce=record['id']
            else:
                nonce=args.nonce or uuid.uuid4().hex;atomic_json(state,{'id':nonce,'request_sha256':digest,'base':args.base})
            data['nonce']=nonce
            # Record before POST. Repeating this same request reuses the same task.
            result=api('/api/automation/reskin/tasks',data)
            if args.wait:result=wait(result)
            result=output(result)
    else:
        id=args.task
        if not id and args.state:id=json.loads(args.state.read_text())['id']
        if not id:parser.error('需要 --task 或 --state')
        route='/api/automation/reskin/tasks/'+id
        if args.action=='cancel':result=api(route+'/cancel',{})
        elif args.action=='resume':result=api(route+'/resume',{'generation_ids':json.loads(args.generation_ids.read_text()) if args.generation_ids else {}})
        else:
            result=api(route)
            if args.action=='wait':result=wait(result)
            if args.action=='download' and not args.output:parser.error('download 需要 --output')
            result=output(result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get('state') not in ('failed','needs_attention','cancelled') else 2


if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,RuntimeError) as e:
        print(json.dumps({'error':str(e),'note':'已保存任务编号；可用 status 查询，勿用新编号重复提交。'},ensure_ascii=False),file=sys.stderr);sys.exit(1)
