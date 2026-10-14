"""Material-stage comparison, with immutable inputs and no animation creation.

Template conditioning is a reference-image constraint, not ControlNet or an
exact geometry guarantee. Raw output is retained; alpha is never clipped to a
template to disguise an invalid generated shape.
"""
import base64
import hashlib
import json
import re
import threading
import time
import uuid
import subprocess
import shutil
import os
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from .core import UserError, atomic_json, png_bytes, read_image, sha, Project
from .provider import DEFAULT_MODEL, failure_details
from .jobs import JobHistory

PROMPT = '''Create six complete animation asset pieces for the character in reference 1.
Reference 1 defines identity, green vest, white shirt, blue jeans, white sneakers and black backpack.
Reference 2 defines the exact six-piece layout, relative sizes, orientations and connection silhouettes.
Keep the same two rows and three columns as reference 2. Fill each piece with the appearance from reference 1.
Preserve the continuous main garment, collar, waist and attached backpack as one piece.
Keep each sleeve and palm continuous, each trouser piece and sneaker continuous.
Give each mounting end complete rounded material coverage matching reference 2.
Keep the portrait separate from the collar. Preserve the reference proportions, relaxed shapes and shoe sizes.
Maintain clean open space around each piece on a uniform white background. Return only the six-piece asset sheet.'''

def identifier(value):
    if not re.fullmatch('[a-f0-9]{32}',str(value)):raise UserError('部件试验编号无效')
    return str(value)

