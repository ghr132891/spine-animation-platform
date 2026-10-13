import base64,io,json,sys,tempfile,unittest,uuid
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import UserError,atomic_json
from app.rig import RigProject
from app.rig_automation import RigAutomation
from app.web_workflows import WebWorkflows,WebError


class WebWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.storage=Path(self.tmp.name)
        loaders={'character':lambda id:CharacterProject(self.storage/'characters'/id),'rig':lambda id:RigProject(self.storage/'rigs'/id)}
        self.agent=AgentTasks(self.storage,ROOT,loaders,RigAutomation(self.storage,loaders['rig']))
        self.web=WebWorkflows(self.agent)
        self.owner,_=self.web.new_session(True);self.a,_=self.web.new_session();self.b,_=self.web.new_session()
        buffer=io.BytesIO();image=Image.new('RGBA',(128,256),(0,0,0,0));ImageDraw.Draw(image).rectangle((30,20,98,238),fill=(230,170,40,255));image.save(buffer,'PNG');self.image=base64.b64encode(buffer.getvalue()).decode()

    def tearDown(self):self.tmp.cleanup()

    def request(self,**kw):return {'nonce':uuid.uuid4().hex,'requirements':'制作行走和待机动画','mode':'image','image':self.image,**kw}

    def test_signed_session_tamper_expiry_and_login(self):
        self.web.require_code=True
        session,cookie=self.web.new_session();self.assertEqual(self.web.session(cookie),session)
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        self.assertIsNone(self.web.session(cookie.replace(session['id'],'x')) if session['id'] in cookie else self.web.session(cookie.replace('=', '=x',1)))
        value=base64.urlsafe_b64encode(json.dumps({**session,'expires':0}).encode()).decode().rstrip('=')
        self.assertIsNone(self.web.session(self.web.cookie_name+'='+value+'.'+self.web.sign(value)))
        peer='remote'
        for _ in range(5):
            with self.assertRaises(WebError) as e:self.web.login('incorrect',peer)
            self.assertEqual(e.exception.status,401)
        with self.assertRaises(WebError) as e:self.web.login(self.web.code,peer)
        self.assertEqual(e.exception.status,429)
        logged,_=self.web.login(self.web.code,'another');self.assertFalse(logged['owner'])

    def test_remote_open_connection_is_private_and_reuses_signed_cookie(self):
        self.web.require_code=False
        session,cookie=self.web.connect(None,'172.16.7.80','172.16.7.76')
        self.assertFalse(session['owner']);self.assertEqual(self.web.session(cookie),session)
        info=self.web.connection_info(session)
        self.assertEqual(info,{'connected':True,'owner':False,'requires_code':False,'access_mode':'open'})
        self.assertNotIn('access_code',info)
        restored,new_cookie=self.web.connect(cookie,'172.16.7.80','172.16.7.76')
        self.assertEqual(restored,session);self.assertIsNone(new_cookie)
        other,_=self.web.connect(None,'172.16.7.81','172.16.7.76')
        first=self.web.submit(session,self.request())
        self.assertEqual(len(self.web.tasks(session)),1);self.assertEqual(self.web.tasks(other),[])
        with self.assertRaises(WebError) as error:self.web.read(other,first['id'])
        self.assertEqual(error.exception.status,404)

    def test_expired_and_tampered_open_cookies_get_new_non_owner_sessions(self):
        self.web.require_code=False
        original,_=self.web.new_session()
        expired=base64.urlsafe_b64encode(json.dumps({**original,'expires':0}).encode()).decode().rstrip('=')
        for cookie in [self.web.cookie_name+'='+expired+'.'+self.web.sign(expired),self.web.cookie_name+'=tampered.invalid']:
            session,new_cookie=self.web.connect(cookie,'172.16.7.80','172.16.7.76')
            self.assertFalse(session['owner']);self.assertNotEqual(original['id'],session['id'])
            self.assertEqual(session,self.web.session(new_cookie))

    def test_explicit_code_mode_preserves_login_and_local_owner(self):
        self.web.require_code=True
        self.assertEqual(self.web.connect(None,'172.16.7.80','172.16.7.76'),(None,None))
        self.assertFalse(self.web.connection_info(None)['connected'])
        logged,cookie=self.web.login(self.web.code,'172.16.7.80')
        self.assertFalse(logged['owner']);self.assertEqual(self.web.connect(cookie,'172.16.7.80','172.16.7.76'),(logged,None))
        owner,_=self.web.connect(None,'127.0.0.1','127.0.0.1')
        self.assertTrue(owner['owner']);self.assertIn('access_code',self.web.connection_info(owner))
        self.web.require_code=False
        self.assertNotIn('access_code',self.web.connection_info(owner))
        with self.assertRaises(WebError) as error:self.web.tasks(None)
        self.assertEqual(error.exception.status,401);self.assertIn('自动连接',str(error.exception))

    def test_access_mode_follows_agent_setting_unless_explicitly_overridden(self):
        for agent_token,override,expected in [(False,'',False),(True,'',True),(False,'1',True),(True,'0',False)]:
            with self.subTest(agent_token=agent_token,override=override):
                self.agent.require_token=agent_token
                with patch.dict('os.environ',{},clear=True):
                    if override:
                        with patch.dict('os.environ',{'SPINE_WEB_REQUIRE_CODE':override}):web=WebWorkflows(self.agent)
                    else:web=WebWorkflows(self.agent)
                self.assertEqual(web.require_code,expected)

    def test_host_and_origin_check(self):
        self.web.trusted_request('172.16.7.76:8780','http://172.16.7.76:8780','172.16.7.76')
        self.web.trusted_request('127.0.0.1:8780',None,'127.0.0.1')
        for host,origin in [('evil.test:8780',None),('127.0.0.1:8780','https://evil.test'),('127.0.0.1:8780','file://127.0.0.1:8780')]:
            with self.assertRaises(WebError):self.web.trusted_request(host,origin,'127.0.0.1')

    def test_image_submission_deduplication_isolation_and_cancel(self):
        request=self.request();first=self.web.submit(self.a,request);second=self.web.submit(self.a,request)
        self.assertEqual(first,second);self.assertEqual(len(list((self.storage/'characters').iterdir())),1)
        raw=self.agent.read(first['id']);source=raw['sources'][0]['id']
        self.assertEqual(raw['max_generations'],0);self.assertEqual(raw['web_mode'],'image')
        self.assertEqual(first['image_url'],'/api/characters/'+source+'/image')
        self.assertNotIn('web_session_id',first);self.assertEqual(len(self.web.tasks(self.a)),1);self.assertEqual(self.web.tasks(self.b),[])
        with self.assertRaises(WebError) as e:self.web.submit(self.b,request)
        self.assertEqual(e.exception.status,404)
        with self.assertRaises(WebError):self.web.read(self.b,first['id'])
        self.assertEqual(self.web.read(self.owner,first['id'])['id'],first['id'])
        cancelled=self.web.public(self.agent.cancel(first['id']));self.assertEqual(cancelled['state'],'cancelled');self.assertIsNone(cancelled['download_url'])

    def test_invalid_images_cleanup_and_source_copy_preserves_original(self):
        for image in ('invalid base64!',base64.b64encode(b'not an image').decode()):
            with self.assertRaises(UserError):self.web.submit(self.a,self.request(image=image))
        self.assertFalse(list((self.storage/'characters').glob('*')))
        created=self.web.submit(self.a,self.request());source=self.agent.read(created['id'])['sources'][0]['id']
        original=self.agent.loaders['character'](source).meta.copy()
        request={'mode':'image','character_id':source,'nonce':uuid.uuid4().hex,'requirements':'制作慢速的原地行走动画'}
        with self.assertRaises(WebError):self.web.submit(self.b,request)
        cloned=self.web.submit(self.owner,request);new_id=self.agent.read(cloned['id'])['sources'][0]['id']
        self.assertNotEqual(source,new_id);self.assertEqual(self.agent.loaders['character'](source).meta,original)
        with self.assertRaises(UserError):self.agent.create({**self.request(),'sources':[{'kind':'character','id':source}]})

    def test_generation_budget_and_stage(self):
        task=self.web.submit(self.a,self.request(mode='generate'));raw=self.agent.read(task['id'])
        self.assertEqual(raw['max_generations'],8);self.assertIsNone(task['image_url']);self.assertEqual(task['step'],0)
        raw.update(state='running',stage='spine_character',stage_detail='build');self.assertEqual(self.web.public(raw)['step'],2)
        raw.update(state='failed',error='遮挡部分无法补全');atomic_json(self.agent.path(raw['id']),raw)
        self.assertEqual(self.web.read(self.a,raw['id'])['error'],'遮挡部分无法补全')

    def draft_fixture(self):
        task=self.web.submit(self.a,self.request());raw=self.agent.read(task['id'])
        raw.update(state='failed',error='肩部验收未通过');atomic_json(self.agent.path(raw['id']),raw)
        self.agent.root=self.storage
        page=self.storage/'web/downloads/repair/index.html';page.parent.mkdir(parents=True);page.write_text('draft')
        self.rig=SimpleNamespace(meta={'revision':8},state={'skeleton':{'skeleton':{'spine':'4.2.00'},'animations':{'idle':{},'walk':{}}}},validate=lambda:{'passed':True})
        self.agent.loaders['rig']=lambda id:self.rig
        return raw,{'rig_id':uuid.uuid4().hex,'summary':'可播放，肩缘仍待优化','process_url':'/downloads/repair/index.html'}

    def test_draft_visible_without_falsifying_failed_task(self):
        raw,data=self.draft_fixture()
        result=self.web.register_delivery(self.owner,raw['id'],data)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['delivery_status'],'draft')
        self.assertEqual(result['artifacts'][0]['revision'],8)
        self.assertEqual(result['artifacts'][0]['animations'],['idle','walk'])
        self.assertEqual(result['download_url'],'/api/rigs/'+data['rig_id']+'/export')
        self.assertEqual(self.agent.read(raw['id']),raw)
        self.assertEqual(self.web.read(self.a,raw['id'])['delivery_status'],'draft')
        self.assertEqual(self.web.tasks(self.b),[])
        with self.assertRaises(WebError):self.web.register_delivery(self.a,raw['id'],data)
        with self.assertRaises(WebError):self.web.read(self.b,raw['id'])
        self.web.register_delivery(self.owner,raw['id'],data)
        self.assertEqual(self.web.read(self.a,raw['id'])['delivery_history'],[])
        self.rig.meta['revision']=9
        revised=self.web.register_delivery(self.owner,raw['id'],data)
        self.assertEqual(revised['artifacts'][0]['revision'],9)
        self.assertEqual(revised['delivery_history'][0]['revision'],8)

    def test_draft_invalid_resource_or_external_page_is_rejected(self):
        raw,data=self.draft_fixture()
        for url in ('https://example.com','//example.com','/downloads/../index.html'):
            with self.assertRaises(WebError):self.web.register_delivery(self.owner,raw['id'],{**data,'process_url':url})
        self.rig.validate=lambda:{'passed':False}
        with self.assertRaises(WebError):self.web.register_delivery(self.owner,raw['id'],data)
        self.assertIsNone(self.web.delivery(raw['id']))

    def test_grouped_resume_retains_draft_but_new_completed_result_wins(self):
        raw,data=self.draft_fixture();self.web.register_delivery(self.owner,raw['id'],data)
        later=self.web.submit(self.a,self.request());current=self.agent.read(later['id'])
        current.update(workflow_id=raw['id'],state='running');atomic_json(self.agent.path(current['id']),current)
        grouped=self.web.read(self.a,current['id'])
        self.assertEqual(grouped['state'],'running');self.assertEqual(grouped['delivery_status'],'draft')
        self.agent.update(current['id'],state='completed')
        self.assertNotIn('delivery_status',self.web.read(self.a,current['id']))

    def test_web_refine_uses_latest_draft_and_groups_with_original(self):
        raw,data=self.draft_fixture();self.web.register_delivery(self.owner,raw['id'],data)
        request={'nonce':uuid.uuid4().hex,'task_id':raw['id'],'requirements':'让摆臂更自然一些，保留衣服'}
        cloned={'kind':'rig','id':uuid.uuid4().hex,'original_id':data['rig_id'],'source_revision':8}
        with patch.object(self.agent,'clone',return_value=cloned) as clone:
            result=self.web.refine(self.a,request)
            clone.assert_called_once_with({'kind':'rig','id':data['rig_id'],'revision':8})
            retry=self.web.refine(self.a,request)
            self.assertEqual(result['id'],retry['id']);self.assertEqual(clone.call_count,1)
        task=self.agent.read(result['id'])
        self.assertEqual(task['max_generations'],0);self.assertEqual(task['image_preparation'],'preserve')
        self.assertEqual(task['web_session_id'],self.a['id']);self.assertEqual(task['workflow_id'],raw['id'])
        self.assertEqual(len(self.web.tasks(self.a)),1);self.assertEqual(result['delivery_status'],'draft');self.assertEqual(result['max_repairs'],4)
        self.assertEqual(result['state'],'queued');self.assertEqual(self.agent.read(raw['id'])['state'],'failed')
        with self.assertRaises(WebError):self.web.refine(self.a,{**request,'nonce':uuid.uuid4().hex})
        with self.assertRaises(WebError):self.web.refine(self.a,{**request,'requirements':'完全不同的修改要求'})
        with self.assertRaises(WebError):self.web.refine(self.b,request)

    def test_result_page_refine_enforces_ownership_and_revision(self):
        raw,data=self.draft_fixture();self.web.register_delivery(self.owner,raw['id'],data)
        request={'nonce':uuid.uuid4().hex,'rig_id':data['rig_id'],'revision':8,'requirements':'把行走速度放慢一些'}
        with self.assertRaises(WebError):self.web.refine(self.b,request)
        with self.assertRaises(WebError):self.web.refine(self.a,{**request,'revision':7})
        with patch.object(self.agent,'clone',return_value={'kind':'rig','id':uuid.uuid4().hex,'original_id':data['rig_id']}):
            result=self.web.refine(self.a,request)
        self.assertEqual(result['workflow_id'],raw['id']);self.assertEqual(result['state'],'queued')


