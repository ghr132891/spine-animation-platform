import unittest
from app.prebind import require,validate_review,CHECKS
from app.core import UserError
class PrebindGateTests(unittest.TestCase):
 def setUp(self):
  self.report={'digest':'current','missing_overlap':[]}
  self.review={'digest':'current','checks':dict.fromkeys(CHECKS,True),'verdict':'ready','reason':'Viewed all three panels.'}
 def test_current_pass_and_changed_resources(self):
  require(self.report,self.review)
  with self.assertRaises(UserError):require(dict(self.report,digest='changed'),self.review)
 def test_no_draft_bypass_or_partial_acceptance(self):
  for r in [None,dict(self.review,verdict='needs_repair'),dict(self.review,checks=dict(self.review['checks'],complete_parts=False))]:
   with self.assertRaises(UserError):require(self.report,r)
 def test_missing_joint_blocks_claimed_pass(self):
  with self.assertRaises(UserError):require(dict(self.report,missing_overlap=['l-upper-arm']),self.review)

 def test_missing_or_partial_assets_block_pass(self):
  for field in ('missing_parts','incomplete_parts'):
   with self.assertRaises(UserError):require(dict(self.report,**{field:['r-upper-arm']}),self.review)
 def test_character_build_draft_cannot_skip_gate(self):
  import tempfile,uuid
  from pathlib import Path
  from PIL import Image
  from app.character import CharacterProject
  from app.core import png_bytes
  with tempfile.TemporaryDirectory() as d:
   c=CharacterProject.create(Path(d),'gate');im=Image.new('RGBA',(160,240));im.paste((70,110,140,255),(20,20,140,220));c.set_image(png_bytes(im),0)
   m=dict(c.meta);m['prebind_contract']='complete-assets-v1';c.save(m)
   data={'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':c.meta['landmarks'],'settings':c.meta['settings'],'draft':True}
   with self.assertRaisesRegex(UserError,'先验收'):c.build(Path(d),data)
   self.assertEqual(c.meta['versions'],[])
