"""Regression cases for distorted boots and crouched source-pose walks."""
import copy, math, unittest
import numpy as np
from PIL import Image
from app.character import checked_settings, default_landmarks, make_skeleton, segments
from app.part_layers import aligned_part

class NaturalWalkTests(unittest.TestCase):
    def test_delivery_requires_contact_and_posture_not_just_a_measurement(self):
        from app.contact_diagnostics import require_natural_walk_contact
        from app.core import UserError
        passed={'status':'assessed','posture_checks_passed':True,'numerical_checks_passed':True}
        require_natural_walk_contact(passed)
        for change in ({'numerical_checks_passed':False},{'posture_checks_passed':False},{'status':'not_assessed'}):
            with self.assertRaises(UserError):require_natural_walk_contact({**passed,**change})
    def character(self, facing='right', lift=.25):
        im=Image.new('RGBA',(300,600),'orange');lm=default_landmarks(im)
        for side,x in [('l',.4),('r',.6)]:
            lm.update({side+'-hip':[x,.5],side+'-knee':[x+.15,.65],side+'-ankle':[x,.8],side+'-toe':[x+.1,.83]})
        settings=checked_settings({'facing':facing,'lift':lift})
        return make_skeleton(im,lm,[],settings)

    def test_crouched_source_can_raise_hip_instead_of_preserving_deep_flexion(self):
        for facing in ('left','right'):
            s=self.character(facing);g=s['skeleton']['gait']
            self.assertGreater(g['hip_offset'],0)
            self.assertEqual(g['unreachable_samples'],0)
            self.assertFalse(g['requires_posture_review'],g)
            for report in g['knee_flexion'].values():
                self.assertLessEqual(report['stance_max_deg'],35)
                self.assertLessEqual(report['swing_max_deg'],50)
            self.assertLess(g['effective_lift'],g['requested_lift'])
            for side in ('l','r'):
                self.assertIn(side+'-lower-leg',s['animations']['idle']['bones'])

    def test_foot_scale_is_uniform_and_keeps_leg_lengths(self):
        s=self.character();bones={b['name']:b for b in s['bones']}
        for side in ('l','r'):
            foot=bones[side+'-foot']
            self.assertEqual(foot['scaleX'],.9);self.assertEqual(foot['scaleY'],.9)
            self.assertNotIn('scaleX',bones[side+'-lower-leg'])
        self.assertEqual(checked_settings({'foot_scale':1})['foot_scale'],1)

    def test_horizontal_or_vertical_reference_pins_never_collapse_shoe(self):
        im=Image.new('RGBA',(120,80),'blue')
        for toe in ([.7,.8],[.5,.95]):
            lm={'l-ankle':[.5,.8],'l-toe':toe}
            for pins in (([.2,.5],[.8,.5]),([.5,.2],[.5,.8])):
                part=aligned_part('l-foot',im,{'start_pin':pins[0],'end_pin':pins[1]},lm,(300,600),lambda _: [('l-foot','l-ankle','l-toe')])
                m=np.array(part['placement']['matrix'])
                self.assertGreater(np.linalg.det(m),0)
                self.assertTrue(np.allclose(m.T@m,np.eye(2)*np.linalg.det(m)))
                self.assertTrue(part['image'].getbbox())
