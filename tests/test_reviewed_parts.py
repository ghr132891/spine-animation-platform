"""Explicit layouts reuse real artwork without template or generation calls."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from app.character import CharacterProject, LABELS
from app.core import UserError, png_bytes, read_image
from app.reviewed_parts import extract_parts, pixel_box, remove_matte, aligned_chain
from app.rig import RigProject

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'storage/characters/5f2902913f5d4c3aaa8b36b76d37e21e'
SHEET_ID = '2e800ae34aa1526f8f097c74a3d2ead6'
SHEET = SOURCE / 'jobs' / (SHEET_ID + '.png')
NODE = shutil.which('node') or '/Users/tu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'


def arm_item(side='l'):
    left = side == 'l'
    box = [149, 69, 299, 452] if left else [549, 70, 700, 452]
    return {'name': side + '-upper-arm', 'chain': 'arm', 'box': box,
            'pins': {'shoulder': [.50, .065], 'elbow': [.50, .480],
                     'wrist': [.39 if left else .57, .767],
                     'hand': [.39 if left else .57, .935]}}


def leg_item(side='l'):
    left = side == 'l'
    legbox = [155, 503, 326, 852] if left else [518, 503, 687, 852]
    shoebox = [143, 1019, 349, 1152] if left else [500, 1020, 709, 1153]
    return {'name': side + '-upper-leg', 'chain': 'leg', 'canvas': [200, 346],
            'pieces': [{'box': shoebox, 'dest': [52, 248, 199, 346]},
                       {'box': legbox, 'dest': [10, 0, 185, 258]}],
            'pins': {'hip': [.50, .087], 'knee': [.50, .529],
                     'ankle': [.50, .777], 'toe': [.755, .965]}}


class ReviewedPartSchemaTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new('RGBA', (100, 120), 'white')
        ImageDraw.Draw(self.image).rectangle((10, 10, 80, 110), fill='#14385c')

    def test_invalid_crop_bounds_and_number_types_are_rejected(self):
        for box in ([0, 0, 101, 30], [0, 0, 30, 121], [20, 0, 10, 30],
                    [-1, 0, 10, 30], [0, 0, 0, 30], [0, 0, True, 30],
                    [0, 0, float('nan'), 30], [0, 0, float('inf'), 30], [0, 10]):
            with self.subTest(box=box), self.assertRaises(UserError):
                pixel_box(box, self.image.size)

    def test_empty_unknown_duplicate_and_overlapping_chain_coverage_are_rejected(self):
        valid = {'name': 'l-hand', 'box': [10, 10, 80, 100]}
        arm = {'name': 'l-upper-arm', 'box': [10, 10, 80, 110], 'chain': 'arm',
               'pins': {'shoulder': [.5, .05], 'elbow': [.5, .4],
                        'wrist': [.5, .7], 'hand': [.5, .95]}}
        cases = [[], [{'name': 'wrong', 'box': [10, 10, 80, 100]}],
                 [valid, valid], [arm, valid], [valid, arm],
                 [{**arm, 'name': 'l-hand'}], [{**arm, 'chain': 'spine'}],
                 [{'name': 'l-hand', 'box': [0, 0, 5, 5]}]]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(UserError):
                extract_parts(self.image, case, LABELS)

    def test_chain_pins_are_complete_normalized_and_not_degenerate(self):
        valid = {'name': 'l-upper-arm', 'box': [10, 10, 80, 110], 'chain': 'arm',
                 'pins': {'shoulder': [.5, .05], 'elbow': [.5, .4],
                          'wrist': [.5, .7], 'hand': [.5, .95]}}
        bad_pins = [None, {'shoulder': [.5, .05]},
                    {**valid['pins'], 'extra': [.3, .4]},
                    {**valid['pins'], 'hand': [1.01, .8]},
                    {**valid['pins'], 'hand': [False, .8]},
                    {**valid['pins'], 'hand': [float('nan'), .8]},
                    {**valid['pins'], 'hand': [.5, .055]},
                    {**valid['pins'], 'elbow': [.5, .051]},
                    {**valid['pins'], 'wrist': [.5, .401]}]
        for pins in bad_pins:
            with self.subTest(pins=pins), self.assertRaises(UserError):
                extract_parts(self.image, [{**valid, 'pins': pins}], LABELS)

    def test_composite_pixels_follow_explicit_destinations_and_overlay_order(self):
        candidate = Image.new('RGBA', (64, 32), 'white')
        d = ImageDraw.Draw(candidate)
        d.rectangle((4, 4, 27, 27), fill='red')
        d.rectangle((36, 4, 59, 27), fill='blue')
        item = {'name': 'l-hand', 'canvas': [32, 32], 'pieces': [
            {'box': [4, 4, 28, 28], 'dest': [0, 0, 24, 24]},
            {'box': [36, 4, 60, 28], 'dest': [8, 8, 32, 32]}]}
        _, sprite, record = extract_parts(candidate, [item], LABELS)[0]
        self.assertEqual(sprite.getpixel((3, 3)), (255, 0, 0, 255))
        self.assertEqual(sprite.getpixel((10, 10)), (0, 0, 255, 255))
        self.assertEqual(record['layout']['mode'], 'reviewed-pixels')
        for bad in ({**item, 'canvas': [True, 32]}, {**item, 'canvas': [15, 32]},
                    {**item, 'canvas': [32, 2049]}, {**item, 'pieces': []},
                    {**item, 'pieces': [{'box': [0, 0, 10, 10], 'dest': [0, 0, 33, 10]}]}):
            with self.subTest(bad=bad), self.assertRaises(UserError):
                extract_parts(candidate, [bad], LABELS)

    def test_white_matte_is_removed_only_at_outer_edge(self):
        image = Image.new('RGBA', (64, 64), 'white')
        d = ImageDraw.Draw(image)
        d.rectangle((14, 14, 50, 50), fill=(210, 210, 210, 255))
        d.rectangle((15, 15, 49, 49), fill=(20, 20, 20, 255))
        d.rectangle((24, 24, 40, 40), fill='white')
        output = remove_matte(image, 32)
        self.assertEqual(output.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(output.getpixel((32, 32)), (255, 255, 255, 255))
        edge = output.getpixel((14, 30))
        self.assertLess(max(edge[:3]), 100)
        self.assertLess(edge[3], 255)
        self.assertGreater(edge[3], 0)

    def test_optional_default_pins_work_for_a_torso_crop(self):
        # The preexisting torso pin table has an outside end; optional new API
        # pins must still give a usable default instead of failing validation.
        result = extract_parts(self.image, [{'name': 'torso', 'box': [10, 10, 80, 110]}], LABELS)
        self.assertEqual(result[0][0], 'torso')

    def test_valid_sprite_cannot_be_bound_to_degenerate_guide(self):
        item = {'name': 'l-upper-arm', 'box': [10, 10, 80, 110], 'chain': 'arm',
                'pins': {'shoulder': [.5, .05], 'elbow': [.5, .4],
                         'wrist': [.5, .7], 'hand': [.5, .95]}}
        name, sprite, record = extract_parts(self.image, [item], LABELS)[0]
        # Artwork joints are distinct, but the guide shrinks a whole arm to
        # ten image pixels. Binding it would create nearly coincident pivots.
        guide = {'l-shoulder': [.4, .4], 'l-hand': [.4, .5]}
        with self.assertRaises(UserError):
            aligned_chain(name, sprite, record, guide, (100, 100))


@unittest.skipUnless(SHEET.exists(), 'recorded Flare candidate is unavailable')
class ReviewedCandidateCharacterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        original = CharacterProject(SOURCE)
        self.original_metadata = (SOURCE / 'character.json').read_bytes()
        self.c = CharacterProject.create(self.storage, 'reviewed candidate QA copy')
        self.c.set_image(png_bytes(original.image()), 0)
        self.c.update({'revision': self.c.meta['revision'], 'landmarks': original.meta['landmarks'],
                       'masks': original.meta['masks'], 'settings': original.meta['settings']})
        self.sheet = read_image(SHEET.read_bytes())
        self.job = json.loads((SOURCE / 'jobs' / (SHEET_ID + '.json')).read_text())
        self.job.update(candidate_ready=True, source_image_id=self.c.meta['image_id'])
        self.source_pixels = png_bytes(self.c.image())
        self.addCleanup(self.assert_original_unchanged)

    def assert_original_unchanged(self):
        self.assertEqual((SOURCE / 'character.json').read_bytes(), self.original_metadata)

    def apply(self, items):
        return self.c.apply_part_layout({'revision': self.c.meta['revision'], 'parts': items}, self.job, self.sheet)

    def test_stale_source_revision_unfinished_job_and_late_invalid_pins_are_atomic(self):
        before = copy.deepcopy(self.c.meta)
        before_disk = (self.c.directory / 'character.json').read_bytes()
        cases = [({'revision': 999, 'parts': [arm_item()]}, self.job),
                 ({'revision': before['revision'], 'parts': [arm_item()]}, {**self.job, 'source_image_id': '0'*64}),
                 ({'revision': before['revision'], 'parts': [arm_item()]}, {**self.job, 'candidate_ready': False}),
                 ({'revision': before['revision'], 'parts': [arm_item()]}, {**self.job, 'mode': 'character'})]
        invalid = arm_item('r')
        invalid['pins'] = {'shoulder': [.5, .5], 'hand': [.5, .56], 'elbow': [1, 0], 'wrist': [0, 1]}
        cases.append(({'revision': before['revision'], 'parts': [arm_item(), invalid]}, self.job))
        for request, job in cases:
            with self.subTest(job=job.get('mode')), self.assertRaises(UserError):
                self.c.apply_part_layout(request, job, self.sheet)
            self.assertEqual(self.c.meta, before)
            self.assertEqual((self.c.directory / 'character.json').read_bytes(), before_disk)
            self.assertEqual(list((self.c.directory / 'part-images').glob('*.png')), [])
            self.assertEqual(png_bytes(self.c.image()), self.source_pixels)

    def test_whole_arm_maps_real_chain_endpoints_and_replaces_three_old_parts(self):
        points = copy.deepcopy(self.c.meta['landmarks'])
        self.apply([arm_item()])
        self.assertTrue(np.allclose(self.c.meta['landmarks']['l-shoulder'], points['l-shoulder']))
        self.assertTrue(np.allclose(self.c.meta['landmarks']['l-hand'], points['l-hand']))
        record = self.c.meta['part_layers']['l-upper-arm']
        self.assertEqual(set(record['coverage']), {'l-upper-arm', 'l-lower-arm', 'l-hand'})
        names = {p['name'] for p in self.c.parts()}
        self.assertIn('l-upper-arm', names)
        self.assertNotIn('l-lower-arm', names)
        self.assertNotIn('l-hand', names)
        self.assertIn('r-lower-arm', names)
        self.assertEqual(png_bytes(self.c.image()), self.source_pixels)
        self.assertEqual(CharacterProject(self.c.directory).meta, self.c.meta)

    def test_single_explicit_part_replacement_removes_overlapping_old_chain(self):
        self.apply([arm_item()])
        self.apply([{'name': 'l-hand', 'box': [149, 310, 260, 452]}])
        self.assertNotIn('l-upper-arm', self.c.meta['part_layers'])
        self.assertIn('l-hand', self.c.meta['part_layers'])
        self.assertIn('l-lower-arm', {p['name'] for p in self.c.parts()})

    def test_legacy_part_sheet_cannot_leave_an_overlapping_continuous_chain(self):
        self.apply([arm_item()])
        candidate = Image.new('RGBA', (100, 100), 'white')
        ImageDraw.Draw(candidate).rectangle((20, 20, 80, 80), fill='#e9ac60')
        job = {**self.job, 'id': uuid.uuid4().hex, 'sheet': 'arms', 'parts': ['l-hand'], 'columns': 1, 'rows': 1}
        self.c.apply_part_sheet({'revision': self.c.meta['revision'], 'mode': 'redraw'}, job, candidate)
        self.assertNotIn('l-upper-arm', self.c.meta['part_layers'])
        self.assertIn('l-hand', {p['name'] for p in self.c.parts()})

    def test_reset_covered_child_restores_whole_source_chain_and_keeps_other_layers(self):
        self.apply([arm_item('l'), arm_item('r')])
        self.c.reset_part({'revision': self.c.meta['revision'], 'name': 'l-hand'})
        self.assertNotIn('l-upper-arm', self.c.meta['part_layers'])
        self.assertIn('r-upper-arm', self.c.meta['part_layers'])
        names = {p['name'] for p in self.c.parts()}
        self.assertTrue({'l-upper-arm', 'l-lower-arm', 'l-hand'} <= names)
        self.assertNotIn('r-hand', names)
        self.assertEqual(png_bytes(self.c.image()), self.source_pixels)

    def test_reset_standalone_part_keeps_unrelated_adjustments(self):
        self.apply([{'name': 'l-hand', 'box': [149, 310, 260, 452]}])
        self.c.meta['part_review'] = {'adjustments': {'l-hand': {'x': 4}, 'head': {'x': 2}},
                                      'draw_order': ['head']}
        self.c.reset_part({'revision': self.c.meta['revision'], 'name': 'l-hand'})
        self.assertNotIn('l-hand', self.c.meta['part_layers'])
        self.assertEqual(self.c.meta['part_review']['adjustments'], {'head': {'x': 2}})
        self.assertEqual(self.c.meta['part_review']['draw_order'], ['head'])

    def test_explicit_face_mask_survives_continuous_arm_pivot_calibration(self):
        W, H = self.c.image().size
        outline = [(280,18),(591,18),(591,325),(574,365),(544,404),(508,426),
                   (469,438),(407,429),(370,406),(331,378),(292,334),(278,270)]
        self.c.update({'revision': self.c.meta['revision'], 'landmarks': self.c.meta['landmarks'],
                       'masks': {'head': [[x/W,y/H] for x,y in outline]},
                       'settings': self.c.meta['settings']})
        self.apply([arm_item('l'), arm_item('r')])
        head = next(p for p in self.c.parts() if p['name'] == 'head')
        for x, y in ((551, 341), (524, 399), (438, 426)):
            original = self.c.cutout().getpixel((x,y))
            self.assertGreater(original[3], 0)
            self.assertEqual(head['image'].getpixel((x-head['box'][0],y-head['box'][1])), original)

    def test_actual_candidate_build_has_local_normalized_weights_and_rigid_shoe_vertices(self):
        self.apply([arm_item('l'), arm_item('r'), leg_item('l'), leg_item('r')])
        result = self.c.build(self.storage, {'revision': self.c.meta['revision'], 'nonce': uuid.uuid4().hex,
            'landmarks': self.c.meta['landmarks'], 'masks': self.c.meta['masks'], 'settings': self.c.meta['settings']})
        version = result['versions'][-1]
        self.assertEqual(version['generator'], 'reviewed-chain-v1')
        rig = RigProject(self.storage / 'rigs' / version['rig_id'])
        self.assertTrue(rig.validate()['passed'])
        skeleton = rig.state['skeleton']; attachments = skeleton['skins'][0]['attachments']
        for side in ('l', 'r'):
            for chain in ('arm', 'leg'):
                name = side + '-upper-' + chain
                self.assertEqual(attachments[name][name]['type'], 'mesh')
                for child in ('lower-'+chain, 'hand' if chain == 'arm' else 'foot'):
                    self.assertNotIn(side + '-' + child, attachments)
                a = attachments[name][name]; offset = 0; allowed = {
                    side + '-' + k for k in (('upper-arm', 'lower-arm', 'hand') if chain == 'arm' else ('upper-leg', 'lower-leg', 'foot'))}
                sole_points = 0
                for i in range(len(a['uvs']) // 2):
                    n = a['vertices'][offset]; vals = a['vertices'][offset+1:offset+1+n*4]; offset += 1+n*4
                    names = [skeleton['bones'][vals[j*4]]['name'] for j in range(n)]
                    self.assertTrue(set(names) <= allowed)
                    self.assertAlmostEqual(sum(vals[3::4]), 1, places=6)
                    if chain == 'leg' and a['uvs'][2*i+1] > .92:
                        # The toe/sole end is below the ankle blend and wholly
                        # follows the rigid foot, rather than a soft leg mix.
                        self.assertEqual(names, [side+'-foot'])
                        self.assertEqual(vals[3], 1)
                        sole_points += 1
                self.assertEqual(offset, len(a['vertices']))
                if chain == 'leg':self.assertGreater(sole_points, 10)
        files, _ = rig.files(); folder = self.storage / 'runtime'; folder.mkdir()
        for name, content in files.items():
            path = folder / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        if not Path(NODE).exists():self.skipTest('official-runtime node interpreter unavailable')
        script = '''
const {runtime,load,pose}=require(process.argv[1]);
const s=runtime('4.2'),rig=load(s,'4.2',process.argv[2],'skeleton.json','skeleton.atlas');
let motions={};for(const animation of rig.data.animations){let samples=[];
for(let i=0;i<=32;i++)samples.push(pose(s,'4.2',rig,animation.name,animation.duration*i/32));
if(!samples.every(p=>p.geometry.length&&p.flat.every(Number.isFinite)))throw Error('invalid geometry');
const last=samples[32],first=samples[0];
const error=Math.max(...first.flat.map((v,i)=>Math.abs(v-last.flat[i])));
if(error>.01)throw Error('non-looping geometry');
motions[animation.name]={samples:samples.length,loopError:error};}
console.log(JSON.stringify(motions));
'''
        completed = subprocess.run([NODE, '-e', script, str(ROOT/'tools/verify_rig_runtime.cjs'), str(folder)],
                                   capture_output=True, text=True, timeout=45)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        runtime = json.loads(completed.stdout)
        self.assertEqual(set(runtime), {'idle', 'walk'})
        self.assertTrue(all(x['samples'] == 33 for x in runtime.values()))
        motion_check = subprocess.run([NODE, str(ROOT/'tools/validate_agent_runtime.cjs'),
                                       str(folder), '4.2', 'skeleton.json', 'skeleton.atlas'],
                                      capture_output=True, text=True, timeout=45)
        report = json.loads(motion_check.stdout)
        self.assertTrue(report['passed'], report.get('error'))
        walks = [clip for clip in report['motion']['clips'] if clip['animation'] == 'walk']
        self.assertTrue(walks)
        for clip in walks:
            # Valid finite meshes also occur if 4.2 ignores the rotation
            # field; the real loaded mesh must move through its joint chain.
            self.assertTrue(clip['visibleMotionObserved'])
            self.assertTrue(clip['articulatedMotionObserved'])
            self.assertFalse(clip['rootOnlyMotion'])


if __name__ == '__main__':unittest.main()


class PolygonCropTests(unittest.TestCase):
    def fixture(self):
        im=Image.new('RGBA',(40,40),(40,110,180,255));im.putpixel((0,0),(0,0,0,0));im.putpixel((20,20),(255,255,255,255));return im
    def test_exclusion_removes_neighbour_without_modifying_retained_material(self):
        im=self.fixture();item={'name':'torso','box':[0,0,40,40],'exclude_polygons':[[[0,0],[.25,0],[.25,1],[0,1]]]}
        name,sprite,record=extract_parts(im,[item],LABELS)[0]
        self.assertEqual(sprite.getpixel((5,20))[3],0)
        for x,y in [(20,20),(30,25)]:self.assertEqual(sprite.getpixel((x,y)),im.getpixel((x,y)))
        self.assertEqual(sprite.size,im.size);self.assertIn('exclude_polygons',record['layout'])
    def test_crop_is_applied_before_rotation(self):
        im=self.fixture();item={'name':'head','box':[0,0,40,40],'crop_polygon':[[0,0],[1,0],[0,1]]}
        a=extract_parts(im,[item],LABELS)[0][1];b=extract_parts(im,[{**item,'rotation':90}],LABELS)[0][1]
        self.assertEqual(b.tobytes(),a.rotate(90,expand=True,resample=Image.Resampling.BICUBIC).tobytes())
    def test_invalid_or_empty_crop_does_not_create_a_usable_part(self):
        for value in [[],[[0,0],[1,1]],[[0,0],[2,0],[0,1]],[[True,0],[1,0],[0,1]]]:
            with self.subTest(value=value),self.assertRaises(UserError):extract_parts(self.fixture(),[{'name':'head','box':[0,0,40,40],'crop_polygon':value}],LABELS)
        with self.assertRaises(UserError):extract_parts(self.fixture(),[{'name':'head','box':[0,0,40,40],'exclude_polygons':[[[0,0],[1,0],[1,1],[0,1]]]}],LABELS)
