import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_NODE = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
NODE = shutil.which('node') or (str(BUNDLED_NODE) if BUNDLED_NODE.exists() else None)
TOOL = ROOT / 'tools/validate_agent_runtime.cjs'


@unittest.skipUnless(NODE, 'Official runtime validation requires Node')
class RuntimeMotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        for source in (ROOT / 'demo').iterdir():
            if source.is_file():
                shutil.copy2(source, self.folder / source.name)
        self.source = json.loads((self.folder / 'robot.json').read_text())
        self.skeleton_name = 'robot.json'
        self.atlas_name = 'robot.atlas'
        self.version = '4.2'

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self, folder=None, preview=None):
        folder = Path(folder or self.folder)
        if folder == self.folder:
            (folder / self.skeleton_name).write_text(json.dumps(self.source))
        command = [NODE, str(TOOL), str(folder), self.version, self.skeleton_name, self.atlas_name]
        if preview:
            command.extend([preview, json.dumps({'frame_count': 8})])
        result = subprocess.run(command, text=True, capture_output=True, timeout=30)
        self.assertTrue(result.stdout.strip(), result.stderr)
        return result.returncode, json.loads(result.stdout)

    @staticmethod
    def clip(report, name):
        return next(clip for clip in report['motion']['clips'] if clip['animation'] == name)

    def test_spine42_nonzero_angle_without_value_is_rejected_before_runtime_load(self):
        self.source['animations'] = {'bad-rotation': {'bones': {'head': {'rotate': [
            {'time': 0, 'angle': 0}, {'time': .5, 'angle': 30}, {'time': 1, 'angle': 0}
        ]}}}}
        # Semantic diagnosis must not depend on a successfully loaded atlas.
        (self.folder / self.atlas_name).unlink()
        code, report = self.validate()
        self.assertNotEqual(code, 0)
        self.assertFalse(report['passed'])
        issue = report['diagnostics'][0]
        self.assertEqual(issue['code'], 'rotate-field-version-mismatch')
        self.assertEqual(issue['animation'], 'bad-rotation')
        self.assertEqual(issue['bone'], 'head')
        self.assertEqual(issue['keyframe'], 1)
        self.assertEqual(issue['providedField'], 'angle')
        self.assertEqual(issue['expectedField'], 'value')

    def test_value_takes_precedence_and_constant_pose_is_allowed(self):
        self.source['animations'] = {'pose': {'bones': {'head': {'rotate': [
            {'time': 0, 'value': 15, 'angle': 90}, {'time': 1, 'value': 15, 'angle': 90}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0)
        self.assertTrue(report['passed'])
        clip = self.clip(report, 'pose')
        self.assertFalse(clip['moving'])
        self.assertFalse(clip['visibleMotionObserved'])
        self.assertFalse(clip['rootRelativeGeometryMotionObserved'])
        self.assertEqual(clip['diagnostic'], 'static-runtime-clip')
        self.assertEqual(clip['geometryMaxDisplacement'], 0)

    def test_zero_angle_noop_does_not_trigger_a_false_semantic_error(self):
        self.source['animations'] = {'pose': {'bones': {'head': {'rotate': [
            {'time': 0, 'angle': 0}, {'time': 1, 'angle': 0}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0)
        self.assertFalse(self.clip(report, 'pose')['moving'])

    def test_spine38_angle_is_legal_and_official_runtime_observes_motion(self):
        legacy = ROOT / 'evidence/rerig/region38-rig'
        self.assertTrue((legacy / 'skeleton.json').exists())
        for source in legacy.iterdir():
            if source.is_file() and source.suffix in ('.json', '.atlas', '.png'):
                shutil.copy2(source, self.folder / source.name)
        self.version, self.skeleton_name, self.atlas_name = '3.8', 'skeleton.json', 'skeleton.atlas'
        self.source = json.loads((self.folder / self.skeleton_name).read_text())
        self.source['animations'] = {'legacy-rotation': {'bones': {'head': {'rotate': [
            {'time': 0, 'angle': 0}, {'time': .5, 'angle': 30}, {'time': 1, 'angle': 0}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0, report)
        clip = self.clip(report, 'legacy-rotation')
        self.assertTrue(clip['moving'])
        self.assertTrue(clip['visibleMotionObserved'])
        self.assertTrue(clip['articulatedMotionObserved'])
        self.assertTrue(clip['rootRelativeGeometryMotionObserved'])
        self.assertTrue(clip['loop']['seamless'])

    def test_root_translation_is_separate_from_articulated_walking(self):
        self.source['animations'] = {'floating-statue': {'bones': {'root': {'translate': [
            {'time': 0, 'x': 0}, {'time': .5, 'x': 40}, {'time': 1, 'x': 0}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0)
        clip = self.clip(report, 'floating-statue')
        self.assertTrue(clip['moving'])
        self.assertTrue(clip['visibleMotionObserved'])
        self.assertFalse(clip['articulatedMotionObserved'])
        self.assertFalse(clip['rootRelativeGeometryMotionObserved'])
        self.assertTrue(clip['rootRelativeGeometryComparable'])
        self.assertTrue(clip['rootOnlyMotion'])
        self.assertGreater(clip['geometryMaxDisplacement'], 39)
        self.assertEqual(clip['diagnostic'], 'root-only-runtime-motion')
        self.assertTrue(clip['loop']['seamless'])

    def test_zero_length_bone_rotation_uses_runtime_matrix_not_fake_endpoints(self):
        head = next(bone for bone in self.source['bones'] if bone['name'] == 'head')
        head['length'] = 0
        self.source['animations'] = {'turn': {'bones': {'head': {'rotate': [
            {'time': 0, 'value': 0}, {'time': .5, 'value': 30}, {'time': 1, 'value': 0}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0)
        clip = self.clip(report, 'turn')
        motion = next(bone for bone in clip['bones'] if bone['name'] == 'head')
        self.assertEqual(motion['endRangeX'], 0)
        self.assertEqual(motion['endRangeY'], 0)
        self.assertGreater(motion['worldDirectionRangeDegrees'], 29)
        self.assertGreater(motion['parentRelativeDirectionRangeDegrees'], 29)
        self.assertTrue(clip['visibleMotionObserved'])
        self.assertTrue(clip['articulatedMotionObserved'])
        self.assertTrue(clip['rootRelativeGeometryMotionObserved'])

    def test_root_transform_and_unused_articulation_do_not_create_rendered_limb_motion(self):
        self.source['bones'].append({'name': 'unused-helper', 'parent': 'root', 'length': 10})
        self.source['animations'] = {'walk': {'bones': {
            'root': {
                'translate': [{'time': 0, 'x': 0, 'y': 0}, {'time': .5, 'x': 80, 'y': 20}, {'time': 1, 'x': 0, 'y': 0}],
                'rotate': [{'time': 0, 'value': 0}, {'time': .5, 'value': 35}, {'time': 1, 'value': 0}],
                'scale': [{'time': 0, 'x': 1, 'y': 1}, {'time': .5, 'x': 1.4, 'y': .8}, {'time': 1, 'x': 1, 'y': 1}]},
            'unused-helper': {'rotate': [{'time': 0, 'value': 0}, {'time': .5, 'value': 30}, {'time': 1, 'value': 0}]}
        }}}
        code, report = self.validate()
        self.assertEqual(code, 0, report)
        self.assertTrue(report['passed'], 'A loadable clip remains valid at the general runtime level')
        clip = self.clip(report, 'walk')
        self.assertTrue(clip['visibleMotionObserved'])
        self.assertTrue(clip['articulatedMotionObserved'])
        self.assertGreater(clip['geometryMaxDisplacement'], 50)
        self.assertEqual(clip['rootBone'], 'root')
        self.assertTrue(clip['rootRelativeGeometryComparable'])
        self.assertFalse(clip['rootRelativeGeometryMotionObserved'])
        self.assertLess(clip['rootRelativeGeometryMaxDisplacement'], 1e-4)
        self.assertEqual(clip['diagnostic'], 'articulation-with-root-only-render-motion')

    def test_rotation_of_an_unused_helper_is_not_reported_as_visible_motion(self):
        self.source['bones'].append({'name': 'unused-helper', 'parent': 'root', 'length': 10})
        self.source['animations'] = {'helper-only': {'bones': {'unused-helper': {'rotate': [
            {'time': 0, 'value': 0}, {'time': .5, 'value': 30}, {'time': 1, 'value': 0}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0)
        clip = self.clip(report, 'helper-only')
        self.assertTrue(clip['skeletalMotionObserved'])
        self.assertFalse(clip['visibleMotionObserved'])
        self.assertFalse(clip['geometryMotionObserved'])
        self.assertEqual(clip['diagnostic'], 'skeletal-motion-with-static-render')

    def test_short_motion_between_uniform_samples_is_observed_at_real_keyframes(self):
        self.source['animations'] = {'brief-turn': {'bones': {'head': {'rotate': [
            {'time': 0, 'value': 0}, {'time': .01, 'value': 30},
            {'time': .02, 'value': 0}, {'time': 1, 'value': 0}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0)
        clip = self.clip(report, 'brief-turn')
        self.assertTrue(clip['visibleMotionObserved'])
        self.assertGreater(clip['samples'], 33)
        self.assertEqual(clip['sampling']['coverage'], 'uniform-and-keyframes')
        self.assertEqual(clip['sampling']['includedKeyframeTimes'], clip['sampling']['keyframeTimes'])
        head = next(bone for bone in clip['bones'] if bone['name'] == 'head')
        self.assertGreater(head['worldDirectionRangeDegrees'], 29)

    def test_nonlooping_motion_is_allowed_and_reports_its_endpoint_difference(self):
        self.source['animations'] = {'move-once': {'bones': {'root': {'translate': [
            {'time': 0, 'x': 0}, {'time': 1, 'x': 40}
        ]}}}}
        code, report = self.validate()
        self.assertEqual(code, 0)
        clip = self.clip(report, 'move-once')
        self.assertTrue(clip['moving'])
        self.assertFalse(clip['loop']['seamless'])
        self.assertGreater(clip['loop']['maximumVertexError'], 39)
        self.assertGreater(clip['loop']['maximumBoneError'], 39)

    def test_real_blue_warrior_final_export_has_visible_articulated_closed_walk(self):
        folder = ROOT / 'outputs/blue-coat-warrior-flare-20261010/final-export'
        self.assertTrue((folder / 'skeleton.json').exists(), 'This task requires the actual delivered export')
        self.skeleton_name, self.atlas_name = 'skeleton.json', 'skeleton.atlas'
        code, report = self.validate(folder)
        self.assertEqual(code, 0, report)
        clip = self.clip(report, 'walk')
        self.assertTrue(clip['moving'])
        self.assertTrue(clip['visibleMotionObserved'])
        self.assertTrue(clip['articulatedMotionObserved'])
        self.assertTrue(clip['rootRelativeGeometryMotionObserved'])
        self.assertFalse(clip['rootOnlyMotion'])
        self.assertGreater(clip['geometryMaxDisplacement'], 50)
        self.assertGreater(clip['rootRelativeGeometryMaxDisplacement'], 50)
        self.assertTrue(clip['loop']['seamless'])
        for name in ('foot-left', 'foot-right'):
            foot = next(bone for bone in clip['bones'] if bone['name'] == name)
            self.assertGreater(foot['originRangeX'], 60)
            self.assertGreater(foot['originRangeY'], 20)


if __name__ == '__main__':
    unittest.main()
