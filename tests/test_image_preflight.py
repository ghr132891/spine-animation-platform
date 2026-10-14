import base64,io,json,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image,ImageDraw
from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import UserError,png_bytes
from app.image_preflight import checked_assessment,requested_camera_policy,selected_sheets
from app.part_prompts import view_prompt
from app.provider import Matrix
ROOT=Path(__file__).resolve().parents[1]
def fixture_image():
    im=Image.new('RGBA',(300,400));ImageDraw.Draw(im).rectangle((40,20,260,380),fill='orange');return im

class ImagePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.storage=Path(self.tmp.name)
        self.loaders={'character':lambda id:CharacterProject(self.storage/'characters'/id)}
        self.tasks=AgentTasks(self.storage,ROOT,self.loaders,None);self.calls=[]
        self.tasks.api=self.api
        image=fixture_image()
        self.task=self.tasks.create({'nonce':uuid.uuid4().hex,'requirements':'保留机器人外观，先检查视角，再制作自然行走','image':base64.b64encode(png_bytes(image)).decode(),'max_generations':4,'image_preparation':'auto'})
        self.id=self.task['id'];self.entity=self.task['sources'][0]['id']
    def tearDown(self):self.tmp.cleanup()
    def api(self,route,data=None,binary=False):
        self.calls.append((route,data));c=self.loaders['character'](self.entity)
        if binary:return png_bytes(c.image())
        return {'versions':[]}
    def assess(self,verdict='ready',**extra):
        return self.tasks.tool(self.id,'spine_assess_image',{'id':self.entity,'verdict':verdict,'observed_view':'正面背手','target_view':'朝右的轻微三分之四视角','target_actions':['idle','walk'],'missing_parts':['far hand'] if verdict=='needs_parts' else [],'reason':'需要露出双手与脚掌',**extra})
    def look(self):return self.tasks.tool(self.id,'spine_image',{'kind':'character','id':self.entity,'view':'image'})
    def action(self,action,**kw):
        c=self.loaders['character'](self.entity)
        return self.tasks.tool(self.id,'spine_character',{'id':self.entity,'action':action,'data':{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,**kw}})
    def test_actual_view_and_ready_required_before_build(self):
        with self.assertRaises(UserError):self.assess()
        with self.assertRaises(UserError):self.action('build')
        self.look();self.assess('needs_view')
        with self.assertRaises(UserError):self.action('build')
        self.assess();self.action('build');self.assertTrue(self.calls[-1][0].endswith('/build'))
    def test_target_view_bound_and_budget_counted_once(self):
        self.look();report=self.assess('needs_view')
        nonce=uuid.uuid4().hex
        self.action('generate-view',nonce=nonce,target_view='fake requested view')
        self.assertEqual(self.calls[-1][1]['target_view'],report['target_view'])
        self.assertEqual(self.calls[-1][1]['target_actions'],['idle','walk'])
        self.action('generate-view',nonce=nonce,target_view='fake requested view')
        self.assertEqual(self.tasks.summary(self.id)['generation_budget']['used_or_reserved'],1)
        self.assess('needs_parts');self.action('generate-parts',sheets=['arms'])
        self.assertEqual(self.tasks.summary(self.id)['generation_budget']['used_or_reserved'],2)
        self.assertEqual(self.tasks.summary(self.id)['image_assessments'][self.entity]['reviewer'],'codex-vision')
    def test_revision_changes_invalidate_assessment(self):
        self.look();self.assess();c=self.loaders['character'](self.entity);c.set_image(png_bytes(c.image()),c.meta['revision'])
        with self.assertRaises(UserError):self.action('build')
        self.look();self.assess();self.action('build')
    def test_preserve_and_zero_budget_stop_generation(self):
        self.tasks.update(self.id,image_preparation='preserve');self.look();self.assess('needs_view')
        with self.assertRaises(UserError):self.action('generate-view')
        self.tasks.update(self.id,image_preparation='auto',max_generations=0)
        with self.assertRaises(UserError):self.action('generate-view')
        self.assertEqual(self.tasks.summary(self.id)['generation_budget']['used_or_reserved'],0)
    def test_zero_budget_still_allows_reviewed_existing_art_to_build(self):
        self.tasks.update(self.id,max_generations=0)
        self.look();self.assess('ready');self.action('build')
        self.assertTrue(self.calls[-1][0].endswith('/build'))
        self.assertFalse(any(route.endswith(('/generate','/generate-view','/generate-parts')) for route,_ in self.calls))
        self.assertEqual(self.tasks.summary(self.id)['generation_budget']['used_or_reserved'],0)
    def test_ready_cannot_bypass_missing_limb_review_and_reach_build(self):
        self.look()
        with self.assertRaisesRegex(UserError,'ready'):
            self.assess('ready',missing_parts=['far hand'])
        self.assertFalse(self.tasks.summary(self.id)['image_assessments'])
        with self.assertRaises(UserError):self.action('build')
        self.assertFalse(any(route.endswith('/build') for route,_ in self.calls))
        self.assess('needs_parts',missing_parts=['far hand'])
        with self.assertRaises(UserError):self.action('build')
    def test_selective_sheets_and_invalid_reports(self):
        for value in ([],['arms','arms'],['unknown'],[{}]):
            with self.assertRaises(UserError):selected_sheets(value)
        self.assertEqual(selected_sheets(['arms']),['arms'])
        with self.assertRaises(UserError):checked_assessment({'verdict':'ready'},'hash')
        self.look()
        with self.assertRaises(UserError):self.assess('needs_parts',missing_parts=[])
        self.assess('unsupported')
        with self.assertRaises(UserError):self.action('generate-parts')
    def test_candidate_image_does_not_count_as_current_source_review(self):
        folder=self.storage/'characters'/self.entity/'jobs';folder.mkdir();job=uuid.uuid4().hex
        (folder/(job+'.png')).write_bytes(png_bytes(Image.new('RGBA',(300,300),'blue')))
        result=self.tasks.tool(self.id,'spine_image',{'kind':'character','id':self.entity,'view':'candidate','job_id':job})
        self.assertEqual(result['content'][0]['type'],'image')
        with self.assertRaises(UserError):self.assess()

