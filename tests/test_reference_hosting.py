import base64
import datetime
import hashlib
import hmac
import io
import json
import re
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app.character import CharacterProject
from app.agent_tasks import AgentTasks
from app.core import UserError, atomic_json, png_bytes, sha
from app.jobs import JobHistory
from app.provider import Matrix
from app.reference_hosting import ReferenceHosting, signed_get
from agent.spine_mcp import Bridge


CONFIG = {
    'provider': 's3',
    'endpoint': 'https://objects.example/v1%20assets/雪',
    'region': 'eu-west-1',
    'bucket': 'spine-tests',
    'access_key_id': 'EXAMPLE_TEST_ACCESS_KEY',
    'secret_access_key': 'example-test-secret-key',
    'session_token': 'EXAMPLE_TEST_SESSION+/=',
    'prefix': 'reference-assets/characters',
    'url_ttl': 86400,
}


def fixture_image():
    image = Image.new('RGBA', (160, 180), (20, 130, 200, 255))
    image.putpixel((70, 90), (230, 140, 0, 0))
    return image


def signature(secret, scope, string_to_sign):
    """Independent AWS SigV4 computation from the actual wire request."""
    date, region, service, terminator = scope.split('/')
    key = ('AWS4' + secret).encode()
    for component in (date, region, service, terminator):
        key = hmac.new(key, component.encode(), hashlib.sha256).digest()
    return hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()


def encode_query(items):
    encoded = [(urllib.parse.quote(key, safe='-_.~'),
                urllib.parse.quote(value, safe='-_.~')) for key, value in items]
    return '&'.join(key + '=' + value for key, value in sorted(encoded))


class UploadResponse(io.BytesIO):
    status = 200


class ReferenceHostingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hosting = ReferenceHosting(self.root)
        self.blob = png_bytes(fixture_image())
        self.image_id = sha(self.blob)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_no_credentials(self, value):
        text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        for field in ('access_key_id', 'secret_access_key', 'session_token'):
            self.assertNotIn(CONFIG[field], text)
        self.assertNotIn('AWS4-HMAC-SHA256 Credential=', text)

    def test_unconfigured_public_status_and_error_require_platform_admin_only(self):
        status = self.hosting.status()
        self.assertFalse(status['configured'])
        self.assertFalse(status['ready'])
        self.assert_no_credentials(status)
        with patch('urllib.request.build_opener') as network:
            with self.assertRaisesRegex(UserError, '管理员') as error:
                self.hosting.publish(self.blob, self.image_id)
        network.assert_not_called()
        self.assert_no_credentials(str(error.exception))

    def test_save_is_private_and_status_never_exposes_credentials(self):
        status = self.hosting.save(dict(CONFIG))
        target = self.root / 'reference-hosting.json'
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        saved = json.loads(target.read_text())
        self.assertEqual(saved['secret_access_key'], CONFIG['secret_access_key'])
        self.assertEqual(saved['session_token'], CONFIG['session_token'])
        self.assertTrue(status['configured'])
        self.assert_no_credentials(status)
        self.assert_no_credentials(ReferenceHosting(self.root).status())
        self.assertFalse(target.with_suffix('.tmp').exists())
        self.hosting.save({**CONFIG, 'secret_access_key': 'replacement-test-secret'})
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertFalse(target.with_suffix('.tmp').exists())

    def test_invalid_settings_preserve_previous_private_configuration(self):
        self.hosting.save(dict(CONFIG))
        target = self.root / 'reference-hosting.json'
        before = target.read_bytes()
        cases = [
            {'endpoint': 'http://objects.example'},
            {'endpoint': 'https://localhost'},
            {'endpoint': 'https://127.0.0.1'},
            {'endpoint': 'https://172.16.7.76'},
            {'endpoint': 'https://169.254.169.254'},
            {'endpoint': 'https://[::1]'},
            {'endpoint': 'https://name:password@objects.example'},
            {'endpoint': 'https://objects.example?secret=example-test-secret-key'},
            {'endpoint': 'https://objects.example/#fragment'},
            {'provider': 'unsupported'},
            {'url_ttl': 3599}, {'url_ttl': 604801}, {'url_ttl': True},
            {'prefix': '../images'}, {'prefix': 'images//characters'},
            {'bucket': '../bucket'}, {'secret_access_key': ''},
        ]
        with patch('urllib.request.build_opener') as network:
            for changed in cases:
                with self.subTest(changed=changed):
                    with self.assertRaises(UserError) as error:
                        self.hosting.save({**CONFIG, **changed})
                    self.assertEqual(target.read_bytes(), before)
                    self.assert_no_credentials(str(error.exception))
        network.assert_not_called()

    def test_null_or_non_string_storage_credentials_are_not_silently_coerced(self):
        self.hosting.save(dict(CONFIG), verified=True)
        before = self.hosting.path.read_bytes()
        with patch('urllib.request.build_opener') as network:
            for field in ('access_key_id', 'secret_access_key'):
                for value in (None, True, 12345, [], {}):
                    with self.subTest(field=field, value=value):
                        with self.assertRaises(UserError):
                            self.hosting.save({**CONFIG, field: value})
                        self.assertEqual(self.hosting.path.read_bytes(), before)
        network.assert_not_called()

    def test_invalid_verification_timestamp_never_reports_storage_ready(self):
        self.hosting.save(dict(CONFIG))
        saved = json.loads(self.hosting.path.read_text())
        for stamp in ('not-a-timestamp', '9999999999', float('nan'), float('inf'),
                      float('-inf'), True, False, 0, -10, None, time.time() + 600):
            with self.subTest(timestamp=stamp):
                self.hosting.path.write_text(json.dumps({**saved, 'verified_at': stamp}))
                status = self.hosting.status()
                self.assertTrue(status['configured'])
                self.assertFalse(status['ready'])
                self.assert_no_credentials(status)

    def test_status_uses_one_coherent_configuration_read(self):
        self.hosting.save(dict(CONFIG), verified=True)
        verified = self.hosting.path.read_text()
        # A second read could observe a concurrently replaced/deleted file.
        with patch.object(Path, 'read_text', side_effect=[verified, OSError('configuration changed')]) as read:
            status = self.hosting.status()
        self.assertTrue(status['configured'])
        self.assertTrue(status['ready'])
        self.assertEqual(read.call_count, 1)

    def test_aws_documented_20130524_presigned_get_signature_vector(self):
        # Public AWS sample credentials; this vector performs no network request.
        config = {'region': 'us-east-1', 'url_ttl': 86400,
                  'access_key_id': 'AKIAIOSFODNN7EXAMPLE',
                  'secret_access_key': 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'}
        stamp = datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc).timestamp()
        url = signed_get(config, 'https://examplebucket.s3.amazonaws.com/test.txt', stamp)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query['X-Amz-Date'], ['20130524T000000Z'])
        self.assertEqual(query['X-Amz-Signature'],
                         ['aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404'])

    def test_upload_signs_put_and_expiring_get_with_canonical_url_encoding(self):
        self.hosting.save(dict(CONFIG))
        opener = Mock()
        opener.open.return_value = UploadResponse()
        before = time.time()
        with patch('urllib.request.build_opener', return_value=opener) as factory:
            result = self.hosting.publish(self.blob, self.image_id)
        after = time.time()

        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'PUT')
        self.assertEqual(request.data, self.blob)
        expected_path = '/v1%20assets/%E9%9B%AA/spine-tests/reference-assets/characters/' + self.image_id + '.png'
        self.assertEqual(urllib.parse.urlsplit(request.full_url).path, expected_path)
        self.assertNotIn('%2520', request.full_url)
        self.assertNotIn('%2F', expected_path)
        handlers = factory.call_args.args
        self.assertTrue(any(isinstance(handler, type) and
                            issubclass(handler, urllib.request.HTTPRedirectHandler) or
                            isinstance(handler, urllib.request.HTTPRedirectHandler)
                            for handler in handlers))

        headers = {key.lower(): value for key, value in request.header_items()}
        auth = headers['authorization']
        match = re.fullmatch(r'AWS4-HMAC-SHA256 Credential=([^,]+), SignedHeaders=([^,]+), Signature=([a-f0-9]{64})', auth)
        self.assertIsNotNone(match)
        credential, names, actual_signature = match.groups()
        access, scope = credential.split('/', 1)
        self.assertEqual(access, CONFIG['access_key_id'])
        self.assertEqual(headers['x-amz-content-sha256'], hashlib.sha256(self.blob).hexdigest())
        canonical_headers = ''.join(name + ':' + ' '.join(headers[name].split()) + '\n'
                                    for name in names.split(';'))
        canonical_request = '\n'.join(('PUT', expected_path, '', canonical_headers, names,
                                       hashlib.sha256(self.blob).hexdigest()))
        string_to_sign = '\n'.join(('AWS4-HMAC-SHA256', headers['x-amz-date'], scope,
                                   hashlib.sha256(canonical_request.encode()).hexdigest()))
        self.assertEqual(actual_signature, signature(CONFIG['secret_access_key'], scope, string_to_sign))

        parsed = urllib.parse.urlsplit(result['url'])
        query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = dict(query_items)
        self.assertEqual(parsed.scheme, 'https')
        self.assertEqual(parsed.path, expected_path)
        self.assertEqual(query['X-Amz-Expires'], '86400')
        self.assertEqual(query['X-Amz-Security-Token'], CONFIG['session_token'])
        self.assertNotIn('+', parsed.query)
        self.assertIn('%2B%2F%3D', parsed.query)
        self.assertNotIn(CONFIG['secret_access_key'], result['url'])
        get_scope = query['X-Amz-Credential'].split('/', 1)[1]
        canonical_get = '\n'.join(('GET', expected_path,
                                   encode_query([(key, value) for key, value in query_items if key != 'X-Amz-Signature']),
                                   'host:' + parsed.netloc + '\n', 'host', 'UNSIGNED-PAYLOAD'))
        get_string = '\n'.join(('AWS4-HMAC-SHA256', query['X-Amz-Date'], get_scope,
                               hashlib.sha256(canonical_get.encode()).hexdigest()))
        self.assertEqual(query['X-Amz-Signature'], signature(CONFIG['secret_access_key'], get_scope, get_string))
        self.assertLessEqual(before + 86400, result['expires_at'])
        self.assertLessEqual(result['expires_at'], after + 86400)
        self.assertEqual(result['provider'], 's3')

    def test_virtual_addressing_places_bucket_in_host_and_retains_key_path(self):
        self.hosting.save({**CONFIG, 'endpoint': 'https://objects.example', 'addressing_style': 'virtual'})
        opener = Mock()
        opener.open.return_value = UploadResponse()
        with patch('urllib.request.build_opener', return_value=opener):
            result = self.hosting.publish(self.blob, self.image_id)
        uploaded = urllib.parse.urlsplit(opener.open.call_args.args[0].full_url)
        self.assertEqual(uploaded.netloc, 'spine-tests.objects.example')
        self.assertEqual(uploaded.path, '/reference-assets/characters/' + self.image_id + '.png')
        self.assertEqual(urllib.parse.urlsplit(result['url']).netloc, uploaded.netloc)

    def test_invalid_image_hash_never_uploads(self):
        self.hosting.save(dict(CONFIG))
        with patch('urllib.request.build_opener') as network:
            for image_id in ('0' * 64, '../object', self.image_id.upper()):
                with self.subTest(image_id=image_id):
                    with self.assertRaises(UserError):
                        self.hosting.publish(self.blob, image_id)
        network.assert_not_called()

    def test_upstream_failures_do_not_echo_secrets_and_redirects_are_refused(self):
        self.hosting.save(dict(CONFIG))
        dangerous = 'AWS4-HMAC-SHA256 Credential=' + CONFIG['access_key_id'] + ' ' + CONFIG['secret_access_key']
        failures = [urllib.error.HTTPError('https://objects.example/' + dangerous, 307, dangerous, {}, io.BytesIO(dangerous.encode())),
                    urllib.error.HTTPError('https://objects.example', 403, dangerous, {}, io.BytesIO(dangerous.encode())),
                    urllib.error.URLError(dangerous), TimeoutError(dangerous)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                opener = Mock()
                opener.open.side_effect = failure
                with patch('urllib.request.build_opener', return_value=opener) as factory:
                    with self.assertRaises(UserError) as error:
                        self.hosting.publish(self.blob, self.image_id)
                self.assert_no_credentials(str(error.exception))
                self.assertEqual(opener.open.call_count, 1)
                handler = factory.call_args.args[0]
                if isinstance(handler, type):
                    handler = handler()
                self.assertIsNone(handler.redirect_request(None, None, 307, dangerous, {}, 'https://other.example'))


class AutomatedReferenceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = fixture_image()
        self.matrix = Matrix(self.root)
        self.matrix.hosting.status = Mock(return_value={'provider':'s3','ready':True})
        self.matrix.request = Mock(side_effect=AssertionError('Must never generate during reference upload'))
        self.matrix.download_result = Mock(return_value=png_bytes(self.image))

    def tearDown(self):
        self.tmp.cleanup()

    def install_publisher(self):
        # The public hoster API is patched; no object storage is contacted.
        return patch.object(ReferenceHosting, 'publish', return_value={
            'url': 'https://objects.example/image.png?verified=1',
            'expires_at': time.time() + 86400, 'provider': 's3'})

    def test_first_image_auto_uploads_verified_pixels_then_reuses_on_restart(self):
        with self.install_publisher() as publisher:
            result = self.matrix.ensure_reference(self.image)
            second = Matrix(self.root)
            second.request = Mock(side_effect=AssertionError('Must never generate'))
            second.download_result = Mock(side_effect=AssertionError('A cache hit is local'))
            cached = second.ensure_reference(self.image.copy())
        self.assertEqual(result['url'] if isinstance(result, dict) else result,
                         cached['url'] if isinstance(cached, dict) else cached)
        publisher.assert_called_once_with(png_bytes(self.image), sha(png_bytes(self.image)))
        self.matrix.download_result.assert_called_once()
        second.download_result.assert_not_called()
        self.matrix.request.assert_not_called()
        second.request.assert_not_called()
        record = self.matrix.cached_reference(self.image)
        self.assertEqual(record['provider'], 's3')
        self.assertGreater(record['expires_at'], time.time() + 3600)

    def test_changed_pixels_require_a_separate_upload(self):
        changed = self.image.copy()
        changed.putpixel((80, 90), (1, 2, 3, 255))
        with self.install_publisher() as publisher:
            self.matrix.ensure_reference(self.image)
            self.matrix.download_result.return_value = png_bytes(changed)
            self.matrix.ensure_reference(changed)
        self.assertEqual(publisher.call_count, 2)
        self.assertNotEqual(publisher.call_args_list[0].args[1], publisher.call_args_list[1].args[1])
        self.assertIsNotNone(self.matrix.cached_reference(self.image))
        self.assertIsNotNone(self.matrix.cached_reference(changed))
        self.matrix.request.assert_not_called()

    def test_non_object_cache_records_are_ignored_without_network_or_crash(self):
        image_id = sha(png_bytes(self.image))
        path = self.matrix.references / (image_id + '.json')
        path.parent.mkdir(parents=True)
        for record in (None, [], 42, 'not-an-object', True):
            with self.subTest(record=record):
                path.write_text(json.dumps(record))
                self.assertIsNone(self.matrix.cached_reference(self.image))
        self.matrix.download_result.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_invalid_signed_url_expiry_is_not_reused(self):
        image_id = sha(png_bytes(self.image))
        path = self.matrix.references / (image_id + '.json')
        path.parent.mkdir(parents=True)
        record = {'image_id': image_id, 'url': 'https://objects.example/signed.png',
                  'verified_at': time.time(), 'provider': 's3'}
        for expiry in (float('nan'), float('inf'), float('-inf'), True, False,
                       '9999999999', None, [], {}):
            with self.subTest(expiry=expiry):
                path.write_text(json.dumps({**record, 'expires_at': expiry}))
                self.assertIsNone(self.matrix.cached_reference(self.image))
        # Legacy manually registered URLs really lacking an expiry stay usable.
        path.write_text(json.dumps(record))
        self.assertEqual(self.matrix.cached_reference(self.image)['url'], record['url'])
        self.matrix.download_result.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_admin_configuration_is_marked_ready_only_after_exact_round_trip(self):
        uploaded = []

        def publish(data, image_id, config=None):
            self.assertEqual(sha(data), image_id)
            self.assertEqual(config['secret_access_key'], CONFIG['secret_access_key'])
            self.assertFalse(self.matrix.hosting.path.exists())
            uploaded.append(data)
            return {'url': 'https://objects.example/probe.png',
                    'expires_at': time.time() + 86400, 'provider': 's3'}

        self.matrix.download_result.side_effect = lambda *args, **kwargs: uploaded[-1]
        with patch.object(ReferenceHosting, 'publish', side_effect=publish) as publisher:
            result = self.matrix.configure_reference_hosting(dict(CONFIG))
        self.assertTrue(result['ready'])
        publisher.assert_called_once()
        saved = json.loads(self.matrix.hosting.path.read_text())
        self.assertGreater(saved['verified_at'], 0)
        self.assertEqual(saved['secret_access_key'], CONFIG['secret_access_key'])
        self.assertNotIn(CONFIG['secret_access_key'], json.dumps(result))
        self.assertEqual(stat.S_IMODE(self.matrix.hosting.path.stat().st_mode), 0o600)
        self.matrix.request.assert_not_called()

    def test_admin_failed_round_trip_does_not_replace_verified_configuration(self):
        self.matrix.hosting.save({**CONFIG, 'secret_access_key': 'old-verified-test-secret'}, verified=True)
        before = self.matrix.hosting.path.read_bytes()
        with self.install_publisher() as publisher:
            with self.assertRaisesRegex(UserError, '回读校验失败'):
                self.matrix.configure_reference_hosting(dict(CONFIG))
        publisher.assert_called_once()
        self.assertEqual(self.matrix.hosting.path.read_bytes(), before)
        self.assertTrue(self.matrix.hosting.status()['ready'])
        self.matrix.request.assert_not_called()

    def test_expiring_signed_url_is_not_reused_and_is_replaced_only_after_verification(self):
        image_id = sha(png_bytes(self.image))
        path = self.matrix.references / (image_id + '.json')
        old = {'image_id': image_id, 'url': 'https://objects.example/old.png',
               'verified_at': time.time() - 86400, 'expires_at': time.time() + 3599, 'provider': 's3'}
        atomic_json(path, old)
        self.assertIsNone(self.matrix.cached_reference(self.image))
        with self.install_publisher() as publisher:
            self.matrix.ensure_reference(self.image)
        publisher.assert_called_once()
        record = json.loads(path.read_text())
        self.assertNotEqual(record['url'], old['url'])
        self.assertGreater(record['expires_at'], time.time() + 3600)
        self.matrix.request.assert_not_called()

    def test_wrong_remote_pixels_keep_old_expired_record_and_never_generate(self):
        image_id = sha(png_bytes(self.image))
        path = self.matrix.references / (image_id + '.json')
        old = {'image_id': image_id, 'url': 'https://objects.example/old.png',
               'verified_at': time.time() - 86400, 'expires_at': time.time() - 100, 'provider': 's3'}
        atomic_json(path, old)
        before = path.read_bytes()
        wrong = self.image.copy()
        wrong.putpixel((0, 0), (1, 2, 3, 255))
        self.matrix.download_result.return_value = png_bytes(wrong)
        with self.install_publisher() as publisher:
            with self.assertRaisesRegex(UserError, '像素'):
                self.matrix.ensure_reference(self.image)
        publisher.assert_called_once()
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(self.matrix.cached_reference(self.image))
        self.matrix.request.assert_not_called()

    def test_concurrent_same_image_publishes_once(self):
        barrier = threading.Barrier(6)

        def ensure(_):
            barrier.wait(timeout=5)
            return self.matrix.ensure_reference(self.image.copy())

        with self.install_publisher() as publisher:
            with ThreadPoolExecutor(max_workers=6) as executor:
                results = list(executor.map(ensure, range(6)))
        self.assertEqual(len(results), 6)
        self.assertEqual(publisher.call_count, 1)
        self.assertEqual(self.matrix.download_result.call_count, 1)
        self.matrix.request.assert_not_called()


class AutomaticReferenceEndpointTests(unittest.TestCase):
    def setUp(self):
        from app import server
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.character = CharacterProject.create(self.root, 'Automated reference fixture')
        self.character.set_image(png_bytes(fixture_image()), 0)
        self.matrix = Matrix(self.root)
        self.matrix.download_result = Mock(return_value=png_bytes(self.character.image()))
        self.matrix.hosting.status = Mock(return_value={'provider':'s3','ready':True})
        self.matrix.request = Mock(side_effect=AssertionError('Reference routes must never generate'))
        self.handler = object.__new__(server.Handler)
        self.handler.path = '/api/characters/' + self.character.id + '/reference'
        self.handler.send = lambda value, *args, **kwargs: value
        self.published = {'url': 'https://objects.example/verified.png',
                          'expires_at': time.time() + 86400, 'provider': 's3'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_only_reports_status_and_does_not_publish(self):
        with patch.object(self.server, 'get_character', return_value=self.character), \
                patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(ReferenceHosting, 'publish', return_value=self.published) as publisher:
            result = self.handler.get()
        self.assertFalse(result['cached'])
        self.assertIsNone(result['reference_url'])
        self.assertTrue(result['upload_ready'])
        self.assertEqual(result['upload_service'], 's3')
        self.assertFalse(self.matrix.references.exists())
        publisher.assert_not_called()
        self.matrix.download_result.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_ensure_publishes_current_original_once_without_editing_revision(self):
        revision = self.character.meta['revision']
        with patch.object(self.server, 'get_character', return_value=self.character), \
                patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(ReferenceHosting, 'publish', return_value=self.published) as publisher:
            result = self.handler.post({'action': 'ensure', 'revision': revision})
            again = self.handler.post({'action': 'ensure', 'revision': revision})
        self.assertTrue(result['cached'])
        self.assertEqual(result['reference_url'], self.published['url'])
        self.assertEqual(again['reference_url'], result['reference_url'])
        self.assertEqual(self.character.meta['revision'], revision)
        self.assertEqual(result['revision'], revision)
        publisher.assert_called_once_with(png_bytes(self.character.image()),
                                          self.character.meta['image_id'])
        self.matrix.download_result.assert_called_once()
        self.matrix.request.assert_not_called()

    def test_stale_revision_never_publishes_new_reference(self):
        with patch.object(self.server, 'get_character', return_value=self.character), \
                patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(ReferenceHosting, 'publish', return_value=self.published) as publisher:
            with self.assertRaisesRegex(UserError, '已更新'):
                self.handler.post({'action': 'ensure', 'revision': self.character.meta['revision'] - 1})
        publisher.assert_not_called()
        self.matrix.download_result.assert_not_called()
        self.matrix.request.assert_not_called()

    def generation_request(self, action):
        request = {'revision': self.character.meta['revision'], 'nonce': uuid.uuid4().hex}
        if action == 'generate-view':
            request.update(target_view='朝右的轻微三分之四视角', target_actions=['idle', 'walk'])
        else:
            request.update(sheets=['arms'], instructions='完整双臂和手掌')
        return request

    def test_generations_without_explicit_url_upload_and_use_only_verified_current_original(self):
        history = JobHistory(self.root, self.matrix, 'characters')
        for action in ('generate-view', 'generate-parts'):
            with self.subTest(action=action):
                # Each changed original needs a new independently verified upload.
                changed = self.character.image().copy()
                changed.putpixel((80, 80), (10 if action == 'generate-view' else 20, 30, 40, 255))
                self.character.set_image(png_bytes(changed), self.character.meta['revision'])
                self.matrix.download_result.return_value = png_bytes(changed)
                self.handler.path = '/api/characters/' + self.character.id + '/' + action
                calls = []

                def generated(*args):
                    cached = self.matrix.cached_reference(changed)
                    self.assertIsNotNone(cached, 'Publish must be pixel-verified before Flare is invoked')
                    self.assertEqual(args[1], cached['url'])
                    self.assertEqual(cached['image_id'], self.character.meta['image_id'])
                    calls.append(args)
                    return 'mock-generation-' + action

                method = 'generate_view' if action == 'generate-view' else 'generate_part_sheet'
                with patch.object(self.server, 'get_character', return_value=self.character), \
                        patch.object(self.server, 'MATRIX', self.matrix), \
                        patch.object(self.server, 'CHARACTER_JOBS', history), \
                        patch.object(ReferenceHosting, 'publish', return_value=self.published) as publisher, \
                        patch.object(self.matrix, method, side_effect=generated) as generate:
                    request = self.generation_request(action)
                    self.assertNotIn('reference_url', request)
                    result = self.handler.post(request)
                publisher.assert_called_once_with(png_bytes(changed), self.character.meta['image_id'])
                generate.assert_called_once()
                self.assertEqual(len(calls), 1)
                job = result if action == 'generate-view' else result[0]
                self.assertEqual(job['model'], 'gpt-image-2.5-flare')
                self.assertEqual(job['source_image_id'], self.character.meta['image_id'])
                self.assertEqual(job['status'], 'pending')
        self.matrix.request.assert_not_called()

    def test_repeated_generation_nonce_skips_both_publication_and_generation(self):
        history = JobHistory(self.root, self.matrix, 'characters')
        for action in ('generate-view', 'generate-parts'):
            with self.subTest(action=action):
                self.handler.path = '/api/characters/' + self.character.id + '/' + action
                request = self.generation_request(action)
                method = 'generate_view' if action == 'generate-view' else 'generate_part_sheet'
                with patch.object(self.server, 'get_character', return_value=self.character), \
                        patch.object(self.server, 'MATRIX', self.matrix), \
                        patch.object(self.server, 'CHARACTER_JOBS', history), \
                        patch.object(ReferenceHosting, 'publish', return_value=self.published), \
                        patch.object(self.matrix, method, return_value='existing-mock-job'):
                    first = self.handler.post(request)
                with patch.object(self.server, 'get_character', return_value=self.character), \
                        patch.object(self.server, 'MATRIX', self.matrix), \
                        patch.object(self.server, 'CHARACTER_JOBS', history), \
                        patch.object(self.matrix, 'ensure_reference', side_effect=AssertionError('Replay must not upload')) as ensure, \
                        patch.object(self.matrix, method, side_effect=AssertionError('Replay must not generate')) as generate:
                    replayed = self.handler.post(request)
                self.assertEqual(first, replayed)
                ensure.assert_not_called()
                generate.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_remote_client_cannot_save_platform_storage_configuration(self):
        self.handler.path = '/api/reference-hosting/connect'
        self.handler.send = lambda value, status=200, **kwargs: {'body': value, 'status': status}
        with patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(self.matrix, 'configure_reference_hosting') as configure, \
                patch.object(self.matrix.hosting, 'save') as save:
            for client in ('172.16.7.76', '203.0.113.2', '::ffff:172.16.7.76'):
                with self.subTest(client=client):
                    self.handler.client_address = (client, 12345)
                    result = self.handler.post(dict(CONFIG))
                    self.assertEqual(result['status'], 403)
                    self.assertIn('服务器本机', result['body']['error'])
        configure.assert_not_called()
        save.assert_not_called()
        self.assertFalse(self.matrix.hosting.path.exists())
        self.matrix.request.assert_not_called()


class AutomaticReferenceMCPTests(unittest.TestCase):
    def test_bridge_ensure_requires_revision_and_forwards_explicit_ensure_action(self):
        bridge = Bridge('http://172.16.7.76:8780')
        bridge.api = Mock(return_value={'cached': True, 'reference_url': 'https://objects.example/image.png'})
        args = {'action': 'ensure', 'kind': 'character', 'id': 'a' * 32}
        for revision in (None, '1', True):
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(ValueError, 'revision'):
                    bridge.call('spine_reference', {**args, 'revision': revision})
        bridge.api.assert_not_called()
        result = bridge.call('spine_reference', {**args, 'revision': 7})
        self.assertTrue(result['cached'])
        bridge.api.assert_called_once_with('/api/characters/' + args['id'] + '/reference',
                                          {'action': 'ensure', 'revision': 7})
        self.assertNotIn('image_path', result)
        self.assertNotIn('image_data', result)

    def test_worker_ensure_checks_ownership_without_reserving_generation_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary)
            loaders = {'character': lambda entity: CharacterProject(storage / 'characters' / entity)}
            tasks = AgentTasks(storage, Path(__file__).resolve().parents[1], loaders, None)
            task = tasks.create({'nonce': uuid.uuid4().hex, 'requirements': '制作待机和行走动画',
                                 'image': base64.b64encode(png_bytes(fixture_image())).decode(),
                                 'max_generations': 0})
            entity = task['sources'][0]['id']
            revision = loaders['character'](entity).meta['revision']
            tasks.api = Mock(return_value={'cached': True, 'reference_url': 'https://objects.example/image.png'})
            tasks.reserve = Mock(side_effect=AssertionError('Uploading reference is not image generation'))
            args = {'action': 'ensure', 'kind': 'character', 'id': entity, 'revision': revision}
            result = tasks.tool(task['id'], 'spine_reference', args)
            self.assertTrue(result['cached'])
            tasks.api.assert_called_once_with('/api/characters/' + entity + '/reference',
                                               {'action': 'ensure', 'revision': revision})
            self.assertEqual(tasks.summary(task['id'])['generation_budget']['used_or_reserved'], 0)
            self.assertEqual(tasks.read(task['id'])['reservations'], {})
            tasks.reserve.assert_not_called()
            tasks.api.reset_mock()
            with self.assertRaisesRegex(UserError, '独立副本'):
                tasks.tool(task['id'], 'spine_reference', {**args, 'id': 'a' * 32})
            tasks.api.assert_not_called()
            tasks.reserve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
