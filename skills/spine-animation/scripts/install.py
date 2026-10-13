#!/usr/bin/env python3
"""Install the conversational skill and register its stdio MCP connection."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from spine_mcp import Bridge

FILES=('SKILL.md','agents/openai.yaml','references/platform-review.md','references/turnaround-review.md','references/seam-review.md','references/rig-refinement.md','references/image-preparation.md','scripts/install.py','scripts/doctor.py','scripts/spine_mcp.py','scripts/spine_workflow.py')


def discover_codex():
    found=shutil.which('codex')
    if found:return found
    for root in (Path('/Applications'),Path.home()/'Applications'):
        candidate=root/'ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
        if candidate.is_file():return str(candidate)
    return None


def probe(base,token=''):
    if not isinstance(token,str) or len(token)>8192:raise ValueError('可选访问令牌格式无效')
    bridge=Bridge(base,token);resources=bridge.call('spine_sources',{})
    api=bridge.api('/api/automation/openapi.json')
    if '/api/automation/agent/refine' not in api.get('paths',{}):raise ValueError('平台版本不支持连续微调，请让管理员更新服务')
    try:
        refine=api['paths']['/api/automation/agent/refine']['post']['requestBody']['content']['application/json']['schema']['properties']
        result=api['paths']['/api/automation/agent/tasks/{task_id}/result-view']['post']['requestBody']['content']['application/json']['schema']['properties']
        capable='part_images' in refine and 'frame_count' in result and {'gait','contact','closeup'}.issubset(result['view']['enum'])
    except (KeyError,TypeError):capable=False
    capable=capable and 'image_preparation' in api.get('components',{}).get('schemas',{}).get('AgentRequest',{}).get('properties',{}) and 'codex-image-preflight' in resources.get('capabilities',[])
    capable=capable and ('configurable-image-provider' in resources.get('capabilities',[]) or ('matrix-gpt-image-2.5-flare' in resources.get('capabilities',[]) and 'matrix-reference-cache' in resources.get('capabilities',[])))
    capable=capable and 'server-reference-upload' in resources.get('capabilities',[])
    capable=capable and {'runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-contact-diagnostics','smooth-gait-curves'}.issubset(resources.get('capabilities',[]))
    capable=capable and 'character-asset-sheet-first' in resources.get('capabilities',[]) and 'side-walk-preparation' in resources.get('capabilities',[]) and 'joint-reviewed-assembly' in resources.get('capabilities',[])
    capable=capable and {'neck-seam-closeups','scoped-texture-repair','scoped-material-finish','contrast-material-review','turnaround-reference-review','multi-region-closeups'}.issubset(resources.get('capabilities',[]))
    capable=capable and {'asset-integrity-review','costume-completion','strict-layer-anchors'}.issubset(resources.get('capabilities',[]))
    capable=capable and {'durable-task-resume','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling'}.issubset(resources.get('capabilities',[]))
    capable=capable and {'platform-review-bundle','scoped-edge-cleanup'}.issubset(resources.get('capabilities',[]))
    if not capable:raise ValueError('此 Skill 需要支持完整角色素材图优先、真实鞋底接地诊断、平滑步态、显式部件布局、连续肢体蒙皮与自动参考图及局部接缝放大修复及衣料完整性验收的1.10.0或更新平台，请让管理员更新服务')
    return {'base':bridge.base,'platform_version':api.get('info',{}).get('version'),'authenticated':bool(token),'access_mode':resources.get('access',{}).get('mode','token' if token else 'open'),'available_characters':len(resources.get('characters',[])),'reference_upload':resources.get('reference_upload',{'ready':False,'message':'平台未报告图片存储状态'})}


def install(source,destination,base,token_file,codex):
    source=Path(source).resolve();destination=Path(destination).resolve();token_file=Path(token_file).expanduser().resolve() if token_file else None
    if not codex:raise ValueError('未找到 Codex CLI；安装并登录 Codex，或用 --codex 指定它的路径')
    if token_file and not token_file.is_file():raise ValueError('没有找到可选的令牌文件')
    checked=probe(base,token_file.read_text(encoding='utf-8-sig').strip() if token_file else '')
    for name in FILES:
        if not (source/name).is_file():raise ValueError('安装包缺少 '+name)
    if source!=destination:
        for name in FILES:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
    command=[codex,'mcp','add','spine_animation','--',sys.executable,str(destination/'scripts/spine_mcp.py'),'--base',checked['base']]
    if token_file:command.extend(['--token-file',str(token_file)])
    result=subprocess.run(command,capture_output=True,text=True)
    if result.returncode:raise ValueError('Codex MCP 注册失败，请检查 Codex CLI 的配置权限；Skill 文件已保留，可修复后重试')
    config=destination/'settings.json';config.write_text(json.dumps({'base':checked['base'],**({'token_file':str(token_file)} if token_file else {})},ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(config,0o600)
    return {'installed_skill':str(destination),'mcp_server':'spine_animation',**checked,'credentials_in_package':False,'next_step':'重启 Codex 或重新加载 MCP，打开新对话，使用 $spine-animation'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--base');p.add_argument('--token-file',type=Path);p.add_argument('--check',action='store_true');p.add_argument('--non-interactive',action='store_true')
    p.add_argument('--codex',default=discover_codex());p.add_argument('--destination',type=Path,default=Path(os.getenv('CODEX_HOME',str(Path.home()/'.codex')))/'skills'/'spine-animation')
    a=p.parse_args();source=Path(__file__).resolve().parents[1]
    if not a.base:
        if a.non_interactive:raise ValueError('需要 --base 平台地址')
        a.base=input('平台地址（例如 http://服务器IP:8780）：').strip()
    if a.check:
        print(json.dumps(probe(a.base,a.token_file.expanduser().read_text(encoding='utf-8-sig').strip() if a.token_file else ''),ensure_ascii=False));return
    result=install(source,a.destination.expanduser(),a.base,a.token_file,a.codex)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.CalledProcessError) as e:print(str(e),file=sys.stderr);sys.exit(1)
