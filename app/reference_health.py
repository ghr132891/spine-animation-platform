"""Bounded, credential-free diagnostics for reference publication and readback."""
import json
import socket
import ssl
import time
import urllib.error
from pathlib import Path
from .core import atomic_json


def network_error(error):
    # Never persist exception text: it can contain signed URLs and credentials.
    status = error.code if isinstance(error, urllib.error.HTTPError) else None
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if status is not None:
        code, message = 'http_error', 'HTTP ' + str(status)
        retryable = status in (404, 408, 425, 429) or status >= 500
    elif isinstance(reason, socket.gaierror):
        code, message, retryable = 'dns_error', 'DNS 解析失败', True
    elif isinstance(reason, ssl.SSLError):
        code, message, retryable = 'tls_error', 'TLS 证书或握手失败', False
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        code, message, retryable = 'timeout', '网络连接或读取超时', True
    elif isinstance(reason, ConnectionRefusedError):
        code, message, retryable = 'connection_refused', '连接被拒绝', True
    elif isinstance(reason, ConnectionResetError):
        code, message, retryable = 'connection_reset', '连接被重置', True
    else:
        code, message, retryable = 'network_error', '网络连接失败', True
    return {'code': code, 'message': message, 'http_status': status, 'retryable': retryable}


def record(storage, provider, stage, image_id, attempt, error=None):
    path = Path(storage) / 'reference-health.json'
    try: previous = json.loads(path.read_text())
    except (OSError, ValueError): previous = {}
    event = {'provider': provider, 'stage': stage, 'image_id': image_id,
             'attempt': attempt, 'checked_at': time.time(), 'ok': error is None}
    if error: event['error'] = error
    atomic_json(path, {**event, 'events': (previous.get('events', []) + [event])[-40:]})
    return event


def status(storage, provider):
    try: value = json.loads((Path(storage) / 'reference-health.json').read_text())
    except (OSError, ValueError): value = {}
    if value.get('provider') != provider:
        return {'state': 'unchecked', 'usable': False, 'message': '尚未执行公网图片回读检查'}
    fresh = time.time() - value.get('checked_at', 0) < 300
    return {**value, 'state': ('healthy' if value.get('ok') else 'unavailable') if fresh else 'stale',
            'usable': bool(value.get('ok') and fresh),
            'message': ('图片发布及像素回读校验通过（5分钟内）' if value.get('ok') else
                        '参考图通道检查失败：' + value.get('error', {}).get('message', '未知错误')) if fresh else '上次检查已过期，需重新检测'}
