"""Server-owned S3 reference publication. No browser or generation credentials."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .core import UserError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_endpoint(value):
    parsed = urllib.parse.urlsplit(str(value).strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or
            parsed.hostname.lower() == 'localhost'):
        raise UserError('图片存储端点必须是公网 HTTPS 地址，不包含账号、查询参数或片段')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise UserError('图片存储不能使用本机或局域网地址，亿创需要从外网读取')
    try:
        parsed.port
    except ValueError:
        raise UserError('图片存储端点端口无效') from None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))


def validated(config):
    if not isinstance(config, dict) or config.get('provider', 's3') != 's3':
        raise UserError('请配置 S3 兼容的图片存储')
    result = {'provider': 's3', 'endpoint': public_endpoint(config.get('endpoint', ''))}
    for field in ('bucket', 'access_key_id', 'secret_access_key'):
        if not isinstance(config.get(field),str):
            raise UserError('请填写完整的图片存储桶名和访问密钥')
        value = config[field].strip()
        if not value or len(value) > 2048 or any(c.isspace() for c in value):
            raise UserError('请填写完整的图片存储桶名和访问密钥')
        result[field] = value
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{1,62}', result['bucket']):
        raise UserError('图片存储桶名无效')
    if not isinstance(config.get('region','us-east-1'),str):raise UserError('图片存储区域无效')
    region = config.get('region', 'us-east-1').strip()
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', region):
        raise UserError('图片存储区域无效')
    result['region'] = region
    if not isinstance(config.get('prefix','spine-references'),str):raise UserError('图片存储目录无效')
    prefix = config.get('prefix', 'spine-references').strip('/')
    if (len(prefix) > 200 or not prefix or
            any(p in ('', '.', '..') for p in prefix.split('/')) or
            not re.fullmatch(r'[A-Za-z0-9_./-]+', prefix)):
        raise UserError('图片存储目录无效，请使用字母、数字、连字符和斜线')
    result['prefix'] = prefix
    style = config.get('addressing_style', 'path')
    if style not in ('path', 'virtual'):
        raise UserError('图片存储寻址方式无效')
    if style == 'virtual' and (address_is_ip(result['endpoint']) or
                              not re.fullmatch(r'[a-z0-9][a-z0-9.-]+', result['bucket'])):
        raise UserError('虚拟主机方式需要域名端点与小写存储桶名')
    result['addressing_style'] = style
    ttl = config.get('url_ttl', 86400)
    if type(ttl) != int or not 3600 <= ttl <= 604800:
        raise UserError('图片链接有效期须为 3600–604800 秒')
    result['url_ttl'] = ttl
    if not isinstance(config.get('session_token',''),str):raise UserError('图片存储临时令牌格式无效')
    token = config.get('session_token', '').strip()
    if token:
        if len(token) > 8192 or any(c.isspace() for c in token):
            raise UserError('图片存储临时令牌格式无效')
        result['session_token'] = token
    return result


def address_is_ip(endpoint):
    try:
        ipaddress.ip_address(urllib.parse.urlsplit(endpoint).hostname)
        return True
    except ValueError:
        return False


def digest(data):
    return hashlib.sha256(data).hexdigest()


def signing_key(secret, date, region):
    def sign(key, value):
        return hmac.new(key, value.encode(), hashlib.sha256).digest()
    return sign(sign(sign(sign(('AWS4' + secret).encode(), date), region), 's3'), 'aws4_request')


def quote(value):
    return urllib.parse.quote(str(value), safe='-_.~')


def object_url(config, image_id):
    parsed = urllib.parse.urlsplit(config['endpoint'])
    key = config['prefix'] + '/' + image_id + '.png'
    if config['addressing_style'] == 'virtual':
        netloc = config['bucket'] + '.' + parsed.netloc
        path = parsed.path + '/' + key
    else:
        netloc = parsed.netloc
        path = parsed.path + '/' + config['bucket'] + '/' + key
    # Preserve object key slashes; S3 does not normalize repeated path components.
    path = urllib.parse.quote(urllib.parse.unquote(path), safe='/-_.~')
    return urllib.parse.urlunsplit(('https', netloc, path, '', ''))


def signed_put(config, url, data, now):
    parsed = urllib.parse.urlsplit(url)
    stamp = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    scope = stamp[:8] + '/' + config['region'] + '/s3/aws4_request'
    headers = {'content-type': 'image/png', 'host': parsed.netloc,
               'x-amz-content-sha256': digest(data), 'x-amz-date': stamp}
    if config.get('session_token'):
        headers['x-amz-security-token'] = config['session_token']
    names = ';'.join(sorted(headers))
    canonical_headers = ''.join(k + ':' + headers[k] + '\n' for k in sorted(headers))
    canonical = '\n'.join(('PUT', parsed.path, '', canonical_headers, names, digest(data)))
    to_sign = '\n'.join(('AWS4-HMAC-SHA256', stamp, scope, digest(canonical.encode())))
    signature = hmac.new(signing_key(config['secret_access_key'], stamp[:8], config['region']), to_sign.encode(), hashlib.sha256).hexdigest()
    headers['authorization'] = ('AWS4-HMAC-SHA256 Credential=' + config['access_key_id'] + '/' + scope +
                                ', SignedHeaders=' + names + ', Signature=' + signature)
    return headers


def signed_get(config, url, now):
    parsed = urllib.parse.urlsplit(url)
    stamp = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    scope = stamp[:8] + '/' + config['region'] + '/s3/aws4_request'
    args = {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
            'X-Amz-Credential': config['access_key_id'] + '/' + scope,
            'X-Amz-Date': stamp, 'X-Amz-Expires': str(config['url_ttl']),
            'X-Amz-SignedHeaders': 'host'}
    if config.get('session_token'):
        args['X-Amz-Security-Token'] = config['session_token']
    query = '&'.join(quote(k) + '=' + quote(v) for k, v in sorted(args.items()))
    canonical = '\n'.join(('GET', parsed.path, query, 'host:' + parsed.netloc + '\n', 'host', 'UNSIGNED-PAYLOAD'))
    to_sign = '\n'.join(('AWS4-HMAC-SHA256', stamp, scope, digest(canonical.encode())))
    signature = hmac.new(signing_key(config['secret_access_key'], stamp[:8], config['region']), to_sign.encode(), hashlib.sha256).hexdigest()
    return url + '?' + query + '&X-Amz-Signature=' + signature


class ReferenceHosting:
    def __init__(self, storage):
        self.path = Path(storage) / 'reference-hosting.json'
        self.lock = threading.RLock()

    def config(self):
        with self.lock:
            if not self.path.exists():
                raise UserError('平台尚未配置自动参考图存储，请平台管理员在服务器本机设置页配置一次；使用者无需登录亿创或提供链接')
            try:
                record=json.loads(self.path.read_text())
                config=validated(record)
                stamp=record.get('verified_at')
                if type(stamp) in (int,float) and math.isfinite(stamp) and 0<stamp<=time.time()+300:
                    config['verified_at']=stamp
                return config
            except (ValueError, OSError):
                raise UserError('平台参考图存储配置无效，请管理员检查；使用者无需提供存储密钥') from None

    def status(self):
        try:
            config=self.config()
        except UserError as error:
            return {'provider': 's3', 'configured': False, 'ready': False, 'message': str(error)}
        verified=bool(config.get('verified_at'))
        return {'provider': 's3', 'configured': True, 'ready': verified,
                'message': '平台已验证服务端自动上传，使用者无需登录亿创或复制参考图链接' if verified else '图片存储已配置，尚需在服务器本机设置页验证连接'}

    def save(self, config,verified=False):
        config = validated(config)
        if verified:config['verified_at']=time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            temporary = self.path.with_suffix('.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, 'w') as target:
                    json.dump(config, target)
                os.chmod(temporary, 0o600)
                temporary.replace(self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return self.status()

    def publish(self, data, image_id,config=None):
        if not re.fullmatch('[a-f0-9]{64}', image_id) or digest(data) != image_id:
            raise UserError('自动上传原图校验失败，未提交生成')
        if len(data) > 30 * 1024 * 1024:
            raise UserError('自动上传参考图超过 30 MB')
        config = self.config() if config is None else validated(config)
        url = object_url(config, image_id)
        now = time.time()
        request = urllib.request.Request(url, data=data, method='PUT', headers=signed_put(config, url, data, now))
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
                if not 200 <= response.status < 300:
                    raise UserError('平台图片存储未接受上传，请管理员检查配置')
        except urllib.error.HTTPError as error:
            raise UserError('平台图片存储上传返回 HTTP ' + str(error.code) + '，请管理员检查桶名、端点和读写权限') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise UserError('平台图片存储上传连接失败；未提交生图，请管理员检查网络和存储配置') from None
        return {'url': signed_get(config, url, now), 'expires_at': now + config['url_ttl'], 'provider': 's3'}
