"""Build a reversible restaurant palette variant without changing Spine geometry.

The supplied original sprites stay the identity source. An optional reviewed AI
fabric swatch contributes only bounded, high-frequency surface relief. It never
replaces faces or supplies anatomical pixels. Masks below are specific to these
eight inspected assets, not a general automatic human segmentation model.
"""
import argparse, hashlib, json, shutil, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))
from app.core import Project, png_bytes, read_image
from render_rig_evidence import render

SOURCE=Path('/Users/tu/Game working/Flambé · 餐厅烹饪/spine animation/asset-export/files/cocos-project/assets/Spine')
OUT=ROOT/'outputs/competitor-spine-20261010'
NODE=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
COLORS={'jade':(34,124,109),'cream':(231,223,197),'navy':(35,67,72),'gold':(196,160,91),
        'mint':(114,166,145),'steel':(85,136,137)}
JADE={'guke01':(29,120,102),'guke02':(109,157,138),'guke03':(33,95,104),
      'guke04':(49,117,109),'guke05':(69,149,126),'guke06':(52,116,137)}
FACE_PARTS={'tou','houfapian','bozi','lian','houtoufa','liuhai1','liuhai2','liuhai3',
            'youmeimao','youshangyanpi','youxiayanpi','youyanbai','youyanren','zui',
            'zuomeimao','zuoshangyanpi','zuoxiayanpi','zuoyanbai','zuoyanren','youshou','zuoshou'}

def hsv(rgb):
    a=rgb.astype(float)/255;hi=a.max(2);lo=a.min(2);d=hi-lo
    h=np.zeros_like(hi);nz=d>1e-6
    for c,offset in [(0,0),(1,2),(2,4)]:
        mask=nz&(a[:,:,c]==hi)
        numerator=(a[:,:,1]-a[:,:,2]) if c==0 else (a[:,:,2]-a[:,:,0]) if c==1 else (a[:,:,0]-a[:,:,1])
        h[mask]=(numerator[mask]/d[mask]+offset)%6
    return h/6, np.divide(d,hi,out=np.zeros_like(d),where=hi>0),hi

def tint(rgb,mask,color,pattern=None,fabric=False):
    if not mask.any():return
    old=rgb.astype(float);lum=old@np.array([.2126,.7152,.0722]);values=lum[mask]
    pivot=max(30,float(np.percentile(values,65)))
    shade=np.clip(lum/pivot,.10,1.65)**.86
    target=np.array(color)[None,None,:]*shade[:,:,None]
    highlight=np.clip((shade-1.13)/.60,0,.26)
    target=target*(1-highlight[:,:,None])+np.array([250,249,236])*highlight[:,:,None]
    if fabric and pattern is not None:
        relief=np.asarray(pattern.resize((rgb.shape[1],rgb.shape[0]),Image.Resampling.BILINEAR),dtype=float)/255-.5
        target*=1+relief[:,:,None]*.045
    # Original near-black edges, stitchwork and facial pixels are not repainted.
    edge=(lum<18);mask=mask&~edge
    rgb[mask]=np.clip(np.rint(target[mask]),0,255).astype(np.uint8)

