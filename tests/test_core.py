import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.core import Project,UserError,decode_files,png_bytes,packed,parse_atlas


class TextureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def demo(self):
        return Project(self.path/'demo',{p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()},'test')
    def test_full_edit_export_preserves_skeleton_and_alpha(self):
        p=self.demo();before=p.files['robot.json'];atlas=p.files['robot.atlas'];old=Image.open(io.BytesIO(p.files['robot.png']))
        p.recolor(['r0','r1'],90,1.2,1.1,0)
        self.assertEqual(p.validate()['passed'],True)
        with zipfile.ZipFile(io.BytesIO(p.export())) as z:
            self.assertEqual(z.read('robot.json'),before);self.assertEqual(z.read('robot.atlas'),atlas)
            new=Image.open(io.BytesIO(z.read('robot.png')))
            np.testing.assert_array_equal(np.array(old)[...,3],np.array(new)[...,3])
            self.assertFalse(np.array_equal(np.array(old),np.array(new)))
            self.assertTrue(json.loads(z.read('_reskin-validation.json'))['passed'])
        p.undo(1);self.assertEqual(p.pages_data['robot.png'],p.files['robot.png']);self.assertEqual(p.changed,[])
    def test_wrong_dimensions_and_stale_revision_leave_project_unchanged(self):
        p=self.demo();before=dict(p.pages_data)
        with self.assertRaises(UserError):p.replace('r0',png_bytes(Image.new('RGBA',(2,3),'red')),0)
        with self.assertRaises(UserError):p.recolor(['r0'],0,1,1,8)
        self.assertEqual(p.pages_data,before);self.assertEqual(p.revision,0)
    def test_persistence_and_restore_original(self):
        p=self.demo();p.recolor(['r0'],80,1,1,0);restored=Project(p.directory)
        self.assertEqual(restored.changed,['r0']);self.assertEqual(restored.pages_data,p.pages_data)
        restored.reset(None,1);self.assertEqual(restored.pages_data['robot.png'],p.files['robot.png'])
    def fixture(self,angle=90,pma=False,compact=True):
        # Distinct corners catch inverse rotation/offset mistakes, not just dimensions.
        arr=np.zeros((3,5,4),dtype=np.uint8)
        for y in range(3):
            for x in range(5):arr[y,x]=[20+30*x,30+40*y,70,128 if (x+y)%2 else 255]
        part=Image.fromarray(arr)
        im=Image.new('RGBA',(20,20))
        rotated=part.rotate(angle,expand=True)
        if pma:rotated=packed(rotated,True,np.array(rotated)[...,3])
        im.paste(rotated,(2,4))
        region=f'body\n  bounds: 2,4,5,3\n  offsets: 2,1,9,7\n  rotate: {angle}\n' if compact else f'body\n  xy: 2,4\n  size: 5,3\n  orig: 9,7\n  offset: 2,1\n  rotate: {"true" if angle==90 else "false"}\n'
        atlas=f'page.png\nsize: 20,20\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\npma: {str(pma).lower()}\n'+region+'\nother.png\nsize: 20,20\nother\nbounds: 1,1,2,2\n'
        sk=json.dumps({'skeleton':{'spine':'4.2.0'},'bones':[{'name':'root'}]}).encode()
        files={'char/rig.json':sk,'char/rig.skel':b'opaque-binary-skeleton','char/atlas/rig.atlas':atlas.encode(),'char/atlas/page.png':png_bytes(im),'char/atlas/other.png':png_bytes(Image.new('RGBA',(20,20),(11,22,33,77)))}
        return Project(self.path/('rot'+str(angle)+str(pma)+str(compact)),files,'fixture'),part
    def test_rotation_trim_multpage_nested_roundtrip(self):
        for angle in [0,90,180,270]:
            with self.subTest(rotation=angle):
                p,part=self.fixture(angle)
                full=p.part('r0');self.assertEqual(full.size,(9,7))
                np.testing.assert_array_equal(np.array(full.crop((2,3,7,6))),np.array(part))
                before=p.pages_data.copy();p.replace('r0',png_bytes(full),0)
                np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(p.pages_data['char/atlas/page.png']))),np.array(Image.open(io.BytesIO(before['char/atlas/page.png']))))
                self.assertEqual(before['char/atlas/other.png'],p.pages_data['char/atlas/other.png'])
                with zipfile.ZipFile(io.BytesIO(p.export())) as z:self.assertEqual(z.read('char/rig.skel'),b'opaque-binary-skeleton')
    def test_pma_edit_keeps_stored_alpha_and_premultiplied_rgb(self):
        p,part=self.fixture(pma=True);candidate=p.part('r0');a=np.array(candidate);a[...,:3]=[220,180,100]
        p.replace('r0',png_bytes(Image.fromarray(a)),0)
        stored=np.array(Image.open(io.BytesIO(p.pages_data['char/atlas/page.png'])))
        self.assertTrue(np.all(stored[...,:3]<=stored[...,3:4]));self.assertTrue(p.validate()['passed'])
    def test_legacy_trim(self):
        p,part=self.fixture(compact=False);np.testing.assert_array_equal(np.array(p.part('r0').crop((2,3,7,6))),np.array(part))
    def test_zip_import_and_path_traversal(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('folder/robot.json',b'{}');z.writestr('folder/robot.png',b'png')
        result=decode_files([{'name':'demo.zip','data':base64.b64encode(out.getvalue()).decode()}]);self.assertEqual(set(result),{'robot.json','robot.png'})
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('../escape',b'x')
        with self.assertRaises(UserError):decode_files([{'name':'bad.zip','data':base64.b64encode(out.getvalue()).decode()}])
    def test_tampered_immutable_file_blocks_export(self):
        p=self.demo();(p.directory/'original/robot.json').write_bytes(b'changed')
        self.assertFalse(p.validate()['passed'])
        with self.assertRaises(UserError):p.export()
    def test_binary_only_preserved(self):
        files={p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file() and p.suffix!='.json'}
        binary=b'\x00\x07' + b'3.8.99'+b'\x00\x10test';files['robot.skel']=binary;p=Project(self.path/'binary',files,'binary')
        self.assertEqual(p.runtime,'3.8');p.recolor(['r0'],35,1,1,0)
        with zipfile.ZipFile(io.BytesIO(p.export())) as z:self.assertEqual(z.read('robot.skel'),binary)


if __name__=='__main__':unittest.main(verbosity=2)
