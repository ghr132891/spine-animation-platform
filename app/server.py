from __future__ import annotations
import argparse
import base64
import io
import json
import mimetypes
import re
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.version import BUILD
from app.core import Project, UserError, decode_files, png_bytes, safe_name, MAX_BYTES,atomic_json,read_image,straight
from app.provider import Matrix,DEFAULT_MODEL,failure_details,ProviderError
from app.image_provider import ImageProvider
from app.ai import reference_for,fit_result
from app.jobs import JobHistory
from app.rig import RigProject
from app.character import CharacterProject
from app.part_prompts import SHEETS,sheet_spec,part_instructions,generation_plan,character_prompt,part_prompt,view_prompt,PROMPT_VERSION
from app.production_reference import REFERENCE_PROFILE
from app.image_preflight import selected_sheets
from app.automation import AutoReskin
from app.rig_automation import RigAutomation
from app.agent_tasks import AgentTasks
from app.web_workflows import WebWorkflows,WebError
from app.parts_lab import PartsLab

STORAGE=ROOT/'storage'
PROJECTS={}
PROJECT_LOCK=threading.RLock()
RIGS={}
MATRIX=ImageProvider(STORAGE)
JOBS=JobHistory(STORAGE,MATRIX)
CHARACTERS={}
CHARACTER_JOBS=JobHistory(STORAGE,MATRIX,'characters')
PARTS_LAB=PartsLab(STORAGE,MATRIX)


def get_character(id):
    if not re.fullmatch(r'[a-f0-9]{32}',id):raise UserError('单图项目编号无效')
    with PROJECT_LOCK:
        if id not in CHARACTERS:
            path=STORAGE/'characters'/id
            if not (path/'character.json').exists():raise UserError('单图项目不存在')
            CHARACTERS[id]=CharacterProject(path)
        return CHARACTERS[id]


def get_project(project_id):
    if not re.fullmatch(r'[a-f0-9]{32}',project_id):
        raise UserError('项目编号无效')
    with PROJECT_LOCK:
        if project_id not in PROJECTS:
            path=STORAGE/'projects'/project_id
            if not (path/'project.json').exists():
                raise UserError('项目不存在，请重新导入')
            PROJECTS[project_id]=Project(path)
        return PROJECTS[project_id]


def new_project(files,label):
    project_id=uuid.uuid4().hex
    p=Project(STORAGE/'projects'/project_id,files,label)
    PROJECTS[p.id]=p
    return p


def get_rig(rig_id):
    if not re.fullmatch(r'[a-f0-9]{32}',rig_id):raise UserError('骨骼项目编号无效')
    with PROJECT_LOCK:
        if rig_id not in RIGS:
            path=STORAGE/'rigs'/rig_id
            if not (path/'rig.json').exists():raise UserError('骨骼项目不存在')
            RIGS[rig_id]=RigProject(path)
        return RIGS[rig_id]


AUTOMATION=AutoReskin(STORAGE,MATRIX,JOBS,get_project)
RIG_AUTOMATION=RigAutomation(STORAGE,get_rig)
AGENT_TASKS=AgentTasks(STORAGE,ROOT,{'project':get_project,'rig':get_rig,'character':get_character},RIG_AUTOMATION)
WEB_WORKFLOWS=WebWorkflows(AGENT_TASKS)


def character_reference(c, supplied=None, publish=True):
    if supplied:
        return MATRIX.reference_url(supplied)
    cached=MATRIX.cached_reference(c.image())
    if cached:return MATRIX.ensure_reference(c.image())['url'] if publish else cached['url']
    from app.core import sha
    for path in (c.directory/'jobs').glob('*.json'):
        job=json.loads(path.read_text())
        image=path.with_suffix('.png')
        if job.get('candidate_ready') and job.get('mode')=='character' and job.get('image_urls') and image.exists():
            if sha(png_bytes(read_image(image.read_bytes())))==c.meta['image_id']:
                if str(job['image_urls'][0]).startswith('https://'):return MATRIX.reference_url(job['image_urls'][0])
    return MATRIX.ensure_reference(c.image())['url'] if publish else None


def character_reference_info(c):
    reference=None
    try:reference=character_reference(c,publish=False)
    except UserError:pass
    return {'id':c.id,'revision':c.meta['revision'],'image_id':c.meta.get('image_id'),
            'reference_url':reference,'cached':bool(reference),
            'image_url':'/api/characters/'+c.id+'/image','upload_service':MATRIX.reference_status()['provider'],
            'upload_ready':MATRIX.reference_status()['ready']}


def record_submission_failure(job,error):
    failure=failure_details(error,operation='submit')
    job.update(failure=failure,status='failed' if failure['submission_state']=='rejected' else 'submission_unknown',
               error=failure['message']+' '+failure['next_action'])
    return job


def webpage_route(path,qs):
    """Use the simple UI by default, preserving explicit editing entry points."""
    advanced=qs.get('advanced',[''])[0]=='1'
    if path=='/rig.html' and not advanced and not qs.get('source'):
        query={key:values for key,values in qs.items() if key!='advanced'}
        suffix=urllib.parse.urlencode(query,doseq=True)
        return 'redirect','/result.html'+('?' + suffix if suffix else '')
    if path in ('/','/index.html') and not qs.get('project') and not advanced:
        return 'file','auto.html'
    return 'file','index.html' if path=='/' else path.lstrip('/')


