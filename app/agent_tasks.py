"""Durable requirements -> Codex -> bounded platform tools -> validated ZIP."""
from __future__ import annotations
import base64
import copy
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from .core import Project,UserError,atomic_json,png_bytes,sha,read_image,safe_name
from .character import CharacterProject
from .rig import RigProject
from .rig_automation import clone_rig
from .asset_integrity import CONTRACT as INTEGRITY_CONTRACT, checked_material_review, require_material_reviews
from .image_preflight import checked_assessment,selected_sheets,POLICIES,checked_part_review,require_side_camera,checked_connections
from .connection_review import CONTRACT as CONNECTION_CONTRACT, CONTRACTS as CONNECTION_CONTRACTS, CHECKS as CONNECTION_CHECKS, checked as checked_connection, require as require_connections
from .orientation_review import CONTRACT as ORIENTATION_CONTRACT, checked_orientation, require_orientation_reviews, required_checks as orientation_fields
from .production_reference import REFERENCE_PROFILE
from .runtime_cache import runtime_reports, runtime_previews
from .asset_integrity import GARMENT_CONTRACT, garment_anchors, garment_frame_count, checked_garment_review, require_garment_reviews

TERMINAL={'completed','failed','cancelled','interrupted'}
KINDS={'project':'projects','rig':'rigs','character':'characters'}
CHAR_ACTIONS={'repair-connections','stress-test','review-stress','garment-template','edge-review','prepare-assembly','finish-parts','prebind','review-prebind','layer-overlap','review-overlap','generate','generate-view','generate-parts','generation-plan','apply-ai','apply-part-sheet',
              'apply-part-layout','apply-source-layout','install-canvas-layers','partition-canvas','review-reconstruction','reconstruction','guide','review','build','restore','foot-layers','reset-part','refresh-jobs'}


def nonce(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9-]{16,64}',value):raise UserError('nonce 须为 16–64 位十六进制字符或连字符')
    return value


def resource_digest(content):
    # ZIP creation timestamps vary; compare the actual named resources.
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return sha(json.dumps([(n,sha(z.read(n))) for n in sorted(z.namelist())],ensure_ascii=False).encode())


def frame_count(value=8):
    if type(value)!=int or not 4<=value<=32:raise UserError('frame_count 须为 4–32 的整数')
    return value


def motion_review(checks,key,digest):
    """Preview credit belongs to the exact named resources, never a prior revision."""
    review=checks.get(key)
    if not isinstance(review,dict) or review.get('sha256')!=digest:
        review={'sha256':digest,'animations':[],'frame_counts':{}}
        checks[key]=review
    if not isinstance(review.get('animations'),list):review['animations']=[]
    if not isinstance(review.get('frame_counts'),dict):review['frame_counts']={}
    return review


def require_requested_motion(requirements,report,reviewed,min_frames=16):
    """A locomotion request needs actual movement; this is not a natural-gait proof."""
    requested=re.search(r'行走|走路|步行|奔跑|跑步|\b(?:walk|walking|run|running)\b',requirements,re.I)
    if not requested:return
    clips=[name for name in report.get('animations',[]) if isinstance(name,str) and name.lower() in ('walk','walking','run','running')]
    counts=reviewed.get('frame_counts',{})
    counts=counts if isinstance(counts,dict) else {}
    motion=report.get('motion')
    samples=motion.get('clips') if isinstance(motion,dict) else None
    for name in clips:
        count=counts.get(name,0)
        if type(count)!=int or count<min_frames:
            raise UserError('动作 '+name+' 尚未查看至少'+str(min_frames)+'帧预览；请用 spine_motion 设置 frame_count=32 查看当前资源后再交付')
        skins=report.get('skins')
        checks=[entry for entry in samples if isinstance(entry,dict) and entry.get('animation')==name] if isinstance(samples,list) else []
        if (type(skins)!=int or skins<1 or len(checks)!=skins or
                len({entry.get('skin') for entry in checks if isinstance(entry.get('skin'),(str,type(None)))})!=skins):
            raise UserError('动作 '+name+' 缺少全部皮肤的运行时运动摘要；请更新 runtime 校验并重新查看当前动作，不标记为完成')
        if any(entry.get('visibleMotionObserved') is not True for entry in checks):
            raise UserError('动作 '+name+' 未检测到角色贴图的可见运动；请检查贴图绑定、动画关键帧及 Spine 版本字段，修正后重新查看32帧预览')
        if any(entry.get('articulatedMotionObserved') is not True for entry in checks):
            raise UserError('动作 '+name+' 未检测到关节运动，整体平移不能作为行走／奔跑；请调整肢体骨骼与动作，修正后重新查看32帧预览')
        if any(entry.get('rootRelativeGeometryMotionObserved') is not True for entry in checks):
            raise UserError('动作 '+name+' 未检测到角色贴图相对根骨骼的实际运动；请检查可见肢体的骨骼绑定与动作，并更新 runtime 校验后重新查看32帧预览')


def part_images(items):
    if not isinstance(items,list) or len(items)>8:raise UserError('补全部件最多 8 张')
    result=[];seen=set();total=0
    for item in items:
        if not isinstance(item,dict):raise UserError('补全部件须包含 name 和 image')
        name=item.get('name','')
        if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',name) or name in seen:raise UserError('补全部件名称须唯一，仅使用字母、数字、下划线或连字符')
        encoded=item.get('image')
        if not isinstance(encoded,str) or len(encoded)>8*1024*1024:raise UserError('单张补全部件最多 6 MB')
        try:blob=base64.b64decode(encoded,validate=True)
        except ValueError:raise UserError('补全部件图片内容无效') from None
        total+=len(blob)
        if total>12*1024*1024:raise UserError('补全部件总计最多 12 MB')
        im=read_image(blob)
        if im.width>2048 or im.height>2048:raise UserError('单张补全部件最大 2048×2048')
        result.append((name,im));seen.add(name)
    return result


from .reference_recovery import ReferenceRecovery
from .provider import ProviderError


