"""Prompt planning contracts; no claim that text alone enforces a model layout."""
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app.character import CharacterProject
from app.core import UserError, png_bytes
from app.jobs import JobHistory
from app.image_preflight import requested_camera_policy
from app.part_prompts import (PROMPT_VERSION, SHEETS, generation_plan,
                              part_instructions, part_prompt, sheet_spec, view_prompt)
from app.provider import Matrix


class PartPromptPlanningTests(unittest.TestCase):
    def test_profile_character_plan_honors_explicit_facing_without_changing_default_camera(self):
        cases=[('Stocky blue warrior, true profile facing LEFT, one near-side shoulder patch', 'profile', 'left'),
               ('Stocky blue warrior, near-profile 75–80 degrees facing RIGHT', 'requested', 'right'),
               ('Stocky blue warrior with a purple patch on the left sleeve', 'requested', None)]
        for description,view,facing in cases:
            with self.subTest(description=description):
                plan=generation_plan({'mode':'character','prompt':description})
                item=plan['items'][0]
                self.assertEqual(item['camera_policy']['view'],view)
                self.assertEqual(item['camera_policy']['facing'],facing)
                self.assertTrue(item['camera_policy']['uniform_camera'])
                self.assertTrue(item['camera_policy']['preserve_side_asymmetry'])
                self.assertTrue(plan['requires_visual_review'])
                self.assertEqual(plan['image_count'],1)
                if view=='profile':
                    self.assertIn('true lateral profile facing LEFT',item['prompt'])
                    self.assertNotIn('slight three-quarter view',item['prompt'])
                    self.assertNotIn('or limb hidden behind another limb',item['prompt'])
                    self.assertIn('Occluded garment and accessory artwork can be completed as separate cut-outs',item['prompt'])
                elif item['camera_policy']['explicit_view']:
                    self.assertIn('the camera view specified in Character design',item['prompt'])
                    self.assertNotIn('slight three-quarter view',item['prompt'])
                else:self.assertIn('slight three-quarter view',item['prompt'])

    def test_explicit_front_camera_is_not_overridden_by_default_right_facing_shoes(self):
        item=generation_plan({'mode':'character','prompt':'Blue warrior, front view for front-facing idle'})['items'][0]
        self.assertTrue(item['camera_policy']['front_or_back'])
        self.assertNotIn('slight three-quarter view',item['prompt'])
        self.assertNotIn('Both shoes point RIGHT',item['prompt'])
        self.assertNotIn('neither shoe faces the camera',item['prompt'])
        self.assertNotIn('neither shoe points toward the camera',view_prompt('front view',['walk']))

    def test_only_requested_sheets_are_planned_and_counted(self):
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['legs']})
        self.assertEqual([item['sheet'] for item in plan['items']], ['legs'])
        self.assertEqual(plan['image_count'], 1)
        self.assertEqual(plan['prompt_version'], PROMPT_VERSION)
        self.assertTrue(plan['requires_visual_review'])
        self.assertEqual(plan['items'][0]['parts'], SHEETS['legs']['parts'])

    def test_per_sheet_notes_do_not_request_the_other_anatomy_group(self):
        arm_notes = '蓝色短袖、白色肘部袖口和裸露前臂；不要额外腕饰。'
        leg_notes = '深色裤腿、灰绑腿与棕鞋；两鞋均朝右。'
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['arms', 'legs'],
                                'instructions_by_sheet': {'arms': arm_notes, 'legs': leg_notes}})
        by_sheet = {item['sheet']: item for item in plan['items']}
        self.assertIn(arm_notes, by_sheet['arms']['prompt'])
        self.assertNotIn(leg_notes, by_sheet['arms']['prompt'])
        self.assertIn(leg_notes, by_sheet['legs']['prompt'])
        self.assertNotIn(arm_notes, by_sheet['legs']['prompt'])
        self.assertEqual(plan['image_count'], 2)

    def test_scoped_notes_reject_mixed_shared_instructions_instead_of_silently_dropping_them(self):
        with self.assertRaisesRegex(UserError, '不混用'):
            generation_plan({'mode': 'complete-parts', 'instructions': '同时补全双臂与双腿',
                             'instructions_by_sheet': {'arms': '只画双臂'}})
        for notes in (None, [], {'feet': 'draw shoes'}, {'arms': ['draw arms']}, {'arms': '长' * 401}):
            with self.subTest(notes=notes):
                with self.assertRaises(UserError):
                    generation_plan({'mode': 'complete-parts', 'instructions_by_sheet': notes})

    def test_unmentioned_group_gets_no_cross_group_note(self):
        data = {'instructions_by_sheet': {'legs': '两只鞋都朝右'}}
        self.assertEqual(part_instructions(data, 'arms'), '')
        self.assertEqual(part_instructions(data, 'legs'), '两只鞋都朝右')

    def test_no_back_body_is_three_real_cells_with_a_supported_ratio(self):
        before = copy.deepcopy(SHEETS)
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['body'], 'include_back': False})
        item = plan['items'][0]
        self.assertEqual(item['parts'], ['head', 'torso', 'pelvis'])
        self.assertEqual((item['columns'], item['rows'], item['ratio']), (3, 1, '3:2'))
        self.assertEqual(len(item['parts']), item['columns'] * item['rows'])
        self.assertFalse(item['include_back'])
        self.assertIn('these three pieces only', item['prompt'])
        self.assertNotIn('BOTTOM RIGHT', item['prompt'])
        self.assertNotIn('reference backpack', item['prompt'])
        self.assertEqual(SHEETS, before)

    def test_legacy_robot_body_preserves_four_cells_and_back(self):
        spec = sheet_spec('body')
        self.assertEqual((spec['columns'], spec['rows'], spec['ratio']), (2, 2, '1:1'))
        self.assertEqual(spec['parts'], ['head', 'torso', 'pelvis', 'back'])
        self.assertIn('reference backpack', part_prompt('body'))
        legacy = generation_plan({'mode': 'complete-parts', 'instructions': '保留鞋带与背包通风口'})
        self.assertEqual([item['sheet'] for item in legacy['items']], ['body', 'arms', 'legs'])
        self.assertEqual(legacy['image_count'], 3)
        for item in legacy['items']:
            self.assertIn('保留鞋带与背包通风口', item['prompt'])

    def test_sheet_specs_are_snapshots_not_mutable_global_layouts(self):
        first = sheet_spec('legs')
        first['parts'].clear()
        first['rows'] = 99
        second = sheet_spec('legs')
        self.assertEqual(len(second['parts']), 6)
        self.assertEqual(second['rows'], 3)

    def test_parts_prompts_put_short_cell_contract_before_design_notes(self):
        for name in ('body', 'arms', 'legs'):
            with self.subTest(sheet=name):
                prompt = part_prompt(name)
                self.assertTrue(prompt.startswith('Exactly '))
                self.assertLess(len(prompt.split()), 230)
        arms = part_prompt('arms')
        self.assertIn('upper sleeve sections', arms)
        self.assertIn('lower sleeve sections', arms)
        self.assertIn('terminal cut-outs', arms)
        self.assertIn('original cuff on its corresponding section', arms)
        legs = part_prompt('legs')
        self.assertIn('upper trouser sections', legs)
        self.assertIn('lower trouser sections', legs)
        self.assertIn('front cuff rim', legs)
        self.assertIn('insert seated inside its shoe', legs)

    def test_facing_changes_both_shoes_without_swapping_image_column_ownership(self):
        right = part_prompt('legs', facing='right')
        left = part_prompt('legs', facing='left')
        self.assertIn('both pointing RIGHT', right)
        self.assertIn('both pointing LEFT', left)
        self.assertIn('heels RIGHT and toes LEFT', left)
        for prompt in (right, left):
            self.assertIn('LEFT COLUMN: source image-left trouser assembly', prompt)
            self.assertIn('RIGHT COLUMN: source image-right trouser assembly', prompt)

    def test_complete_limbs_is_explicit_one_image_and_requires_reviewed_layout(self):
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['limbs']})
        self.assertEqual(plan['image_count'], 1)
        self.assertTrue(plan['requires_reviewed_layout'])
        item = plan['items'][0]
        self.assertEqual(item['sheet'], 'limbs')
        self.assertEqual(item['parts'], ['l-upper-arm', 'r-upper-arm', 'l-upper-leg', 'r-upper-leg'])
        self.assertEqual((item['columns'], item['rows'], item['ratio']), (2, 2, '1:1'))
        self.assertTrue(item['requires_reviewed_layout'])
        for name in ('body', 'arms', 'legs'):
            self.assertFalse(sheet_spec(name)['requires_reviewed_layout'])
        self.assertFalse(sheet_spec('body', False)['requires_reviewed_layout'])
        legacy = generation_plan({'mode': 'complete-parts'})
        self.assertEqual([item['sheet'] for item in legacy['items']], ['body', 'arms', 'legs'])
        self.assertFalse(legacy['requires_reviewed_layout'])
        self.assertTrue(all(not item['requires_reviewed_layout'] for item in legacy['items']))

    def test_character_assets_is_one_wide_complete_layout_with_reviewed_crops(self):
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['character-assets']})
        self.assertEqual(plan['prompt_version'], 'animation-parts-v21')
        self.assertEqual(plan['image_count'], 1)
        self.assertTrue(plan['requires_reviewed_layout'])
        self.assertTrue(plan['requires_visual_review'])
        item = plan['items'][0]
        self.assertEqual(item['sheet'], 'character-assets')
        self.assertEqual((item['columns'], item['rows'], item['ratio']), (4, 2, '3:2'))
        self.assertEqual(item['parts'], ['head', 'torso', 'pelvis', 'back',
                                        'l-upper-arm', 'r-upper-arm', 'l-upper-leg', 'r-upper-leg'])
        self.assertTrue(item['requires_reviewed_layout'])
        self.assertEqual(item['count'], 1)
        self.assertIn('original head/headwear', item['prompt'])
        self.assertIn('source image-left complete arm/palm', item['prompt'])
        self.assertIn('source image-right complete leg/shoe', item['prompt'])

    def test_character_assets_without_back_keeps_empty_top_cell_and_original_limb_slots(self):
        before = copy.deepcopy(SHEETS['character-assets'])
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['character-assets'],
                                'include_back': False})
        item = plan['items'][0]
        self.assertEqual((item['columns'], item['rows'], item['ratio']), (4, 2, '3:2'))
        self.assertEqual(item['parts'], ['head', 'torso', 'pelvis',
                                        'l-upper-arm', 'r-upper-arm', 'l-upper-leg', 'r-upper-leg'])
        self.assertFalse(item['include_back'])
        self.assertTrue(item['requires_reviewed_layout'])
        self.assertIn('waist/pelvis costume; an empty white cell. BOTTOM ROW', item['prompt'])
        self.assertNotIn('reference back accessory', item['prompt'])
        self.assertIn('each occupied cell', item['prompt'])
        self.assertEqual(SHEETS['character-assets'], before)
        chinese = part_prompt('character-assets', '保留原厨师服装', include_back=False)
        self.assertIn('腰胯衣装；纯白空格。下排', chinese)
        self.assertNotIn('完整背部配件', chinese)

    def test_character_asset_prompt_preserves_identity_angle_scale_and_continuous_binding_art(self):
        notes = 'Preserve the chef apron and the patch on its original sleeve.'
        prompt = part_prompt('character-assets', notes, facing='left')
        self.assertTrue(prompt.startswith('Exactly 4 columns x 2 rows'))
        self.assertIn(notes, prompt)
        self.assertIn('face, orientation, reference footwear size and ONE assembly scale', prompt)
        self.assertIn('arm/palm and leg/shoe stays connected', prompt)
        self.assertIn('Rounded shoulder/hip overlaps and natural elbow/knee contours', prompt)
        self.assertIn('Match the reference character, camera', prompt)
        self.assertIn('original image-side details', prompt)
        self.assertIn('Complete occluded garment and accessory artwork', prompt)
        self.assertIn('Both shoes pointing LEFT', prompt)
        for side in ('left', 'right'):
            self.assertIn('source image-' + side + ' complete arm/palm', prompt)
            self.assertIn('source image-' + side + ' complete leg/shoe', prompt)
        chinese = generation_plan({'mode': 'complete-parts', 'sheets': ['character-assets'],
                                   'instructions_by_sheet': {'character-assets': '保留原厨师脸与单侧袖章'}},
                                  'left')['items'][0]
        self.assertEqual(chinese['prompt_language'], 'zh')
        self.assertIn('保留原脸、头部角度与比例', chinese['prompt'])
        self.assertIn('同一组装尺度', chinese['prompt'])
        self.assertIn('两鞋均朝左', chinese['prompt'])
        self.assertNotIn('TOP ROW', chinese['prompt'])

    def test_complete_asset_layout_rejects_every_overlapping_group(self):
        for other in ('body', 'arms', 'legs', 'limbs'):
            for sheets in (['character-assets', other], [other, 'character-assets']):
                with self.subTest(sheets=sheets), self.assertRaises(UserError):
                    generation_plan({'mode': 'complete-parts', 'sheets': sheets})

    def test_limb_prompt_keeps_continuous_joints_reference_angle_and_both_shoe_directions(self):
        for facing, heels, toes in (('right', 'LEFT', 'RIGHT'), ('left', 'RIGHT', 'LEFT')):
            prompt = part_prompt('limbs', facing=facing)
            self.assertLess(len(prompt.split()), 230)
            self.assertIn('two complete sleeve-to-terminal assemblies', prompt)
            self.assertIn('two complete trouser-to-shoe assemblies', prompt)
            self.assertIn('ONE unbroken silhouette', prompt)
            self.assertIn('whole shoe connected', prompt)
            self.assertIn('reference camera/side angle', prompt)
            self.assertIn('horizontal soles', prompt)
            self.assertIn('pointing ' + facing.upper(), prompt)
            self.assertIn('heels ' + heels + ', toes ' + toes, prompt)
            self.assertIn('LEFT COLUMN: source image-left assemblies', prompt)
            self.assertIn('RIGHT COLUMN: source image-right assemblies', prompt)
            self.assertNotIn('no combined segments', prompt)
            self.assertNotIn('NO forearms or hands', prompt)

    def test_body_and_limbs_can_share_plan_but_keep_scoped_notes_and_markers(self):
        notes = {'body': '原头脸、领巾与腰带', 'limbs': '完整侧向双臂和连鞋双腿'}
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['body', 'limbs'],
                                'include_back': False, 'instructions_by_sheet': notes})
        self.assertEqual(plan['image_count'], 2)
        self.assertEqual([item['requires_reviewed_layout'] for item in plan['items']], [False, True])
        for item in plan['items']:
            self.assertIn(notes[item['sheet']], item['prompt'])
            self.assertNotIn(notes['limbs' if item['sheet'] == 'body' else 'body'], item['prompt'])
        for data in ({'instructions_by_sheet': {'limbs': '长'*401}},
                     {'instructions_by_sheet': {'limbs': []}},
                     {'instructions': 'shared', 'instructions_by_sheet': notes}):
            with self.subTest(data=data), self.assertRaises(UserError):
                generation_plan({'mode': 'complete-parts', 'sheets': ['limbs'], **data})

    def test_complete_limb_group_rejects_overlapping_segment_groups_before_generation(self):
        for sheets in (['limbs', 'arms'], ['arms', 'limbs'], ['limbs', 'legs'],
                       ['legs', 'limbs'], ['body', 'arms', 'limbs'], ['limbs', 'limbs']):
            with self.subTest(sheets=sheets), self.assertRaises(UserError):
                generation_plan({'mode': 'complete-parts', 'sheets': sheets})

    def test_view_preparation_does_not_instruct_adding_an_absent_backpack(self):
        prompt = view_prompt('three-quarter facing RIGHT', ['idle', 'walk'])
        self.assertIn('Preserve accessories only when present', prompt)
        self.assertNotIn('add a backpack', prompt)

    def test_walk_view_explicitly_aligns_both_shoes_without_forcing_it_on_other_motions(self):
        for action in ('walk', 'walk_loop', 'run'):
            with self.subTest(action=action):
                prompt = view_prompt('three-quarter facing RIGHT', [action])
                self.assertIn('Both shoes must point in the specified facing direction', prompt)
                self.assertIn('readable shoe sides and horizontal soles', prompt)
        for action in ('jump', 'grunt'):
            prompt = view_prompt('正面视角', [action])
            self.assertNotIn('neither shoe points toward the camera', prompt)

    def test_template_language_matches_chinese_request_with_normal_product_names(self):
        description='蓝衣武者，朝左正侧面，用于 Spine 的 idle/walk 动画，输出 PNG'
        item=generation_plan({'mode':'character','prompt':description})['items'][0]
        self.assertEqual(item['prompt_language'],'zh')
        self.assertIn('严格正侧面，朝左',item['prompt'])
        self.assertIn(description,item['prompt'])
        self.assertNotIn('Create ONE',item['prompt'])
        prompt=view_prompt('朝右纯侧面',['idle','walk'],'保留原服装与 PNG 配色')
        self.assertIn('目标动作：待机、行走',prompt)
        self.assertIn('另作素材',prompt)
        self.assertNotIn('Target motions',prompt)
        plan=generation_plan({'mode':'complete-parts','sheets':['arms'],
                              'instructions_by_sheet':{'arms':'保留蓝色袖口，适合 Spine 动画'}})
        self.assertEqual(plan['items'][0]['prompt_language'],'zh')
        self.assertIn('原图左侧袖装组合',plan['items'][0]['prompt'])
        self.assertIn('保留蓝色袖口',plan['items'][0]['prompt'])
        self.assertFalse(plan['prompt_policy']['moderation_rewrite_retry'])
        item=generation_plan({'mode':'character','prompt':'Original blue-coat warrior, 朝左纯侧面'})['items'][0]
        self.assertEqual(item['prompt_language'],'en')
        self.assertIn('true lateral profile facing LEFT',item['prompt'])

    def test_artwork_templates_are_positive_and_do_not_request_hidden_anatomy_reconstruction(self):
        import re
        prompts=[part_prompt(name) for name in SHEETS]
        prompts.extend([view_prompt('true profile facing RIGHT',['walk']),
                        generation_plan({'mode':'character','prompt':'Original blue-coat warrior'})['items'][0]['prompt']])
        for prompt in prompts:
            with self.subTest(prompt=prompt[:80]):
                self.assertIsNone(re.search(r'\b(?:hidden surfaces|reconstruct|hidden anatomy|exposed anatomy)\b',prompt,re.I))
                self.assertNotIn('NO ',prompt)
                self.assertNotIn('Forbidden:',prompt)
                self.assertLess(len(prompt.split()),180)

    def test_invalid_layout_flags_and_sheet_requests_fail_before_generation(self):
        with tempfile.TemporaryDirectory() as root:
            matrix = Matrix(root)
            matrix.request = Mock(side_effect=AssertionError('Planning must never generate'))
            for value in (None, 'false', 0, 1, [], {}):
                with self.subTest(include_back=value):
                    with self.assertRaises(UserError):
                        generation_plan({'mode': 'complete-parts', 'include_back': value})
            for sheets in ([], ['arms', 'arms'], ['head'], 'arms'):
                with self.subTest(sheets=sheets):
                    with self.assertRaises(UserError):
                        generation_plan({'mode': 'complete-parts', 'sheets': sheets})
            matrix.request.assert_not_called()
            matrix.tunnel.close()


