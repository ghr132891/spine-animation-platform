import unittest
import math
import numpy as np
from PIL import Image,ImageDraw
from app.foot_contact import sole_profile
from app.gait import sole_target,walk_phase

def shoe(slope=0,chain=False):
    image=Image.new('RGBA',(120,180))
    draw=ImageDraw.Draw(image)
    draw.polygon([(10,30),(100,30),(100,70+slope*90),(10,70)],fill=(120,30,50,255))
    return {'name':'l-upper-leg' if chain else 'l-foot','box':[20,100,140,280],
            'image':image,'complete':True,**({'chain':'leg'} if chain else {})}

class FootContourTests(unittest.TestCase):
    def profile(self,part):return sole_profile(part,(50,135),-35,1,(0,0),240)
    def test_uses_visible_pixels_not_transparent_rectangle(self):
        profile=self.profile(shoe())
        self.assertAlmostEqual(profile['setup_ground_y'],69.5)
        self.assertGreater(profile['sample_count'],8)
        self.assertAlmostEqual(profile['observed_sole_angle'],0,places=6)
    def test_flattening_a_tilted_shoe_keeps_lower_contour_level(self):
        profile=self.profile(shoe(.2))
        phase=walk_phase(.2)
        target=sole_target((50,105),profile['points'],profile['flat_angle'],phase,30,15,ground_y=0)
        self.assertAlmostEqual(target['sole_min_y'],0)
        heights=np.array(target['sole_points'])[:,1]
        self.assertLess(float(np.ptp(heights)),1.2)
    def test_complete_chain_excludes_pants_above_ankle(self):
        part=shoe(chain=True)
        ImageDraw.Draw(part['image']).rectangle((0,0,119,25),fill=(30,30,30,255))
        self.assertAlmostEqual(self.profile(part)['setup_ground_y'],69.5)
    def test_incomplete_blended_source_is_not_certified_rigid(self):
        part=shoe();part['complete']=False
        self.assertIsNone(self.profile(part))
    def test_empty_or_sparse_pixels_return_fallback(self):
        part=shoe();part['image']=Image.new('RGBA',(120,100))
        self.assertIsNone(self.profile(part))
    def test_strongly_tilted_source_requires_view_review(self):
        self.assertIsNone(self.profile(shoe(.8)))
    def test_swing_clears_actual_sole(self):
        profile=self.profile(shoe(.2));phase=walk_phase(.8)
        target=sole_target((50,105),profile['points'],profile['flat_angle']+phase['pitch_deg'],phase,30,15,ground_y=0)
        self.assertAlmostEqual(target['sole_min_y'],15*phase['clearance'])

    def test_narrow_contact_pixel_is_not_lost_by_profile_sampling(self):
        part=shoe()
        ImageDraw.Draw(part['image']).line((13,69,13,77),fill=(120,30,50,255))
        profile=self.profile(part)
        self.assertAlmostEqual(profile['setup_ground_y'],62.5)
        phase=walk_phase(.2)
        target=sole_target((50,105),profile['points'],profile['flat_angle'],phase,30,15,ground_y=0)
        self.assertAlmostEqual(min(p[1] for p in target['sole_points']),0)

if __name__=='__main__':unittest.main()
