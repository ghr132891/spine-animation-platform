"""Matrix adapter. Secrets are never returned to the browser or log output."""
from __future__ import annotations
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from .core import UserError

DEFAULT_MODEL = "gpt-image-2.5-flare"
MODEL_NAME = "GPT Image 2.5 Flare"
LEGACY_MODEL = "nano-banana-2"

BASE = "https://api-matrix.tuyoo.com/api/matrix/aimake/v1"


def _failure_tokens(payload):
    """Inspect reason fields only; never return upstream text, URLs or prompts."""
    import re
    keys={'error','errorcode','failurecode','finishreason','reason','message','msg','code',
          'blockreason','blockedreason','status','itemstatus','errormessage','failurereason'}
    ignored={'input','prompt','inputimages','imageurl','imageurls'}
    tokens=[]
    def token(value):
        text=str(value)[:4096]
        return re.sub(r'https?://\S+|data:[^\s]+','',text,flags=re.I)
    def visit(value,depth=0,reason=False):
        if depth>6 or len(tokens)>128:return
        if isinstance(value,dict):
            for key,item in list(value.items())[:128]:
                normalized=re.sub(r'[^a-z]','',str(key).casefold())
                if normalized in ignored:continue
                if isinstance(item,(dict,list)):visit(item,depth+1,normalized in keys)
                elif normalized in keys and isinstance(item,(str,int)):tokens.append(token(item))
        elif isinstance(value,list):
            for item in value[:128]:visit(item,depth+1,reason)
        elif reason and isinstance(value,str):tokens.append(token(value))
    visit(payload)
    return ' '.join(tokens).casefold()


def classify_provider_failure(payload=None, *, http_status=None, operation='query', category=None):
    """Safe display + retry contract. New generation submissions are never automatic."""
    import re
    tokens=_failure_tokens(payload or {})
    if 'image_recitation' in tokens or '内容涉嫌抄袭' in tokens or 'image recitation' in tokens:
        category='image_recitation'
    elif (re.search(r'image[_ -]safety|content[_ -]policy[_ -]violation|prohibited_content|blocked_content|内容审核|安全审核|敏感内容|违禁|内容不合规',tokens) or
          ((http_status!=403 or not re.search(r'permission|access|credential|权限|凭据',tokens)) and
           re.search(r'content[_ -]policy|moderation|\bsafety\b|content[_ -]filter|\bblocked\b|\bblocklist\b',tokens))):
        category='content_review'
    elif category is None:
        code=(payload or {}).get('code') if isinstance(payload,dict) else None
        status=http_status if http_status is not None else code if type(code) is int else None
        if status in (401,403):category='credentials'
        elif status==429 or re.search(r'rate[_ -]limit|too[_ -]many[_ -]requests|resource_exhausted|请求过于频繁',tokens):category='rate_limit'
        elif status in (400,413,422):category='invalid_request'
        elif http_status is not None and http_status>=500:category='service_unavailable'
        else:category='generation_failed'
    messages={
        'image_recitation':('生成被图像复述审核拦截，本次没有图片结果。','停止自动重试；请核实参考素材权利与原创需求，不自动改写、换图或切换模型。'),
        'content_review':('生成被内容审核拦截，本次没有图片结果。','停止自动重试；请查看生图服务审核说明并重新确认合规需求，不自动改写、换图或切换模型。'),
        'credentials':('生图服务凭据无效、过期或权限不足。','请管理员检查生图服务 API Key 与模型权限。'),
        'rate_limit':('生图服务请求过于频繁。','已有生成编号时稍后继续查询同一任务；新提交须先核实未创建任务。'),
        'invalid_request':('生图服务未接受图片请求参数。','检查格式、尺寸和提示词长度后再提交；不以参数修正规避内容审核。'),
        'service_unavailable':('生图服务服务暂时不可用。','已有生成编号时继续查询同一任务；提交状态不明时先在生图服务核实，避免重复计费。'),
        'submission_unknown':('生图服务提交结果不明，尚不能确认是否创建了生成任务。','先核实生图服务任务列表；不要再次提交或切换模型，避免重复计费。'),
        'connection_failed':('生图服务连接失败或响应无效。','保留任务编号，稍后继续查询同一任务。'),
        'download_failed':('生成图片下载暂时失败。','稍后重新查询并下载同一任务结果，无需重新生图。'),
        'reference_unavailable':('参考图链接发布或回读失败，本次尚未提交生图。','先恢复参考图通道并执行 reference ensure，再沿用同一生成请求编号；不要把它当作生成结果下载失败。'),
        'empty_output':('生图服务任务已结束，但没有成功的图片输出。','保留原任务编号并核实生图服务失败原因；不自动重新提交。'),
        'generation_failed':('生图服务本次生成未完成。','保留原任务编号并核实生图服务失败原因；不自动重新提交。'),
    }
    if category not in messages:category='generation_failed'
    message,action=messages[category]
    is_submission=operation=='submit'
    outcome=('not-submitted' if category=='reference_unavailable' else 'unknown' if is_submission and category in ('submission_unknown','connection_failed','service_unavailable','generation_failed') else
             'rejected' if is_submission else 'existing-task')
    same_task=not is_submission and category in ('rate_limit','service_unavailable','connection_failed','download_failed')
    return {'category':category,'message':message,'next_action':action,'operation':operation,
            'submission_state':outcome,'auto_retry':False,'retry_same_task':same_task,'retry_reference':category=='reference_unavailable',
            'new_submission_allowed':False,'rewrite_retry_allowed':False,
            'stop_generation':category in ('image_recitation','content_review'),
            'http_status':http_status if isinstance(http_status,int) else None}


