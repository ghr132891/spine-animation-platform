#!/usr/bin/env python3
"""Read-only connection and MCP diagnostic; never generates images or tasks."""
import argparse,json,subprocess,sys
from pathlib import Path
from spine_mcp import settings,PUBLIC_TOOLS
from install import discover_codex,probe


def main():
    config=settings();p=argparse.ArgumentParser();p.add_argument('--base',default=config.get('base'));p.add_argument('--token-file',type=Path,default=Path(config['token_file']) if config.get('token_file') else None);p.add_argument('--codex',default=discover_codex());a=p.parse_args()
    if not a.base:raise ValueError('请先运行 install.py，或提供 --base 平台地址')
    result=probe(a.base,a.token_file.read_text(encoding='utf-8-sig').strip() if a.token_file else '')
    requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'spine_sources','arguments':{}}}]
    command=[sys.executable,str(Path(__file__).with_name('spine_mcp.py')),'--base',a.base]
    if a.token_file:command.extend(['--token-file',str(a.token_file)])
    process=subprocess.run(command,input='\n'.join(json.dumps(r) for r in requests)+'\n',capture_output=True,text=True,timeout=180)
    if process.returncode:raise ValueError('MCP 客户端启动失败，请核对 Python 与平台地址')
    replies=[json.loads(line) for line in process.stdout.splitlines()]
    if len(replies)!=3 or any('error' in r or r.get('result',{}).get('isError') for r in replies):raise ValueError('MCP 握手或平台调用失败')
    names=[t['name'] for t in replies[1]['result']['tools']]
    if set(names)!={t['name'] for t in PUBLIC_TOOLS}:raise ValueError('安装的 MCP 工具不完整，请重新安装')
    result.update(mcp_protocol_passed=True,tools=names,submitted_tasks=0,generated_images=0)
    if a.codex:
        registered=subprocess.run([a.codex,'mcp','get','spine_animation','--json'],capture_output=True,text=True)
        entry=json.loads(registered.stdout) if registered.returncode==0 else {}
        result['codex_registered']=bool(entry.get('enabled'))
        if not result['codex_registered']:raise ValueError('平台已连通，但 Codex 尚未注册 spine_animation，请再次运行安装程序')
        transport=entry.get('transport',{});args=transport.get('args',[])
        try:
            matched=transport.get('type')=='stdio' and Path(args[0]).resolve()==Path(__file__).with_name('spine_mcp.py').resolve() and args[args.index('--base')+1].rstrip('/')==a.base.rstrip('/')
            matched=matched and (Path(args[args.index('--token-file')+1]).resolve()==a.token_file.resolve() if a.token_file else '--token-file' not in args)
        except (ValueError,IndexError):matched=False
        if not matched:raise ValueError('Codex 中已有不同的 Spine 连接配置，请运行已安装 Skill 的 doctor.py 或重新安装')
        result['codex_configuration_matches']=True
    else:result['codex_registered']=None
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.TimeoutExpired) as e:print(str(e),file=sys.stderr);sys.exit(1)
