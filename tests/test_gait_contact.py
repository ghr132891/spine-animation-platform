"""Mathematical contact checks, not certification of textured natural gait."""
import math
import unittest

from app.gait import solve_two_bone, sole_target, walk_phase, spine_bezier_timeline


def rotate(point, angle):
    r = math.radians(angle)
    c, s = math.cos(r), math.sin(r)
    return c*point[0]-s*point[1], s*point[0]+c*point[1]


class GaitPhaseTests(unittest.TestCase):
    def test_cycle_wrap_and_loop_pose_are_identical(self):
        self.assertEqual(walk_phase(0), walk_phase(1))
        self.assertEqual(walk_phase(-0.5), walk_phase(1.5))
        for cycle in (0, .125, .5, .75):
            self.assertEqual(walk_phase(cycle), walk_phase(cycle+4))

    def test_default_phase_has_double_support_and_no_floating_interval(self):
        double = 0
        for i in range(1000):
            phases = [walk_phase(i/1000+offset) for offset in (0, .5)]
            self.assertTrue(any(p['stance'] for p in phases))
            double += all(p['stance'] for p in phases)
            for p in phases:
                self.assertGreaterEqual(p['clearance'], 0)
                self.assertLessEqual(p['clearance'], 1)
                if p['stance']:
                    self.assertEqual(p['clearance'], 0)
                    self.assertEqual(p['pitch_deg'], 0)
        self.assertAlmostEqual(double/1000, .2, delta=.002)

    def test_position_and_speed_match_at_liftoff_and_loop(self):
        h = 1e-6
        for boundary in (.6, 1.0):
            for key in ('dx', 'clearance', 'pitch_deg'):
                with self.subTest(boundary=boundary, channel=key):
                    left = walk_phase(boundary-h)[key]
                    center = walk_phase(boundary)[key]
                    right = walk_phase(boundary+h)[key]
                    self.assertLess(abs(left-right), 4e-6)
                    self.assertAlmostEqual((center-left)/h, (right-center)/h, delta=.001)

    def test_mid_swing_reaches_configured_lift_and_pitch(self):
        p = walk_phase(.8)
        self.assertFalse(p['stance'])
        self.assertAlmostEqual(p['clearance'], 1)
        self.assertAlmostEqual(p['dx'], 0)
        self.assertAlmostEqual(p['pitch_deg'], 6)
        self.assertEqual(walk_phase(.3)['dx'], 0)

    def test_phase_rejects_nonfinite_and_invalid_duty(self):
        for value in (float('nan'), float('inf'), None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                walk_phase(value)
        for stance in (0, 1, -.1, 1.1):
            with self.subTest(stance=stance), self.assertRaises(ValueError):
                walk_phase(.2, stance=stance)


class SpineCurveTests(unittest.TestCase):
    def test_absolute_bezier_controls_reproduce_a_cubic_motion(self):
        keys = spine_bezier_timeline(lambda t: {'value': t**3}, [0., .2, .7, 1.])
        for first, last in zip(keys, keys[1:]):
            for u in (.25, .5, .75):
                _, cy1, _, cy2 = first['curve']
                value = ((1-u)**3*first['value']+3*(1-u)**2*u*cy1+
                         3*(1-u)*u*u*cy2+u**3*last['value'])
                time = first['time']+(last['time']-first['time'])*u
                self.assertAlmostEqual(value, time**3, places=9)

    def test_adjacent_translation_intervals_share_endpoint_tangents(self):
        keys = spine_bezier_timeline(lambda t: {'x': 3*t, 'y': math.sin(t)},
                                     [0., .3, .8, 1.], ('x', 'y'))
        for i in range(1, len(keys)-1):
            for axis, index in [('x', 0), ('y', 4)]:
                before, at = keys[i-1], keys[i]
                incoming = (at[axis]-before['curve'][index+3])/(at['time']-before['curve'][index+2])
                outgoing = (at['curve'][index+1]-at[axis])/(at['curve'][index]-at['time'])
                self.assertAlmostEqual(incoming, outgoing, places=10)

    def test_rotation_crossing_180_uses_short_path_and_finite_controls(self):
        keys = spine_bezier_timeline(lambda t: {'value': (170+40*t+180) % 360-180},
                                     [0., .25, .5, .75, 1.], angular=True)
        self.assertEqual([frame['value'] for frame in keys], [170, 180, 190, 200, 210])
        for first, last in zip(keys, keys[1:]):
            self.assertTrue(all(math.isfinite(value) for value in first['curve']))
            self.assertAlmostEqual((first['curve'][1]-first['value'])/(first['curve'][0]-first['time']), 40, places=6)
            self.assertLess(abs(last['value']-first['value']), 11)

    def test_looping_motion_preserves_pose_and_endpoint_speed(self):
        keys = spine_bezier_timeline(lambda t: {'x': math.cos(2*math.pi*t), 'y': math.sin(2*math.pi*t)},
                                     [i/16 for i in range(17)], ('x', 'y'))
        for axis, index in [('x', 0), ('y', 4)]:
            self.assertAlmostEqual(keys[0][axis], keys[-1][axis], places=10)
            incoming = (keys[-1][axis]-keys[-2]['curve'][index+3])/(1-keys[-2]['curve'][index+2])
            outgoing = (keys[0]['curve'][index+1]-keys[0][axis])/keys[0]['curve'][index]
            self.assertAlmostEqual(incoming, outgoing, places=8)

    def test_invalid_time_sequence_axes_or_sample_is_rejected(self):
        for times in ([], [0], [0, 0], [1, 0], [-1, 0], [0, float('inf')]):
            with self.subTest(times=times), self.assertRaises(ValueError):
                spine_bezier_timeline(lambda t: {'value': t}, times)
        for sample in (lambda t: {}, lambda t: 1, lambda t: {'value': float('nan')}):
            with self.assertRaises(ValueError):
                spine_bezier_timeline(sample, [0, 1])
        with self.assertRaises(ValueError):
            spine_bezier_timeline(lambda t: {'x': t}, [0, 1], ('x',))
        with self.assertRaises(ValueError):
            spine_bezier_timeline(lambda t: {'x': t, 'y': t}, [0, 1], ('x', 'y'), angular=True)


class SoleContactTests(unittest.TestCase):
    def setUp(self):
        # An uneven sole in foot-local space; bone angle alone cannot locate
        # its contact plane. The actual sampler may provide many more points.
        self.sole = [(-12, -8), (-6, -9), (12, -9), (20, -5)]

    def test_given_absolute_angles_keep_every_sample_above_contact_plane(self):
        for angle in (-75, -15, 0, 20, 115):
            for i in range(101):
                phase = walk_phase(i/100)
                target = sole_target((20, 13), self.sole, angle, phase, 24, 7, ground_y=3)
                minimum = min(y for x, y in target['sole_points'])
                expected = 3 if phase['stance'] else 3+7*phase['clearance']
                with self.subTest(angle=angle, cycle=i/100):
                    self.assertAlmostEqual(minimum, expected, places=10)
                    self.assertAlmostEqual(target['contact_y'], minimum, places=10)
                    self.assertTrue(all(y >= expected-1e-10 for _, y in target['sole_points']))

    def test_sole_height_uses_pixels_not_the_old_ankle_height(self):
        phase = walk_phase(.3)
        a = sole_target((0, 2), self.sole, 20, phase, 24, 7)
        b = sole_target((0, 200), self.sole, 20, phase, 24, 7)
        self.assertEqual(a, b)
        self.assertNotEqual(a['ankle'][1], 2)
        self.assertAlmostEqual(a['sole_min_y'], 0)

    def test_left_and_right_targets_are_geometric_mirrors(self):
        mirrored = [(-x, y) for x, y in self.sole]
        for i in range(101):
            phase = walk_phase(i/100)
            angle = 20+phase['pitch_deg']
            right = sole_target((30, 12), self.sole, angle, phase, 24, 7, 1)
            left = sole_target((-30, 12), mirrored, -angle, phase, 24, 7, -1)
            self.assertAlmostEqual(left['ankle'][0], -right['ankle'][0])
            self.assertAlmostEqual(left['ankle'][1], right['ankle'][1])
            for a, b in zip(left['sole_points'], right['sole_points']):
                self.assertAlmostEqual(a[0], -b[0])
                self.assertAlmostEqual(a[1], b[1])

    def test_root_travel_cancels_support_foot_sliding(self):
        stride, period = 24, 1.2
        for facing in (1, -1):
            world_x = []
            for i in range(60):
                cycle = i/100
                target = sole_target((0, 12), self.sole, 0, walk_phase(cycle), stride, 7, facing)
                root_travel = facing*stride/(.6*period)*(cycle*period)
                world_x.append(target['sole_points'][0][0]+root_travel)
            self.assertLess(max(world_x)-min(world_x), 1e-10)

    def test_bad_sole_or_phase_cannot_produce_a_contact_claim(self):
        for sole in ([], None, [(1, float('nan'))], [(1,) ]):
            with self.subTest(sole=sole), self.assertRaises(ValueError):
                sole_target((0, 10), sole, 0, walk_phase(0), 24, 7)
        for phase in ({}, {'stance': 1, 'dx': 0, 'clearance': 0},
                      {'stance': False, 'dx': 0, 'clearance': 1.1}):
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                sole_target((0, 10), self.sole, 0, phase, 24, 7)


class TwoBoneContactTests(unittest.TestCase):
    def test_reachable_steps_keep_bone_lengths_and_actual_sole_contact(self):
        sole = [(-10, -8), (18, -8), (23, -5)]
        for facing in (1, -1):
            points = [(x*facing, y) for x, y in sole]
            for i in range(241):
                phase = walk_phase(i/240)
                angle = facing*phase['pitch_deg']
                target = sole_target((0, 8), points, angle, phase, 24, 7, facing)
                result = solve_two_bone((0, 80), target['ankle'], 40, 40, bend=-facing)
                with self.subTest(facing=facing, cycle=i/240):
                    self.assertEqual(result['reach'], 'reachable')
                    self.assertFalse(result['clamped'])
                    self.assertAlmostEqual(math.dist(result['hip'], result['knee']), 40, places=10)
                    self.assertAlmostEqual(math.dist(result['knee'], result['ankle']), 40, places=10)
                    self.assertLess(math.dist(result['ankle'], target['ankle']), 1e-10)
                    rendered = [rotate(p, angle) for p in points]
                    lowest = min(result['ankle'][1]+p[1] for p in rendered)
                    self.assertAlmostEqual(lowest, target['contact_y'], places=10)

    def test_absolute_angles_reconstruct_the_returned_joints(self):
        for bend in (-1, 1):
            pose = solve_two_bone((13, 90), (18, 14), 43, 39, bend)
            upper = rotate((43, 0), pose['upper_angle'])
            lower = rotate((39, 0), pose['lower_angle'])
            knee = (13+upper[0], 90+upper[1])
            ankle = (knee[0]+lower[0], knee[1]+lower[1])
            self.assertLess(math.dist(knee, pose['knee']), 1e-10)
            self.assertLess(math.dist(ankle, pose['ankle']), 1e-10)
            self.assertAlmostEqual(pose['lower_angle']-pose['upper_angle'], pose['bend_angle'])

    def test_unreachable_targets_are_reported_and_never_stretch_bones(self):
        for target, expected, distance in (((0, -500), 'too_far', 60), ((2, 0), 'too_close', 20)):
            with self.subTest(target=target):
                pose = solve_two_bone((0, 0), target, 40, 20)
                self.assertEqual(pose['reach'], expected)
                self.assertTrue(pose['clamped'])
                self.assertAlmostEqual(pose['solved_distance'], distance)
                self.assertAlmostEqual(math.dist(pose['hip'], pose['knee']), 40)
                self.assertAlmostEqual(math.dist(pose['knee'], pose['ankle']), 20)
                self.assertGreater(math.dist(pose['ankle'], target), 1)

    def test_singular_targets_still_return_finite_geometry(self):
        for target in ((0, 0), (80, 0)):
            for bend in (-1, 1):
                pose = solve_two_bone((0, 0), target, 40, 40, bend)
                self.assertEqual(pose['reach'], 'singular')
                self.assertFalse(pose['clamped'])
                self.assertTrue(all(math.isfinite(v) for key in ('hip', 'knee', 'ankle') for v in pose[key]))
                self.assertLess(math.dist(pose['ankle'], target), 1e-10)

    def test_mirrored_bend_selects_the_mirrored_knee(self):
        right = solve_two_bone((10, 80), (18, 8), 43, 39, -1)
        left = solve_two_bone((-10, 80), (-18, 8), 43, 39, 1)
        for joint in ('knee', 'ankle'):
            self.assertAlmostEqual(right[joint][0], -left[joint][0])
            self.assertAlmostEqual(right[joint][1], left[joint][1])

    def test_degenerate_lengths_and_nonfinite_positions_are_rejected(self):
        for lengths in ((0, 20), (20, -1), (float('nan'), 20)):
            with self.subTest(lengths=lengths), self.assertRaises(ValueError):
                solve_two_bone((0, 80), (18, 8), *lengths)
        with self.assertRaises(ValueError):
            solve_two_bone((0, float('inf')), (18, 8), 43, 39)
        with self.assertRaises(ValueError):
            solve_two_bone((0, 80), (18, 8), 43, 39, bend=0)


if __name__ == '__main__':
    unittest.main()
