import copy,unittest
from tests import test_assembly_pipeline as fixtures
from app.core import UserError,png_bytes,sha
class AccessoryOcclusionTests(unittest.TestCase):
 def setUp(self):
  f=fixtures.AssemblyExecutionTests();f.setUp();self.addCleanup(f.doCleanups);self.c=f.c
  # Fixture torso is explicitly a complete costume for this ownership test.
  m=copy.deepcopy(self.c.meta);m['part_layers']['torso']['group']='costume';self.c.save(m)
  p=next(p for p in self.c.base_parts() if p['name']=='torso');self.digest=sha(png_bytes(p['image']))
  self.c.prepare_assembly({'revision':self.c.meta['revision'],'overlays':[{'name':'strap','source':'torso','subject':'l-upper-arm','source_sha256':self.digest,'polygon':[[0,0],[1,0],[1,1],[0,1]],'reason':'synthetic fixed strap'}]})
 def rule(self):return {'mode':'fixed-to-costume','source':'torso','near_part':'l-upper-arm','source_sha256':self.digest,'remove_foregrounds':['strap'],'reason':'visible duplicate; real strap retained on costume'}
 def test_only_duplicate_overlay_removed_all_pixels_and_pins_preserved(self):
  c=self.c;before=copy.deepcopy(c.meta['part_layers']);lm=copy.deepcopy(c.meta['landmarks']);c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[self.rule()]})
  self.assertEqual(c.meta['part_layers'],before);self.assertEqual(c.meta['landmarks'],lm);self.assertEqual(c.meta['assembly_overlays'],[]);self.assertEqual(len(c.meta['accessory_occlusion_history']),1)
 def test_stale_or_foreign_overlay_rejected_atomically(self):
  c=self.c;before=(c.directory/'character.json').read_bytes()
  for rule in ({**self.rule(),'source_sha256':'0'*64},{**self.rule(),'near_part':'r-upper-arm'},{**self.rule(),'remove_foregrounds':['head']}):
   with self.assertRaises(UserError):c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[rule]})
   self.assertEqual(before,(c.directory/'character.json').read_bytes())
 def test_new_overlay_cannot_reintroduce_duplicate_in_same_transaction(self):
  with self.assertRaises(UserError):self.c.prepare_assembly({'revision':self.c.meta['revision'],'accessory_occlusion':[self.rule()],'overlays':[]})
