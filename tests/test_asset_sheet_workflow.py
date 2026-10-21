"""Asset-sheet import and review gates use real crops without paid generation."""
import base64
import copy
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import UserError, png_bytes, read_image
from app.rig import RigProject
from app.image_preflight import JOINTS
from app.asset_integrity import ASSEMBLY_CHECKS
from app.orientation_review import ASSEMBLY_CHECKS as ORIENTATION_CHECKS


ROOT = Path(__file__).resolve().parents[1]
PAID_ACTIONS = ('/generate', '/generate-view', '/generate-parts')


def asset_fixture():
    """Seven source crops with an empty fourth cell, not a portrait to split."""
    image = Image.new('RGBA', (600, 400))
    draw = ImageDraw.Draw(image)
    boxes = {'head': [30, 20, 120, 130], 'torso': [175, 30, 275, 170],
             'pelvis': [330, 50, 425, 110], 'l-upper-arm': [25, 215, 125, 385],
             'r-upper-arm': [175, 215, 275, 385], 'l-upper-leg': [325, 215, 445, 385],
             'r-upper-leg': [475, 215, 595, 385]}
    colors = ['#e6a15d', '#196da4', '#334b65', '#af5b31', '#e9af64', '#29364d', '#475f71']
    for (name, box), color in zip(boxes.items(), colors):
        draw.rounded_rectangle([box[0] + 6, box[1] + 4, box[2] - 7, box[3] - 5],
                               radius=10, fill=color)
    # A white interior costume mark must survive extraction.
    draw.rectangle((210, 70, 238, 110), fill='white')
    draw.rectangle((37, 34, 45, 44), fill='#632421')
    parts = [{'name': name, 'box': box} for name, box in boxes.items()]
    for item in parts:
        if item['name'].endswith('upper-arm'):
            item.update(chain='arm', pins={'shoulder': [.5, .05], 'elbow': [.5, .4],
                                           'wrist': [.5, .75], 'hand': [.5, .94]})
        elif item['name'].endswith('upper-leg'):
            item.update(chain='leg', pins={'hip': [.5, .05], 'knee': [.5, .43],
                                           'ankle': [.5, .82], 'toe': [.8, .94]})
    return image, parts


def assembly_guide():
    guide = {'head': [.5, .15], 'neck': [.5, .29], 'hip': [.5, .52]}
    for side, x in (('l', .44), ('r', .56)):
        guide.update({side + '-shoulder': [x, .31], side + '-elbow': [x, .44],
                      side + '-wrist': [x, .56], side + '-hand': [x, .63],
                      side + '-hip': [x, .53], side + '-knee': [x, .72],
                      side + '-ankle': [x, .89], side + '-toe': [x + .06, .96]})
    return guide


def accept_fixture_prebind(character):
    """Synthetic test artwork has explicit complete silhouettes and overlaps."""
    report,_=character.prebind_evidence()
    character.review_prebind({'revision':character.meta['revision'],'digest':report['digest'],
        'checks':dict.fromkeys(report['checks'],True),'verdict':'ready',
        'reason':'Synthetic fixture: complete separate limbs and overlapping joints.'})
    from tests.stress_fixture import accept_synthetic_stress
    accept_synthetic_stress(character)


class AssetSheetWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        self.loaders = {'character': lambda entity: CharacterProject(self.storage / 'characters' / entity)}
        self.tasks = AgentTasks(self.storage, ROOT, self.loaders, None)
        self.tasks.api = self.api
        self.calls = []
        self.allow_mock_generation = False
        self.source, self.layout = asset_fixture()
        task = self.tasks.create({'nonce': uuid.uuid4().hex,
                                  'requirements': '复用完整角色部件，检查组装后制作行走动画',
                                  'image': base64.b64encode(png_bytes(self.source)).decode(),
                                  'max_generations': 4})
        self.task_id = task['id']
        self.entity = task['sources'][0]['id']
        character = self.character()
        character.update({'revision': character.meta['revision'], 'landmarks': assembly_guide(),
                          'settings': character.meta['settings'], 'masks': {}})

    def character(self):
        return self.loaders['character'](self.entity)

    def api(self, route, data=None, binary=False):
        self.calls.append((route, copy.deepcopy(data)))
        pieces = route.strip('/').split('/')
        character = self.loaders['character'](pieces[2])
        action = pieces[3] if len(pieces) > 3 else None
        if binary:
            if action == 'image':
                return png_bytes(character.image())
            if action == 'assembly':
                return png_bytes(character.assembly())
            raise AssertionError(route)
        if action == 'apply-source-layout':
            return character.apply_source_layout(data)
        if action == 'prepare-assembly':
            return character.prepare_assembly(data)
        if action == 'build':
            return character.build(self.storage, data)
        if action == 'review-prebind':
            return character.review_prebind(data)
        if action == 'generate-parts' and self.allow_mock_generation:
            return [{'id': 'mock-only', 'status': 'pending'}]
        if action is None:
            return character.summary()
        raise AssertionError('Unexpected API call: ' + route)

    def look(self, view='image'):
        return self.tasks.tool(self.task_id, 'spine_image',
                               {'kind': 'character', 'id': self.entity, 'view': view})

    def assess(self, **changes):
        data = {'id': self.entity, 'verdict': 'ready', 'source_kind': 'asset_sheet',
                'observed_view': '同一相机的七片角色素材', 'target_view': '朝右轻微三分之四视角',
                'target_actions': ['idle', 'walk'], 'missing_parts': [],
                'camera_view':'three-quarter-side','direction_consistent':True,
                'orientation_checks':dict.fromkeys(ORIENTATION_CHECKS,True),'material_checks':dict.fromkeys(ASSEMBLY_CHECKS,True),'joint_review':{key:True for key in JOINTS},
                'reason': '检查实际素材和连接轮廓', 'parts_reviewed': False, **changes}
        return self.tasks.tool(self.task_id, 'spine_assess_image', data)

    def action(self, action, **changes):
        character = self.character()
        data = {'revision': character.meta['revision'], 'nonce': uuid.uuid4().hex, **changes}
        if action == 'build':
            data.update(landmarks=copy.deepcopy(character.meta['landmarks']),
                        settings=copy.deepcopy(character.meta['settings']),
                        masks=copy.deepcopy(character.meta['masks']))
        return self.tasks.tool(self.task_id, 'spine_character',
                               {'id': self.entity, 'action': action, 'data': data})

    def import_source(self, parts=None):
        self.look()
        self.assess()
        self.review_parts()
        return self.action('apply-source-layout', parts=copy.deepcopy(self.layout if parts is None else parts))

    def review_parts(self, **changes):
        return self.tasks.tool(self.task_id,'spine_review_parts',{'id':self.entity,'verdict':'ready',
            'camera_view':'three-quarter-side','direction_consistent':True,
            'orientation_checks':dict.fromkeys(ORIENTATION_CHECKS,True),'material_checks':dict.fromkeys(ASSEMBLY_CHECKS,True),'joint_review':{key:True for key in JOINTS},'identity_preserved':True,
            'silhouette_intact':True,'separated':True,'reason':'查看当前素材的完整轮廓、两侧连接和分离范围',**changes})

    def test_worker_executes_automatic_reuse_with_owned_source_and_no_generation(self):
        self.import_source()
        result=self.action('prepare-assembly',replacements=[{'name':'l-upper-leg','character_id':self.entity}],back_accessory_part='torso')
        self.assertEqual(result['part_layers']['l-upper-leg']['completion_source']['anchor_mode'],'landmarks')
        outsider=CharacterProject.create(self.storage,'not owned')
        with self.assertRaises(UserError):self.action('prepare-assembly',replacements=[{'name':'l-upper-leg','character_id':outsider.id}])
        self.assert_no_paid_calls()

    def test_front_walk_cannot_reach_generation_or_ready_and_does_not_reserve_budget(self):
        self.look()
        with self.assertRaises(UserError):self.assess(camera_view='front')
        self.assess(verdict='needs_view',camera_view='front')
        self.allow_mock_generation=True
        with self.assertRaises(UserError):self.action('generate-parts')
        self.assert_no_paid_calls()
        self.assess(verdict='needs_parts',camera_view='side',missing_parts=['separated costume'])
        self.action('generate-parts')
        self.assertEqual(self.tasks.summary(self.task_id)['generation_budget']['used_or_reserved'],1)

    def test_inconsistent_shoe_direction_cannot_pass_camera_gate(self):
        self.look()
        with self.assertRaises(UserError):self.assess(direction_consistent=False)
        self.tasks.update(self.task_id,requirements='只制作正面待机，保持原视角')
        self.assess(target_actions=['idle'],camera_view='front',direction_consistent=False)

    def test_preassembly_part_review_is_bound_to_current_revision(self):
        self.look();self.assess()
        with self.assertRaises(UserError):self.action('apply-source-layout',parts=self.layout)
        self.review_parts()
        c=self.character();c.update({'revision':c.meta['revision'],'landmarks':c.meta['landmarks'],'settings':c.meta['settings'],'masks':{}})
        self.assess()
        with self.assertRaises(UserError):self.action('apply-source-layout',parts=self.layout)
        self.review_parts();self.action('apply-source-layout',parts=self.layout)

    def test_joint_defects_block_final_ready_even_when_assets_exist(self):
        self.import_source();self.look('assembly')
        with self.assertRaises(UserError):self.assess(parts_reviewed=True,joint_review={**{key:True for key in JOINTS},'ankle':False})
        self.assess(parts_reviewed=True)

    def test_needs_adjustment_review_allows_crop_repair_but_not_false_ready(self):
        self.look();self.assess()
        with self.assertRaises(UserError):self.review_parts(separated=False)
        self.review_parts(verdict='needs_adjustment',separated=False,reason='需要裁掉袖装边的多余邻近像素，然后复查组装')
        self.action('apply-source-layout',parts=self.layout)

    def assert_no_paid_calls(self):
        self.assertFalse(any(route.endswith(PAID_ACTIONS) for route, _ in self.calls))
        self.assertEqual(self.tasks.summary(self.task_id)['generation_budget']['used_or_reserved'], 0)

    def test_complete_source_import_preserves_pixels_and_never_splits_atlas_cells(self):
        character = self.character()
        image_id = character.meta['image_id']
        image_path = character.directory / 'images' / (image_id + '.png')
        source_bytes = image_path.read_bytes()
        with patch('app.character.split_image', side_effect=AssertionError('An asset sheet is not a portrait')) as split:
            result = self.import_source()
            character = self.character()
            self.assertTrue(result['asset_status']['ready'])
            parts = character.parts()
            self.assertEqual({part['name'] for part in parts}, {item['name'] for item in self.layout})
            self.assertTrue(all(part['complete'] for part in parts))
            self.assertEqual(character.assembly().size, self.source.size)
            split.assert_not_called()
        self.assertEqual(character.meta['image_id'], image_id)
        self.assertEqual(image_path.read_bytes(), source_bytes)
        self.assertEqual(png_bytes(character.image()), source_bytes)
        torso = character.meta['assembly_inputs']['torso']
        crop = read_image((character.directory / 'part-images' / (torso['image_id'] + '.png')).read_bytes())
        self.assertEqual(crop.getpixel((40, 50)), (255, 255, 255, 255))
        for record in character.meta['part_layers'].values():
            self.assertEqual(record['mode'], 'canvas-layer')
            self.assertEqual(record['source_image_id'], image_id)
            self.assertNotIn('job_id', record)
        self.assertEqual(list((character.directory / 'jobs').glob('*')), [])
        self.assert_no_paid_calls()

    def test_source_import_requires_actual_current_image_and_asset_sheet_assessment(self):
        with self.assertRaises(UserError):
            self.action('apply-source-layout', parts=self.layout)
        self.look()
        self.assess(source_kind='character')
        with self.assertRaises(UserError):
            self.action('apply-source-layout', parts=self.layout)
        self.assess()
        character = self.character()
        character.update({'revision': character.meta['revision'], 'landmarks': character.meta['landmarks'],
                          'settings': {**character.meta['settings'], 'period': 1.2}, 'masks': {}})
        with self.assertRaises(UserError):
            self.action('apply-source-layout', parts=self.layout)
        self.assertFalse(any(route.endswith('/apply-source-layout') for route, _ in self.calls))
        self.assertEqual(self.character().meta['part_layers'], {})
        self.assert_no_paid_calls()

    def test_rotation_is_counterclockwise_and_pins_use_final_sprite_coordinates(self):
        item = {'name': 'head', 'box': self.layout[0]['box'], 'rotation': 90,
                'start_pin': [.2, .8], 'end_pin': [.8, .2]}
        self.import_source([item])
        character = self.character()
        record = character.meta['part_layers']['head']
        sprite = read_image((character.directory / 'part-images' / (record['image_id'] + '.png')).read_bytes())
        expected = self.source.crop(item['box']).rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
        self.assertEqual(sprite.size, (110, 90))
        self.assertEqual(sprite.tobytes(), expected.tobytes())
        self.assertEqual(record['layout']['rotation'], 90)
        self.assertEqual(record['layout']['sprite_size'], [110, 90])
        self.assertEqual(record['start_pin'], item['start_pin'])
        self.assertEqual(record['end_pin'], item['end_pin'])
        self.assert_no_paid_calls()

    def test_invalid_rotation_is_atomic_for_existing_source_parts(self):
        self.import_source()
        character = self.character()
        before = copy.deepcopy(character.meta)
        disk = (character.directory / 'character.json').read_bytes()
        blobs = {path.name: path.read_bytes() for path in (character.directory / 'part-images').glob('*.png')}
        for value in (-181, 181, True, float('nan')):
            with self.subTest(rotation=value), self.assertRaises(UserError):
                character.apply_source_layout({'revision': character.meta['revision'],
                                               'parts': [{**self.layout[0], 'rotation': value}]})
            self.assertEqual(character.meta, before)
            self.assertEqual((character.directory / 'character.json').read_bytes(), disk)
            self.assertEqual({path.name: path.read_bytes() for path in (character.directory / 'part-images').glob('*.png')}, blobs)

    def test_segment_coverage_cannot_replace_a_required_complete_arm_chain(self):
        parts = [copy.deepcopy(item) for item in self.layout if item['name'] != 'l-upper-arm']
        parts += [{'name': 'l-upper-arm', 'box': [25, 215, 125, 275]},
                  {'name': 'l-lower-arm', 'box': [25, 275, 125, 335]},
                  {'name': 'l-hand', 'box': [25, 335, 125, 385]}]
        self.import_source(parts)
        status = self.character().asset_status()
        self.assertEqual(status['missing_parts'], [])
        self.assertEqual(status['missing_chains'], ['l-upper-arm'])
        self.assertFalse(status['ready'])
        self.look('assembly')
        with self.assertRaises(UserError):
            self.assess(parts_reviewed=True)
        self.assess()
        with self.assertRaises(UserError):
            self.action('build')
        self.assertFalse(any(route.endswith('/build') for route, _ in self.calls))
        self.assert_no_paid_calls()

    def test_complete_parts_still_require_current_assembly_and_explicit_review(self):
        self.import_source()
        self.assess()
        with self.assertRaises(UserError):
            self.action('build')
        with self.assertRaises(UserError):
            self.assess(parts_reviewed=True)
        self.look('assembly')
        self.assess(parts_reviewed=False)
        with self.assertRaises(UserError):
            self.action('build')
        self.assess(parts_reviewed=True)
        self.assertEqual(self.tasks.read(self.task_id)['assembly_checks'][self.entity],
                         self.character().meta['revision'])
        self.assertFalse(any(route.endswith('/build') for route, _ in self.calls))

    def test_changed_revision_invalidates_prior_assembly_review(self):
        self.import_source()
        self.look('assembly')
        self.assess(parts_reviewed=True)
        character = self.character()
        character.update_review({'revision': character.meta['revision'],
                                 'part_review': {'adjustments': {'head': {'dx': 1}}}})
        with self.assertRaises(UserError):
            self.action('build')
        with self.assertRaises(UserError):
            self.assess(parts_reviewed=True)
        self.assess(parts_reviewed=False)
        with self.assertRaises(UserError):
            self.action('build')
        self.look('assembly')
        self.assess(parts_reviewed=True)
        self.assertFalse(any(route.endswith('/build') for route, _ in self.calls))
        self.assert_no_paid_calls()

    def test_zero_budget_reuses_complete_source_assets_and_builds_a_real_rig(self):
        self.import_source()
        original = self.character()
        source_metadata = (original.directory / 'character.json').read_bytes()
        source_pixels = png_bytes(original.image())
        task = self.tasks.create({'nonce': uuid.uuid4().hex, 'requirements': '复用完整素材与已校准关节，检查后绑定行走',
                                  'sources': [{'kind': 'character', 'id': original.id,
                                               'revision': original.meta['revision']}], 'max_generations': 0})
        self.task_id = task['id']
        self.entity = task['sources'][0]['id']
        self.assertNotEqual(self.entity, original.id)
        self.assertEqual(task['image_preparation'], 'asset-sheet')
        self.assertTrue(self.character().asset_status()['ready'])
        with patch('app.character.split_image', side_effect=AssertionError('Complete crops must be reused')) as split:
            c=self.character();report=c.prebind_evidence()[0]
            # Fixture shapes have been inspected; actual worker views are enforced separately.
            c.review_prebind({'revision':c.meta['revision'],'digest':report['digest'],'checks':dict.fromkeys(report['checks'],True),'verdict':'ready','reason':'Synthetic complete fixture overlaps at shoulders and hips.'})
            from tests.stress_fixture import accept_synthetic_stress
            accept_synthetic_stress(c)
            self.look()
            self.look('assembly')
            self.assess(parts_reviewed=True)
            result = self.action('build')
            split.assert_not_called()
        version = result['versions'][-1]
        self.assertEqual(version['generator'], 'reviewed-chain-v1')
        self.assertEqual(version['render_parts'], 7)
        rig = RigProject(self.storage / 'rigs' / version['rig_id'])
        self.assertTrue(rig.validate()['passed'])
        self.assertIn(version['rig_id'], self.tasks.read(self.task_id)['owned']['rig'])
        self.assertEqual((original.directory / 'character.json').read_bytes(), source_metadata)
        self.assertEqual(png_bytes(original.image()), source_pixels)
        self.assert_no_paid_calls()

    def test_default_asset_sheet_generation_reserves_two_mocked_images(self):
        self.assertEqual(self.tasks.summary(self.task_id)['image_preparation'], 'asset-sheet')
        self.look()
        self.assess(verdict='needs_parts', source_kind='character', missing_parts=['occluded palms and shoes'])
        self.allow_mock_generation = True
        self.action('generate-parts', include_back=False)
        route, data = self.calls[-1]
        self.assertTrue(route.endswith('/generate-parts'))
        self.assertEqual(data['sheets'], ['turnaround','character-assets'])
        budget=self.tasks.summary(self.task_id)['generation_budget']
        self.assertEqual(budget['used_or_reserved'],0)
        self.assertEqual(budget['baseline_used_or_reserved'],2)
        self.assertEqual(budget['total_images_reserved'],2)


if __name__ == '__main__':
    unittest.main()