if __name__=='__main__':unittest.main()

class WorkflowRecoveryTests(WebWorkflowTests):
    def test_resume_keeps_identity_assets_budget_and_is_idempotent(self):
        task=self.web.submit(self.a,self.request());raw=self.agent.read(task['id'])
        raw.update(state='failed',error='timeout',reservations={'job1':{'id':'existing','count':1}},calls=12)
        atomic_json(self.agent.path(raw['id']),raw)
        retry={'nonce':uuid.uuid4().hex}
        result=self.web.resume(self.a,raw['id'],retry)
        self.assertEqual(result['id'],raw['id']);self.assertEqual(result['state'],'queued')
        self.web.resume(self.a,raw['id'],retry)
        saved=self.agent.read(raw['id']);self.assertEqual(saved['reservations'],raw['reservations'])
        self.assertEqual(saved['owned'],raw['owned']);self.assertEqual(saved['max_generations'],raw['max_generations'])
        self.assertEqual(len(saved['execution_attempts']),1)
        with self.assertRaises(WebError):self.web.resume(self.b,raw['id'],retry)
        self.assertEqual(len(self.web.tasks(self.a)),1)

    def test_explicit_grouping_preserves_failures_and_session_isolation(self):
        ids=[]
        for i in range(4):
            t=self.web.submit(self.a,self.request());raw=self.agent.read(t['id']);ids.append(raw['id'])
            raw.update(workflow_id='blue-dress',state='failed',error='review failed')
            atomic_json(self.agent.path(raw['id']),raw)
        other=self.web.submit(self.b,self.request());raw=self.agent.read(other['id']);raw['workflow_id']='blue-dress';atomic_json(self.agent.path(raw['id']),raw)
        rows=self.web.tasks(self.a);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['attempt_count'],4)
        self.assertEqual(set(rows[0]['attempt_ids']),set(ids));self.assertEqual(rows[0]['state'],'failed')
        self.assertEqual(self.web.read(self.a,ids[0])['id'],rows[0]['id'])
        self.assertEqual(len(self.web.tasks(self.owner)),2)
