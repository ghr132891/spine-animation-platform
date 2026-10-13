import base64
import io
import json
import re
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.automation import AutoReskin
from app.core import Project,UserError,png_bytes,atomic_json
from app.jobs import JobHistory
from app.provider import Matrix


class FakeMatrix:
    job_metadata = Matrix.job_metadata
    def __init__(self):self.submissions=[];self.ambiguous=False;self.fail=False
    def status(self):return {'configured':True}
    def generate(self,prompt,reference_url=None):
        self.submissions.append((prompt,reference_url))
        if self.ambiguous:raise TimeoutError('ambiguous')
        return 'remote-'+str(len(self.submissions))
    def poll(self,id,model=None):
        return {'data':{'status':'failed' if self.fail else 'completed','outputs':[{'item_status':'completed','data':{'image_url':'https://example.test/art.png'}}]}}
    def download_result(self,url):return png_bytes(Image.new('RGBA',(32,32),(180,30,200,255)))


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.storage=Path(self.tmp.name);self.provider=FakeMatrix();self.cache={}
        self.jobs=JobHistory(self.storage,self.provider);self.a=AutoReskin(self.storage,self.provider,self.jobs,self.load)
        self.p=Project(self.storage/'projects'/uuid.uuid4().hex,{p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()},'test');self.cache[self.p.id]=self.p
    def tearDown(self):self.tmp.cleanup()
    def load(self,id):
        if id not in self.cache:self.cache[id]=Project(self.storage/'projects'/id)
        return self.cache[id]
    def request(self,**changes):return {'nonce':uuid.uuid4().hex,'project_id':self.p.id,'style':'深蓝银色科技装甲','region_ids':['r0','r1'],**changes}
    def finish(self,id):
        self.a.step(id);task=self.a.read(id)
        for job in task['jobs']:self.jobs.refresh(self.jobs.path(task['output_project_id'],job['id']),force=True)
        self.a.step(id);return self.a.summary(id)
    def test_plan_is_free_and_exposes_exact_submitted_prompt(self):
        request=self.request();plan=self.a.plan(request)
        self.assertEqual(len(self.provider.submissions),0);self.assertFalse(self.a.root.exists());self.assertEqual(self.p.revision,0)
        for p in plan['parts']:self.assertEqual(p['provider_input'],Matrix.generation_input(p['prompt'],p['reference_url']))
        self.assertEqual(plan['generation_count'],2);self.assertEqual(plan['source']['animations'],self.p.animations)
    def test_fabric_swatch_does_not_inherit_anatomical_part_instructions(self):
        style='Jade green matte linen fabric. Fine even weave.'
        request=self.request(style=style,region_ids=['r0'])
        plan=self.a.plan(request);prompt=plan['parts'][0]['provider_input']['prompt']
        self.assertIn(style,prompt)
        for word in ['face','limb','character','part context','median RGB',self.p.by_region['r0']['name']]:
            self.assertNotRegex(prompt.lower(),r'\b'+re.escape(word.lower())+r'\b')
        task=self.a.create(request);self.a.step(task['id'])
        submitted=Matrix.generation_input(*self.provider.submissions[0])
        self.assertEqual(submitted,plan['parts'][0]['provider_input'])
    def test_completed_batch_preserves_source_skeleton_alpha_and_unselected_pixels(self):
        request=self.request();original=self.p.pages_data.copy();task=self.a.create(request);done=self.finish(task['id'])
        self.assertEqual(done['state'],'completed');self.assertEqual(done['progress']['ready'],2);self.assertEqual(self.p.pages_data,original);self.assertEqual(self.p.revision,0)
        output=self.load(done['output_project_id']);self.assertEqual(output.revision,1);self.assertTrue(output.validate()['passed'])
        with zipfile.ZipFile(io.BytesIO(self.a.download(done['id']))) as z:
            self.assertEqual(z.read(self.p.skeleton),self.p.files[self.p.skeleton]);self.assertEqual(z.read(self.p.atlas),self.p.files[self.p.atlas])
            for page in self.p.pages:
                before=np.array(Image.open(io.BytesIO(original[page['file']])));after=np.array(Image.open(io.BytesIO(z.read(page['file']))));self.assertTrue(np.array_equal(before[:,:,3],after[:,:,3]))
        for r in self.p.regions[2:]:self.assertEqual(png_bytes(output.part(r['id'])),png_bytes(self.p.part(r['id'])))
        self.assertNotEqual(png_bytes(output.part('r0')),png_bytes(self.p.part('r0')))
    def test_nonce_reuse_and_restart_never_resubmit(self):
        request=self.request();task=self.a.create(request);self.assertEqual(task['id'],self.a.create(request)['id'])
        self.a.step(task['id']);self.assertEqual(len(self.provider.submissions),2)
        self.a=AutoReskin(self.storage,self.provider,self.jobs,self.load);self.a.step(task['id']);self.assertEqual(len(self.provider.submissions),2)
        done=self.finish(task['id']);self.assertEqual(done['state'],'completed');self.a.step(task['id']);self.assertEqual(len(self.provider.submissions),2)
        with self.assertRaises(UserError):self.a.create({**request,'style':'另一种风格'})
    def test_ambiguous_submission_requires_binding_and_keeps_single_remote_call(self):
        task=self.a.create(self.request(region_ids=['r0']));self.provider.ambiguous=True;self.a.step(task['id'])
        self.assertEqual(self.a.summary(task['id'])['state'],'needs_attention');self.a.step(task['id']);self.assertEqual(len(self.provider.submissions),1)
        job=self.a.read(task['id'])['jobs'][0];self.a.resume(task['id'],{'generation_ids':{job['id']:'already-created-remote'}});self.a.step(task['id'])
        self.assertEqual(self.a.summary(task['id'])['state'],'completed');self.assertEqual(len(self.provider.submissions),1)
    def test_failed_generation_never_exports_partial_batch(self):
        task=self.a.create(self.request());self.provider.fail=True;done=self.finish(task['id']);self.assertEqual(done['state'],'failed')
        with self.assertRaises(UserError):self.a.download(task['id'])
        self.assertEqual(self.load(task['output_project_id']).revision,0)
    def test_budget_and_missing_reference_are_rejected_before_billing(self):
        for changes in [{'max_generations':1},{'mode':'reference'},{'region_ids':['r0','r0']},{'region_ids':[]}]:
            with self.assertRaises(UserError):self.a.create(self.request(**changes))
        self.assertEqual(self.provider.submissions,[]);self.assertFalse(self.a.root.exists())
    def test_cancel_stops_unsubmitted_parts_even_during_remote_submission(self):
        task=self.a.create(self.request());generate=self.provider.generate
        def call(prompt,ref=None):
            result=generate(prompt,ref);self.a.cancel(task['id']);return result
        self.provider.generate=call;self.a.step(task['id']);self.assertEqual(self.a.summary(task['id'])['state'],'cancelled');self.assertEqual(len(self.provider.submissions),1)
        self.a.step(task['id']);self.assertEqual(len(self.provider.submissions),1)
    def test_source_snapshot_and_output_edits_are_protected(self):
        task=self.a.create(self.request());baseline=dict(self.load(task['output_project_id']).files);self.p.recolor(['r0'],70,1,1,0)
        self.assertEqual(self.load(task['output_project_id']).files,baseline)
        output=self.load(task['output_project_id']);output.recolor(['r0'],30,1,1,0);self.a.step(task['id'])
        self.assertEqual(self.provider.submissions,[]);self.assertEqual(self.a.summary(task['id'])['state'],'needs_attention');self.assertEqual(output.revision,1)
    def test_inline_zip_plan_and_task_need_no_preimport(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for n,b in self.p.files.items():z.writestr('input/'+n,b)
        request={'nonce':uuid.uuid4().hex,'files':[{'name':'source.zip','data':base64.b64encode(out.getvalue()).decode()}],'style':'紫色陶瓷','region_ids':['r0']}
        plan=self.a.plan(request);self.assertIsNone(plan['source']['project_id']);task=self.a.create(request);self.assertEqual(self.finish(task['id'])['state'],'completed')
    def test_result_restart_after_commit_recovers_without_overwriting_edits(self):
        task=self.a.create(self.request());self.finish(task['id']);saved=self.a.read(task['id']);self.a.update(task['id'],state='assembling')
        self.a=AutoReskin(self.storage,self.provider,self.jobs,self.load);self.a.step(task['id']);self.assertEqual(self.a.summary(task['id'])['state'],'completed');self.assertEqual(len(self.provider.submissions),2)
        self.a.update(task['id'],state='assembling');output=self.load(saved['output_project_id']);output.recolor(['r1'],50,1,1,1);self.a.step(task['id']);self.assertEqual(self.a.summary(task['id'])['state'],'needs_attention');self.assertEqual(output.revision,2)
    def test_reference_prompt_keeps_exact_reference_canvas_layout(self):
        request=self.request(region_ids=['r0'],mode='reference',reference_urls={'r0':'https://example.test/reference.png'});task=self.a.create(request);done=self.finish(task['id'])
        self.assertEqual(done['state'],'completed');self.assertEqual(self.provider.submissions[0][1],'https://example.test/reference.png');self.assertNotIn('mode',task['plan']['parts'][0]['layout'])

    def test_omitting_region_ids_automatically_reskins_all_parts(self):
        request=self.request();request.pop('region_ids');task=self.a.create(request);done=self.finish(task['id'])
        self.assertEqual(done['state'],'completed');self.assertEqual(done['progress']['ready'],len(self.p.regions));self.assertEqual(len(self.provider.submissions),len(self.p.regions))
        self.assertEqual(set(self.load(done['output_project_id']).changed),set(self.p.by_region))

    def test_known_failed_job_stops_remaining_submissions(self):
        task=self.a.create(self.request());spec=self.a.read(task['id'])['jobs'][0];path=self.jobs.path(task['output_project_id'],spec['id']);job=json.loads(path.read_text());job['status']='failed';atomic_json(path,job)
        self.a.step(task['id']);self.assertEqual(self.a.summary(task['id'])['state'],'failed');self.assertEqual(self.provider.submissions,[])


if __name__=='__main__':unittest.main()
