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
 def test_default_prepare_removes_only_previously_confirmed_same_foreground(self):
  c=self.c;overlay=copy.deepcopy(c.meta['assembly_overlays']);before=copy.deepcopy(c.meta['part_layers'])
  c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[self.rule()]})
  self.assertEqual(len(c.meta['accessory_occlusion_rules']),1)
  c.prepare_assembly({'revision':c.meta['revision'],'overlays':overlay})
  self.assertEqual(c.meta['assembly_overlays'],[]);self.assertEqual(c.meta['part_layers'],before);self.assertEqual(c.meta['accessory_preparation']['automatically_repaired'],['strap'])
  history=len(c.meta['accessory_auto_repairs']);c.prepare_assembly({'revision':c.meta['revision']});self.assertEqual(len(c.meta['accessory_auto_repairs']),history)
 def test_new_polygon_is_reported_and_not_deleted(self):
  c=self.c;overlay=copy.deepcopy(c.meta['assembly_overlays']);c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[self.rule()]})
  overlay[0]['polygon']=[[0,0],[.8,0],[.8,1],[0,1]]
  c.prepare_assembly({'revision':c.meta['revision'],'overlays':overlay})
  self.assertEqual(c.meta['assembly_overlays'],overlay);self.assertEqual(c.meta['accessory_preparation']['status'],'needs_visual_review')
 def test_changed_arm_pixels_do_not_reuse_old_approval(self):
  from app.core import read_image
  c=self.c;overlay=copy.deepcopy(c.meta['assembly_overlays']);c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[self.rule()]})
  m=copy.deepcopy(c.meta);r=m['part_layers']['l-upper-arm'];folder=c.directory/'part-images';im=read_image((folder/(r['image_id']+'.png')).read_bytes());im.putpixel((3,3),(30,40,50,255));blob=png_bytes(im);r['image_id']=sha(blob);(folder/(r['image_id']+'.png')).write_bytes(blob);c.save(m)
  c.prepare_assembly({'revision':c.meta['revision'],'overlays':overlay})
  self.assertEqual(c.meta['assembly_overlays'],overlay);self.assertEqual(c.meta['accessory_preparation']['status'],'needs_visual_review')
 def test_complete_import_executes_saved_rule_without_manual_repair(self):
  from tests.test_asset_sheet_workflow import asset_fixture
  c=self.c;overlay=copy.deepcopy(c.meta['assembly_overlays']);c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[self.rule()]})
  m=copy.deepcopy(c.meta);m['assembly_overlays']=overlay;c.save(m)
  im,layout=asset_fixture();c.apply_source_layout({'revision':c.meta['revision'],'parts':[x for x in layout if x['name']=='head']})
  self.assertEqual(c.meta['assembly_overlays'],[]);self.assertEqual(c.meta['accessory_preparation']['automatically_repaired'],['strap'])
 def test_task_start_preparation_repairs_reintroduced_overlay_without_model(self):
  import uuid
  from pathlib import Path
  from app.agent_tasks import AgentTasks
  from app.character import CharacterProject
  from app.generation_flow import prepare
  c=self.c;overlay=copy.deepcopy(c.meta['assembly_overlays']);c.prepare_assembly({'revision':c.meta['revision'],'accessory_occlusion':[self.rule()]})
  m=copy.deepcopy(c.meta);m['assembly_overlays']=overlay;c.save(m)
  storage=c.directory.parent.parent;agent=AgentTasks(storage,Path(__file__).resolve().parents[1],{'character':lambda id:CharacterProject(storage/'characters'/id)},None)
  t=agent.create({'nonce':uuid.uuid4().hex,'requirements':'复用已确认配件归属的完整素材继续检查','max_generations':0,'image_preparation':'preserve','sources':[{'kind':'character','id':c.id}]})
  cid=t['sources'][0]['id'];record=prepare(agent,t['id'],cid);result=agent.loaders['character'](cid)
  self.assertEqual(result.meta['assembly_overlays'],[]);self.assertEqual(record['accessory_preparation']['status'],'no_duplicate_foreground');self.assertEqual(agent.read(t['id'])['calls'],0)
  self.assertEqual(c.meta['assembly_overlays'],overlay) # source remains untouched
