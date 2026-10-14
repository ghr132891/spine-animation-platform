import copy
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.contact_diagnostics import diagnose_contact


ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / 'outputs/blue-coat-warrior-gait-v2/side-view-v2/export'
NODE = shutil.which('node') or str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')


@unittest.skipUnless(Path(NODE).is_file(), 'Official runtime requires Node')
class ContactDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.version = '4.2'
        self.source = self.fixture()
        self.options = {
            'samples': 64, 'ground_y': .5, 'scene_speed': 10 / .6, 'stance': .6,
            'pairs': [{'slot': 'shoe-slot', 'hip': 'thigh', 'knee': 'shin', 'foot': 'foot', 'phase': 0}],
        }
        self.texture()

    def tearDown(self):
        self.temporary.cleanup()

    def texture(self, rotated=False):
        page = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        page.paste((30, 50, 90, 255), (3, 5, 13 if rotated else 33, 35 if rotated else 15))
        page.save(self.folder / 'page.png')
        (self.folder / 'skeleton.atlas').write_text(
            'page.png\nsize: 64,64\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\n'
            'shoe\n  rotate: ' + ('true' if rotated else 'false') +
            '\n  xy: 3,5\n  size: 30,10\n  orig: 30,10\n  offset: 0,0\n  index: -1\n')

    @staticmethod
    def fixture(version='4.2'):
        attachment = {'type': 'region', 'path': 'shoe', 'width': 30, 'height': 10, 'y': 5}
        skins = [{'name': 'default', 'attachments': {'shoe-slot': {'shoe': attachment}}}]
        return {
            'skeleton': {'spine': version + '.99'},
            # Declared lengths intentionally disagree; actual child origins form a 40-unit leg.
            'bones': [{'name': 'root', 'y': 40}, {'name': 'thigh', 'parent': 'root', 'length': 999},
                      {'name': 'shin', 'parent': 'thigh', 'y': -20, 'length': 999},
                      {'name': 'foot', 'parent': 'shin', 'y': -20, 'length': 999}],
            'slots': [{'name': 'shoe-slot', 'bone': 'foot', 'attachment': 'shoe'}], 'skins': skins,
            'animations': {'walk': {'bones': {'thigh': {'translate': [
                {'time': 0, 'x': 5, 'y': 0}, {'time': .6, 'x': -5, 'y': 0},
                {'time': .8, 'x': 0, 'y': 10}, {'time': 1, 'x': 5, 'y': 0}]}}}},
        }

    def diagnose(self, *, options=None, animation='walk'):
        (self.folder / 'skeleton.json').write_text(json.dumps(self.source))
        return diagnose_contact(self.folder, self.version, animation, self.options if options is None else options)

    def test_runtime_knee_angle_does_not_trust_declared_length_or_metadata(self):
        self.source['skeleton']['gait']={'knee_flexion':{'l':{'passed':True}}}
        self.source['bones'][2]['rotation']=65
        report=self.diagnose()
        self.assertEqual(report['status'],'assessed',report)
        self.assertFalse(report['posture_checks_passed'])
        self.assertAlmostEqual(report['feet'][0]['knee_flexion']['stance_max_deg'],65,places=4)

    def test_actual_opaque_shoe_contact_and_child_offset_length_not_declared_length(self):
        report = self.diagnose()
        self.assertEqual(report['status'], 'assessed', report)
        self.assertTrue(report['numerical_checks_passed'], report)
        foot = report['feet'][0]
        self.assertAlmostEqual(foot['actual_chain_length'], 40, places=6)
        self.assertGreater(foot['alpha_boundary_pixels'], 30)
        self.assertLess(foot['support']['sole_height_drift'], 1e-5)
        self.assertGreater(foot['support']['persistent_contact_pixels']['comparisons'], 0)
        self.assertTrue(foot['checks']['persistent_contact_speed'])
        self.assertGreater(foot['swing']['maximum_lift'], 9)
        self.assertTrue(report['loop']['seamless'])
        self.assertEqual(report['samples'], 64)
        self.assertNotIn('samples', foot, 'Full sample arrays are opt-in')
        self.assertTrue(report['limits']['numerical_only'])
        self.assertIn('natural_gait', report['limits']['does_not_certify'])

    def test_rotated_atlas_and_mirrored_bone_use_actual_uvs(self):
        ordinary = self.diagnose()
        self.texture(rotated=True)
        self.source['bones'][1]['scaleX'] = -1
        rotated = self.diagnose()
        self.assertEqual(rotated['status'], 'assessed', rotated)
        self.assertTrue(rotated['numerical_checks_passed'], rotated)
        self.assertEqual(rotated['feet'][0]['alpha_boundary_pixels'], ordinary['feet'][0]['alpha_boundary_pixels'])
        self.assertAlmostEqual(rotated['feet'][0]['support']['measured_plane'], .5, places=5)

    def test_bad_known_stance_is_measured_as_contact_failure(self):
        report = self.diagnose(options={**self.options, 'stance': .95})
        self.assertEqual(report['status'], 'assessed', report)
        self.assertFalse(report['checks']['ground_contact'])
        self.assertFalse(report['numerical_checks_passed'])
        self.assertGreater(report['feet'][0]['support']['sole_height_drift'], 9)

    def test_wrong_shared_scene_speed_is_detected_from_pixels(self):
        report = self.diagnose(options={**self.options, 'scene_speed': 0})
        self.assertEqual(report['status'], 'assessed', report)
        self.assertTrue(report['checks']['ground_contact'])
        self.assertFalse(report['checks']['shared_scene_speed'])
        self.assertFalse(report['feet'][0]['checks']['persistent_contact_speed'])
        self.assertGreater(report['feet'][0]['support']['slip_range'], 9)

    def test_same_shoe_center_with_moving_contact_texels_does_not_pass_slip_check(self):
        keys = [(0, 0), (.2, 1), (.4, -1), (.6, 0), (1, 0)]
        self.source['animations']['walk']['bones']['foot'] = {
            'rotate': [{'time': time, 'value': angle} for time, angle in keys],
            'translate': [{'time': time, 'x': 5 * math.sin(math.radians(angle)),
                           'y': .5 + 14.5 * abs(math.sin(math.radians(angle))) - .5 * math.cos(math.radians(angle))}
                          for time, angle in keys],
        }
        report = self.diagnose()
        self.assertEqual(report['status'], 'assessed', report)
        foot = report['feet'][0]
        self.assertTrue(foot['checks']['ground_contact'])
        self.assertTrue(foot['checks']['shoe_center_scene_speed'], foot)
        self.assertFalse(foot['checks']['persistent_contact_speed'], foot)
        self.assertFalse(report['checks']['shared_scene_speed'])
        self.assertFalse(report['numerical_checks_passed'])

    def test_per_foot_speed_cannot_hide_different_common_speed(self):
        options = copy.deepcopy(self.options)
        options['pairs'][0]['scene_speed'] = -self.options['scene_speed']
        with patch('app.contact_diagnostics.subprocess.run') as process:
            report = self.diagnose(options=options)
        self.assertEqual(report['status'], 'not_assessed')
        self.assertEqual(report['reason_code'], 'inconsistent-scene-speed')
        process.assert_not_called()

    def test_missing_conditions_or_mapping_are_not_guessed(self):
        report = self.diagnose(options={})
        self.assertEqual(report['status'], 'not_assessed')
        self.assertEqual(report['feet'], [])
        report = self.diagnose(options={key: value for key, value in self.options.items() if key != 'pairs'})
        self.assertEqual(report['reason_code'], 'missing-leg-mapping')
        self.assertFalse(report['numerical_checks_passed'])

    def test_nonexistent_animation_never_runs_runtime(self):
        with patch('app.contact_diagnostics.subprocess.run') as process:
            report = self.diagnose(animation='run')
        self.assertEqual(report['reason_code'], 'animation-not-found')
        self.assertFalse(report['numerical_checks_passed'])
        process.assert_not_called()

    def test_slot_with_no_foot_owned_pixels_is_not_assessed(self):
        self.source['slots'][0]['bone'] = 'thigh'
        report = self.diagnose()
        self.assertEqual(report['status'], 'not_assessed', report)
        self.assertEqual(report['reason_code'], 'insufficient-rigid-foot-pixels')

    def test_wrong_chain_mapping_is_not_assessed(self):
        options = copy.deepcopy(self.options)
        options['pairs'][0]['knee'] = 'root'
        report = self.diagnose(options=options)
        self.assertEqual(report['reason_code'], 'mapping-is-not-a-continuous-leg')
        self.assertFalse(report['numerical_checks_passed'])

    def test_invisible_attachment_at_uniform_frame_is_not_assessed(self):
        self.source['animations']['walk']['slots'] = {'shoe-slot': {'alpha': [
            {'time': 0, 'value': 1}, {'time': .5, 'value': 0}, {'time': 1, 'value': 1}]}}
        report = self.diagnose()
        self.assertEqual(report['reason_code'], 'foot-attachment-not-opaque')

    def test_invisible_attachment_only_between_uniform_samples_is_not_assessed(self):
        self.source['animations']['walk']['slots'] = {'shoe-slot': {'alpha': [
            {'time': 0, 'value': 1, 'curve': 'stepped'}, {'time': .01, 'value': 0, 'curve': 'stepped'},
            {'time': .0101, 'value': 1}, {'time': 1, 'value': 1}]}}
        report = self.diagnose(options={**self.options, 'samples': 32})
        self.assertEqual(report['reason_code'], 'foot-attachment-not-opaque', report)

    def test_loop_break_is_reported_separately_from_stance(self):
        self.source['animations']['walk']['bones']['thigh']['translate'][-1]['x'] = 8
        report = self.diagnose()
        self.assertEqual(report['status'], 'assessed')
        self.assertTrue(report['checks']['ground_contact'])
        self.assertFalse(report['checks']['loop'])
        self.assertGreater(report['loop']['maximum_vertex_error'], 2.9)

    def test_collapsed_shoe_is_not_a_successful_contact_check(self):
        self.source['animations']['walk']['bones']['foot'] = {'scale': [
            {'time': 0, 'x': 1, 'y': 1}, {'time': .5, 'x': 0, 'y': 1}, {'time': 1, 'x': 1, 'y': 1}]}
        report = self.diagnose()
        self.assertEqual(report['reason_code'], 'degenerate-foot-transform')
        self.assertFalse(report['numerical_checks_passed'])

    def test_38_angle_timeline_is_legal_and_actual_sole_rotates(self):
        self.version = '3.8'
        self.source = self.fixture('3.8')
        self.source['animations']['walk']['bones']['foot'] = {'rotate': [
            {'time': 0, 'angle': 0}, {'time': .6, 'angle': 0},
            {'time': .8, 'angle': 8}, {'time': 1, 'angle': 0}]}
        report = self.diagnose()
        self.assertEqual(report['status'], 'assessed', report)
        self.assertTrue(report['numerical_checks_passed'], report)
        self.assertGreater(report['feet'][0]['sole_angle']['whole_cycle_range'], 7.5)

    def test_42_ignored_angle_field_is_not_a_successful_contact_check(self):
        self.source['animations']['walk']['bones']['foot'] = {'rotate': [
            {'time': 0, 'angle': 0}, {'time': .8, 'angle': 8}, {'time': 1, 'angle': 0}]}
        report = self.diagnose()
        self.assertEqual(report['reason_code'], 'rotate-field-version-mismatch')
        self.assertFalse(report['numerical_checks_passed'])

    def test_atlas_external_symlink_is_rejected_before_38_runtime_reads(self):
        self.version, self.source = '3.8', self.fixture('3.8')
        with tempfile.TemporaryDirectory() as outside:
            image = Path(outside) / 'secret.png'
            shutil.copy2(self.folder / 'page.png', image)
            (self.folder / 'page.png').unlink()
            (self.folder / 'page.png').symlink_to(image)
            with patch('app.contact_diagnostics.subprocess.run') as process:
                report = self.diagnose()
        self.assertEqual(report['reason_code'], 'resource-path-outside-export')
        process.assert_not_called()
        self.assertNotIn('secret.png', json.dumps(report))

    def test_budget_timeout_and_invalid_nonfinite_options_are_bounded(self):
        for options in ({**self.options, 'samples': 513}, {**self.options, 'samples': True},
                        {**self.options, 'ground_y': float('nan')}, {**self.options, 'alpha_threshold': 100}):
            with patch('app.contact_diagnostics.subprocess.run') as process:
                self.assertEqual(self.diagnose(options=options)['status'], 'not_assessed')
            process.assert_not_called()
        with patch('app.contact_diagnostics.subprocess.run', side_effect=subprocess.TimeoutExpired('node', 30)) as process:
            report = self.diagnose()
        self.assertEqual(report['reason_code'], 'diagnostic-timeout')
        self.assertEqual(process.call_args.kwargs['timeout'], 30)

    def test_resource_hash_and_read_only_contract(self):
        (self.folder / 'skeleton.json').write_text(json.dumps(self.source))
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        report = diagnose_contact(self.folder, options=self.options)
        after = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(report['source_sha256'], {name: hashlib.sha256(data).hexdigest() for name, data in before.items()})
        self.source['skeleton']['name'] = 'new-resource-version'
        changed = self.diagnose()
        self.assertNotEqual(report['source_sha256']['skeleton.json'], changed['source_sha256']['skeleton.json'])

    def test_known_template_prefers_separate_foot_slot_for_pixel_mapping(self):
        source = self.fixture()
        source['skeleton']['gait'] = {'version': 'sole-contact-v1', 'ground_y': .5, 'scene_speed': 10 / .6, 'stance': .6}
        source['bones'] = [{'name': 'root', 'y': 40}]
        source['slots'] = []
        source['skins'][0]['attachments'] = {}
        source['animations']['walk']['bones'] = {}
        for side in ('l', 'r'):
            source['bones'].extend([{'name': f'{side}-upper-leg', 'parent': 'root'},
                                    {'name': f'{side}-lower-leg', 'parent': f'{side}-upper-leg', 'y': -20},
                                    {'name': f'{side}-foot', 'parent': f'{side}-lower-leg', 'y': -20}])
            source['slots'].extend([{'name': f'{side}-upper-leg', 'bone': f'{side}-upper-leg'},
                                    {'name': f'{side}-foot', 'bone': f'{side}-foot', 'attachment': 'shoe'}])
            source['skins'][0]['attachments'][f'{side}-foot'] = self.fixture()['skins'][0]['attachments']['shoe-slot']
            source['animations']['walk']['bones'][f'{side}-upper-leg'] = self.fixture()['animations']['walk']['bones']['thigh']
        self.source = source
        # Right curve deliberately has the wrong phase; test mapping, not gait passing.
        report = self.diagnose(options={'samples': 32})
        self.assertEqual(report['status'], 'assessed', report)
        self.assertEqual([foot['slot'] for foot in report['feet']], ['l-foot', 'r-foot'])
        self.assertFalse(report['numerical_checks_passed'])

    def test_signed_left_template_and_advertised_foot_slots_use_one_compensating_speed(self):
        source = self.fixture()
        source['skeleton']['gait'] = {'version': 'sole-contact-v1', 'ground_y': .5,
                                      'scene_speed': -10 / .6, 'stance': .6, 'facing': 'left',
                                      'feet': {'l': {'slot': 'left-shoe'}, 'r': {'slot': 'right-shoe'}}}
        source['bones'] = [{'name': 'root', 'y': 40, 'scaleX': -1}]
        source['slots'] = []
        source['skins'][0]['attachments'] = {}
        source['animations']['walk']['bones'] = {}
        for side, slot in [('l', 'left-shoe'), ('r', 'right-shoe')]:
            source['bones'].extend([{'name': f'{side}-upper-leg', 'parent': 'root'},
                                    {'name': f'{side}-lower-leg', 'parent': f'{side}-upper-leg', 'y': -20},
                                    {'name': f'{side}-foot', 'parent': f'{side}-lower-leg', 'y': -20}])
            source['slots'].append({'name': slot, 'bone': f'{side}-foot', 'attachment': 'shoe'})
            source['skins'][0]['attachments'][slot] = self.fixture()['skins'][0]['attachments']['shoe-slot']
            source['animations']['walk']['bones'][f'{side}-upper-leg'] = self.fixture()['animations']['walk']['bones']['thigh'] if side == 'l' else {'translate': [
                {'time': 0, 'x': -10 / 3, 'y': 0}, {'time': .1, 'x': -5, 'y': 0},
                {'time': .3, 'x': 0, 'y': 10}, {'time': .5, 'x': 5, 'y': 0},
                {'time': 1, 'x': -10 / 3, 'y': 0}]}
        self.source = source
        report = self.diagnose(options={'samples': 64})
        self.assertEqual(report['status'], 'assessed', report)
        self.assertTrue(report['numerical_checks_passed'], report)
        self.assertLess(report['scene_speed'], 0)
        self.assertEqual([foot['slot'] for foot in report['feet']], ['left-shoe', 'right-shoe'])
        wrong = self.diagnose(options={'samples': 64, 'scene_speed': -source['skeleton']['gait']['scene_speed']})
        self.assertFalse(wrong['checks']['shared_scene_speed'])

    def test_advertised_mapping_with_incomplete_foot_still_not_assessed(self):
        self.source['skeleton']['gait'] = {'version': 'sole-contact-v1', 'ground_y': .5,
                                          'scene_speed': 10 / .6, 'stance': .6,
                                          'feet': {'l': {'slot': 'shoe-slot'}}}
        # Explicit mapping cannot turn upper-leg-owned pixels into a complete shoe.
        self.source['slots'][0]['bone'] = 'thigh'
        report = self.diagnose()
        self.assertEqual(report['reason_code'], 'insufficient-rigid-foot-pixels')

    def test_ankle_fallback_metadata_never_turns_into_an_automatic_pixel_pass(self):
        self.source['skeleton']['gait'] = {'version': 'sole-contact-v1', 'ground_y': .5,
                                          'scene_speed': 10 / .6, 'stance': .6,
                                          'feet': {'l': {'source': 'ankle-fallback'}}}
        with patch('app.contact_diagnostics.subprocess.run') as process:
            report = self.diagnose(options={'samples': 64})
        self.assertEqual(report['reason_code'], 'unverified-contact-profile')
        process.assert_not_called()
        # Explicit known mapping still must prove actual opaque, foot-owned pixels.
        explicit = self.diagnose()
        self.assertEqual(explicit['status'], 'assessed')
        self.assertTrue(explicit['numerical_checks_passed'])

    @unittest.skipUnless((FINAL / 'skeleton.json').is_file(), 'Real accepted side-view export not available')
    def test_real_weighted_final_export_passes_contact_but_does_not_claim_c1_or_natural_gait(self):
        report = diagnose_contact(FINAL, options={'samples': 128})
        self.assertEqual(report['status'], 'assessed', report)
        self.assertTrue(report['numerical_checks_passed'], report)
        for foot in report['feet']:
            self.assertLess(foot['support']['sole_height_drift'], .002)
            self.assertLess(foot['support']['normalized_slip_range'], .00001)
            self.assertLess(foot['support']['persistent_contact_pixels']['normalized_maximum_velocity_error'], .0005)
            self.assertGreater(foot['velocity_difference']['normalized_maximum'], .1,
                               'A loop/contact pass must not erase actual transition-speed differences')
            self.assertLess(foot['arm_foot_correlation'], -.8)
        self.assertTrue(report['limits']['numerical_only'])


if __name__ == '__main__':
    unittest.main()
