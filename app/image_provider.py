"""Selectable image backend. Existing Matrix jobs keep their original routing.

OpenAI Images-compatible synchronous APIs run off the HTTP request thread. Each
accepted operation is persisted before execution; interrupted submissions never
retry automatically. Configuration and credentials are snapshotted separately.
"""
from __future__ import annotations
import base64
import binascii
import ipaddress
import http.client
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from .core import UserError, atomic_json, png_bytes, read_image, sha
from .provider import Matrix, DEFAULT_MODEL, NoRedirect, ProviderError, classify_provider_failure

LIMIT = 30 * 1024 * 1024


def safe_https(value):
    """Validate configured endpoints; never allow credentials in URLs or redirects."""
    p = urllib.parse.urlsplit(str(value))
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.query or p.fragment or any(c.isspace() for c in str(value))):
        raise UserError('API 地址必须是 HTTPS，不包含密钥、查询参数或片段')
    if p.hostname.lower() == 'localhost' or p.hostname.lower().endswith('.localhost'):
        raise UserError('API 地址不能指向本机或内网')
    try: address = ipaddress.ip_address(p.hostname)
    except ValueError: address = None
    if address is not None and not address.is_global:
        raise UserError('API 地址不能指向本机或内网')
    return str(value).rstrip('/')


def check_public_host(url):
    p = urllib.parse.urlsplit(url)
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise UserError('图片服务地址必须可从公网访问，不能指向本机或内网')
    except (OSError, ValueError):
        raise ProviderError(classify_provider_failure(category='connection_failed')) from None


def secret_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream)
    temp.replace(path)


