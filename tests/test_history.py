import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.core import Project,UserError,atomic_json
from app.jobs import JobHistory


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        files={p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()}
        self.p=Project(self.root/'projects'/('a'*32),files,'历史测试')
    def tearDown(self):self.tmp.cleanup()

    def test_versions_survive_reload_and_restore_without_erasing_newer_edits(self):
        p=self.p;p.recolor(['r1'],80,1,1,0);first=dict(p.pages_data)
        saved=p.save_version('蓝色方案',1)
        p.recolor(['r1'],-100,1,1,1);second=dict(p.pages_data)
        self.assertNotEqual(first,second)
        p=Project(p.directory);self.assertEqual(len(p.history()),4)
        p.restore_version(saved['id'],2)
        self.assertEqual(p.pages_data,first);self.assertEqual(p.revision,3)
        previous=next(e for e in p.history() if e['revision']==2)
        p=Project(p.directory);p.restore_version(previous['id'],3)
        self.assertEqual(p.pages_data,second);self.assertTrue(p.validate()['passed'])
        self.assertEqual(len(p.history()),6)

    def test_legacy_original_undo_and_current_recovered(self):
        p=self.p;p.recolor(['r1'],80,1,1,0);one=dict(p.pages_data);p.recolor(['r1'],-100,1,1,1)
        shutil.rmtree(p.directory/'history')
        recovered=Project(p.directory);self.assertEqual({e['revision'] for e in recovered.history()},{0,1,2})
        snapshot=next(e for e in recovered.history() if e['revision']==1)
        recovered.restore_version(snapshot['id'],2);self.assertEqual(recovered.pages_data,one)

    def test_named_saves_deduplicate_image_storage(self):
        p=self.p;before=len(list((p.directory/'history/blobs').glob('*.png')))
        p.save_version('一',0);p.save_version('二',0)
        self.assertEqual(len(p.history()),3)
        self.assertEqual(len(list((p.directory/'history/blobs').glob('*.png'))),before)

    def test_invalid_or_stale_restore_does_not_touch_current_state(self):
        p=self.p;initial=p.history()[0];p.recolor(['r1'],80,1,1,0);before=dict(p.pages_data)
        with self.assertRaises(UserError):p.restore_version(initial['id'],0)
        entry=p.history_entries[0];digest=next(iter(entry['pages'].values()))
        (p.directory/'history/blobs'/(digest+'.png')).write_bytes(b'broken')
        with self.assertRaises(UserError):p.restore_version(initial['id'],1)
        self.assertEqual(p.pages_data,before);self.assertEqual(p.revision,1)

    def test_all_jobs_visible_sorted_by_creation_not_polling_time(self):
        for n in range(12):
            path=self.p.directory/'jobs'/f'{n:032x}.json'
            atomic_json(path,{'id':f'{n:032x}','region':'r0','created_at':100+n,'status':'pending','revision':0})
        os.utime(self.p.directory/'jobs'/('0'*32+'.json'),(10000,10000))
        jobs=self.p.summary()['jobs'];self.assertEqual(len(jobs),12)
        self.assertEqual(jobs[0]['created_at'],111);self.assertEqual(jobs[-1]['created_at'],100)

    def job(self):
        path=self.p.directory/'jobs'/('b'*32+'.json')
        atomic_json(path,{'id':'b'*32,'region':'r0','created_at':1,'status':'pending','gen_id':'already-submitted','revision':0})
        return path

    def test_background_worker_saves_result_without_any_browser_request(self):
        path=self.job();done=threading.Event();png=self.p.files['robot.png']
        class Provider:
            calls=0
            def generate(self,*args):raise AssertionError('History must never regenerate')
            def poll(self,id,model=None):
                self.calls+=1
                return {'data':{'status':'completed','input':{'prompt':'saved prompt'},'outputs':[{'item_status':'completed','data':{'image_url':'https://result.test/file.png'}}]}}
            def download_result(self,url):done.set();return png
        provider=Provider();worker=JobHistory(self.root,provider);thread=threading.Thread(target=worker.run,daemon=True);thread.start()
        self.assertTrue(done.wait(3));worker.stop.set();worker.wake.set();thread.join(3)
        saved=json.loads(path.read_text());self.assertTrue(saved['candidate_ready']);self.assertEqual(saved['prompt'],'saved prompt');self.assertEqual(path.with_suffix('.png').read_bytes(),png)
        second_worker=JobHistory(self.root,provider);second_worker.refresh(path)
        self.assertEqual(provider.calls,1)

    def test_download_failure_preserves_identity_and_can_resume(self):
        path=self.job();png=self.p.files['robot.png']
        class Provider:
            fail=True
            def poll(self,id,model=None):return {'data':{'status':'completed','outputs':[{'data':{'image_url':'https://result.test/file.png'}}]}}
            def download_result(self,url):
                if self.fail:raise UserError('temporary failure')
                return png
        provider=Provider();worker=JobHistory(self.root,provider);job=worker.refresh(path)
        self.assertEqual(job['gen_id'],'already-submitted');self.assertFalse(job.get('candidate_ready'));self.assertIn('poll_error',job)
        provider.fail=False;job=worker.refresh(path,force=True);self.assertTrue(job['candidate_ready']);self.assertNotIn('poll_error',job)


if __name__=='__main__':unittest.main(verbosity=2)
