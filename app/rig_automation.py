"""Apply a sequence on an isolated rig and publish only after validation."""
from __future__ import annotations
import copy
import json
import re
import shutil
import threading
import uuid
from pathlib import Path
from .core import UserError, atomic_json, sha
from .rig import RigProject, number, name_value


EDIT_ACTIONS = {'bone','add-bone','delete-bone','slot','attachment','delete-attachment',
                'ik','texture','texture-repair','material-finish','recolor','add-part','skeleton','animation','copy-animation'}


def clone_rig(source, directory, name=None):
    directory=Path(directory);directory.mkdir(parents=True)
    with source.lock:
        meta=copy.deepcopy(source.meta)
        meta.update(id=directory.name, revision=0, history=[], name=name or source.meta['name']+' · 自动化')
        for part in meta['state']['parts']:
            target=directory/'blobs'/(part['sha']+'.png');target.parent.mkdir(exist_ok=True)
            target.write_bytes(source.blob(part))
        for material in meta.get('material_sources',[]):
            target=directory/'blobs'/(material['sha']+'.png');target.parent.mkdir(exist_ok=True)
            target.write_bytes(source.blob(material))
        atomic_json(directory/'rig.json',meta)
        atomic_json(directory/'source-preview.json',source.raw_data())
    rig=RigProject(directory);rig.commit(rig.state,'自动化源快照',initial=True)
    return rig


def edit_animation(rig, data):
    """Full clip replacement. The matching Spine runtime is the schema authority."""
    state=copy.deepcopy(rig.state);s=state['skeleton'];name=name_value(data['name'])
    if data.get('delete'):
        if name not in s.get('animations',{}):raise UserError('动作不存在')
        del s['animations'][name]
    else:
        clip=data['animation']
        if not isinstance(clip,dict):raise UserError('animation 必须是 Spine 动作 JSON 对象')
        s.setdefault('animations',{})[name]=copy.deepcopy(clip)
    rig.commit(state,'自动替换动作：'+name)


def mapped_animation(target, source, data):
    """Explicit local-space mapping, not arbitrary skeleton retargeting."""
    a=source.state['skeleton'];b=target.state['skeleton']
    if a.get('skeleton',{}).get('spine','')[:3]!=b.get('skeleton',{}).get('spine','')[:3]:
        raise UserError('动作映射需要相同 Spine runtime 版本')
    clip=copy.deepcopy(a.get('animations',{}).get(data['source_animation']))
    if clip is None:raise UserError('源动作不存在')
    if set(clip)-{'bones'}:raise UserError('此动作含插槽、网格变形或约束曲线，不能仅映射骨骼；请提交完整适配后的动作')
    mapping=data.get('bone_map',{})
    if not isinstance(mapping,dict):raise UserError('bone_map 须为源骨骼到目标骨骼的映射')
    names={bone['name'] for bone in b['bones']};result={}
    time_scale=number(data.get('time_scale',1),.1,10)
    translation_scale=number(data.get('translation_scale',1),.01,100)
    for bone,timelines in clip.get('bones',{}).items():
        dest=mapping.get(bone,bone)
        if dest not in names:raise UserError('动作骨骼缺少映射：'+bone)
        if dest in result:raise UserError('多个动作骨骼不能映射到同一目标骨骼')
        for kind,frames in timelines.items():
            if kind not in ('rotate','translate','translatex','translatey','scale','scalex','scaley','shear','shearx','sheary'):
                raise UserError('尚未支持映射此骨骼曲线：'+kind)
            for frame in frames:
                frame['time']=number(frame.get('time',0),0)/time_scale
                # Bezier control points contain absolute times/values in 4.2.
                if (time_scale!=1 or translation_scale!=1) and isinstance(frame.get('curve'),list):
                    raise UserError('贝塞尔曲线缩放需完整适配，不能直接缩放关键帧')
                if kind.startswith('translate'):
                    for key in ('x','y','value'):
                        if key in frame:frame[key]=number(frame[key])*translation_scale
        result[dest]=timelines
    return {'bones':result}


class RigAutomation:
    def __init__(self,storage,load_rig):
        self.storage=Path(storage);self.root=self.storage/'rig-automation';self.load_rig=load_rig
        self.lock=threading.RLock()

    def path(self,id):
        if not isinstance(id,str) or not re.fullmatch('[a-f0-9-]{16,64}',id):raise UserError('自动化任务编号无效')
        return self.root/id/'task.json'

    def create(self,data):
        id=data.get('nonce');path=self.path(id)
        digest=sha(json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
        operations=data.get('operations')
        if not isinstance(operations,list) or not 1<=len(operations)<=100:raise UserError('operations 须为 1–100 个操作')
        for op in operations:
            if not isinstance(op,dict) or op.get('action') not in EDIT_ACTIONS:raise UserError('不支持此自动骨骼操作')
            required={'bone':['bone'],'add-bone':['name','parent'],'delete-bone':['bone'],'slot':['slot','bone'],
                      'attachment':['skin','slot','attachment'],'delete-attachment':['skin','slot','attachment'],
                      'ik':['name','target','bones'],'texture':['part','image'],'texture-repair':['part','reason'],'material-finish':['part','reason','operations'],'recolor':['part'],
                      'add-part':['name','bone','image'],'skeleton':['skeleton'],'animation':['name'],
                      'copy-animation':['source_rig_id','source_animation']}[op['action']]
            missing=[key for key in required if key not in op]
            if missing:raise UserError(op['action']+' 操作缺少参数：'+', '.join(missing)+'。可修正参数并重试；源项目未修改。')
        with self.lock:
            if path.exists():
                old=json.loads(path.read_text())
                if old['request_sha256']!=digest:raise UserError('nonce 已用于不同请求')
                return old
            source=self.load_rig(data['source_rig_id'])
            work=path.parent/'working';output_id=uuid.uuid4().hex
            path.parent.mkdir(parents=True,exist_ok=True)
            if work.exists():shutil.rmtree(work)
            with source.lock:
                source.check_revision(data['revision']);rig=clone_rig(source,work)
            try:
                for op in operations:
                    payload={**op,'revision':rig.meta['revision']};action=op['action']
                    if action=='animation':edit_animation(rig,payload)
                    elif action=='copy-animation':
                        clip=mapped_animation(rig,self.load_rig(op['source_rig_id']),op)
                        edit_animation(rig,{'name':op.get('name',op['source_animation']),'animation':clip})
                    else:rig.mutate(action,payload)
                report=rig.validate()
                if not report['passed']:raise UserError('自动骨骼校验失败')
                # Runtime gate is applied by agent task completion before delivery.
                rig.meta['id']=output_id;atomic_json(work/'rig.json',rig.meta)
                final=self.storage/'rigs'/output_id;final.parent.mkdir(parents=True,exist_ok=True)
                work.rename(final)
                result={'id':id,'state':'completed','request_sha256':digest,'rig_id':output_id,
                        'validation':report,'preview_url':'/result.html?rig='+output_id,
                        'editor_url':'/rig.html?rig='+output_id+'&advanced=1',
                        'download_url':'/api/rigs/'+output_id+'/export'}
                atomic_json(path,result);return result
            except Exception:
                shutil.rmtree(work,ignore_errors=True);raise
