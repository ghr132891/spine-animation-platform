import unittest
from app.runtime_cache import RuntimeReports

class CacheTests(unittest.TestCase):
 def test_isolated_objects_invalidation_and_failure(self):
  c=RuntimeReports();calls=[]
  def compute():calls.append(1);return {'frames':[{'geometry':[]}],'passed':True}
  a=c.get(('digest1','walk',8),compute);a.pop('frames')
  self.assertIn('frames',c.get(('digest1','walk',8),compute));self.assertEqual(len(calls),1)
  c.get(('digest2','walk',8),compute);c.get(('digest2','walk',32),compute);self.assertEqual(len(calls),3)
  def fail():raise ValueError('invalid')
  with self.assertRaises(ValueError):c.get('invalid',fail)
  self.assertNotIn('invalid',c.entries)
 def test_bounded_eviction(self):
  c=RuntimeReports(max_bytes=30)
  for i in range(20):c.get(i,lambda:{'value':'abc'})
  self.assertLessEqual(c.bytes,30);self.assertNotIn(0,c.entries)

class ConcurrentCacheTests(unittest.TestCase):
 def test_different_keys_run_concurrently_and_same_key_computes_once(self):
  import threading
  from concurrent.futures import ThreadPoolExecutor
  c=RuntimeReports();barrier=threading.Barrier(2);calls=[]
  def work(key):
   def compute():calls.append(key);barrier.wait(timeout=3);return {'key':key}
   return c.get(key,compute)
  with ThreadPoolExecutor(4) as pool:
   a=pool.submit(work,'a');b=pool.submit(work,'b')
   self.assertEqual(a.result(timeout=5),{'key':'a'});self.assertEqual(b.result(timeout=5),{'key':'b'})
  ready=threading.Event();release=threading.Event()
  def compute():calls.append('shared');ready.set();release.wait(3);return {'ok':True}
  with ThreadPoolExecutor(5) as pool:
   futures=[pool.submit(c.get,'shared',compute) for _ in range(5)];self.assertTrue(ready.wait(2));release.set()
   for future in futures:self.assertTrue(future.result(timeout=5)['ok'])
  self.assertEqual(calls.count('shared'),1)
 def test_preview_bytes_copy_isolation_eviction_and_oversize(self):
  from app.runtime_cache import RuntimePreviews
  c=RuntimePreviews(max_bytes=50);calls=[]
  def compute():calls.append(1);return {'values':[1]},b'png-data'
  result=c.get('a',compute);result[0]['values'].append(2)
  self.assertEqual(c.get('a',compute),({'values':[1]},b'png-data'));self.assertEqual(len(calls),1)
  c.get('large',lambda:({},b'x'*100));self.assertNotIn('large',c.entries)
  for k in range(10):c.get(k,compute)
  self.assertLessEqual(c.bytes,50);self.assertNotIn('a',c.entries)
 def test_failure_does_not_poison_key(self):
  c=RuntimeReports()
  def fail():raise ValueError('bad')
  with self.assertRaises(ValueError):c.get('a',fail)
  self.assertEqual(c.pending,{})
  self.assertEqual(c.get('a',lambda:{'ok':True}),{'ok':True})
