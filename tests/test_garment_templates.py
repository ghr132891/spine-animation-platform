import copy,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app.core import UserError,png_bytes
from app import garment_templates as g
from tests import test_assembly_pipeline as fixtures
from app.part_prompts import generation_plan,part_prompt

class GarmentTemplateTests(unittest.TestCase):
 def setUp(self):
  f=fixtures.AssemblyExecutionTests();f.setUp();self.addCleanup(f.doCleanups);self.c=f.c
 def opening(self,name='l-upper-leg'):
  c=self.c;side=name[:2];leg=name.endswith('leg');j='hip' if leg else 'shoulder';k='knee' if leg else 'elbow';w,h=c.image().size
  x,y=c.meta['landmarks'][side+j];end=c.meta['landmarks'][side+k][1]*h
  return {'span':[x*w-10,x*w+10],'join_y':y*h,'release_y':end-3,'insert_depth':2,'evidence':'reviewed reference opening'}
 def install(self,**kw):
  c=self.c;c.garment_template({'revision':c.meta['revision'],'template':{'type':'trousers',**kw}});return c.meta['garment_template']
 def test_typed_prompts_remove_conflicting_material_instructions(self):
  for kind in g.CATALOG:
   d,t=g.generation_request(self.c,{'mode':'complete-parts','garment_template':{'type':kind}})
   prompt=generation_plan(d)['items'][1]['prompt']
   if kind in ('trousers','shorts','armor'):self.assertNotIn('裙摆自然垂落',prompt)
   if kind in ('skirt','split-robe','armor'):self.assertNotIn('裤腰和腿根使用连续的同源裤料',prompt)
 def test_reference_dimensions_and_material_ownership(self):
  t=self.install();w,h=self.c.image().size
  self.assertAlmostEqual(t['dimensions']['hip_span'],.12*w)
  self.assertEqual(t['profile']['ownership']['hip_cloth'],'shared-costume-master');self.assertEqual(t['profile']['ownership']['pockets'],'torso')
  self.assertEqual(t['status'],'needs_opening_measurements')
 def test_different_reference_sizes_scale_dimensions(self):
  a=g.contract(self.c,{'type':'trousers'});im=self.c.image()
  with patch.object(self.c,'image',return_value=im.resize((im.width*2,im.height*2))):b=g.contract(self.c,{'type':'trousers'})
  self.assertAlmostEqual(b['dimensions']['shoulder_span'],a['dimensions']['shoulder_span']*2)
 def test_shared_source_cannot_override_target_or_hash(self):
  with self.assertRaises(UserError):g.contract(self.c,{'type':'trousers','shared_sources':{'l-upper-leg':{'donor_layout':{},'texture_join':{'mode':'shared-pixels'},'part':'head'}}})
 def test_invalid_type_is_a_user_error(self):
  with self.assertRaises(UserError):g.contract(self.c,{'type':[]})
 def test_bad_geometry_and_material_declaration_rejected(self):
  for opening in ({**self.opening(),'span':[0,2]},{**self.opening(),'insert_depth':50},{**self.opening(),'evidence':''}):
   with self.assertRaises(UserError):self.install(openings={'l-upper-leg':opening})
  with self.assertRaises(UserError):g.contract(self.c,{'type':'skirt','shared_sources':{'l-upper-leg':{'donor_layout':{},'texture_join':{'mode':'shared-pixels'}}}})
 def test_missing_shared_pixels_produces_specific_completion(self):
  self.install(openings={'l-upper-leg':self.opening()});p=g.plan(self.c)
  self.assertFalse(p['connections']);self.assertEqual(p['completion_sheets'],['costume']);self.assertEqual(p['completion_requests'][0]['part'],'l-upper-leg');self.assertEqual(len(p['completion_requests'][0]['box']),4)
 def test_changed_mother_anchors_invalidate_measurements(self):
  self.install();self.c.meta['landmarks']['l-shoulder'][0]+=.01
  with self.assertRaisesRegex(UserError,'重新测量'):g.current(self.c)
 def test_explicit_empty_missing_regions_does_not_generate(self):
  self.install()
  with self.assertRaisesRegex(UserError,'不重复提交'):g.generation_request(self.c,{'completion_only':True})
 def test_targeted_completion_and_provider_prompt_match(self):
  self.install(missing_regions=[{'part':'l-upper-leg','box':[40,40,80,80],'reason':'hidden cloth missing'}])
  data,t=g.generation_request(self.c,{'mode':'complete-parts','completion_only':True,'instructions':'保留原图'})
  self.assertEqual(data['sheets'],['costume']);plan=generation_plan(data)
  self.assertEqual(plan['items'][0]['prompt'],part_prompt('costume',data['_garment_notes']['costume'],'right',True,data.get('body_layout','separate'),'trousers'))
  self.assertIn('40,40,80,80',plan['items'][0]['prompt']);self.assertEqual(plan['image_count'],1)
 def test_generation_preview_never_modifies_character(self):
  before=copy.deepcopy(self.c.meta);d,t=g.generation_request(self.c,{'mode':'complete-parts','garment_template':{'type':'armor'}})
  self.assertEqual(self.c.meta,before);self.assertEqual(t['type'],'armor');self.assertIn('硬甲',d['_garment_notes']['character-assets'])
 def test_untrusted_internal_notes_are_not_used(self):
  d,t=g.generation_request(self.c,{'_garment_notes':{'character-assets':'UNTRUSTED'}})
  self.assertNotIn('_garment_notes',d)
 def test_skirt_is_not_patched_with_external_garment(self):
  self.c.garment_template({'revision':self.c.meta['revision'],'template':{'type':'skirt','openings':{'l-upper-leg':self.opening()}}})
  self.assertFalse(g.plan(self.c)['connections']);self.assertEqual(self.c.meta['garment_template']['profile']['ownership']['bare_legs'],'legs')
 def test_auto_execution_hook_on_import_and_no_hook_for_unrelated_manual_repair(self):
  from tests.test_asset_sheet_workflow import asset_fixture
  self.install();im,layout=asset_fixture()
  with patch('app.garment_templates.execute',wraps=g.execute) as execute:
   self.c.apply_source_layout({'revision':self.c.meta['revision'],'parts':[layout[0]]});self.assertEqual(execute.call_count,1)
  with patch('app.garment_templates.execute',wraps=g.execute) as execute:
   self.c.prepare_assembly({'revision':self.c.meta['revision']});self.assertEqual(execute.call_count,0)
 def test_failed_template_update_does_not_lose_old_assets(self):
  before=(self.c.directory/'character.json').read_bytes()
  with self.assertRaises(UserError):self.c.prepare_assembly({'revision':self.c.meta['revision'],'garment_template':{'type':'invalid'}})
  self.assertEqual(before,(self.c.directory/'character.json').read_bytes())
