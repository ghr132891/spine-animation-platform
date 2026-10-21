"""Combined costume generation and real rigging stay aligned with reviewed assets."""
import copy
import json
import math
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import ImageDraw

from app.agent_tasks import AgentTasks, require_requested_motion, resource_digest
from app.character import CharacterProject
from app.core import UserError, png_bytes
from app.jobs import JobHistory
from app.part_prompts import generation_plan
from app.provider import Matrix
from app.rig import RigProject
from tests.test_asset_sheet_workflow import assembly_guide, asset_fixture


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node') or str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')


def combined_fixture():
    image, layout = asset_fixture()
    draw = ImageDraw.Draw(image)
    draw.rectangle((160, 10, 440, 195), fill=(0, 0, 0, 0))
    draw.polygon([(195, 25), (250, 25), (255, 105), (283, 184),
                  (172, 184), (190, 105)], fill='#196da4')
    draw.rectangle((206, 60, 237, 110), fill='white')
    draw.line((187, 128, 264, 128), fill='#a43a29', width=4)
    parts = [copy.deepcopy(item) for item in layout if item['name'] != 'pelvis']
    torso = next(item for item in parts if item['name'] == 'torso')
    torso.update(box=[165, 15, 290, 195], group='costume',
                 start_pin=[.5, .62], end_pin=[.5, .08])
    return image, parts


class ReferenceGenerationTests(unittest.TestCase):
    def setUp(self):
        from app import server
        self.server = server
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        self.matrix = Matrix(self.storage)
        self.addCleanup(self.matrix.tunnel.close)
        self.matrix.request = Mock(return_value={'data': {'gen_id': 'mock-combined-generation'}})
        self.matrix.download_result = Mock(side_effect=AssertionError('No real candidate downloads'))
        self.character = CharacterProject.create(self.storage, 'combined request fixture')
        image, _ = combined_fixture()
        self.character.set_image(png_bytes(image), 0)
        self.character.meta['settings']['facing'] = 'left'
        self.history = JobHistory(self.storage, self.matrix, 'characters')
        self.handler = object.__new__(server.Handler)
        self.handler.path = '/api/characters/' + self.character.id + '/generate-parts'
        self.handler.send = lambda value, *args, **kwargs: value
        self.reference = Mock(return_value='https://assets.example/combined-source.png')

    def request(self, **changes):
        return {'nonce': uuid.uuid4().hex, 'revision': self.character.meta['revision'],
                'sheets': ['character-assets'], 'body_layout': 'combined', **changes}

    def post(self, payload):
        with patch.object(self.server, 'get_character', return_value=self.character), \
                patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(self.server, 'CHARACTER_JOBS', self.history), \
                patch.object(self.server, 'character_reference', self.reference):
            return self.handler.post(payload)

    def test_combined_layout_keeps_one_costume_and_counts_six_or_seven_assets(self):
        for include_back in (False, True):
            with self.subTest(include_back=include_back):
                plan = generation_plan({'mode': 'complete-parts', 'sheets': ['character-assets'],
                                        'body_layout': 'combined', 'include_back': include_back})
                item = plan['items'][0]
                expected = ['head', 'torso'] + (['back'] if include_back else []) + [
                    'l-upper-arm', 'r-upper-arm', 'l-upper-leg', 'r-upper-leg']
                self.assertEqual(item['parts'], expected)
                self.assertEqual(len(item['parts']), 7 if include_back else 6)
                self.assertEqual(item['groups'], {'torso': 'costume'})
                self.assertEqual(item['body_layout'], 'combined')
                self.assertEqual((item['columns'], item['rows'], item['ratio']), (4, 2, '3:2'))
                self.assertTrue(item['requires_reviewed_layout'])
                self.assertEqual(plan['image_count'], 1)
                self.assertIn('ONE continuous main costume from the reference neckline through waist and hem', item['prompt'])
                self.assertNotIn('waist/pelvis costume', item['prompt'])

    def test_plan_provider_request_and_server_job_use_identical_combined_contract(self):
        notes = '保留原脸与单侧袖章，整块衣装连同腰带和衣摆，原图左右一致。'
        for include_back in (False, True):
            with self.subTest(include_back=include_back):
                payload = self.request(include_back=include_back,
                                       instructions_by_sheet={'character-assets': notes})
                item = generation_plan({**payload, 'mode': 'complete-parts'}, 'left')['items'][0]
                self.matrix.request.reset_mock()
                self.matrix.generate_part_sheet('character-assets', self.reference.return_value,
                                                notes, 'left', include_back, 'combined')
                provider_wire = self.matrix.request.call_args.args[1]
                self.assertEqual(provider_wire['input']['prompt'], item['prompt'])
                self.assertEqual(provider_wire['input']['aspect_ratio'], item['ratio'])
                self.matrix.request.reset_mock()
                job = self.post(payload)[0]
                self.matrix.request.assert_called_once()
                route, wire = self.matrix.request.call_args.args
                self.assertIn('/gpt-image-2.5-flare/generate', route)
                self.assertEqual(wire, provider_wire)
                self.assertEqual(wire['request_num'], 1)
                self.assertEqual(wire['input']['input_images'], [self.reference.return_value])
                self.assertEqual(job['prompt'], item['prompt'])
                self.assertEqual(job['parts'], item['parts'])
                self.assertEqual(job['groups'], {'torso': 'costume'})
                self.assertEqual(job['body_layout'], 'combined')
                self.assertEqual(job['include_back'], include_back)
                self.assertTrue(job['requires_reviewed_layout'])
                self.assertEqual(json.loads(self.history.path(self.character.id, job['id']).read_text()), job)

    def test_invalid_body_layout_fails_before_reference_publication_or_paid_calls(self):
        before = copy.deepcopy(self.character.meta)
        for value in (None, True, False, '', 'whole', 0, [], {}):
            with self.subTest(body_layout=value):
                with self.assertRaises(UserError):
                    generation_plan({'mode': 'complete-parts', 'sheets': ['character-assets'],
                                     'body_layout': value})
                with self.assertRaises(UserError):
                    self.matrix.generate_part_sheet('character-assets', self.reference.return_value,
                                                    body_layout=value)
                with self.assertRaises(UserError):
                    self.post(self.request(body_layout=value))
        self.reference.assert_not_called()
        self.matrix.request.assert_not_called()
        self.assertEqual(self.character.meta, before)
        self.assertEqual(list((self.character.directory / 'jobs').glob('*.json')), [])

    def test_front_camera_keeps_both_shoes_in_the_reference_camera(self):
        for notes in ('Preserve the front view and original chef costume.', '保留原厨师正面视角与整块衣装。'):
            for facing in ('right', 'left'):
                with self.subTest(notes=notes, facing=facing):
                    item = generation_plan({'mode': 'complete-parts', 'sheets': ['character-assets'],
                                            'body_layout': 'combined', 'include_back': False,
                                            'instructions_by_sheet': {'character-assets': notes}}, facing)['items'][0]
                    self.assertNotIn('shoes pointing RIGHT', item['prompt'])
                    self.assertNotIn('shoes pointing LEFT', item['prompt'])
                    self.assertNotIn('两鞋均朝右', item['prompt'])
                    self.assertNotIn('两鞋均朝左', item['prompt'])
                    self.assertIn('retain the specified reference camera' if item['prompt_language'] == 'en'
                                  else '两鞋保持指定参考视角', item['prompt'])


    def test_omitted_fields_submit_two_complete_default_sheets_once(self):
        payload={'nonce':uuid.uuid4().hex,'revision':self.character.meta['revision']}
        result=self.post(payload)
        self.assertEqual([j['sheet'] for j in result],['turnaround','character-assets'])
        self.assertEqual(result[1]['groups'],{'torso':'costume'})
        count=self.matrix.request.call_count
        again=self.post(payload)
        self.assertEqual([j['id'] for j in again],[j['id'] for j in result])
        self.assertEqual(self.matrix.request.call_count,count)


class CombinedReferenceBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        self.image, self.layout = combined_fixture()
        self.character = CharacterProject.create(self.storage, 'complete combined source fixture')
        self.character.set_image(png_bytes(self.image), 0)
        guide=assembly_guide();guide['r-shoulder'][0]=.54  # Embed the narrow synthetic shirt's sleeve root.
        self.character.update({'revision': self.character.meta['revision'], 'landmarks': guide,
                               'settings': self.character.meta['settings'], 'masks': {}})
        self.source_pixels = png_bytes(self.character.image())
        self.matrix = Matrix(self.storage)
        self.addCleanup(self.matrix.tunnel.close)
        self.matrix.request = Mock(side_effect=AssertionError('Existing art needs no paid generation'))

    def apply(self):
        return self.character.apply_source_layout({'revision': self.character.meta['revision'],
                                                   'parts': self.layout})

    def build(self):
        self.apply()
        from tests.test_asset_sheet_workflow import accept_fixture_prebind
        accept_fixture_prebind(self.character)
        with patch('app.character.split_image', side_effect=AssertionError('Complete crops need no segmentation')) as split, \
                patch('app.character.skin_field', return_value=object()):
            # A deliberately unusable atlas field proves the costume attachment
            # and complete chains never borrow the montage's global weights.
            result = self.character.build(self.storage, {'revision': self.character.meta['revision'],
                'nonce': uuid.uuid4().hex, 'landmarks': self.character.meta['landmarks'],
                'settings': self.character.meta['settings'], 'masks': {}})
            split.assert_not_called()
        self.matrix.request.assert_not_called()
        self.assertEqual(png_bytes(self.character.image()), self.source_pixels)
        return RigProject(self.storage / 'rigs' / result['versions'][-1]['rig_id'])

    def test_combined_source_is_complete_with_six_parts_and_legs_behind_the_costume(self):
        result = self.apply()
        self.assertTrue(result['asset_status']['ready'])
        self.assertEqual(result['asset_status']['missing_parts'], [])
        self.assertEqual(result['asset_status']['missing_chains'], [])
        with patch('app.character.split_image', side_effect=AssertionError('A complete montage is not a portrait')):
            parts = self.character.parts()
            self.assertEqual(len(parts), 6)
            self.assertNotIn('pelvis', {part['name'] for part in parts})
            self.assertEqual(next(part for part in parts if part['name'] == 'torso')['group'], 'costume')
            order = self.character.review()['draw_order']
            for side in ('l', 'r'):
                self.assertLess(order.index(side + '-upper-leg'), order.index('torso'))
            self.assertEqual(self.character.assembly().size, self.image.size)
        self.assertEqual(png_bytes(self.character.image()), self.source_pixels)
        self.matrix.request.assert_not_called()

    def test_real_spine42_costume_is_one_weighted_mesh_using_only_hip_and_torso(self):
        rig = self.build()
        self.assertTrue(rig.validate()['passed'])
        skeleton = rig.state['skeleton']
        self.assertTrue(skeleton['skeleton']['spine'].startswith('4.2'))
        attachments = skeleton['skins'][0]['attachments']
        self.assertEqual(len(attachments), 6)
        self.assertNotIn('pelvis', attachments)
        costume = attachments['torso']['torso']
        self.assertEqual(costume['type'], 'mesh')
        self.assertGreater(costume['hull'], 0)
        offset = 0
        saw_blend = False
        for _ in range(len(costume['uvs']) // 2):
            count = costume['vertices'][offset]
            values = costume['vertices'][offset + 1:offset + 1 + count * 4]
            offset += 1 + count * 4
            names = {skeleton['bones'][values[i]]['name'] for i in range(0, len(values), 4)}
            self.assertTrue(names <= {'hip', 'torso'})
            self.assertTrue(all(math.isfinite(value) for value in values))
            self.assertAlmostEqual(sum(values[3::4]), 1, places=10)
            saw_blend |= count == 2
        self.assertTrue(saw_blend)
        self.assertEqual(offset, len(costume['vertices']))
        order = [slot['name'] for slot in skeleton['slots']]
        for side in ('l', 'r'):
            self.assertLess(order.index(side + '-upper-leg'), order.index('torso'))

    @unittest.skipUnless(Path(NODE).is_file(), 'Official runtime and contact diagnostics require Node')
    def test_actual_runtime_motion_and_contact_are_bound_to_current_resources_without_generation(self):
        rig = self.build()
        content = rig.export()
        tasks = AgentTasks(self.storage, ROOT, {'rig': lambda entity: RigProject(self.storage / 'rigs' / entity)}, None)
        tasks.api = Mock(side_effect=AssertionError('No provider or HTTP calls are needed'))
        report = tasks.runtime_check(content)
        self.assertTrue(report['passed'])
        self.assertEqual(set(report['animations']), {'idle', 'walk'})
        walk = next(clip for clip in report['motion']['clips'] if clip['animation'] == 'walk')
        self.assertTrue(walk['visibleMotionObserved'])
        self.assertTrue(walk['articulatedMotionObserved'])
        self.assertTrue(walk['rootRelativeGeometryMotionObserved'])
        self.assertGreaterEqual(walk['samples'], 32)
        self.assertTrue(walk['loop']['seamless'])
        require_requested_motion('制作行走动画', report, {'frame_counts': {'walk': 32}}, min_frames=32)
        contact = tasks.contact_check(content, 'walk', {'samples': 32})
        self.assertEqual(contact['status'], 'assessed', contact)
        self.assertEqual(contact['resource_sha256'], resource_digest(content))
        self.assertEqual(contact['resource_sha256'], resource_digest(rig.export()))
        self.assertEqual(contact['animation'], 'walk')
        self.assertEqual(contact['samples'], 32)
        self.assertTrue(contact['limits']['numerical_only'])
        tasks.api.assert_not_called()
        self.matrix.request.assert_not_called()
        self.assertEqual(png_bytes(self.character.image()), self.source_pixels)


class PreparedMotionReviewContractTests(unittest.TestCase):
    def report(self):
        return {'animations': ['walk'], 'skins': 1, 'motion': {'clips': [
            {'animation': 'walk', 'skin': 'default', 'visibleMotionObserved': True,
             'articulatedMotionObserved': True, 'rootRelativeGeometryMotionObserved': True}]}}

    def test_legacy_default_keeps_16_frames_but_prepared_pipeline_requires_32(self):
        report = self.report()
        require_requested_motion('制作行走动画', report, {'frame_counts': {'walk': 16}})
        with self.assertRaisesRegex(UserError, '32'):
            require_requested_motion('制作行走动画', report, {'frame_counts': {'walk': 16}}, min_frames=32)
        require_requested_motion('制作行走动画', report, {'frame_counts': {'walk': 32}}, min_frames=32)

    def test_idle_preview_cannot_lend_32_frame_credit_to_walking(self):
        with self.assertRaises(UserError):
            require_requested_motion('制作行走动画', self.report(),
                                     {'frame_counts': {'idle': 32, 'walk': 16}}, min_frames=32)


if __name__ == '__main__':
    unittest.main()