def prepare_template(project, page_image=None):
    """Unpack rotation/trim using atlas metadata, keeping exact part pixels."""
    if len(project.regions)>24:raise UserError('模板最多24块部件')
    if page_image is not None:
        if len(project.pages)!=1:raise UserError('单张模板 PNG 需要单页 atlas')
        im=read_image(page_image)
        if list(im.size)!=project.pages[0]['size']:raise UserError('模板 PNG 尺寸与 atlas 不符')
        # Independent Project instance: no source file is written.
        project.pages_data[project.pages[0]['file']]=png_bytes(im)
    images={r['name']:project.part(r['id']) for r in project.regions}
    if len(images)!=6:raise UserError('当前模板试验支持6个完整部件，其他结构需先配置独立模板')
    W,H=1344,1280; board=Image.new('RGBA',(W,H),'white'); slots=[]
    scale=min(376/max(im.width for im in images.values()),544/max(im.height for im in images.values()))
    for i,(name,im) in enumerate(images.items()):
        x=(i%3)*448;y=(i//3)*640
        scaled=im.resize((round(im.width*scale),round(im.height*scale)),Image.Resampling.LANCZOS)
        px=x+(448-scaled.width)//2;py=y+(640-scaled.height)//2
        board.alpha_composite(scaled,(px,py))
        slots.append({'name':name,'cell':[x,y,x+448,y+640],'placement':[px,py,scaled.width,scaled.height],
                      'size':list(im.size),'sha256':sha(png_bytes(im))})
    return board,images,{'contract':'template-reference-v1','canvas':[W,H],'slots':slots,
        'constraint_mode':'reference-image-only','hard_geometry_control':False,
        'source_skeleton_sha256':sha(project.files[project.skeleton]),'draw_order':[s['name'] for s in project.skeleton_data['slots']]}

def extract_candidate(folder):
    """Use known cells, preserving aspect ratio and reporting silhouette drift."""
    from .character import cut_background
    folder=Path(folder);spec=json.loads((folder/'template.json').read_text())
    raw=read_image((folder/'candidate.png').read_bytes());w,h=raw.size;W,H=spec['canvas'];rows=[]
    parts=folder/'parts';parts.mkdir(exist_ok=True)
    for slot in spec['slots']:
        name=slot['name'];x0,y0,x1,y1=slot['cell'];cell=raw.crop((round(x0*w/W),round(y0*h/H),round(x1*w/W),round(y1*h/H)))
        im=cut_background(cell,10);bbox=im.getbbox()
        if not bbox:raise UserError('生成部件为空：'+name)
        crop=im.crop(bbox);crop.save(parts/(name+'.png'))
        ref=read_image((folder/'reference-parts'/ (name+'.png')).read_bytes());refbox=ref.getbbox();ref=ref.crop(refbox)
        # Measurement only. Never impose the reference alpha onto generated art.
        a=np.asarray(ref.resize((128,128)).getchannel('A'))>127
        b=np.asarray(crop.resize((128,128)).getchannel('A'))>127
        iou=float((a&b).sum()/max(1,(a|b).sum()))
        rows.append({'name':name,'file':'parts/'+name+'.png','sha256':sha(png_bytes(crop)),
            'cell_crop':list(bbox),'size':list(crop.size),'silhouette_iou_diagnostic':round(iou,4),
            'aspect_ratio_relative_error':round(abs((crop.width/crop.height)/(ref.width/ref.height)-1),4),
            'touches_cell_edge':bbox[0]<2 or bbox[1]<2 or bbox[2]>cell.width-2 or bbox[3]>cell.height-2})
    atomic_json(folder/'parts.json',{'parts':rows,'visual_verdict':'not_reviewed','alpha_clipped_to_template':False})
    return rows

class PartsLab:
    def __init__(self,storage,provider):
        self.storage=Path(storage);self.root=self.storage/'parts-lab';self.provider=provider
        self.lock=threading.RLock();self.running=set();self.jobs=JobHistory(storage,provider,'parts-lab')

    def directory(self,id):return self.root/identifier(id)

    def read(self,id):
        p=self.directory(id)/'experiment.json'
        if not p.exists():raise UserError('部件试验不存在')
        return json.loads(p.read_text())

    def create(self,character,project,data):
        id=identifier(data.get('nonce'));folder=self.directory(id)
        page=base64.b64decode(data['template_png'],validate=True) if data.get('template_png') else None
        fingerprint=sha(json.dumps({'character':character.id,'source':character.meta['image_id'],
            'project':project.id,'revision':project.revision,'template_png':sha(page) if page else None},sort_keys=True).encode())
        with self.lock:
            if (folder/'experiment.json').exists():
                result=self.read(id)
                if result['request_sha256']!=fingerprint:raise UserError('试验编号已用于其他输入')
                return result
            board,images,spec=prepare_template(project,page)
            folder.mkdir(parents=True,exist_ok=True);(folder/'reference-parts').mkdir(exist_ok=True)
            for name,im in images.items():
                if '/' in name or '\\' in name or name in ('.','..'):raise UserError('模板部件名无效')
                im.save(folder/'reference-parts'/(name+'.png'))
            character.image().save(folder/'master.png');board.save(folder/'template.png')
            atomic_json(folder/'template.json',spec)
            # Template geometry remains evidence, never a new generated result.
            atomic_json(folder/'template-skeleton.json',project.skeleton_data)
            experiment={'id':id,'character_id':character.id,'template_project_id':project.id,
                'request_sha256':fingerprint,'master_sha256':sha((folder/'master.png').read_bytes()),
                'template_sha256':sha((folder/'template.png').read_bytes()),'created_at':time.time(),
                'routes':{'template':'prepared','see-through':'prepared'},'status':'materials_only',
                'max_template_generations':1,'animation_allowed':False,'review':None}
            atomic_json(folder/'experiment.json',experiment);return experiment

    def start(self,id,route):
        if route not in ('template','see-through'):raise UserError('未知部件路线')
        with self.lock:
            exp=self.read(id);key=(id,route)
            if key not in self.running:
                self.running.add(key)
                threading.Thread(target=self._run,args=(id,route),daemon=True).start()
            return exp

    def _run(self,id,route):
        folder=self.directory(id)
        import fcntl
        lease=(folder/(route+'.lock')).open('a')
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            with self.lock:self.running.discard((id,route))
            return
        try:
            if route=='see-through':
                from .see_through import run, download_outputs
                j=run(folder/'see-through',(folder/'master.png').read_bytes())
                if j['status']=='completed':download_outputs(folder/'see-through')
                status=j['status']
            else:
                path=self.jobs.path(id,id)
                if not path.exists():
                    # Both references must pass real publication/read-back before submission.
                    refs=[self.provider.ensure_reference(read_image((folder/n).read_bytes()))['url'] for n in ('master.png','template.png')]
                    job={'id':id,'status':'submission_unknown','created_at':time.time(),'prompt':PROMPT,
                        'mode':'template-parts','model':self.provider.status()['model'],'source_image_id':self.read(id)['master_sha256']}
                    atomic_json(path,job)
                    try:
                        response=self.provider.request('/models/'+DEFAULT_MODEL+'/generate',{'input':{'prompt':PROMPT,
                            'input_images':refs,'aspect_ratio':'1:1','resolution':'1K','quality':'high','output_format':'png','background':'opaque'},'request_num':1})
                        gen_id=self.provider.generation_id(response)
                        job.update(status='pending',gen_id=gen_id,**self.provider.job_metadata(gen_id));atomic_json(path,job)
                    except Exception as error:
                        job['failure']=failure_details(error);atomic_json(path,job);raise
                job=self.jobs.refresh(path,force=True)
                if job.get('candidate_ready'):
                    (folder/'candidate.png').write_bytes(path.with_suffix('.png').read_bytes());extract_candidate(folder)
                status=job['status']
            with self.lock:
                exp=self.read(id);exp['routes'][route]=status
                if route in exp.get('errors',{}):
                    exp.setdefault('error_history',[]).append({'route':route,**exp['errors'].pop(route)})
                atomic_json(folder/'experiment.json',exp)
        except Exception as error:
            with self.lock:
                exp=self.read(id);exp['routes'][route]='needs_attention'
                exp.setdefault('errors',{})[route]={'type':type(error).__name__,'message':str(error)[:400]}
                atomic_json(folder/'experiment.json',exp)
        finally:
            lease.close()
            with self.lock:self.running.discard((id,route))

    def status(self,id):
        exp=self.read(id);folder=self.directory(id)
        for route,path in [('template',self.jobs.path(id,id)),('see-through',folder/'see-through/job.json')]:
            if path.exists():
                j=json.loads(path.read_text());exp.setdefault('jobs',{})[route]={k:j[k] for k in ('status','gen_id','event_id','error','failure','created_at','updated_at') if k in j}
        exp['running']=[r for i,r in self.running if i==id]
        for route,job in exp.get('jobs',{}).items():exp['routes'][route]=job['status']
        for route,review in exp.get('reviews',{}).items():
            rdir=folder if route=='template' else folder/'see-through'
            if review.get('digest')!=evidence_digest(rdir):
                review.update(verdict='needs_repair',stale=True,reason='素材或检查证据已变化，之前的验收已失效，请重新检查。')
        return exp

    def inspect(self,id,route,node=None):
        folder=self.directory(id)
        if route=='template':return inspect_template(folder,node=node)
        if route=='see-through':return inspect_see_through(folder/'see-through')
        raise UserError('未知部件路线')

    def set_probe_bones(self,id,data):
        folder=self.directory(id);s=json.loads((folder/'template-skeleton.json').read_text())
        known={b['name'] for b in s['bones']};mapping=data.get('probe_bones')
        if not isinstance(mapping,dict) or set(mapping)!= {'arms','legs'} or any(not isinstance(v,list) or len(v)!=2 or any(n not in known for n in v) for v in mapping.values()):
            raise UserError('请按模板真实骨架指定两侧肩和髋的起始骨骼')
        if len(set(mapping['arms']+mapping['legs']))!=4:raise UserError('摆开检查需要四根不同的肢体根骨骼')
        with self.lock:
            spec=json.loads((folder/'template.json').read_text());spec['probe_bones']=mapping;atomic_json(folder/'template.json',spec)
            exp=self.read(id);exp.get('reviews',{}).pop('template',None);atomic_json(folder/'experiment.json',exp)
            return spec

    def file(self,id,name):
        # No job receipts/private upload paths are published as downloadable files.
        if not re.fullmatch(r'(?:reference-parts/|parts/|see-through/)?[A-Za-z0-9_-]+\.(?:png|psd|json)',name):
            raise UserError('素材文件名无效')
        if name.endswith('job.json') or '/jobs/' in name:raise UserError('不能下载任务内部记录')
        p=self.directory(id)/name
        if not p.is_file():raise UserError('素材尚未生成')
        return p.read_bytes()

    def review(self,id,data):
        """Evidence-bound route review. Does not itself create or approve a rig."""
        route=data.get('route')
        if route not in ('template','see-through'):raise UserError('未知部件路线')
        folder=self.directory(id);rdir=folder if route=='template' else folder/'see-through'
        report_path=rdir/'inspection.json'
        if not report_path.exists():raise UserError('先生成完整部件、试拼、摆开检查包')
        report=json.loads(report_path.read_text())
        if not all((rdir/name).is_file() for name in ('parts.png','assembly.png','spread.png')):
            raise UserError('完整部件、试拼、摆开证据缺失')
        current=evidence_digest(rdir)
        if current!=report['digest'] or data.get('digest')!=current:raise UserError('素材已变化，需重新验收')
        checks=data.get('checks',{})
        keys=('complete_parts','assembly_matches','spread_intact','connection_geometry','appearance_matches')
        if set(checks)!=set(keys) or any(type(v)!=bool for v in checks.values()):raise UserError('请记录五项实际验收结论')
        verdict=data.get('verdict');reason=data.get('reason','')
        if verdict not in ('ready','needs_repair') or not isinstance(reason,str) or not reason.strip():raise UserError('请记录具体问题')
        if verdict=='ready' and (not all(checks.values()) or report.get('blockers')):raise UserError('存在缺件或连接问题，不能进入动画')
        with self.lock:
            exp=self.read(id);exp.setdefault('reviews',{})[route]={'digest':current,'verdict':verdict,'checks':checks,'reason':reason}
            # Material approval is not an animation approval. The normal prebind
            # gate still applies after actual import/registration into a character.
            exp['animation_allowed']=False
            atomic_json(folder/'experiment.json',exp);return exp

def evidence_digest(folder):
    folder=Path(folder);items=[]
    for p in sorted(folder.rglob('*.png')):
        if 'reference-parts' in p.parts or 'see-through' in p.relative_to(folder).parts:continue
        items.append((p.relative_to(folder).as_posix(),sha(p.read_bytes())))
    # PSD has authoritative ordering, coordinates and full-resolution layers.
    if (folder/'layers.psd').exists():items.append(('layers.psd',sha((folder/'layers.psd').read_bytes())))
    for name in ('template.json','template-skeleton.json','parts.json'):
        if (folder/name).exists():items.append((name,sha((folder/name).read_bytes())))
    return sha(json.dumps(items,sort_keys=True).encode())

def inspect_see_through(folder):
    """Read actual PSD, never interpret sorted gallery order as draw order."""
    try:from psd_tools import PSDImage
    except ImportError:
        # Optional isolated dependency folder, installed by the local admin.
        import sys
        deps=Path(__file__).resolve().parents[1]/'storage/parts-lab-deps'
        if deps.is_dir() and str(deps) not in sys.path:sys.path.append(str(deps))
        try:from psd_tools import PSDImage
        except ImportError:raise UserError('请安装 requirements-parts-lab.txt 后读取 PSD') from None
    folder=Path(folder);psd=PSDImage.open(folder/'layers.psd');w,h=psd.size
    canvases=[];rows=[]
    for index,layer in enumerate(psd):
        if layer.is_group():raise UserError('PSD 包含图层组，需显式展开后验收')
        im=layer.topil()
        if im is None:continue
        im=im.convert('RGBA');canvas=Image.new('RGBA',psd.size);canvas.alpha_composite(im,(layer.left,layer.top))
        canvas.save(folder/f'psd-{index:02d}.png')
        rows.append({'name':layer.name,'file':f'psd-{index:02d}.png','offset':[layer.left,layer.top],'visible':layer.visible})
        canvases.append((layer.name,canvas,layer.visible))
    # psd-tools iteration follows file order bottom-to-top for this exported PSD.
    assembled=Image.new('RGBA',psd.size)
    for name,im,visible in canvases:
        if visible:assembled.alpha_composite(im)
    assembled.save(folder/'assembly.png')
    sheets=Image.new('RGB',(1200,420*((len(canvases)+3)//4)),'#e1e8f0');draw=ImageDraw.Draw(sheets)
    for i,(name,im,visible) in enumerate(canvases):
        a=np.asarray(im);mask=a[:,:,3]>127
        if mask.any():
            yy,xx=np.where(mask);crop=im.crop((int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1))
            crop.thumbnail((276,374));x=i%4*300+(300-crop.width)//2;y=i//4*420+30
            sheets.paste(crop,(x,y),crop)
        draw.text((i%4*300+8,i//4*420+8),name,fill='black')
    sheets.save(folder/'parts.png')
    # Show real available layers exploded without inventing missing left/right legs.
    spread=Image.new('RGBA',(w+440,h));pivots={}
    for name,im,visible in canvases:
        if not visible:continue
        if name.startswith('handwear'):
            a=np.asarray(im)[:,:,3]>127;yy,xx=np.where(a)
            if len(xx):
                top=yy.min();topx=float(np.median(xx[yy<top+12]));pivot=(topx,float(top+8));pivots[name]=pivot
                angle=30 if topx<w*.52 else -30
                im=im.rotate(angle,Image.Resampling.BICUBIC,center=pivot)
        elif name=='legwear':
            # Translate the whole pants layer: marking it as two legs would lie.
            im=im.transform(psd.size,Image.Transform.AFFINE,(1,0,-155,0,1,0))
        spread.alpha_composite(im,(120,0))
    spread.save(folder/'spread.png')
    names=[r['name'] for r in rows];blockers=[]
    if 'legwear' in names and not any(n.startswith('legwear-') for n in names):blockers.append('双腿合在一个图层，不能独立摆开；需逐腿遮挡补全')
    if 'footwear' in names:blockers.append('鞋与裤腿分离且双鞋同层，需建立逐侧归属与连续连接')
    report={'provider':'see-through','draw_order':[r['name'] for r in rows],'canvas':list(psd.size),
        'layers':rows,'blockers':blockers,'spread_scope':'双臂旋转；合并裤层整体移开诊断，非双腿独立通过',
        'visual_verdict':'not_reviewed','digest':evidence_digest(folder)}
    atomic_json(folder/'inspection.json',report);return report

def inspect_template(folder,node=None):
    """Register unchanged-aspect candidate pieces to the approved template.

    Large shape mismatches block acceptance even if the original mesh could hide
    them. Original template images are never substituted for missing candidates.
    """
    from .rig import pack
    from tools.render_rig_evidence import render
    folder=Path(folder).resolve();spec=json.loads((folder/'template.json').read_text())
    manifest=json.loads((folder/'parts.json').read_text());parts=[];blockers=[]
    tiles=Image.new('RGB',(1200,900),'#e1e8f0')
    for i,(slot,row) in enumerate(zip(spec['slots'],manifest['parts'])):
        if slot['name']!=row['name']:raise UserError('部件顺序与模板不符')
        name=slot['name'];im=read_image((folder/row['file']).read_bytes())
        ref=read_image((folder/'reference-parts'/(name+'.png')).read_bytes());box=ref.getbbox()
        factor=min((box[2]-box[0])/im.width,(box[3]-box[1])/im.height)
        fitted=im.resize((max(1,round(im.width*factor)),max(1,round(im.height*factor))),Image.Resampling.LANCZOS)
        canvas=Image.new('RGBA',ref.size);canvas.alpha_composite(fitted,(box[0]+((box[2]-box[0])-fitted.width)//2,box[1]))
        parts.append(({'name':name},canvas))
        preview=im.copy();preview.thumbnail((360,400));x=i%3*400;y=i//3*450
        tiles.paste(preview,(x+(400-preview.width)//2,y+32),preview);ImageDraw.Draw(tiles).text((x+10,y+10),name,fill='black')
        if row['touches_cell_edge']:blockers.append(name+' 接触格边，可能被裁断')
        if row['silhouette_iou_diagnostic']<.88 or row['aspect_ratio_relative_error']>.08:
            blockers.append(name+' 轮廓或宽高比偏离模板，旧网格可能裁掉新图，须重新对位')
    tiles.save(folder/'parts.png');probe=folder/'probe';probe.mkdir(exist_ok=True)
    files,_=pack(parts)
    for name,data in files.items():(probe/name).write_bytes(data)
    executable=node or os.getenv('SPINE_AGENT_NODE') or shutil.which('node')
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
    if not executable and bundled.is_file():executable=str(bundled)
    if not executable:raise UserError('未找到 Node.js，不能执行真实模板组装检查')
    root=Path(__file__).resolve().parents[1]
    subprocess.run([executable,str(root/'tools/probe_parts_template.cjs'),str(folder)],check=True,capture_output=True,timeout=60)
    poses=json.loads((probe/'poses.json').read_text())
    xy=np.array([coord for pose in poses for g in pose['geometry'] for coord in g['xy']]).reshape(-1,2)
    view=[*xy.min(axis=0),*xy.max(axis=0)];panels=[]
    for pose in poses:
        im=render(pose,probe,view,(500,850));panels.append(im)
    panels[0].save(folder/'assembly.png');spread=Image.new('RGB',(500*len(panels),880),'#e1e8f0')
    for i,(im,pose) in enumerate(zip(panels,poses)):
        spread.paste(im,(500*i,30));ImageDraw.Draw(spread).text((500*i+12,8),pose['name'],fill='black')
    spread.save(folder/'spread.png')
    report={'route':'template','constraint_mode':'reference-image-only','blockers':blockers,
        'alpha_clipped_to_template':False,'generated_texture_registration':'isotropic-fit-to-template',
        'scope':'原模板真实网格静态试拼及关节正反摆开；不代表新动画验收',
        'visual_verdict':'not_reviewed','digest':evidence_digest(folder)}
    atomic_json(folder/'inspection.json',report);return report
