#!/usr/bin/env python3
"""Portable conversational workflow client; stdlib only."""
import argparse,base64,json,os,sys,uuid
from pathlib import Path
from spine_mcp import Bridge,settings


def main():
    config=settings()
    p=argparse.ArgumentParser();p.add_argument('action',choices=['reference','sources','submit','batch','refine','status','cancel','inspect','preview','gait','contact','closeup','download'])
    p.add_argument('--base',default=os.getenv('SPINE_AGENT_BASE',config.get('base','http://127.0.0.1:8780')));p.add_argument('--token-file',type=Path,default=Path(config['token_file']) if config.get('token_file') and not os.getenv('SPINE_AGENT_TOKEN') else None)
    p.add_argument('--request',type=Path);p.add_argument('--task');p.add_argument('--output',type=Path);p.add_argument('--state',type=Path)
    p.add_argument('--image',type=Path);p.add_argument('--requirements');p.add_argument('--max-generations',type=int);p.add_argument('--artifact-index',type=int);p.add_argument('--animation')
    p.add_argument('--preparation',choices=['asset-sheet','auto','preserve'],default='asset-sheet');p.add_argument('--frames',type=int,choices=range(4,33));p.add_argument('--parts',type=Path)
    target=p.add_mutually_exclusive_group();target.add_argument('--rig');target.add_argument('--project');p.add_argument('--anchor');p.add_argument('--isolated',action='store_true');p.add_argument('--samples',type=int,choices=range(32,513))
    a=p.parse_args();token=a.token_file.read_text(encoding='utf-8-sig').strip() if a.token_file else os.getenv('SPINE_AGENT_TOKEN','')
    bridge=Bridge(a.base,token);saved=json.loads(a.state.read_text(encoding="utf-8-sig")) if a.state and a.state.exists() else {}
    if saved and saved.get('base')!=bridge.base:raise ValueError('state 对应的服务地址不同，请使用原服务地址')
    task=a.task or saved.get('task_id')
    if a.action in ('submit','refine','batch'):
        if a.request:request=json.loads(a.request.read_text(encoding="utf-8-sig"))
        elif a.action=='batch':p.error('batch 需要 --request JSON 文件')
        elif a.requirements:
            request={'nonce':saved.get('request',{}).get('nonce',uuid.uuid4().hex),'requirements':a.requirements}
            if a.action=='refine':
                if not a.task:p.error('refine 需要 --task 指定上一版任务')
                request['task_id']=a.task;request['max_generations']=a.max_generations if a.max_generations is not None else 0
                if a.artifact_index is not None:request['artifact_index']=a.artifact_index
            else:
                request['image_preparation']=a.preparation
                if a.image:request['image_path']=str(a.image.resolve())
                request['max_generations']=a.max_generations if a.max_generations is not None else 0 if a.preparation=='preserve' else 8
        elif saved.get('request'):request=saved['request']
        else:p.error('需要 --requirements 或 --request；重试可使用已有 --state')
        if a.parts:
            if a.action!='refine':p.error('--parts 只用于 refine')
            request['part_images']=json.loads(a.parts.read_text(encoding='utf-8-sig'))
        if saved and (saved.get('action')!=a.action or saved.get('request')!=request):raise ValueError('state 已用于其他需求，请为新一版选择新 state 文件')
        if a.state:
            a.state.parent.mkdir(parents=True,exist_ok=True);a.state.write_text(json.dumps({'action':a.action,'base':bridge.base,'request':request,'task_id':request.get('nonce')},ensure_ascii=False,indent=2),encoding='utf-8')
        result=bridge.call('spine_'+a.action,request)
    elif a.action=='reference':
        if not a.request:p.error('reference 需要 --request JSON，包含 action/kind/id；register 还需 url/revision')
        result=bridge.call('spine_reference',json.loads(a.request.read_text(encoding='utf-8-sig')))
    elif a.action=='sources':result=bridge.call('spine_sources',{})
    elif a.action=='closeup':
        direct=a.rig or a.project
        if direct and task:p.error('closeup的--rig/--project与--task/--state二选一')
        args={'kind':'rig' if a.rig else 'project','id':direct} if direct else {'task_id':task}
        if not direct and not task:p.error('closeup需要--rig、--project或--task/--state')
        if not a.output:p.error('closeup需要--output图片路径')
        if a.animation:args['animation']=a.animation
        if a.anchor:args['anchor']=a.anchor
        if a.frames is not None:args['frame_count']=a.frames
        if a.isolated:args['isolated']=True
        if a.artifact_index is not None:args['artifact_index']=a.artifact_index
        result=bridge.call('spine_closeup',args);image=next(c for c in result['content'] if c['type']=='image')
        if a.output.exists():raise ValueError('局部预览目标已存在，请选择新文件名')
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(base64.b64decode(image['data'],validate=True))
        result={'output_file':str(a.output.resolve()),'details':[json.loads(c['text']) for c in result['content'] if c['type']=='text']}
    elif a.action=='contact':
        direct=a.rig or a.project
        if direct and task:p.error('contact 的 --rig/--project 与 --task/--state 只能选一种')
        args={'kind':'rig' if a.rig else 'project','id':direct} if direct else {'task_id':task}
        if not direct and not task:p.error('contact 需要 --rig、--project 或 --task/--state')
        if a.artifact_index is not None:args['artifact_index']=a.artifact_index
        if a.animation:args['animation']=a.animation
        options=json.loads(a.request.read_text(encoding='utf-8-sig')) if a.request else {}
        if not isinstance(options,dict):p.error('contact --request 需要 options JSON 对象')
        if a.samples is not None:options['samples']=a.samples
        if options:args['options']=options
        result=bridge.call('spine_contact',args)
    else:
        if not task:p.error('需要 --task 或保存过任务的 --state')
        args={'task_id':task}
        if a.artifact_index is not None:args['artifact_index']=a.artifact_index
        if a.action=='preview':
            if not a.output:p.error('preview 需要 --output 图片路径')
            if a.animation:args['animation']=a.animation
            if a.frames is not None:args['frame_count']=a.frames
            result=bridge.call('spine_preview',args);image=next(c for c in result['content'] if c['type']=='image')
            if a.output.exists():raise ValueError('预览目标文件已存在，请选择新文件名')
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(base64.b64decode(image['data'],validate=True))
            result={'output_file':str(a.output.resolve()),'details':[json.loads(c['text']) for c in result['content'] if c['type']=='text']}
        elif a.action=='gait':
            if not a.request:p.error('gait 需要 --request，包含真实 anchor 和同侧 pairs')
            args.update(json.loads(a.request.read_text(encoding='utf-8-sig')))
            args['task_id']=task
            if a.animation:args['animation']=a.animation
            result=bridge.call('spine_gait',args)
        elif a.action=='download':
            if not a.output:p.error('download 需要 --output ZIP 路径')
            result=bridge.call('spine_download',{'task_id':task,'output_path':str(a.output.resolve())})
        else:result=bridge.call('spine_result' if a.action=='inspect' else 'spine_'+a.action,args)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 2 if result.get('state') in ('failed','cancelled','interrupted') else 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(1)
