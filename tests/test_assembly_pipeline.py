import copy,tempfile,unittest
from pathlib import Path
from app.character import CharacterProject,make_skeleton
from app.core import png_bytes,sha,UserError
from app.assembly_pipeline import overlay_parts
from tests.test_asset_sheet_workflow import asset_fixture,assembly_guide

class AssemblyExecutionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.c=CharacterProject.create(self.root,'test');image,layout=asset_fixture();self.c.set_image(png_bytes(image),0)
  self.c.update({'revision':self.c.meta['revision'],'landmarks':assembly_guide(),'settings':self.c.meta['settings']})
  self.c.apply_source_layout({'revision':self.c.meta['revision'],'parts':layout})
 def test_import_executes_registration_and_guide_reregisters(self):
  c=self.c
  self.assertEqual(c.meta['canvas_contract'],'canvas-layers-v1')
  self.assertTrue(all('canvas_offset' in x for x in c.meta['part_layers'].values()))
  self.assertTrue(c.meta['assembly_inputs'])
  before={p['name']:p['box'] for p in c.parts()}
  guide=copy.deepcopy(c.meta['landmarks']);guide['l-hand'][0]+=.02
  c.update({'revision':c.meta['revision'],'landmarks':guide,'settings':c.meta['settings']})
  after={p['name']:p['box'] for p in c.parts()}
  self.assertNotEqual(before['l-upper-arm'],after['l-upper-arm'])
  self.assertEqual(before['head'],after['head'])
 def test_preview_and_export_share_overlay_geometry_and_order(self):
  c=self.c;p=next(p for p in c.parts() if p['name']=='torso')
  spec={'name':'front','source':'torso','subject':'head','source_sha256':sha(png_bytes(p['image'])),'polygon':[[0,0],[1,0],[1,.3],[0,.3]],'reason':'front collar over neck'}
  # Prime caches before mutation to exercise overlay invalidation.
  c.parts();c.review()
  c.prepare_assembly({'revision':c.meta['revision'],'overlays':[spec]})
  parts=c.parts();order=c.review_order([p['name'] for p in parts]);self.assertLess(order.index('torso'),order.index('head'));self.assertEqual(order.index('front'),order.index('head')+1)
  c.review();report,images=c.prebind_evidence();self.assertEqual(report['part_count'],8)
  s=make_skeleton(c.image(),c.meta['landmarks'],parts,c.meta['settings'],{},order)
  at=s['skins'][0]['attachments'];front=copy.deepcopy(at['front']['front']);front.pop('path',None);base=copy.deepcopy(at['torso']['torso']);base.pop('path',None)
  self.assertEqual(front,base);self.assertEqual([x['name'] for x in s['slots']],order)
  with self.assertRaises(UserError):overlay_parts(parts[:-1],[dict(spec,source_sha256='stale')])
  c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'torso':{'dx':4,'dy':2}},'draw_order':order}})
  moved={p['name']:p for p in c.parts()};self.assertEqual(moved['torso']['review_transform'],moved['front']['review_transform'])
  self.assertEqual(moved['torso']['box'],moved['front']['box']);self.assertEqual(moved['torso']['image'].size,moved['front']['image'].size)
  c.review()  # Base review must remain readable after a shared adjustment.
 def test_reuse_records_real_source_and_preserves_hidden_alpha(self):
  c=self.c;p=next(p for p in c.parts() if p['name']=='l-upper-arm');r=c.meta['part_layers'][p['name']];xy=r['canvas_offset'];pins={k:[v[0]-xy[0],v[1]-xy[1]] for k,v in r['canvas_pins'].items()}
  item={'name':p['name'],'part':p['name'],'character_id':c.id,'source_pair':[pins['shoulder'],pins['hand']],'target_pair':[r['canvas_pins']['shoulder'],r['canvas_pins']['hand']],'pins':pins}
  c.prepare_assembly({'revision':c.meta['revision'],'replacements':[item]})
  record=c.meta['part_layers'][p['name']];self.assertTrue(record['complete']);self.assertFalse(record['completion_source']['registration']['alpha_clipped_to_previous_layer'])
  self.assertEqual(record['completion_source']['sha256'],r['image_id'])
  self.assertTrue(record['canvas_pins']);self.assertNotIn('prebind_review',c.meta)

 def collar(self):
  c=self.c;p=next(p for p in c.base_parts() if p['name']=='torso')
  spec={'name':'front','source':'torso','subject':'head','source_sha256':sha(png_bytes(p['image'])),'polygon':[[0,0],[1,0],[1,.3],[0,.3]],'reason':'front collar'}
  c.prepare_assembly({'revision':c.meta['revision'],'overlays':[spec]});return spec

 def test_automatic_reuse_preserves_collar_and_unrelated_adjustment(self):
  c=self.c;spec=self.collar();order=c.review_order([p['name'] for p in c.parts()])
  c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'head':{'dx':3}},'draw_order':order}})
  before={p['name']:(p['box'],png_bytes(p['image'])) for p in c.parts()}
  rev=c.meta['revision']
  c.prepare_assembly({'revision':rev,'replacements':[{'name':'l-upper-leg','character_id':c.id}]})
  self.assertEqual(c.meta['revision'],rev+1)
  self.assertEqual(c.meta['assembly_overlays'],[spec])
  after={p['name']:(p['box'],png_bytes(p['image'])) for p in c.parts()}
  for n in ('head','torso','front','r-upper-leg'):self.assertEqual(before[n],after[n])
  self.assertEqual(c.meta['part_layers']['l-upper-leg']['completion_source']['anchor_mode'],'landmarks')

 def test_partial_import_retains_unrelated_pixels_order_and_collar(self):
  c=self.c;spec=self.collar();order=c.review_order([p['name'] for p in c.parts()]);order[0],order[1]=order[1],order[0]
  c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'head':{'dx':3}},'draw_order':order}})
  before={p['name']:(p['box'],png_bytes(p['image'])) for p in c.parts()}
  image,layout=asset_fixture();c.apply_source_layout({'revision':c.meta['revision'],'parts':[p for p in layout if p['name']=='l-upper-leg']})
  after={p['name']:(p['box'],png_bytes(p['image'])) for p in c.parts()}
  for n in before:
   if n!='l-upper-leg':self.assertEqual(before[n],after[n],n)
  self.assertEqual(c.meta['assembly_overlays'],[spec]);self.assertEqual(c.review_order(list(after)),order)

 def test_invalid_inline_plan_is_atomic_and_keeps_accepted_metadata(self):
  c=self.c;self.collar();before=copy.deepcopy(c.meta);disk=(c.directory/'character.json').read_bytes()
  image,layout=asset_fixture()
  with self.assertRaises(UserError):c.apply_source_layout({'revision':c.meta['revision'],'parts':[p for p in layout if p['name']=='head'],'assembly_plan':{'overlays':[{'name':'bad'}]}})
  self.assertEqual(c.meta,before);self.assertEqual((c.directory/'character.json').read_bytes(),disk)
  with self.assertRaises(UserError):c.prepare_assembly({'revision':c.meta['revision'],'replacements':[{'name':'l-upper-leg','character_id':c.id}],'overlays':[{'name':'bad'}]})
  self.assertEqual(c.meta,before);self.assertEqual((c.directory/'character.json').read_bytes(),disk)

 def test_changed_overlay_source_is_reported_not_reused(self):
  c=self.c;self.collar();image,layout=asset_fixture();torso=next(p for p in layout if p['name']=='torso');torso['box'][0]+=4
  c.apply_source_layout({'revision':c.meta['revision'],'parts':[torso]})
  self.assertEqual(c.meta['assembly_overlays'],[])
  self.assertEqual(c.meta['assembly_invalidations'][-1]['source'],'torso')
