"""Browser sessions for the simple workflow UI; no Agent token in JavaScript."""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from .core import UserError,atomic_json


class WebError(UserError):
    def __init__(self,message,status=400):super().__init__(message);self.status=status


class WebWorkflows:
    cookie_name='spine_workflow_session'

    def __init__(self,agent):
        self.agent=agent;self.secret=agent.token.encode();self.guard=threading.RLock();self.attempts={}
        self.require_code=os.getenv('SPINE_WEB_REQUIRE_CODE','1' if agent.require_token else '0')=='1'
        path=agent.storage/'workflow-access-code'
        if not path.exists():
            try:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(6))
            except FileExistsError:pass
        self.code=path.read_text().strip()

    def sign(self,value):return hmac.new(self.secret,value.encode(),hashlib.sha256).hexdigest()

    def new_session(self,owner=False):
        session={'id':secrets.token_hex(16),'owner':owner,'expires':int(time.time())+30*86400}
        value=base64.urlsafe_b64encode(json.dumps(session,separators=(',',':')).encode()).decode().rstrip('=')
        cookie=f'{self.cookie_name}={value}.{self.sign(value)}; HttpOnly; SameSite=Strict; Path=/api/workflows; Max-Age=2592000'
        return session,cookie

    def session(self,cookie):
        try:
            parsed=SimpleCookie();parsed.load(cookie or '');value,signature=parsed[self.cookie_name].value.split('.')
            if not hmac.compare_digest(signature,self.sign(value)):return None
            s=json.loads(base64.urlsafe_b64decode(value+'='*(-len(value)%4)))
            if s['expires']<time.time() or not re.fullmatch('[a-f0-9]{32}',s['id']):return None
            return s
        except (ValueError,KeyError,TypeError):return None

    def connect(self,cookie,peer,address):
        session=self.session(cookie)
        if session:return session,None
        try:owner=ipaddress.ip_address(peer).is_loopback or peer==address
        except ValueError:owner=False
        if owner or not self.require_code:return self.new_session(owner=owner)
        return None,None

    def connection_info(self,session):
        result={'connected':bool(session),'owner':bool(session and session.get('owner')),
                'requires_code':self.require_code,'access_mode':'code' if self.require_code else 'open'}
        if session and session.get('owner') and self.require_code:result['access_code']=self.code
        return result

    def trusted_request(self,host,origin,address):
        hostname=urlsplit('//'+host).hostname
        if hostname not in ('localhost','127.0.0.1','::1',address,socket.gethostname().lower()):raise WebError('请使用平台的本机或局域网地址打开',403)
        if origin:
            p=urlsplit(origin)
            if p.scheme not in ('http','https') or p.netloc.lower()!=host.lower():raise WebError('请在平台页面中提交',403)

    def login(self,code,peer):
        if not self.require_code:return self.new_session()
        now=time.time()
        with self.guard:
            recent=[t for t in self.attempts.get(peer,[]) if now-t<60]
            if len(recent)>=5:raise WebError('访问码尝试过多，请一分钟后再试',429)
            if not isinstance(code,str) or not hmac.compare_digest(code.strip(),self.code):
                self.attempts[peer]=recent+[now];raise WebError('访问码不正确，请向平台管理员获取',401)
            self.attempts.pop(peer,None)
        return self.new_session()

    def require(self,s):
        if not s:raise WebError('首次使用请输入访问码' if self.require_code else '连接已过期，请刷新页面自动连接',401)

    def allowed(self,s,t):
        self.require(s)
        if not s.get('owner') and t.get('web_session_id')!=s['id']:raise WebError('未找到这次制作记录',404)

    def delivery(self,id):
        # Separate from worker state: a usable repair draft is not a passed task.
        path=self.agent.storage/'workflow-deliveries'/(id+'.json')
        return json.loads(path.read_text()) if path.exists() else None

    def register_delivery(self,s,id,data):
        self.require(s)
        if not s.get('owner'):raise WebError('仅平台管理员可以关联修复草稿',403)
        self.agent.read(id)
        if not isinstance(data,dict):raise WebError('草稿信息格式无效')
        rid=data.get('rig_id','');summary=data.get('summary','');process=data.get('process_url','')
        if not isinstance(rid,str) or not re.fullmatch('[a-f0-9]{32}',rid):raise WebError('草稿骨架编号无效')
        if not isinstance(summary,str) or not 1<=len(summary.strip())<=2000:raise WebError('请填写草稿结果和待修问题')
        if not isinstance(process,str) or not re.fullmatch(r'/downloads/[a-zA-Z0-9_-]+/index\.html',process):raise WebError('请使用平台内的制作过程页')
        if not (self.agent.root/'web'/process.lstrip('/')).is_file():raise WebError('制作过程页不存在')
        rig=self.agent.loaders['rig'](rid)
        report=rig.validate()
        if not report['passed']:raise WebError('草稿资源无法加载，不能作为可用动画交付')
        entry={'rig_id':rid,'revision':rig.meta['revision'],'runtime':rig.state['skeleton'].get('skeleton',{}).get('spine','')[:3],
               'animations':list(rig.state['skeleton'].get('animations',{})),
               'summary':summary.strip(),'process_url':process,'updated_at':time.time(),'status':'draft'}
        from .workflow_delivery import save_delivery
        entry.pop('updated_at',None)
        save_delivery(self.agent.storage,id,entry)
        return self.read(s,id)

    def attach_delivery(self,result,delivery):
        if not delivery:return result
        rid=delivery['rig_id']
        # Retain the actual worker state/error for diagnosis and continuation.
        result.update(delivery_status='draft',delivery_updated_at=delivery['updated_at'],
                      process_url=delivery.get('process_url'),delivery_summary=delivery['summary'],delivery_task_id=delivery.get('task_id'),
                      delivery_history=delivery.get('history',[]),
                      download_url='/api/rigs/'+rid+'/export',
                      artifacts=[{'kind':'rig','id':rid,'rig_id':rid,'revision':delivery['revision'],
                                  'runtime':delivery['runtime'],'animations':delivery['animations'],
                                  'preview_url':'/result.html?rig='+rid+'&animation=walk',
                                  'editor_url':'/rig.html?rig='+rid+'&advanced=1'}])
        return result

    def public(self,t):
        from .stages import public as stage_timings
        ready=t['state']=='completed'
        detail=t.get('stage_detail','') if t.get('stage')=='spine_character' else ''
        stage={'waiting_reference':'等待参考图恢复（自动继续）','queued':'排队等待','completed':'制作完成','failed':'本次未完成','cancelled':'已停止','interrupted':'处理已中断'}.get(t['state'])
        if not stage:
            stage={'generate':'生成角色图片','generate-view':'准备动作所需视角','generate-parts':'准备完整角色部件图','apply-ai':'准备角色图片','apply-part-sheet':'组装角色部件','apply-part-layout':'组装并检查部件','apply-source-layout':'复用原图部件','prepare-assembly':'补全并统一装配与遮挡','finish-parts':'修整部件连接边','prebind':'检查部件、试拼与摆开','guide':'定位骨骼与关节','review':'调整部件接合','build':'制作行走动画','foot-layers':'调整脚腿遮挡'}.get(detail)
        if not stage:stage={'spine_context':'读取制作需求','spine_assess_image':'检查视角与连接','spine_review_parts':'检查部件与连接','spine_inspect':'分析角色与骨骼','spine_image':'检查角色外观','spine_wait':'等待图片生成','spine_edit_rig':'调整骨骼与动作','spine_create_rig':'创建骨架','spine_validate':'检查资源','spine_motion':'检查动画画面','spine_gait':'检查摆臂与循环','spine_contact':'测量鞋底接地与滑步'}.get(t.get('stage'),'正在自动制作')
        steps=['准备视角与部件','检查与组装','制作动作','优化与导出']
        index=0 if detail in ('generate','generate-view','apply-ai') or t.get('stage') in ('spine_context','spine_image','spine_inspect','spine_assess_image') else 1 if t.get('stage')=='spine_review_parts' or detail in ('generate-parts','apply-part-sheet','apply-part-layout','apply-source-layout','prepare-assembly','finish-parts','prebind','review-prebind','guide','review') else 2 if detail in ('build','foot-layers') or t.get('stage') in ('spine_edit_rig','spine_create_rig') else 3
        if t['state']=='queued' or t.get('stage') in ('等待后台执行','Codex 自动编排'):index=0
        result=[]
        for a in t.get('artifacts',[]):
            if not ready:continue
            rig_id=a['id'] if a['kind']=='rig' else None
            if a['kind']=='character':
                c=self.agent.loaders['character'](a['id']);v=next((v for v in c.meta['versions'] if v['id']==a.get('version')),None)
                if v:rig_id=v['rig_id']
            runtime=self.agent.loaders['rig'](rig_id).state['skeleton'].get('skeleton',{}).get('spine','')[:3] if rig_id else None
            animations=a.get('runtime_validation',{}).get('animations',[])
            preview=('/result.html?rig='+rig_id+('&animation=walk' if 'walk' in animations else '') if rig_id else a['preview_url'])
            editor=('/rig.html?rig='+rig_id+'&advanced=1' if rig_id else a.get('editor_url',a['preview_url']))
            result.append({'kind':a['kind'],'id':a['id'],'rig_id':rig_id,'runtime':runtime,'animations':animations,'preview_url':preview,'editor_url':editor})
        return {'id':t['id'],'state':t['state'],'reference_recovery':t.get('reference_recovery'),'requirements':t['requirements'],'created_at':t['created_at'],'stage':stage,
                'is_refinement':bool(t.get('web_refine_parent')),'max_repairs':t.get('max_repairs',4),
                'steps':steps,'step':index,'error':t.get('error'),'summary':t.get('summary',''),'artifacts':result,
                'download_url':'/api/workflows/tasks/'+t['id']+'/download' if ready else None,
                'image_url':'/api/characters/'+t['sources'][0]['id']+'/image' if t['sources'] and t['sources'][0]['kind']=='character' and self.agent.loaders['character'](t['sources'][0]['id']).meta.get('image_id') else None,
                'image_assessments':t.get('image_assessments',{}),'generation_budget':t.get('generation_budget',{}),
                'stage_timings':stage_timings(t),'animation_transitions':list(t.get('animation_transitions',{}).values()),'material_preparation':list(t.get('material_preparation',{}).values()),'worker_failure':t.get('worker_failure'),
                'material_delivery':t.get('material_delivery'),'output_scope':t.get('output_scope','animation')}

    def visible_tasks(self,s):
        self.require(s)
        return [t for p in self.agent.tasks.glob('*/task.json')
                if (t:=self.agent.read(p.parent.name)) and (s.get('owner') or t.get('web_session_id')==s['id'])]

    def grouped(self,records):
        groups={}
        for t in records:
            # Explicit production association only, never image/name similarity.
            key=(t.get('web_session_id'),t.get('workflow_id',t['id']))
            groups.setdefault(key,[]).append(t)
        results=[]
        for members in groups.values():
            members.sort(key=lambda t:t['created_at'])
            current=max(members,key=lambda t:(t.get('updated_at',t['created_at']),t['created_at']))
            result=self.public(current)
            result.update(workflow_id=members[0].get('workflow_id',members[0]['id']),
                          attempt_ids=[t['id'] for t in members],
                          attempt_count=sum(1+len(t.get('execution_attempts',[])) for t in members),
                          attempts=[{'id':t['id'],'state':t['state'],'error':t.get('error'),
                                     'created_at':t['created_at']} for t in members]+[{'id':t['id'],'state':a['state'],'error':a.get('error'),'created_at':a.get('ended_at') or t['created_at']} for t in members for a in t.get('execution_attempts',[])])
            result['requirements']=members[0].get('workflow_title',members[0]['requirements'])
            if current.get('web_refine_parent'):result['current_request']=current.get('web_request_summary') or current['requirements']
            if not result.get('image_url'):result['image_url']=self.public(members[0]).get('image_url')
            result['updated_at']=current.get('updated_at',current['created_at'])
            deliveries=[d for t in members if (d:=self.delivery(t['id']))]
            if deliveries:
                latest=max(deliveries,key=lambda d:d['updated_at'])
                if current['state']!='completed' or latest['updated_at']>=result['updated_at']:
                    self.attach_delivery(result,latest)
                    result['updated_at']=max(result['updated_at'],latest['updated_at'])
            if not result['artifacts'] and current['state']!='completed':
                previous=next((t for t in reversed(members) if t['state']=='completed' and t.get('artifacts')),None)
                if previous:
                    saved=self.public(previous)
                    result.update(artifacts=saved['artifacts'],download_url=saved['download_url'],delivery_status='previous',
                                  delivery_summary='正在修改这套动画，当前展示上次保存的版本。'+(' 本次未完成：'+str(result['error']) if result.get('error') else ''))
            results.append(result)
        return sorted(results,key=lambda t:t['updated_at'],reverse=True)

    def tasks(self,s):return self.grouped(self.visible_tasks(s))[:50]

    def read(self,s,id):
        t=self.agent.read(id);self.allowed(s,t)
        return next(item for item in self.grouped(self.visible_tasks(s)) if id in item['attempt_ids'])

    def resume(self,s,id,data):
        t=self.agent.read(id);self.allowed(s,t)
        current=self.read(s,id)
        if current['id']!=id:raise WebError('此制作已有更新记录，请刷新后继续')
        return self.public(self.agent.resume(id,data))

    def refine(self,s,data):
        self.require(s)
        if not isinstance(data,dict):raise WebError('修改要求格式无效')
        request_id=data.get('nonce','');requirements=data.get('requirements','')
        if not isinstance(request_id,str) or not re.fullmatch('[a-f0-9]{32}',request_id):raise WebError('请求编号无效')
        if not isinstance(requirements,str) or not 5<=len(requirements.strip())<=2000:raise WebError('请用 5–2000 个字描述修改要求')
        digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.agent.lock:
            if self.agent.path(request_id).exists():
                previous=self.agent.read(request_id);self.allowed(s,previous)
                if previous.get('web_refine_sha256')!=digest:raise WebError('此请求编号已用于其他修改')
                return self.read(s,request_id)
            current=None
            if data.get('task_id'):
                current=self.read(s,data['task_id'])
                index=data.get('artifact_index',0)
                if type(index)!=int or not 0<=index<len(current['artifacts']):raise WebError('请先选择一个可用动画')
                rid=current['artifacts'][index].get('rig_id')
            else:
                rid=data.get('rig_id')
                current=next((t for t in self.tasks(s) if any(a.get('rig_id')==rid for a in t['artifacts'])),None)
                if not current and not s.get('owner'):raise WebError('未找到你可修改的制作成果',404)
            if not isinstance(rid,str) or not re.fullmatch('[a-f0-9]{32}',rid):raise WebError('请先选择一个可用动画')
            if current and current['state'] not in ('completed','failed','cancelled','interrupted'):raise WebError('这套角色正在处理，请完成后再提交修改')
            rig=self.agent.loaders['rig'](rid)
            if data.get('revision') is not None and data['revision']!=rig.meta['revision']:raise WebError('动画已有更新，请刷新后再修改')
            parent=self.agent.read(current['id']) if current else None
            request={'nonce':request_id,'requirements':'基于所附当前动画副本按以下要求修改：'+requirements.strip()+'\n优先复用现有素材；保留未要求修改的外观和其他动作，另存版本。检查修改后的待机与行走，报告实际结果和残留问题。',
                     'sources':[{'kind':'rig','id':rid,'revision':rig.meta['revision']}],
                     'image_preparation':'preserve','max_generations':0,'max_repairs':4,'timeout_seconds':1800,
                     '_workflow_id':current['workflow_id'] if current else request_id,
                     '_workflow_title':current['requirements'] if current else requirements.strip(),
                     '_web_refine_parent':current['id'] if current else None,'_web_refine_sha256':digest}
            session_id=parent.get('web_session_id') if parent else s['id']
            if session_id:request.update(_web_session_id=session_id,_web_mode='refine')
            created=self.agent.create(request)
            return self.read(s,created['id'])

    def animate_materials(self,s,id,data):
        """Continue an accepted material delivery in its existing browser record."""
        self.require(s)
        if not isinstance(data,dict) or not isinstance(data.get('nonce'),str) or not re.fullmatch('[a-f0-9]{32}',data['nonce']):raise WebError('请求编号无效')
        signature=hashlib.sha256(json.dumps([id,data],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.agent.lock:
            if self.agent.path(data['nonce']).exists():
                old=self.agent.read(data['nonce']);self.allowed(s,old)
                if old.get('material_handoff_sha256')!=signature:raise WebError('此请求编号已用于其他制作')
                return self.read(s,old['id'])
            parent=self.agent.read(id);self.allowed(s,parent)
            if self.read(s,id)['id']!=id:raise WebError('此制作已有更新成果，请刷新后继续')
            delivered=parent.get('material_delivery',{}).get('characters',[])
            if parent['state']!='completed' or parent.get('output_scope')!='materials' or len(delivered)!=1:raise WebError('先完成一套素材的验收与交付')
            item=delivered[0];cid=item['character_id'];self.agent.own(parent,'character',cid)
            c=self.agent.loaders['character'](cid)
            with c.lock:
                from .prebind import require
                report,_=c.prebind_evidence();require(report,c.meta.get('prebind_review'))
                if report['digest']!=item['digest']:raise WebError('素材已变化，请先重新验收，不能沿用旧版结论')
                request={'nonce':data['nonce'],'sources':[{'kind':'character','id':cid,'revision':c.meta['revision']}],
                    'requirements':'用这套已验收完整素材制作待机 idle 和自然朝右行走 walk，保留外观与修复，另存版本。先检查当前组装与视角，平台自动绑定并交付草稿，再完成全部动态局部、接地与循环验收。不得重新生图或回退旧素材。',
                    'output_scope':'animation','image_preparation':'preserve','max_generations':0,'max_repairs':4,'timeout_seconds':1800,
                    '_material_animation':True,'_material_handoff_sha256':signature,
                    '_workflow_id':parent.get('workflow_id',id),'_workflow_title':parent.get('workflow_title',parent['requirements']),
                    '_web_session_id':parent.get('web_session_id',s['id']),'_web_mode':'animation-from-materials','_web_refine_parent':id,
                    '_web_request_summary':'使用这套已验收素材制作待机和自然行走，保留修复与旧版，不重新生图。'}
                created=self.agent.create(request)
            return self.read(s,created['id'])

    def submit(self,s,data):
        self.require(s)
        if not isinstance(data,dict):raise WebError('制作要求格式无效')
        mode=data.get('mode','image')
        if mode not in ('image','generate'):raise WebError('请选择角色图片来源')
        preparation=data.get('image_preparation','preserve')
        if preparation not in ('asset-sheet','auto','preserve'):raise WebError('图片准备方式无效')
        request={'nonce':data.get('nonce'),'requirements':data.get('requirements',''),
                 'image_preparation':preparation,'sources':[],'max_generations':0 if mode=='image' and preparation=='preserve' else 8,'max_repairs':4,'timeout_seconds':1800,
                 '_web_session_id':s['id'],'_web_mode':mode}
        if mode=='image':
            if data.get('character_id'):
                if not s.get('owner'):raise WebError('请上传自己的角色图片',403)
                c=self.agent.loaders['character'](data['character_id'])
                if not c.meta.get('image_id'):raise WebError('当前角色还没有图片')
                request['sources']=[{'kind':'character','id':c.id}]
            else:
                if not data.get('image'):raise WebError('请先上传一张角色图片')
                request['image']=data['image']
        with self.agent.lock:
            path=self.agent.path(request['nonce'])
            if path.exists():self.allowed(s,self.agent.read(request['nonce']))
            return self.public(self.agent.create(request))
