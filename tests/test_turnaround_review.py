import copy,json,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from app.core import UserError,atomic_json,png_bytes
from app.part_prompts import generation_plan
from app.turnaround_review import CHECKS,checked_review,require_review
from app.seam_review import required_detail_anchors,require_detail_reviews,detail_view

class TurnaroundTests(unittest.TestCase):
    def setUp(self):
        from tests.test_asset_sheet_workflow import AssetSheetWorkflowTests
        self.fixture=AssetSheetWorkflowTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture;self.tasks=self.f.tasks;self.id=self.f.task_id;self.entity=self.f.entity
        c=self.f.character();self.job=uuid.uuid4().hex;directory=c.directory/'jobs';directory.mkdir(exist_ok=True)
        atomic_json(directory/(self.job+'.json'),{'id':self.job,'sheet':'turnaround','candidate_ready':True,'mode':'complete-parts','source_image_id':c.meta['image_id']})
        (directory/(self.job+'.png')).write_bytes(png_bytes(self.f.source))
        self.args={'id':self.entity,'job_id':self.job,'phase':'reference','verdict':'ready','checks':dict.fromkeys(CHECKS,True),'reason':'测试记录：同源三视图，六项对照完整'}
    def look(self):return self.tasks.tool(self.id,'spine_image',{'kind':'character','id':self.entity,'view':'candidate','job_id':self.job})
    def review(self,**changes):return self.tasks.tool(self.id,'spine_review_turnaround',{**self.args,**changes})
    def test_reference_needs_actual_view_and_every_check_then_invalidates_on_pixel_change(self):
        with self.assertRaises(UserError):self.review()
        self.look()
        with self.assertRaises(UserError):self.review(checks={**self.args['checks'],'hair_silhouette':False})
        self.review();c=self.f.character();self.assertEqual(require_review(self.tasks.read(self.id),c,'reference')['job_id'],self.job)
        with open(c.directory/'jobs'/(self.job+'.png'),'ab') as file:file.write(b'changed')
        with self.assertRaises(UserError):require_review(self.tasks.read(self.id),c,'reference')
    def test_assembly_requires_current_view_and_revision(self):
        self.look();self.review()
        with self.assertRaises(UserError):self.review(phase='assembly')
        c=self.f.character();c.apply_source_layout({'revision':c.meta['revision'],'parts':self.f.layout})
        self.f.look('assembly');self.review(phase='assembly');c=self.f.character()
        require_review(self.tasks.read(self.id),c,'assembly')
        c.update({'revision':c.meta['revision'],'landmarks':c.meta['landmarks'],'settings':c.meta['settings']})
        with self.assertRaises(UserError):require_review(self.tasks.read(self.id),c,'assembly')
        require_review(self.tasks.read(self.id),c,'reference')
    def test_build_preserves_current_assembly_review_but_rejects_changed_parameters(self):
        c=self.f.character();c.apply_source_layout({'revision':c.meta['revision'],'parts':self.f.layout})
        self.f.look();self.f.look('assembly');self.f.assess(source_kind='character',parts_reviewed=True)
        self.look();self.review();self.review(phase='assembly')
        c=self.f.character();settings={**c.meta['settings'],'stride':.3}
        with self.assertRaisesRegex(UserError,'参数与已验收'):
            self.tasks.tool(self.id,'spine_character',{'id':self.entity,'action':'build','data':{'revision':c.meta['revision'],'settings':settings,'nonce':uuid.uuid4().hex}})
        result=self.f.action('build')
        review=require_review(self.tasks.read(self.id),self.f.character(),'assembly')
        self.assertEqual(review['revision'],result['revision'])
    def test_reference_sheet_cannot_be_imported_as_parts(self):
        c=self.f.character();job=json.loads((c.directory/'jobs'/(self.job+'.json')).read_text())
        for method in (c.apply_part_layout,c.apply_part_sheet):
            with self.assertRaisesRegex(UserError,'三视图'):method({'revision':c.meta['revision'],'parts':self.f.layout},job,self.f.source)
    def test_budget_covers_both_and_replay_does_not_reserve_twice(self):
        self.f.look();self.f.assess(verdict='needs_parts',source_kind='character',missing_parts=['遮挡服装'])
        self.f.allow_mock_generation=True;nonce=uuid.uuid4().hex
        self.f.action('generate-parts',nonce=nonce)
        self.f.action('generate-parts',nonce=nonce)
        budget=self.tasks.summary(self.id)['generation_budget']
        self.assertEqual(budget['used_or_reserved'],0);self.assertEqual(budget['baseline_used_or_reserved'],2);self.assertEqual(budget['total_images_reserved'],2)
        task=self.tasks.read(self.id);task['max_generations']=1;self.tasks.save_tool_state(task)
        before=len(self.f.calls)
        with self.assertRaises(UserError):self.f.action('generate-parts',nonce=uuid.uuid4().hex)
        self.assertEqual(len(self.f.calls),before)
    def test_eight_additional_plus_two_baseline_max_and_zero_forbids_baseline(self):
        task=self.tasks.read(self.id);task['max_generations']=0;self.tasks.save_tool_state(task)
        with self.assertRaises(UserError):self.tasks.reserve(task,'zero',2,{},['turnaround','character-assets'])
        task['max_generations']=8;self.tasks.save_tool_state(task)
        self.tasks.reserve(task,'base',2,{'n':1},['turnaround','character-assets'])
        for i in range(4):self.tasks.reserve(task,'revision'+str(i),2,{'n':i},['turnaround','character-assets'])
        budget=self.tasks.summary(self.id)['generation_budget'];self.assertEqual(budget['used_or_reserved'],8);self.assertEqual(budget['total_images_reserved'],10)
        with self.assertRaises(UserError):self.tasks.reserve(task,'eleventh',1,{},['turnaround'])
        self.assertNotIn('eleventh',self.tasks.read(self.id)['reservations'])
    def test_reviewed_reference_pixels_survive_build_and_rig_clone(self):
        from app.rig import RigProject
        from app.rig_automation import clone_rig
        c=self.f.character();c.apply_source_layout({'revision':c.meta['revision'],'parts':self.f.layout})
        self.look();self.f.look('assembly');record=self.review(phase='assembly')
        c=self.f.character();c.meta['turnaround_reference']=record;atomic_json(c.directory/'character.json',c.meta)
        result=c.build(self.f.storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':c.meta['landmarks'],'settings':c.meta['settings']})
        rig=RigProject(self.f.storage/'rigs'/result['versions'][-1]['rig_id'])
        material=next(m for m in rig.meta['material_sources'] if m['id']=='turnaround')
        self.assertEqual(rig.blob(material),(c.directory/'jobs'/(self.job+'.png')).read_bytes())
        clone=clone_rig(rig,self.f.storage/'rigs'/uuid.uuid4().hex)
        self.assertEqual(clone.blob(material),rig.blob(material));self.assertEqual(clone.meta['turnaround_reference'],record)
    def test_two_distinct_outputs_left_direction_and_no_cutout_language_in_reference(self):
        plan=generation_plan({'mode':'complete-parts','sheets':['turnaround','character-assets'],'body_layout':'combined'},'left')
        reference,parts=plan['items'];self.assertEqual(plan['image_count'],2);self.assertTrue(reference['reference_only']);self.assertEqual(reference['parts'],[])
        self.assertIn('LEFT PROFILE',reference['prompt']);self.assertNotIn('one isolated cut-out',reference['prompt']);self.assertTrue(parts['requires_reviewed_layout'])

class DetailReviewTests(unittest.TestCase):
    def test_head_only_cannot_pass_whole_character_and_stale_digest_is_rejected(self):
        anchors=required_detail_anchors({'bones':[{'name':n} for n in ('head','torso','l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg')]})
        review={'sha256':'a','regions':{'walk':{'head':{'review':{'verdict':'ready'}}}}}
        with self.assertRaises(UserError):require_detail_reviews(review,'a',['walk'],anchors)
        for anchor in anchors:review['regions']['walk'][anchor]={'review':{'verdict':'ready'}}
        require_detail_reviews(review,'a',['walk'],anchors)
        with self.assertRaises(UserError):require_detail_reviews(review,'b',['walk'],anchors)
        with self.assertRaises(UserError):require_detail_reviews(review,'a',['walk','idle'],anchors)
    def test_zero_length_head_hair_uses_actual_attachment_extent(self):
        from app.seam_review import render_closeups
        from PIL import Image
        frame={'bones':[{'name':'head','x':0,'y':0,'ex':0,'ey':0}], 'geometry':[{'slot':'head','path':'hair','xy':[-30,180,50,180,50,70,-30,70]}]}
        image,report=render_closeups([frame],Path('.'),'__hair__',lambda *args:Image.new('RGBA',(400,400),'#333333'))
        self.assertEqual(report['samples'][0]['extent_source'],'animated-chain-or-artwork-bounds')
        self.assertEqual(report['anchor'],'__hair__')
    def test_leg_closeup_covers_actual_chain_endpoint_and_hair_covers_full_art(self):
        frame={'bones':[{'name':'l-upper-leg','x':0,'y':100,'ex':5,'ey':50},{'name':'l-lower-leg','x':5,'y':50,'ex':10,'ey':10},{'name':'l-foot','x':10,'y':10,'ex':40,'ey':0}], 'geometry':[{'slot':'head','path':'hair','xy':[-30,180,50,180,50,70,-30,70]}]}
        v=detail_view(frame,'l-upper-leg');self.assertLess(v[1],0);self.assertGreater(v[2],40);self.assertGreater(v[3],100)
        v=detail_view(frame,'__hair__');self.assertLess(v[0],-30);self.assertGreater(v[3],180)

class TurnaroundServerTests(unittest.TestCase):
    def setUp(self):
        from tests.test_reference_generation import ReferenceGenerationTests
        self.f=ReferenceGenerationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_two_jobs_same_source_and_exact_replay_without_extra_provider_calls(self):
        payload=self.f.request(sheets=['turnaround','character-assets'])
        jobs=self.f.post(payload)
        self.assertEqual(len(jobs),2);self.assertEqual(self.f.matrix.request.call_count,2)
        self.assertEqual(jobs[0]['source_image_id'],jobs[1]['source_image_id'])
        self.assertEqual(jobs[0]['sheet'],'turnaround');self.assertTrue(jobs[0]['reference_only'])
        self.assertFalse(jobs[1]['reference_only']);self.assertNotEqual(jobs[0]['id'],jobs[1]['id'])
        self.f.post(payload);self.assertEqual(self.f.matrix.request.call_count,2)
