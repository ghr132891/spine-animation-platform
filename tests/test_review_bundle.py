import copy,io,json,tempfile,threading,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app.agent_tasks import AgentTasks
from app.core import UserError,png_bytes
from app.review_bundle import prepare,read_file
from app.runtime_cache import RuntimeReports
class FakeSource:
 def __init__(self):
  self.lock=threading.RLock();self.meta={'revision':1};self.state={'parts':[],'skeleton':{'bones':[{'name':'head'},{'name':'l-upper-leg'}],'animations':{'idle':{},'walk':{}}}}
 def export(self):
  f=io.BytesIO()
  with zipfile.ZipFile(f,'w') as z:z.writestr('skeleton.json',json.dumps(self.state['skeleton']))
  return f.getvalue()
class ReviewBundleTests(unittest.TestCase):
 def test_persistent_snapshot_and_revision_invalidation(self):
  with tempfile.TemporaryDirectory() as folder,patch('app.review_bundle.renderer_signature',return_value='renderer'),patch('app.review_bundle.bundles',RuntimeReports()):
   source=FakeSource();a=object.__new__(AgentTasks);a.root=Path(folder);a.loaders={'rig':lambda id:source};calls=[]
   def render(content,clip,count,closeup=None):
    calls.append((clip,count,closeup));return ({'closeup':{'frames':count,'anchor':closeup,'isolated':False}} if closeup else {'passed':True}),png_bytes(Image.new('RGBA',(2,2)))
   a.runtime_check=render;a.contact_check=lambda *args:{'status':'assessed'}
   first=prepare(a,'rig','id',{});self.assertEqual(len(calls),8);self.assertIn(('walk',32,'l-upper-leg'),calls);self.assertEqual(first['visual_verdict'],'not_reviewed')
   with patch('app.review_bundle.bundles',RuntimeReports()):
    self.assertEqual(prepare(a,'rig','id',{})['bundle_id'],first['bundle_id']);self.assertEqual(len(calls),8)
   source.state['skeleton']['bones'].append({'name':'torso'})
   self.assertNotEqual(prepare(a,'rig','id',{})['bundle_id'],first['bundle_id']);self.assertEqual(len(calls),18)
   with self.assertRaises(UserError):read_file(Path(folder),'../x','p0.png')
   with self.assertRaises(UserError):prepare(a,'rig','id',{'animations':['missing']})
 def test_preparation_alone_cannot_attest(self):
  a=object.__new__(AgentTasks)
  with self.assertRaises(UserError):a.record_closeup_review({},dict(kind='rig',id='id',animation='walk',verdict='ready',reason='x'),'digest',{})
 def test_batch_reviews_atomic_on_invalid_entry(self):
  a=object.__new__(AgentTasks);a.tool_lock=threading.RLock();s=FakeSource();a.loaders={'rig':lambda id:s}
  from app.agent_tasks import resource_digest
  digest=resource_digest(s.export());a.own=lambda *args:None
  task={'state':'running','calls':0,'closeup_checks':{'rig:id':{'sha256':digest,'regions':{'idle':{'head':{'report':{'frames':8}}}}}}}
  saved=[];a.read=lambda id:copy.deepcopy(task);a.save_tool_state=lambda t:saved.append(copy.deepcopy(t))
  entries=[{'animation':'idle','anchor':'head','verdict':'ready','reason':'viewed'},{'animation':'walk','anchor':'head','verdict':'ready','reason':'not viewed'}]
  with self.assertRaises(UserError):a.tool('task','spine_review_regions',{'kind':'rig','id':'id','reviews':entries})
  self.assertTrue(all('review' not in t['closeup_checks']['rig:id']['regions']['idle']['head'] for t in saved))
