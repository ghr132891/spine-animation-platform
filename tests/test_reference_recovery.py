import base64
import io
import json
import socket
import ssl
import tempfile
import time
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from app.core import png_bytes, atomic_json, UserError
from app.provider import Matrix, ProviderError, classify_provider_failure
from app.reference_health import network_error
from app.agent_tasks import AgentTasks
from app.character import CharacterProject

ROOT=Path(__file__).resolve().parents[1]


def unavailable(retryable=True):
    d=classify_provider_failure(category='reference_unavailable',operation='reference')
    d.update(retry_reference=retryable,reference_check={'stage':'readback','error':{'code':'timeout','message':'网络超时','retryable':retryable}})
    return ProviderError(d)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.m=Matrix(self.root);self.im=Image.new('RGBA',(20,20),'blue')
        self.m.tunnel.status=Mock(return_value={'provider':'quick-tunnel','ready':True,'active':True})
        self.m.tunnel.active_reference=Mock(return_value=True)
        self.m.tunnel.publish=Mock(return_value={'url':'https://test.trycloudflare.com/secret.png','expires_at':time.time()+3600,'provider':'quick-tunnel'})
        self.m.request=Mock(side_effect=AssertionError('No generation for reference checks'))
        self.sleep=patch('time.sleep');self.sleep.start()
    def tearDown(self):self.sleep.stop();self.m.tunnel.close();self.tmp.cleanup()

    def test_retry_exact_pixels_and_safe_history_then_cached(self):
        err=ProviderError({**classify_provider_failure(category='download_failed'),'network':network_error(TimeoutError('SECRET URL'))})
        self.m.download_result=Mock(side_effect=[err,err,png_bytes(self.im)])
        result=self.m.ensure_reference(self.im)
        self.assertEqual(self.m.ensure_reference(self.im),result)
        self.assertEqual(self.m.download_result.call_count,3)
        self.m.tunnel.publish.assert_called_once()
        health=self.m.reference_status();self.assertTrue(health['usable'])
        raw=(self.root/'reference-health.json').read_text()
        self.assertNotIn('SECRET',raw);self.assertNotIn('https',raw)
        self.assertEqual([e['ok'] for e in json.loads(raw)['events']],[False,False,True])
        self.m.request.assert_not_called()

    def test_exhaustion_is_not_submitted_and_stale_health_is_not_ready(self):
        self.m.download_result=Mock(side_effect=ProviderError({**classify_provider_failure(category='download_failed'),'network':network_error(socket.gaierror('SECRET'))}))
        with self.assertRaises(ProviderError) as got:self.m.ensure_reference(self.im)
        self.assertEqual(got.exception.details['submission_state'],'not-submitted')
        self.assertEqual(got.exception.details['reference_check']['error']['code'],'dns_error')
        self.assertEqual(self.m.download_result.call_count,3)
        self.assertFalse(self.m.reference_status()['usable'])
        self.assertIsNone(self.m.cached_reference(self.im))

    def test_http_tls_and_mismatched_pixels_do_not_retry_permanent_errors(self):
        for error in (urllib.error.HTTPError('https://secret',403,'SECRET',{},None),ssl.SSLError('SECRET')):
            with self.subTest(error=type(error).__name__):
                self.m.download_result=Mock(side_effect=ProviderError({**classify_provider_failure(category='download_failed'),'network':network_error(error)}))
                with self.assertRaises(ProviderError) as got:self.m.ensure_reference(self.im)
                self.assertFalse(got.exception.details['retry_reference'])
                self.m.download_result.assert_called_once()
        self.m.download_result=Mock(return_value=png_bytes(Image.new('RGBA',(20,20),'red')))
        with self.assertRaises(ProviderError) as got:self.m.ensure_reference(self.im)
        self.assertEqual(got.exception.details['reference_check']['error']['code'],'invalid_image')
        self.assertFalse(got.exception.details['retry_reference'])

    def test_force_check_revalidates_existing_link_and_never_generates(self):
        self.m.download_result=Mock(return_value=png_bytes(self.im))
        self.m.ensure_reference(self.im);self.m.ensure_reference(self.im,force=True)
        self.assertEqual(self.m.download_result.call_count,2)
        self.m.request.assert_not_called()
        path=self.root/'reference-health.json';record=json.loads(path.read_text());record['checked_at']-=301;atomic_json(path,record)
        self.assertFalse(self.m.reference_status()['usable'])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.loaders={'character':lambda id:CharacterProject(self.root/'characters'/id)}
        self.tasks=AgentTasks(self.root,ROOT,self.loaders,None)
        image=Image.new('RGBA',(160,180),(0,0,0,0));image.paste((20,120,200,255),(50,30,100,150))
        self.t=self.tasks.create({'nonce':uuid.uuid4().hex,'requirements':'制作待机和自然侧向行走',
            'image':base64.b64encode(png_bytes(image)).decode(),'max_generations':8})
        self.id=self.t['id'];self.c=self.loaders['character'](self.t['sources'][0]['id'])
        self.args={'kind':'character','id':self.c.id,'action':'ensure','revision':self.c.meta['revision']}
        self.tasks.update(self.id,state='running')
    def tearDown(self):self.tmp.cleanup()
    def due(self):
        t=self.tasks.read(self.id);r=t['reference_recovery'];r['next_check_at']=0;self.tasks.update(self.id,reference_recovery=r)
    def defer(self):
        self.tasks.api=Mock(side_effect=unavailable())
        with self.assertRaises(ProviderError):self.tasks.tool(self.id,'spine_reference',self.args)
        self.assertEqual(self.tasks.read(self.id)['state'],'waiting_reference')
        self.due()

    def test_durable_wait_recovers_same_task_and_replays_without_budget_change(self):
        self.defer()
        tasks=AgentTasks(self.root,ROOT,self.loaders,None)
        tasks.api=Mock(return_value={'cached':True})
        tasks.recover_references();self.assertEqual(tasks.read(self.id)['state'],'queued')
        tasks.update(self.id,state='running');tasks.replay_reference_request(self.id)
        self.assertEqual(tasks.read(self.id)['reference_recovery']['state'],'recovered')
        self.assertEqual(tasks.read(self.id)['reservations'],{})
        self.assertEqual(len(list(tasks.tasks.glob('*/task.json'))),1)
        tasks.replay_reference_request(self.id);self.assertEqual(tasks.api.call_count,2)

    def test_backoff_deadline_cancel_and_revision_failure(self):
        self.defer();self.tasks.recover_references()
        r=self.tasks.read(self.id)['reference_recovery'];self.assertEqual(r['attempts'],1)
        self.assertGreater(r['next_check_at'],time.time())
        self.tasks.cancel(self.id);self.due();self.tasks.api.reset_mock();self.tasks.recover_references();self.tasks.api.assert_not_called()
        self.assertEqual(self.tasks.read(self.id)['state'],'cancelled')
        self.tasks.update(self.id,state='waiting_reference',reference_recovery={**r,'deadline':0,'next_check_at':0})
        self.tasks.recover_references();self.assertEqual(self.tasks.read(self.id)['state'],'failed')
        self.assertTrue(self.tasks.reference_request_path(self.id).exists())
        self.tasks.update(self.id,state='waiting_reference',reference_recovery={**r,'next_check_at':0})
        self.tasks.api=Mock(side_effect=UserError('原图版本已变化'))
        self.tasks.recover_references();self.assertEqual(self.tasks.read(self.id)['state'],'failed')

    def test_unknown_submission_or_permanent_reference_failure_never_auto_resumes(self):
        for error in (ProviderError(classify_provider_failure(category='submission_unknown',operation='submit')),unavailable(False)):
            self.assertFalse(self.tasks.defer_reference(self.id,'spine_reference',self.args,error))
        self.assertFalse(self.tasks.reference_request_path(self.id).exists())

    def test_retry_failure_during_replay_keeps_original_deadline(self):
        self.defer();original=self.tasks.read(self.id)['reference_recovery']['deadline']
        self.tasks.api=Mock(return_value={});self.tasks.recover_references();self.tasks.update(self.id,state='running')
        self.tasks.api=Mock(side_effect=unavailable())
        with self.assertRaises(ProviderError):self.tasks.replay_reference_request(self.id)
        t=self.tasks.read(self.id);self.assertEqual(t['state'],'waiting_reference')
        self.assertEqual(t['reference_recovery']['deadline'],original)

    def test_structured_reference_error_survives_http_boundary(self):
        error=unavailable();body=io.BytesIO(json.dumps({'error':str(error),'failure':error.details}).encode())
        with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('http://local',503,'error',{},body)):
            with self.assertRaises(ProviderError) as got:self.tasks.api('/api/characters/'+self.c.id+'/reference',{})
        self.assertEqual(got.exception.details['submission_state'],'not-submitted')

    def test_generation_replay_keeps_nonce_budget_and_durable_unknown_sentinel(self):
        from app import server
        from app.jobs import JobHistory
        backend=Matrix(self.root)
        backend.status=Mock(return_value={'model':'fixture','provider':'matrix'})
        backend.job_metadata=Mock(return_value={'provider':'matrix','model':'fixture'})
        backend.cached_reference=Mock(return_value=None)
        backend.ensure_reference=Mock(side_effect=unavailable())
        backend.generate_view=Mock(return_value='existing-provider-job')
        jobs=JobHistory(self.root,backend,'characters')
        handler=object.__new__(server.Handler);handler.send=lambda value,*a,**kw:value
        def api(route,data=None,binary=False):
            handler.path=route
            return handler.post(data)
        self.tasks.api=api
        self.tasks.update(self.id,image_assessments={self.c.id:{'image_id':self.c.meta['image_id'],
            'revision':self.c.meta['revision'],'verdict':'needs_view','target_view':'right side',
            'target_actions':['idle','walk']}})
        nonce=uuid.uuid4().hex
        args={'id':self.c.id,'action':'generate-view','data':{'nonce':nonce,'revision':self.c.meta['revision'],'instructions':'Keep colors and costume'}}
        with patch.object(server,'MATRIX',backend),patch.object(server,'CHARACTER_JOBS',jobs),patch.object(server,'get_character',return_value=self.c):
            with self.assertRaises(ProviderError):self.tasks.tool(self.id,'spine_character',args)
            backend.generate_view.assert_not_called()
            budget=self.tasks.summary(self.id)['generation_budget']
            backend.ensure_reference.side_effect=None
            backend.ensure_reference.return_value={'url':'https://example.org/image.png'}
            backend.reference_status=Mock(return_value={'provider':'quick-tunnel','ready':True})
            self.due();self.tasks.recover_references()
            self.tasks.update(self.id,state='running');self.tasks.replay_reference_request(self.id)
            self.assertEqual(jobs.read(self.c.id,nonce)['gen_id'],'existing-provider-job')
            self.assertEqual(self.tasks.summary(self.id)['generation_budget'],budget)
            # Simulate restart after provider submission: replay the same action.
            self.tasks.tool(self.id,'spine_character',args)
            backend.generate_view.assert_called_once()
            record=jobs.read(self.c.id,nonce);record.update(status='submission_unknown');record.pop('gen_id')
            atomic_json(jobs.path(self.c.id,nonce),record)
            result=self.tasks.tool(self.id,'spine_character',args)
            self.assertEqual(result['status'],'submission_unknown')
            backend.generate_view.assert_called_once()
            self.assertEqual(self.tasks.summary(self.id)['generation_budget'],budget)
        backend.tunnel.close()