class PartPromptRequestTests(unittest.TestCase):
    def setUp(self):
        from app import server
        self.server = server
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matrix = Matrix(self.root)
        self.matrix.request = Mock(return_value={'data': {'gen_id': 'mock-flare-generation'}})
        self.matrix.download_result = Mock(side_effect=AssertionError('Explicit test reference must not fetch'))
        image = Image.new('RGBA', (160, 180), (30, 130, 200, 255))
        image.putpixel((0, 0), (0, 0, 0, 0))
        self.character = CharacterProject.create(self.root, 'Prompt route fixture')
        self.character.set_image(png_bytes(image), 0)
        self.history = JobHistory(self.root, self.matrix, 'characters')
        self.handler = object.__new__(server.Handler)
        self.handler.path = '/api/characters/' + self.character.id + '/generate-parts'
        self.handler.send = lambda value, *args, **kwargs: value
        self.reference = 'https://assets.example/exact-reference.png'

    def tearDown(self):
        self.matrix.tunnel.close()
        self.temporary.cleanup()

    def request(self, **fields):
        return {'nonce': uuid.uuid4().hex, 'revision': self.character.meta['revision'], **fields}

    def post(self, payload, reference=None):
        with patch.object(self.server, 'get_character', return_value=self.character), \
                patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(self.server, 'CHARACTER_JOBS', self.history), \
                patch.object(self.server, 'character_reference', reference or Mock(return_value=self.reference)):
            return self.handler.post(payload)

    def persisted(self, job):
        return json.loads(self.history.path(self.character.id, job['id']).read_text())

    def test_profile_character_request_matches_plan_and_is_one_mocked_generation(self):
        description='Stocky blue warrior, true profile facing LEFT, one near-side shoulder patch'
        item=generation_plan({'mode':'character','prompt':description})['items'][0]
        self.handler.path='/api/characters/'+self.character.id+'/generate'
        before=copy.deepcopy(self.character.meta)
        payload=self.request(prompt=description)
        job=self.post(payload)
        self.matrix.request.assert_called_once()
        route,wire=self.matrix.request.call_args.args
        self.assertIn('/gpt-image-2.5-flare/generate',route)
        self.assertEqual(wire['input']['prompt'],item['prompt'])
        self.assertEqual(wire['input']['aspect_ratio'],item['ratio'])
        self.assertEqual(wire['request_num'],1)
        self.assertEqual(job['prompt'],item['prompt'])
        self.assertEqual(job['description'],description)
        self.assertEqual(self.persisted(job),job)
        self.assertEqual(self.character.meta,before)
        self.matrix.request.reset_mock()
        self.assertEqual(self.post(payload),job)
        self.matrix.request.assert_not_called()

    def test_view_request_preserves_exact_reference_and_camera_intent_without_profile_forcing(self):
        self.handler.path='/api/characters/'+self.character.id+'/generate-view'
        before=copy.deepcopy(self.character.meta)
        cases=[('true profile facing LEFT',['walk'],'profile'),
               ('near-profile 75–80 degrees facing RIGHT',['walk'],'requested'),
               ('front view',['idle'],'requested')]
        for target,actions,policy in cases:
            with self.subTest(target=target):
                self.matrix.request.reset_mock()
                payload=self.request(target_view=target,target_actions=actions)
                job=self.post(payload)
                self.matrix.request.assert_called_once()
                wire=self.matrix.request.call_args.args[1]
                self.assertEqual(wire['input']['input_images'],[self.reference])
                self.assertEqual(wire['input']['prompt'],view_prompt(target,actions))
                self.assertEqual(wire['request_num'],1)
                self.assertEqual(job['target_view'],target)
                self.assertEqual(job['target_actions'],actions)
                self.assertEqual(job['source_image_id'],before['image_id'])
                self.assertEqual(job['prompt'],wire['input']['prompt'])
                self.assertEqual(requested_camera_policy(target)['view'],policy)
                if policy=='profile':self.assertIn('Use a true lateral profile',wire['input']['prompt'])
                else:self.assertNotIn('Use a true lateral profile',wire['input']['prompt'])
                self.assertEqual(self.persisted(job),job)
                self.assertEqual(self.character.meta,before)
                self.matrix.request.reset_mock()
                replay=self.post({**payload,'target_view':'conflicting replay camera'})
                self.assertEqual(replay,job)
                self.matrix.request.assert_not_called()

    def test_provider_no_back_matches_preview_prompt_and_supported_ratio(self):
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['body'], 'include_back': False,
                                'instructions_by_sheet': {'body': '保留脸和衣领'}})
        item = plan['items'][0]
        result = self.matrix.generate_part_sheet('body', self.reference, '保留脸和衣领', 'right', False)
        self.assertEqual(result, 'mock-flare-generation')
        route, request = self.matrix.request.call_args.args
        self.assertIn('/gpt-image-2.5-flare/generate', route)
        self.assertEqual(request['input']['prompt'], item['prompt'])
        self.assertEqual(request['input']['aspect_ratio'], '3:2')
        self.assertEqual(request['input']['input_images'], [self.reference])
        self.assertEqual(request['request_num'], 1)
        self.assertNotIn('reference backpack', request['input']['prompt'])

    def test_server_persists_the_same_three_part_layout_it_submits(self):
        payload = self.request(sheets=['body'], include_back=False,
                               instructions_by_sheet={'body': '保留头部与原衣服，不新增配件'})
        revision = self.character.meta['revision']
        jobs = self.post(payload)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        record = self.persisted(job)
        self.assertEqual(record, job)
        self.assertEqual(record['parts'], ['head', 'torso', 'pelvis'])
        self.assertEqual((record['columns'], record['rows']), (3, 1))
        self.assertFalse(record['include_back'])
        self.assertEqual(record['prompt_version'], PROMPT_VERSION)
        self.assertEqual(record['source_image_id'], self.character.meta['image_id'])
        self.assertEqual(record['status'], 'pending')
        submitted = self.matrix.request.call_args.args[1]['input']
        self.assertEqual(submitted['prompt'], record['prompt'])
        self.assertEqual(submitted['aspect_ratio'], '3:2')
        self.assertEqual(self.character.meta['revision'], revision)
        self.matrix.request.assert_called_once()

    def test_server_routes_only_each_sheet_notes_into_its_actual_request_and_job(self):
        notes = {'arms': '蓝色上臂和白色肘部袖口；完整裸露前臂与手。',
                 'legs': '深色裤、灰绑腿与棕鞋，两鞋尖向左。'}
        self.character.meta['settings']['facing'] = 'left'
        jobs = self.post(self.request(sheets=['arms', 'legs'], include_back=False,
                                      instructions_by_sheet=notes))
        self.assertEqual([job['sheet'] for job in jobs], ['arms', 'legs'])
        self.assertEqual(self.matrix.request.call_count, 2)
        for job, call in zip(jobs, self.matrix.request.call_args_list):
            sheet = job['sheet']
            other_sheet = 'legs' if sheet == 'arms' else 'arms'
            wire = call.args[1]['input']
            self.assertEqual(job['instructions'], notes[sheet])
            self.assertEqual(wire['prompt'], job['prompt'])
            self.assertIn(notes[sheet], wire['prompt'])
            self.assertNotIn(notes[other_sheet], wire['prompt'])
            self.assertEqual(job['parts'], SHEETS[sheet]['parts'])
            self.assertEqual((job['columns'], job['rows'], wire['aspect_ratio']), (2, 3, '2:3'))
            self.assertEqual(self.persisted(job), job)
        self.assertIn('均朝左', jobs[1]['prompt'])

    def test_legacy_robot_request_retains_back_and_shared_notes(self):
        job = self.post(self.request(sheets=['body'], instructions='保留原背包通风口'))[0]
        self.assertTrue(job['include_back'])
        self.assertEqual(job['parts'], ['head', 'torso', 'pelvis', 'back'])
        self.assertEqual((job['columns'], job['rows']), (2, 2))
        request = self.matrix.request.call_args.args[1]['input']
        self.assertEqual(request['aspect_ratio'], '1:1')
        self.assertEqual(request['prompt'], job['prompt'])
        self.assertIn('保留原背包通风口', job['prompt'])

    def test_provider_and_server_complete_limbs_match_plan_and_persist_review_marker(self):
        notes = '保留原侧向视角和衣服，近远肢体完整、双鞋水平朝左。'
        self.character.meta['settings']['facing'] = 'left'
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['limbs'],
                                'include_back': False, 'instructions_by_sheet': {'limbs': notes}}, 'left')
        job = self.post(self.request(sheets=['limbs'], include_back=False,
                                    instructions_by_sheet={'limbs': notes}))[0]
        item = plan['items'][0]
        self.matrix.request.assert_called_once()
        wire = self.matrix.request.call_args.args[1]
        self.assertEqual(wire['request_num'], 1)
        self.assertEqual(wire['input']['prompt'], item['prompt'])
        self.assertEqual(wire['input']['aspect_ratio'], item['ratio'])
        self.assertEqual(wire['input']['input_images'], [self.reference])
        self.assertEqual(job['parts'], item['parts'])
        self.assertEqual((job['columns'], job['rows']), (2, 2))
        self.assertTrue(job['requires_reviewed_layout'])
        self.assertEqual(job['instructions'], notes)
        self.assertEqual(self.persisted(job), job)

    def test_provider_and_server_character_assets_preserve_wide_layout_and_review_marker(self):
        notes = '保留原厨师脸、围裙与单侧袖章，完整手掌与鞋。'
        self.character.meta['settings']['facing'] = 'left'
        plan = generation_plan({'mode': 'complete-parts', 'sheets': ['character-assets'],
                                'include_back': False, 'instructions_by_sheet': {'character-assets': notes}},
                               'left')
        job = self.post(self.request(sheets=['character-assets'], include_back=False,
                                    instructions_by_sheet={'character-assets': notes}))[0]
        item = plan['items'][0]
        self.matrix.request.assert_called_once()
        wire = self.matrix.request.call_args.args[1]
        self.assertEqual(wire['request_num'], 1)
        self.assertEqual(wire['input']['prompt'], item['prompt'])
        self.assertEqual(wire['input']['aspect_ratio'], '3:2')
        self.assertEqual(wire['input']['input_images'], [self.reference])
        self.assertEqual(job['parts'], item['parts'])
        self.assertEqual((job['columns'], job['rows']), (4, 2))
        self.assertTrue(job['requires_reviewed_layout'])
        self.assertEqual(job['prompt_version'], 'animation-parts-v21')
        self.assertEqual(job['instructions'], notes)
        self.assertEqual(self.persisted(job), job)

    def test_server_overlapping_groups_fail_before_reference_or_paid_request(self):
        reference = Mock(side_effect=AssertionError('Overlap must not publish any reference'))
        for sheets in (['limbs', 'arms'], ['arms', 'limbs'], ['limbs', 'legs'], ['body', 'limbs', 'legs']):
            with self.subTest(sheets=sheets), self.assertRaises(UserError):
                self.post(self.request(sheets=sheets), reference=reference)
        reference.assert_not_called()
        self.matrix.request.assert_not_called()
        self.assertFalse((self.character.directory / 'jobs').exists())

    def test_complete_limbs_cannot_be_silently_applied_as_four_segment_cells(self):
        spec = sheet_spec('limbs')
        job = {'id': uuid.uuid4().hex, 'mode': 'complete-parts', 'candidate_ready': True,
               'source_image_id': self.character.meta['image_id'], 'sheet': 'limbs',
               'parts': spec['parts'], 'columns': 2, 'rows': 2, 'requires_reviewed_layout': True}
        before = copy.deepcopy(self.character.meta)
        disk = (self.character.directory/'character.json').read_bytes()
        image = Image.new('RGBA', (256, 256), '#147edc')
        for variant in (job, {**job, 'requires_reviewed_layout': False}, {**job, 'sheet': 'arms'}):
            with self.subTest(variant=variant), self.assertRaisesRegex(UserError, 'apply-part-layout'):
                self.character.apply_part_sheet({'revision': before['revision'], 'mode': 'redraw'}, variant, image)
            self.assertEqual(self.character.meta, before)
            self.assertEqual((self.character.directory/'character.json').read_bytes(), disk)
            self.assertEqual(list((self.character.directory/'part-images').glob('*.png')), [])
        self.matrix.request.assert_not_called()

    def test_complete_asset_image_requires_reviewed_layout_even_if_marker_is_missing(self):
        spec = sheet_spec('character-assets', False)
        job = {'id': uuid.uuid4().hex, 'mode': 'complete-parts', 'candidate_ready': True,
               'source_image_id': self.character.meta['image_id'], 'sheet': 'character-assets',
               'parts': spec['parts'], 'columns': 4, 'rows': 2, 'requires_reviewed_layout': True}
        before = copy.deepcopy(self.character.meta)
        disk = (self.character.directory / 'character.json').read_bytes()
        image = Image.new('RGBA', (300, 200), '#147edc')
        for variant in (job, {**job, 'requires_reviewed_layout': False}):
            with self.subTest(variant=variant), self.assertRaisesRegex(UserError, 'apply-part-layout'):
                self.character.apply_part_sheet({'revision': before['revision'], 'mode': 'redraw'}, variant, image)
            self.assertEqual(self.character.meta, before)
            self.assertEqual((self.character.directory / 'character.json').read_bytes(), disk)
            self.assertEqual(list((self.character.directory / 'part-images').glob('*.png')), [])
        self.matrix.request.assert_not_called()

    def test_invalid_flags_and_notes_stop_before_reference_publication_or_generation(self):
        cases = ({'include_back': 'false'},
                 {'instructions_by_sheet': {'arms': '长' * 401}},
                 {'instructions_by_sheet': {'legs': []}},
                 {'instructions': 'shared', 'instructions_by_sheet': {'arms': 'specific'}},
                 {'instructions': '长' * 1201})
        reference = Mock(side_effect=AssertionError('Invalid prompt must not publish any reference'))
        for changed in cases:
            with self.subTest(fields=changed):
                with self.assertRaises(UserError):
                    self.post(self.request(sheets=['arms'], **changed), reference=reference)
        reference.assert_not_called()
        self.matrix.request.assert_not_called()
        self.assertFalse((self.character.directory / 'jobs').exists())

    def test_same_nonce_does_not_generate_again_or_replace_original_layout(self):
        payload = self.request(sheets=['body'], include_back=False,
                               instructions_by_sheet={'body': '保留原头部和身体'})
        first = self.post(payload)
        self.matrix.request.reset_mock()
        reference = Mock(side_effect=AssertionError('Nonce replay must not publish'))
        replay = self.post(payload, reference=reference)
        self.assertEqual(replay, first)
        self.assertEqual(self.persisted(replay[0]), first[0])
        reference.assert_not_called()
        self.matrix.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
