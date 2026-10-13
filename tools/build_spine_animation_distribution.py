#!/usr/bin/env python3
"""Build the credential-free Skill + MCP handoff archive from explicit files."""
import hashlib,json,shutil,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/spine-animation/scripts'))
from install import FILES
from spine_mcp import PUBLIC_TOOLS
DOCS=('安装说明.txt','给Codex的安装指令.txt','管理员交付说明.txt','安装.command','安装.cmd','connection.example.json')


def build():
    staging=ROOT/'agent/spine-animation-distribution';source=ROOT/'skills/spine-animation'
    paths=list(DOCS)
    for name in FILES:
        target=staging/'spine-animation'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target);paths.append('spine-animation/'+name)
    (staging/'安装.command').chmod(0o755)
    token=(ROOT/'storage/agent-access-token').read_bytes().strip() if (ROOT/'storage/agent-access-token').exists() else b''
    entries={}
    for name in paths:
        payload=(staging/name).read_bytes()
        if token and token in payload:raise ValueError('真实访问令牌混入 '+name)
        if b'/Users/tu/' in payload:raise ValueError('个人电脑路径混入 '+name)
        entries[name]={'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload)}
    manifest={'name':'spine-animation-skill-mcp','version':'1.3.0','python':'>=3.9','platforms':['macOS','Windows','Linux'],'entrypoint':'spine-animation/scripts/install.py','skill_name':'spine-animation','mcp_server':'spine_animation','tools':[t['name'] for t in PUBLIC_TOOLS],'contains_credentials':False,'files':entries}
    (staging/'package.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');paths.append('package.json')
    output=ROOT/'agent/spine-animation-skill.zip'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for name in sorted(paths):z.write(staging/name,name)
    web=ROOT/'web/downloads';web.mkdir(exist_ok=True);shutil.copy2(output,web/output.name)
    print(json.dumps({'archive':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(paths),'contains_credentials':False},ensure_ascii=False,indent=2))


if __name__=='__main__':build()
