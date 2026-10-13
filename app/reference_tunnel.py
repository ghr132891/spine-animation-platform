"""Temporary PNG publication through an isolated Cloudflare Quick Tunnel.

The public origin is a separate loopback HTTP server. It cannot serve workspace
files or call the workbench API; its only resources are short-lived PNG bytes.
"""
from __future__ import annotations

import atexit
import collections
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from .core import MAX_PIXELS, UserError


MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_PUBLISHED_BYTES = 128 * 1024 * 1024
MAX_REFERENCES = 64
STARTUP_TIMEOUT = 35
TUNNEL_URL = re.compile(r'https://([a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com)\b')
REFERENCE_PATH = re.compile(r'/([a-f0-9]{64})\.png')


class ReferenceTunnel:
    def __init__(self, storage):
        self.path = Path(storage) / 'reference-tunnel.json'
        self.lock = threading.RLock()
        self.start_lock = threading.RLock()
        self._process = None
        self._server = None
        self._hostname = None
        self._connected = False
        self._objects = {}
        self._by_image = {}
        self._logs = collections.deque(maxlen=20)
        self._closed = False
        self._stop_event = threading.Event()
        self._cleanup_thread = None
        atexit.register(self.close)

    def config(self):
        try:
            record = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {'provider': 'quick-tunnel', 'enabled': False, 'url_ttl': 3600}
        if (not isinstance(record, dict) or record.get('provider') != 'quick-tunnel'
                or type(record.get('enabled')) is not bool
                or type(record.get('url_ttl')) is not int
                or not 3600 <= record['url_ttl'] <= 86400):
            return {'provider': 'quick-tunnel', 'enabled': False, 'url_ttl': 3600}
        return {key: record[key] for key in ('provider', 'enabled', 'url_ttl')}

    def _binary(self):
        configured = os.environ.get('SPINE_CLOUDFLARED_BINARY')
        path = Path(configured) if configured else Path(__file__).resolve().parent.parent / 'tools' / 'bin' / 'cloudflared'
        return path if path.is_file() and os.access(path, os.X_OK) else None

    def status(self):
        config = self.config()
        configured = config['enabled']
        available = self._binary() is not None
        with self.lock:
            active = (self._process is not None and self._process.poll() is None
                      and self._hostname is not None and self._connected)
            ready = configured and available and not self._closed
        message = ('平台会自动提供临时 HTTPS 参考图链接；无需登录亿创或配置图片存储'
                   if ready else '临时参考图通道尚未启用')
        if configured and not available:
            message = '临时参考图通道已启用，服务器尚未安装 cloudflared'
        if self._closed:
            message = '临时参考图通道已关闭，请重启平台服务'
        return {'provider': 'quick-tunnel', 'configured': configured, 'ready': ready,
                'active': active, 'url_ttl': config['url_ttl'], 'message': message}

    def _save(self, enabled, url_ttl):
        if type(url_ttl) is not int or not 3600 <= url_ttl <= 86400:
            raise UserError('临时图片链接有效期须为 3600–86400 秒')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as target:
                json.dump({'provider': 'quick-tunnel', 'enabled': enabled, 'url_ttl': url_ttl}, target)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def enable(self, url_ttl=3600):
        with self.start_lock:
            if self._closed:
                raise UserError('临时参考图通道已关闭，请重启平台服务')
            self._save(True, url_ttl)
        return self.status()

    def disable(self):
        with self.start_lock:
            self._save(False, self.config()['url_ttl'])
            self._stop()
        return self.status()

    def _cleanup_locked(self, now):
        for token, item in list(self._objects.items()):
            if item['expires_at'] <= now:
                del self._objects[token]
                if self._by_image.get(item['image_id']) == token:
                    self._by_image.pop(item['image_id'], None)

    def _cleanup_loop(self):
        while not self._stop_event.wait(30):
            with self.lock:
                self._cleanup_locked(time.time())

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'ReferenceImage'
            sys_version = ''

            def log_message(self, *_args):
                pass

            def _serve_png(self, head=False):
                match = REFERENCE_PATH.fullmatch(self.path)
                if match is None:
                    self.send_error(404)
                    return
                with owner.lock:
                    owner._cleanup_locked(time.time())
                    item = owner._objects.get(match.group(1))
                    data = None if item is None else item['data']
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store, max-age=0')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Connection', 'close')
                self.end_headers()
                if head:
                    return
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def do_GET(self):
                self._serve_png()

            def do_HEAD(self):
                self._serve_png(head=True)

            def _unsupported(self):
                self.send_response(405)
                self.send_header('Allow', 'GET, HEAD')
                self.send_header('Content-Length', '0')
                self.send_header('Connection', 'close')
                self.end_headers()

            do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _unsupported

        return Handler

    def _drain(self, process, ready_event):
        try:
            while True:
                line = process.stdout.readline(2048)
                if not line:
                    break
                match = TUNNEL_URL.search(line)
                with self.lock:
                    if process is not self._process:
                        break
                    if match:
                        self._hostname = match.group(1)
                    if 'registered tunnel connection' in line.lower():
                        self._connected = True
                    if self._hostname and self._connected:
                        ready_event.set()
                    # Keep only bounded diagnostic text; links and opaque tokens
                    # must never enter the platform status or normal logs.
                    sanitized = re.sub(r'https?://\S+', '[网址]', line.strip())
                    sanitized = re.sub(r'[A-Za-z0-9_./+=-]{40,}', '[标识]', sanitized)
                    sanitized = ''.join(c for c in sanitized if c.isprintable())
                    self._logs.append(sanitized[:300])
        finally:
            ready_event.set()
            if process.stdout is not None:
                process.stdout.close()

    def _ensure_started(self):
        with self.start_lock:
            config = self.config()
            if self._closed or not config['enabled']:
                raise UserError('平台临时参考图通道未启用；未提交生图')
            binary = self._binary()
            if binary is None:
                raise UserError('服务器尚未安装 cloudflared；未提交生图')
            with self.lock:
                if (self._process is not None and self._process.poll() is None
                        and self._hostname and self._connected):
                    return
            self._stop()
            server = ThreadingHTTPServer(('127.0.0.1', 0), self._handler())
            server.daemon_threads = True
            server_thread = threading.Thread(target=server.serve_forever,
                                             kwargs={'poll_interval': 0.2}, daemon=True,
                                             name='spine-reference-images')
            with self.lock:
                self._server = server
                if self._cleanup_thread is None:
                    self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True,
                                                            name='spine-reference-expiry')
                    self._cleanup_thread.start()
            server_thread.start()
            try:
                process = subprocess.Popen(
                    [str(binary), 'tunnel', '--config', '/dev/null',
                     '--url', 'http://127.0.0.1:' + str(server.server_port),
                     '--no-autoupdate', '--protocol', 'http2'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', bufsize=1,
                )
            except (OSError, ValueError):
                self._stop()
                raise UserError('临时 HTTPS 通道无法启动；未提交生图，请管理员检查服务器运行环境') from None
            ready_event = threading.Event()
            with self.lock:
                self._process = process
                self._hostname = None
                self._connected = False
                self._logs.clear()
            threading.Thread(target=self._drain, args=(process, ready_event), daemon=True,
                             name='spine-reference-tunnel-log').start()
            ready_event.wait(STARTUP_TIMEOUT)
            with self.lock:
                ready = process.poll() is None and self._hostname is not None and self._connected
            if not ready:
                self._stop()
                raise UserError('临时 HTTPS 通道未在 35 秒内连通；未提交生图，请检查服务器外网连接')

    def publish(self, data, image_id):
        if (not isinstance(data, bytes) or not isinstance(image_id, str)
                or not re.fullmatch(r'[a-f0-9]{64}', image_id)
                or hashlib.sha256(data).hexdigest() != image_id):
            raise UserError('自动上传原图校验失败，未提交生成')
        if len(data) > MAX_IMAGE_BYTES:
            raise UserError('自动上传参考图超过 30 MB')
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != 'PNG' or image.width * image.height > MAX_PIXELS:
                    raise ValueError('invalid png')
                image.verify()
        except Exception:
            raise UserError('临时参考图必须是有效 PNG 图片，未提交生成') from None
        with self.start_lock:
            self._ensure_started()
            with self.lock:
                now = time.time()
                self._cleanup_locked(now)
                token = self._by_image.get(image_id)
                item = self._objects.get(token)
                ttl = self.config()['url_ttl']
                from .reference_policy import publication_authorization
                authorization = publication_authorization(self.path.parent)
                if authorization['authorized']:
                    ttl = min(ttl, authorization['max_url_ttl'])
                # A cache refresh must produce a link with enough lifetime for
                # queued generation. Keep the old token until its own expiry so
                # requests already submitted can still retrieve their input.
                refresh_margin = max(60, ttl / 4)
                if item is None or item['expires_at'] <= now + refresh_margin:
                    if (len(self._objects) >= MAX_REFERENCES
                            or sum(len(item['data']) for item in self._objects.values()) + len(data) > MAX_PUBLISHED_BYTES):
                        raise UserError('临时参考图数量已达上限，请等待旧链接过期后再提交')
                    token = secrets.token_hex(32)
                    item = {'data': data, 'image_id': image_id,
                            'expires_at': now + ttl}
                    self._objects[token] = item
                    self._by_image[image_id] = token
                return {'url': 'https://' + self._hostname + '/' + token + '.png',
                        'expires_at': item['expires_at'], 'provider': 'quick-tunnel'}

    def active_reference(self, url):
        if not isinstance(url, str):
            return False
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return False
        match = REFERENCE_PATH.fullmatch(parsed.path)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query
                or parsed.fragment or match is None):
            return False
        with self.lock:
            self._cleanup_locked(time.time())
            return bool(not self._closed and self._process is not None
                        and self._process.poll() is None and self._hostname
                        and self._connected
                        and parsed.netloc == self._hostname and match.group(1) in self._objects)

    def _stop(self):
        with self.lock:
            process, server = self._process, self._server
            self._process = self._server = self._hostname = None
            self._connected = False
            self._objects.clear()
            self._by_image.clear()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        if server is not None:
            server.shutdown()
            server.server_close()

    def close(self):
        with self.start_lock:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            self._stop()
