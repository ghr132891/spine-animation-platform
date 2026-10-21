"""Platform-owned material preparation and immutable progress snapshots.

No model calls, cross-task searches, invented pixels or visual attestations.
"""
import copy
import time
from .core import UserError,atomic_json,png_bytes,sha

MATERIAL_ACTIONS={'repair-connections','review-stress','garment-template','apply-source-layout','apply-part-layout','install-canvas-layers',
                  'partition-canvas','prepare-assembly','finish-parts','guide','review','review-prebind','apply-ai'}


def build_approved_animation(agent,task_id,entity):
    """Compatibility entry; eligibility is derived from current accepted content."""
    from .animation_transition import advance
    advance(agent,task_id,entity)
    return agent.read(task_id).get('automatic_animation',{}).get(entity)


def prepare(agent,task_id,entity):
    task=agent.read(task_id);agent.own(task,'character',entity)
    character=agent.loaders['character'](entity)
    with character.lock:
        status=character.asset_status()
        record={'character_id':entity,'revision':character.meta['revision'],'executor':'platform','updated_at':time.time()}
        if character.meta.get('garment_template'):
            record['garment_template']={k:character.meta['garment_template'][k] for k in ('type','dimensions','openings','status')}
            record['garment_preparation']=character.meta.get('garment_preparation')
        try:
            if not character.meta.get('image_id'):
                record.update(status='needs_source_image',panels=[],message='等待角色原图，生成后继续准备完整素材。')
            elif not status['ready']:
                record.update(status='needs_complete_parts',asset_status=status,panels=[],
                              message='尚缺完整部件，保留现有素材；先补齐后再自动装配。')
            else:
                if character.meta.get('canvas_contract')!='canvas-layers-v1':
                    character.prepare_assembly({'revision':character.meta['revision']})
                report,images=character.prebind_evidence();digest=report['digest']
                edges,edge_image=character.edge_evidence()
                images={**images,'edges':edge_image}
                snapshot_digest=sha((digest+edges['digest']).encode())
                folder=agent.path(task_id).parent/'materials'/entity/snapshot_digest
                folder.mkdir(parents=True,exist_ok=True)
                panels=[]
                for name,image in images.items():
                    blob=png_bytes(image);file=folder/(name+'.png')
                    if not file.exists() or sha(file.read_bytes())!=sha(blob):
                        temporary=file.with_suffix('.png.tmp');temporary.write_bytes(blob);temporary.replace(file)
                    panels.append({'name':name,'sha256':sha(blob),'url':f'/api/workflows/tasks/{task_id}/materials/{entity}/{snapshot_digest}/{name}'})
                blocked=any(report.get(key) for key in ('missing_overlap','missing_parts','incomplete_parts'))
                review=character.meta.get('prebind_review',{})
                current_review=review.get('digest')==digest
                accepted=not blocked and current_review and review.get('verdict')=='ready'
                needs_repair=blocked or current_review and review.get('verdict')=='needs_repair'
                record.update(revision=character.meta['revision'],digest=digest,snapshot_digest=snapshot_digest,report=report,panels=panels,edge_review=edges,
                              status='needs_repair' if needs_repair else 'reviewed' if accepted else 'awaiting_visual_review',
                              message='平台已复用完整素材、完成同坐标装配并生成部件／试拼／摆开图。'+(review.get('reason','') if current_review else '存在连接或完整性问题，继续修复。' if blocked else '请查看实际图面，验收通过后再制作动画。'))
                atomic_json(folder/'manifest.json',record)
        except UserError as error:
            record.update(status='needs_repair',panels=[],error=str(error),message='自动装配需要修复：'+str(error))
    with agent.lock:
        current=agent.read(task_id)
        records=copy.deepcopy(current.get('material_preparation',{}))
        old=records.get(entity,{})
        history=list(old.get('history',[]))
        if old.get('digest') and old.get('snapshot_digest',old['digest'])!=record.get('snapshot_digest',record.get('digest')):
            history.append({k:v for k,v in old.items() if k!='history'})
        record['history']=history[-12:];records[entity]=record
        agent.update(task_id,material_preparation=records)
    return record


def panel(agent,task,entity,digest,name):
    import re
    if not re.fullmatch('[a-f0-9]{64}',digest) or name not in ('parts','assembly','spread','edges'):raise UserError('素材阶段图片无效')
    agent.own(task,'character',entity)
    record=task.get('material_preparation',{}).get(entity,{})
    match=next((r for r in [record,*record.get('history',[])] if digest in (r.get('digest'),r.get('snapshot_digest'))),None)
    if not match:raise UserError('该任务没有对应的素材阶段记录')
    expected=next((p for p in match['panels'] if p['name']==name),None)
    path=agent.path(task['id']).parent/'materials'/entity/match.get('snapshot_digest',digest)/(name+'.png')
    if not path.exists() or not expected:raise UserError('素材阶段图片尚未准备好')
    blob=path.read_bytes()
    if sha(blob)!=expected['sha256']:raise UserError('素材阶段图片已损坏，请重新准备')
    return blob


def finish_materials(agent,task,result):
    """Deliver explicitly requested materials, without inventing an animation."""
    import io,json,zipfile
    from .prebind import require
    output=io.BytesIO();delivered=[]
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for source in task['sources']:
            if source['kind']!='character':raise UserError('素材交付需要角色副本')
            cid=source['id'];agent.own(task,'character',cid);character=agent.loaders['character'](cid)
            with character.lock:
                report,images=character.prebind_evidence();require(report,character.meta.get('prebind_review'))
                if any(task.get('prebind_seen',{}).get(cid,{}).get('prebind-'+name)!=report['digest'] for name in images):raise UserError('先查看本次三张素材验收图再交付')
                for name,image in images.items():archive.writestr(cid+'/review/'+name+'.png',png_bytes(image))
                for name,record in character.meta['part_layers'].items():
                    archive.writestr(cid+'/parts/'+name+'.png',(character.directory/'part-images'/(record['image_id']+'.png')).read_bytes())
                layout={k:character.meta.get(k) for k in ('landmarks','settings','part_layers','part_review','assembly_overlays','prebind_review','canvas_contract','canvas_owners')}
                layout['canvas_size']=list(character.image().size)
                archive.writestr(cid+'/layout.json',json.dumps(layout,ensure_ascii=False,indent=2))
                delivered.append({'character_id':cid,'revision':character.meta['revision'],'digest':report['digest']})
        if not delivered:raise UserError('没有已验收的素材可交付')
        archive.writestr('manifest.json',json.dumps({'scope':'materials','characters':delivered,'summary':result.get('summary','')},ensure_ascii=False))
    content=output.getvalue();path=agent.path(task['id']).parent/'result.zip';temporary=path.with_suffix('.tmp');temporary.write_bytes(content);temporary.replace(path)
    with agent.lock:
        if agent.read(task['id'])['state']=='cancelled':return
        agent.update(task['id'],state='completed',stage='素材修复与验收完成',error=None,summary=result.get('summary',''),artifacts=[],result_sha256=sha(content),
                     material_delivery={'characters':delivered,'download_url':'/api/workflows/tasks/'+task['id']+'/download'})
