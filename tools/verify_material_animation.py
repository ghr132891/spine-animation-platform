"""Exercise web handoff and automatic binding on an accepted real fixture.

Temporary storage only; no model/image calls and no production delivery edits.
Visual flags are inherited test expectations from a completed, matching task,
not an automatic visual acceptance policy.
"""
import argparse,copy,json,re,shutil,sys,tempfile,uuid,zipfile,io
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import Project,png_bytes,sha
from app.rig import RigProject
from app.web_workflows import WebWorkflows
from app.asset_integrity import ASSEMBLY_CHECKS
from app.orientation_review import ASSEMBLY_CHECKS as ORIENTATION_CHECKS
from app.generation_flow import prepare

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--character',required=True);parser.add_argument('--reference-task',required=True);parser.add_argument('--before-character');parser.add_argument('--output',required=True,type=Path);a=parser.parse_args()
    for id in (a.character,a.reference_task,*([a.before_character] if a.before_character else [])):
        if not re.fullmatch('[a-f0-9]{32}',id):parser.error('Invalid identifier')
    production=ROOT/'storage';source=production/'characters'/a.character;source_json=(source/'character.json').read_bytes()
    reference=json.loads((production/'agent-tasks'/a.reference_task/'task.json').read_text());assert reference['state']=='completed'
    reference_source=next(s for s in reference['sources'] if s['kind']=='character' and s.get('original_id')==a.character)
    expected=json.loads((production/'characters'/reference_source['id']/'character.json').read_text());original=json.loads(source_json)
    assert expected['part_layers']==original['part_layers']
    assert expected['landmarks']==original['landmarks'] and expected['settings']==original['settings']
    assert all(r['review']['verdict']=='ready' for v in reference['closeup_checks'].values() for clip in v['regions'].values() for r in clip.values())
    a.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        storage=Path(tmp);target=storage/'characters'/a.character;target.parent.mkdir()
        shutil.copytree(source,target,ignore=shutil.ignore_patterns('prebind-cache','edge-review-cache'))
        loaders={'character':lambda id:CharacterProject(storage/'characters'/id),'rig':lambda id:RigProject(storage/'rigs'/id),'project':lambda id:Project(storage/'projects'/id)}
        agent=AgentTasks(storage,ROOT,loaders,None);web=WebWorkflows(agent);owner,_=web.new_session(True)
        parent=agent.create({'nonce':uuid.uuid4().hex,'requirements':'已验收真实素材的隔离回归样本','sources':[{'kind':'character','id':a.character}],'output_scope':'materials','image_preparation':'preserve','max_generations':0,'_web_session_id':owner['id']})
        c=loaders['character'](parent['sources'][0]['id']);report,images=c.prebind_evidence()
        assert report['digest']==original['prebind_review']['digest'] and c.meta['prebind_review']['verdict']=='ready'
        agent.update(parent['id'],prebind_seen={c.id:{'prebind-'+name:report['digest'] for name in images}})
        agent.finish(parent['id'],{'outcome':'completed','summary':'Unchanged accepted material fixture','artifacts':[]})
        request={'nonce':uuid.uuid4().hex};task=web.animate_materials(owner,parent['id'],request)
        assert web.animate_materials(owner,parent['id'],request)['id']==task['id']
        raw=agent.read(task['id']);cid=raw['sources'][0]['id'];record=prepare(agent,task['id'],cid)
        def api(route,data=None,binary=False):
            c=loaders['character'](cid);action=route.rsplit('/',1)[-1]
            if binary and action=='image':return png_bytes(c.image())
            if binary and action=='assembly':return png_bytes(c.assembly())
            raise AssertionError(route)
        agent.api=api
        for view in ('image','assembly'):agent.tool(task['id'],'spine_image',{'kind':'character','id':cid,'view':view})
        assessment=copy.deepcopy(reference['image_assessments'][reference_source['id']]);assessment.update(id=cid,material_checks=dict.fromkeys(ASSEMBLY_CHECKS,True),orientation_checks=dict.fromkeys(ORIENTATION_CHECKS,True))
        built=agent.tool(task['id'],'spine_assess_image',assessment)['automatic_animation']
        again=agent.tool(task['id'],'spine_assess_image',assessment)['automatic_animation'];assert built==again
        rig=loaders['rig'](built['rig_id']);blob=rig.export();runtime=agent.runtime_check(blob)
        assert runtime['passed'];assert set(rig.state['skeleton']['animations'])=={'idle','walk'}
        assert loaders['character'](cid).meta['part_layers']==original['part_layers']
        assert loaders['character'](cid).meta['assembly_overlays']==original['assembly_overlays']
        edges,edge_image=loaders['character'](cid).edge_evidence();edge_image.save(a.output/'edges-after.png')
        before_detected=None;after_detected=None
        if a.before_character:
            before=CharacterProject(production/'characters'/a.before_character);prior,prior_image=before.edge_evidence();prior_image.save(a.output/'edges-before.png')
            # Report candidates, without treating every bright edge as a defect.
            before_detected=[p for r in prior['parts'] if r['part']=='head' for p in r['points'] if p['kind']=='opaque_light_spot']
            after_detected=[p for r in edges['parts'] if r['part']=='head' for p in r['points'] if p['kind']=='opaque_light_spot']
        for name,image in images.items():image.save(a.output/(name+'.png'))
        (a.output/'regression-draft.zip').write_bytes(blob)
        result={'mode':'isolated regression using previously accepted artwork; no new art approval','production_source_unchanged':(source/'character.json').read_bytes()==source_json,
            'web_handoff_deduplicated':True,'automatic_bind_after_assessment':True,'repeat_assessment_same_rig':True,
            'part_pixels_and_placement_unchanged':True,'collar_overlay_preserved':True,'runtime_passed':runtime['passed'],
            'animations':list(rig.state['skeleton']['animations']),'draft_only':True,'new_image_calls':0,
            'source_character':a.character,'reference_task':a.reference_task,
            'contracts':{k:raw.get(k) for k in ('detail_contract','orientation_contract','integrity_contract','connection_contract')},
            'edge_panel_prepared':any(p['name']=='edges' for p in record['panels']),'opaque_head_candidates_before':before_detected,'opaque_head_candidates_after':after_detected}
        (a.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