class ImageProvider(Matrix):
    def __init__(self, storage):
        super().__init__(storage)
        self.config_path = Path(storage) / 'image-provider.json'
        self.profiles = Path(storage) / 'image-provider-profiles'
        self.jobs = Path(storage) / 'image-provider-jobs'
        self.local_refs = Path(storage) / 'image-provider-references'
        self.running = set()
        self.slots = threading.BoundedSemaphore(2)

    def config(self):
        if not self.config_path.exists():
            return {'provider': 'matrix', 'model': DEFAULT_MODEL}
        active = json.loads(self.config_path.read_text())
        if active['provider'] == 'matrix':
            return {'provider': 'matrix', 'model': DEFAULT_MODEL}
        profile = active['profile']
        if not re.fullmatch(r'[a-f0-9]{32}', profile):
            raise UserError('生图配置编号无效')
        return json.loads((self.profiles / (profile + '.json')).read_text())

    def status(self):
        cfg = self.config()
        if cfg['provider'] == 'matrix':
            return {**super().status(), 'provider': 'matrix', 'provider_name': '亿创',
                    'base_url': '', 'supports_edit': True}
        return {'state': 'configured', 'message': 'API 配置已保存；是否能生图需实际任务验证',
                'provider': cfg['provider'], 'provider_name': 'OpenAI Images 兼容 API',
                'model': cfg['model'], 'model_name': cfg['model'], 'base_url': cfg['base_url'],
                'size': cfg['size'], 'quality': cfg['quality'], 'response_format': cfg['response_format'],
                'configured': bool(cfg['key']), 'source': '平台本机配置', 'kind': 'API Key',
                'reference_transport': 'multipart-file', 'supports_edit': True}

    def configure(self, data):
        with self.lock:
            kind = data.get('provider')
            if kind == 'matrix':
                if data.get('key'):
                    # Matrix.connect calls status; select Matrix only after credential validation.
                    super().connect(data['key'])
                secret_json(self.config_path, {'provider': 'matrix'})
                return self.status()
            if kind != 'openai-images':
                raise UserError('不支持的生图接口类型')
            base = safe_https(data.get('base_url', ''))
            if base.endswith(('/images/generations', '/images/edits')):
                raise UserError('填写 API 根地址（例如 https://api.openai.com/v1），不要填写具体生图路径')
            model = str(data.get('model', '')).strip()
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}', model):
                raise UserError('请填写服务商提供的准确模型 ID')
            key = str(data.get('key', '')).strip()
            current = self.config()
            if not key and current.get('provider') == kind and current.get('base_url') == base:
                key = current['key']
            if not key or len(key) > 8192 or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
                raise UserError('请填写此 API 地址对应的有效密钥；更换地址需重新填写')
            size = data.get('size', 'auto')
            if size not in ('auto', '1024x1024', '1024x1536', '1536x1024'):
                raise UserError('图片尺寸选项无效')
            quality = data.get('quality', '')
            response_format = data.get('response_format', '')
            if quality not in ('', 'auto', 'low', 'medium', 'high') or response_format not in ('', 'url', 'b64_json'):
                raise UserError('生图质量或返回格式选项无效')
            profile = uuid.uuid4().hex
            cfg = {'provider': kind, 'profile': profile, 'base_url': base, 'model': model,
                   'key': key, 'size': size, 'quality': quality, 'response_format': response_format}
            secret_json(self.profiles / (profile + '.json'), cfg)
            secret_json(self.config_path, {'provider': kind, 'profile': profile})
            return self.status()

    def connect(self, key=''):
        if self.config()['provider'] == 'matrix':
            return super().connect(key)
        if key:
            raise UserError('请在生图服务设置中保存该 API 的密钥')
        return self.status()

    def reference_status(self):
        if self.config()['provider'] == 'matrix':
            return super().reference_status()
        return {'provider': 'direct-upload', 'ready': True,
                'message': '当前 API 直接接收参考图片文件，无需公开链接或图片存储',
                'reference_transport': 'multipart-file', 'public_link': False}

    def cached_reference(self, image):
        if self.config()['provider'] == 'matrix':
            return super().cached_reference(image)
        digest = sha(png_bytes(image.convert('RGBA')))
        if (self.local_refs / (digest + '.png')).exists():
            return {'url': 'local-reference:' + digest, 'image_id': digest, 'provider': 'direct-upload'}
        return None

    def ensure_reference(self, image, force=False):
        if self.config()['provider'] == 'matrix':
            return super().ensure_reference(image, force=force)
        data = png_bytes(image.convert('RGBA')); digest = sha(data)
        with self.lock:
            self.local_refs.mkdir(parents=True, exist_ok=True)
            path = self.local_refs / (digest + '.png')
            if not path.exists():
                temp = path.with_suffix('.tmp'); temp.write_bytes(data); temp.replace(path)
        return {'url': 'local-reference:' + digest, 'image_id': digest, 'provider': 'direct-upload'}

    def reference_url(self, reference):
        if re.fullmatch(r'local-reference:[a-f0-9]{64}', str(reference)):
            if self.config()['provider'] != 'openai-images':
                raise UserError('当前服务需要 HTTPS 参考图，请重新准备参考图')
            return reference
        return Matrix.reference_url(reference)

    def generate(self, prompt, reference_url=None, width=512, height=512):
        if self.config()['provider'] == 'matrix':
            return super().generate(prompt, reference_url, width, height)
        # Keep the existing artwork prompt, but never send the placeholder upstream.
        inputs = Matrix.generation_input(prompt, 'https://reference.invalid/image.png' if reference_url else None, width, height)
        if reference_url: inputs['input_images'] = [self.reference_url(reference_url)]
        return self.generation_id(self.request('/models/' + DEFAULT_MODEL + '/generate', {'input': inputs}))

    def request(self, route, data=None, key=None):
        cfg = self.config()
        if not route.endswith('/generate') or cfg['provider'] == 'matrix':
            return super().request(route, data, key)
        inputs = data['input']
        if not isinstance(inputs.get('prompt'), str) or not inputs['prompt'].strip():
            raise UserError('生图提示词不能为空')
        identifier = 'api-' + uuid.uuid4().hex
        record = {'id': identifier, 'status': 'queued', 'profile': cfg['profile'],
                  'provider': cfg['provider'], 'model': cfg['model'], 'input': inputs,
                  'created_at': time.time()}
        with self.lock:
            atomic_json(self.job_path(identifier), record)
            self._start(identifier)
        return {'data': {'gen_id': identifier}}

    def job_path(self, identifier):
        if not re.fullmatch(r'api-[a-f0-9]{32}', str(identifier)):
            raise UserError('API 任务编号无效')
        return self.jobs / (identifier + '.json')

    def _start(self, identifier):
        if identifier in self.running: return
        self.running.add(identifier)
        threading.Thread(target=self._execute, args=(identifier,), daemon=True).start()

    def _reference_bytes(self, value):
        if re.fullmatch(r'local-reference:[a-f0-9]{64}', value):
            return (self.local_refs / (value.split(':')[1] + '.png')).read_bytes()
        value = Matrix.reference_url(value)
        check_public_host(value)
        return super().download_result(value)

    def _wire(self, cfg, inputs):
        fields = {'model': cfg['model'], 'prompt': inputs['prompt'], 'n': 1, 'size': cfg['size']}
        for key in ('quality', 'response_format'):
            if cfg[key]: fields[key] = cfg[key]
        references = inputs.get('input_images') or []
        if not references:
            return '/images/generations', json.dumps(fields).encode(), 'application/json'
        boundary = 'spine' + uuid.uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        for index, ref in enumerate(references):
            data = png_bytes(read_image(self._reference_bytes(ref)))
            if len(data) > LIMIT: raise UserError('参考图片超过 30 MB')
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="reference-{index}.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + data + b'\r\n')
        chunks.append(f'--{boundary}--\r\n'.encode())
        return '/images/edits', b''.join(chunks), 'multipart/form-data; boundary=' + boundary

    def _api_request(self, cfg, route, body, content_type):
        check_public_host(cfg['base_url'])
        req = urllib.request.Request(cfg['base_url'] + route, data=body,
                                     headers={'Authorization': 'Bearer ' + cfg['key'],
                                              'Content-Type': content_type, 'Accept': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=180) as response:
                raw = response.read(45 * 1024 * 1024 + 1)
                if len(raw) > 45 * 1024 * 1024: raise ValueError('oversized response')
                result = json.loads(raw)
                if not isinstance(result, dict): raise ValueError('invalid response')
                if result.get('error'):
                    raise ProviderError(classify_provider_failure(result, operation='submit'))
                return result
        except ProviderError:
            raise
        except urllib.error.HTTPError as error:
            try: payload = json.loads(error.read(65536))
            except (ValueError, OSError): payload = {}
            raise ProviderError(classify_provider_failure(payload, http_status=error.code, operation='submit')) from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException):
            raise ProviderError(classify_provider_failure(category='submission_unknown', operation='submit')) from None

    def _execute(self, identifier):
        path = self.job_path(identifier)
        with self.slots:
            try:
                with self.lock:
                    record = json.loads(path.read_text())
                    if record['status'] != 'queued': return
                    # This transition occurs before I/O. After a crash it may have billed.
                    record['status'] = 'running'; atomic_json(path, record)
                cfg = json.loads((self.profiles / (record['profile'] + '.json')).read_text())
                route, body, mime = self._wire(cfg, record['input'])
                result = self._api_request(cfg, route, body, mime)
                rows = result.get('data')
                item = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
                if item.get('b64_json'):
                    try: image = base64.b64decode(item['b64_json'], validate=True)
                    except (ValueError, binascii.Error, TypeError): raise UserError('API 图片编码无效') from None
                    self._save_image(path, image)
                    record['status'] = 'completed'
                elif item.get('url'):
                    url = Matrix.reference_url(item['url'])
                    # Save the URL before download so download retries never generate again.
                    record.update(status='download_pending', result_url=url)
                    atomic_json(path, record)
                else:
                    raise ProviderError(classify_provider_failure(result, category='empty_output'))
                record.pop('input', None)
                atomic_json(path, record)
            except Exception as error:
                if 'record' in locals():
                    failure = error.details if isinstance(error, ProviderError) else classify_provider_failure(category='generation_failed')
                    record.update(status='submission_unknown' if failure['submission_state'] == 'unknown' else 'failed', failure=failure)
                    record.pop('input', None); atomic_json(path, record)
            finally:
                with self.lock: self.running.discard(identifier)

    @staticmethod
    def _save_image(path, image):
        if len(image) > LIMIT: raise UserError('生成图片超过 30 MB')
        image = png_bytes(read_image(image))
        target = path.with_suffix('.png'); temp = target.with_suffix('.png.tmp')
        temp.write_bytes(image); temp.replace(target)

    def poll(self, gen_id, model=DEFAULT_MODEL):
        if not str(gen_id).startswith('api-'):
            return super().poll(gen_id, model)
        with self.lock:
            path = self.job_path(gen_id); record = json.loads(path.read_text())
            if record['status'] == 'queued': self._start(gen_id)
            if record['status'] == 'running' and gen_id not in self.running:
                record.update(status='submission_unknown', failure=classify_provider_failure(category='submission_unknown', operation='submit'))
                record.pop('input', None); atomic_json(path, record)
            if record['status'] == 'download_pending':
                check_public_host(record['result_url'])
                self._save_image(path, super().download_result(record['result_url']))
                record['status'] = 'completed'; atomic_json(path, record)
            if record.get('failure'):
                raise ProviderError(record['failure'], terminal_status=record['status'])
            outputs = [{'data': {'image_url': 'provider-result:' + gen_id}}] if record['status'] == 'completed' else []
            return {'data': {'status': 'completed' if outputs else 'pending', 'outputs': outputs}}

    def download_result(self, url, timeout=60):
        if str(url).startswith('provider-result:'):
            path = self.job_path(str(url).split(':', 1)[1])
            return path.with_suffix('.png').read_bytes()
        return super().download_result(url, timeout)

    def job_metadata(self, gen_id):
        if str(gen_id).startswith('api-'):
            record = json.loads(self.job_path(gen_id).read_text())
            return {k: record[k] for k in ('provider', 'model')}
        return {'provider': 'matrix', 'model': DEFAULT_MODEL}
