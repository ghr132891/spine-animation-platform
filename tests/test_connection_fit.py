import copy,json,tempfile,unittest
from pathlib import Path
import numpy as np
from PIL import Image
from app.connection_fit import fit_pixels,apply,registered_donor
from app.core import UserError,png_bytes,sha
from tests import test_assembly_pipeline as fixtures

class ConnectionPixelsTests(unittest.TestCase):
 def setUp(self):
  a=np.zeros((100,80,4),dtype=np.uint8);a[:,10:70]=[60,70,100,255];a[::7,10:70,:3]=[65,75,105]
  self.image=Image.fromarray(a);self.record={'canvas_offset':[0,0],'chain':'leg','canvas_pins':{'hip':[40,10],'knee':[40,80]}}
  self.donor=Image.new('RGBA',(80,100),(70,80,110,255));self.dr={'canvas_offset':[0,0]}
  self.plan={'reason':'measured garment aperture','join_y':20,'release_y':65,'target_span':[20,60]}
 def run_fit(self,**kwargs):return fit_pixels(self.image,self.record,self.donor,self.dr,{**self.plan,**kwargs})
 def test_local_width_change_preserves_distal_and_metadata(self):
  before=copy.deepcopy(self.record);im,r=self.run_fit();a=np.array(im)
  self.assertTrue(np.array_equal(a[65:],np.array(self.image)[65:]));self.assertEqual(before,self.record)
  xs=np.flatnonzero(a[20,:,3]>128);self.assertEqual([int(xs[0]),int(xs[-1])],[20,60]);self.assertTrue(r['pins_preserved'])
 def test_unsafe_shapes_and_parameters_rejected(self):
  for patch in ({'release_y':90},{'target_span':[38,43]},{'target_span':[2,30]},{'join_y':float('nan')},{'texture_join':[]}):
   with self.subTest(patch=patch),self.assertRaises(UserError):self.run_fit(**patch)
 def test_color_match_keeps_alpha_and_real_texture(self):
  plain,_=self.run_fit();colored,r=self.run_fit(texture_join={'donor_y':20,'target_y':25,'release_y':60,'material_note':'same cloth'})
  a,b=np.array(plain),np.array(colored);self.assertTrue(np.array_equal(a[:,:,3],b[:,:,3]));self.assertLess(r['texture_join']['after_rgb_mae'],r['texture_join']['before_rgb_mae']);self.assertTrue(np.array_equal(a[60:],b[60:]))
 def test_shared_pixels_copy_pattern_without_alpha_fade(self):
  donor=np.array(self.donor);donor[:,38:41,:3]=[20,30,50];self.donor=Image.fromarray(donor)
  spec={'mode':'shared-pixels','start_y':10,'solid_until_y':30,'release_y':55,'material_note':'same mother cloth'}
  base,_=self.run_fit();im,r=self.run_fit(texture_join=spec);a=np.array(im);visible=a[25,:,3]>0
  self.assertTrue(np.array_equal(a[25,visible,:3],donor[25,visible,:3]));self.assertTrue(np.array_equal(a[:,:,3],np.array(base)[:,:,3]));self.assertTrue(np.array_equal(a[55:],np.array(base)[55:]));self.assertFalse(r['texture_join']['alpha_fade'])
 def test_shared_pixels_reject_missing_source(self):
  a=np.array(self.donor);a[22,40,3]=0;self.donor=Image.fromarray(a)
  with self.assertRaisesRegex(UserError,'透明缺口'):self.run_fit(texture_join={'mode':'shared-pixels','start_y':10,'solid_until_y':30,'release_y':55,'material_note':'cloth'})
 def test_color_match_rejects_different_material(self):
  self.donor=Image.new('RGBA',(80,100),(240,230,200,255))
  with self.assertRaisesRegex(UserError,'色差过大'):self.run_fit(texture_join={'donor_y':20,'target_y':25,'release_y':60,'material_note':'wrong material'})

class ConnectionTransactionTests(unittest.TestCase):
 def setUp(self):
  fixture=fixtures.AssemblyExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups);self.c=fixture.c
 def test_invalid_source_hash_is_atomic(self):
  c=self.c;before=(c.directory/'character.json').read_bytes();revision=c.meta['revision']
  with self.assertRaises(UserError):c.prepare_assembly({'revision':revision,'connections':[{'part':'l-upper-leg','donor':'torso','source_sha256':'stale'}]})
  self.assertEqual(before,(c.directory/'character.json').read_bytes());self.assertEqual(c.meta['revision'],revision)
 def test_invalid_donor_type_is_user_error(self):
  with self.assertRaises(UserError):apply(self.c,[{'part':'l-upper-leg','donor':[]}])
 def test_donor_path_cannot_escape_character(self):
  with self.assertRaises(UserError):registered_donor(self.c,{'job_id':'../../outside'})
 def test_failed_second_item_does_not_commit_first(self):
  from unittest.mock import patch
  c=self.c;before=copy.deepcopy(c.meta);disk=(c.directory/'character.json').read_bytes()
  def spec(n):return {'part':n,'source_sha256':c.meta['part_layers'][n]['image_id'],'donor_sha256':c.meta['part_layers']['torso']['image_id']}
  plans=[spec('l-upper-leg'),spec('r-upper-leg')];plans[1]['source_sha256']='stale'
  with patch('app.connection_fit.fit_pixels',return_value=(Image.new('RGBA',(100,100),'blue'),{'test':True})),self.assertRaises(UserError):c.prepare_assembly({'revision':c.meta['revision'],'connections':plans})
  self.assertEqual(c.meta,before);self.assertEqual(disk,(c.directory/'character.json').read_bytes())
