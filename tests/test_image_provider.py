import base64
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from app.core import UserError, png_bytes, atomic_json
from app.image_provider import ImageProvider, safe_https
from app.provider import Matrix, DEFAULT_MODEL, ProviderError, classify_provider_failure
from app.jobs import JobHistory


class ImageProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.provider = ImageProvider(self.tmp.name)
        self.addCleanup(self.provider.tunnel.close)
        self.provider._start = Mock()  # Execute accepted operations deterministically.
        self.config = {'provider': 'openai-images', 'base_url': 'https://api.example/v1',
                       'model': 'gpt-image-2', 'key': 'secret-fixture-key'}
        self.png = png_bytes(Image.new('RGBA', (8, 8), (23, 75, 150, 170)))

    def configure(self, **changes):
        return self.provider.configure({**self.config, **changes})

    def generate(self):
        self.configure()
        return self.provider.generate_character('Original character in blue costume')

    def test_config_validates_endpoint_and_never_leaks_or_reuses_cross_host_key(self):
        for url in ['http://api.example/v1', 'https://127.0.0.1/v1', 'https://10.0.0.1/v1',
                    'https://localhost/v1', 'https://user:secret@api.example/v1',
                    'https://api.example/v1?key=secret', 'https://api.example/v1#secret']:
            with self.subTest(url=url), self.assertRaises(UserError): self.configure(base_url=url)
        status = self.configure()
        self.assertNotIn('secret-fixture-key', json.dumps(status))
        self.assertEqual(status['reference_transport'], 'multipart-file')
        cfg = self.provider.config()
        self.assertEqual((self.provider.profiles / (cfg['profile'] + '.json')).stat().st_mode & 0o777, 0o600)
        self.configure(key='', model='other/image2')
        self.assertEqual(self.provider.config()['key'], self.config['key'])
        with self.assertRaises(UserError): self.configure(key='', base_url='https://different.example/v1')

    def test_legacy_matrix_routing_is_preserved_after_switch(self):
        self.configure()
        with patch.object(Matrix, 'request', return_value={'data': {'status': 'pending'}}) as request:
            self.provider.poll('legacy-gen-id', 'nano-banana-2')
        self.assertIn('/nano-banana-2/legacy-gen-id/status', request.call_args.args[0])
        self.provider.configure({'provider': 'matrix'})
        with patch.object(Matrix, 'request', return_value={'data': {'gen_id': 'matrix-new'}}) as request:
            self.assertEqual(self.provider.generate_character('character'), 'matrix-new')
        self.assertIn(DEFAULT_MODEL, request.call_args.args[0])

    def test_model_snapshot_base64_and_result_survive_provider_switch_and_restart(self):
        gen = self.generate()
        self.configure(model='replacement-model', base_url='https://replacement.example/v1')
        seen = []
        def respond(cfg, route, body, mime):
            seen.append((cfg, route, json.loads(body), mime))
            return {'data': [{'b64_json': base64.b64encode(self.png).decode()}]}
        with patch.object(self.provider, '_api_request', side_effect=respond): self.provider._execute(gen)
        self.assertEqual(seen[0][0]['base_url'], 'https://api.example/v1')
        self.assertEqual(seen[0][2]['model'], 'gpt-image-2')
        self.assertEqual(seen[0][1], '/images/generations')
        self.assertEqual(seen[0][2]['n'], 1)
        self.provider.configure({'provider': 'matrix'})
        restored = ImageProvider(self.tmp.name); self.addCleanup(restored.tunnel.close)
        result = restored.poll(gen)
        self.assertEqual(result['data']['status'], 'completed')
        self.assertEqual(restored.download_result(result['data']['outputs'][0]['data']['image_url']), self.png)
        record = json.loads(restored.job_path(gen).read_text())
        self.assertNotIn('input', record)
        self.assertNotIn(self.config['key'], json.dumps(record))

    def test_reference_edit_uploads_exact_png_without_public_hosting(self):
        self.configure()
        image = Image.open(io.BytesIO(self.png)).convert('RGBA')
        with patch.object(self.provider.tunnel, 'publish', side_effect=AssertionError('No publication')):
            ref = self.provider.ensure_reference(image)['url']
            gen = self.provider.generate_part_sheet('character-assets', ref, body_layout='combined')
        calls = []
        def respond(cfg, route, body, mime):
            calls.append((route, body, mime))
            return {'data': [{'b64_json': base64.b64encode(self.png).decode()}]}
        with patch.object(self.provider, '_api_request', side_effect=respond): self.provider._execute(gen)
        route, body, mime = calls[0]
        self.assertEqual(route, '/images/edits')
        self.assertIn(b'name="image[]"', body)
        self.assertIn(self.png, body)
        self.assertNotIn(b'local-reference:', body)
        self.assertTrue(mime.startswith('multipart/form-data;'))

    def test_download_retry_does_not_resubmit_and_uses_no_api_credentials(self):
        gen = self.generate()
        with patch.object(self.provider, '_api_request', return_value={'data': [{'url': 'https://cdn.example/image.png'}]}) as request:
            self.provider._execute(gen)
            with patch('app.image_provider.check_public_host'), patch.object(Matrix, 'download_result', side_effect=[ProviderError(classify_provider_failure(category='download_failed')), self.png]) as download:
                with self.assertRaises(ProviderError): self.provider.poll(gen)
                self.assertEqual(self.provider.poll(gen)['data']['status'], 'completed')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(download.call_count, 2)

    def test_timeout_and_interrupted_submission_do_not_retry(self):
        gen = self.generate()
        with patch.object(self.provider, '_api_request', side_effect=ProviderError(classify_provider_failure(category='submission_unknown', operation='submit'))) as request:
            self.provider._execute(gen)
            with self.assertRaises(ProviderError) as caught: self.provider.poll(gen)
            self.assertEqual(caught.exception.terminal_status, 'submission_unknown')
            self.provider._execute(gen)
            self.assertEqual(request.call_count, 1)
        path = self.provider.job_path(gen)
        record = json.loads(path.read_text());record['status'] = 'running';record.pop('failure', None);atomic_json(path,record)
        with self.assertRaises(ProviderError) as caught: self.provider.poll(gen)
        self.assertEqual(caught.exception.terminal_status, 'submission_unknown')
        self.assertEqual(self.provider._start.call_count, 1)

    def test_rejection_is_terminal_in_history_without_fallback(self):
        gen = self.generate()
        error = ProviderError(classify_provider_failure({'error': {'code': 'content_policy_violation'}}, operation='submit'))
        with patch.object(self.provider, '_api_request', side_effect=error) as request:
            self.provider._execute(gen)
            history = JobHistory(self.tmp.name, self.provider)
            path = history.path(uuid.uuid4().hex, uuid.uuid4().hex)
            atomic_json(path, {'status': 'pending', 'gen_id': gen, 'model': 'gpt-image-2'})
            record = history.refresh(path)
            self.assertEqual(record['status'], 'failed')
            self.assertTrue(record['failure']['stop_generation'])
            history.refresh(path, force=True)
            self.assertEqual(request.call_count, 1)

    def test_http_error_redaction_and_success_error_envelope_keep_moderation(self):
        self.configure(); cfg = self.provider.config()
        response = Mock(); response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({'error': {'code': 'content_policy_violation', 'message': self.config['key']}}).encode()
        opener = Mock(); opener.open.return_value = response
        with patch('app.image_provider.check_public_host'), patch('app.image_provider.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ProviderError) as caught:
                self.provider._api_request(cfg, '/images/generations', b'{}', 'application/json')
        self.assertTrue(caught.exception.details['stop_generation'])
        self.assertNotIn(self.config['key'], str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_unknown_history_stops_polling(self):
        gen = self.generate(); path = self.provider.job_path(gen)
        record = json.loads(path.read_text()); record['status'] = 'running'; atomic_json(path, record)
        history = JobHistory(self.tmp.name, self.provider)
        owner = history.path(uuid.uuid4().hex, uuid.uuid4().hex)
        atomic_json(owner, {'status': 'pending', 'gen_id': gen, 'model': 'gpt-image-2'})
        result = history.refresh(owner)
        self.assertEqual(result['status'], 'submission_unknown')
        self.assertEqual(result['failure']['submission_state'], 'unknown')
        self.assertFalse(history.due(owner, result, True))

    def test_settings_reject_remote_changes_and_return_sanitized_local_config(self):
        from app import server
        handler = object.__new__(server.Handler)
        handler.path = '/api/provider/configure'
        handler.send = lambda value, status=200: (value, status)
        with patch.object(server, 'MATRIX', self.provider):
            handler.client_address = ('192.168.1.5', 1234)
            self.assertEqual(handler.post(self.config)[1], 403)
            self.assertFalse(self.provider.config_path.exists())
            handler.client_address = ('127.0.0.1', 1234)
            response, status = handler.post(self.config)
        self.assertEqual(status, 200)
        self.assertEqual(response['model'], 'gpt-image-2')
        self.assertNotIn(self.config['key'], json.dumps(response))

    def test_character_endpoint_to_durable_candidate(self):
        from app import server
        from app.character import CharacterProject
        self.configure()
        character = CharacterProject.create(self.tmp.name, 'provider integration fixture')
        source = Image.new('RGBA', (128, 128)); source.paste('#397ac1', (32, 16, 96, 120))
        character.set_image(png_bytes(source), 0)
        history = JobHistory(self.tmp.name, self.provider, 'characters')
        handler = object.__new__(server.Handler)
        handler.path = '/api/characters/' + character.id + '/generate-parts'
        handler.send = lambda value, *args: value
        with patch.object(server, 'MATRIX', self.provider), patch.object(server, 'get_character', return_value=character), patch.object(server, 'CHARACTER_JOBS', history):
            request = {'nonce': uuid.uuid4().hex, 'revision': character.meta['revision'], 'sheets': ['character-assets'], 'body_layout': 'combined'}
            jobs = handler.post(request)
            self.assertEqual(handler.post(request), jobs)
        self.assertEqual(self.provider._start.call_count, 1)
        job = jobs[0]
        self.assertEqual((job['provider'], job['model']), ('openai-images', 'gpt-image-2'))
        with patch.object(self.provider, '_api_request', return_value={'data': [{'b64_json': base64.b64encode(self.png).decode()}]}):
            self.provider._execute(job['gen_id'])
        result = history.refresh(history.path(character.id, job['id']))
        self.assertTrue(result['candidate_ready'])
        self.assertEqual(history.path(character.id, job['id']).with_suffix('.png').read_bytes(), self.png)

if __name__ == '__main__': unittest.main()