class Handler(BaseHTTPRequestHandler):
    def log_message(self,format,*args):
        # No request bodies, auth headers, or credentials in logs.
        if args and str(args[0]).startswith(('GET /api/','POST /api/')):
            return
        super().log_message(format,*args)

    def send(self,data,status=200,mime='application/json',filename=None,headers=None):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if filename:self.send_header('Content-Disposition',f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}")
        for key,value in (headers or {}).items():self.send_header(key,value)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:self.get()
        except ProviderError as e:self.send({'error':str(e),'failure':e.details},503)
        except WebError as e:self.send({'error':str(e)},e.status)
        except (UserError,KeyError,IndexError) as e:self.send({'error':str(e)},400)
        except (ConnectionResetError,BrokenPipeError):pass
        except Exception:
            self.send({'error':'读取失败，请重新载入项目'},500)

    def get(self):
        url=urllib.parse.urlparse(self.path)
        path=urllib.parse.unquote(url.path)
        qs=urllib.parse.parse_qs(url.query)
        parts=path.strip('/').split('/')
        if parts[:2]==['api','workflows']:
            return self.workflow(parts,qs=qs)
        if parts[:3]==['api','automation','agent']:
            if not AGENT_TASKS.authorized(self.headers.get('Authorization')):return self.send({'error':'Agent 接口需要 Bearer 访问令牌'},401)
            if len(parts)==6 and parts[:4]==['api','automation','agent','review-bundles']:
                from app.review_bundle import read_file
                return self.send(read_file(ROOT,parts[4],parts[5]),mime='image/png')
            if parts==['api','automation','agent','sources']:
                return self.send({'access':{'requires_token':AGENT_TASKS.require_token,'mode':'token' if AGENT_TASKS.require_token else 'open'},'projects':AGENT_TASKS.api('/api/projects'),'rigs':AGENT_TASKS.api('/api/rigs'),'characters':AGENT_TASKS.api('/api/characters'),
                                  'capabilities':['texture-reskin','rig-operations','same-runtime-animation-replacement','mapped-local-bone-curves','character-walk','runtime-validation','runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-sole-contact','continuous-limb-prompts','pixel-contact-diagnostics','smooth-gait-curves','simple-result-view','concise-art-prompts','safe-generation-failures','character-asset-sheet-first','side-walk-preparation','joint-reviewed-assembly','polygon-part-crops','neck-seam-closeups','scoped-texture-repair','scoped-material-finish','contrast-material-review','long-hem-secondary-motion','turnaround-reference-review','multi-region-closeups','asset-integrity-review','costume-completion','strict-layer-anchors','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling','platform-review-bundle','scoped-edge-cleanup','per-side-orientation-review','natural-walk-posture','durable-task-resume','runtime-sample-cache','grouped-workflow-history','sampled-motion-vision','motion-preview-4-32','gait-phase-diagnostics','rig-supplemental-parts','codex-image-preflight','matrix-reference-view-preparation','matrix-gpt-image-2.5-flare','matrix-reference-cache','server-reference-upload','temporary-reference-tunnel','configurable-image-provider','openai-images-api','reviewed-occlusion-overlay','assembly-source-snapshot'],
                                  'garment_templates':{'version':'garment-template-v1','types':['trousers','shorts','skirt','split-robe','armor'],'action':'garment-template','automatic_fit_on_import':True,'directional_completion':True},'assembly_execution':{'version':'assembly-execution-v2','default_on_complete_import':True,'action':'prepare-assembly','reuse_complete_sources':True,'automatic_source_anchors':True,'preserve_unrelated_repairs':True,'atomic_import_and_repair':True,'integrated_backpack_review':True,'prebind_overlays_exported':True,'preserve_order_on_guide_save':True,'resolve_overlay_constraints':True,'persistent_prebind_evidence':True,'automatic_task_material_preparation':True,'web_material_progress':True,'complete_generation_defaults':True,'edge_evidence':'edge-evidence-v1','material_animation_handoff':True,'automatic_build_after_assessment':True,'content_bound_animation_transition':True,'late_generated_materials_auto_bind':True,'durable_dynamic_review_queue':True,'runtime_stress_before_build':True,'preserved_assets_keep_detail_review':True},'canvas_layers':{'contract':'canvas-layers-v1','coordinate_system':'source-pixels-y-down','actions':['partition-canvas','install-canvas-layers','reconstruction','review-reconstruction'],'visible_pixels_protected':True,'formal_build_requires_review':True,'complete_silhouette_field':'completed_image','overlap_allowed':True,'spread_review':'layer-overlap'},'image_generation':{'model':MATRIX.status()['model'],'provider':MATRIX.status().get('provider','matrix'),'reference_transport':MATRIX.status()['reference_transport'],'prompt_version':PROMPT_VERSION,'prompt_style':'concise-positive-artwork','default_preparation':'asset-sheet','generation_budget':{'additional_default':8,'baseline':{'turnaround':1,'character-assets':1},'zero_forbids_all':True},'preparation_contract':'side-parts-v2','walk_camera':'side-or-three-quarter-side','part_review_required':True,'integrity_contract':'asset-integrity-v1','garment_review':{'contract':'garment-occlusion-v1','scope':'generated-long-costume','walk_leg_frames':32,'idle_leg_frames':8,'isolated_is_diagnostic_only':True},'costume_completion_sheet':'costume','missing_layer_anchor':'reject','numeric_checks_certify_visual_quality':False,'seam_review':{'contract':'neck-seam-v1','frames_per_animation':8,'requires_visual_review':True,'scoped_pixel_repair':True,'material_finish':True,'contrast_backgrounds':['light','dark']},'connection_review':{'contract':'assembly-connections-v3','walk_head_frames':32,'anchors':['head','__hip__','__back__'],'walk_hip_frames':32,'walk_back_frames':32,'requires_visual_review':True},'detail_review':{'contract':'articulation-v1','anchors':['head','torso','l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg','__hair__'],'frames_per_animation_per_anchor':8},'turnaround':{'contract':'three-view-v1','sheet':'turnaround','views':['front','side','back'],'reference_only':True,'shares_generation_budget':True},'asset_sheet':{'sheet':'character-assets','requires_reviewed_layout':True,'source_layout_reuse':True},'failure_policy':{'review_rejection':'stop','unknown_submission':'verify-existing','query_or_download_error':'retry-same-task','visual_quality':'one-budgeted-revision'}},
                                  'production_reference':REFERENCE_PROFILE,
                                  'ui':{'create':'/auto.html?new=1','result':'/result.html?rig={id}','editor':'/rig.html?rig={id}&advanced=1'},
                                  'reference_upload':MATRIX.reference_status(),
                                  'limitations':['不支持任意骨架自动重定向或二进制 SKEL 骨骼编辑','多帧视觉检查不保证所有帧或所有皮肤的美术质量']})
            if len(parts)==6 and parts[:4]==['api','automation','agent','tasks'] and parts[5]=='retry-stage':
                from app.stages import retry_stage
                return self.send(retry_stage(AGENT_TASKS,parts[4],data.get('stage')))
            if parts==['api','automation','agent','tasks']:
                return self.send([AGENT_TASKS.summary(p.parent.name) for p in sorted(AGENT_TASKS.tasks.glob('*/task.json'),key=lambda p:p.stat().st_mtime,reverse=True)])
            if len(parts)==5 and parts[3]=='tasks':return self.send(AGENT_TASKS.summary(parts[4]))
            if len(parts)==6 and parts[3]=='tasks' and parts[5]=='download':return self.send(AGENT_TASKS.download(parts[4]),mime='application/zip',filename='spine-agent-'+parts[4]+'.zip')
            return self.send({'error':'未找到 Agent 接口'},404)
        if path=='/api/version':return self.send(BUILD)
        if path=='/api/health':return self.send({'ok':True,'app':'spine-reskin'})
        if len(parts)>=3 and parts[:2]==['api','parts-lab']:
            if len(parts)==3:return self.send(PARTS_LAB.status(parts[2]))
            if parts[3]=='files':
                name='/'.join(parts[4:])
                return self.send(PARTS_LAB.file(parts[2],name),mime=mimetypes.guess_type(name)[0] or 'application/octet-stream')
            raise UserError('未找到部件试验接口')
        if path=='/api/provider':return self.send(MATRIX.status())
        if path=='/api/reference-hosting':return self.send(MATRIX.reference_status())
        if path=='/api/automation/openapi.json':
            return self.send(json.loads((ROOT/'agent/openapi.json').read_text()))
        if path=='/api/automation/reskin/tasks':
            return self.send([AUTOMATION.summary(p.parent.name) for p in sorted(AUTOMATION.root.glob('*/task.json'),key=lambda p:p.stat().st_mtime,reverse=True)])
        if len(parts) in (5,6) and parts[:4]==['api','automation','reskin','tasks']:
            if len(parts)==5:return self.send(AUTOMATION.summary(parts[4]))
            if parts[5]=='download':return self.send(AUTOMATION.download(parts[4]),mime='application/zip',filename='spine-reskin-'+parts[4]+'.zip')
            return self.send({'error':'未找到自动换皮接口'},404)
        if path=='/api/characters':
            values=[]
            for f in (STORAGE/'characters').glob('*/character.json'):
                m=json.loads(f.read_text());values.append({'id':m['id'],'name':m['name'],'revision':m['revision'],'versions':len(m['versions']),'updated_at':m.get('updated_at',m['created_at'])})
            return self.send(sorted(values,key=lambda v:v['updated_at'],reverse=True))
        if len(parts)>=3 and parts[:2]==['api','characters']:
            c=get_character(parts[2])
            with c.lock:
                if len(parts)==3:return self.send(c.summary())
                action=parts[3]
                if action=='reference':return self.send(character_reference_info(c))
                if action=='image':return self.send(png_bytes(c.image()),mime='image/png')
                if action=='cutout':return self.send(png_bytes(c.cutout()),mime='image/png')
                if action=='review':return self.send(c.review())
                if action=='prebind':return self.send(c.prebind_evidence()[0])
                if action=='edge-review':return self.send(c.edge_evidence()[0])
                if action=='edge-review-image':return self.send(png_bytes(c.edge_evidence()[1]),mime='image/png')
                if action=='stress-image':
                    from app.assembly_stress import panel
                    return self.send(panel(c,qs.get('panel',[''])[0],qs.get('digest',[None])[0])[2],mime='image/png')
                if action in ('prebind-parts','prebind-assembly','prebind-spread'):
                    from app.image_evidence import select
                    options={'resolution':'original'}
                    for key in ('pose','zoom','crop'):
                        if key in qs:
                            try:options[key]=json.loads(qs[key][0])
                            except ValueError:raise UserError('检查图参数无效')
                    return self.send(png_bytes(select(c.prebind_evidence()[1][action.removeprefix('prebind-')],options,action)[0]),mime='image/png')
                if action=='layer-overlap':return self.send(c.layer_overlap()[0])
                if action=='layer-overlap-image':return self.send(png_bytes(c.layer_overlap()[1]),mime='image/png')
                if action=='reconstruction':return self.send(c.reconstruction()[0])
                if action=='reconstruction-image':return self.send(png_bytes(c.reconstruction()[2 if qs.get('view',[''])[0]=='diff' else 1]),mime='image/png')
                if action=='assembly':return self.send(png_bytes(c.assembly()),mime='image/png')
                if action=='parts':return self.send([{'name':p['name'],'label':p['label'],'box':p['box'],'pixel_count':p['pixel_count'],'complete':p.get('complete',False)} for p in c.parts()])
                if action=='part':
                    candidates=c.base_parts() if qs.get('unadjusted',['0'])[0]=='1' else c.parts()
                    p=next(p for p in candidates if p['name']==qs.get('name',[''])[0])
                    image=p['image']
                    if qs.get('cuff',['0'])[0]=='1':
                        if p['name'] not in ('l-foot','r-foot'):raise UserError('只有脚部支持鞋口前缘')
                        from app.ankle_join import cuff_image
                        image=cuff_image(image)
                    return self.send(png_bytes(image),mime='image/png')
                if action=='candidate':
                    job=CHARACTER_JOBS.read(c.id,qs.get('id',[''])[0])
                    if not job.get('candidate_ready'):raise UserError('候选图尚未完成')
                    return self.send(CHARACTER_JOBS.path(c.id,job['id']).with_suffix('.png').read_bytes(),mime='image/png')
                if action=='export':return self.send(c.export(STORAGE,qs.get('version',[''])[0]),mime='application/zip',filename=c.meta['name']+'-walk.zip')
            return self.send({'error':'未找到单图接口'},404)
        if path=='/api/rigs':
            values=[]
            for f in (STORAGE/'rigs').glob('*/rig.json'):
                meta=json.loads(f.read_text())
                values.append({'id':f.parent.name,'name':meta['name'],'revision':meta['revision'],'source_id':meta['state']['source_id'],'updated_at':f.stat().st_mtime})
            return self.send(sorted(values,key=lambda v:v['updated_at'],reverse=True))
        if len(parts)>=3 and parts[:2]==['api','rigs']:
            rig=get_rig(parts[2])
            with rig.lock:
                if len(parts)==3:return self.send(rig.summary())
                action=parts[3]
                if action=='preview':return self.send(rig.raw_data(qs.get('original',['0'])[0]=='1'))
                if action=='validate':return self.send(rig.validate())
                if action=='export':return self.send(rig.export(),mime='application/zip',filename=rig.meta['name']+'-rig.zip')
                if action=='part':return self.send(rig.blob(rig.part(qs.get('id',[''])[0])),mime='image/png',filename='part.png' if 'download' in qs else None)
            return self.send({'error':'未找到骨骼接口'},404)
        if path=='/api/projects':
            entries=[]
            for f in sorted((STORAGE/'projects').glob('*/project.json'),key=lambda f:f.stat().st_mtime,reverse=True):
                meta=json.loads(f.read_text())
                history_path=f.parent/'history/index.json'
                updated_at=max(f.stat().st_mtime,history_path.stat().st_mtime if history_path.exists() else 0)
                entries.append({'id':f.parent.name,'name':meta['label'],'revision':meta['revision'],'updated_at':updated_at,'job_count':len(list((f.parent/'jobs').glob('*.json')))})
            entries.sort(key=lambda p:p['updated_at'],reverse=True)
            return self.send(entries)
        if len(parts)>=3 and parts[:2]==['api','projects']:
            p=get_project(parts[2])
            with p.lock:
                if len(parts)==3:return self.send(p.summary())
                action=parts[3]
                if action=='history':return self.send(p.history())
                if action=='history-thumbnail':
                    entry,pages=p.history_pages(qs.get('id',[''])[0])
                    index=p.by_region[entry['changed'][0]]['page'] if entry['changed'] else 0
                    page=p.pages[index];im=straight(read_image(pages[page['file']]),page['pma']);im.thumbnail((140,100))
                    return self.send(png_bytes(im),mime='image/png')
                if action=='part':
                    original=qs.get('original',['0'])[0]=='1'
                    region=qs.get('id',[''])[0]
                    im=p.part(region,original)
                    if 'thumb' in qs:im.thumbnail((100,100))
                    return self.send(png_bytes(im),mime='image/png',filename=region+'.png' if 'download' in qs else None)
                if action=='reference':
                    ref,_=reference_for(p.part(qs.get('id',[''])[0],True))
                    return self.send(ref,mime='image/png',filename='reference.png')
                if action=='page':
                    index=int(qs.get('index',['0'])[0]);im=p.page_image(index,qs.get('original',['0'])[0]=='1')
                    return self.send(png_bytes(im),mime='image/png',filename='page.png' if 'download' in qs else None)
                if action=='preview':return self.send(p.raw_data(qs.get('original',['0'])[0]=='1'))
                if action=='validate':return self.send(p.validate())
                if action=='export':return self.send(p.export(),mime='application/zip',filename=p.label+'-reskin.zip')
                if action=='parts':
                    import zipfile
                    out=io.BytesIO()
                    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                        manifest=[]
                        for r in p.regions:
                            z.writestr(r['id']+'.png',png_bytes(p.part(r['id'],True)))
                            manifest.append({**r,'replacement_file':r['id']+'.png'})
                        z.writestr('parts-map.json',json.dumps(manifest,indent=2,ensure_ascii=False))
                    return self.send(out.getvalue(),mime='application/zip',filename='spine-parts.zip')
                if action=='candidate':
                    job_id=qs.get('id',[''])[0]
                    if not re.fullmatch(r'[a-f0-9-]{16,64}',job_id):raise UserError('任务编号无效')
                    job=json.loads((p.directory/'jobs'/(job_id+'.json')).read_text())
                    data=(p.directory/'jobs'/(job_id+'.png')).read_bytes()
                    fitted=fit_result(data,job['layout'],p.part(job['region'],True))
                    fitted.putalpha(p.part(job['region'],True).getchannel('A'))
                    return self.send(png_bytes(fitted),mime='image/png',filename='ai-part.png' if 'download' in qs else None)
                if action=='jobs':
                    if 'id' in qs:return self.send(JOBS.read(p.id,qs['id'][0]))
                    return self.send(p.summary()['jobs'])
        page_kind,relative=webpage_route(path,qs)
        if page_kind=='redirect':return self.send(b'',302,mime='text/plain',headers={'Location':relative})
        full=(ROOT/'web'/relative).resolve()
        if not full.is_relative_to((ROOT/'web').resolve()) or not full.is_file():return self.send({'error':'未找到页面'},404)
        return self.send(full.read_bytes(),mime=mimetypes.guess_type(full.name)[0] or 'application/octet-stream')

    def do_POST(self):
        try:
            if self.headers.get('X-Spine-Local')!='1':return self.send({'error':'请求需携带 X-Spine-Local: 1'},403)
            length=int(self.headers.get('Content-Length','0'))
            if length>MAX_BYTES*2:return self.send({'error':'请求过大'},413)
            data=json.loads(self.rfile.read(length))
            return self.post(data)
        except ProviderError as e:self.send({'error':str(e),'failure':e.details},503)
        except WebError as e:self.send({'error':str(e)},e.status)
        except (UserError,ValueError,KeyError,IndexError) as e:self.send({'error':str(e)},400)
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:self.send({'error':'操作失败，原始文件已保留，请刷新后重试'},500)

    def post(self,data):
        parts=urllib.parse.urlparse(self.path).path.strip('/').split('/')
        if parts[:2]==['api','parts-lab']:
            import ipaddress
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send({'error':'部件路线试验目前由服务器本机管理员执行'},403)
            if len(parts)==3 and parts[2]=='create':
                # Fresh instance protects the currently loaded project's page data.
                project=get_project(str(data.get('template_project_id','')))
                return self.send(PARTS_LAB.create(get_character(str(data.get('character_id',''))),Project(project.directory),data))
            if len(parts)==4 and parts[3]=='run':return self.send(PARTS_LAB.start(parts[2],data.get('route')),202)
            if len(parts)==4 and parts[3]=='review':return self.send(PARTS_LAB.review(parts[2],data))
            if len(parts)==4 and parts[3]=='probe-bones':return self.send(PARTS_LAB.set_probe_bones(parts[2],data))
            if len(parts)==4 and parts[3]=='inspect':return self.send(PARTS_LAB.inspect(parts[2],data.get('route')))
            raise UserError('未找到部件试验操作')
        if parts==['api','provider','configure']:
            import ipaddress
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send({'error':'生图服务配置只允许管理员在服务器本机保存'},403)
            return self.send(MATRIX.configure(data))
        if parts==['api','reference-hosting','check']:
            import ipaddress
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send({'error':'参考图检测只允许管理员在服务器本机执行'},403)
            if MATRIX.status()['reference_transport']!='https-url':
                return self.send({'message':'当前 API 直接上传图片，无需公网参考图通道','required':False})
            return self.send(MATRIX.check_reference_channel())
        if parts==['api','reference-hosting','tunnel']:
            import ipaddress
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send({'error':'临时图片通道只允许管理员在服务器本机启用或关闭'},403)
            action=data.get('action')
            if action=='enable':return self.send(MATRIX.tunnel.enable())
            if action=='disable':return self.send(MATRIX.tunnel.disable())
            raise UserError('临时通道操作必须为 enable 或 disable')
        if parts==['api','reference-hosting','connect']:
            import ipaddress
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send({'error':'图片存储配置只允许平台管理员在服务器本机设置页保存'},403)
            return self.send(MATRIX.configure_reference_hosting(data))
        if parts[:2]==['api','workflows']:return self.workflow(parts,data=data)
        if parts[:3]==['api','automation','agent']:
            if not AGENT_TASKS.authorized(self.headers.get('Authorization')):return self.send({'error':'Agent 接口需要 Bearer 访问令牌'},401)
            if len(parts)==6 and parts[:4]==['api','automation','agent','tasks'] and parts[5]=='retry-stage':
                from app.stages import retry_stage
                return self.send(retry_stage(AGENT_TASKS,parts[4],data.get('stage')))
            if parts==['api','automation','agent','tasks']:return self.send(AGENT_TASKS.create(data),202)
            if parts==['api','automation','agent','batch']:return self.send(AGENT_TASKS.batch(data),202)
            if parts==['api','automation','agent','refine']:return self.send(AGENT_TASKS.refine(data),202)
            if parts==['api','automation','agent','review-bundle']:
                result=AGENT_TASKS.review_bundle(data.get('kind'),data.get('id'),data)
                if data.get('panel') is None:return self.send(result)
                bundle,panel,png=result
                return self.send({'content':[{'type':'text','text':json.dumps({**panel,'resource_sha256':bundle['resource_sha256']})},{'type':'image','mimeType':'image/png','data':base64.b64encode(png).decode()}]})
            if parts==['api','automation','agent','contact']:return self.send(AGENT_TASKS.contact_target(data.get('kind'),data.get('id'),data))
            if parts==['api','automation','agent','closeup']:
                report,preview=AGENT_TASKS.closeup_target(data.get('kind'),data.get('id'),data)
                return self.send({'content':[{'type':'text','text':json.dumps(report)}, {'type':'image','mimeType':'image/png','data':base64.b64encode(preview).decode()}]})
            if len(parts)==6 and parts[3]=='tasks':
                if parts[5]=='result-view':return self.send(AGENT_TASKS.result_view(parts[4],data))
                if parts[5]=='cancel':return self.send(AGENT_TASKS.cancel(parts[4]))
                if parts[5]=='resume':return self.send(AGENT_TASKS.resume(parts[4],data))
                if parts[5]=='tools':return self.send(AGENT_TASKS.tool(parts[4],data['name'],data.get('arguments',{})))
            return self.send({'error':'未找到 Agent 操作'},404)
        if parts==['api','automation','reskin','plan']:return self.send(AUTOMATION.plan(data))
        if parts==['api','automation','reskin','tasks']:return self.send(AUTOMATION.create(data),202)
        if len(parts)==6 and parts[:4]==['api','automation','reskin','tasks']:
            if parts[5]=='cancel':return self.send(AUTOMATION.cancel(parts[4]))
            if parts[5]=='resume':return self.send(AUTOMATION.resume(parts[4],data))
            return self.send({'error':'未找到自动换皮操作'},404)
        if parts==['api','characters','create']:
            c=CharacterProject.create(STORAGE,data.get('name',''));m=dict(c.meta);m['stress_contract']='runtime-assembly-stress-v1';c.save(m);CHARACTERS[c.id]=c
            return self.send(c.summary())
        if len(parts)==4 and parts[:2]==['api','characters']:
            c=get_character(parts[2]);action=parts[3]
            if action=='reference':
                with c.lock:
                    c.check(data['revision'])
                    if data.get('action')=='ensure':MATRIX.ensure_reference(c.image())
                    elif data.get('action','register')=='register':MATRIX.register_reference(c.image(),data.get('url',''))
                    else:raise UserError('参考图操作无效')
                    return self.send(character_reference_info(c))
            if action=='stress-test':
                from app.assembly_stress import prepare
                return self.send(prepare(AGENT_TASKS,c))
            if action=='generation-plan':
                with c.lock:
                    from app.garment_templates import generation_request
                    prepared,template=generation_request(c,data) if data.get('mode')=='complete-parts' else (data,None)
                    return self.send({**generation_plan(prepared,c.meta['settings']['facing']),'garment_template':template,'model':MATRIX.status()['model'],'reference_transport':MATRIX.status()['reference_transport']})
            if action=='generate-parts':
                from app.part_prompts import complete_generation_request
                if data.get('sheets') is None:
                    saved=[json.loads(p.read_text()) for p in (c.directory/'jobs').glob('*.json')]
                    saved=[j for j in saved if j.get('generation_nonce')==data.get('nonce') and j.get('sheet')]
                    if saved and all(j['sheet'] in ('body','arms','legs') for j in saved):data={**data,'sheets':['body','arms','legs'],'body_layout':data.get('body_layout',saved[0].get('body_layout','separate'))}
                from app.garment_templates import generation_request
                data,template=generation_request(c,data)
                nonce=str(data.get('nonce',''))
                if not re.fullmatch('[a-f0-9-]{16,48}',nonce):raise UserError('生成任务编号无效')
                sheets=selected_sheets(data.get('sheets'))
                include_back=data.get('include_back',True)
                body_layout=data.get('body_layout','separate')
                # Validate every requested layout/note before publishing a
                # reference or reserving any paid generation.
                specs={s:sheet_spec(s,include_back,body_layout) for s in sheets}
                notes=data.get('_garment_notes') or {s:part_instructions(data,s) for s in sheets}
                prompts={s:part_prompt(s,notes[s],c.meta['settings']['facing'],include_back,body_layout,template['type'] if template else None) for s in sheets}
                existing=[CHARACTER_JOBS.path(c.id,uuid.uuid5(uuid.NAMESPACE_URL,'spine-part:'+nonce+':'+sheet).hex) for sheet in sheets]
                saved=[json.loads(path.read_text()) for path in existing if path.exists()]
                if len(saved)==len(existing) or any(job.get('status')=='submission_unknown' or job.get('failure',{}).get('stop_generation') for job in saved):
                    return self.send(saved)
                with c.lock:
                    c.check(data['revision']);image_id=c.meta['image_id'];facing=c.meta['settings']['facing']
                    if template:
                        meta=dict(c.meta);meta['garment_template']=template;c.save(meta)
                    if 'character-assets' in sheets and c.meta.get('prebind_contract')!='complete-assets-v1':
                        meta=dict(c.meta);meta['prebind_contract']='complete-assets-v1';c.save(meta)
                    prompts={s:part_prompt(s,notes[s],facing,include_back,body_layout,template['type'] if template else None) for s in sheets}
                    reference_url=character_reference(c,data.get('reference_url'))
                sheets=selected_sheets(data.get('sheets'))
                result=[]
                for sheet in sheets:
                    spec=specs[sheet];instructions=notes[sheet]
                    # Sheet names are not hexadecimal. Derive a stable valid ID
                    # so job paths and candidate-image tools accept the same ID.
                    job_id=uuid.uuid5(uuid.NAMESPACE_URL,'spine-part:'+nonce+':'+sheet).hex
                    path=CHARACTER_JOBS.path(c.id,job_id);path.parent.mkdir(exist_ok=True)
                    with c.lock:
                        if path.exists():result.append(json.loads(path.read_text()));continue
                        job={'id':job_id,'generation_nonce':nonce,'status':'submission_unknown','model':MATRIX.status()['model'],'provider':MATRIX.status().get('provider','matrix'),'created_at':time.time(),'prompt':prompts[sheet],'prompt_version':PROMPT_VERSION,'instructions':instructions,'garment_template':template,'include_back':include_back,'mode':'complete-parts','sheet':sheet,'reference_only':spec.get('reference_only',False),'body_layout':body_layout,'groups':spec.get('groups',{}),'parts':spec['parts'],'columns':spec['columns'],'rows':spec['rows'],'requires_reviewed_layout':spec.get('requires_reviewed_layout',False),'source_image_id':image_id};atomic_json(path,job)
                    try:gen_id=MATRIX.generate_part_sheet(sheet,reference_url,instructions,facing,include_back,body_layout,template['type'] if template else None)
                    except UserError as e:
                        record_submission_failure(job,e);atomic_json(path,job);result.append(job)
                        # A review rejection or ambiguous POST must not trigger
                        # later paid groups from this request.
                        if job['failure']['stop_generation'] or job['status']=='submission_unknown':break
                        continue
                    job.update(gen_id=gen_id,status='pending',**MATRIX.job_metadata(gen_id));atomic_json(path,job);result.append(job)
                CHARACTER_JOBS.wake.set();return self.send(result)
            if action=='generate-view':
                prompt=view_prompt(data.get('target_view'),data.get('target_actions'),data.get('instructions',''))
                nonce=str(data.get('nonce',''));path=CHARACTER_JOBS.path(c.id,nonce);path.parent.mkdir(exist_ok=True)
                with c.lock:
                    if path.exists():return self.send(json.loads(path.read_text()))
                    c.check(data['revision'])
                    reference=character_reference(c,data.get('reference_url'))
                    job={'id':nonce,'status':'submission_unknown','model':MATRIX.status()['model'],'provider':MATRIX.status().get('provider','matrix'),'created_at':time.time(),'prompt':prompt,'mode':'character','purpose':'animation-view','source_image_id':c.meta['image_id'],'target_view':data['target_view'],'target_actions':data['target_actions']}
                    atomic_json(path,job)
                try:gen_id=MATRIX.generate_view(prompt,reference)
                except UserError as e:
                    record_submission_failure(job,e);atomic_json(path,job);return self.send(job)
                job.update(gen_id=gen_id,status='pending',**MATRIX.job_metadata(gen_id));atomic_json(path,job);CHARACTER_JOBS.wake.set();return self.send(job)
            if action=='generate':
                prompt=str(data.get('prompt','')).strip()
                if not 5<=len(prompt)<=2000:raise UserError('请填写 5–2000 字的角色描述')
                nonce=str(data.get('nonce',''))
                path=CHARACTER_JOBS.path(c.id,nonce);path.parent.mkdir(exist_ok=True)
                with c.lock:
                    if path.exists():return self.send(json.loads(path.read_text()))
                    c.check(data['revision']);job={'id':nonce,'status':'submission_unknown','model':MATRIX.status()['model'],'provider':MATRIX.status().get('provider','matrix'),'created_at':time.time(),'prompt':character_prompt(prompt),'description':prompt,'prompt_version':PROMPT_VERSION,'mode':'character'};atomic_json(path,job)
                try:gen_id=MATRIX.generate_character(prompt)
                except UserError as e:
                    record_submission_failure(job,e);atomic_json(path,job);return self.send(job)
                job.update(gen_id=gen_id,status='pending',**MATRIX.job_metadata(gen_id));atomic_json(path,job);CHARACTER_JOBS.wake.set();return self.send(job)
            if action=='refresh-jobs':CHARACTER_JOBS.request_refresh(c.id);return self.send({'ok':True})
            with c.lock:
                if action=='upload':result=c.set_image(base64.b64decode(data['image'],validate=True),data['revision'])
                elif action=='apply-ai':
                    job=CHARACTER_JOBS.read(c.id,data['job_id'])
                    if not job.get('candidate_ready'):raise UserError('候选图尚未完成')
                    if job.get('mode')=='complete-parts':raise UserError('完整部件候选图须使用部件应用入口')
                    result=c.set_image(CHARACTER_JOBS.path(c.id,job['id']).with_suffix('.png').read_bytes(),data['revision'])
                elif action=='apply-part-sheet':
                    job=CHARACTER_JOBS.read(c.id,data['job_id'])
                    result=c.apply_part_sheet(data,job,read_image(CHARACTER_JOBS.path(c.id,job['id']).with_suffix('.png').read_bytes()))
                elif action=='apply-part-layout':
                    job=CHARACTER_JOBS.read(c.id,data['job_id'])
                    result=c.apply_part_layout(data,job,read_image(CHARACTER_JOBS.path(c.id,job['id']).with_suffix('.png').read_bytes()))
                elif action=='apply-source-layout':result=c.apply_source_layout(data)
                elif action=='reset-part':result=c.reset_part(data)
                elif action=='prebind':return self.send(c.prebind_evidence()[0])
                elif action=='edge-review':return self.send(c.edge_evidence()[0])
                elif action=='finish-parts':result=c.finish_parts(data)
                elif action=='repair-connections':
                    from app.repair_pipeline import repair
                    result=repair(c,data)
                elif action=='review-stress':
                    from app.assembly_stress import review
                    result=review(c,data)
                elif action=='review-prebind':result=c.review_prebind(data)
                elif action=='layer-overlap':return self.send(c.layer_overlap()[0])
                elif action=='review-overlap':result=c.review_overlap(data)
                elif action=='reconstruction':return self.send(c.reconstruction()[0])
                elif action=='partition-canvas':result=c.partition_canvas(data)
                elif action=='install-canvas-layers':result=c.install_canvas_layers(data)
                elif action=='garment-template':result=c.garment_template(data)
                elif action=='prepare-assembly':result=c.prepare_assembly(data)
                elif action=='review-reconstruction':result=c.review_reconstruction(data)
                elif action=='install-source-layers':result=c.install_source_layers(data)
                elif action=='guide':result=c.update(data)
                elif action=='review':result=c.update_review(data)
                elif action=='build':
                    from app.assembly_stress import require as require_stress
                    require_stress(c)
                    result=c.build(STORAGE,data)
                elif action=='restore':result=c.restore(data)
                elif action=='foot-layers':result=c.foot_layers(STORAGE,data)
                else:raise UserError('未知单图操作')
            from app.generation_flow import MATERIAL_ACTIONS
            if action in MATERIAL_ACTIONS:
                from app.animation_transition import notify
                notify(AGENT_TASKS,c)
            return self.send(result)
        if parts==['api','rigs','create']:
            source=get_project(data['source_id'])
            with source.lock:
                source.check_revision(data['revision'])
                rig=RigProject.create(STORAGE/'rigs'/uuid.uuid4().hex,source)
                RIGS[rig.id]=rig
            return self.send(rig.summary())
        if len(parts)==4 and parts[:2]==['api','rigs']:
            rig=get_rig(parts[2]);action=parts[3]
            if action=='contact':return self.send(AGENT_TASKS.contact_target('rig',rig.id,data))
            if action=='source-ai':
                source=get_project(rig.state['source_id']);job=JOBS.read(source.id,data['job_id'])
                if not job.get('candidate_ready'):raise UserError('候选图未完成')
                part=rig.part(data['part']);region=source.by_region.get(job['region'])
                if not region or region['name']!=part['name']:raise UserError('候选图与所选部件不匹配')
                fitted=fit_result((source.directory/'jobs'/(job['id']+'.png')).read_bytes(),job['layout'],source.part(job['region'],True))
                fitted.putalpha(source.part(job['region'],True).getchannel('A'))
                data={**data,'image':base64.b64encode(png_bytes(fitted)).decode()}
            return self.send(rig.mutate(action,data))
        if parts==['api','provider','connect']:return self.send(MATRIX.connect(data.get('key','')))
        if parts==['api','import']:
            files=decode_files(data['files']);p=new_project(files,str(data.get('name','Spine 角色'))[:80])
            return self.send(p.summary())
        if parts==['api','demo']:
            files={p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()}
            p=new_project(files,'快递机器人');return self.send(p.summary())
        if len(parts)==4 and parts[:2]==['api','projects']:
            p=get_project(parts[2]);action=parts[3]
            if action=='contact':return self.send(AGENT_TASKS.contact_target('project',p.id,data))
            if action=='recolor':p.recolor(data['ids'],float(data['hue']),float(data['saturation']),float(data['brightness']),data['revision'])
            elif action=='replace':p.replace(data['id'],base64.b64decode(data['image'],validate=True),data['revision'])
            elif action=='replace-page':p.replace_page(data['index'],base64.b64decode(data['image'],validate=True),data['revision'])
            elif action=='reset':p.reset(data.get('ids'),data['revision'])
            elif action=='undo':p.undo(data['revision'])
            elif action=='save-version':p.save_version(data.get('label',''),data['revision'])
            elif action=='restore-version':p.restore_version(data['snapshot_id'],data['revision'])
            elif action=='refresh-jobs':
                JOBS.request_refresh(p.id)
                return self.send({'ok':True})
            elif action=='apply-ai':
                job_id=str(data['job_id'])
                if not re.fullmatch(r'[a-f0-9-]{16,64}',job_id):raise UserError('任务编号无效')
                job=json.loads((p.directory/'jobs'/(job_id+'.json')).read_text())
                if not job.get('candidate_ready'):raise UserError('候选图尚未完成')
                fitted=fit_result((p.directory/'jobs'/(job_id+'.png')).read_bytes(),job['layout'],p.part(job['region'],True))
                p.replace(job['region'],png_bytes(fitted),data['revision'],label='应用 AI：'+p.by_region[job['region']]['name'])
            elif action=='ai':
                p.check_revision(data['revision'])
                r=p.by_region[data['id']]
                if data.get('mode','material') not in ('material','reference'):raise UserError('请选择有效的 AI 生成方式')
                if not str(data.get('prompt','')).strip():raise UserError('请输入新外观描述')
                if data.get('mode')=='reference' and not str(data.get('reference_url','')).startswith('https://'):
                    raise UserError('参考图重绘需要 HTTPS 图片地址')
                nonce=str(data['nonce'])
                if not re.fullmatch(r'[a-f0-9-]{16,64}',nonce):raise UserError('任务编号不合法')
                jobs=p.directory/'jobs';jobs.mkdir(exist_ok=True)
                job_path=jobs/(nonce+'.json')
                with p.lock:
                    if job_path.exists():return self.send(json.loads(job_path.read_text()))
                    # Record before POST; an ambiguous failure never causes an automatic resubmit.
                    ref,layout=reference_for(p.part(r['id'],True))
                    if data.get('mode','material')=='material':layout['mode']='material'
                    job={'id':nonce,'region':r['id'],'revision':p.revision,'status':'submission_unknown','model':MATRIX.status()['model'],'provider':MATRIX.status().get('provider','matrix'),'created_at':time.time(),'layout':layout,'prompt':str(data['prompt'])[:2000],'mode':data.get('mode','material')}
                    atomic_json(job_path,job)
                try:gen_id=MATRIX.generate(str(data['prompt'])[:2000],data.get('reference_url') if data.get('mode')=='reference' else None)
                except UserError as e:
                    record_submission_failure(job,e);atomic_json(job_path,job);return self.send(job)
                job.update(gen_id=gen_id,status='pending',**MATRIX.job_metadata(gen_id));atomic_json(job_path,job);JOBS.wake.set()
                return self.send(job)
            else:raise UserError('未知操作')
            return self.send(p.summary())
        return self.send({'error':'未找到接口'},404)

    def workflow(self,parts,data=None,qs=None):
        local_address=self.connection.getsockname()[0]
        WEB_WORKFLOWS.trusted_request(self.headers.get('Host',''),self.headers.get('Origin'),local_address)
        session=WEB_WORKFLOWS.session(self.headers.get('Cookie'))
        route=parts[2:]
        if route==['session']:
            if data is not None and not isinstance(data,dict):raise WebError('连接请求格式无效')
            if data is not None and WEB_WORKFLOWS.require_code:session,cookie=WEB_WORKFLOWS.login(data.get('code'),self.client_address[0])
            else:session,cookie=WEB_WORKFLOWS.connect(self.headers.get('Cookie'),self.client_address[0],local_address)
            result=WEB_WORKFLOWS.connection_info(session)
            return self.send(result,headers={'Set-Cookie':cookie} if cookie else None)
        WEB_WORKFLOWS.require(session)
        if route==['source'] and data is None:
            if not session.get('owner'):raise WebError('请上传自己的角色图片',403)
            c=get_character((qs or {}).get('character',[''])[0])
            return self.send({'id':c.id,'name':c.meta['name'],'image_url':'/api/characters/'+c.id+'/image' if c.meta.get('image_id') else None})
        if route==['tasks']:
            return self.send(WEB_WORKFLOWS.tasks(session) if data is None else WEB_WORKFLOWS.submit(session,data),200 if data is None else 202)
        if route==['refine'] and data is not None:return self.send(WEB_WORKFLOWS.refine(session,data),202)
        if len(route)>=2 and route[0]=='tasks':
            t=AGENT_TASKS.read(route[1]);WEB_WORKFLOWS.allowed(session,t)
            if len(route)==3 and route[2]=='retry-stage' and data is not None:
                from app.stages import retry_stage
                retry_stage(AGENT_TASKS,route[1],data.get('stage'))
                return self.send(WEB_WORKFLOWS.read(session,route[1]))
            if len(route)==6 and route[2]=='materials' and data is None:
                from app.generation_flow import panel
                return self.send(panel(AGENT_TASKS,t,route[3],route[4],route[5]),mime='image/png')
            if len(route)==2 and data is None:return self.send(WEB_WORKFLOWS.read(session,route[1]))
            if len(route)==3 and route[2]=='animate-materials' and data is not None:return self.send(WEB_WORKFLOWS.animate_materials(session,route[1],data),202)
            if len(route)==3 and route[2]=='delivery' and data is not None:return self.send(WEB_WORKFLOWS.register_delivery(session,route[1],data))
            if len(route)==3 and route[2]=='resume' and data is not None:return self.send(WEB_WORKFLOWS.resume(session,route[1],data))
            if len(route)==3 and route[2]=='cancel' and data is not None:return self.send(WEB_WORKFLOWS.public(AGENT_TASKS.cancel(route[1])))
            if len(route)==3 and route[2]=='download' and data is None:return self.send(AGENT_TASKS.download(route[1]),mime='application/zip',filename='spine-animation-'+route[1]+'.zip')
        return self.send({'error':'未找到制作入口'},404)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--port',type=int,default=8780);args=parser.parse_args()
    STORAGE.mkdir(exist_ok=True)
    AGENT_TASKS.base=f'http://127.0.0.1:{args.port}'
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    def check_credentials():
        try:
            if MATRIX.credential()[0]:MATRIX.connect()
        except UserError:pass
    threading.Thread(target=check_credentials,daemon=True).start()
    threading.Thread(target=JOBS.run,daemon=True).start()
    threading.Thread(target=CHARACTER_JOBS.run,daemon=True).start()
    threading.Thread(target=AUTOMATION.run,daemon=True).start()
    threading.Thread(target=AGENT_TASKS.run,daemon=True).start()
    from app.animation_transition import run as run_animation_transitions,run_reviews as run_animation_reviews
    threading.Thread(target=run_animation_transitions,args=(AGENT_TASKS,),daemon=True).start()
    threading.Thread(target=run_animation_reviews,args=(AGENT_TASKS,),daemon=True).start()
    print(f'Spine 换皮工具监听 {args.host}:{args.port}',flush=True)
    print(f'本机访问 http://127.0.0.1:{args.port}/；局域网访问 http://本机局域网IP:{args.port}/',flush=True)
    import signal
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        MATRIX.tunnel.close()
        server.server_close()
