"""Keep worker tool isolation while retaining the operator's inference route.

Only model/provider settings are selected; never inherit user MCPs, hooks or
permissions. Inline provider credentials travel in child environment, not argv.
"""
import os,re
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path
from urllib.parse import urlsplit
from .core import UserError

MODEL_FIELDS=('model','model_provider','model_reasoning_effort','model_reasoning_summary','model_verbosity')
PROVIDER_FIELDS=('name','base_url','wire_api','env_key','requires_openai_auth',
                 'request_max_retries','stream_max_retries','stream_idle_timeout_ms',
                 'supports_websockets')


def model_configuration(environ=None):
    environ=os.environ if environ is None else environ
    home=Path(environ.get('CODEX_HOME') or Path.home()/'.codex')
    path=home/'config.toml'
    if not path.exists():return {},{}
    try:
        config=tomllib.loads(path.read_text())
        profile=config.get('profile')
        if profile:
            if not isinstance(profile,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',profile):raise UserError('后台模型配置的 profile 名称无效')
            file=home/(profile+'.config.toml')
            selected=tomllib.loads(file.read_text()) if file.exists() else config.get('profiles',{}).get(profile)
            if not isinstance(selected,dict):raise UserError('后台模型配置的 profile 不存在')
            config={**config,**selected,'model_providers':{**config.get('model_providers',{}),**selected.get('model_providers',{})}}
    except (OSError,tomllib.TOMLDecodeError):
        raise UserError('无法读取后台模型配置，请检查 Codex config.toml；未回退到其他服务') from None
    overrides={k:config[k] for k in MODEL_FIELDS if k in config};env={}
    provider=config.get('model_provider','openai')
    if not isinstance(provider,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',provider):raise UserError('后台模型服务名称无效')
    definition=config.get('model_providers',{}).get(provider)
    if definition is None:
        if provider not in ('openai','ollama','lmstudio'):raise UserError('后台模型服务缺少定义，未回退到默认服务')
        return overrides,env
    prefix='model_providers.'+provider+'.'
    for key in PROVIDER_FIELDS:
        if key in definition:overrides[prefix+key]=definition[key]
    url=urlsplit(definition.get('base_url',''))
    if url.username or url.password or url.query or url.fragment:
        raise UserError('后台模型地址含内嵌认证或查询参数，请改用服务的环境变量认证')
    for header,variable in definition.get('env_http_headers',{}).items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+',header):raise UserError('后台模型 HTTP 头名称无效')
        overrides[prefix+'env_http_headers.'+header]=variable
    for i,(header,value) in enumerate(definition.get('http_headers',{}).items()):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',header):raise UserError('后台模型 HTTP 头名称无效')
        variable='SPINE_WORKER_PROVIDER_HEADER_'+str(i)
        env[variable]=str(value);overrides[prefix+'env_http_headers.'+header]=variable
    if definition.get('experimental_bearer_token'):
        variable='SPINE_WORKER_PROVIDER_API_KEY'
        env[variable]=definition['experimental_bearer_token'];overrides[prefix+'env_key']=variable
    return overrides,env