class AgentTasks(ReferenceRecovery):
    def __init__(self,storage,root,loaders,rig_automation):
        self.storage=Path(storage);self.root=Path(root);self.tasks=self.storage/'agent-tasks'
        self.loaders=loaders;self.rig_automation=rig_automation
        self.lock=threading.RLock();self.tool_lock=threading.RLock();self.wake=threading.Event();self.stop=threading.Event()
        from .task_locks import TaskLocks
        self.task_locks=TaskLocks();self.processes={};self.active_tasks=set()
        self.process=None;self.active=None;self.base='http://127.0.0.1:8780'
        self.token_path=self.storage/'agent-access-token'
        self.storage.mkdir(parents=True,exist_ok=True)
        if not self.token_path.exists():
            try:
                fd=os.open(self.token_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(32))
            except FileExistsError:pass
        self.token=self.token_path.read_text().strip()
        self.require_token=os.getenv('SPINE_AGENT_REQUIRE_TOKEN','0')=='1'

    def authorized(self,header):
        return not self.require_token or secrets.compare_digest(header or '', 'Bearer '+self.token)

    def path(self,id):return self.tasks/nonce(id)/'task.json'
    def read(self,id):
        path=self.path(id)
        if not path.exists():raise UserError('Agent 任务不存在')
        return json.loads(path.read_text())

    def update(self,id,**kw):
        with self.lock:
            task=self.read(id);task.update(kw,updated_at=time.time());atomic_json(self.path(id),task);return task

    def summary(self,id):
        t=self.read(id)
        jobs=[]
        for kind in ('project','character'):
            for entity in t['owned'].get(kind,[]):
                for path in (self.storage/KINDS[kind]/entity/'jobs').glob('*.json'):
                    job=json.loads(path.read_text())
                    jobs.append({'kind':kind,'project_id':entity,**{key:job[key] for key in ('id','provider','model','gen_id','status','candidate_ready','error','poll_error') if key in job}})
        return {k:v for k,v in t.items() if k not in ('request_sha256','owned','reservations','calls','edits')} | {
            'status_url':'/api/automation/agent/tasks/'+id,
            'download_url':'/api/automation/agent/tasks/'+id+'/download' if t['state']=='completed' else None,
            'generation_budget':{'used_or_reserved':sum(v['count'] for v in t['reservations'].values()),'limit':t['max_generations'],'baseline_used_or_reserved':sum(v.get('baseline_count',0) for v in t['reservations'].values()),'baseline_limit':2 if t.get('turnaround_contract') and t['max_generations']>0 else 0,'total_images_reserved':sum(v.get('image_count',v['count']) for v in t['reservations'].values())},'generation_jobs':jobs}

    def clone(self,source):
        kind=source['kind'];original=self.loaders[kind](source['id'])
        with original.lock:
            revision=original.revision if kind=='project' else original.meta['revision']
            if source.get('revision') is not None and source['revision']!=revision:raise UserError('源项目已更新')
            if kind=='project':
                value=Project(self.storage/'projects'/uuid.uuid4().hex,
                              {n:original.pages_data.get(n,b) for n,b in original.files.items()},original.label+' · Agent 源副本')
            elif kind=='rig':value=clone_rig(original,self.storage/'rigs'/uuid.uuid4().hex)
            else:
                value=CharacterProject.create(self.storage,original.meta['name']+' · Agent 副本')
                for folder in ('images','part-images'):
                    if (original.directory/folder).exists():shutil.copytree(original.directory/folder,value.directory/folder)
                m=copy.deepcopy(original.meta);m.update(id=value.id,revision=0,versions=[])
                atomic_json(value.directory/'character.json',m);value=CharacterProject(value.directory)
                # Keep completed reference jobs, never duplicate pending generation polling.
                for p in (original.directory/'jobs').glob('*.json'):
                    j=json.loads(p.read_text())
                    if j.get('candidate_ready') and p.with_suffix('.png').exists():
                        dst=value.directory/'jobs'/p.name;dst.parent.mkdir(exist_ok=True)
                        shutil.copy2(p,dst);shutil.copy2(p.with_suffix('.png'),dst.with_suffix('.png'))
        return {'kind':kind,'id':value.id,'original_id':source['id'],'source_revision':revision}

    def save_tool_state(self,t):
        # A cancellation during an HTTP/provider call must not be overwritten.
        with self.lock:
            current=self.read(t['id'])
            if current['state'] in TERMINAL:
                t['state']=current['state'];t['stage']=current['stage']
            # The platform review coordinator can finish while a model tool is
            # running. A stale model snapshot must not rewind its durable queue.
            for key in ('animation_transitions','automatic_animation','automatic_build_attempts','material_animation','stage_checkpoints'):
                if key in current:t[key]=copy.deepcopy(current[key])
            atomic_json(self.path(t['id']),t)

    def create(self,data):
        if not isinstance(data,dict):raise UserError('任务请求须为 JSON 对象')
        id=nonce(data.get('nonce'));path=self.path(id)
        requirements=data.get('requirements','')
        if not isinstance(requirements,str) or not 5<=len(requirements.strip())<=6000:raise UserError('requirements 须为 5–6000 字')
        sources=data.get('sources',[])
        encoded_image=data.get('image')
        preparation=data.get('image_preparation','asset-sheet')
        output_scope=data.get('output_scope','animation')
        if output_scope not in ('animation','materials'):raise UserError('output_scope 须为 animation 或 materials')
        material_edits=data.get('material_edits')
        if material_edits is not None and (output_scope!='materials' or not isinstance(sources,list) or len(sources)!=1 or not isinstance(sources[0],dict) or sources[0].get('kind')!='character'):
            raise UserError('预置素材修整仅支持单个 character 的 materials 任务')
        if preparation not in POLICIES:raise UserError('image_preparation 须为 asset-sheet/auto/preserve')
        supplements=part_images(data.get('_part_images',[]))
        if supplements and (not isinstance(sources,list) or len(sources)!=1 or not isinstance(sources[0],dict) or sources[0].get('kind')!='rig'):raise UserError('补全部件目前只支持单个 rig 微调')
        if encoded_image is not None:
            if sources:raise UserError('图片和已有源项目只能选一种')
            if not isinstance(encoded_image,str) or len(encoded_image)>16*1024*1024:raise UserError('上传图片最多 12 MB')
            try:image_bytes=base64.b64decode(encoded_image,validate=True)
            except ValueError:raise UserError('图片内容无效') from None
        if not isinstance(sources,list) or len(sources)>16:raise UserError('每个任务最多支持 16 个源项目')
        for s in sources:
            if not isinstance(s,dict) or s.get('kind') not in KINDS or not re.fullmatch('[a-f0-9]{32}',str(s.get('id',''))):raise UserError('sources 中需提供 kind 和项目 id')
        budget=data.get('max_generations',0 if preparation=='preserve' else 8);repairs=data.get('max_repairs',4);timeout=data.get('timeout_seconds',1800)
        if type(budget)!=int or not 0<=budget<=128:raise UserError('max_generations 须为 0–128')
        if type(repairs)!=int or not 0<=repairs<=5:raise UserError('max_repairs 须为 0–5')
        if type(timeout)!=int or not 60<=timeout<=7200:raise UserError('timeout_seconds 须为 60–7200')
        digest=sha(json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
        with self.lock:
            if path.exists():
                if self.read(id)['request_sha256']!=digest:raise UserError('此 nonce 已用于不同需求')
                return self.summary(id)
            clones=[]
            try:
                for s in sources:clones.append(self.clone(s))
                if supplements:
                    rig=self.loaders['rig'](clones[0]['id']);state=copy.deepcopy(rig.state)
                    for name,im in supplements:
                        if any(p['name']==name for p in state['parts']):raise UserError('补全部件名称与已有贴图重复：'+name)
                        state['parts'].append({'id':'p'+uuid.uuid4().hex,'name':safe_name(name),'sha':rig.put_blob(png_bytes(im)),'width':im.width,'height':im.height})
                    rig.commit(state,'导入待绑定的补全部件')
                if not clones:
                    c=CharacterProject.create(self.storage,'上传角色' if encoded_image is not None else 'Agent 生成角色');clones=[{'kind':'character','id':c.id,'original_id':None}]
                    if encoded_image is not None:c.set_image(image_bytes,0)
                if material_edits is not None:
                    if not isinstance(material_edits,list) or not 1<=len(material_edits)<=16:raise UserError('预置素材修整须为1–16项')
                    c=self.loaders['character'](clones[0]['id'])
                    for edit in material_edits:
                        if not isinstance(edit,dict) or not isinstance(edit.get('part'),str) or edit.get('expected_sha256')!=c.meta.get('part_layers',{}).get(edit.get('part'),{}).get('image_id') or not edit.get('expected_sha256'):
                            raise UserError('预置修整与当前源贴图摘要不一致，先重新检查素材')
                    c.finish_parts({'revision':c.meta['revision'],'edits':material_edits})
                if output_scope=='animation':
                    from .assembly_stress import CONTRACT as STRESS_CONTRACT
                    for source in clones:
                        if source['kind']=='character':
                            c=self.loaders['character'](source['id']);m=copy.deepcopy(c.meta);m['stress_contract']=STRESS_CONTRACT;c.save(m)
                now=time.time()
                if output_scope=='materials' and any(s['kind']!='character' for s in clones):raise UserError('素材交付仅支持 character 来源')
                task={'id':id,'state':'queued','requirements':requirements.strip(),'sources':clones,
                      'output_scope':output_scope,'automatic_animation_enabled':data.get('_material_animation',True) is not False,
                      **({'material_animation':True} if data.get('_material_animation') is True and output_scope=='animation' else {}),
                      **({'material_repairs_applied':[edit['part'] for edit in material_edits]} if material_edits is not None else {}),
                      'image_preparation':preparation,'image_assessments':{},'image_checks':{},
                      'max_generations':budget,'max_repairs':repairs,'timeout_seconds':timeout,
                      'created_at':now,'updated_at':now,'request_sha256':digest,'artifacts':[],
                      'owned':{k:[s['id'] for s in clones if s['kind']==k] for k in KINDS},
                      'reservations':{},'calls':0,'edits':0,'motion_checks':{},'seam_contract':'neck-seam-v1','stage':'等待后台执行',
                      **({k:data['_'+k] for k in ('parent_task_id','refinement_artifact_index','refinement_request_sha256')} if data.get('_parent_task_id') else {}),
                      **({'web_session_id':data['_web_session_id'],'web_mode':data.get('_web_mode','image')} if data.get('_web_session_id') else {})}
                for key in ('workflow_id','workflow_title','web_refine_sha256','web_refine_parent','web_request_summary','material_handoff_sha256'):
                    if '_'+key in data:task[key]=data['_'+key]
                if preparation=='asset-sheet' and any(s['kind']=='character' for s in clones):
                    task['preparation_contract']='side-parts-v2'
                    if any(s['kind']=='character' and not self.loaders['character'](s['id']).asset_status()['ready'] for s in clones):task['canvas_contract']='canvas-layers-v1'
                    task['turnaround_contract']='three-view-v1'
                    task['detail_contract']='articulation-v1'
                if any(s['kind']=='rig' and (self.loaders['rig'](s['id']).meta.get('turnaround_reference') or garment_anchors(self.loaders['rig'](s['id']).state['skeleton'])) for s in clones):task['detail_contract']='articulation-v1'
                if output_scope=='animation' and any(
                    s['kind']=='character' and self.loaders['character'](s['id']).meta.get('image_id') and self.loaders['character'](s['id']).asset_status()['ready'] or
                    s['kind']=='rig' and self.loaders['rig'](s['id']).meta.get('canvas_layers') for s in clones):
                    task['detail_contract']='articulation-v1'
                if output_scope=='animation' and len(clones)==1 and clones[0]['kind']=='character' and data.get('_material_animation',True) is True:
                    source=self.loaders['character'](clones[0]['id'])
                    if source.meta.get('prebind_review',{}).get('verdict')=='ready' and source.asset_status()['ready']:
                        task['material_animation']=True
                if task.get('detail_contract')=='articulation-v1':
                    task['orientation_contract']=ORIENTATION_CONTRACT
                    task['connection_contract']=CONNECTION_CONTRACT
                    task['integrity_contract']=INTEGRITY_CONTRACT
                    task['garment_contract']=GARMENT_CONTRACT
                atomic_json(path,task);os.chmod(path.parent,0o700)
            except Exception:
                for s in clones:shutil.rmtree(self.storage/KINDS[s['kind']]/s['id'],ignore_errors=True)
                raise
        self.wake.set();return self.summary(id)

    def batch(self,data):
        requests=data.get('requests')
        if not isinstance(requests,list) or not 1<=len(requests)<=32:raise UserError('requests 须为 1–32 个任务')
        results=[]
        for item in requests:
            try:results.append({'ok':True,'task':self.create(item)})
            except (UserError,ValueError,KeyError) as e:results.append({'ok':False,'nonce':item.get('nonce') if isinstance(item,dict) else None,'error':str(e)})
        return {'results':results}

    def result_target(self,id,index=0):
        t=self.read(id)
        artifacts=t.get('artifacts',[]) if t['state']=='completed' else t.get('draft_artifacts',[])
        if not artifacts:raise UserError('尚无可查看或微调的已保存成果')
        if type(index)!=int or not 0<=index<len(artifacts):raise UserError('成果序号无效')
        a=artifacts[index];kind=a['kind'];entity=a['id']
        if kind=='character':
            c=self.loaders['character'](entity)
            saved=next((v for v in c.meta['versions'] if v['id']==a.get('version')),None)
            if not saved:raise UserError('上一版角色的保存记录不存在')
            kind='rig';entity=saved['rig_id']
        source=self.loaders[kind](entity)
        check=t.get('motion_checks',{}).get(kind+':'+entity,{})
        expected=check.get('sha256') if t['state']=='completed' else a.get('resource_sha256')
        if expected!=resource_digest(source.export()):raise UserError('上一版成果已被另行修改，请先提交当前版本并重新检查')
        return t,a,kind,source

    def refine(self,data):
        if not isinstance(data,dict):raise UserError('微调请求须为 JSON 对象')
        id=nonce(data.get('nonce'));digest=sha(json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
        with self.lock:
            if self.path(id).exists():
                old=self.read(id)
                if old.get('refinement_request_sha256')!=digest:raise UserError('此 nonce 已用于不同微调需求')
                return self.summary(id)
            parent_id=data.get('task_id');parent=self.read(parent_id)
            if len(parent.get('artifacts',[]))>1 and 'artifact_index' not in data:raise UserError('上一版有多个成果，请指定 artifact_index')
            index=data.get('artifact_index',0)
            parent,artifact,kind,source=self.result_target(parent_id,index)
            revision=source.revision if kind=='project' else source.meta['revision']
            if data.get('part_images') and kind!='rig':raise UserError('请先将项目转换为 rig，再导入补全部件')
            return self.create({'nonce':id,'requirements':data.get('requirements',''),
                                'sources':[{'kind':kind,'id':source.id,'revision':revision}],
                                'max_generations':data.get('max_generations',0),'max_repairs':data.get('max_repairs',4),
                                'timeout_seconds':data.get('timeout_seconds',1800),
                                '_parent_task_id':parent_id,'_refinement_artifact_index':index,'_refinement_request_sha256':digest,
                                '_part_images':data.get('part_images',[])})

    def result_view(self,id,data):
        t,a,kind,source=self.result_target(id,data.get('artifact_index',0));view=data.get('view','inspect')
        if view=='inspect':
            return {'task_id':id,'artifact':a,'kind':kind,'id':source.id,'resource':self.api('/api/'+KINDS[kind]+'/'+source.id)}
        if view=='contact':return {'task_id':id,**self.contact_target(kind,source.id,data)}
        if view=='closeup':
            report,preview=self.closeup_target(kind,source.id,data)
            return {'content':[{'type':'text','text':json.dumps(report)}, {'type':'image','mimeType':'image/png','data':base64.b64encode(preview).decode()}]}
        if view not in ('motion','gait'):raise UserError('成果查看支持 inspect、motion、gait 或 contact')
        clip=data.get('animation','__setup__');count=frame_count(data.get('frame_count',8))
        gait={k:data[k] for k in ('pairs','anchor','axis') if k in data} if view=='gait' else None
        report,preview=self.runtime_check(source.export(),clip,count,gait)
        if view=='gait':return {'task_id':id,'kind':kind,'id':source.id,'validation':report}
        return {'content':[{'type':'text','text':json.dumps({'task_id':id,'kind':kind,'id':source.id,'animation':clip,'frames':count,'validation':report})},
                           {'type':'image','mimeType':'image/png','data':base64.b64encode(preview).decode()}]}

    def resume(self,id,data):
        request_nonce=nonce(data.get('nonce'))
        with self.lock:
            t=self.read(id)
            if t.get('resume_nonce')==request_nonce:return self.summary(id)
            if t['state'] not in ('failed','interrupted'):raise UserError('仅未完成或中断的制作可以继续')
            t.setdefault('execution_attempts',[]).append({'state':t['state'],'error':t.get('error'),'worker_failure':t.get('worker_failure'),'ended_at':t.get('updated_at')})
            t.update(state='queued',stage='复用已有素材继续制作',resume_nonce=request_nonce,updated_at=time.time())
            t.pop('error',None)
            t.pop('worker_failure',None)
            if self.reference_request_path(id).exists():
                t['reference_recovery']={'state':'replaying','started_at':time.time(),'deadline':time.time()+1800,'attempts':0}
            prior=self.path(id).parent/'agent-result.json'
            if prior.exists():prior.rename(prior.with_name('agent-result-attempt-'+str(len(t['execution_attempts']))+'.json'))
            # Owned assets, reservations, consumed budget and generation IDs are preserved.
            atomic_json(self.path(id),t)
        self.wake.set();return self.summary(id)

    def cancel(self,id):
        with self.lock:
            task=self.read(id)
            if task['state'] in TERMINAL:return self.summary(id)
            self.update(id,state='cancelled',stage='已取消；已提交生图仍保留编号')
            process=self.processes.get(id)
            if process:process.terminate()
        return self.summary(id)

    def api(self,route,data=None,binary=False):
        req=urllib.request.Request(self.base+route,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Spine-Local':'1'})
        try:
            with urllib.request.urlopen(req,timeout=110) as r:body=r.read()
        except urllib.error.HTTPError as e:
            try:body=json.load(e)
            except Exception:body={}
            if isinstance(body.get('failure'),dict) and body['failure'].get('category')=='reference_unavailable':
                raise ProviderError(body['failure']) from None
            message=body.get('error','平台请求失败')
            raise UserError(message) from None
        return body if binary else json.loads(body)

    def own(self,t,kind,id):
        if id not in t['owned'].get(kind,[]):raise UserError('仅可操作当前任务的独立副本')

    def contact_target(self,kind,entity,data):
        if kind not in ('rig','project'):raise UserError('鞋底接地诊断须使用 rig 或 project')
        source=self.loaders[kind](entity)
        with source.lock:
            content=source.export()
            revision=source.revision if kind=='project' else source.meta['revision']
        return {'kind':kind,'id':entity,'revision':revision,**self.contact_check(content,data.get('animation','walk'),data.get('options'))}

    def closeup_target(self,kind,entity,data):
        if kind not in ('rig','project'):raise UserError('局部动画检查须使用rig或project')
        source=self.loaders[kind](entity)
        with source.lock:
            content=source.export();revision=source.meta['revision'] if kind=='rig' else source.revision
            skeleton=copy.deepcopy(source.state['skeleton'] if kind=='rig' else source.skeleton_data)
        clip=data.get('animation','idle');anchor=data.get('anchor','head')
        default_count=garment_frame_count(clip) if anchor in garment_anchors(skeleton) or anchor in ('__hip__','__back__','head') else 8
        count=frame_count(data.get('frame_count',default_count))
        isolated=data.get('isolated',False)
        if type(isolated) is not bool:raise UserError('isolated 须为布尔值')
        report,preview=self.runtime_check(content,clip,count,closeup=anchor,isolated=isolated)
        diagnostic={'kind':kind,'id':entity,'revision':revision,'animation':clip,'resource_sha256':resource_digest(content),**report['closeup']}
        return diagnostic,preview

    def review_bundle(self,kind,entity,data):
        from .review_bundle import prepare,read_file
        bundle=prepare(self,kind,entity,data)
        if data.get('panel') is None:return bundle
        panel=next((p for p in bundle['panels'] if p['key']==data['panel']),None)
        if panel is None:raise UserError('复检图编号不存在，请先读取复检清单')
        return bundle,panel,read_file(self.root,bundle['bundle_id'],panel['file'])

    def contact_check(self,content,animation='walk',options=None):
        """Inspect an immutable export snapshot without editing assets or generating images."""
        from .contact_diagnostics import diagnose_contact
        if not isinstance(animation,str) or not animation or len(animation)>200:raise UserError('请指定实际动作名')
        if options is not None and not isinstance(options,dict):raise UserError('接地诊断 options 须为 JSON 对象')
        if len(content)>64*1024*1024:raise UserError('接地诊断资源包最多64 MB')
        with tempfile.TemporaryDirectory(prefix='spine-contact-') as tmp:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                infos=z.infolist();names=[i.filename for i in infos]
                if len(names)>1024 or len(set(names))!=len(names) or sum(i.file_size for i in infos)>128*1024*1024:raise UserError('接地诊断资源包过大或文件重名')
                for n in names:
                    if Path(n).is_absolute() or '..' in Path(n).parts or '\\' in n:raise UserError('资源包路径无效')
                skeleton=None;skeleton_name=None
                for info in infos:
                    if info.filename.endswith('.json') and info.file_size<8*1024*1024:
                        try:candidate=json.loads(z.read(info.filename))
                        except (ValueError,UnicodeError):continue
                        if isinstance(candidate,dict) and 'bones' in candidate:
                            skeleton=candidate;skeleton_name=info.filename;break
                if skeleton is None:raise UserError('接地诊断需要可读取的 Spine JSON')
                version=str(skeleton.get('skeleton',{}).get('spine',''))[:3]
                atlas_name=next((n for n in names if n.endswith('.atlas')),None)
                if atlas_name is None:raise UserError('接地诊断需要 atlas')
                z.extractall(tmp)
            resolved=dict(options or {});resolved.setdefault('skeleton_name',skeleton_name);resolved.setdefault('atlas_name',atlas_name)
            report=diagnose_contact(Path(tmp),version,animation,resolved)
        return {**report,'animation':animation,'resource_sha256':resource_digest(content)}

    def reserve(self,t,key,count,payload,baseline_sheets=()):
        digest=sha(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode())
        old=t['reservations'].get(key)
        if old:
            if old['sha']!=digest:raise UserError('同一生图编号已用于不同请求')
            return
        image_count=count
        if image_count and (t['max_generations']==0 or t.get('image_preparation')=='preserve'):raise UserError('当前任务禁止生图，基础图也不例外')
        claimed={sheet for v in t['reservations'].values() for sheet in v.get('baseline_sheets',[])}
        baseline=[sheet for sheet in baseline_sheets if sheet in ('turnaround','character-assets') and sheet not in claimed] if t.get('turnaround_contract') else []
        count=image_count-len(baseline)
        if sum(v['count'] for v in t['reservations'].values())+count>t['max_generations']:raise UserError('超过任务补图／修订额度，未提交新的生成')
        t['reservations'][key]={'count':count,'image_count':image_count,'baseline_count':len(baseline),'baseline_sheets':baseline,'sha':digest};self.save_tool_state(t)

    def guard_generation(self,t):
        """Stop a pipeline after an ambiguous submission or review rejection.

        Only jobs reserved by this task are relevant. An older source's rejected
        attempt is not authorization to overwrite its assets or retry it.
        """
        for entity in t['owned'].get('character',[]):
            keys={key.split(':',1)[1] for key in t['reservations'] if key.startswith(entity+':')}
            if not keys:continue
            directory=self.loaders['character'](entity).directory/'jobs'
            for path in directory.glob('*.json'):
                job=json.loads(path.read_text())
                if job.get('generation_nonce',job.get('id')) not in keys:continue
                if job.get('failure',{}).get('stop_generation'):
                    raise UserError('本次生图已被审核拦截，停止后续自动生成；保留任务记录，不自动改写、换图或切换模型')
                if job.get('status')=='submission_unknown':
                    raise UserError('本次生图提交状态尚未确认，停止新提交；请先核实并查询原任务，避免重复计费')

    def tool(self,id,name,args):
        with self.task_locks.for_task(id):
            try:
                from .stages import run_stage
                stage='vision' if name in ('spine_assess_image','spine_review_parts','spine_review_turnaround','spine_review_regions') else 'tool:'+name
                return run_stage(self,id,stage,sha(json.dumps(args,sort_keys=True).encode()),lambda:self._tool(id,name,args))
            except ProviderError as error:
                self.defer_reference(id,name,args,error)
                raise

    def _tool(self,id,name,args):
        with self.task_locks.for_task(id):
            t=self.read(id)
            if t['state'] not in ('queued','running'):raise UserError('任务已结束，不能继续调用工具')
            if t['calls']>=512:raise UserError('超过工具调用上限')
            t['calls']+=1;t['stage']=name;self.save_tool_state(t)
            if name=='spine_images':
                requests=args.get('images')
                if not isinstance(requests,list) or not 1<=len(requests)<=8:raise UserError('批量检查图须为1–8项')
                for item in requests:
                    if not isinstance(item,dict):raise UserError('检查图参数须为对象')
                    self.own(t,item.get('kind'),item.get('id'))
                content=[]
                for item in requests:
                    result=self._tool(id,'spine_image',item);content.extend(result['content'])
                return {'content':content}
            if name=='spine_context':return {'image_generation':self.image_provider_info(),'requirements':t['requirements'],'sources':t['sources'],'owned':t['owned'],'automatic_animation_enabled':t.get('automatic_animation_enabled',True),'animation_transitions':t.get('animation_transitions',{}),'material_animation':t.get('material_animation',False),'automatic_animation':t.get('automatic_animation',{}),'budget':self.summary(id)['generation_budget'],'max_repairs':t['max_repairs'],'edit_budget':{'used':t['edits'],'limit':max(1,len(t['sources']))*(t['max_repairs']+1)},'image_preparation':t.get('image_preparation','preserve'),'image_assessments':t.get('image_assessments',{}),'turnaround_contract':t.get('turnaround_contract'),'turnaround_checks':t.get('turnaround_checks',{}),'canvas_contract':t.get('canvas_contract'),'connection_contract':t.get('connection_contract'),'orientation_contract':t.get('orientation_contract'),'integrity_contract':t.get('integrity_contract'),'garment_contract':t.get('garment_contract'),'detail_contract':t.get('detail_contract'),'seam_contract':t.get('seam_contract'),'preparation_contract':t.get('preparation_contract'),'part_checks':t.get('part_checks',{}),'production_reference':copy.deepcopy(REFERENCE_PROFILE),'capabilities':['runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-sole-contact','continuous-limb-prompts','pixel-contact-diagnostics','smooth-gait-curves','simple-result-view','concise-art-prompts','safe-generation-failures','character-asset-sheet-first','side-walk-preparation','joint-reviewed-assembly','polygon-part-crops','neck-seam-closeups','scoped-texture-repair','scoped-material-finish','contrast-material-review','turnaround-reference-review','multi-region-closeups','asset-integrity-review','costume-completion','strict-layer-anchors','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling','platform-review-bundle','scoped-edge-cleanup','per-side-orientation-review','natural-walk-posture','assembly-connection-review','reviewed-occlusion-overlay','assembly-source-snapshot']}
            if name=='spine_deliver_draft':return self.publish_draft(id,args['id'],args['summary'])
            if name=='spine_assess_image':
                entity=args['id'];self.own(t,'character',entity);c=self.loaders['character'](entity)
                image_id=c.meta.get('image_id')
                if not image_id or t.get('image_checks',{}).get(entity)!=image_id:raise UserError('先用 spine_image 查看当前角色原图，再记录视角与遮挡检查')
                assessment=checked_assessment(args,image_id)
                from .animation_transition import enabled
                if assessment['parts_reviewed'] and c.asset_status()['ready'] and enabled(t,entity):
                    t.update(detail_contract='articulation-v1',orientation_contract=ORIENTATION_CONTRACT,connection_contract=CONNECTION_CONTRACT,integrity_contract=INTEGRITY_CONTRACT,garment_contract=GARMENT_CONTRACT)
                if t.get('preparation_contract')=='side-parts-v2' and assessment['verdict']=='ready':
                    require_side_camera(assessment,t['requirements'])
                    if assessment['parts_reviewed'] and not all(checked_connections(assessment.get('joint_review')).values()):
                        raise UserError('肩肘腕／髋膝踝仍有连接问题，修整后重新检查当前组装')
                if t.get('integrity_contract')==INTEGRITY_CONTRACT and assessment['parts_reviewed']:
                    assessment['material_checks']=checked_material_review(args)
                if t.get('orientation_contract')==ORIENTATION_CONTRACT and assessment['parts_reviewed']:
                    assessment['orientation_checks']=checked_orientation(args)
                if t.get('image_preparation')=='asset-sheet' and assessment['parts_reviewed']:
                    from .animation_transition import assembly_seen
                    if not assembly_seen(t,c):raise UserError('先查看当前版本的 assembly 组装图，再记录部件已检查')
                    if not c.asset_status()['ready']:raise UserError('完整部件尚未全部接入，请检查缺失部件与连续臂／腿鞋链')
                if assessment['source_kind']=='asset_sheet' and not t.get('reservations'):
                    t.pop('turnaround_contract',None) # user-supplied complete parts, no paid reference required
                assessment['revision']=c.meta['revision'];assessment['recorded_at']=time.time()
                if assessment['parts_reviewed'] and c.asset_status()['ready']:assessment['source_digest']=c.prebind_evidence()[0]['digest']
                t.setdefault('image_assessments',{})[entity]=assessment;self.save_tool_state(t)
                from .animation_transition import advance
                transition=advance(self,id,entity)
                built=self.read(id).get('automatic_animation',{}).get(entity) if transition and transition['phase'] in ('review_pending','review_preparing','review_ready') else None
                return {**assessment,**({'animation_transition':transition} if transition else {}),**({'automatic_animation':built} if built else {})}
            if name=='spine_review_turnaround':
                from .turnaround_review import checked_review,candidate
                entity=args['id'];self.own(t,'character',entity);c=self.loaders['character'](entity);job_id=nonce(args['job_id'])
                evidence=candidate(c,job_id);review=checked_review(args)
                if t.get('candidate_checks',{}).get(entity+':'+job_id)!=c.meta['image_id'] or t.get('candidate_pixel_checks',{}).get(entity+':'+job_id)!=evidence['sha256']:raise UserError('先用spine_image candidate实际查看当前三视图像素')
                from .animation_transition import assembly_seen,advance
                if review['phase']=='assembly' and not assembly_seen(t,c):raise UserError('先查看当前assembly再对照三视图')
                review.update(**evidence,revision=c.meta['revision'],recorded_at=time.time())
                if review['phase']=='assembly':review['source_digest']=c.prebind_evidence()[0]['digest']
                t.setdefault('turnaround_checks',{}).setdefault(entity,{})[review['phase']]=review
                self.save_tool_state(t)
                transition=advance(self,id,entity)
                return {**review,**({'animation_transition':transition} if transition else {})}
            if name=='spine_review_parts':
                entity=args['id'];self.own(t,'character',entity);c=self.loaders['character'](entity);job_id=args.get('job_id')
                key=entity+':'+str(job_id or 'source')
                if job_id:
                    nonce(job_id)
                    if t.get('candidate_checks',{}).get(entity+':'+job_id)!=c.meta['image_id']:raise UserError('先实际查看当前候选部件图再检查部件')
                elif t.get('image_checks',{}).get(entity)!=c.meta['image_id']:raise UserError('先实际查看当前原部件图')
                review=checked_part_review(args)
                if t.get('orientation_contract')==ORIENTATION_CONTRACT:
                    review['orientation_checks']=checked_orientation(args)
                if t.get('integrity_contract')==INTEGRITY_CONTRACT:
                    review['material_checks']=checked_material_review(args)
                    if job_id:
                        pixels=(c.directory/'jobs'/(job_id+'.png')).read_bytes()
                        if t.get('candidate_pixel_checks',{}).get(entity+':'+job_id)!=sha(pixels):raise UserError('候选图像素已变化，请重新看图再记录部件检查')
                        review['candidate_sha256']=sha(pixels)
                assessment=t.get('image_assessments',{}).get(entity,{})
                if t.get('preparation_contract')=='side-parts-v2' and review['verdict']!='unsupported':require_side_camera({**assessment,**review},t['requirements'])
                review.update(image_id=c.meta['image_id'],revision=c.meta['revision'],job_id=job_id,recorded_at=time.time())
                t.setdefault('part_checks',{})[key]=review;self.save_tool_state(t);return review
            if name=='spine_reference':
                kind=args.get('kind');entity=args.get('id');self.own(t,kind,entity)
                if kind!='character':raise UserError('参考图自动关联目前支持单图角色')
                action=args.get('action');path='/api/characters/'+entity+'/reference'
                if action=='ensure':return self.api(path,{'action':'ensure','revision':args.get('revision')})
                if action=='register':return self.api(path,{'url':args.get('url'),'revision':args.get('revision')})
                if action!='prepare':raise UserError('参考图操作无效')
                result=self.api(path)
                if not result.get('cached'):
                    result['image_data']=base64.b64encode(self.api(result['image_url'],binary=True)).decode()
                return result
            if name=='spine_wait':
                delay=args.get('seconds',5)
                if type(delay)!=int or not 1<=delay<=20:raise UserError('seconds 须为 1–20')
                self.stop.wait(delay);return {'state':self.read(id)['state']}
            if name in ('spine_inspect','spine_image','spine_character'):
                kind=args.get('kind','character' if name=='spine_character' else None);entity=args['id'];self.own(t,kind,entity)
                route='/api/'+KINDS[kind]+'/'+entity
                if name=='spine_inspect':return self.api(route)
                if name=='spine_image':
                    view=args.get('view','image');query=''
                    if kind=='character' and view=='candidate':
                        job_id=nonce(args['job_id']);path=self.storage/'characters'/entity/'jobs'/(job_id+'.png')
                        if not path.is_file():raise UserError('候选图片尚未完成')
                        content=path.read_bytes()
                    else:content=None
                    if kind=='rig' and view=='material':
                        rig=self.loaders['rig'](entity);material=next((r for r in rig.meta.get('material_sources',[]) if r['id']==args.get('part')),None)
                        if material is None:raise UserError('找不到当前骨架保留的原像素参考')
                        content=rig.blob(material)
                    elif kind=='character' and view=='stress-image':
                        from .assembly_stress import panel
                        manifest,_,content=panel(self.loaders['character'](entity),args.get('part'))

                        if args.get('crop') is None:t.setdefault('stress_seen',{}).setdefault(entity,{})[args.get('part')]=manifest['digest'];self.save_tool_state(t)
                    elif kind=='character' and view in ('image','assembly','reconstruction-image','layer-overlap-image','prebind-parts','prebind-assembly','prebind-spread','edge-review-image'):pass
                    elif content is not None:pass
                    elif view=='part':
                        from urllib.parse import urlencode
                        query='?'+urlencode({'name' if kind=='character' else 'id':args['part']})
                    else:raise UserError('图像只支持单图原图／组装图或指定部件')
                    from PIL import Image
                    raw_image=content if content is not None else self.api(route+'/'+view+query,binary=True)
                    im=Image.open(io.BytesIO(raw_image));pixel_size=im.size
                    from .image_evidence import select
                    im,selection=select(im,args,view)
                    if kind=='character' and view=='image' and args.get('crop') is None:
                        t.setdefault('image_checks',{})[entity]=self.loaders['character'](entity).meta['image_id'];self.save_tool_state(t)
                    elif kind=='character' and view.startswith('prebind-') and args.get('crop') is None and args.get('pose') is None:
                        t.setdefault('prebind_seen',{}).setdefault(entity,{})[view]=self.loaders['character'](entity).prebind_evidence()[0]['digest'];self.save_tool_state(t)
                    elif kind=='character' and view=='layer-overlap-image':
                        t.setdefault('overlap_seen',{})[entity]=self.loaders['character'](entity).layer_overlap()[0]['digest'];self.save_tool_state(t)
                    elif kind=='character' and view=='reconstruction-image':
                        t.setdefault('reconstruction_seen',{})[entity]=self.loaders['character'](entity).reconstruction()[0]['digest'];self.save_tool_state(t)
                    elif kind=='character' and view=='assembly' and args.get('crop') is None:
                        c=self.loaders['character'](entity)
                        if sha(raw_image)!=sha(png_bytes(c.assembly())):raise UserError('读取期间组装已变化，请重新查看当前图')
                        t.setdefault('assembly_checks',{})[entity]=c.meta['revision']
                        if c.asset_status()['ready']:t.setdefault('assembly_digests',{})[entity]=c.prebind_evidence()[0]['digest']
                        self.save_tool_state(t)
                    elif kind=='character' and view=='candidate':
                        t.setdefault('candidate_checks',{})[entity+':'+job_id]=self.loaders['character'](entity).meta['image_id'];t.setdefault('candidate_pixel_checks',{})[entity+':'+job_id]=sha(content);self.save_tool_state(t)
                    return {'content':[{'type':'image','mimeType':'image/png','data':base64.b64encode(png_bytes(im.convert('RGBA'))).decode()},
                                       {'type':'text','text':json.dumps({'view':view,'selection':selection,'pixel_size':list(pixel_size),'preview_size':list(im.size),'crop_coordinates':'Use original pixel_size coordinates for box/pieces, not thumbnail pixels.'})}]}
                action=args['action'];payload=args.get('data',{})
                if t.get('image_preparation')=='asset-sheet' and 'sheets' not in payload and (action=='generate-parts' or action=='generation-plan' and payload.get('mode')=='complete-parts'):
                    payload={**payload,'sheets':['turnaround','character-assets'] if t.get('turnaround_contract') else ['character-assets']}
                if t.get('image_preparation')=='asset-sheet' and 'character-assets' in payload.get('sheets',[]):payload={'body_layout':'combined',**payload}
                if action in ('generate-parts','generation-plan') and t.get('turnaround_contract') and 'character-assets' in payload.get('sheets',[]) and 'turnaround' not in payload['sheets']:
                    from .turnaround_review import require_review
                    try:require_review(t,self.loaders['character'](entity),'reference')
                    except UserError:payload={**payload,'sheets':['turnaround',*payload['sheets']]}
                if action=='generate-parts' or action=='generation-plan' and payload.get('mode')=='complete-parts':
                    from .part_prompts import complete_generation_request
                    payload=complete_generation_request(payload)
                if action in ('generate-parts','generation-plan') and payload.get('completion_only'):
                    from .garment_templates import generation_request
                    prepared,_=generation_request(self.loaders['character'](entity),payload)
                    payload={**payload,'sheets':prepared['sheets']}
                t['stage_detail']=action;self.save_tool_state(t)
                if action not in CHAR_ACTIONS:raise UserError('不允许此单图操作')
                plan=payload if action=='prepare-assembly' else payload.get('assembly_plan',{})
                if not isinstance(plan,dict):raise UserError('装配方案须为对象')
                for replacement in plan.get('replacements',[]):self.own(t,'character',replacement.get('character_id'))
                if action=='review-stress':
                    from .assembly_stress import read_manifest
                    manifest=read_manifest(self.loaders['character'](entity))
                    if any(t.get('stress_seen',{}).get(entity,{}).get(p['key'])!=manifest['digest'] for p in manifest['panels']):raise UserError('先逐张查看当前骨骼压力测试全身、腰胯与肩带图')
                if action=='review-prebind' and any(t.get('prebind_seen',{}).get(entity,{}).get(v)!=payload.get('digest') for v in ('prebind-parts','prebind-assembly','prebind-spread')):raise UserError('先查看当前完整部件、试拼和摆开三张图')
                if action=='review-overlap' and t.get('overlap_seen',{}).get(entity)!=payload.get('digest'):raise UserError('先查看当前layer-overlap-image')
                if action=='review-reconstruction' and t.get('reconstruction_seen',{}).get(entity)!=payload.get('digest'):raise UserError('先查看当前reconstruction-image，再记录拼回验收')
                if action=='build' and t.get('canvas_contract')=='canvas-layers-v1' and self.loaders['character'](entity).meta.get('canvas_contract')!='canvas-layers-v1':raise UserError('先安装同坐标图层并完成拼回验收；不能用格子布局代替母版位置')
                if action in ('apply-source-layout','apply-part-layout') and t.get('preparation_contract')=='side-parts-v2':
                    c=self.loaders['character'](entity);review=t.get('part_checks',{}).get(entity+':'+str(payload.get('job_id') if action=='apply-part-layout' else 'source'),{})
                    if review.get('image_id')!=c.meta['image_id'] or review.get('revision')!=c.meta['revision'] or review.get('verdict') not in ('ready','needs_adjustment'):
                        raise UserError('先用 spine_review_parts 检查当前部件图与六组连接，再裁切修整和组装')
                    if t.get('integrity_contract')==INTEGRITY_CONTRACT and action=='apply-part-layout':
                        path=c.directory/'jobs'/(nonce(payload.get('job_id'))+'.png')
                        if not path.is_file() or review.get('candidate_sha256')!=sha(path.read_bytes()):raise UserError('部件候选已变化，先重新查看并审查当前图面')
                if t.get('turnaround_contract') and action in ('apply-part-layout','build'):
                    from .turnaround_review import require_review
                    reference_review=require_review(t,self.loaders['character'](entity),'assembly' if action=='build' else 'reference')
                    if action=='build':
                        c=self.loaders['character'](entity)
                        if any(k in payload and payload[k]!=c.meta.get(k,{}) for k in ('landmarks','settings','masks')):raise UserError('build参数与已验收组装不一致，请先更新guide并复检')
                        c.meta['turnaround_reference']=reference_review;atomic_json(c.directory/'character.json',c.meta)
                if action=='apply-source-layout':
                    c=self.loaders['character'](entity);a=t.get('image_assessments',{}).get(entity,{})
                    if a.get('source_kind')!='asset_sheet' or a.get('image_id')!=c.meta.get('image_id') or a.get('revision')!=c.meta['revision']:raise UserError('先检查当前原图并确认是部件图，再导入实际布局')
                if action=='apply-part-layout' and t.get('image_preparation')=='asset-sheet':
                    c=self.loaders['character'](entity)
                    if t.get('candidate_checks',{}).get(entity+':'+str(payload.get('job_id','')))!=c.meta['image_id']:raise UserError('先查看候选部件图，再应用实际裁片与关节')
                if action in ('build','generate-view','generate-parts'):
                    c=self.loaders['character'](entity);assessment=t.get('image_assessments',{}).get(entity,{})
                    assessment_current=(assessment.get('source_digest')==c.prebind_evidence()[0]['digest'] if action=='build' and assessment.get('source_digest') else assessment.get('revision')==c.meta['revision'])
                    if assessment.get('image_id')!=c.meta.get('image_id') or not assessment_current:raise UserError('角色图或部件已变化，请查看图片并重新记录适配检查')
                    if action in ('build','generate-parts') and t.get('preparation_contract')=='side-parts-v2':require_side_camera(assessment,t['requirements'])
                    if action=='build' and assessment.get('verdict')!='ready':raise UserError('图片尚不适合目标动作，请先补视角／部件或说明无法制作')
                    if action=='build' and t.get('orientation_contract')==ORIENTATION_CONTRACT:
                        checked_orientation(assessment)
                    if action=='build' and t.get('integrity_contract')==INTEGRITY_CONTRACT:
                        checked_material_review(assessment)
                    if action=='build' and t.get('image_preparation')=='asset-sheet':
                        from .animation_transition import assembly_seen
                        if not c.asset_status()['ready'] or not assessment.get('parts_reviewed') or not assembly_seen(t,c):raise UserError('先接入完整部件，查看当前 assembly 并记录 parts_reviewed=true，再绑定动画')
                    if action=='generate-view':
                        if t.get('image_preparation') not in ('auto','asset-sheet'):raise UserError('当前要求保留原图，不允许自动换视角')
                        if assessment.get('verdict')!='needs_view':raise UserError('检查记录未要求补视角')
                        payload={**payload,'target_view':assessment['target_view'],'target_actions':assessment['target_actions']}
                    if action=='generate-parts' and assessment.get('verdict')=='unsupported':raise UserError('当前图无法适配目标动作，不能用部件补图掩盖问题')
                if action in ('generate','generate-view','generate-parts'):
                    if action=='generate-parts':
                        if t.get('image_preparation')=='asset-sheet' and 'sheets' not in payload:payload={**payload,'sheets':['turnaround','character-assets'] if t.get('turnaround_contract') else ['character-assets']}
                        from .part_prompts import generation_plan
                        from .garment_templates import generation_request
                        prepared,_=generation_request(c,{**payload,'mode':'complete-parts'})
                        generation_plan(prepared,c.meta['settings']['facing'])
                    key=nonce(payload.get('nonce'));count=len(selected_sheets(payload.get('sheets'))) if action=='generate-parts' else 1
                    if entity+':'+key not in t['reservations']:self.guard_generation(t)
                    self.reserve(t,entity+':'+key,count,payload,selected_sheets(payload.get('sheets')) if action=='generate-parts' else ())
                result=self.api(route+'/'+action,payload)
                from .generation_flow import MATERIAL_ACTIONS,prepare
                if action in MATERIAL_ACTIONS:
                    prepare(self,id,entity)
                    t=self.read(id)
                if action in MATERIAL_ACTIONS:
                    from .animation_transition import advance
                    transition=advance(self,id,entity);t=self.read(id)
                    if transition:result={**result,'animation_transition':transition}
                    built=t.get('automatic_animation',{}).get(entity) if transition and transition['phase'] in ('review_pending','review_preparing','review_ready') else None
                    if built:result['automatic_animation']=built
                if action=='build' and t.get('turnaround_contract'):
                    # Saving a version increments revision without changing the reviewed artwork.
                    t['turnaround_checks'][entity]['assembly']['revision']=result['revision']
                if action in ('build','foot-layers'):
                    for v in result.get('versions',[]):
                        for k,field in (('rig','rig_id'),('project','source_id')):
                            if v[field] not in t['owned'][k]:t['owned'][k].append(v[field])
                    self.save_tool_state(t)
                return result
            if name=='spine_create_rig':
                self.own(t,'project',args['project_id'])
                result=self.api('/api/rigs/create',{'source_id':args['project_id'],'revision':args['revision']})
                t['owned']['rig'].append(result['id']);self.save_tool_state(t);return result
            if name=='spine_edit_rig':
                self.own(t,'rig',args['source_rig_id'])
                for op in args.get('operations',[]):
                    if op.get('action')=='copy-animation':self.own(t,'rig',op['source_rig_id'])
                    if op.get('action') in ('texture-repair','material-finish','occlusion-overlay'):
                        key='rig:'+args['source_rig_id'];current=resource_digest(self.loaders['rig'](args['source_rig_id']).export())
                        if t.get('closeup_checks',{}).get(key,{}).get('sha256')!=current:raise UserError('先查看当前资源的spine_closeup局部图，再按实际像素修复')
                completed_before=self.rig_automation.path(args.get('nonce')).exists()
                if not completed_before and t['edits']>=max(1,len(t['sources']))*(t['max_repairs']+1):raise UserError('已达到骨架修正次数上限')
                result=self.rig_automation.create(args)
                if not completed_before:t['edits']+=1
                if result['rig_id'] not in t['owned']['rig']:t['owned']['rig'].append(result['rig_id'])
                self.save_tool_state(t);return result
            if name=='spine_reskin':
                action=args.get('action','plan');payload=args.get('data',{})
                if action in ('plan','submit'):
                    self.own(t,'project',payload['project_id']);payload={**payload,'max_generations':t['max_generations']}
                    plan=self.api('/api/automation/reskin/plan',payload)
                    if action=='plan':return plan
                    key=nonce(payload.get('nonce'));self.reserve(t,'reskin:'+key,plan['generation_count'],payload)
                    result=self.api('/api/automation/reskin/tasks',payload)
                elif action=='status':
                    key=nonce(payload.get('nonce'))
                    if 'reskin:'+key not in t['reservations']:raise UserError('换皮任务不属于当前 Agent 任务')
                    result=self.api('/api/automation/reskin/tasks/'+key)
                else:raise UserError('换皮只支持 plan、submit、status')
                if result.get('state')=='completed':
                    out=result['output_project_id']
                    if out not in t['owned']['project']:t['owned']['project'].append(out)
                    self.save_tool_state(t)
                return result
            if name=='spine_validate':
                kind=args['kind'];entity=args['id'];self.own(t,kind,entity)
                if kind not in ('rig','project'):raise UserError('请校验生成后的 rig 或 project')
                return self.api('/api/'+KINDS[kind]+'/'+entity+'/validate')
            if name=='spine_review_regions':
                kind=args['kind'];entity=args['id'];self.own(t,kind,entity)
                entries=args.get('reviews')
                if not isinstance(entries,list) or not 1<=len(entries)<=64 or any(not isinstance(e,dict) for e in entries):raise UserError('reviews须为1–64项实际局部验收记录')
                pairs=[(e.get('animation'),e.get('anchor','head')) for e in entries]
                if any(not isinstance(c,str) or not isinstance(a,str) for c,a in pairs) or len(set(pairs))!=len(pairs):raise UserError('每个动作与区域只记录一次')
                source=self.loaders[kind](entity)
                with source.lock:
                    current=resource_digest(source.export());skeleton=copy.deepcopy(source.state['skeleton'] if kind=='rig' else source.skeleton_data)
                updated=copy.deepcopy(t);result=[]
                for entry in entries:result.append(self.record_closeup_review(updated,{**entry,'kind':kind,'id':entity},current,skeleton))
                self.save_tool_state(updated);return {'resource_sha256':current,'recorded':len(result),'items':result}
            if name=='spine_review_bundle':
                kind=args['kind'];entity=args['id'];self.own(t,kind,entity)
                result=self.review_bundle(kind,entity,args)
                if args.get('panel') is None:return result
                bundle,panel,png=result;key=kind+':'+entity;digest=bundle['resource_sha256']
                if panel['anchor'] is None:
                    review=motion_review(t.setdefault('motion_checks',{}),key,digest)
                    if panel['animation'] not in review['animations']:review['animations'].append(panel['animation'])
                    review['frame_counts'][panel['animation']]=max(panel['frames'],review['frame_counts'].get(panel['animation'],0))
                else:
                    check=t.setdefault('closeup_checks',{}).setdefault(key,{})
                    if check.get('sha256')!=digest:check.clear();check.update(sha256=digest,clips={})
                    item={'report':{**panel['report'],'resource_sha256':digest,'animation':panel['animation']}}
                    check.setdefault('regions',{}).setdefault(panel['animation'],{})[panel['anchor']]=item
                    if panel['anchor']=='head':check['clips'][panel['animation']]=item
                self.save_tool_state(t)
                return {'content':[{'type':'text','text':json.dumps({**panel,'resource_sha256':digest})}, {'type':'image','mimeType':'image/png','data':base64.b64encode(png).decode()}]}
            if name=='spine_contact':
                kind=args['kind'];entity=args['id'];self.own(t,kind,entity)
                report=self.contact_target(kind,entity,args)
                t.setdefault('contact_checks',{})[kind+':'+entity]=report
                self.save_tool_state(t)
                return report
            if name in ('spine_closeup','spine_review_closeup'):
                kind=args['kind'];entity=args['id'];self.own(t,kind,entity);key=kind+':'+entity
                if name=='spine_closeup':
                    report,preview=self.closeup_target(kind,entity,args)
                    if report.get('isolated'):
                        return {'content':[{'type':'text','text':json.dumps(report)},{'type':'image','mimeType':'image/png','data':base64.b64encode(preview).decode()}]}
                    checks=t.setdefault('closeup_checks',{});review=checks.setdefault(key,{})
                    if review.get('sha256')!=report['resource_sha256']:review.clear();review.update(sha256=report['resource_sha256'],clips={})
                    anchor=report['anchor'];item={'report':report}
                    review.setdefault('regions',{}).setdefault(report['animation'],{})[anchor]=item
                    if anchor=='head':review['clips'][report['animation']]=item
                    self.save_tool_state(t)
                    return {'content':[{'type':'text','text':json.dumps(report)}, {'type':'image','mimeType':'image/png','data':base64.b64encode(preview).decode()}]}
                source=self.loaders[kind](entity)
                with source.lock:
                    current=resource_digest(source.export());skeleton=source.state['skeleton'] if kind=='rig' else source.skeleton_data
                item=self.record_closeup_review(t,args,current,skeleton)
                self.save_tool_state(t);return item
            if name in ('spine_motion','spine_gait'):
                kind=args['kind'];entity=args['id'];self.own(t,kind,entity)
                if kind not in ('rig','project'):raise UserError('动作预览须使用 rig 或 project')
                clip=args.get('animation','__setup__')
                content=self.api('/api/'+KINDS[kind]+'/'+entity+'/export',binary=True)
                count=frame_count(args.get('frame_count',8))
                gait={k:args[k] for k in ('pairs','anchor','axis') if k in args} if name=='spine_gait' else None
                report,preview=self.runtime_check(content,clip,count,gait)
                if name=='spine_gait':return report
                key=kind+':'+entity
                digest=resource_digest(content)
                review=motion_review(t.setdefault('motion_checks',{}),key,digest)
                if clip not in review['animations']:review['animations'].append(clip)
                previous=review['frame_counts'].get(clip,0)
                review['frame_counts'][clip]=max(count,previous if type(previous)==int else 0)
                self.save_tool_state(t)
                return {'content':[{'type':'text','text':json.dumps({'animation':clip,'frames':count,'validation':report})},
                                   {'type':'image','mimeType':'image/png','data':base64.b64encode(preview).decode()}]}
            raise UserError('未知 Agent 工具')

    def record_closeup_review(self,t,args,current,skeleton):
        key=args['kind']+':'+args['id']
        check=t.get('closeup_checks',{}).get(key,{})
        clip=args['animation'];anchor=args.get('anchor','head');item=check.get('regions',{}).get(clip,{}).get(anchor,{})
        if not item and anchor=='head':item=check.get('clips',{}).get(clip,{})
        if check.get('sha256')!=current or not item.get('report'):raise UserError('先查看当前资源对应动作的8帧局部图，修改后旧检查失效')
        if args.get('verdict') not in ('ready','needs_repair','unsupported') or not isinstance(args.get('reason'),str) or not args['reason'].strip():raise UserError('局部复检须记录真实结论与原因')
        material=checked_material_review(args,anchor) if t.get('integrity_contract')==INTEGRITY_CONTRACT else None
        assessment={'verdict':args['verdict'],'reason':args['reason'][:1000],'recorded_at':time.time()}
        if material is not None:assessment['material_checks']=material
        if t.get('connection_contract') in CONNECTION_CONTRACTS and anchor in CONNECTION_CHECKS:
            assessment['connection_checks']=checked_connection(args,anchor,t['connection_contract'])
        if t.get('orientation_contract')==ORIENTATION_CONTRACT and orientation_fields(anchor):
            assessment['orientation_checks']=checked_orientation(args,anchor)
        if t.get('garment_contract')==GARMENT_CONTRACT and anchor in garment_anchors(skeleton):
            if item['report'].get('frames',0)<garment_frame_count(clip):raise UserError('先查看足够帧数的当前腿部放大图，行走须32帧')
            assessment['garment_checks']=checked_garment_review(args)
        item['review']=assessment
        if anchor=='head':check.setdefault('clips',{})[clip]=copy.deepcopy(item)
        return item

    def runtime_check(self,content,preview_clip=None,count=8,gait=None,closeup=None,isolated=False):
        count=frame_count(count)
        if isolated and not closeup:raise UserError('隔离诊断须指定局部 anchor')
        if not preview_clip or gait is not None:
            return self._runtime_check_uncached(content,preview_clip,count,gait,closeup,isolated)
        files=['app/agent_tasks.py','app/seam_review.py','tools/render_rig_evidence.py',
               'tools/validate_agent_runtime.cjs','tools/verify_rig_runtime.cjs']
        files.extend(str(p.relative_to(self.root)) for p in sorted((self.root/'web/runtime').glob('*/spine-player.js')))
        renderer=sha(b''.join((self.root/n).read_bytes() for n in files))
        key=(str(self.root),resource_digest(content),renderer,preview_clip,count,closeup,isolated)
        return runtime_previews.get(key,lambda:self._runtime_check_uncached(content,preview_clip,count,gait,closeup,isolated))

    def _runtime_check_uncached(self,content,preview_clip=None,count=8,gait=None,closeup=None,isolated=False):
        count=frame_count(count)
        with tempfile.TemporaryDirectory(prefix='spine-agent-runtime-') as tmp:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names=z.namelist();sk=next((n for n in names if n.endswith('.json') and n not in ('manifest.json','validation.json') and isinstance(json.loads(z.read(n)),dict) and 'bones' in json.loads(z.read(n))),None)
                atlas=next((n for n in names if n.endswith('.atlas')),None)
                if not sk or not atlas:raise UserError('自动骨骼交付需要可由 runtime 校验的 JSON 与 atlas')
                for n in names:
                    if Path(n).is_absolute() or '..' in Path(n).parts:raise UserError('资源包路径无效')
                z.extractall(tmp)
                version=json.loads(z.read(sk)).get('skeleton',{}).get('spine','')[:3]
            node=os.getenv('SPINE_AGENT_NODE') or shutil.which('node')
            bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
            if not node and bundled.exists():node=str(bundled)
            if not node:raise UserError('缺少 Node，不能完成官方 runtime 校验')
            cmd=[node,str(self.root/'tools/validate_agent_runtime.cjs'),tmp,version,sk,atlas]
            if preview_clip:cmd.extend([preview_clip,json.dumps({'frame_count':count,'gait':gait})])
            # Cache only validated runtime output for identical resources and sampling.
            # Each caller receives a fresh object; pop('frames') cannot poison the cache.
            validator=self.root/'tools/validate_agent_runtime.cjs'
            cache_key=(str(self.root),resource_digest(content),sha(validator.read_bytes()+(self.root/'tools/verify_rig_runtime.cjs').read_bytes()+(self.root/'web/runtime'/version/'spine-player.js').read_bytes()),preview_clip,count,json.dumps(gait,sort_keys=True))
            def sample():
                p=subprocess.run(cmd,capture_output=True,timeout=60,text=True)
                try:report=json.loads(p.stdout)
                except ValueError:raise UserError('Spine runtime 校验失败') from None
                if p.returncode or not report.get('passed'):raise UserError('Spine runtime 校验失败：'+str(report.get('error',''))[:300])
                return report
            report=runtime_reports.get(cache_key,sample)
            frames=report.pop('frames',[])
            if not preview_clip:return report
            if gait is not None:return report,None
            import numpy as np
            from PIL import Image,ImageDraw
            sys.path.insert(0,str(self.root/'tools'))
            from render_rig_evidence import render as rasterize
            textures={}
            def render(*args,**kwargs):return rasterize(*args,**kwargs,textures=textures)
            if closeup:
                from .seam_review import render_closeups
                sheet,diagnostic=render_closeups(frames,Path(tmp)/Path(atlas).parent,closeup,render,isolated=isolated)
                report['closeup']=diagnostic
                return report,png_bytes(sheet.convert('RGBA'))
            xy=np.array([v for f in frames for g in f['geometry'] for v in g['xy']]).reshape(-1,2)
            if not len(xy):raise UserError('动作没有可渲染的附件')
            lo=xy.min(0);hi=xy.max(0)
            if np.any(hi-lo<.001):raise UserError('动作附件显示范围无效')
            view=[*lo,*hi];sheet=Image.new('RGB',(1200,400*((count+3)//4)),'#e5ebf3');draw=ImageDraw.Draw(sheet)
            for i,frame in enumerate(frames):
                tile=render(frame,Path(tmp)/Path(atlas).parent,view,(300,370),20)
                x=i%4*300;y=i//4*400;sheet.paste(tile,(x,y+25));draw.text((x+8,y+5),f'{preview_clip} {i}/{count}',fill='#22324b')
            return report,png_bytes(sheet.convert('RGBA'))

    def publish_draft(self,id,entity,summary):
        """Publish only task-owned, loadable resources; visual approval stays separate."""
        if not isinstance(summary,str) or not 1<=len(summary.strip())<=2000:
            raise UserError('草稿必须说明当前结果和待修问题')
        t=self.read(id);self.own(t,'rig',entity)
        rig=self.loaders['rig'](entity)
        with rig.lock:
            if not rig.validate()['passed']:raise UserError('草稿资源无法加载')
            content=rig.export();report=self.runtime_check(content)
            entry={'rig_id':entity,'revision':rig.meta['revision'],
                   'runtime':rig.state['skeleton'].get('skeleton',{}).get('spine','')[:3],
                   'animations':list(rig.state['skeleton'].get('animations',{})),
                   'summary':summary.strip(),'process_url':None}
        # Carry forward the stable process entry when refining an existing workflow.
        for parent in (id,t.get('workflow_id'),t.get('parent_task_id')):
            if not parent:continue
            path=self.storage/'workflow-deliveries'/(parent+'.json')
            if path.exists():
                entry['process_url']=json.loads(path.read_text()).get('process_url')
                if entry['process_url']:break
        from .workflow_delivery import save_delivery
        with self.lock:
            current=self.read(id)
            if current['state']=='cancelled':raise UserError('任务已停止，不能发布新草稿')
            delivery=save_delivery(self.storage,id,entry)
            artifact={'kind':'rig','id':entity,'revision':entry['revision'],'summary':summary.strip(),
                      'preview_url':'/result.html?rig='+entity,'download_url':'/api/rigs/'+entity+'/export',
                      'runtime_validation':report,'resource_sha256':resource_digest(content),'visual_acceptance':{'status':'draft'}}
            previous=current.get('draft_artifacts',[])
            drafts=[artifact]+[a for a in previous if a['id']!=entity]
            self.update(id,draft_artifacts=drafts,delivery_status='draft')
        return artifact

    def finish(self,id,result):
        t=self.read(id)
        if result.get('outcome')!='completed':
            # A failed visual verdict must not hide an explicitly returned usable draft.
            candidates=result.get('artifacts') or []
            for a in candidates:self.own(t,a['kind'],a['id'])
            for a in candidates:
                if a['kind']=='rig':self.publish_draft(id,a['id'],a.get('summary') or result.get('summary','未通过正式验收'))
            with self.lock:
                if self.read(id)['state']!='cancelled':
                    self.update(id,state='failed',stage='处理未完成',error=str(result.get('summary','AI 未完成此需求'))[:1000])
            return
        if t.get('output_scope')=='materials':
            from .generation_flow import finish_materials
            return finish_materials(self,t,result)
        artifacts=result.get('artifacts')
        if not isinstance(artifacts,list) or not artifacts:raise UserError('没有可交付资源，不标记为完成')
        out=io.BytesIO();deliver=[]
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for i,a in enumerate(artifacts):
                kind=a['kind'];entity=a['id'];self.own(t,kind,entity)
                version=a.get('version','')
                route='/api/'+KINDS[kind]+'/'+entity+'/export'
                if kind=='character':
                    from urllib.parse import urlencode
                    if not version:raise UserError('单图成果必须指定已保存的 version')
                    route+='?'+urlencode({'version':version})
                content=self.api(route,binary=True)
                report=self.runtime_check(content)
                check_kind=kind;check_id=entity
                if kind=='character':
                    character=self.loaders['character'](entity)
                    saved=next((v for v in character.meta['versions'] if v['id']==version),None)
                    if not saved:raise UserError('角色版本不存在')
                    check_kind='rig';check_id=saved['rig_id']
                    checked_content=self.api('/api/rigs/'+check_id+'/export',binary=True)
                else:checked_content=content
                key=check_kind+':'+check_id
                digest=resource_digest(checked_content)
                previous=t.setdefault('motion_checks',{}).get(key,{})
                same_resources=isinstance(previous,dict) and previous.get('sha256')==digest
                reviewed=motion_review(t['motion_checks'],key,digest)
                if not same_resources:self.save_tool_state(t)
                required=report['animations'] or ['__setup__']
                if not same_resources or set(required)-set(reviewed.get('animations',[])):
                    raise UserError('成果尚未查看所有动作的多帧预览，不标记为完成')
                prepared=t.get('image_preparation')=='asset-sheet' and any(s.get('kind')=='character' for s in t['sources'])
                require_requested_motion(t['requirements'],report,reviewed,32 if prepared else 16)
                if t.get('seam_contract')=='neck-seam-v1':
                    skeleton=(self.loaders[check_kind](check_id).state['skeleton'] if check_kind=='rig' else self.loaders[check_kind](check_id).skeleton_data)
                    if any(b['name']=='head' for b in skeleton.get('bones',[])):
                        seam=t.get('closeup_checks',{}).get(key,{})
                        if seam.get('sha256')!=digest or any(seam.get('clips',{}).get(clip,{}).get('review',{}).get('verdict')!='ready' for clip in required):
                            raise UserError('头颈接缝尚未完成当前资源所有动作的8帧放大查看及spine_review_closeup；白底或截口须修复后复检')
                if t.get('detail_contract')=='articulation-v1':
                    from .seam_review import required_detail_anchors,require_detail_reviews
                    skeleton=(self.loaders[check_kind](check_id).state['skeleton'] if check_kind=='rig' else self.loaders[check_kind](check_id).skeleton_data)
                    require_detail_reviews(t.get('closeup_checks',{}).get(key,{}),digest,required,required_detail_anchors(skeleton))
                    if t.get('orientation_contract')==ORIENTATION_CONTRACT:
                        require_orientation_reviews(t.get('closeup_checks',{}).get(key,{}),digest,required,required_detail_anchors(skeleton))
                    if t.get('integrity_contract')==INTEGRITY_CONTRACT:
                        require_material_reviews(t.get('closeup_checks',{}).get(key,{}),digest,required,required_detail_anchors(skeleton))
                if t.get('connection_contract') in CONNECTION_CONTRACTS:
                    skeleton=(self.loaders[check_kind](check_id).state['skeleton'] if check_kind=='rig' else self.loaders[check_kind](check_id).skeleton_data)
                    require_connections(t.get('closeup_checks',{}).get(key,{}),digest,required,skeleton,t['connection_contract'])
                if t.get('garment_contract')==GARMENT_CONTRACT:
                    skeleton=(self.loaders[check_kind](check_id).state['skeleton'] if check_kind=='rig' else self.loaders[check_kind](check_id).skeleton_data)
                    require_garment_reviews(t.get('closeup_checks',{}).get(key,{}),digest,required,skeleton)
                if t.get('turnaround_contract'):
                    from .turnaround_review import require_review
                    for source in t['sources']:
                        if source['kind']=='character':require_review(t,self.loaders['character'](source['id']),'assembly')
                if prepared and re.search(r'行走|走路|步行|奔跑|跑步|\b(?:walk|walking|run|running)\b',t['requirements'],re.I):
                    locomotion=[n for n in report['animations'] if n.lower() in ('walk','walking','run','running')]
                    if locomotion:
                        contact=t.get('contact_checks',{}).get(key,{})
                        if contact.get('resource_sha256')!=digest or contact.get('animation') not in locomotion or contact.get('status')!='assessed':raise UserError('完整部件图成果尚未完成当前资源的鞋底接地诊断，请查看 spine_contact 和32帧实际动作')
                final_skeleton=(self.loaders[check_kind](check_id).state['skeleton'] if check_kind=='rig' else self.loaders[check_kind](check_id).skeleton_data)
                if final_skeleton.get('skeleton',{}).get('gait',{}).get('posture_policy')=='natural-walk-v2' and 'walk' in required:
                    posture=self.contact_check(checked_content,'walk')
                    from .contact_diagnostics import require_natural_walk_contact
                    require_natural_walk_contact(posture)
                filename=f'{i+1:02d}-{kind}-{entity}.zip';z.writestr(filename,content)
                preview=('/result.html?rig='+check_id if kind in ('rig','character') else '/?project='+entity)
                if kind in ('rig','character') and 'walk' in report['animations']:preview+='&animation=walk'
                editor=('/rig.html?rig='+check_id+'&advanced=1' if kind in ('rig','character') else '/?project='+entity)
                contact=t.get('contact_checks',{}).get(key,{})
                contact_fields={'contact_diagnostics':contact} if contact.get('resource_sha256')==digest else {}
                seam=t.get('closeup_checks',{}).get(key,{})
                seam_fields={'seam_diagnostics':seam} if seam.get('sha256')==digest else {}
                reference_fields={}
                if check_kind=='rig':
                    rig=self.loaders['rig'](check_id)
                    material=next((m for m in rig.meta.get('material_sources',[]) if m['id']=='turnaround'),None)
                    if material and rig.meta.get('turnaround_reference'):
                        reference_name=f'references/{i+1:02d}-turnaround.png';z.writestr(reference_name,rig.blob(material))
                        reference_fields={'turnaround_reference':{'file':reference_name,'sha256':material['sha'],'review':rig.meta['turnaround_reference']}}
                visual={'status':'passed','contract':INTEGRITY_CONTRACT,'method':'codex-visual-review','resource_sha256':digest} if t.get('integrity_contract')==INTEGRITY_CONTRACT else {'status':'legacy-review','contract':t.get('detail_contract') or t.get('seam_contract')}
                deliver.append({**a,'visual_acceptance':visual,**reference_fields,**seam_fields,'filename':filename,'sha256':sha(content),'runtime_validation':report,'preview_url':preview,'editor_url':editor,**contact_fields})
            z.writestr('manifest.json',json.dumps({'task_id':id,'requirements':t['requirements'],'turnaround_checks':t.get('turnaround_checks',{}),'artifacts':deliver},ensure_ascii=False,indent=2))
        content=out.getvalue();path=self.path(id).parent/'result.zip';tmp=path.with_suffix('.tmp');tmp.write_bytes(content);tmp.replace(path)
        with self.lock:
            if self.read(id)['state'] in ('cancelled','waiting_reference'):return
            self.update(id,state='completed',stage='已完成 runtime 校验与导出',error=None,artifacts=deliver,result_sha256=sha(content),summary=result.get('summary',''))

    def image_provider_info(self):
        from .image_provider import ImageProvider
        status=ImageProvider(self.storage).status()
        return {key:status[key] for key in ('provider','model','reference_transport','configured')}

    def command(self,id,output,model_config=None):
        executable=os.getenv('SPINE_AGENT_CODEX') or shutil.which('codex')
        if not executable:raise UserError('服务机器未安装 Codex CLI')
        token_env='SPINE_AGENT_TOKEN'
        if model_config is None:
            from .worker_config import model_configuration
            model_config,_=model_configuration()
        configs={**model_config,'mcp_servers.spine.command':sys.executable,
                 'mcp_servers.spine.args':[str(self.root/'agent/spine_mcp.py'),'--base',self.base,'--task',id],
                 'mcp_servers.spine.env_vars':[token_env],
                 'mcp_servers.spine.required':True,'mcp_servers.spine.tool_timeout_sec':90,
                 'mcp_servers.spine.default_tools_approval_mode':'auto'}
        cmd=[executable,'exec','--ignore-user-config','--sandbox','read-only','--skip-git-repo-check','--json',
             '--output-schema',str(self.root/'agent/result.schema.json'),'-o',str(output)]
        for key,value in configs.items():cmd.extend(['-c',key+'='+json.dumps(value,ensure_ascii=False)])
        cmd.append('-');return cmd

    def worker_failure(self,directory):
        """Classify local worker errors without exposing raw logs or secrets."""
        path=Path(directory)/'events.jsonl'
        lines=path.read_text(errors='replace').splitlines()[-12:] if path.exists() else []
        messages=[]
        for line in lines:
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')=='error':messages.append(str(event.get('message','')))
            if event.get('type')=='turn.failed':messages.append(str(event.get('error',{}).get('message','')))
        text=' '.join(messages)
        if '401' in text or 'invalid_api_key' in text:
            return {'code':'worker_authentication_failed','message':'后台模型认证失败（401），请管理员检查后台模型登录与凭据；已准备的素材保留，可恢复原任务，无需重新生图。'}
        return {'code':'worker_execution_failed','message':'后台助手执行失败；已保存素材保留，请检查服务端日志后继续原任务。'}

    def execute(self,id):
        directory=self.path(id).parent;result_path=directory/'agent-result.json'
        prompt=(self.root/'agent/workflow-instructions.txt').read_text()
        from .reference_policy import publication_authorization
        prompt+='\nConfigured image backend (use this provider and transport): '+json.dumps(self.image_provider_info())
        prompt+='\nInstallation reference publication authorization: '+json.dumps(publication_authorization(self.storage),ensure_ascii=False)
        env=os.environ.copy();env['SPINE_AGENT_TOKEN']=self.token
        env.pop('YC_API_KEY',None);env.pop('API_MATRIX_TOKEN',None)
        try:
            with self.lock:
                if self.read(id)['state']!='queued':return
                self.update(id,state='running',stage='恢复原请求' if self.read(id).get('reference_recovery',{}).get('state')=='replaying' else 'Codex 自动编排')
            self.replay_reference_request(id)
            from .generation_flow import prepare
            for source in self.read(id)['sources']:
                if source['kind']=='character':
                    if self.read(id)['state']!='running':return
                    self.update(id,stage='平台自动准备素材')
                    from .stages import run_stage
                    run_stage(self,id,'material-preparation',str(self.loaders['character'](source['id']).meta['revision']),lambda:prepare(self,id,source['id']))
            prompt+='\nPlatform checkpoints (reuse completed outputs; only revisit failed/stale stages; focus on visual decisions): '+json.dumps(self.read(id).get('stage_checkpoints',{}),ensure_ascii=False)
            prompt+='\nTask ID: '+id+'\n'+json.dumps(self.summary(id),ensure_ascii=False)
            from .worker_config import model_configuration
            model_config,provider_env=model_configuration(env)
            env.update(provider_env)
            cmd=self.command(id,result_path,model_config)
            with self.lock:
                if self.read(id)['state']!='running':return
                self.update(id,stage='Codex 自动编排')
                trace=open(directory/'events.jsonl','w');errors=open(directory/'stderr.log','w')
                process=subprocess.Popen(cmd,cwd=directory,stdin=subprocess.PIPE,stdout=trace,stderr=errors,text=True,env=env)
                self.processes[id]=process;self.active_tasks.add(id)
            try:
                from .stages import run_stage
                run_stage(self,id,'model-session',str(self.read(id).get('execution_attempts',[])),lambda:process.communicate(prompt,timeout=self.read(id)['timeout_seconds']) and {'returncode':process.returncode})
            except subprocess.TimeoutExpired:
                process.kill();process.wait();raise UserError('任务达到时间上限；已提交生图编号保留，不会自动重提')
            finally:trace.close();errors.close()
            if self.read(id)['state'] in ('cancelled','waiting_reference'):return
            if process.returncode or not result_path.exists():
                failure=self.worker_failure(directory);self.update(id,worker_failure=failure)
                raise UserError(failure['message'])
            self.update(id,stage='校验并打包成果');self.finish(id,json.loads(result_path.read_text()))
        except Exception as e:
            with self.lock:
                if self.read(id)['state'] not in ('cancelled','waiting_reference'):self.update(id,state='failed',stage='处理未完成',error=str(e)[:1000] if isinstance(e,UserError) else '后台处理失败，任务和生成编号已保留')
        finally:
            with self.lock:self.processes.pop(id,None);self.active_tasks.discard(id)

    def run(self):
        self.tasks.mkdir(parents=True,exist_ok=True)
        # A interrupted model run is never silently rerun and billed again.
        for p in self.tasks.glob('*/task.json'):
            if json.loads(p.read_text())['state']=='running':self.update(p.parent.name,state='interrupted',stage='服务重启，保留所有生成与成果记录')
        from concurrent.futures import ThreadPoolExecutor
        # Default two sessions after persistent timing is available; hard cap four.
        try:limit=max(1,min(4,int(os.getenv('SPINE_AGENT_CONCURRENCY','2'))))
        except ValueError:limit=2
        with ThreadPoolExecutor(max_workers=limit,thread_name_prefix='spine-task') as pool:
            pending={}
            while not self.stop.is_set():
                for tid,future in list(pending.items()):
                    if future.done():
                        try:future.result()
                        except Exception:pass  # execute persists the concrete task failure
                        pending.pop(tid,None)
                self.recover_references()
                queued=sorted((p for p in self.tasks.glob('*/task.json') if json.loads(p.read_text())['state']=='queued' and p.parent.name not in pending),key=lambda p:p.stat().st_mtime)
                for path in queued[:max(0,limit-len(pending))]:pending[path.parent.name]=pool.submit(self.execute,path.parent.name)
                self.wake.wait(1);self.wake.clear()


    def download(self,id):
        t=self.read(id)
        if t['state']!='completed':raise UserError('任务尚未完成')
        content=(self.path(id).parent/'result.zip').read_bytes()
        if sha(content)!=t['result_sha256']:raise UserError('成果摘要校验失败')
        return content
