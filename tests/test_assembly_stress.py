import copy,io,json,unittest,uuid,zipfile
from unittest.mock import Mock,patch
from PIL import Image
from tests import test_assembly_pipeline as fixture
from app.core import UserError,png_bytes
from app import assembly_stress as stress

class AssemblyStressTests(unittest.TestCase):
 def setUp(self):
  f=fixture.AssemblyExecutionTests();f.setUp();self.addCleanup(f.doCleanups);self.c=f.c
  self.agent=Mock();self.agent.runtime_check.return_value=({'passed':True},png_bytes(Image.new('RGBA',(40,40),'blue')))
 def test_real_skeleton_and_weighted_meshes_are_used_without_creating_versions(self):
  before=copy.deepcopy(self.c.meta);blob,s=stress.diagnostic_zip(self.c)
  self.assertEqual(self.c.meta,before);self.assertEqual(set(s['animations']),set(stress.CLIPS))
  self.assertTrue(any(a.get('type')=='mesh' for v in s['skins'][0]['attachments'].values() for a in v.values()))
  self.assertEqual([f['value'] for f in s['animations']['stress-arms']['bones']['l-upper-arm']['rotate']],[0,-35,-35,0,35,35,0,0,0])
  with zipfile.ZipFile(io.BytesIO(blob)) as z:self.assertIn('skeleton.atlas',z.namelist())
 def test_nine_runtime_panels_including_both_closeups_and_cache(self):
  r=stress.prepare(self.agent,self.c);self.assertEqual(self.agent.runtime_check.call_count,9)
  self.assertEqual({p['anchor'] for p in r['panels']},{None,'__hip__','__straps__'});self.assertEqual(r['visual_verdict'],'not_reviewed')
  self.assertEqual(stress.prepare(self.agent,self.c),r);self.assertEqual(self.agent.runtime_check.call_count,9)
  with self.assertRaises(UserError):stress.require(self.c)
 def test_failed_pressure_blocks_formal_build_even_with_static_ready(self):
  r=stress.prepare(self.agent,self.c);c=self.c;p,_=c.prebind_evidence();c.review_prebind({'revision':c.meta['revision'],'digest':p['digest'],'checks':dict.fromkeys(p['checks'],True),'verdict':'ready','reason':'Synthetic static pass'})
  checks=dict.fromkeys(stress.CHECKS,True);checks['hip_contour']=False
  stress.review(c,{'revision':c.meta['revision'],'digest':r['digest'],'checks':checks,'verdict':'needs_repair','reason':'hip spike under driven pose'})
  with self.assertRaises(UserError):c.build(c.directory.parent.parent,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':c.meta['landmarks'],'settings':c.meta['settings']})
  self.assertFalse(c.meta['versions'])
 def test_missing_panels_false_checks_stale_and_corrupt_data_rejected(self):
  r=stress.prepare(self.agent,self.c);data={'revision':self.c.meta['revision'],'digest':r['digest'],'checks':dict.fromkeys(stress.CHECKS,True),'verdict':'ready','reason':'synthetic fixture'}
  with self.assertRaises(UserError):stress.review(self.c,{**data,'checks':{**data['checks'],'strap_integrity':False}})
  stress.review(self.c,data);stress.require(self.c)
  c=self.c;c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'head':{'dx':1}},'draw_order':c.review_order([p['name'] for p in c.parts()])}})
  with self.assertRaises(UserError):stress.require(c)
 def test_panel_retrieval_rejects_old_digest(self):
  r=stress.prepare(self.agent,self.c)
  with self.assertRaises(UserError):stress.panel(self.c,'p0','0'*64)
  _,p,blob=stress.panel(self.c,'p0',r['digest']);self.assertTrue(blob.startswith(b'\x89PNG'))
 def test_runtime_failure_does_not_create_ready_manifest(self):
  self.agent.runtime_check.side_effect=UserError('renderer offline')
  with self.assertRaises(UserError):stress.prepare(self.agent,self.c)
  with self.assertRaises(UserError):stress.read_manifest(self.c)
