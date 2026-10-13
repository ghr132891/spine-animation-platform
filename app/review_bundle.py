"""Platform-owned immutable review preparation, reusable across clients/restarts.

Preparation is not visual acceptance. Only reading a panel grants evidence to a
worker; semantic attestations still go through the existing review validators.
"""
import copy
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .core import UserError, atomic_json, read_image
from .runtime_cache import RuntimeReports
from .seam_review import required_detail_anchors
from .asset_integrity import garment_frame_count
from .material_finish import inspect_alpha

bundles=RuntimeReports(max_bytes=4*1024*1024,max_concurrent=1)


def renderer_signature(root):
    names=['app/review_bundle.py','app/agent_tasks.py','app/material_finish.py','app/seam_review.py',
           'app/contact_diagnostics.py','tools/render_rig_evidence.py','tools/validate_agent_runtime.cjs','tools/verify_rig_runtime.cjs']
    files=[root/n for n in names]+sorted((root/'web/runtime').glob('*/spine-player.js'))
    return hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest()


def prepare(agent,kind,entity,data):
    from .agent_tasks import resource_digest
    if kind not in ('rig','project'):raise UserError('复检包须使用rig或project')
    source=agent.loaders[kind](entity)
    with source.lock:
        content=source.export();revision=source.meta['revision'] if kind=='rig' else source.revision
        skeleton=copy.deepcopy(source.state['skeleton'] if kind=='rig' else source.skeleton_data)
        textures=[(p['id'],read_image(source.blob(p))) for p in source.state['parts']] if kind=='rig' else []
    clips=data.get('animations',list(skeleton.get('animations',{})) or ['__setup__'])
    if not isinstance(clips,list) or not 1<=len(clips)<=8 or len(set(map(str,clips)))!=len(clips) or any(not isinstance(c,str) or c not in skeleton.get('animations',{}) and c!='__setup__' for c in clips):
        raise UserError('请提供1–8个实际且不重复的动作名')
    anchors=required_detail_anchors(skeleton);digest=resource_digest(content)
    key=hashlib.sha256(json.dumps([digest,renderer_signature(agent.root),clips,anchors]).encode()).hexdigest()
    folder=agent.root/'storage/review-bundles'/key;folder.mkdir(parents=True,exist_ok=True)
    def compute():
        record=folder/'manifest.json'
        if record.exists():
            saved=json.loads(record.read_text())
            if all((folder/p['file']).is_file() for p in saved['panels']):return saved
        started=time.perf_counter()
        jobs=[(clip,anchor) for clip in clips for anchor in [None,*anchors]]
        def render(job):
            clip,anchor=job;count=garment_frame_count(clip) if anchor is None or anchor.endswith('-leg') else 8
            panel_id='p'+str(jobs.index(job));png=folder/(panel_id+'.png');meta=folder/(panel_id+'.json')
            if png.exists() and meta.exists():return json.loads(meta.read_text())
            report,image=agent.runtime_check(content,clip,count,closeup=anchor)
            temp=png.with_suffix('.png.tmp');temp.write_bytes(image);temp.replace(png)
            item={'key':panel_id,'animation':clip,'anchor':anchor,'frames':count,'file':png.name,
                  'report':report.get('closeup',report),'image_url':'/api/automation/agent/review-bundles/'+key+'/'+png.name}
            atomic_json(meta,item);return item
        with ThreadPoolExecutor(max_workers=2) as pool:panels=list(pool.map(render,jobs))
        contact={clip:agent.contact_check(content,clip) for clip in clips if garment_frame_count(clip)==32}
        result={'bundle_id':key,'resource_sha256':digest,'status':'prepared','visual_verdict':'not_reviewed','panels':panels,
                'contact':contact,'alpha_diagnostics':{part:inspect_alpha(im) for part,im in textures},
                'elapsed_seconds':round(time.perf_counter()-started,3),'limits':'准备证据不等于美术验收；小连通块仅为候选，不自动删除设计细节。'}
        atomic_json(record,result);return result
    result=bundles.get((str(agent.root),key),compute)
    # Reading a persisted package always rechecks current resource identity.
    return {**result,'kind':kind,'id':entity,'revision':revision}


def read_file(root,bundle_id,filename):
    if not re.fullmatch('[a-f0-9]{64}',bundle_id) or not re.fullmatch('p[0-9]{1,2}\\.png',filename):raise UserError('复检图片路径无效')
    folder=root/'storage/review-bundles'/bundle_id
    if not (folder/'manifest.json').exists():raise UserError('复检包尚未完成')
    if not (folder/filename).is_file():raise UserError('复检图片不存在')
    return (folder/filename).read_bytes()