class ProviderError(UserError):
    def __init__(self,details,terminal_status=None):
        self.details=details
        self.terminal_status=terminal_status if terminal_status in ('failed','submission_unknown') else None
        super().__init__(details['message']+' '+details['next_action'])


def failure_details(error, *, operation='query'):
    """Only ProviderError carries trusted classification; other errors stay generic."""
    if isinstance(error,ProviderError):return dict(error.details)
    return classify_provider_failure(operation=operation,
                                     category='submission_unknown' if operation=='submit' else 'connection_failed')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Matrix:
    def __init__(self, storage):
        self.path = Path(storage) / "credentials.json"
        self.references = Path(storage) / "reference-images"
        from .reference_hosting import ReferenceHosting
        self.hosting = ReferenceHosting(storage)
        from .reference_tunnel import ReferenceTunnel
        self.tunnel = ReferenceTunnel(storage)
        self.lock = threading.RLock()
        self.last = {"state": "unchecked", "message": "尚未验证亿创连接"}

    def credential(self):
        if self.path.exists():
            return json.loads(self.path.read_text()).get("key", ""), "本工具本地配置"
        key = os.getenv("YC_API_KEY") or os.getenv("API_MATRIX_TOKEN")
        if key:
            return key, "环境变量"
        old = Path.home() / ".config/api-matrix/config.json"
        if old.exists():
            return json.loads(old.read_text()).get("token", ""), "已有 Matrix 配置"
        return "", "未配置"

    def status(self):
        key, source = self.credential()
        return {**self.last, "model": DEFAULT_MODEL, "model_name": MODEL_NAME, "reference_transport": "https-url", "configured": bool(key), "source": source, "kind": "API Key" if key.startswith("sk-") else "登录 Token" if key else None}

    def request(self, route, data=None, key=None):
        operation='submit' if route.endswith('/generate') else 'query'
        if key is None:
            key, _ = self.credential()
        if not key:
            raise ProviderError(classify_provider_failure(operation=operation,category='credentials'))
        headers = {"Accept": "application/json"}
        headers["X-API-KEY" if key.startswith("sk-") else "Authorization"] = key if key.startswith("sk-") else "Bearer "+key
        if data is not None:
            headers["Content-Type"] = "application/json"
        url = 'https://api-matrix.tuyoo.com'+route if route.startswith('/api/matrix/') else BASE+route
        req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(), headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as e:
            # Do not include arbitrary upstream bodies, which could reflect credentials.
            # Structured reason fields can identify a review rejection, but no body is logged.
            try:
                upstream=json.loads(e.read(65537))
                if not isinstance(upstream,dict):upstream={}
            except (ValueError,OSError):upstream={}
            raise ProviderError(classify_provider_failure(upstream,http_status=e.code,operation=operation)) from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise ProviderError(classify_provider_failure(operation=operation,category='submission_unknown' if operation=='submit' else 'connection_failed')) from None
        if not isinstance(result,dict):
            raise ProviderError(classify_provider_failure(operation=operation,category='submission_unknown' if operation=='submit' else 'connection_failed'))
        if result.get("code", 0) != 0:
            raise ProviderError(classify_provider_failure(result,operation=operation))
        return result

    def connect(self, key=""):
        key = key.strip()
        if key and (len(key) < 12 or len(key) > 8192 or any(c.isspace() for c in key)):
            raise UserError("凭据格式不正确，请粘贴完整 API Key 或 Token")
        try:
            response = self.request("/models", key=key or None)
        except UserError as e:
            self.last = {"state":"error","message":str(e)}
            raise
        if key:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            with self.lock:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump({"key":key}, f)
                os.chmod(temporary, 0o600)
                temporary.replace(self.path)
        self.last = {"state":"connected","message":"亿创凭据验证通过，已连接"}
        return self.status()

    @staticmethod
    def generation_input(prompt, reference_url=None, width=512, height=512):
        ratios=['1:1','2:3','3:2','3:4','4:3','9:16','16:9','21:9']
        def distance(r):
            a,b=map(int,r.split(':'));return abs(a/b-width/height)
        ratio=min(ratios,key=distance)
        from .part_prompts import prompt_language
        if prompt_language(prompt)=='zh':
            full_prompt='重绘这张独立动画素材切片。保持原姿态、轮廓、连接位置、朝向、比例与画布布局，仅调整配色、材质和表面细节。纯色背景。目标风格：'+prompt
        else:
            full_prompt='Redraw this isolated animation asset cut-out. Preserve its pose, silhouette, mounting positions, orientation, scale and canvas placement. Change only colors, materials and surface details. Plain background. Target style: '+prompt
        inputs={"prompt":full_prompt,"aspect_ratio":ratio,"resolution":"1K","quality":"high","output_format":"png","background":"opaque"}
        if reference_url:
            parsed=urllib.parse.urlparse(reference_url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.hostname in ('localhost','127.0.0.1'):
                raise UserError('参考图重绘需要亿创可访问的 HTTPS 图片 URL')
            inputs['input_images']=[Matrix.reference_url(reference_url)]
        else:
            inputs['prompt']=('方形无缝材质样张。' if prompt_language(prompt)=='zh' else 'Square seamless material swatch. ')+prompt
        return inputs

    def generate(self, prompt, reference_url=None, width=512, height=512):
        inputs=self.generation_input(prompt,reference_url,width,height)
        result = self.request('/models/'+DEFAULT_MODEL+'/generate', {"input":inputs,"request_num":1})
        return self.generation_id(result)

    @staticmethod
    def generation_id(result):
        data=result.get('data') if isinstance(result,dict) else None
        gen_id=data.get('gen_id') if isinstance(data,dict) else None
        if not isinstance(gen_id,(str,int)) or isinstance(gen_id,bool) or not str(gen_id).strip() or len(str(gen_id))>512:
            raise ProviderError(classify_provider_failure(result,operation='submit',category='submission_unknown'))
        return str(gen_id)

    def generate_character(self, prompt):
        from .part_prompts import character_prompt
        result=self.request('/models/'+DEFAULT_MODEL+'/generate',{'input':{'prompt':character_prompt(prompt),'aspect_ratio':'3:4','resolution':'1K','quality':'high','output_format':'png','background':'opaque'},'request_num':1})
        return self.generation_id(result)

    def cached_reference(self,image):
        from .core import png_bytes,sha
        image_id=sha(png_bytes(image.convert('RGBA')))
        path=self.references/(image_id+'.json')
        with self.lock:
            if not path.exists():return None
            try:
                record=json.loads(path.read_text())
                if not isinstance(record,dict):return None
                if record.get('image_id')!=image_id:return None
                if record.get('provider') == 'quick-tunnel' and not self.tunnel.active_reference(record.get('url')):
                    return None
                import time
                if 'expires_at' in record:
                    import math
                    if type(record['expires_at']) not in (int,float) or not math.isfinite(record['expires_at']):return None
                    stamp=record.get('verified_at',0)
                    if type(stamp) not in (int,float) or not math.isfinite(stamp):return None
                    remaining_margin=min(3600,max(60,(record['expires_at']-record.get('verified_at',0))/4))
                    if record['expires_at'] <= time.time()+remaining_margin:return None
                self.reference_url(record['url'])
                return record
            except (ValueError,KeyError,OSError,TypeError):return None

    def register_reference(self,image,url):
        from .core import png_bytes,read_image,sha,atomic_json
        import time
        url=self.reference_url(url)
        image_id=sha(png_bytes(image.convert('RGBA')))
        downloaded=self.download_result(url)
        if sha(png_bytes(read_image(downloaded)))!=image_id:
            raise UserError('参考图链接与当前原图像素不一致，未关联；请上传本次准备的原图，不使用旧图或缩略图')
        record={'image_id':image_id,'url':url,'verified_at':time.time()}
        with self.lock:atomic_json(self.references/(image_id+'.json'),record)
        return record

    def image_reference(self,image):
        record=self.cached_reference(image)
        if record:return record['url']
        raise UserError('当前原图尚未关联云端参考图；spine_reference ensure 或补图生成会由平台自动发布。管理员可启用临时 HTTPS 通道或配置存储，使用者无需登录亿创或复制链接。旧版仍可使用 spine_reference prepare。')

    def ensure_reference(self,image):
        from .core import png_bytes,read_image,sha,atomic_json
        import time
        # Shared across characters: concurrent requests for the same pixels publish once.
        with self.lock:
            cached=self.cached_reference(image)
            if cached:return cached
            data=png_bytes(image.convert('RGBA'));image_id=sha(data)
            publisher = self.hosting if self.hosting.status()['ready'] else self.tunnel
            published=publisher.publish(data,image_id)
            url=self.reference_url(published['url'])
            try:downloaded=self.download_result(url,timeout=20)
            except ProviderError:
                raise ProviderError(classify_provider_failure(category='reference_unavailable',operation='reference')) from None
            if sha(png_bytes(read_image(downloaded)))!=image_id:
                raise UserError('平台自动发布的参考图像素校验失败，未关联或提交生图；请管理员检查图片通道')
            record={'image_id':image_id,'url':url,'verified_at':time.time(),
                    'expires_at':published['expires_at'],'provider':published['provider']}
            atomic_json(self.references/(image_id+'.json'),record)
            return record

    def reference_status(self):
        from .reference_policy import publication_authorization
        storage = self.hosting.status()
        status = dict(storage if storage['ready'] else self.tunnel.status())
        status['publication_authorization'] = publication_authorization(self.path.parent)
        return status

    def configure_reference_hosting(self,config):
        from .reference_hosting import validated
        from .core import png_bytes,read_image,sha
        from PIL import Image
        config=validated(config)
        data=png_bytes(Image.new('RGBA',(2,2),(39,99,151,255)))
        published=self.hosting.publish(data,sha(data),config=config)
        if sha(png_bytes(read_image(self.download_result(published['url'],timeout=20))))!=sha(data):
            raise UserError('图片存储回读校验失败，原有配置已保留')
        return self.hosting.save(config,verified=True)

    @staticmethod
    def reference_url(reference):
        import ipaddress
        parsed=urllib.parse.urlparse(str(reference))
        if parsed.scheme!='https' or not parsed.hostname or parsed.hostname=='localhost' or parsed.username or parsed.password:
            raise UserError('GPT Image 2.5 Flare 的参考图必须是可访问的 HTTPS 图片链接，不支持 Base64 或本地地址')
        try:address=ipaddress.ip_address(parsed.hostname)
        except ValueError:address=None
        if address is not None and not address.is_global:
            raise UserError('参考图不能使用局域网或本机地址')
        return str(reference)

    def generate_view(self,prompt,reference):
        result=self.request('/models/'+DEFAULT_MODEL+'/generate',{'input':{'prompt':prompt,'aspect_ratio':'3:4','resolution':'1K','quality':'high','output_format':'png','background':'opaque','input_images':[self.reference_url(reference)]},'request_num':1})
        return self.generation_id(result)

    def job_metadata(self, gen_id):
        return {"provider": "matrix", "model": DEFAULT_MODEL}

    def poll(self, gen_id, model=DEFAULT_MODEL):
        if model not in (DEFAULT_MODEL, LEGACY_MODEL, 'gpt-image-2'):
            raise UserError('任务记录中的生图模型不受支持')
        return self.request('/models/'+model+'/'+urllib.parse.quote(gen_id,safe='')+'/status')

    def generate_part_sheet(self,sheet,reference_url,instructions='',facing='right',include_back=True,body_layout='separate'):
        from .part_prompts import part_prompt,sheet_spec
        spec=sheet_spec(sheet,include_back,body_layout);reference_url=self.reference_url(reference_url)
        result=self.request('/models/'+DEFAULT_MODEL+'/generate',{'input':{'prompt':part_prompt(sheet,instructions,facing,include_back,body_layout),'aspect_ratio':spec['ratio'],'resolution':'1K','quality':'high','output_format':'png','background':'opaque','input_images':[reference_url]},'request_num':1})
        return self.generation_id(result)

    def download_result(self, url,timeout=60):
        from .core import read_image
        import ipaddress
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.hostname=='localhost':
            raise UserError('亿创输出不是可下载的 HTTPS 图片')
        try:
            if ipaddress.ip_address(parsed.hostname).is_private:raise UserError('输出图片地址无效')
        except ValueError:pass
        try:
            with urllib.request.build_opener(NoRedirect).open(url,timeout=timeout) as r:
                data=r.read(30*1024*1024+1)
                if len(data)>30*1024*1024:raise UserError('生成图片超过 30 MB')
                read_image(data)
                return data
        except (urllib.error.URLError,TimeoutError):
            raise ProviderError(classify_provider_failure(category='download_failed')) from None
