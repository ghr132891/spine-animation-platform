import unittest
import numpy as np
from PIL import Image,ImageDraw
from app.edge_review import candidates

class EdgeEvidenceTests(unittest.TestCase):
    def test_finds_opaque_hair_spot_without_modifying_pixels(self):
        im=Image.new('RGBA',(24,24));ImageDraw.Draw(im).rectangle((4,4,20,20),fill=(80,60,49,255))
        im.putpixel((6,10),(239,239,231,255));before=im.tobytes()
        r=candidates(im)
        self.assertIn({'pixel':[6,10],'rgba':[239,239,231,255],'kind':'opaque_light_spot'},r['points'])
        self.assertEqual(im.tobytes(),before)
        im.putpixel((6,10),(80,60,49,255))
        self.assertFalse(any(p['kind']=='opaque_light_spot' for p in candidates(im)['points']))

    def test_white_material_is_not_assumed_to_be_contamination(self):
        im=Image.new('RGBA',(24,24));d=ImageDraw.Draw(im)
        d.rectangle((5,5,18,18),fill=(245,245,245,255));d.line((4,6,4,17),fill=(250,250,250,120))
        self.assertEqual(candidates(im)['points'],[])

    def test_partial_fringe_has_separate_diagnostic(self):
        im=Image.new('RGBA',(24,24));d=ImageDraw.Draw(im)
        d.rectangle((5,5,18,18),fill=(80,45,25,255));d.line((4,6,4,17),fill=(205,190,180,120))
        self.assertTrue(any(p['kind']=='partial_alpha_fringe' for p in candidates(im)['points']))

    def test_persistent_evidence_is_identical_and_invalidates_on_pixel_change(self):
        import tempfile
        from pathlib import Path
        from app.character import CharacterProject
        from app.core import png_bytes,sha
        from app.edge_review import prepare
        with tempfile.TemporaryDirectory() as tmp:
            c=CharacterProject.create(Path(tmp),'edges');folder=c.directory/'part-images';folder.mkdir()
            im=Image.new('RGBA',(24,24));ImageDraw.Draw(im).rectangle((4,4,20,20),fill=(80,60,49,255));im.putpixel((6,10),(239,239,231,255))
            blob=png_bytes(im);digest=sha(blob);(folder/(digest+'.png')).write_bytes(blob);c.meta['part_layers']={'head':{'image_id':digest}}
            a,image=prepare(c);b,cached=prepare(c);self.assertEqual(a,b);self.assertEqual(png_bytes(image),png_bytes(cached))
            im.putpixel((6,10),(80,60,49,255));blob=png_bytes(im);digest=sha(blob);(folder/(digest+'.png')).write_bytes(blob);c.meta['part_layers']['head']['image_id']=digest
            changed,_=prepare(c);self.assertNotEqual(a['digest'],changed['digest']);self.assertFalse(changed['pixels_modified'])
