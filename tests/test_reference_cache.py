import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, PngImagePlugin

from app.character import CharacterProject
from app.core import UserError, png_bytes, sha
from app.provider import Matrix
from agent.spine_mcp import Bridge


REFERENCE_URL = 'https://assets.example/original.png'


def fixture_image():
    image = Image.new('RGBA', (160, 180), (20, 130, 200, 255))
    image.putpixel((1, 2), (70, 10, 80, 0))
    image.putpixel((90, 130), (250, 170, 20, 255))
    return image


class ReferenceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = fixture_image()
        self.matrix = Matrix(self.root)
        self.matrix.request = Mock(side_effect=AssertionError('Must not submit or poll generation'))
        self.matrix.download_result = Mock(return_value=png_bytes(self.image))

    def tearDown(self):
        self.tmp.cleanup()

    def test_registration_checks_decoded_pixels_instead_of_png_encoding(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('description', 'Different PNG encoding, identical RGBA pixels')
        buffer = io.BytesIO()
        self.image.save(buffer, format='PNG', pnginfo=metadata, compress_level=0)
        uploaded = buffer.getvalue()
        self.assertNotEqual(uploaded, png_bytes(self.image))
        self.matrix.download_result.return_value = uploaded

        result = self.matrix.register_reference(self.image, REFERENCE_URL)

        self.assertEqual(result['image_id'], sha(png_bytes(self.image)))
        self.assertEqual(result['url'], REFERENCE_URL)
        self.assertIsInstance(result['verified_at'], float)
        self.assertEqual(self.matrix.image_reference(self.image), REFERENCE_URL)
        self.matrix.download_result.assert_called_once_with(REFERENCE_URL)
        self.matrix.request.assert_not_called()

    def test_wrong_image_or_thumbnail_is_rejected_without_caching(self):
        wrong = self.image.copy()
        wrong.putpixel((90, 130), (251, 170, 20, 255))
        for uploaded in (png_bytes(wrong), png_bytes(self.image.resize((80, 90)))):
            with self.subTest(image_bytes=len(uploaded)):
                self.matrix.download_result.return_value = uploaded
                with self.assertRaisesRegex(UserError, '像素不一致'):
                    self.matrix.register_reference(self.image, REFERENCE_URL)
                self.assertIsNone(self.matrix.cached_reference(self.image))
        self.assertFalse(self.matrix.references.exists())
        self.matrix.request.assert_not_called()

    def test_failed_replacement_keeps_previously_verified_reference(self):
        self.matrix.register_reference(self.image, REFERENCE_URL)
        wrong = self.image.copy()
        wrong.putpixel((0, 0), (0, 0, 0, 255))
        self.matrix.download_result.return_value = png_bytes(wrong)
        with self.assertRaises(UserError):
            self.matrix.register_reference(self.image, 'https://assets.example/wrong.png')
        self.assertEqual(self.matrix.image_reference(self.image), REFERENCE_URL)
        self.matrix.request.assert_not_called()

    def test_restart_reuses_cache_but_changed_image_does_not(self):
        self.matrix.register_reference(self.image, REFERENCE_URL)
        restarted = Matrix(self.root)
        restarted.request = Mock(side_effect=AssertionError('Must not generate'))
        restarted.download_result = Mock(side_effect=AssertionError('Cache lookup must be local'))
        self.assertEqual(restarted.image_reference(self.image.copy()), REFERENCE_URL)
        changed = self.image.copy()
        changed.putpixel((0, 0), (0, 0, 0, 255))
        self.assertIsNone(restarted.cached_reference(changed))
        with self.assertRaisesRegex(UserError, 'spine_reference prepare'):
            restarted.image_reference(changed)
        restarted.request.assert_not_called()
        restarted.download_result.assert_not_called()

    def test_invalid_url_is_rejected_before_download(self):
        for url in ('http://assets.example/image.png', 'data:image/png;base64,AA==',
                    'https://127.0.0.1/image.png', 'https://172.16.7.76/image.png',
                    'https://user:password@assets.example/image.png'):
            with self.subTest(url=url):
                with self.assertRaises(UserError):
                    self.matrix.register_reference(self.image, url)
        self.matrix.download_result.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_invalid_persisted_reference_is_not_reused(self):
        result = self.matrix.register_reference(self.image, REFERENCE_URL)
        path = self.matrix.references / (result['image_id'] + '.json')
        for record in ({**result, 'url': 'https://localhost/wrong.png'},
                       {**result, 'image_id': '0' * 64}):
            path.write_text(json.dumps(record))
            self.assertIsNone(self.matrix.cached_reference(self.image))
        path.write_text('{broken')
        self.assertIsNone(self.matrix.cached_reference(self.image))
        self.matrix.request.assert_not_called()


class ReferenceEndpointTests(unittest.TestCase):
    def setUp(self):
        from app import server
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.character = CharacterProject.create(self.root, 'Reference fixture')
        self.character.set_image(png_bytes(fixture_image()), 0)
        self.matrix = Matrix(self.root)
        self.matrix.download_result = Mock(return_value=png_bytes(self.character.image()))
        self.matrix.request = Mock(side_effect=AssertionError('Reference operation must not generate'))
        self.handler = object.__new__(server.Handler)
        self.handler.path = '/api/characters/' + self.character.id + '/reference'
        self.handler.send = lambda value, *args, **kwargs: value

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_and_register_bind_current_original_without_changing_revision(self):
        revision = self.character.meta['revision']
        with patch.object(self.server, 'get_character', return_value=self.character), \
                patch.object(self.server, 'MATRIX', self.matrix):
            before = self.handler.get()
            self.assertFalse(before['cached'])
            self.assertIsNone(before['reference_url'])
            self.assertEqual(before['image_id'], self.character.meta['image_id'])
            self.assertEqual(before['image_url'], '/api/characters/' + self.character.id + '/image')
            registered = self.handler.post({'url': REFERENCE_URL, 'revision': revision})
            after = self.handler.get()
        self.assertTrue(registered['cached'])
        self.assertEqual(after['reference_url'], REFERENCE_URL)
        self.assertEqual(after['revision'], revision)
        self.assertEqual(self.character.meta['revision'], revision)
        self.matrix.download_result.assert_called_once_with(REFERENCE_URL)
        self.matrix.request.assert_not_called()

    def test_stale_revision_stops_registration_before_download(self):
        with patch.object(self.server, 'get_character', return_value=self.character), \
                patch.object(self.server, 'MATRIX', self.matrix):
            with self.assertRaisesRegex(UserError, '已更新'):
                self.handler.post({'url': REFERENCE_URL, 'revision': self.character.meta['revision'] - 1})
        self.matrix.download_result.assert_not_called()
        self.matrix.request.assert_not_called()


class ReferenceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.blob = png_bytes(fixture_image())
        self.image_id = sha(self.blob)
        self.character_id = 'a' * 32
        self.result = {'id': self.character_id, 'revision': 7, 'image_id': self.image_id,
                       'cached': False, 'reference_url': None,
                       'image_url': '/api/characters/' + self.character_id + '/image'}
        self.bridge = Bridge('http://172.16.7.76:8780')
        self.bridge.api = Mock(side_effect=lambda *args: dict(self.result))
        self.args = {'action': 'prepare', 'kind': 'character', 'id': self.character_id}

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_downloads_verified_original_onto_caller_machine(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(self.blob)) as network, \
                patch('tempfile.gettempdir', return_value=str(self.root)):
            result = self.bridge.call('spine_reference', self.args)
        target = Path(result['image_path'])
        self.assertTrue(target.is_absolute())
        self.assertEqual(target.parent, (self.root / 'spine-matrix-references').resolve())
        self.assertEqual(target.read_bytes(), self.blob)
        self.assertEqual(target.stem, self.image_id)
        self.assertNotIn('image_data', result)
        self.assertEqual(result['revision'], 7)
        self.bridge.api.assert_called_once_with('/api/characters/' + self.character_id + '/reference')
        self.assertEqual(network.call_args.args[0].full_url,
                         self.bridge.base + self.result['image_url'])
        self.assertIsNone(network.call_args.args[0].get_header('Authorization'))

    def test_worker_prepare_decodes_original_and_omits_image_data(self):
        self.bridge.task = 'b' * 32
        self.result['image_data'] = base64.b64encode(self.blob).decode()
        with patch('urllib.request.urlopen', side_effect=AssertionError('Worker already returned original')), \
                patch('tempfile.gettempdir', return_value=str(self.root)):
            result = self.bridge.call('spine_reference', self.args)
        self.assertEqual(Path(result['image_path']).read_bytes(), self.blob)
        self.assertNotIn('image_data', result)
        self.assertNotIn(self.result['image_data'], json.dumps(result))
        self.bridge.api.assert_called_once_with(
            '/api/automation/agent/tasks/' + self.bridge.task + '/tools',
            {'name': 'spine_reference', 'arguments': self.args})

    def test_changed_original_fails_hash_check_without_writing_file(self):
        for worker in (False, True):
            with self.subTest(worker=worker):
                self.bridge.task = 'b' * 32 if worker else None
                if worker:
                    self.result['image_data'] = base64.b64encode(b'wrong-image').decode()
                with patch('urllib.request.urlopen', return_value=io.BytesIO(b'wrong-image')), \
                        patch('tempfile.gettempdir', return_value=str(self.root)):
                    with self.assertRaisesRegex(ValueError, '原图已变化'):
                        self.bridge.call('spine_reference', self.args)
                self.assertFalse((self.root / 'spine-matrix-references').exists())

    def test_cached_prepare_does_not_download_or_export_again(self):
        self.result.update(cached=True, reference_url=REFERENCE_URL)
        with patch('urllib.request.urlopen', side_effect=AssertionError('Cache should avoid network download')):
            result = self.bridge.call('spine_reference', self.args)
        self.assertEqual(result['reference_url'], REFERENCE_URL)
        self.assertNotIn('image_path', result)
        self.assertFalse((self.root / 'spine-matrix-references').exists())

    def test_register_transmits_revision_and_url_without_new_generation(self):
        args = {'action': 'register', 'kind': 'character', 'id': self.character_id,
                'url': REFERENCE_URL, 'revision': 7}
        with patch('urllib.request.urlopen', side_effect=AssertionError('Register uses API only')):
            self.bridge.call('spine_reference', args)
        self.bridge.api.assert_called_once_with('/api/characters/' + self.character_id + '/reference',
                                               {'url': REFERENCE_URL, 'revision': 7})
        self.assertEqual(args['revision'], 7)


if __name__ == '__main__':
    unittest.main()
