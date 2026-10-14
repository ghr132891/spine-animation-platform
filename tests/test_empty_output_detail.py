import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from app.jobs import JobHistory
class EmptyOutputDetailTests(unittest.TestCase):
 def test_failed_item_records_error_without_retry_or_url(self):
  with tempfile.TemporaryDirectory() as root:
   provider=Mock();provider.poll.return_value={'data':{'status':'completed','outputs':[{'item_status':'failed','error':'文件下载失败 https://example.test/secret?token=private'}]}}
   worker=JobHistory(root,provider,'characters');p=worker.path('a'*32,'b'*32);p.parent.mkdir(parents=True);p.write_text(json.dumps({'id':'b'*32,'gen_id':'existing','status':'pending'}))
   result=worker.refresh(p,force=True)
   self.assertEqual(result['status'],'failed');self.assertEqual(result['failure']['provider_message'],'文件下载失败 [URL]');provider.generate.assert_not_called();provider.download_result.assert_not_called()
