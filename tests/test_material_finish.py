import copy, unittest
import numpy as np
from PIL import Image, ImageDraw
from app.core import UserError
from app.material_finish import finish_material
from tools.render_rig_evidence import sample_linear

WHOLE=[[0,0],[1,0],[1,1],[0,1]]
class MaterialFinishTests(unittest.TestCase):
    def test_sheer_gradient_preserves_rgb_and_unselected_costume(self):
        im=Image.new('RGBA',(80,100),(20,120,220,255));before=np.array(im)
        out,report=finish_material(im,{'reason':'原图下裙为轻纱','operations':[{'kind':'opacity','polygon':[[0,.5],[1,.5],[1,1],[0,1]],'top':.5,'bottom':1,'start':1,'end':.5,'reference_note':'依据原立绘材质，非alpha精确恢复'}]})
        a=np.array(out);self.assertTrue(np.array_equal(a[:,:,:3],before[:,:,:3]));self.assertTrue(np.array_equal(a[:45],before[:45]));self.assertGreater(a[55,40,3],a[90,40,3]);self.assertTrue(125<a[97,40,3]<140);self.assertEqual(out.size,im.size)
        self.assertGreater(report['alpha_after']['partial_pixels'],0)
    def test_scoped_antialias_erase_leaves_collar_and_art_unchanged(self):
        im=Image.new('RGBA',(80,80),(120,90,60,255));ImageDraw.Draw(im).rectangle((0,0,20,20),fill='white')
        before=np.array(im);out,_=finish_material(im,{'reason':'实看杂片','operations':[{'kind':'erase','polygon':[[.5,.3],[.9,.7],[.5,.7]]}]});a=np.array(out)
        self.assertTrue(np.array_equal(a[:20,:20],before[:20,:20]));self.assertTrue(np.array_equal(a[:,:,:3],before[:,:,:3]));self.assertTrue(((a[:,:,3]>0)&(a[:,:,3]<255)).any());self.assertEqual(a[48,45,3],0)
    def test_cleanup_never_adds_opaque_background_or_changes_interior(self):
        im=Image.new('RGBA',(80,80));ImageDraw.Draw(im).rectangle((20,20,60,60),fill=(20,110,220,255));im.putpixel((61,40),(20,110,220,255))
        a=np.array(im);out,_=finish_material(im,{'reason':'一像素毛刺','operations':[{'kind':'edge-cleanup','polygon':WHOLE,'radius':.6}]});b=np.array(out)
        self.assertTrue((b[:,:,3]<=a[:,:,3]).all());self.assertEqual(b[40,40,3],255);self.assertLess(b[40,61,3],255)
    def test_invalid_material_instructions_fail_before_change(self):
        im=Image.new('RGBA',(20,20),'white')
        cases=[{'kind':'opacity','polygon':WHOLE,'end':.4}, {'kind':'edge-cleanup','polygon':WHOLE,'radius':float('nan')}, {'kind':'erase','polygon':[[0,0],[2,0],[1,1]]}]
        for op in cases:
            with self.assertRaises(UserError):finish_material(im,{'reason':'test','operations':[op]})
        self.assertEqual(im.getpixel((10,10)),(255,255,255,255))
    def test_closeup_adds_real_pose_contrast_evidence(self):
        from app.seam_review import render_closeups
        calls=[]
        def draw(frame,directory,view,size,padding,background=(229,235,243,255)):
            calls.append((frame['tag'],background));return Image.new('RGBA',size,background)
        frame={'tag':'pose','bones':[{'name':'torso','x':0,'y':0,'ex':0,'ey':20}], 'geometry':[]}
        sheet,report=render_closeups([frame]*8,None,'torso',draw)
        self.assertEqual(sheet.size,(1600,1200));self.assertTrue(report['material_contrast']['included'])
        self.assertEqual(len(calls),12);self.assertIn(('pose',(36,43,55,255)),calls)

    def test_linear_renderer_matches_atlas_filter_not_nearest(self):
        a=np.array([[[0,0,0,0],[200,100,50,200]],[[0,0,0,0],[200,100,50,200]]],dtype='uint8')
        r=sample_linear(a,np.array([[.5,.75],[.5,.5]]));np.testing.assert_allclose(r[0],[100,50,25,100]);np.testing.assert_allclose(r[1],[200,100,50,200])

if __name__=='__main__':unittest.main()
