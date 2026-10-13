"""Simple page routing and safe, non-generating image-failure recovery."""
import json,tempfile,threading,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from urllib.parse import parse_qs,urlsplit
from app import server
from app.agent_tasks import AgentTasks
from app.core import UserError,atomic_json
from app.jobs import JobHistory
from app.provider import ProviderError,classify_provider_failure
from app.web_workflows import WebWorkflows,WebError


class SimplePageTests(unittest.TestCase):
    def test_default_home_and_explicit_editing(self):
        self.assertEqual(server.webpage_route('/',{}),('file','auto.html'))
        self.assertEqual(server.webpage_route('/index.html',{}),('file','auto.html'))
        self.assertEqual(server.webpage_route('/',{'project':['a'*32]}),('file','index.html'))
        self.assertEqual(server.webpage_route('/index.html',{'advanced':['1']}),('file','index.html'))

    def test_legacy_rig_links_preserve_selection(self):
        data={'rig':['a'*32],'animation':['walk slow'],'skin':['blue'],'speed':['1']}
        kind,url=server.webpage_route('/rig.html',data)
        self.assertEqual(kind,'redirect');self.assertEqual(urlsplit(url).path,'/result.html')
        self.assertEqual(parse_qs(urlsplit(url).query),data)
        self.assertEqual(server.webpage_route('/rig.html',{**data,'advanced':['1']}),('file','rig.html'))
        self.assertEqual(server.webpage_route('/rig.html',{'source':['b'*32]}),('file','rig.html'))
        self.assertEqual(server.webpage_route('/rig.html',{}),('redirect','/result.html'))

    def test_server_redirect_is_internal_and_readonly(self):
        handler=object.__new__(server.Handler);handler.path='/rig.html?rig='+ 'a'*32+'&animation=walk'
        handler.send=Mock()
        with patch.object(server.MATRIX,'generate') as generate:
            handler.get()
        generate.assert_not_called();self.assertEqual(handler.send.call_args.args,(b'',302))
        self.assertEqual(handler.send.call_args.kwargs['headers']['Location'],'/result.html?rig='+ 'a'*32+'&animation=walk')

    def test_old_task_links_use_the_saved_rig_not_latest_character_version(self):
        saved='a'*32;latest='b'*32
        web=object.__new__(WebWorkflows);web.agent=SimpleNamespace(loaders={
            'character':lambda _:SimpleNamespace(meta={'versions':[{'id':'saved','rig_id':saved},{'id':'latest','rig_id':latest}]}),
            'rig':lambda _:SimpleNamespace(state={'skeleton':{'skeleton':{'spine':'4.2.0'}}})})
        for kind,entity,version in [('rig',saved,''),('character','c'*32,'saved')]:
            task={'id':'d'*32,'state':'completed','requirements':'待机行走','created_at':0,'sources':[],'artifacts':[{'kind':kind,'id':entity,'version':version,'preview_url':'/old-editor','runtime_validation':{'animations':['idle','walk']}}]}
            artifact=web.public(task)['artifacts'][0]
            self.assertEqual(artifact['preview_url'],'/result.html?rig='+saved+'&animation=walk')
            self.assertEqual(artifact['editor_url'],'/rig.html?rig='+saved+'&advanced=1')


class WebSessionHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        with patch.dict('os.environ',{'SPINE_WEB_REQUIRE_CODE':'0'}):
            self.web=WebWorkflows(SimpleNamespace(storage=Path(self.tmp.name),token='test-session-secret',require_token=False))
        self.handler=object.__new__(server.Handler)
        self.handler.connection=SimpleNamespace(getsockname=lambda:('172.16.7.76',8780))
        self.handler.client_address=('172.16.7.80',40000)
        self.handler.headers={'Host':'172.16.7.76:8780'}
        self.handler.send=Mock(side_effect=lambda value,*args,**kwargs:value)

    def tearDown(self):self.tmp.cleanup()

    def test_first_phone_visit_connects_without_code_or_generation(self):
        with patch.object(server,'WEB_WORKFLOWS',self.web),patch.object(server.MATRIX,'generate') as generate:
            result=self.handler.workflow(['api','workflows','session'])
        generate.assert_not_called()
        self.assertEqual(result,{'connected':True,'owner':False,'requires_code':False,'access_mode':'open'})
        cookie=self.handler.send.call_args.kwargs['headers']['Set-Cookie']
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        self.handler.headers['Cookie']=cookie
        with patch.object(server,'WEB_WORKFLOWS',self.web):resumed=self.handler.workflow(['api','workflows','session'],data={})
        self.assertEqual(resumed,result);self.assertIsNone(self.handler.send.call_args.kwargs['headers'])

    def test_protected_mode_still_requires_code_for_a_new_phone(self):
        self.web.require_code=True
        with patch.object(server,'WEB_WORKFLOWS',self.web):
            result=self.handler.workflow(['api','workflows','session'])
            self.assertEqual(result,{'connected':False,'owner':False,'requires_code':True,'access_mode':'code'})
            with self.assertRaises(WebError) as error:self.handler.workflow(['api','workflows','session'],data={'code':'bad'})
            self.assertEqual(error.exception.status,401)
            logged=self.handler.workflow(['api','workflows','session'],data={'code':self.web.code})
        self.assertTrue(logged['connected']);self.assertFalse(logged['owner']);self.assertNotIn('access_code',logged)

    def test_open_connection_does_not_bypass_host_or_origin_checks(self):
        with patch.object(server,'WEB_WORKFLOWS',self.web):
            for header,value in [('Host','evil.example:8780'),('Origin','https://evil.example')]:
                with self.subTest(header=header),patch.dict(self.handler.headers,{header:value}):
                    with self.assertRaises(WebError) as error:self.handler.workflow(['api','workflows','session'])
                    self.assertEqual(error.exception.status,403)
            with self.assertRaises(WebError):self.handler.workflow(['api','workflows','session'],data=[])


class ImageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.storage=Path(self.tmp.name);self.entity='a'*32;self.nonce='b'*32
        self.directory=self.storage/'characters'/self.entity;self.directory.mkdir(parents=True)
        self.jobs=JobHistory(self.storage,Mock(),collection='characters')
        self.path=self.jobs.path(self.entity,self.nonce)
        atomic_json(self.path,{'id':self.nonce,'gen_id':'existing-generation','status':'pending','model':'gpt-image-2.5-flare'})

    def tearDown(self):self.tmp.cleanup()

    def test_review_failure_reason_survives_without_sensitive_output(self):
        for code in ['IMAGE_RECITATION','IMAGE_SAFETY']:
            result={'data':{'status':'completed','input':{'prompt':'secret prompt'},'outputs':[{'item_status':'failed','error':{'finish_reason':code,'message':'sk-do-not-display'}}]}}
            atomic_json(self.path,{'id':self.nonce,'gen_id':'existing-generation','status':'pending'})
            self.jobs.provider.poll.return_value=result
            job=self.jobs.refresh(self.path,force=True)
            self.assertEqual(job['status'],'failed');self.assertTrue(job['failure']['stop_generation'])
            self.assertFalse(job['failure']['rewrite_retry_allowed']);self.assertFalse(job['failure']['new_submission_allowed'])
            self.assertNotIn('sk-do-not-display',json.dumps(job));self.assertNotIn('secret prompt',json.dumps(job['failure']))
            self.jobs.refresh(self.path,force=True)
        self.assertEqual(self.jobs.provider.poll.call_count,2)
        self.jobs.provider.generate.assert_not_called();self.jobs.provider.download_result.assert_not_called()

    def test_failed_task_and_empty_output_never_become_candidates(self):
        for status,expected in [('failed','generation_failed'),('completed','empty_output')]:
            atomic_json(self.path,{'id':self.nonce,'gen_id':'existing-generation','status':'pending'})
            self.jobs.provider.poll.return_value={'data':{'status':status,'outputs':[]}}
            job=self.jobs.refresh(self.path,force=True)
            self.assertEqual(job['failure']['category'],expected);self.assertFalse(job.get('candidate_ready'))
            self.assertEqual(job['gen_id'],'existing-generation')
        self.jobs.provider.generate.assert_not_called()

    def test_query_failure_reuses_existing_task(self):
        self.jobs.provider.poll.side_effect=ProviderError(classify_provider_failure(http_status=429,operation='query'))
        job=self.jobs.refresh(self.path,force=True)
        self.assertEqual(job['status'],'pending');self.assertTrue(job['failure']['retry_same_task'])
        self.jobs.provider.poll.side_effect=None;self.jobs.provider.poll.return_value={'data':{'status':'pending'}}
        self.jobs.refresh(self.path,force=True)
        for call in self.jobs.provider.poll.call_args_list:self.assertEqual(call.args,('existing-generation',))
        self.jobs.provider.generate.assert_not_called()

    def test_query_review_exception_stops_polling(self):
        self.jobs.provider.poll.side_effect=ProviderError(classify_provider_failure({'error_code':'IMAGE_SAFETY'},operation='query'))
        job=self.jobs.refresh(self.path,force=True);self.assertEqual(job['status'],'failed')
        self.jobs.refresh(self.path,force=True);self.assertEqual(self.jobs.provider.poll.call_count,1)

    def test_submission_rejection_and_unknown_are_distinct(self):
        blocked=ProviderError(classify_provider_failure({'finish_reason':'IMAGE_RECITATION'},operation='submit'))
        job=server.record_submission_failure({'id':self.nonce},blocked)
        self.assertEqual(job['status'],'failed');self.assertTrue(job['failure']['stop_generation'])
        unknown=server.record_submission_failure({'id':self.nonce},TimeoutError('secret network detail'))
        self.assertEqual(unknown['status'],'submission_unknown');self.assertNotIn('secret network detail',unknown['error'])

    def test_agent_guard_checks_only_this_tasks_attempts(self):
        agent=object.__new__(AgentTasks);agent.loaders={'character':lambda _:SimpleNamespace(directory=self.directory)}
        task={'owned':{'character':[self.entity]},'reservations':{self.entity+':'+self.nonce:{'count':1}}}
        atomic_json(self.path,{'id':self.nonce,'status':'failed','failure':{'stop_generation':True}})
        with self.assertRaisesRegex(UserError,'审核'):agent.guard_generation(task)
        atomic_json(self.path,{'id':self.nonce,'status':'submission_unknown'})
        with self.assertRaisesRegex(UserError,'提交状态'):agent.guard_generation(task)
        agent.guard_generation({**task,'reservations':{self.entity+':'+ 'c'*32:{'count':1}}})
        atomic_json(self.path,{'id':self.nonce,'status':'pending'})
        agent.guard_generation(task)

    def test_multipart_rejection_or_unknown_stops_remaining_submissions_and_replay(self):
        c=SimpleNamespace(id=self.entity,lock=threading.RLock(),check=Mock(),meta={'revision':0,'image_id':'original','settings':{'facing':'right'}})
        handler=object.__new__(server.Handler);handler.path='/api/characters/'+self.entity+'/generate-parts';handler.send=lambda value,*args,**kwargs:value
        for error in [ProviderError(classify_provider_failure({'error_code':'IMAGE_SAFETY'},operation='submit')),UserError('temporary POST timeout')]:
            request={'revision':0,'nonce':uuid.uuid4().hex,'sheets':['body','arms','legs']}
            with patch.object(server,'get_character',return_value=c),patch.object(server,'CHARACTER_JOBS',self.jobs),patch.object(server,'character_reference',return_value='https://reference.example/image.png'),patch.object(server.MATRIX,'generate_part_sheet',side_effect=error) as generate:
                first=handler.post(request);second=handler.post(request)
            self.assertEqual(generate.call_count,1);self.assertEqual(first,second);self.assertEqual(len(first),1)


if __name__=='__main__':unittest.main()
