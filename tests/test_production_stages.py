import copy,json,tempfile,threading,time,unittest,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock,patch
from PIL import Image
from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import UserError,png_bytes,sha
from app.image_evidence import select
from app.stages import run_stage,retry_stage
from app.repair_pipeline import repair
from tests import test_assembly_pipeline as fixtures
ROOT=Path(__file__).resolve().parents[1]
class ProductionStagesTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.storage=Path(self.tmp.name)
  self.agent=AgentTasks(self.storage,ROOT,{'character':lambda id:CharacterProject(self.storage/'characters'/id)},None)
 def task(self):return self.agent.create({'nonce':uuid.uuid4().hex,'requirements':'isolated production-stage test','max_generations':0})['id']
 def test_checkpoint_failure_then_only_same_stage_retries(self):
  tid=self.task();fn=Mock(return_value={'file':'test'})
  run_stage(self.agent,tid,'prepare','one',fn,valid=lambda r:True);run_stage(self.agent,tid,'prepare','one',fn,valid=lambda r:True);self.assertEqual(fn.call_count,1)
  with self.assertRaises(UserError):run_stage(self.agent,tid,'render','two',lambda:(_ for _ in ()).throw(UserError('renderer offline')))
  c=self.agent.read(tid)['stage_checkpoints'];self.assertEqual(c['prepare']['status'],'completed');self.assertEqual(c['render']['status'],'failed')
  run_stage(self.agent,tid,'render','two',lambda:{'ready':True});c=self.agent.read(tid)['stage_checkpoints'];self.assertEqual(c['prepare']['attempts'],1);self.assertEqual(c['render']['attempts'],2);self.assertGreaterEqual(c['render']['duration_seconds'],0)
 def test_large_image_results_are_not_persisted_as_task_checkpoints(self):
  tid=self.task();r=run_stage(self.agent,tid,'tool:spine_image','x',lambda:{'data':'x'*100000});self.assertEqual(len(r['data']),100000);self.assertIsNone(self.agent.read(tid)['stage_checkpoints']['tool:spine_image']['result'])
 def test_two_tasks_tools_can_overlap_but_same_task_remains_serial(self):
  a,b=self.task(),self.task();barrier=threading.Barrier(2)
  with patch.object(self.agent,'_tool',side_effect=lambda *args:(barrier.wait(timeout=3),{'ok':True})[1]):
   with ThreadPoolExecutor(2) as pool:r=list(pool.map(lambda tid:self.agent.tool(tid,'test',{}),(a,b)))
  self.assertEqual(len(r),2)
  entered=threading.Event();release=threading.Event();second=threading.Event()
  def slow(tid,name,args):
   if name=='first':entered.set();release.wait(3)
   else:second.set()
   return {}
  with patch.object(self.agent,'_tool',side_effect=slow):
   with ThreadPoolExecutor(2) as pool:
    first=pool.submit(self.agent.tool,a,'first',{});self.assertTrue(entered.wait(2));last=pool.submit(self.agent.tool,a,'second',{});self.assertFalse(second.wait(.05));release.set();first.result();last.result()
 def test_bounded_scheduler_starts_two_tasks_without_single_global_process(self):
  ids=[self.task() for _ in range(3)];lock=threading.Lock();active=set();peak=[];two=threading.Event();release=threading.Event()
  def execute(tid):
   self.agent.update(tid,state='running')
   with lock:active.add(tid);peak.append(len(active));two.set() if len(active)==2 else None
   release.wait(3)
   with lock:active.remove(tid)
   self.agent.update(tid,state='completed')
  with patch.dict('os.environ',{'SPINE_AGENT_CONCURRENCY':'2'}),patch.object(self.agent,'execute',side_effect=execute),patch.object(self.agent,'recover_references'):
   thread=threading.Thread(target=self.agent.run);thread.start();self.assertTrue(two.wait(3));self.assertEqual(max(peak),2);self.agent.stop.set();release.set();self.agent.wake.set();thread.join(4);self.assertFalse(thread.is_alive())
 def test_cancel_and_reference_failure_target_only_matching_process(self):
  a,b=self.task(),self.task();pa,pb=Mock(),Mock();self.agent.processes.update({a:pa,b:pb});self.agent.cancel(a);pa.terminate.assert_called_once();pb.terminate.assert_not_called()
 def test_retry_cannot_generate_or_restart_model(self):
  tid=self.task()
  for name in ('generate','model-session','vision'):
   with self.assertRaises(UserError):retry_stage(self.agent,tid,name)
class ResolutionTests(unittest.TestCase):
 def test_each_spread_pose_retains_native_pixels(self):
  im=Image.new('RGB',(4480,1200));im.putpixel((896*3+50,77),(255,1,2));result,meta=select(im,{'pose':3},'prebind-spread')
  self.assertEqual(result.size,(896,1200));self.assertEqual(result.getpixel((50,77)),(255,1,2));self.assertEqual(meta['pose_box'],[2688,0,3584,1200])
 def test_roi_zoom_preserves_a_single_pixel_defect(self):
  im=Image.new('RGB',(4480,1200));im.putpixel((900,55),(255,255,255));result,_=select(im,{'pose':1,'crop':[0,50,10,60],'zoom':4},'prebind-spread');self.assertEqual(result.size,(40,40));self.assertEqual(result.getpixel((16,20)),(255,255,255))
 def test_invalid_region_and_excessive_size_are_rejected(self):
  im=Image.new('RGBA',(4480,1200))
  for args in ({'pose':5},{'crop':[-1,0,2,2]},{'resolution':'original','zoom':4}):
   with self.assertRaises(UserError):select(im,args,'prebind-spread')
class RepairBatchTests(unittest.TestCase):
 def setUp(self):
  f=fixtures.AssemblyExecutionTests();f.setUp();self.addCleanup(f.doCleanups);self.c=f.c
 def test_restore_only_same_part_recorded_pixels_and_rollback_bad_followup(self):
  c=self.c;n='l-upper-leg';r=c.meta['part_layers'][n];source=r['image_id'];folder=c.directory/'part-images';im=Image.open(folder/(source+'.png')).convert('RGBA');im.putpixel((2,2),(1,2,3,255));blob=png_bytes(im);current=sha(blob);(folder/(current+'.png')).write_bytes(blob)
  m=copy.deepcopy(c.meta);m['part_layers'][n]['image_id']=current;m['part_finish_history']=[[{'part':n,'before':source,'after':current}]];c.save(m)
  edit={'part':n,'source_sha256':source,'expected_sha256':current,'box':[0,0,4,4],'reason':'unit fixture bad erase restoration'}
  before=(c.directory/'character.json').read_bytes()
  with self.assertRaises(UserError):repair(c,{'revision':c.meta['revision'],'restore_parts':[edit],'assembly_plan':{'overlays':[{'bad':True}]}})
  self.assertEqual(before,(c.directory/'character.json').read_bytes())
  repair(c,{'revision':c.meta['revision'],'restore_parts':[edit]});self.assertEqual(c.meta['part_layers'][n]['image_id'],source)

 def test_batched_assembly_rebuilds_prebind_only_once(self):
  from app.prebind import cached_evidence
  c=self.c
  with patch('app.prebind.cached_evidence',wraps=cached_evidence) as render:
   repair(c,{'revision':c.meta['revision'],'assembly_plan':{'overlays':[]}})
  self.assertEqual(render.call_count,1)
