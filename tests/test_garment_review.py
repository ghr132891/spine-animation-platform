import copy, unittest
from app.asset_integrity import GARMENT_CHECKS, garment_anchors, garment_frame_count, checked_garment_review, require_garment_reviews
from app.core import UserError

class GarmentTests(unittest.TestCase):
 def setUp(self):
  self.skeleton={'skeleton':{'garment_motion':{'version':'long-hem-v1'}},'bones':[{'name':s+'-upper-leg'} for s in ('l','r')]}
  self.record={'sha256':'current','regions':{clip:{anchor:{'report':{'frames':count,'isolated':False},'review':{'verdict':'ready','garment_checks':dict.fromkeys(GARMENT_CHECKS,True)}} for anchor in garment_anchors(self.skeleton)} for clip,count in [('idle',8),('walk',32)]}}
 def check(self):require_garment_reviews(self.record,'current',['idle','walk'],self.skeleton)
 def test_full_composite_cycle_and_explicit_findings_required(self):
  self.check()
  for field in GARMENT_CHECKS:
   bad=dict.fromkeys(GARMENT_CHECKS,True);bad[field]=False
   with self.assertRaises(UserError):checked_garment_review({'verdict':'ready','garment_checks':bad})
   self.assertEqual(checked_garment_review({'verdict':'needs_repair','garment_checks':bad}),bad)
  original=copy.deepcopy(self.record)
  for change in ({'frames':8},{'isolated':True}):
   self.record=copy.deepcopy(original);self.record['regions']['walk']['l-upper-leg']['report'].update(change)
   with self.assertRaises(UserError):self.check()
  self.record=copy.deepcopy(original);self.record['sha256']='old'
  with self.assertRaises(UserError):self.check()
 def test_generic_ready_or_truthy_values_cannot_pass(self):
  item=self.record['regions']['walk']['l-upper-leg']['review'];item.pop('garment_checks')
  with self.assertRaises(UserError):self.check()
  item['garment_checks']=dict.fromkeys(GARMENT_CHECKS,1)
  with self.assertRaises(UserError):self.check()
 def test_scope_and_custom_locomotion_names(self):
  self.assertEqual(garment_anchors({'bones':[{'name':'l-upper-leg'}]}),[])
  require_garment_reviews({},'new',['walk'],{'bones':[]})
  for clip in ('walk_loop','Walk','run-fast','行走'):self.assertEqual(garment_frame_count(clip),32)
  self.assertEqual(garment_frame_count('idle'),8)
