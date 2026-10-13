"""Build shareable clients from canonical files, excluding all local credentials."""
import ast,hashlib,json,re,shutil,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION='1.10.0'

def installer_files():
    tree=ast.parse((ROOT/'skills/spine-animation/scripts/install.py').read_text())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FILES' for t in n.targets))

def package(path,entries):
    tmp=path.with_suffix('.zip.tmp')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
        for file,name in entries:
            assert 'settings.json' not in name and not name.endswith(('.token','.pyc')) and '__pycache__' not in name
            z.write(file,name)
    tmp.replace(path)

def main():
    skill=ROOT/'skills/spine-animation';dist=ROOT/'agent/spine-animation-distribution'
    for name in installer_files():
        target=dist/'spine-animation'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(skill/name,target)
    meta=json.loads((dist/'package.json').read_text());meta['version']=VERSION
    meta['minimum_server_version']=VERSION;meta['tools']=['spine_reference','spine_sources','spine_submit','spine_batch','spine_status','spine_resume','spine_cancel','spine_refine','spine_result','spine_preview','spine_gait','spine_contact','spine_closeup','spine_review_bundle','spine_download']
    for name in ('安装说明.txt','给Codex的安装指令.txt','管理员交付说明.txt'):
        guide=dist/name;content=guide.read_text()
        content=re.sub(r'(Spine 动画助手 )\d+\.\d+\.\d+',lambda m:m[1]+VERSION,content)
        content=re.sub(r'(Spine 动画助手 · Skill \+ MCP 安装包（)\d+\.\d+\.\d+',lambda m:m[1]+VERSION,content)
        content=re.sub(r'(MCP握手、)\d+(个工具)',lambda m:m[1]+str(len(meta['tools']))+m[2],content)
        guide.write_text(content)
    top=['安装说明.txt','给Codex的安装指令.txt','管理员交付说明.txt','安装.command','安装.cmd','connection.example.json']
    files=top+['spine-animation/'+name for name in installer_files()]
    meta['files']={name:{'sha256':hashlib.sha256((dist/name).read_bytes()).hexdigest(),'bytes':(dist/name).stat().st_size} for name in files}
    (dist/'package.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    target=ROOT/'agent/spine-animation-skill.zip';package(target,[(dist/name,'spine-animation-install/'+name) for name in files+['package.json']])
    shutil.copy2(target,ROOT/'web/downloads'/target.name)
    client=ROOT/'agent/spine-agent-client';client.mkdir(exist_ok=True)
    names=['spine_mcp.py','spine_workflow.py','WORKFLOWS.md','openapi.json']
    for name in names:shutil.copy2(ROOT/'agent'/name,client/name)
    target=ROOT/'agent/spine-agent-client.zip';package(target,[(client/name,'spine-agent-client/'+name) for name in names]);shutil.copy2(target,ROOT/'web/downloads'/target.name)
    reskin=ROOT/'skills/spine-ai-reskin';names=['SKILL.md','scripts/spine_agent.py','agents/openai.yaml','references/api.md','references/openapi.json']
    target=ROOT/'agent/spine-ai-reskin-skill.zip';package(target,[(reskin/name,'spine-ai-reskin/'+name) for name in names]);shutil.copy2(target,ROOT/'web/downloads'/target.name)
    print(json.dumps({'version':VERSION,'animation_zip':str(ROOT/'web/downloads/spine-animation-skill.zip'),'skill_files':len(installer_files()),'contains_credentials':False}))

if __name__=='__main__':main()
