import unittest
from PIL import Image,ImageDraw
from app.canvas_layers import partition,reconstruction
from app.layer_overlap import inspect,require,CHECKS
from app.core import UserError
class LayerOverlapTests(unittest.TestCase):
 def test_complete_underlayer_survives_visible_partition(self):
  ref=Image.new('RGBA',(30,30),(40,80,120,255));full=ref.copy()
  items=[{'name':'torso','owns':['coat'],'polygon':[[0,0],[30,0],[30,30],[0,30]],'completed_image':full},{'name':'l-upper-arm','owns':['sleeve'],'polygon':[[15,0],[30,0],[30,30],[15,30]]}]
  layers,owners=partition(ref,items,['torso','l-upper-arm'])
  self.assertEqual(layers[0]['image'].size,(30,30))
  parts=[{'name':p['name'],'image':p['image'],'box':[*p['offset'],p['offset'][0]+p['image'].width,p['offset'][1]+p['image'].height],'start':'shoulder'} for p in layers]
  report,_,_=reconstruction(ref,parts,['torso','l-upper-arm'],owners);self.assertTrue(report['numerical_checks_passed'])
  diagnostic,_=inspect(ref,parts,['torso','l-upper-arm'],{'shoulder':[.5,.5]});self.assertFalse(diagnostic['missing_overlap'])
  require(diagnostic,{'digest':diagnostic['digest'],'verdict':'ready','checks':dict.fromkeys(CHECKS,True)})
  parts[1]['box'][0]+=1
  changed,_=inspect(ref,parts,['torso','l-upper-arm'],{'shoulder':[.5,.5]})
  with self.assertRaises(UserError):require(changed,{'digest':diagnostic['digest'],'verdict':'ready','checks':dict.fromkeys(CHECKS,True)})
 def test_flat_partition_cannot_pass_overlap_even_with_true_claims(self):
  ref=Image.new('RGBA',(30,30),(40,80,120,255))
  items=[{'name':'torso','owns':['coat'],'polygon':[[0,0],[30,0],[30,30],[0,30]]},{'name':'l-upper-arm','owns':['sleeve'],'polygon':[[15,0],[30,0],[30,30],[15,30]]}]
  layers,_=partition(ref,items,['torso','l-upper-arm']);parts=[{'name':p['name'],'image':p['image'],'box':[*p['offset'],30,30],'start':'shoulder'} for p in layers]
  report,_=inspect(ref,parts,['torso','l-upper-arm'],{'shoulder':[.5,.5]});self.assertEqual(report['missing_overlap'],['l-upper-arm'])
  with self.assertRaises(UserError):require(report,{'digest':report['digest'],'verdict':'ready','checks':dict.fromkeys(CHECKS,True)})
