"""Delivery checks use real official-runtime motion, without provider or network calls."""
import base64
import copy
import io
import json
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from app.agent_tasks import AgentTasks, frame_count, require_requested_motion, resource_digest
from app.character import CharacterProject
from app.core import Project, UserError, png_bytes
from app.rig import RigProject
from app.rig_automation import RigAutomation


ROOT = Path(__file__).resolve().parents[1]


class AgentMotionGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name)
        source = Project(self.storage / 'projects' / uuid.uuid4().hex,
                         {p.name: p.read_bytes() for p in (ROOT / 'demo').iterdir() if p.is_file()}, 'fixture')
        original = RigProject.create(self.storage / 'rigs' / uuid.uuid4().hex, source)
        self.loaders = {'rig': lambda entity: RigProject(self.storage / 'rigs' / entity)}
        self.tasks = AgentTasks(self.storage, ROOT, self.loaders, RigAutomation(self.storage, self.loaders['rig']))
        self.tasks.api = self.api
        self.task = self.tasks.create({'nonce': uuid.uuid4().hex, 'requirements': '制作待机和行走动画',
                                      'sources': [{'kind': 'rig', 'id': original.id}], 'max_generations': 0})
        self.entity = self.task['sources'][0]['id']
        self.key = 'rig:' + self.entity
        self.original_runtime = self.tasks.runtime_check
        self.reports = {}
        self.tasks.runtime_check = self.runtime

    def tearDown(self):
        self.tmp.cleanup()

    def api(self, route, data=None, binary=False):
        bits = route.strip('/').split('/')
        self.assertEqual(bits[1], 'rigs')
        self.assertEqual(bits[3], 'export')
        return self.loaders['rig'](bits[2]).export()

    def runtime(self, content, preview_clip=None, count=8, gait=None):
        # Geometry/motion comes from the real official runtime. Rendering the image
        # grid is covered separately; a small PNG avoids repeating that cost here.
        frame_count(count)
        digest = resource_digest(content)
        if digest not in self.reports:
            self.reports[digest] = self.original_runtime(content)
        report = copy.deepcopy(self.reports[digest])
        if preview_clip:
            self.assertIn(preview_clip, ['__setup__'] + report['animations'])
            return report, None if gait is not None else png_bytes(Image.new('RGBA', (2, 2)))
        return report

    def set_clip(self, clip, name='walk', bones=None):
        rig = self.loaders['rig'](self.entity)
        state = copy.deepcopy(rig.state)
        state['skeleton']['animations'] = {name: clip}
        if bones:
            state['skeleton']['bones'].extend(bones)
        rig.commit(state, 'test independent fixture')

    def preview(self, name='walk', count=16):
        return self.tasks.tool(self.task['id'], 'spine_motion',
                               {'kind': 'rig', 'id': self.entity, 'animation': name, 'frame_count': count})

    def finish(self):
        # Exercise the real closeup renderer independently from this suite's
        # cached motion-report adapter before testing the motion gate itself.
        runtime=self.tasks.runtime_check
        self.tasks.runtime_check=self.original_runtime
        try:
            for clip in self.loaders['rig'](self.entity).state['skeleton']['animations']:
                self.tasks.tool(self.task['id'],'spine_closeup',{'kind':'rig','id':self.entity,'animation':clip})
                self.tasks.tool(self.task['id'],'spine_review_closeup',{'kind':'rig','id':self.entity,'animation':clip,'verdict':'ready','reason':'真实fixture放大图已查看；连接验收与运动判定分开'})
        finally:self.tasks.runtime_check=runtime
        return self.tasks.finish(self.task['id'], {'outcome': 'completed', 'summary': 'fixture',
                                 'artifacts': [{'kind': 'rig', 'id': self.entity}]})

    def motion(self, name='walk'):
        report = self.runtime(self.loaders['rig'](self.entity).export())
        return next(clip for clip in report['motion']['clips'] if clip['animation'] == name)

    def test_static_walk_is_loadable_but_cannot_be_delivered_as_walking(self):
        self.set_clip({'bones': {'head': {'rotate': [{'time': 0, 'value': 0}, {'time': 1, 'value': 0}]}}})
        self.preview(count=32)
        self.assertFalse(self.motion()['visibleMotionObserved'])
        with self.assertRaises(UserError):
            self.finish()
        self.assertNotEqual(self.tasks.read(self.task['id'])['state'], 'completed')
        self.assertFalse((self.tasks.path(self.task['id']).parent / 'result.zip').exists())

    def test_only_root_translation_is_not_articulated_walking(self):
        self.set_clip({'bones': {'root': {'translate': [
            {'time': 0, 'x': 0}, {'time': .5, 'x': 40}, {'time': 1, 'x': 0}]}}})
        self.preview(count=32)
        motion = self.motion()
        self.assertTrue(motion['visibleMotionObserved'])
        self.assertFalse(motion['articulatedMotionObserved'])
        with self.assertRaises(UserError):
            self.finish()

    def test_unused_helper_has_bone_motion_but_no_character_motion(self):
        self.set_clip({'bones': {'head-helper': {'rotate': [
            {'time': 0, 'value': 0}, {'time': .5, 'value': 30}, {'time': 1, 'value': 0}]}}},
                      bones=[{'name': 'head-helper', 'parent': 'root', 'length': 10}])
        self.preview(count=32)
        motion = self.motion()
        self.assertTrue(motion['articulatedMotionObserved'])
        self.assertFalse(motion['visibleMotionObserved'])
        with self.assertRaises(UserError):
            self.finish()

    def test_root_movement_and_unused_helper_cannot_jointly_fake_walking(self):
        self.set_clip({'bones': {
            'root': {'translate': [{'time': 0, 'x': 0}, {'time': .5, 'x': 40}, {'time': 1, 'x': 0}]},
            'head-helper': {'rotate': [{'time': 0, 'value': 0}, {'time': .5, 'value': 30}, {'time': 1, 'value': 0}]}}},
            bones=[{'name': 'head-helper', 'parent': 'root', 'length': 10}])
        self.preview(count=32)
        motion = self.motion()
        self.assertTrue(motion['visibleMotionObserved'])
        self.assertTrue(motion['articulatedMotionObserved'])
        self.assertFalse(motion['rootRelativeGeometryMotionObserved'])
        with self.assertRaises(UserError):
            self.finish()
        self.assertNotEqual(self.tasks.read(self.task['id'])['state'], 'completed')

    def test_real_walking_can_complete_and_each_clip_keeps_its_largest_preview(self):
        self.preview('walk', 32)
        self.preview('walk', 8)
        self.preview('idle', 8)
        review = self.tasks.read(self.task['id'])['motion_checks'][self.key]
        self.assertEqual(review['frame_counts'], {'walk': 32, 'idle': 8})
        self.finish()
        self.assertEqual(self.tasks.read(self.task['id'])['state'], 'completed')
        with zipfile.ZipFile(io.BytesIO(self.tasks.download(self.task['id']))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        walk = next(clip for clip in manifest['artifacts'][0]['runtime_validation']['motion']['clips']
                    if clip['animation'] == 'walk')
        self.assertTrue(walk['visibleMotionObserved'])
        self.assertTrue(walk['articulatedMotionObserved'])

    def test_fewer_than_16_walking_frames_cannot_complete(self):
        self.preview('walk', 15)
        self.preview('idle', 32)
        with self.assertRaises(UserError):
            self.finish()
        self.assertEqual(self.tasks.read(self.task['id'])['motion_checks'][self.key]['frame_counts']['walk'], 15)

    def test_old_digest_cannot_lend_frame_count_to_a_new_revision(self):
        self.preview('walk', 32)
        self.preview('idle', 16)
        old = self.tasks.read(self.task['id'])['motion_checks'][self.key]['sha256']
        rig = self.loaders['rig'](self.entity)
        state = copy.deepcopy(rig.state)
        state['skeleton']['bones'][0]['x'] = 3
        rig.commit(state, 'changed independent version')
        self.preview('walk', 8)
        review = self.tasks.read(self.task['id'])['motion_checks'][self.key]
        self.assertNotEqual(old, review['sha256'])
        self.assertEqual(review['animations'], ['walk'])
        self.assertEqual(review['frame_counts'], {'walk': 8})
        self.preview('idle', 16)
        with self.assertRaises(UserError):
            self.finish()

    def test_finish_detects_resource_changes_and_clears_stale_preview_credit(self):
        self.preview('walk', 32)
        self.preview('idle', 16)
        rig = self.loaders['rig'](self.entity)
        state = copy.deepcopy(rig.state)
        state['skeleton']['bones'][0]['x'] = 3
        rig.commit(state, 'changed after preview')
        with self.assertRaises(UserError):
            self.finish()
        review = self.tasks.read(self.task['id'])['motion_checks'][self.key]
        self.assertEqual(review['frame_counts'], {})
        self.assertEqual(review['animations'], [])
        self.assertEqual(review['sha256'], resource_digest(rig.export()))

    def test_missing_motion_report_is_not_silently_grandfathered_in(self):
        self.preview('walk', 32)
        self.preview('idle', 8)
        runtime = self.tasks.runtime_check
        def missing_motion(*args, **kwargs):
            report = runtime(*args, **kwargs)
            report.pop('motion')
            return report
        self.tasks.runtime_check = missing_motion
        with self.assertRaises(UserError):
            self.finish()

    def test_static_pose_without_a_walking_request_remains_deliverable(self):
        self.tasks.update(self.task['id'], requirements='保留人物当前姿势，输出静态待机')
        self.set_clip({'bones': {'head': {'rotate': [{'time': 0, 'value': 15}, {'time': 1, 'value': 15}]}}}, 'pose')
        self.preview('pose', 8)
        self.assertFalse(self.motion('pose')['visibleMotionObserved'])
        self.finish()
        self.assertEqual(self.tasks.read(self.task['id'])['state'], 'completed')

    def test_non_looping_one_shot_action_is_not_globally_rejected(self):
        self.tasks.update(self.task['id'], requirements='制作一次性的点头动作，保留原贴图')
        self.set_clip({'bones': {'head': {'rotate': [{'time': 0, 'value': 0}, {'time': 1, 'value': 25}]}}}, 'nod')
        self.preview('nod', 8)
        self.assertFalse(self.motion('nod')['loop']['seamless'])
        self.finish()
        self.assertEqual(self.tasks.read(self.task['id'])['state'], 'completed')

    def test_gait_numbers_alone_do_not_record_a_viewed_image_preview(self):
        self.tasks.tool(self.task['id'], 'spine_gait', {'kind': 'rig', 'id': self.entity, 'animation': 'walk',
                                                      'frame_count': 32, 'pairs': []})
        self.assertNotIn(self.key, self.tasks.read(self.task['id'])['motion_checks'])

    def test_context_advertises_available_worker_capabilities(self):
        context = self.tasks.tool(self.task['id'], 'spine_context', {})
        self.assertTrue({'runtime-motion-diagnostics', 'reviewed-part-layout', 'continuous-limb-mesh'} <= set(context['capabilities']))


class MotionSummaryContractTests(unittest.TestCase):
    @staticmethod
    def report(name='walk'):
        return {'animations': [name], 'skins': 2, 'motion': {'clips': [
            {'animation': name, 'skin': skin, 'visibleMotionObserved': True, 'articulatedMotionObserved': True,
             'rootRelativeGeometryMotionObserved': True}
            for skin in ('default', 'winter')]}}

    def test_every_skin_must_have_visible_and_articulated_motion(self):
        review = {'frame_counts': {'walk': 16}}
        report = self.report()
        require_requested_motion('制作行走动画', report, review)
        for flag in ('visibleMotionObserved', 'articulatedMotionObserved', 'rootRelativeGeometryMotionObserved'):
            with self.subTest(flag=flag):
                broken = copy.deepcopy(report)
                broken['motion']['clips'][1][flag] = False
                with self.assertRaises(UserError):
                    require_requested_motion('制作行走动画', broken, review)

    def test_legacy_motion_summary_missing_rendered_relative_motion_is_not_enough(self):
        report = self.report()
        for skin in report['motion']['clips']:
            skin.pop('rootRelativeGeometryMotionObserved')
        with self.assertRaises(UserError):
            require_requested_motion('制作行走动画', report, {'frame_counts': {'walk': 32}})

    def test_missing_or_duplicate_skin_reports_do_not_satisfy_all_skins(self):
        report = self.report()
        for clips in (report['motion']['clips'][:1], [report['motion']['clips'][0]] * 2):
            with self.subTest(clips=clips):
                with self.assertRaises(UserError):
                    require_requested_motion('制作行走动画', {**report, 'motion': {'clips': clips}},
                                              {'frame_counts': {'walk': 32}})

    def test_explicit_english_and_chinese_locomotion_names_are_checked(self):
        for requirement in ('制作走路动作', '制作奔跑动画', 'Create a WALK animation', 'Create a running clip'):
            for name in ('walk', 'Walking', 'RUN', 'running'):
                with self.subTest(requirement=requirement, name=name):
                    report = self.report(name)
                    report['motion']['clips'][0]['visibleMotionObserved'] = False
                    with self.assertRaises(UserError):
                        require_requested_motion(requirement, report, {'frame_counts': {name: 32}})

    def test_unrelated_pose_and_non_locomotion_requests_do_not_require_locomotion_motion(self):
        require_requested_motion('制作待机与点头动作', {'animations': ['walk'], 'skins': 1}, {})
        require_requested_motion('制作行走和静态预备姿势', {'animations': ['pose'], 'skins': 1}, {})


class WorkerPartValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name)
        loaders = {'character': lambda entity: CharacterProject(self.storage / 'characters' / entity)}
        self.tasks = AgentTasks(self.storage, ROOT, loaders, None)
        image = Image.new('RGBA', (256, 512))
        image.paste('blue', (60, 40, 190, 480))
        self.task = self.tasks.create({'nonce': uuid.uuid4().hex, 'requirements': '补全双臂制作行走动画',
                                      'image': base64.b64encode(png_bytes(image)).decode(),
                                      'max_generations': 3})
        self.entity = self.task['sources'][0]['id']
        c = loaders['character'](self.entity)
        self.tasks.update(self.task['id'], image_assessments={self.entity: {
            'image_id': c.meta['image_id'], 'revision': c.meta['revision'], 'verdict': 'ready',
            'camera_view':'three-quarter-side','direction_consistent':True,'target_actions':['walk']}})
        self.payload = {'nonce': uuid.uuid4().hex, 'revision': c.meta['revision'], 'sheets': ['arms']}
        self.tasks.api = Mock(return_value={'jobs': []})
        self.tasks.reserve = Mock(wraps=self.tasks.reserve)

    def tearDown(self):
        self.tmp.cleanup()

    def generate(self, data):
        return self.tasks.tool(self.task['id'], 'spine_character',
                               {'kind': 'character', 'id': self.entity, 'action': 'generate-parts', 'data': data})

    def test_invalid_notes_or_include_back_do_not_reserve_paid_generation(self):
        for invalid in ({'instructions_by_sheet': {'arms': 'x' * 401}},
                        {'instructions_by_sheet': {'legs': []}},
                        {'instructions_by_sheet': {'feet': 'shoes'}},
                        {'instructions_by_sheet': {'arms': 'sleeves'}, 'instructions': 'all parts'},
                        {'instructions': 'x' * 1201}, {'include_back': None}, {'include_back': 1},
                        {'include_back': 'false'}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(UserError):
                    self.generate({**self.payload, **invalid})
                self.assertEqual(self.tasks.read(self.task['id'])['reservations'], {})
        self.tasks.reserve.assert_not_called()
        self.tasks.api.assert_not_called()

    def test_valid_scoped_plan_reserves_only_requested_parts_once(self):
        data = {**self.payload, 'instructions_by_sheet': {'arms': '蓝色短袖与裸露手掌'}, 'include_back': False}
        self.generate(data)
        self.assertEqual(self.tasks.summary(self.task['id'])['generation_budget']['used_or_reserved'], 1)
        self.tasks.api.assert_called_once_with('/api/characters/' + self.entity + '/generate-parts', data)


if __name__ == '__main__':
    unittest.main()