class MatrixPreparationTests(unittest.TestCase):
    def test_camera_policy_interprets_request_not_observed_pixels_or_arm_names(self):
        cases=[('right-facing profile','profile','right'),('朝左纯侧面','profile','left'),
               ('side-on facing left','profile','left'),('near-profile facing right','requested','right'),
               ('近正侧面朝右','requested','right'),('three-quarter facing left','requested','left'),
               ('front view, no profile','requested',None),('正面，不要正侧面','requested',None),
               ('purple patch on left arm','requested',None),
               ('strict profile, not three-quarter','profile',None),
               ('facing left or facing right','requested',None)]
        for target,view,facing in cases:
            with self.subTest(target=target):
                result=requested_camera_policy(target)
                self.assertEqual(result['view'],view);self.assertEqual(result['facing'],facing)
                self.assertTrue(result['uniform_camera']);self.assertTrue(result['preserve_side_asymmetry'])
        with self.assertRaises(UserError):requested_camera_policy(None)
    def test_assessment_strips_lists_rejects_blank_and_contradictory_ready(self):
        data={'verdict':'ready','observed_view':'right three-quarter','target_view':'right three-quarter',
              'reason':'Complete art suits the requested motions','target_actions':[' idle ',' walk '],'missing_parts':[]}
        result=checked_assessment(data,'source-hash')
        self.assertEqual(result['target_actions'],['idle','walk'])
        self.assertEqual(data['target_actions'],[' idle ',' walk '])
        self.assertEqual(result['image_id'],'source-hash')
        for field in ('target_actions','missing_parts'):
            with self.subTest(field=field),self.assertRaises(UserError):
                checked_assessment({**data,field:[' ']},'source-hash')
        with self.assertRaisesRegex(UserError,'ready'):
            checked_assessment({**data,'missing_parts':['missing far palm']},'source-hash')
    def test_negative_frontal_view_and_unrelated_angle_do_not_change_camera_branch(self):
        policy=requested_camera_policy('朝右侧面，避免正面')
        self.assertFalse(policy['front_or_back']);self.assertEqual(policy['facing'],'right')
        self.assertEqual(requested_camera_policy('190 degrees')['view'],'requested')
        self.assertEqual(requested_camera_policy('90 degrees facing left')['view'],'profile')
    def test_reference_and_motion_prompt_preserve_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Matrix(tmp);m.request=Mock(return_value={'data':{'gen_id':'test-id'}})
            ref='https://assets.example/character.png'
            with self.assertRaises(UserError):m.image_reference(Image.new('RGBA',(150,200),'orange'))
            prompt=view_prompt('side view facing LEFT',['jump'],'Keep the original shoes.')
            self.assertIn('LEFT',prompt);self.assertIn('jump',prompt);self.assertIn('Change only the preparation pose and camera',prompt)
            m.generate_view(prompt,ref);wire=m.request.call_args.args[1]['input']
            self.assertEqual(wire['input_images'],[ref]);self.assertEqual(wire['prompt'],prompt)
            m.generate_part_sheet('arms',ref)
            self.assertEqual(m.request.call_args.args[1]['input']['input_images'],[ref])
    def test_server_upload_uses_public_reference_and_deduplicates(self):
        from app import server
        from app.jobs import JobHistory
        with tempfile.TemporaryDirectory() as tmp:
            c=CharacterProject.create(Path(tmp),'robot');c.set_image(png_bytes(fixture_image()),0)
            m=Matrix(tmp);m.request=Mock(return_value={'data':{'gen_id':'part-id'}})
            jobs=JobHistory(tmp,m,'characters')
            handler=object.__new__(server.Handler);handler.path='/api/characters/'+c.id+'/generate-parts';handler.send=lambda value,*a,**k:value
            payload={'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'sheets':['body','arms','legs'],'reference_url':'https://assets.example/character.png'}
            with patch.object(server,'get_character',return_value=c),patch.object(server,'MATRIX',m),patch.object(server,'CHARACTER_JOBS',jobs):
                result=handler.post(payload)
                repeat=handler.post(payload)
            self.assertEqual(len(result),3);self.assertEqual([j['sheet'] for j in result],payload['sheets'])
            self.assertEqual(m.request.call_count,3)
            self.assertEqual([j['id'] for j in result],[j['id'] for j in repeat])
            self.assertEqual(len({j['id'] for j in result}),3)
            for job in result:
                self.assertRegex(job['id'],r'^[a-f0-9]{32}$')
                self.assertTrue(jobs.path(c.id,job['id']).exists())
                self.assertEqual(job['generation_nonce'],payload['nonce'])
            self.assertEqual(m.request.call_args.args[1]['input']['input_images'],[payload['reference_url']])

if __name__=='__main__':unittest.main()


class ReferenceFailurePhaseTests(unittest.TestCase):
    def test_idle_only_does_not_require_side_view_when_walk_is_explicitly_excluded(self):
        from app.image_preflight import require_side_camera
        for text in ['只做正面待机，不要行走','front idle only, no walking','idle only, do not generate walking']:
            require_side_camera({'target_actions':['idle'],'camera_view':'front','direction_consistent':False},text)
    def test_reference_readback_failure_is_not_a_submitted_generation(self):
        from app.provider import ProviderError,classify_provider_failure
        with tempfile.TemporaryDirectory() as directory:
            matrix=Matrix(Path(directory))
            with patch.object(matrix.hosting,'status',return_value={'ready':True,'provider':'s3'}),patch.object(matrix.hosting,'publish',return_value={'url':'https://example.com/fixture.png','expires_at':9999999999,'provider':'s3'}),patch.object(matrix,'download_result',side_effect=ProviderError(classify_provider_failure(category='download_failed'))),patch.object(matrix,'request') as paid:
                with self.assertRaises(ProviderError) as caught:matrix.ensure_reference(fixture_image())
                self.assertEqual(caught.exception.details['category'],'reference_unavailable')
                self.assertEqual(caught.exception.details['operation'],'reference')
                paid.assert_not_called()
                self.assertIsNone(matrix.cached_reference(fixture_image()))
