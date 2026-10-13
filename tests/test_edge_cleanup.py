import unittest
import numpy as np
from PIL import Image, ImageDraw
from app.core import UserError
from app.material_finish import finish_material,inspect_alpha
WHOLE=[[0,0],[1,0],[1,1],[0,1]]
class EdgeCleanupTests(unittest.TestCase):
 def test_specks_respect_full_connectivity_and_selected_region(self):
  im=Image.new('RGBA',(40,40));d=ImageDraw.Draw(im);d.rectangle((10,10,25,25),fill='blue');d.line((25,25,32,32),fill='blue');im.putpixel((5,5),(20,30,40,255));im.putpixel((38,38),(20,30,40,255))
  a=np.array(im);out,report=finish_material(im,{'reason':'已确认孤点','operations':[{'kind':'remove-specks','max_pixels':4,'polygon':[[0,0],[.9,0],[.9,.9],[0,.9]]}]});b=np.array(out)
  self.assertEqual(b[5,5,3],0);self.assertEqual(b[38,38,3],255);self.assertEqual(b[32,32,3],255);self.assertEqual(report['changed_pixels'],1);np.testing.assert_array_equal(a[:,:,:3],b[:,:,:3])
 def test_trim_leaves_protected_sole_and_interior_identical(self):
  im=Image.new('RGBA',(40,80));ImageDraw.Draw(im).rectangle((10,5,25,79),fill='blue');a=np.array(im)
  out,_=finish_material(im,{'reason':'确认阶梯边缘','operations':[{'kind':'edge-trim','pixels':.5,'polygon':[[0,0],[1,0],[1,.6],[0,.6]]}]});b=np.array(out)
  self.assertLess(b[20,10,3],255);self.assertEqual(b[20,15,3],255);np.testing.assert_array_equal(a[60:],b[60:]);self.assertTrue((b[:,:,3]<=a[:,:,3]).all())
  with self.assertRaises(UserError):finish_material(im,{'reason':'test','operations':[{'kind':'edge-trim','pixels':2,'polygon':WHOLE}]})
 def test_diagnostic_is_candidate_not_pass(self):
  im=Image.new('RGBA',(10,10));im.putpixel((4,3),(0,0,0,100));r=inspect_alpha(im)
  self.assertEqual(r['semantic_verdict'],'requires_review');self.assertEqual(r['small_components'][0]['box'],[4,3,5,4])

class AlphaDustTests(unittest.TestCase):
 def test_only_weak_detached_islands_are_automatic(self):
  from app.material_finish import clean_alpha_dust
  im=Image.new('RGBA',(20,20));d=ImageDraw.Draw(im);d.rectangle((5,5,15,15),fill='blue')
  for pos,alpha in [((2,2),3),((4,5),3),((18,18),80)]:im.putpixel(pos,(20,30,40,alpha))
  a=np.array(im);out,r=clean_alpha_dust(im);b=np.array(out)
  self.assertEqual(b[2,2,3],0);self.assertEqual(b[5,4,3],3);self.assertEqual(b[18,18,3],80);self.assertEqual(r['removed_pixels'],1)
  np.testing.assert_array_equal(a[:,:,:3],b[:,:,:3])
