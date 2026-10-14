import tempfile,unittest,json
from pathlib import Path
from unittest.mock import Mock
from PIL import Image
from app.character import CharacterProject
from app.core import png_bytes,sha,UserError
class PartFinishTests(unittest.TestCase):
 def test_preserves_source_and_invalidates_review(self):
  with tempfile.TemporaryDirectory() as root:
   c=CharacterProject.create(Path(root),'finish');c.summary=Mock(return_value={});im=Image.new('RGBA',(20,20),(40,60,80,255));blob=png_bytes(im);digest=sha(blob);folder=c.directory/'part-images';folder.mkdir(exist_ok=True);(folder/(digest+'.png')).write_bytes(blob)
   c.meta['part_layers']={'head':{'image_id':digest,'canvas_offset':[5,7]}};c.meta['prebind_review']={'verdict':'ready'}
   c.finish_parts({'revision':c.meta['revision'],'edits':[{'part':'head','reason':'verified extra edge','operations':[{'kind':'erase','polygon':[[0,0],[.2,0],[.2,1],[0,1]]}]}]})
   self.assertTrue((folder/(digest+'.png')).exists());self.assertNotEqual(c.meta['part_layers']['head']['image_id'],digest);self.assertEqual(c.meta['part_layers']['head']['canvas_offset'],[5,7]);self.assertNotIn('prebind_review',c.meta)
   before=c.meta['revision']
   with self.assertRaises(UserError):c.finish_parts({'revision':before,'edits':[{'part':'missing','reason':'bad','operations':[]}]})
   self.assertEqual(c.meta['revision'],before)
