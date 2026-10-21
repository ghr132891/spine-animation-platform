import copy,json,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import UserError,png_bytes
from app.generation_flow import prepare,panel
from app.part_prompts import generation_plan
from app.web_workflows import WebWorkflows,WebError
from tests.test_asset_sheet_workflow import asset_fixture,assembly_guide

ROOT=Path(__file__).resolve().parents[1]

class DefaultGenerationFlowTests(unittest.TestCase):
    def test_text_only_task_keeps_generation_available_before_landmarks_exist(self):
        task=self.agent.create({'nonce':uuid.uuid4().hex,'requirements':'制作一个绿色马甲人物的待机和行走','image_preparation':'asset-sheet'})
        cid=task['sources'][0]['id'];record=prepare(self.agent,task['id'],cid)
        self.assertEqual(record['status'],'needs_source_image');self.assertEqual(self.agent.read(task['id'])['state'],'queued')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.storage=Path(self.temp.name)
        self.c=CharacterProject.create(self.storage,'complete source')
        image,layout=asset_fixture();self.c.set_image(png_bytes(image),0)
        self.c.update({'revision':self.c.meta['revision'],'landmarks':assembly_guide(),'settings':self.c.meta['settings']})
        self.c.apply_source_layout({'revision':self.c.meta['revision'],'parts':layout})
        self.agent=AgentTasks(self.storage,ROOT,{'character':lambda id:CharacterProject(self.storage/'characters'/id)},None)
        self.web=WebWorkflows(self.agent);self.owner,_=self.web.new_session(True)
        self.request={'nonce':uuid.uuid4().hex,'mode':'image','character_id':self.c.id,'image_preparation':'preserve','requirements':'仅复用完整素材，检查部件、试拼与摆开'}

    def test_browser_submission_prepares_before_any_model_and_keeps_evidence_on_failure(self):
        original=(self.c.directory/'character.json').read_bytes()
        created=self.web.submit(self.owner,self.request)
        # Deliberately no CLI: material execution must already have happened.
        with patch.object(self.agent,'command',side_effect=UserError('test: worker unavailable')),patch.object(self.agent,'image_provider_info',return_value={}):
            self.agent.execute(created['id'])
        result=self.web.read(self.owner,created['id']);record=result['material_preparation'][0]
        self.assertEqual(result['state'],'failed');self.assertEqual(record['executor'],'platform')
        self.assertEqual(record['status'],'awaiting_visual_review');self.assertEqual(len(record['panels']),4)
        self.assertEqual(self.agent.summary(created['id'])['generation_budget']['total_images_reserved'],0)
        self.assertEqual((self.c.directory/'character.json').read_bytes(),original)
        task=self.agent.read(created['id']);cid=task['sources'][0]['id']
        self.assertNotIn('prebind_review',self.agent.loaders['character'](cid).meta)
        self.assertTrue(panel(self.agent,task,cid,record['digest'],'assembly').startswith(b'\x89PNG'))
        other,_=self.web.new_session()
        with self.assertRaises(WebError):self.web.read(other,created['id'])

    def test_import_executes_real_prebind_and_progress_keeps_prior_version(self):
        self.assertEqual(self.c.meta['generation_preparation']['executor'],'platform')
        self.assertTrue(list((self.c.directory/'prebind-cache').glob('*/manifest.json')))
        task=self.web.submit(self.owner,self.request);cid=self.agent.read(task['id'])['sources'][0]['id']
        first=prepare(self.agent,task['id'],cid)
        again=prepare(self.agent,task['id'],cid);self.assertFalse(again['history'])
        c=self.agent.loaders['character'](cid)
        c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'head':{'dx':2}},'draw_order':c.review_order([p['name'] for p in c.parts()])}})
        second=prepare(self.agent,task['id'],cid)
        self.assertNotEqual(first['digest'],second['digest']);self.assertEqual(second['history'][0]['digest'],first['digest'])
        self.assertTrue(panel(self.agent,self.agent.read(task['id']),cid,first['digest'],'parts'))
        with self.assertRaises(UserError):panel(self.agent,self.agent.read(task['id']),self.c.id,first['digest'],'parts')

    def test_direct_generation_default_is_complete_and_explicit_legacy_is_retained(self):
        plan=generation_plan({'mode':'complete-parts'})
        self.assertEqual([x['sheet'] for x in plan['items']],['turnaround','character-assets'])
        self.assertEqual(plan['items'][1]['groups'],{'torso':'costume'})
        legacy=generation_plan({'mode':'complete-parts','sheets':['body','arms','legs'],'body_layout':'separate'})
        self.assertEqual([x['sheet'] for x in legacy['items']],['body','arms','legs'])

    def test_incomplete_source_is_exposed_without_inventing_fill(self):
        c=CharacterProject.create(self.storage,'portrait');image,_=asset_fixture();c.set_image(png_bytes(image),0)
        task=self.web.submit(self.owner,{**self.request,'character_id':c.id});cid=self.agent.read(task['id'])['sources'][0]['id']
        record=prepare(self.agent,task['id'],cid)
        self.assertEqual(record['status'],'needs_complete_parts');self.assertFalse(record['panels'])
        self.assertFalse(self.agent.loaders['character'](cid).meta['part_layers'])

    def test_worker_authentication_error_is_specific_and_redacted(self):
        path=self.storage/'events.jsonl'
        path.write_text(json.dumps({'type':'error','message':'401 Unauthorized invalid_api_key SECRET_VALUE'}))
        failure=self.agent.worker_failure(self.storage)
        self.assertEqual(failure['code'],'worker_authentication_failed')
        self.assertNotIn('SECRET_VALUE',json.dumps(failure))

    def test_preparation_error_is_visible_and_does_not_stop_later_repair(self):
        task=self.web.submit(self.owner,self.request);cid=self.agent.read(task['id'])['sources'][0]['id']
        with patch('app.character.CharacterProject.prebind_evidence',side_effect=UserError('前后关系冲突')):
            result=prepare(self.agent,task['id'],cid)
        self.assertEqual(result['status'],'needs_repair');self.assertIn('前后关系冲突',result['message'])
        self.assertEqual(self.agent.read(task['id'])['state'],'queued')

    def test_visual_repair_verdict_is_shown_despite_structural_pass(self):
        task=self.web.submit(self.owner,self.request);cid=self.agent.read(task['id'])['sources'][0]['id']
        c=self.agent.loaders['character'](cid);report,_=c.prebind_evidence()
        c.review_prebind({'revision':c.meta['revision'],'digest':report['digest'],'checks':dict.fromkeys(report['checks'],True),'verdict':'needs_repair','reason':'Visible fringe needs repair'})
        record=prepare(self.agent,task['id'],cid)
        self.assertEqual(record['status'],'needs_repair');self.assertIn('Visible fringe',record['message'])

    def test_explicit_material_delivery_requires_current_visual_acceptance(self):
        import io,zipfile
        task=self.agent.create({'nonce':uuid.uuid4().hex,'requirements':'仅修复与交付素材，不做动画','output_scope':'materials','sources':[{'kind':'character','id':self.c.id}],'max_generations':0})
        cid=task['sources'][0]['id'];result={'outcome':'completed','summary':'素材已修复并检查','artifacts':[]}
        with self.assertRaises(UserError):self.agent.finish(task['id'],result)
        c=self.agent.loaders['character'](cid);report,_=c.prebind_evidence()
        c.review_prebind({'revision':c.meta['revision'],'digest':report['digest'],'checks':dict.fromkeys(report['checks'],True),'verdict':'ready','reason':'Actual synthetic fixture checked'})
        with self.assertRaises(UserError):self.agent.finish(task['id'],result)
        self.agent.update(task['id'],prebind_seen={cid:{'prebind-'+v:report['digest'] for v in ('parts','assembly','spread')}})
        self.agent.finish(task['id'],result)
        finished=self.agent.read(task['id']);self.assertEqual(finished['state'],'completed');self.assertFalse(finished['artifacts'])
        with zipfile.ZipFile(io.BytesIO(self.agent.download(task['id']))) as z:
            self.assertIn(cid+'/parts/head.png',z.namelist());self.assertIn(cid+'/review/spread.png',z.namelist())
            self.assertEqual(json.loads(z.read('manifest.json'))['scope'],'materials')

    def test_reviewed_pixel_plan_applies_only_once_to_independent_copy(self):
        original=(self.c.directory/'character.json').read_bytes();old=self.c.meta['part_layers']['head']['image_id']
        request={'nonce':uuid.uuid4().hex,'requirements':'仅修整已检查的素材边缘并验收','output_scope':'materials','image_preparation':'preserve',
                 'sources':[{'kind':'character','id':self.c.id}],
                 'material_edits':[{'part':'head','expected_sha256':old,'reason':'reviewed synthetic border','operations':[{'kind':'edge-trim','polygon':[[0,0],[1,0],[1,1],[0,1]],'pixels':.1}]}]}
        first=self.agent.create(request);second=self.agent.create(request)
        self.assertEqual(first,second);self.assertEqual((self.c.directory/'character.json').read_bytes(),original)
        clone=self.agent.loaders['character'](first['sources'][0]['id'])
        self.assertNotEqual(clone.meta['part_layers']['head']['image_id'],old)
        self.assertEqual(len(clone.meta['part_finish_history']),1)
        invalid=copy.deepcopy(request);invalid['nonce']=uuid.uuid4().hex;invalid['material_edits'][0]['expected_sha256']='stale'
        before=set((self.storage/'characters').iterdir())
        with self.assertRaises(UserError):self.agent.create(invalid)
        self.assertEqual(before,set((self.storage/'characters').iterdir()))
