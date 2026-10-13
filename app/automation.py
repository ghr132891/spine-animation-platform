"""Durable agent-driven texture reskin. Existing skeleton bytes stay immutable."""
from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

import numpy as np

from .ai import reference_for, fit_result
from .core import Project, UserError, atomic_json, decode_files, png_bytes, sha
from .provider import Matrix,DEFAULT_MODEL


def part_description(name):
    rules=[('head|face|头|脸','head or face surface'),('foot|boot|shoe|脚|鞋','boot or foot surface'),
           ('hand|palm|手','hand surface'),('back|bag|背','backpack or back surface'),
           ('torso|body|chest|身体|躯干','torso surface'),('arm|elbow|腕|臂','arm surface'),
           ('leg|knee|腿|膝','leg surface'),('hip|pelvis|髋|盆','hip surface')]
    return next((meaning for pattern,meaning in rules if re.search(pattern,name,re.I)),'game character part surface')


class AutoReskin:
    def __init__(self,storage,provider,jobs,load_project):
        self.storage=Path(storage);self.root=self.storage/'automation'
        self.provider=provider;self.jobs=jobs;self.load_project=load_project
        self.guard=threading.RLock();self.step_guard=threading.Lock()
        self.stop=threading.Event();self.wake=threading.Event()

    def path(self,id):
        if not isinstance(id,str) or not re.fullmatch(r'[a-f0-9-]{16,64}',id):raise UserError('nonce / 任务编号须为 16–64 位十六进制字符或连字符')
        return self.root/id/'task.json'

    def read(self,id):
        path=self.path(id)
        if not path.exists():raise UserError('自动换皮任务不存在')
        return json.loads(path.read_text())

    def update(self,id,**changes):
        with self.guard:
            task=self.read(id);task.update(changes,updated_at=time.time());atomic_json(self.path(id),task)
            return task

    @contextlib.contextmanager
    def source(self,data):
        if bool(data.get('project_id'))==bool(data.get('files')):raise UserError('请提供 project_id 或 files，二选一')
        if data.get('project_id'):
            yield self.load_project(data['project_id'])
        else:
            with tempfile.TemporaryDirectory(prefix='spine-agent-plan-') as tmp:
                yield Project(Path(tmp)/uuid.uuid4().hex,decode_files(data['files']),str(data.get('name','AI 自动换皮'))[:80])

    def plan_for(self,p,data):
        style=data.get('style','')
        if not isinstance(style,str) or not 2<=len(style.strip())<=1000:raise UserError('style 须为 2–1000 字的目标风格描述')
        style=style.strip();mode=data.get('mode','material')
        if mode not in ('material','reference'):raise UserError('mode 仅支持 material 或 reference')
        ids=data.get('region_ids',[r['id'] for r in p.regions])
        if not isinstance(ids,list) or not ids or not all(isinstance(i,str) and i in p.by_region for i in ids) or len(set(ids))!=len(ids):raise UserError('region_ids 须为不重复的有效部件编号')
        budget=data.get('max_generations',32)
        if type(budget)!=int or not 1<=budget<=128:raise UserError('max_generations 须为 1–128 的整数')
        refs=data.get('reference_urls',{})
        if not isinstance(refs,dict) or any(i not in ids for i in refs):raise UserError('reference_urls 必须以所选部件编号为键')
        parts=[]
        # Prompt construction is deterministic and inspectable, not a hidden LLM call.
        for id in ids:
            r=p.by_region[id];part=p.part(id,True);arr=np.array(part);visible=arr[:,:,3]>32
            palette=np.median(arr[visible,:3],axis=0).round().astype(int).tolist() if visible.any() else [0,0,0]
            description=part_description(r['name'])
            prompt=(f'{style}. Consistent palette, materials and detail density across the whole character. '
                    f'Part context: {description}; source asset name: {r["name"][:120]}; '
                    f'original canvas {part.width} x {part.height}; original median RGB {palette}. '
                    'Keep existing readable facial features, panel seams and dark line art. '
                    'Adapt only the surface finish to the target style; no extra limbs, accessories, text or logos.')
            if mode=='material':
                # A swatch is generated without a body-part reference. Anatomical
                # metadata belongs in the plan, not in this fabric-only prompt.
                prompt=style.rstrip('. ')+'. Flat even lighting, subtle surface detail, uniform scale.'
            reference=refs.get(id) if mode=='reference' else None
            if mode=='reference' and not isinstance(reference,str):raise UserError('reference 模式须为每个部件提供其 512×512 参考画布的 HTTPS 地址')
            provider_input=Matrix.generation_input(prompt,reference)
            _,layout=reference_for(part)
            if mode=='material':layout['mode']='material'
            parts.append({'id':id,'name':r['name'],'description':description,'size':list(part.size),'median_rgb':palette,
                          'prompt':prompt,'provider_input':provider_input,'reference_url':reference,'layout':layout})
        return {'style':style,'mode':mode,'prompt_strategy':'material-swatch-v2' if mode=='material' else 'source-metadata-template-v1',
                'source':{'project_id':p.id if data.get('project_id') else None,'revision':p.revision,'name':p.label,
                          'skeleton':p.skeleton,'atlas':p.atlas,'version':p.version,'animations':p.animations,
                          'bones':len(p.skeleton_data['bones']) if p.skeleton_data else None},
                'parts':parts,'generation_count':len(parts),'max_generations':budget,'within_budget':len(parts)<=budget,
                'constraints':{'skeleton':'byte-identical','atlas':'byte-identical','dimensions':'unchanged','alpha':'unchanged'},
                'limitations':['material 模式生成表面材质，再按原轮廓和明暗贴回；不重画整个人物。',
                               'reference 模式需要每个部件参考画布的公共 HTTPS 图片地址，不接受本机地址或 Base64。',
                               '自动提示词使用部件名称、尺寸、颜色及目标风格，不进行视觉模型语义识别。']}

    def plan(self,data):
        with self.source(data) as p,p.lock:return self.plan_for(p,data)

    def create(self,data):
        id=data.get('nonce');path=self.path(id)
        fingerprint=sha(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
        with self.guard:
            if path.exists():
                old=self.read(id)
                if old['request_sha256']!=fingerprint:raise UserError('此 nonce 已用于不同请求，请使用新的任务编号')
                return self.summary(id)
            with self.source(data) as p,p.lock:
                if data.get('revision') is not None:p.check_revision(data['revision'])
                plan=self.plan_for(p,data)
                if not plan['within_budget']:raise UserError(f'需生成 {plan["generation_count"]} 张，超过 max_generations={plan["max_generations"]}；请缩小 region_ids 或显式提高上限')
                if not self.provider.status().get('configured'):raise UserError('请先配置亿创凭据，未提交生图任务')
                if not p.validate()['passed']:raise UserError('源资源校验失败，未提交生成')
                # Freeze current textures, even if the source is later edited.
                files={n:p.pages_data.get(n,b) for n,b in p.files.items()}
                output_id=uuid.uuid4().hex;output=Project(self.storage/'projects'/output_id,files,(p.label+' · '+plan['style'])[:80])
                task={'id':id,'request_sha256':fingerprint,'state':'queued','created_at':time.time(),'updated_at':time.time(),
                      'output_project_id':output_id,'plan':plan,'jobs':[], 'source_sha256':{n:sha(b) for n,b in files.items()}}
                for part in plan['parts']:
                    job_id=uuid.uuid4().hex;job={'id':job_id,'region':part['id'],'revision':0,'status':'prepared','model':DEFAULT_MODEL,'created_at':time.time(),
                         'layout':part['layout'],'prompt':part['prompt'],'mode':plan['mode'],'automation_id':id}
                    atomic_json(self.jobs.path(output_id,job_id),job)
                    task['jobs'].append({'id':job_id,'region':part['id']})
                atomic_json(path,task)
            self.wake.set();return self.summary(id)

    def summary(self,id):
        with self.guard:
            task=self.read(id);jobs=[self.jobs.read(task['output_project_id'],j['id']) for j in task['jobs']]
        ready=sum(bool(j.get('candidate_ready')) for j in jobs)
        result={k:v for k,v in task.items() if k not in ('request_sha256','expected_pages')}
        result['jobs']=[{k:j[k] for k in ('id','region','model','status','gen_id','candidate_ready','poll_error','error','submission_error') if k in j} for j in jobs]
        result['progress']={'total':len(jobs),'submitted':sum(bool(j.get('gen_id')) for j in jobs),'ready':ready}
        result['status_url']=f'/api/automation/reskin/tasks/{id}'
        result['preview_url']=f'/?project={task["output_project_id"]}'
        result['download_url']=f'/api/automation/reskin/tasks/{id}/download' if task['state']=='completed' else None
        return result

    def cancel(self,id):
        with self.guard:
            task=self.read(id)
            if task['state']=='assembling':raise UserError('结果正在保存，请稍后查询；此时无法取消')
            if task['state'] not in ('completed','cancelled'):self.update(id,state='cancelled')
        return self.summary(id)

    def resume(self,id,data):
        with self.guard:
            task=self.read(id)
            if task['state'] not in ('needs_attention','generating'):raise UserError('此任务无需恢复或已结束')
            bindings=data.get('generation_ids',{})
            if not isinstance(bindings,dict):raise UserError('generation_ids 须为本地 job_id 到已存在亿创 gen_id 的映射')
            if bindings and task['state']!='needs_attention':raise UserError('提交仍在进行，暂不能绑定生图编号；请等待状态核实')
            updates=[]
            for job_id,gen_id in bindings.items():
                if job_id not in [j['id'] for j in task['jobs']] or not isinstance(gen_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',gen_id):raise UserError('generation_ids 编号无效')
                job=self.jobs.read(task['output_project_id'],job_id)
                if job['status']!='submission_unknown' or job.get('gen_id'):raise UserError('只能绑定提交结果待核实的任务')
                job.update(gen_id=gen_id,status='pending');updates.append((self.jobs.path(task['output_project_id'],job_id),job))
            for path,job in updates:atomic_json(path,job)
            self.update(id,state='generating',error=None,refresh_requested=True);self.wake.set()
        return self.summary(id)

    def download(self,id):
        task=self.read(id)
        if task['state']!='completed':raise UserError('任务尚未完成，暂不能下载')
        result=self.path(id).parent/'result.zip';content=result.read_bytes()
        if sha(content)!=task['result_sha256']:raise UserError('结果文件校验失败')
        return content

    def step(self,id):
        # One submission owner per service. Status reads never wait on networking.
        if not self.step_guard.acquire(blocking=False):return
        try:self._step(id)
        except UserError as e:self.update(id,state='needs_attention',error=str(e))
        except Exception:self.update(id,state='needs_attention',error='任务处理暂未完成，已有生图编号保留；检查资源后可恢复，不会自动重复提交')
        finally:self.step_guard.release()

    def _step(self,id):
        task=self.read(id)
        if task['state'] not in ('queued','generating','assembling'):return
        output=self.load_project(task['output_project_id'])
        if task.get('refresh_requested'):
            for spec in task['jobs']:
                self.jobs.refresh(self.jobs.path(output.id,spec['id']),force=True)
            task=self.update(id,refresh_requested=False)
        for spec,part in zip(task['jobs'],task['plan']['parts']):
            if self.read(id)['state']=='cancelled':return
            if any(self.jobs.read(output.id,j['id'])['status']=='failed' for j in task['jobs']):
                self.update(id,state='failed',error='至少一个部件生成失败，已停止后续提交；未导出半成品');return
            path=self.jobs.path(output.id,spec['id']);job=self.jobs.read(output.id,spec['id'])
            if job['status']=='prepared':
                with self.guard:
                    if self.read(id)['state']=='cancelled':return
                    if output.revision!=0:raise UserError('输出项目已被编辑，已停止后续生成；请使用新任务')
                    job.update(status='submission_unknown',model=DEFAULT_MODEL);atomic_json(path,job);self.update(id,state='generating')
                try:gen_id=self.provider.generate(job['prompt'],part['reference_url'])
                except Exception:
                    job['submission_error']='提交结果待核实；请在亿创核对后绑定已有 gen_id，不会自动重新生成'
                    atomic_json(path,job)
                    if self.read(id)['state']!='cancelled':self.update(id,state='needs_attention',error=job['submission_error'])
                    return
                job.update(gen_id=gen_id,status='pending');atomic_json(path,job);self.jobs.wake.set()
            if job['status']=='submission_unknown':
                self.update(id,state='needs_attention',error='存在提交结果待核实的生图任务，请绑定已有 gen_id 后恢复');return
        if self.read(id)['state']=='cancelled':return
        jobs=[self.jobs.read(output.id,j['id']) for j in task['jobs']]
        if any(j['status']=='failed' for j in jobs):self.update(id,state='failed',error='至少一个部件生成失败，未导出半成品；其他已完成候选保留');return
        if any(j.get('poll_failures',0)>=8 or j.get('poll_count',0)>=225 for j in jobs if not j.get('candidate_ready')):
            self.update(id,state='needs_attention',error='已达到查询上限；恢复只查询已有任务，不重新生成');return
        if not all(j.get('candidate_ready') for j in jobs):return
        with self.guard:
            if self.read(id)['state']=='cancelled':return
            task=self.update(id,state='assembling')
        with output.lock:
            candidates=[]
            for job in jobs:
                original=output.part(job['region'],True);fitted=fit_result(self.jobs.path(output.id,job['id']).with_suffix('.png').read_bytes(),job['layout'],original)
                fitted.putalpha(original.getchannel('A'));candidates.append((job['region'],fitted))
            if output.revision==0:
                output.pages_data={p['file']:output.files[p['file']] for p in output.pages}
                for region,image in candidates:output._put_part(region,image)
                hashes={n:sha(b) for n,b in output.pages_data.items()};task=self.update(id,expected_pages=hashes)
                output.commit('AI 自动换皮：'+id)
            elif output.revision!=1 or task.get('expected_pages')!={n:sha(b) for n,b in output.pages_data.items()}:
                raise UserError('输出项目已被编辑，未覆盖手工修改；请创建新任务')
            report=output.validate()
            if not report['passed']:raise UserError('骨架、图集或透明轮廓校验失败，未生成结果包')
            content=output.export()
            bundle=io.BytesIO(content)
            with zipfile.ZipFile(bundle,'a',zipfile.ZIP_DEFLATED) as z:
                manifest_name='_reskin-automation.json'
                while manifest_name in z.namelist():manifest_name='_'+manifest_name
                z.writestr(manifest_name,json.dumps({'task_id':id,'style':task['plan']['style'],'mode':task['plan']['mode'],
                    'prompt_strategy':task['plan']['prompt_strategy'],'source_sha256':task['source_sha256'],
                    'parts':[{'region':j['region'],'job_id':j['id'],'gen_id':j['gen_id'],'prompt':j['prompt']} for j in jobs]},ensure_ascii=False,indent=2))
            content=bundle.getvalue()
        target=self.path(id).parent/'result.zip';temporary=target.with_suffix('.zip.tmp');temporary.write_bytes(content);temporary.replace(target)
        atomic_json(self.path(id).parent/'validation.json',report)
        self.update(id,state='completed',error=None,validation=report,result_sha256=sha(content),completed_at=time.time())

    def run(self):
        while not self.stop.is_set():
            for path in self.root.glob('*/task.json'):
                try:
                    task=json.loads(path.read_text())
                    if task['state'] in ('queued','generating','assembling'):self.step(task['id'])
                except (OSError,ValueError,KeyError):continue
            self.wake.wait(3);self.wake.clear()
