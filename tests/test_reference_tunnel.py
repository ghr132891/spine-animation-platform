"""Temporary reference publication regressions; no public tunnel or generation."""
import hashlib
import io
import json
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app.core import UserError, atomic_json, png_bytes, sha
from app.provider import Matrix
from app.reference_tunnel import ReferenceTunnel


REFERENCE = 'https://example-ephemeral.trycloudflare.com/r/' + 'a' * 64 + '.png'


def fixture_image():
    image = Image.new('RGBA', (160, 180), (30, 130, 200, 255))
    image.putpixel((80, 90), (240, 150, 30, 128))
    return image


class ReferenceTunnelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tunnel = ReferenceTunnel(self.root)
        self.tunnel._binary = Mock(return_value=Path('/mock/cloudflared'))
        self.data = png_bytes(fixture_image())
        self.image_id = sha(self.data)
        self.process = Mock()
        self.process.poll.return_value = None

    def tearDown(self):
        self.tunnel.close()
        self.temporary.cleanup()

    def fake_started(self):
        self.tunnel._process = self.process
        self.tunnel._hostname = 'example-ephemeral.trycloudflare.com'
        self.tunnel._connected = True

    def publish_locally(self):
        self.tunnel.enable()
        with patch.object(self.tunnel, '_ensure_started', side_effect=self.fake_started):
            return self.tunnel.publish(self.data, self.image_id)

    def test_enabling_and_reading_status_persist_only_mode_without_starting_process(self):
        with patch('subprocess.Popen') as start, patch('app.reference_tunnel.ThreadingHTTPServer') as server:
            initial = self.tunnel.status()
            enabled = self.tunnel.enable(7200)
            status = self.tunnel.status()
        self.assertFalse(initial['ready'])
        self.assertTrue(enabled['ready'])
        self.assertFalse(status['active'])
        self.assertEqual(status['url_ttl'], 7200)
        saved = json.loads(self.tunnel.path.read_text())
        self.assertEqual(saved, {'provider': 'quick-tunnel', 'enabled': True, 'url_ttl': 7200})
        self.assertEqual(stat.S_IMODE(self.tunnel.path.stat().st_mode), 0o600)
        self.assertEqual(set(status), {'provider', 'configured', 'ready', 'active', 'url_ttl', 'message'})
        start.assert_not_called()
        server.assert_not_called()

    def test_invalid_configuration_does_not_enable_the_publisher(self):
        for ttl in (0, 3599, 86401, True, 3600.0, '3600', None):
            with self.subTest(ttl=ttl):
                with self.assertRaises(UserError):
                    self.tunnel.enable(ttl)
                self.assertFalse(self.tunnel.path.exists())
        for value in (None, [], 'enabled', {'provider': 'quick-tunnel', 'enabled': 'true', 'url_ttl': 3600},
                      {'provider': 'quick-tunnel', 'enabled': True, 'url_ttl': False}):
            with self.subTest(value=value):
                self.tunnel.path.write_text(json.dumps(value))
                self.assertFalse(self.tunnel.status()['ready'])

    def test_disabled_mode_rejects_publication_before_launch(self):
        with patch('subprocess.Popen') as start, patch('app.reference_tunnel.ThreadingHTTPServer') as server:
            with self.assertRaisesRegex(UserError, '未启用'):
                self.tunnel.publish(self.data, self.image_id)
        start.assert_not_called()
        server.assert_not_called()

    def test_bad_hash_non_png_and_oversized_images_never_start_a_tunnel(self):
        self.tunnel.enable()
        cases = [(self.data, '0' * 64), (self.data, self.image_id.upper()),
                 (b'not a PNG', hashlib.sha256(b'not a PNG').hexdigest()),
                 (bytearray(self.data), self.image_id)]
        with patch.object(self.tunnel, '_ensure_started') as start:
            for data, image_id in cases:
                with self.subTest(image_id=image_id):
                    with self.assertRaises(UserError):
                        self.tunnel.publish(data, image_id)
            with patch('app.reference_tunnel.MAX_IMAGE_BYTES', len(self.data) - 1):
                with self.assertRaisesRegex(UserError, '30 MB'):
                    self.tunnel.publish(self.data, self.image_id)
        start.assert_not_called()
        self.assertEqual(self.tunnel._objects, {})

    def test_publication_uses_unpredictable_token_and_deduplicates_same_pixels(self):
        self.tunnel.enable()
        before = time.time()
        with patch.object(self.tunnel, '_ensure_started', side_effect=self.fake_started):
            first = self.tunnel.publish(self.data, self.image_id)
            second = self.tunnel.publish(self.data, self.image_id)
        self.assertEqual(first, second)
        path = urllib.parse.urlsplit(first['url']).path
        self.assertRegex(path, r'^/[a-f0-9]{64}\.png$')
        self.assertNotIn(self.image_id, first['url'])
        self.assertEqual(first['provider'], 'quick-tunnel')
        self.assertGreaterEqual(first['expires_at'], before + 3600)
        self.assertEqual(len(self.tunnel._objects), 1)
        self.assertTrue(self.tunnel.active_reference(first['url']))
        for url in (first['url'] + '?query=1', first['url'] + '#fragment',
                    first['url'].replace('https://', 'http://'),
                    first['url'].replace('https://', 'https://name:secret@'),
                    first['url'].replace('example-ephemeral', 'another-ephemeral'),
                    first['url'].replace('.com/', '.com:443/'),
                    'https://example-ephemeral.trycloudflare.com/' + '0' * 64 + '.png'):
            with self.subTest(url=url):
                self.assertFalse(self.tunnel.active_reference(url))
        self.process.poll.return_value = 1
        self.assertFalse(self.tunnel.active_reference(first['url']))

    def test_expiry_and_disable_remove_only_ephemeral_objects(self):
        record = self.publish_locally()
        token = urllib.parse.urlsplit(record['url']).path[1:-4]
        self.tunnel._objects[token]['expires_at'] = time.time() - 1
        self.assertFalse(self.tunnel.active_reference(record['url']))
        self.assertEqual(self.tunnel._objects, {})
        self.assertEqual(self.tunnel._by_image, {})
        again = self.publish_locally()
        self.assertNotEqual(record['url'], again['url'])
        status = self.tunnel.disable()
        self.assertFalse(status['ready'])
        self.assertFalse(status['active'])
        self.assertFalse(self.tunnel.active_reference(again['url']))
        self.assertEqual(self.tunnel._objects, {})
        self.process.terminate.assert_called_once()

    def test_near_expiry_same_image_gets_fresh_token_while_old_inflight_reference_remains(self):
        old = self.publish_locally()
        old_token = urllib.parse.urlsplit(old['url']).path[1:-4]
        self.tunnel._objects[old_token]['expires_at'] = time.time() + 20
        with patch.object(self.tunnel, '_ensure_started', side_effect=self.fake_started):
            new = self.tunnel.publish(self.data, self.image_id)
        self.assertNotEqual(old['url'], new['url'])
        self.assertGreater(new['expires_at'], time.time() + 3500)
        self.assertTrue(self.tunnel.active_reference(old['url']), 'An in-flight request can still fetch its original token')
        self.assertTrue(self.tunnel.active_reference(new['url']))
        self.assertEqual(len(self.tunnel._objects), 2)
        self.tunnel._objects[old_token]['expires_at'] = time.time() - 1
        self.assertFalse(self.tunnel.active_reference(old['url']))
        self.assertTrue(self.tunnel.active_reference(new['url']))
        new_token = urllib.parse.urlsplit(new['url']).path[1:-4]
        self.assertEqual(self.tunnel._by_image[self.image_id], new_token)

    def test_count_and_memory_limits_reject_new_images_without_evicting_inflight_reference(self):
        record = self.publish_locally()
        changed = fixture_image()
        changed.putpixel((0, 0), (1, 2, 3, 255))
        changed_data = png_bytes(changed)
        with patch.object(self.tunnel, '_ensure_started', side_effect=self.fake_started):
            for limit in ('MAX_REFERENCES', 'MAX_PUBLISHED_BYTES'):
                value = 1 if limit == 'MAX_REFERENCES' else len(self.data)
                with self.subTest(limit=limit), patch('app.reference_tunnel.' + limit, value):
                    with self.assertRaisesRegex(UserError, '上限'):
                        self.tunnel.publish(changed_data, sha(changed_data))
                self.assertTrue(self.tunnel.active_reference(record['url']))
                self.assertEqual(len(self.tunnel._objects), 1)

    def test_process_origin_is_isolated_loopback_png_service_never_workbench_port(self):
        self.tunnel.enable()
        process = Mock()
        process.poll.return_value = None
        process.stdout = io.StringIO(
            'INF Your quick Tunnel is available at https://isolated-tests.trycloudflare.com\n'
            'INF Registered tunnel connection connIndex=0 protocol=http2\n')
        with patch('subprocess.Popen', return_value=process) as start:
            self.tunnel._ensure_started()
        args = start.call_args.args[0]
        origin = args[args.index('--url') + 1]
        parsed = urllib.parse.urlsplit(origin)
        self.assertEqual(parsed.scheme, 'http')
        self.assertEqual(parsed.hostname, '127.0.0.1')
        self.assertEqual(parsed.port, self.tunnel._server.server_port)
        self.assertNotEqual(parsed.port, 8780)
        self.assertEqual(self.tunnel._server.server_address[0], '127.0.0.1')
        self.assertIn('--no-autoupdate', args)
        self.assertTrue(self.tunnel.status()['active'])


class IsolatedReferenceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.tunnel = ReferenceTunnel(Path(self.temporary.name))
        self.data = png_bytes(fixture_image())
        self.tunnel._binary = Mock(return_value=Path('/mock/cloudflared'))
        self.tunnel.enable()
        self.process = Mock()
        self.process.poll.return_value = None

        def started():
            self.tunnel._process = self.process
            self.tunnel._hostname = 'isolated-tests.trycloudflare.com'
            self.tunnel._connected = True

        with patch.object(self.tunnel, '_ensure_started', side_effect=started):
            self.record = self.tunnel.publish(self.data, sha(self.data))
        self.path = urllib.parse.urlsplit(self.record['url']).path
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.tunnel._handler())
        self.server.daemon_threads = True
        self.tunnel._server = self.server
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.tunnel.close()
        self.temporary.cleanup()

    def request(self, path, method='GET'):
        return urllib.request.urlopen(urllib.request.Request(self.origin + path, method=method), timeout=2)

    def test_known_token_serves_exact_png_bytes_and_safe_cache_headers(self):
        with self.request(self.path) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.headers['Content-Length'], str(len(self.data)))
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertEqual(response.read(), self.data)

    def test_head_exposes_same_png_headers_without_a_body(self):
        with self.request(self.path, method='HEAD') as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.headers['Content-Length'], str(len(self.data)))
            self.assertEqual(response.read(), b'')

    def test_unknown_paths_platform_apis_workspace_paths_queries_and_traversals_are_not_served(self):
        paths = ('/', '/api/health', '/api/characters', '/storage/credentials.json',
                 '/../../etc/passwd', '/%2e%2e/%2e%2e/etc/passwd',
                 self.path + '?download=1', self.path + '/', '/missing.png',
                 '/' + '0' * 64 + '.png')
        for path in paths:
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(path)
                self.assertEqual(caught.exception.code, 404)
                body = caught.exception.read().decode()
                self.assertNotIn(self.temporary.name, body)
                self.assertNotIn('root:', body)
                self.assertNotIn(self.data.hex(), body)

    def test_write_methods_cannot_change_or_remove_the_reference(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'):
            with self.subTest(method=method):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(self.path, method=method)
                self.assertEqual(caught.exception.code, 405)
                self.assertEqual(caught.exception.read(), b'')
        with self.request(self.path) as response:
            self.assertEqual(response.read(), self.data)

    def test_expired_reference_returns_404_and_stays_removed(self):
        token = self.path[1:-4]
        self.tunnel._objects[token]['expires_at'] = time.time() - 1
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(self.path)
        self.assertEqual(caught.exception.code, 404)
        self.assertFalse(self.tunnel.active_reference(self.record['url']))
        self.assertEqual(self.tunnel._objects, {})


class TunnelAdminEndpointTests(unittest.TestCase):
    def setUp(self):
        from app import server
        self.server = server
        self.temporary = tempfile.TemporaryDirectory()
        self.matrix = Matrix(Path(self.temporary.name))
        self.handler = object.__new__(server.Handler)
        self.handler.path = '/api/reference-hosting/tunnel'
        self.handler.send = lambda value, status=200, **kwargs: {'body': value, 'status': status}

    def tearDown(self):
        self.matrix.tunnel.close()
        self.temporary.cleanup()

    def test_lan_clients_cannot_enable_or_disable_public_reference_channel(self):
        with patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(self.matrix.tunnel, 'enable') as enable, \
                patch.object(self.matrix.tunnel, 'disable') as disable:
            for client in ('172.16.7.76', '203.0.113.2', '::ffff:172.16.7.76'):
                for action in ('enable', 'disable'):
                    with self.subTest(client=client, action=action):
                        self.handler.client_address = (client, 12345)
                        response = self.handler.post({'action': action})
                        self.assertEqual(response['status'], 403)
            enable.assert_not_called()
            disable.assert_not_called()

    def test_loopback_admin_can_select_mode_but_unknown_actions_do_not_start_anything(self):
        self.handler.client_address = ('127.0.0.1', 12345)
        with patch.object(self.server, 'MATRIX', self.matrix), \
                patch.object(self.matrix.tunnel, 'enable', return_value={'ready': True}) as enable, \
                patch.object(self.matrix.tunnel, 'disable', return_value={'ready': False}) as disable:
            self.assertTrue(self.handler.post({'action': 'enable'})['body']['ready'])
            self.assertFalse(self.handler.post({'action': 'disable'})['body']['ready'])
            enable.assert_called_once_with()
            disable.assert_called_once_with()
            with self.assertRaises(UserError):
                self.handler.post({'action': 'unknown'})
            self.assertEqual(enable.call_count, 1)
            self.assertEqual(disable.call_count, 1)


class TunnelProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.image = fixture_image()
        self.data = png_bytes(self.image)
        self.image_id = sha(self.data)
        self.matrix = Matrix(self.root)
        self.matrix.request = Mock(side_effect=AssertionError('Reference publication must never generate'))
        self.matrix.download_result = Mock(return_value=self.data)
        self.matrix.hosting.status = Mock(return_value={
            'provider': 's3', 'configured': False, 'ready': False, 'message': 'Not configured'})
        self.matrix.hosting.publish = Mock(side_effect=AssertionError('No S3 should be contacted'))
        self.matrix.tunnel.status = Mock(return_value={
            'provider': 'quick-tunnel', 'configured': True, 'ready': True,
            'active': False, 'message': 'Ready to start on first image'})
        self.matrix.tunnel.publish = Mock(return_value={
            'provider': 'quick-tunnel', 'url': REFERENCE, 'expires_at': time.time() + 3600})
        self.matrix.tunnel.active_reference = Mock(return_value=True)

    def tearDown(self):
        self.matrix.tunnel.close()
        self.temporary.cleanup()

    def test_unconfigured_s3_uses_tunnel_then_verified_cache_without_generation(self):
        record = self.matrix.ensure_reference(self.image)
        again = self.matrix.ensure_reference(self.image.copy())
        self.assertEqual(record, again)
        self.assertEqual(record['provider'], 'quick-tunnel')
        self.assertEqual(record['image_id'], self.image_id)
        self.matrix.tunnel.publish.assert_called_once_with(self.data, self.image_id)
        self.matrix.download_result.assert_called_once_with(REFERENCE, timeout=20)
        self.matrix.tunnel.active_reference.assert_called_with(REFERENCE)
        self.matrix.hosting.publish.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_previous_tunnel_cache_is_rejected_after_session_ends(self):
        self.matrix.ensure_reference(self.image)
        restarted = Matrix(self.root)
        restarted.request = Mock(side_effect=AssertionError('Local cache lookup must never generate'))
        restarted.download_result = Mock(side_effect=AssertionError('Local cache lookup must not fetch'))
        restarted.tunnel.active_reference = Mock(return_value=False)
        try:
            self.assertIsNone(restarted.cached_reference(self.image))
            restarted.tunnel.active_reference.assert_called_once_with(REFERENCE)
            restarted.request.assert_not_called()
            restarted.download_result.assert_not_called()
        finally:
            restarted.tunnel.close()

    def test_stopped_tunnel_reference_is_republished_under_new_hostname(self):
        old = self.matrix.ensure_reference(self.image)
        new_url = REFERENCE.replace('example-ephemeral', 'replacement-ephemeral')
        self.matrix.tunnel.active_reference.side_effect = lambda url: url == new_url
        self.matrix.tunnel.publish.return_value = {
            'provider': 'quick-tunnel', 'url': new_url, 'expires_at': time.time() + 3600}
        new = self.matrix.ensure_reference(self.image)
        self.assertNotEqual(old['url'], new['url'])
        self.assertEqual(self.matrix.tunnel.publish.call_count, 2)
        self.assertEqual(self.matrix.download_result.call_count, 2)
        self.assertEqual(self.matrix.cached_reference(self.image)['url'], new_url)
        self.matrix.hosting.publish.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_expired_tunnel_cache_is_never_reused_even_when_process_is_running(self):
        record = self.matrix.ensure_reference(self.image)
        atomic_json(self.matrix.references / (self.image_id + '.json'),
                    {**record, 'expires_at': time.time() - 1})
        self.assertIsNone(self.matrix.cached_reference(self.image))
        self.matrix.request.assert_not_called()

    def test_wrong_public_pixels_do_not_cache_or_submit_generation(self):
        wrong = self.image.copy()
        wrong.putpixel((0, 0), (1, 2, 3, 255))
        self.matrix.download_result.return_value = png_bytes(wrong)
        with self.assertRaisesRegex(UserError, '像素'):
            self.matrix.ensure_reference(self.image)
        self.assertIsNone(self.matrix.cached_reference(self.image))
        self.matrix.tunnel.publish.assert_called_once_with(self.data, self.image_id)
        self.matrix.request.assert_not_called()

    def test_verified_s3_configuration_retains_precedence_over_temporary_tunnel(self):
        self.matrix.hosting.status.return_value = {
            'provider': 's3', 'configured': True, 'ready': True, 'message': 'Verified'}
        permanent = 'https://objects.example/permanent.png'
        self.matrix.hosting.publish.side_effect = None
        self.matrix.hosting.publish.return_value = {
            'provider': 's3', 'url': permanent, 'expires_at': time.time() + 86400}
        self.assertEqual(self.matrix.reference_status()['provider'], 's3')
        self.assertEqual(self.matrix.ensure_reference(self.image)['url'], permanent)
        self.matrix.hosting.publish.assert_called_once_with(self.data, self.image_id)
        self.matrix.tunnel.publish.assert_not_called()
        self.matrix.tunnel.active_reference.assert_not_called()
        self.matrix.request.assert_not_called()

    def test_reading_upload_readiness_does_not_start_or_publish_a_tunnel(self):
        with patch('subprocess.Popen', side_effect=AssertionError('Status must not start a process')) as start:
            status = self.matrix.reference_status()
        self.assertTrue(status['ready'])
        self.assertFalse(status['active'])
        self.assertEqual(status['provider'], 'quick-tunnel')
        start.assert_not_called()
        self.matrix.tunnel.publish.assert_not_called()
        self.matrix.download_result.assert_not_called()
        self.matrix.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
