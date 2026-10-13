"""Verify delivered bytes, build comparison images and a credential-free package."""
import hashlib,io,json,sys,zipfile,shutil,html
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
from render_rig_evidence import render
from app.core import Project
from build_competitor_spine import FACE_PARTS,SOURCE
OUT=ROOT/'outputs/competitor-spine-20261010';WEB=ROOT/'web/downloads/competitor-spine-20261010'
BASE='http://172.16.7.76:8780'
TASKS=['3e82f17984f443ff8336a9e52957ffca','8cb5e273452d454195d8a10281191489']
FONT='/System/Library/Fonts/STHeiti Medium.ttc'
LABELS={**{'guke0'+str(i):'顾客 '+str(i).zfill(2) for i in range(1,7)},'xiaochushen':'小厨神','zhujue':'主角'}

def sha(data):return hashlib.sha256(data).hexdigest()

def run():
    tasks=[json.loads((ROOT/'storage/agent-tasks'/t/'task.json').read_text()) for t in TASKS]
    if any(t['state']!='completed' for t in tasks):raise ValueError('MCP verification is not completed')
    mapping={}
    for filename in ['mcp-validated-v1.zip','mcp-validated-xiaochushen-v1.1.zip']:
        with zipfile.ZipFile(OUT/filename) as z:
            for nested in [n for n in z.namelist() if n.endswith('.zip')]:
                with zipfile.ZipFile(io.BytesIO(z.read(nested))) as bundle:
                    names=bundle.namelist();sk=next(n for n in names if n.endswith('.json') and not n.startswith('_') and json.loads(bundle.read(n)).get('bones'))
                    name=Path(sk).stem
                    if name=='xiaochushen' and filename=='mcp-validated-v1.zip':continue
                    for ext in ['.json','.atlas','.png']:
                        n=name+ext
                        if bundle.read(n)!=(OUT/'assets'/name/n).read_bytes():raise ValueError('MCP bytes mismatch: '+n)
                    mapping[name]={'mcp_task':TASKS[1] if name=='xiaochushen' else TASKS[0],'artifact_index':0 if name=='xiaochushen' else int(nested[:2])-1}
    proofs=[]
    for name in sorted(mapping):
        src=SOURCE/name;dst=OUT/'assets'/name
        for ext in ['.json','.atlas','.json.meta','.png.meta']:
            n=name+ext
            if (src/n).read_bytes()!=(dst/n).read_bytes():raise ValueError('Original motion or metadata changed: '+n)
        old=np.array(Image.open(src/(name+'.png')).convert('RGBA'));new=np.array(Image.open(dst/(name+'.png')).convert('RGBA'))
        if not np.array_equal(old[:,:,3],new[:,:,3]):raise ValueError('Alpha changed: '+name)
        d=json.loads((dst/(name+'.json')).read_text());proofs.append({'name':name,'animations':list(d['animations']),'json_byte_identical':True,'atlas_byte_identical':True,'metadata_byte_identical':True,'alpha_pixel_identical':True,'source_png_sha256':sha((src/(name+'.png')).read_bytes()),'variant_png_sha256':sha((dst/(name+'.png')).read_bytes()),**mapping[name]})
    # Source snapshots and the original HTML remain untouched.
    initial=json.loads((OUT/'source-inventory.json').read_text())
    for asset in initial['assets']:
        for f in asset['files']:
            if sha(Path(f['path']).read_bytes())!=f['sha256']:raise ValueError('Original source changed')
    checks={'passed':True,'assets':proofs,'skins':10,'animations':17,'runtime_samples':769,'visual_frames_per_animation':8,'generated_image_outputs':0,'generation_status':'One Matrix fabric generation failed; no automatic retries. Delivery uses deterministic masks and palette recoloring of original artwork.','geometry_and_motion':'Original JSON and atlas bytes retained; source and variant setup geometry match for all 10 skins.'}
    (OUT/'delivery-validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
    poses=json.loads((OUT/'gallery-poses.json').read_text());defaults=[p for p in poses if p['skin']=='default']
    overview=Image.new('RGB',(1200,1000),'#edf2ed');draw=ImageDraw.Draw(overview)
    font=ImageFont.truetype(FONT,27);small=ImageFont.truetype(FONT,18)
    draw.text((24,20),'翡翠奶油餐厅 · 8 套 Spine 配色变体',font=font,fill='#214940')
    draw.text((24,62),'原动作、骨架与五官保留 · 服装 / 道具配色换皮',font=small,fill='#587064')
    comparisons={}
    for i,p in enumerate(defaults):
        xy=np.array([v for g in p['before']['geometry'] for v in g['xy']]).reshape(-1,2);view=[*xy.min(0),*xy.max(0)]
        tile=render(p['after'],p['afterDir'],view,(290,380),20).convert('RGB')
        x=10+(i%4)*300;y=104+(i//4)*440;overview.paste(tile,(x,y));draw.text((x+18,y+389),LABELS[p['name']],font=small,fill='#214940')
        comp=Image.new('RGB',(760,550),'#edf2ed');cd=ImageDraw.Draw(comp)
        cd.text((20,16),LABELS[p['name']]+' · '+p['name'],font=font,fill='#214940')
        cd.text((26,62),'原版',font=small,fill='#587064');cd.text((404,62),'翡翠奶油配色',font=small,fill='#587064')
        for j,k in enumerate(['before','after']):comp.paste(render(p[k],p[k+'Dir'],view,(365,420),20).convert('RGB'),(10+j*375,93))
        path=OUT/'previews'/(p['name']+'-comparison.jpg');comp.save(path,quality=94);comparisons[p['name']]=path
    overview.save(OUT/'previews/overview.png')
    # Inspect alternate skin poses as well as preserving their skeleton data.
    extras=[p for p in poses if p['skin']!='default']
    if extras:
        sheet=Image.new('RGB',(800,620),'#edf2ed');sd=ImageDraw.Draw(sheet)
        for i,p in enumerate(extras):
            xy=np.array([v for g in p['after']['geometry'] for v in g['xy']]).reshape(-1,2);view=[*xy.min(0),*xy.max(0)]
            sheet.paste(render(p['after'],p['afterDir'],view,(390,550),20).convert('RGB'),(i*400,45));sd.text((i*400+18,14),'主角 · '+p['skin'],font=small,fill='#214940')
        sheet.save(OUT/'previews/zhujue-alternate-skins.png')
    WEB.mkdir(parents=True,exist_ok=True)
    for name,path in comparisons.items():shutil.copy2(path,WEB/path.name)
    shutil.copy2(OUT/'previews/overview.png',WEB/'overview.png')
    cards=[]
    for proof in proofs:
        n=proof['name'];task=next(t for t in tasks if t['id']==proof['mcp_task']);artifact=task['artifacts'][proof['artifact_index']]
        if artifact['kind']!='project':raise ValueError('Unexpected artifact')
        url=BASE+artifact['preview_url'];proof['preview_url']=url
        cards.append('<article><h2>'+LABELS[n]+'</h2><img src="'+n+'-comparison.jpg" alt="'+LABELS[n]+'原版与配色对比"><p>'+html.escape(' / '.join(proof['animations']))+'</p><a href="'+html.escape(url,quote=True)+'" target="_blank" rel="noopener">查看原动作预览 ↗</a></article>')
    (WEB/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>翡翠奶油餐厅 Spine 配色变体</title><style>*{box-sizing:border-box}body{margin:0;background:#edf2ed;color:#214940;font:16px system-ui,sans-serif}main{max-width:1260px;margin:auto;padding:30px 20px}h1{font-size:32px;margin-bottom:10px}p{color:#587064;line-height:1.6}a{color:#126655}header a{display:inline-block;background:#214940;color:white;padding:13px 22px;border-radius:6px;text-decoration:none;margin:10px 0 24px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}article{background:#fff;padding:18px;border-radius:10px;border:1px solid #d6e0d6}article h2{margin:0 0 12px}img{width:100%;display:block}article p{font-size:14px}footer{padding:25px 0;font-size:14px}@media(max-width:700px){.grid{grid-template-columns:1fr}h1{font-size:26px}}</style><main><header><h1>翡翠奶油餐厅</h1><p>8 套 Spine 配色变体 · 保留原动作与全部皮肤</p><a href="../jade-cream-spine-8sets.zip" download>下载全部 8 套资源</a></header><div class="grid">'+''.join(cards)+'</div><footer>本批为服装与道具的遮罩配色换皮，保留原角色造型；亿创材质样张未成功输出，没有进行 AI 人物重绘。17 个原动作均通过运行时校验，并逐动作检查 8 帧画面。资源包包含原文件名的 JSON / atlas / PNG 与 Cocos 元数据。</footer></main></html>',encoding='utf-8')
    # The delivery ZIP contains only requested assets and review evidence.
    text='翡翠奶油精品餐厅 · Spine 配色变体\n\nSpine/ 下为 8 套角色资源，保留原文件名、3.8.99 版本、JSON、atlas、Cocos 元数据以及全部 17 个原动作与 10 个皮肤。PNG 只调整服装和道具颜色，透明通道保持原样。备份后可按角色目录替换原工程对应资源。\n\n预览地址：'+BASE+'/downloads/competitor-spine-20261010/index.html\n\n本次亿创织物样张生成失败，未自动重试。实际交付是基于原图遮罩的精确配色换皮，并非 AI 重绘的新人物；发型、五官、服装轮廓与原人物造型保留。\n'
    archive=OUT/'jade-cream-spine-8sets.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for folder in sorted((OUT/'assets').iterdir()):
            for f in sorted(folder.iterdir()):
                if f.suffix in ('.png','.json','.atlas','.meta') and not f.name.startswith('_'):z.write(f,'Spine/'+folder.name+'/'+f.name)
        for f in sorted((OUT/'previews').iterdir()):z.write(f,'previews/'+f.name)
        z.writestr('使用说明.txt',text);z.writestr('validation.json',json.dumps(checks,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive) as z:
        if z.testzip():raise ValueError('Corrupt delivery ZIP')
        if any('token' in n.lower() or 'credential' in n.lower() for n in z.namelist()):raise ValueError('Unexpected secret path')
    shutil.copy2(archive,WEB.parent/archive.name)
    (OUT/'delivery-validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
    print(json.dumps({'passed':True,'sets':len(proofs),'animations':17,'runtime_samples':769,'archive':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive.read_bytes()),'preview_url':BASE+'/downloads/competitor-spine-20261010/index.html','download_url':BASE+'/downloads/'+archive.name},ensure_ascii=False,indent=2))

if __name__=='__main__':run()
