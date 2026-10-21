"""Visual turnaround evidence bound to source pixels and the reviewed assembly."""
import json
from .core import UserError, sha

CHECKS=('identity','scale','arm_position','leg_alignment','hair_silhouette','costume_layers')

def checked_review(data):
    phase=data.get('phase','reference');verdict=data.get('verdict')
    if phase not in ('reference','assembly') or verdict not in ('ready','needs_adjustment','unsupported'):
        raise UserError('三视图检查须指定 reference/assembly 与 ready/needs_adjustment/unsupported')
    checks=data.get('checks')
    if not isinstance(checks,dict) or set(checks)!=set(CHECKS) or any(type(v)!=bool for v in checks.values()):
        raise UserError('须逐项检查 identity/scale/arm_position/leg_alignment/hair_silhouette/costume_layers')
    reason=data.get('reason')
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=2000:raise UserError('三视图比较须记录具体观察')
    if verdict=='ready' and not all(checks.values()):raise UserError('仍有比例、定位、头发或衣裙层级问题，不能通过验收')
    return {'phase':phase,'verdict':verdict,'checks':checks,'reason':reason.strip()}

def candidate(character,job_id):
    path=character.directory/'jobs'/(job_id+'.json')
    if not path.is_file():raise UserError('三视图任务不存在')
    job=json.loads(path.read_text());png=path.with_suffix('.png')
    if job.get('sheet')!='turnaround' or not job.get('candidate_ready') or not png.is_file():raise UserError('须使用已完成的真实三视图候选')
    if job.get('source_image_id')!=character.meta.get('image_id'):raise UserError('三视图不属于当前参考图，请重新准备或重新关联')
    return {'job_id':job_id,'image_id':character.meta['image_id'],'sha256':sha(png.read_bytes())}

def require_review(task,character,phase):
    record=task.get('turnaround_checks',{}).get(character.id,{}).get(phase,{})
    if record.get('verdict')!='ready':raise UserError('先查看并通过三视图'+('与当前组装的比对' if phase=='assembly' else '参考一致性检查'))
    current=candidate(character,record['job_id'])
    assembly_current=(record.get('source_digest')==character.prebind_evidence()[0]['digest'] if record.get('source_digest') else record.get('revision')==character.meta['revision']) if phase=='assembly' else True
    if any(record.get(k)!=v for k,v in current.items()) or not assembly_current:
        raise UserError('三视图或组装已变化，旧比对失效')
    return record
