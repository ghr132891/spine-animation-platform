"""A complete costume stays one surface with setup-local hip/torso weights."""
import math
import unittest

import numpy as np
from PIL import Image, ImageDraw

from app.character import LABELS
from app.core import UserError
from app.part_layers import aligned_part
from app.part_review import apply_adjustment
from app.reviewed_parts import costume_mesh, extract_parts
from app.rig import validate_state


def costume_fixture():
    image = Image.new('RGBA', (240, 320))
    draw = ImageDraw.Draw(image)
    draw.polygon([(75, 15), (130, 15), (145, 130), (175, 290), (25, 290), (60, 130)],
                 fill='#24619a')
    draw.rectangle((79, 36, 125, 74), fill='white')
    draw.line((30, 250, 170, 250), fill='#a32c28', width=5)
    item = {'name': 'torso', 'group': 'costume', 'box': [20, 10, 180, 300],
            'start_pin': [.5, .62], 'end_pin': [.5, .09]}
    return image, item


def influences(mesh):
    offset = 0
    result = []
    for _ in range(len(mesh['uvs']) // 2):
        count = mesh['vertices'][offset]
        offset += 1
        values = mesh['vertices'][offset:offset + count * 4]
        result.append([values[i:i + 4] for i in range(0, len(values), 4)])
        offset += count * 4
    if offset != len(mesh['vertices']):
        raise AssertionError('Weighted vertices do not match their UV count')
    return result


class CostumeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.image, self.item = costume_fixture()

    def test_complete_costume_keeps_one_crop_covering_torso_and_pelvis(self):
        name, sprite, record = extract_parts(self.image, [self.item], LABELS)[0]
        self.assertEqual(name, 'torso')
        self.assertEqual(sprite.tobytes(), self.image.crop(self.item['box']).tobytes())
        self.assertEqual(sprite.getpixel((70, 45)), (255, 255, 255, 255))
        self.assertEqual(sprite.getpixel((70, 240)), (163, 44, 40, 255))
        self.assertEqual(record['group'], 'costume')
        self.assertEqual(record['coverage'], ['torso', 'pelvis'])
        self.assertEqual(record['start_pin'], self.item['start_pin'])
        self.assertEqual(record['end_pin'], self.item['end_pin'])
        self.assertNotIn('chain', record)

    def test_group_only_accepts_torso_costume_without_a_limb_chain(self):
        cases = [{**self.item, 'group': None}, {**self.item, 'group': 'body'},
                 {**self.item, 'name': 'pelvis'}, {**self.item, 'name': 'l-upper-arm'},
                 {**self.item, 'chain': 'arm'}]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(UserError):
                extract_parts(self.image, [item], LABELS)

    def test_costume_pins_are_explicit_normalized_and_distinct(self):
        missing_start = {key: value for key, value in self.item.items() if key != 'start_pin'}
        missing_end = {key: value for key, value in self.item.items() if key != 'end_pin'}
        cases = [missing_start, missing_end, {**self.item, 'start_pin': [-.1, .6]},
                 {**self.item, 'end_pin': [.5, 1.1]}, {**self.item, 'start_pin': [True, .6]},
                 {**self.item, 'start_pin': [float('nan'), .6]},
                 {**self.item, 'end_pin': self.item['start_pin']}]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(UserError):
                extract_parts(self.image, [item], LABELS)

    def test_costume_and_independent_pelvis_are_rejected_in_both_orders(self):
        pelvis = {'name': 'pelvis', 'box': [25, 200, 170, 290]}
        for items in ([self.item, pelvis], [pelvis, self.item]):
            with self.subTest(items=items), self.assertRaises(UserError):
                extract_parts(self.image, items, LABELS)

    def test_rotation_uses_pins_on_the_complete_final_costume_sprite(self):
        item = {**self.item, 'rotation': 90, 'start_pin': [.62, .5], 'end_pin': [.09, .5]}
        _, sprite, record = extract_parts(self.image, [item], LABELS)[0]
        expected = self.image.crop(item['box']).rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
        self.assertEqual(sprite.size, (290, 160))
        self.assertEqual(sprite.tobytes(), expected.tobytes())
        self.assertEqual(record['layout']['sprite_size'], [290, 160])
        self.assertEqual(record['start_pin'], [.62, .5])
        self.assertEqual(record['end_pin'], [.09, .5])

    def test_ordinary_torso_crop_retains_legacy_optional_pins(self):
        _, _, record = extract_parts(self.image, [{'name': 'torso', 'box': self.item['box']}], LABELS)[0]
        self.assertNotIn('group', record)
        self.assertNotIn('coverage', record)
        self.assertIn('start_pin', record)
        self.assertIn('end_pin', record)


class CostumeMeshTests(unittest.TestCase):
    def setUp(self):
        image, item = costume_fixture()
        name, sprite, record = extract_parts(image, [item], LABELS)[0]
        self.W, self.H = 600, 500
        self.scale = 1.7
        self.origin = np.array([300., 500.])
        self.landmarks = {'hip': [.5, .60], 'neck': [.52, .27]}
        self.part = aligned_part(name, sprite, record, self.landmarks, (self.W, self.H),
                                 lambda _: [('torso', 'neck', 'hip')])
        self.part['group'] = 'costume'
        self.bones = [{'name': 'root'}, {'name': 'hip', 'parent': 'root'},
                      {'name': 'torso', 'parent': 'hip'}, {'name': 'head', 'parent': 'torso'},
                      {'name': 'l-upper-leg', 'parent': 'hip'}]
        hip = np.array(self.landmarks['hip']) * [self.W, self.H]
        neck = np.array(self.landmarks['neck']) * [self.W, self.H]
        world = np.array([(hip[0] - self.origin[0]) * self.scale, (self.H - hip[1]) * self.scale])
        angle = math.degrees(math.atan2(hip[1] - neck[1], neck[0] - hip[0]))
        self.absposes = {'hip': (world, -7.), 'torso': (world, angle)}

    def mesh(self, part=None, landmarks=None):
        return costume_mesh(self.part if part is None else part, self.bones, self.absposes,
                             self.scale, self.origin, self.H,
                             self.landmarks if landmarks is None else landmarks, self.W)

    def placed_points(self, mesh, part):
        x0, y0, x1, y1 = part['box']
        return [np.array([x0 + u * (x1 - x0), y0 + v * (y1 - y0)])
                for u, v in zip(mesh['uvs'][::2], mesh['uvs'][1::2])]

    def assert_setup_positions(self, mesh, part):
        for point, vertex in zip(self.placed_points(mesh, part), influences(mesh)):
            expected = np.array([(point[0] - self.origin[0]) * self.scale,
                                 (self.H - point[1]) * self.scale])
            combined = np.zeros(2)
            for index, x, y, weight in vertex:
                name = self.bones[index]['name']
                position, angle = self.absposes[name]
                radians = math.radians(angle)
                rotation = np.array([[math.cos(radians), -math.sin(radians)],
                                     [math.sin(radians), math.cos(radians)]])
                posed = position + rotation @ [x, y]
                np.testing.assert_allclose(posed, expected, atol=1e-9)
                combined += weight * posed
            np.testing.assert_allclose(combined, expected, atol=1e-9)

    def test_weights_are_finite_normalized_and_only_bind_hip_and_torso(self):
        mesh = self.mesh()
        saw_blend = False
        for vertex in influences(mesh):
            self.assertTrue(all(isinstance(value[0], int) for value in vertex))
            self.assertTrue(all(math.isfinite(number) for value in vertex for number in value))
            self.assertTrue({self.bones[value[0]]['name'] for value in vertex} <= {'hip', 'torso'})
            self.assertTrue(all(0 < value[3] <= 1 for value in vertex))
            self.assertAlmostEqual(sum(value[3] for value in vertex), 1, places=12)
            saw_blend |= len(vertex) == 2
        self.assertTrue(saw_blend)
        self.assert_setup_positions(mesh, self.part)

    def test_above_neck_follows_torso_and_hanging_hem_follows_hip(self):
        mesh = self.mesh()
        hip = np.array(self.landmarks['hip']) * [self.W, self.H]
        axis = np.array(self.landmarks['neck']) * [self.W, self.H] - hip
        seen_upper = seen_lower = 0
        for point, vertex in zip(self.placed_points(mesh, self.part), influences(mesh)):
            position = float((point - hip) @ axis / (axis @ axis))
            if position >= 1:
                self.assertEqual([self.bones[value[0]]['name'] for value in vertex], ['torso'])
                self.assertEqual(vertex[0][3], 1)
                seen_upper += 1
            elif position <= 0:
                self.assertEqual([self.bones[value[0]]['name'] for value in vertex], ['hip'])
                self.assertEqual(vertex[0][3], 1)
                seen_lower += 1
        self.assertGreater(seen_upper, 0)
        self.assertGreater(seen_lower, 0)

    def test_waist_is_one_connected_surface_with_a_compatible_hull(self):
        mesh = self.mesh()
        count = len(mesh['uvs']) // 2
        neighbors = {i: set() for i in range(count)}
        torso_weights = [sum(value[3] for value in vertex if self.bones[value[0]]['name'] == 'torso')
                         for vertex in influences(mesh)]
        crossed_waist = False
        for offset in range(0, len(mesh['triangles']), 3):
            triangle = mesh['triangles'][offset:offset + 3]
            for index in triangle:
                neighbors[index].update(triangle)
            weights = [torso_weights[index] for index in triangle]
            crossed_waist |= min(weights) < .5 < max(weights)
        reached = {0}
        pending = [0]
        while pending:
            for neighbor in neighbors[pending.pop()] - reached:
                reached.add(neighbor)
                pending.append(neighbor)
        self.assertEqual(len(reached), count)
        self.assertTrue(crossed_waist)
        self.assertTrue(0 < mesh['hull'] < count)
        for index in range(mesh['hull']):
            u, v = mesh['uvs'][index * 2:index * 2 + 2]
            self.assertTrue(u in (0, 1) or v in (0, 1))
        skeleton = {'skeleton': {'spine': '4.2.00'}, 'bones': self.bones,
                    'slots': [{'name': 'torso', 'bone': 'torso', 'attachment': 'torso'}],
                    'skins': [{'name': 'default', 'attachments': {'torso': {'torso': mesh}}}],
                    'animations': {}}
        report = validate_state({'skeleton': skeleton, 'parts': [{'name': 'torso'}]})
        self.assertTrue(report['passed'], report['errors'])

    def test_adjusted_costume_uses_actual_setup_coordinates_and_never_atlas_sampling(self):
        pivot = np.array(self.landmarks['hip']) * [self.W, self.H]
        adjusted = apply_adjustment(self.part, {'dx': 33, 'dy': 45, 'rotation': 18, 'scale': 1.1}, pivot)
        mesh = self.mesh(adjusted)
        self.assert_setup_positions(mesh, adjusted)
        # Dropping the inverse map must change neither the actual vertex
        # positions nor weights; the original atlas cells are irrelevant.
        without_inverse = {key: value for key, value in adjusted.items() if key != 'review_transform'}
        self.assertEqual(mesh, self.mesh(without_inverse))
        vertices = influences(mesh)
        points = self.placed_points(mesh, adjusted)
        hip = np.array(self.landmarks['hip']) * [self.W, self.H]
        below_hip = [vertex for point, vertex in zip(points, vertices) if point[1] > hip[1] + 40]
        self.assertTrue(below_hip)
        self.assertTrue(all([self.bones[value[0]]['name'] for value in vertex] == ['hip']
                            for vertex in below_hip))

    def test_degenerate_hip_neck_axis_fails_instead_of_emitting_invalid_weights(self):
        with self.assertRaises(UserError):
            self.mesh(landmarks={'hip': [.5, .5], 'neck': [.5, .5]})


if __name__ == '__main__':
    unittest.main()
