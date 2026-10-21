"""Synthetic state-transition fixtures, never production visual approvals."""
import copy,json,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from app.agent_tasks import AgentTasks,resource_digest
from app.character import CharacterProject
from app.core import Project,UserError,png_bytes,sha,atomic_json
from app.rig import RigProject
from app.web_workflows import WebWorkflows
from app.animation_transition import advance,prepare_pending_review,notify,process_events
from app.asset_integrity import ASSEMBLY_CHECKS
from app.orientation_review import ASSEMBLY_CHECKS as ORIENTATION_CHECKS
from app.image_preflight import JOINTS
from tests.test_asset_sheet_workflow import asset_fixture,assembly_guide
ROOT=Path(__file__).resolve().parents[1]

class AnimationTransitionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.storage=Path(self.tmp.name)
  self.loaders={'character':lambda id:CharacterProject(self.storage/'characters'/id),'rig':lambda id:RigProject(self.storage/'rigs'/id),'project':lambda id:Project(self.storage/'projects'/id)}
  self.agent=AgentTasks(self.storage,ROOT,self.loaders,None);self.web=WebWorkflows(self.agent);self.owner,_=self.web.new_session(True)
 def start(self,**kw):
  t=self.agent.create({'nonce':uuid.uuid4().hex,'requirements':'从零生成完整角色待机和自然侧向行走','max_generations':0,'image_preparation':'auto',**kw});self.tid=t['id'];self.cid=t['sources'][0]['id']
  self.assertFalse(t.get('material_animation'));self.assertFalse(self.c().meta['image_id'])
  c=self.c();im,layout=asset_fixture();c.set_image(png_bytes(im),c.meta['revision']);c.update({'revision':c.meta['revision'],'landmarks':assembly_guide(),'settings':c.meta['settings']});c.apply_source_layout({'revision':c.meta['revision'],'parts':layout})
  def api(route,data=None,binary=False):
   c=self.c();action=route.rsplit('/',1)[-1]
   if binary and action=='image':return png_bytes(c.image())
   if binary and action=='assembly':return png_bytes(c.assembly())
   if binary and action.startswith('prebind-'):return png_bytes(c.prebind_evidence()[1][action.split('-',1)[1]])
   if action=='review-prebind':return c.review_prebind(data)
   if action=='review-stress':
    from app.assembly_stress import review
    return review(c,data)
   raise AssertionError(route)
  self.agent.api=api
  from tests.stress_fixture import accept_synthetic_stress
  accept_synthetic_stress(self.c())
  for view in ('image','assembly'):self.agent.tool(self.tid,'spine_image',{'kind':'character','id':self.cid,'view':view})
  self.args={'id':self.cid,'verdict':'ready','source_kind':'character','parts_reviewed':True,'camera_view':'three-quarter-side','direction_consistent':True,'observed_view':'three-quarter side','target_view':'right side','reason':'Synthetic fixture only','target_actions':['idle','walk'],'missing_parts':[],'joint_review':dict.fromkeys(JOINTS,True),'material_checks':dict.fromkeys(ASSEMBLY_CHECKS,True),'orientation_checks':dict.fromkeys(ORIENTATION_CHECKS,True)}
  return t
 def c(self):return self.loaders['character'](self.cid)
 def material(self):
  c=self.c();r,_=c.prebind_evidence()
  for name in ('parts','assembly','spread'):self.agent.tool(self.tid,'spine_image',{'kind':'character','id':self.cid,'view':'prebind-'+name})
  return self.agent.tool(self.tid,'spine_character',{'id':self.cid,'action':'review-prebind','data':{'revision':c.meta['revision'],'digest':r['digest'],'checks':dict.fromkeys(r['checks'],True),'verdict':'ready','reason':'synthetic material fixture'}})
 def view(self):return self.agent.tool(self.tid,'spine_assess_image',self.args)
 def ready(self):self.start();self.material();return self.view()['automatic_animation']
 def test_later_generated_material_enables_after_material_then_view(self):
  self.start();self.material();self.assertFalse(self.agent.read(self.tid).get('automatic_animation'))
  result=self.view();self.assertTrue(result['automatic_animation']['rig_id']);t=self.agent.read(self.tid)
  self.assertTrue(t['material_animation']);self.assertEqual(t['animation_transitions'][self.cid]['phase'],'review_pending');self.assertEqual(t['reservations'],{});self.assertEqual(t['draft_artifacts'][0]['visual_acceptance']['status'],'draft')
 def test_view_first_survives_review_only_revision_then_automatically_builds(self):
  self.start();self.assertEqual(self.view()['animation_transition']['phase'],'awaiting_materials');before=self.c().meta['revision']
  result=self.material();self.assertGreater(self.c().meta['revision'],before);self.assertTrue(result['automatic_animation']['rig_id'])
 def test_default_asset_sheet_waits_for_real_turnaround_evidence(self):
  self.start(image_preparation='asset-sheet');self.material();self.assertEqual(self.view()['animation_transition']['phase'],'awaiting_turnaround')
  from app.turnaround_review import CHECKS
  c=self.c();jid=uuid.uuid4().hex;folder=c.directory/'jobs';folder.mkdir(exist_ok=True);pixels=png_bytes(c.image());(folder/(jid+'.png')).write_bytes(pixels)
  atomic_json(folder/(jid+'.json'),{'sheet':'turnaround','source_image_id':c.meta['image_id'],'candidate_ready':True})
  self.agent.update(self.tid,candidate_checks={self.cid+':'+jid:c.meta['image_id']},candidate_pixel_checks={self.cid+':'+jid:sha(pixels)})
  for phase in ('reference','assembly'):result=self.agent.tool(self.tid,'spine_review_turnaround',{'id':self.cid,'job_id':jid,'phase':phase,'checks':dict.fromkeys(CHECKS,True),'verdict':'ready','reason':'synthetic turnaround fixture'})
  self.assertEqual(result['animation_transition']['phase'],'review_pending');rid=result['animation_transition']['rig_id'];self.assertEqual(self.loaders['rig'](rid).meta['turnaround_reference']['job_id'],jid)
 def test_changed_pixels_invalidate_old_view_even_if_prebind_is_ready_again(self):
  self.start();self.view();c=self.c();c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'head':{'dx':2}},'draw_order':c.review_order([p['name'] for p in c.parts()])}})
  result=self.material();self.assertEqual(result['animation_transition']['phase'],'awaiting_view');self.assertFalse(self.agent.read(self.tid).get('automatic_animation'))
 def test_material_only_and_explicit_opt_out_never_bind(self):
  for kwargs in ({'output_scope':'materials'},{'_material_animation':False}):
   with self.subTest(kwargs=kwargs):self.start(**kwargs);self.material();self.view();self.assertFalse(self.c().meta['versions'])
 def test_repeated_and_concurrent_triggers_reuse_one_rig(self):
  built=self.ready()
  with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:advance(self.agent,self.tid,self.cid),range(2)))
  self.assertEqual({r['rig_id'] for r in results},{built['rig_id']});self.assertEqual(len(self.c().meta['versions']),1);self.assertEqual(len(self.agent.read(self.tid)['automatic_build_attempts']),1)
 def test_failed_delivery_recovers_saved_build_without_new_attempt(self):
  self.start();self.material()
  with patch.object(self.agent,'publish_draft',side_effect=UserError('temporary delivery failure')):self.view()
  self.assertEqual(len(self.c().meta['versions']),1);rig=self.c().meta['versions'][0]['rig_id'];self.assertEqual(self.agent.read(self.tid)['animation_transitions'][self.cid]['phase'],'retryable_error')
  result=advance(self.agent,self.tid,self.cid,recover=True);self.assertEqual(result['rig_id'],rig);self.assertEqual(result['phase'],'review_pending');self.assertEqual(len(self.c().meta['versions']),1)
 def test_review_queue_prepares_without_marking_visual_acceptance(self):
  built=self.ready();rig=self.loaders['rig'](built['rig_id']);bundle={'bundle_id':'a'*64,'resource_sha256':resource_digest(rig.export()),'panels':[{'key':'p0'},{'key':'p1'}]}
  # A new AgentTasks instance simulates the platform restarting; task files persist.
  restarted=AgentTasks(self.storage,ROOT,self.loaders,None)
  with patch.object(restarted,'review_bundle',return_value=bundle) as review:result=prepare_pending_review(restarted,self.tid,self.cid)
  review.assert_called_once();self.assertEqual(result['phase'],'review_ready');self.assertEqual(result['visual_verdict'],'not_reviewed');self.assertNotIn('closeup_checks',restarted.read(self.tid))
  public=self.web.public(restarted.read(self.tid));self.assertEqual(public['animation_transitions'][0]['phase'],'review_ready')
 def test_model_tool_save_cannot_rewind_background_review_state(self):
  self.ready();stale=self.agent.read(self.tid)
  from app.animation_transition import save
  save(self.agent,self.tid,self.cid,phase='review_ready',bundle_id='a'*64,panel_count=22)
  stale['stage']='spine_context';self.agent.save_tool_state(stale)
  self.assertEqual(self.agent.read(self.tid)['animation_transitions'][self.cid]['phase'],'review_ready')
 def test_review_failure_keeps_draft_and_retries_same_rig(self):
  built=self.ready()
  with patch.object(self.agent,'review_bundle',side_effect=UserError('renderer temporarily unavailable')):result=prepare_pending_review(self.agent,self.tid,self.cid)
  self.assertEqual(result['phase'],'review_pending');self.assertIn('renderer',result['error']);self.assertEqual(self.agent.read(self.tid)['draft_artifacts'][0]['id'],built['rig_id']);self.assertEqual(len(self.c().meta['versions']),1)
 def test_cancelled_or_changed_rig_cannot_publish_stale_bundle(self):
  built=self.ready();rig=self.loaders['rig'](built['rig_id']);state=copy.deepcopy(rig.state);state['skeleton']['bones'][0]['x']=2;rig.commit(state,'change')
  with patch.object(self.agent,'review_bundle') as review:r=prepare_pending_review(self.agent,self.tid,self.cid)
  review.assert_not_called();self.assertEqual(r['phase'],'superseded')
  self.agent.update(self.tid,state='cancelled');self.assertIsNone(advance(self.agent,self.tid,self.cid))
 def test_http_notification_does_not_wait_on_model_tool_lock(self):
  self.start()
  with ThreadPoolExecutor(max_workers=1) as pool:
   with self.agent.tool_lock:pool.submit(notify,self.agent,self.c()).result(timeout=2)
  self.assertTrue((self.storage/'animation-events'/(self.cid+'.json')).exists())
 def test_web_character_event_uses_same_transition_without_model_call(self):
  self.start();self.view();c=self.c();r,_=c.prebind_evidence();c.review_prebind({'revision':c.meta['revision'],'digest':r['digest'],'checks':dict.fromkeys(r['checks'],True),'verdict':'ready','reason':'synthetic HTTP material review'})
  notify(self.agent,c);process_events(self.agent)
  self.assertEqual(self.agent.read(self.tid)['animation_transitions'][self.cid]['phase'],'review_pending');self.assertEqual(len(self.c().meta['versions']),1);self.assertFalse(list((self.storage/'animation-events').glob('*.json')))

 def test_static_and_view_approval_wait_for_pressure_then_resume_after_reading_all_panels(self):
  from app.assembly_stress import CHECKS,read_manifest
  self.start();c=self.c();meta=copy.deepcopy(c.meta);meta.pop('stress_review',None);c.save(meta)
  # Saving an acceptance record only does not change the assembled pixels.
  self.material();result=self.view();self.assertEqual(result['animation_transition']['phase'],'awaiting_stress_review');self.assertFalse(self.c().meta['versions'])
  r=read_manifest(self.c());data={'revision':self.c().meta['revision'],'digest':r['digest'],'checks':dict.fromkeys(CHECKS,True),'verdict':'ready','reason':'Synthetic runtime fixture'}
  with self.assertRaisesRegex(UserError,'逐张'):self.agent.tool(self.tid,'spine_character',{'id':self.cid,'action':'review-stress','data':data})
  for panel in r['panels']:self.agent.tool(self.tid,'spine_image',{'kind':'character','id':self.cid,'view':'stress-image','part':panel['key']})
  result=self.agent.tool(self.tid,'spine_character',{'id':self.cid,'action':'review-stress','data':data})
  self.assertEqual(result['animation_transition']['phase'],'review_pending');self.assertEqual(len(self.c().meta['versions']),1)

 def test_failed_pressure_never_consumes_binding_budget(self):
  from app.assembly_stress import CHECKS,read_manifest,review
  self.start();c=self.c();r=read_manifest(c);checks=dict.fromkeys(CHECKS,True);checks['hip_contour']=False
  review(c,{'revision':c.meta['revision'],'digest':r['digest'],'checks':checks,'verdict':'needs_repair','reason':'Visible hip spike under alternating pose'})
  self.material();result=self.view();self.assertEqual(result['animation_transition']['phase'],'stress_needs_repair');self.assertFalse(self.c().meta['versions']);self.assertFalse(self.agent.read(self.tid).get('automatic_build_attempts'))

 def test_missing_pressure_is_queued_and_preparation_never_creates_production_rig(self):
  from app.animation_transition import prepare_stress
  self.start();self.material()
  with patch('app.assembly_stress.require',side_effect=UserError('missing')),patch('app.assembly_stress.read_manifest',side_effect=UserError('missing')):result=self.view()
  self.assertEqual(result['animation_transition']['phase'],'stress_pending');self.assertFalse(self.c().meta['versions'])
  with patch('app.assembly_stress.prepare',return_value={'digest':'b'*64,'panels':[{'key':'p0'}]}):prepare_stress(self.agent,self.tid,self.cid)
  self.assertEqual(self.agent.read(self.tid)['animation_transitions'][self.cid]['phase'],'awaiting_stress_review');self.assertFalse(self.c().meta['versions']);self.assertFalse(self.agent.read(self.tid).get('automatic_build_attempts'))

 def test_cancelled_pressure_queue_does_not_render(self):
  from app.animation_transition import prepare_stress,save
  self.start();save(self.agent,self.tid,self.cid,phase='stress_pending');self.agent.update(self.tid,state='cancelled')
  with patch('app.assembly_stress.prepare') as prepare:prepare_stress(self.agent,self.tid,self.cid)
  prepare.assert_not_called()