def redesign(name,part,region,pattern):
    a=np.array(part);rgb=a[:,:,:3].copy();h,s,v=hsv(rgb);vis=a[:,:,3]>0
    y,x=np.mgrid[0:a.shape[0],0:a.shape[1]];y=y/max(1,a.shape[0]-1);x=x/max(1,a.shape[1]-1)
    n=region['name'];masks=[]
    def add(mask,color,fabric=True):masks.append((mask&vis,COLORS.get(color,color),fabric))
    # Head sprites, hair, eyes, eyebrows and hands are preserved byte for byte.
    if n in ('tou','houfapian') or (name=='xiaochushen' and n not in ('guohou','guoqian','shangbanshen','toujin1','weijin1','weijin2','weijin_hou','xiabanshen','youdabi','zuodabi','youxiaobi')):
        return part.copy(),0
    jade=JADE.get(name,COLORS['jade'])
    if name=='guke01':
        if n in ('shengti','youbi','zuobi'):
            add(((y>.105)&(s>.30)&(h<.23)) if n=='shengti' else ((h>.080)&(h<.22)&(s>.30)),jade)
            add((h>.22)&(h<.48)&(s>.30),'navy',False)
        elif n in ('youtui','zuotui'):add((y>.59)&(h<.055)&(s>.65),'navy',False)
    elif name=='guke02':
        if n in ('bao','jiandaihou'):add(vis,'navy',False)
        elif n in ('shengti','youbi','zuobi'):
            add((h>.15)&(h<.43)&(s>.2),jade)
            if n=='shengti':
                add(y>.44,'cream')
                add((y<.25)&(((h>.5)&(h<.95))|((h>.92)&(s>.4))),'gold',False)
        elif n in ('tui01','youtui'):add(y>.59,'navy',False)
    elif name=='guke03':
        if n in ('shenti','youbi','zuobi'):
            add((h>.68)&(h<.97)&(s>.16),jade)
            if n=='shenti':add(y>.78,'cream')
        elif n in ('youtui','zuotui'):
            add(y<.86,'cream');add(y>=.86,'navy',False)
    elif name=='guke04':
        if n in ('gebo','gebo1','shengti'):
            add((s<.32)&(v<.45)&((y>.1) if n=='shengti' else vis),jade)
            if n=='shengti':add((h>.52)&(h<.80)&(s>.20),'cream')
        elif n in ('youtui','zuotui'):
            add((h>.51)&(h<.8)&(s>.16),'cream');add((y>.80)&(s<.35)&(v<.40),'navy',False)
    elif name=='guke05':
        if n in ('shenti','yougebo','zuogebo'):
            add((h>.02)&(h<.115)&(s>.52)&((y>.15)&(y<.61) if n=='shenti' else y<.60),jade)
            if n=='shenti':add((h>.48)&(h<.76)&(s>.20),'cream')
        elif n in ('youtui','zuotui'):
            add((y<.30)&(h>.48)&(h<.76)&(s>.2),'cream');add((y>.74)&(s<.3),'cream',False)
    elif name=='guke06':
        if n=='shenti':
            add((h>.16)&(h<.44)&(s>.28),jade)
            add((h>.47)&(h<.77)&(s>.2)&(y>.75),'cream')
            add((x<.35)&(y<.60)&(v<.35)&(s<.35),'navy',False)
        elif n in ('yougebo','zuogebo'):add((y<.57)&(s<.22)&(v>.3),'cream')
        elif n in ('youtui','zuotui'):
            add((y<.69)&(h>.47)&(h<.78)&(s>.18),'cream');add((y>.77)&(s<.25),'cream',False)
    elif name=='zhujue':
        if n=='shengti':
            add((y>.42),'jade')
            add((y<.32)&((h>.95)|(h<.065))&(s>.80),'jade')
            add((y>.14)&(y<.40)&(x<.75)&(s<.32)&(v>.5),'cream')
        elif n in ('youbi','zuobi'):
            add((s<.32)&(v>.6),'cream',False)
            add((v<.35)&(s>.35),'navy',False)
        elif n in ('youtui','zuotui'):add((v<.30),'navy',False)
    elif name=='xiaochushen':
        if n in ('guohou','guoqian'):add(vis,'steel',False)
        elif n=='xiabanshen':add(vis,'jade')
        elif n in ('weijin1','weijin2','weijin_hou','toujin1','shangbanshen'):
            add(((h>.97)|(h<.075))&(s>.80),'jade')
            if n in ('shangbanshen','toujin1'):add((s<.28)&(v>.68),'cream')
        elif n in ('youdabi','zuodabi','youxiaobi'):
            add((s<.28)&(v>.65),'cream')
            if n!='youxiaobi':add((v<.28)&(s>.30),'navy',False)
    for mask,c,f in masks:tint(rgb,mask,c,pattern,f)
    a[:,:,:3]=rgb
    return Image.fromarray(a),int(np.count_nonzero(np.any(rgb!=np.asarray(part)[:,:,:3],axis=2)))

def runtime(folder,name,clip=None):
    cmd=[str(NODE),str(ROOT/'tools/validate_agent_runtime.cjs'),str(folder),'3.8',name+'.json',name+'.atlas']
    if clip:cmd.append(clip)
    p=subprocess.run(cmd,capture_output=True,text=True,check=True);return json.loads(p.stdout)

