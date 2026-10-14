import unittest
import numpy as np
from PIL import Image
from app.core import UserError
from app.semantic_completion import register_sprite, restore_observed_texture, inspect_limb_identity

class SemanticCompletionTests(unittest.TestCase):
    def test_registration_preserves_similarity_and_maps_joints(self):
        im=Image.new('RGBA',(20,40),(20,40,70,255))
        canvas,pins,report=register_sprite(im,[[10,5],[10,35]],[[40,20],[70,20]],(100,100),{'hip':[10,5],'ankle':[10,35]})
        np.testing.assert_allclose(pins['hip'],[40,20]);np.testing.assert_allclose(pins['ankle'],[70,20])
        m=np.array(report['matrix']);np.testing.assert_allclose(m.T@m,np.eye(2),atol=1e-10)
        self.assertEqual(canvas.getpixel((55,20))[3],255)

    def test_bad_registration_cannot_collapse_a_limb(self):
        with self.assertRaises(UserError):register_sprite(Image.new('RGBA',(10,10)),[[2,2],[2,2]],[[3,3],[3,8]],(20,20),{})

    def test_restoration_keeps_real_hidden_fill_without_loose_fragments(self):
        complete=Image.new('RGBA',(20,20));complete.paste((20,30,50,255),(5,2,15,19))
        observed=Image.new('RGBA',(20,20));observed.paste((60,80,100,255),(9,5,18,12))
        result,report=restore_observed_texture(complete,observed,Image.new('L',(20,20),255))
        self.assertEqual(result.getpixel((6,15)),complete.getpixel((6,15)))
        self.assertEqual(result.getpixel((10,8)),observed.getpixel((10,8)))
        self.assertEqual(result.getpixel((17,8))[3],0)
        self.assertTrue(report['complete_alpha_not_clipped'])

    def test_duplicate_leg_image_is_not_independent_completion(self):
        im=Image.new('RGBA',(10,30),'blue')
        with self.assertRaises(UserError):inspect_limb_identity({'l-upper-leg':im,'r-upper-leg':im.copy()})

if __name__=='__main__':unittest.main()