def framesheet(folder,name,clip,path):
    data=runtime(folder,name,clip);frames=data.pop('frames');xy=np.array([c for f in frames for g in f['geometry'] for c in g['xy']]).reshape(-1,2)
    lo=xy.min(0);hi=xy.max(0);view=[*lo,*hi];sheet=Image.new('RGB',(1200,800),'#e5ebf3');draw=ImageDraw.Draw(sheet)
    for i,f in enumerate(frames):
        tile=render(f,folder,view,(300,375),20);x=i%4*300;y=i//4*400;sheet.paste(tile,(x,y+25));draw.text((x+10,y+5),name+' / '+clip+' / '+str(i),fill='#22324b')
    sheet.save(path);return data

def build(pattern_path=None,only=None):
    pattern=None
    if pattern_path:
        im=Image.open(pattern_path).convert('L');low=im.filter(ImageFilter.GaussianBlur(6));high=np.array(im,dtype=float)-np.array(low,dtype=float)
        pattern=Image.fromarray(np.clip(128+high*1.3,0,255).astype(np.uint8))
    reports=[]
    with tempfile.TemporaryDirectory() as temp:
        for folder in sorted(SOURCE.iterdir()):
            name=folder.name
            if not folder.is_dir() or (only and name!=only):continue
            files={f.name:f.read_bytes() for f in folder.iterdir() if f.suffix in ('.json','.atlas','.png','.meta')}
            p=Project(Path(temp)/name,files,name);changed=[];heads={r['id']:np.array(p.part(r['id'],True)) for r in p.regions if r['name'] in FACE_PARTS}
            for r in p.regions:
                original=p.part(r['id'],True);part,count=redesign(name,original,r,pattern)
                if count:
                    # Some source atlas rectangles include neighboring artwork.
                    # Preserve edits in overlapping rectangles instead of putting
                    # the original neighboring pixels back over previous edits.
                    before=np.array(original);candidate=np.array(part);current=np.array(p.part(r['id']))
                    delta=np.any(before[:,:,:3]!=candidate[:,:,:3],axis=2)
                    current[delta,:3]=candidate[delta,:3]
                    p._put_part(r['id'],Image.fromarray(current));changed.append({'id':r['id'],'name':r['name'],'edited_pixels':count})
            if name=='xiaochushen':
                # This atlas has overlapping rectangular parts: protect the exact
                # original facial/hand pixels after all garment overlays.
                for id,old in heads.items():
                    current=np.array(p.part(id));visible=old[:,:,3]>0
                    current[visible]=old[visible];p._put_part(id,Image.fromarray(current))
            for id,old in heads.items():
                if not np.array_equal(old,np.array(p.part(id))):raise ValueError(name+' protected face pixels changed')
            p.commit('翡翠奶油精品餐厅 · 服装与材质变体')
            report=p.validate()
            if not report['passed']:raise ValueError(name+' invariant validation failed')
            dst=OUT/'assets'/name;dst.mkdir(parents=True,exist_ok=True)
            for filename,data in files.items():(dst/filename).write_bytes(p.pages_data.get(filename,data))
            validated=runtime(dst,name);validated.pop('frames',None)
            result={'name':name,'changes':changed,'validation':report,'runtime':validated,'protected_head_parts_unchanged':True,'source_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in files.items()},'ai_material':str(pattern_path) if pattern_path else None}
            (dst/'_variant-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));reports.append(result)
            print(json.dumps({'name':name,'changed_parts':len(changed),'runtime_samples':validated['samples'],'passed':True}),flush=True)
    (OUT/('build-'+only+'.json' if only else 'build-manifest.json')).write_text(json.dumps(reports,ensure_ascii=False,indent=2))
    return reports

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pattern',type=Path);parser.add_argument('--only');parser.add_argument('--render',action='store_true');args=parser.parse_args()
    reports=build(args.pattern,args.only)
    if args.render:
        (OUT/'previews').mkdir(exist_ok=True)
        for result in reports:
            n=result['name']
            for clip in result['runtime']['animations']:framesheet(OUT/'assets'/n,n,clip,OUT/'previews'/(n+'-'+clip+'.png'))
