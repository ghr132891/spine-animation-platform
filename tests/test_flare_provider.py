import io,json,tempfile,unittest,uuid,urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from app.provider import Matrix,DEFAULT_MODEL,LEGACY_MODEL,ProviderError,classify_provider_failure,failure_details
from app.core import UserError,atomic_json
from app.jobs import JobHistory

class FlareProviderTests(unittest.TestCase):
    def test_every_generation_entry_uses_flare_and_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Matrix(tmp);m.request=Mock(return_value={'data':{'gen_id':'remote'}})
            actions=[lambda:m.generate('blue linen'),lambda:m.generate('blue linen','https://assets.example/part.png',400,700),lambda:m.generate_character('A friendly warrior'),lambda:m.generate_view('Same character, side view','https://assets.example/hero.png'),lambda:m.generate_part_sheet('arms','https://assets.example/hero.png')]
            for action in actions:
                self.assertEqual(action(),'remote')
                route,wire=m.request.call_args.args
                self.assertEqual(route,'/models/'+DEFAULT_MODEL+'/generate');self.assertEqual(wire['request_num'],1)
                self.assertEqual(wire['input']['quality'],'high');self.assertEqual(wire['input']['output_format'],'png')
                self.assertNotIn('ref_images',wire['input'])
            self.assertEqual(m.status()['model'],DEFAULT_MODEL)
    def test_invalid_reference_never_submits_or_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Matrix(tmp);m.request=Mock()
            for ref in ['data:image/png;base64,eA==','http://assets.example/a.png','https://172.16.7.76/a.png','https://localhost/a.png','https://user:pass@assets.example/a.png']:
                with self.assertRaises(UserError):m.generate_view('Preserve identity',ref)
            m.request.assert_not_called()
    def test_legacy_polling_and_flare_provenance_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Matrix(tmp);m.request=Mock(return_value={'data':{'status':'pending'}})
            history=JobHistory(tmp,m);project=uuid.uuid4().hex
            for model in [None,DEFAULT_MODEL,LEGACY_MODEL]:
                job={'id':uuid.uuid4().hex,'gen_id':'same-remote','status':'pending'}
                if model:job['model']=model
                path=history.path(project,job['id']);atomic_json(path,job)
                JobHistory(tmp,m).refresh(path)
                self.assertEqual(m.request.call_args.args[0],'/models/'+(model or LEGACY_MODEL)+'/same-remote/status')
                self.assertEqual(json.loads(path.read_text()).get('model'),model)
            self.assertEqual(m.request.call_count,3)
    def test_supported_aspect_ratios_only(self):
        ratios={'1:1','2:3','3:2','3:4','4:3','9:16','16:9','21:9'}
        for width,height in [(1,8),(8,1),(4,5),(5,4),(300,650)]:
            self.assertIn(Matrix.generation_input('cloth','https://assets.example/a.png',width,height)['aspect_ratio'],ratios)

    def test_review_failure_is_terminal_and_never_exposes_upstream_content(self):
        payload={'data':{'outputs':[{'item_status':'failed','error':{'finish_reason':'IMAGE_RECITATION',
                 'message':'sk-test-secret-123 https://assets.example/signed?token=private'}}]},
                 'input':{'prompt':'private prompt','input_images':['data:image/png;base64,private']}}
        result=classify_provider_failure(payload,category='empty_output')
        self.assertEqual(result['category'],'image_recitation')
        self.assertTrue(result['stop_generation'])
        for field in ('auto_retry','retry_same_task','new_submission_allowed','rewrite_retry_allowed'):
            self.assertFalse(result[field])
        serialized=json.dumps(result,ensure_ascii=False)
        for private in ('sk-test-secret','signed?token','private prompt','base64'):
            self.assertNotIn(private,serialized)
        self.assertIn('停止自动重试',result['next_action'])

    def test_content_reason_is_read_without_scanning_the_input_prompt(self):
        result=classify_provider_failure({'data':{'error':{'code':'content_policy_violation'}}})
        self.assertEqual(result['category'],'content_review')
        result=classify_provider_failure({'data':{'error':'temporary failure','input':{'prompt':'SAFETY IMAGE_RECITATION'}}})
        self.assertEqual(result['category'],'generation_failed')

    def test_camel_case_reasons_and_review_codes_preserve_safe_query_states(self):
        for field,code in (('finishReason','IMAGE_RECITATION'),('blockReason','PROHIBITED_CONTENT'),
                           ('errorCode','SAFETY'),('blockedReason','BLOCKED')):
            with self.subTest(field=field):
                result=classify_provider_failure({'data':{'outputs':[{field:code}]}})
                self.assertTrue(result['stop_generation'])
                self.assertFalse(result['auto_retry'])
        for status in ('pending','completed'):
            result=classify_provider_failure({'data':{'status':status,
                  'input':{'prompt':'BLOCKED SAFETY'},'inputImages':['data:image/png;base64,SAFETY'],
                  'imageUrl':'https://assets.example/IMAGE_RECITATION.png',
                  'message':'https://assets.example/SAFETY.png'}})
            self.assertFalse(result['stop_generation'])
        result=classify_provider_failure({'errorCode':'CONTENT_POLICY_VIOLATION',
                  'message':'no permission'},http_status=403)
        self.assertEqual(result['category'],'content_review')

    def test_permission_403_is_not_assumed_to_be_content_review(self):
        for payload in ({},{'error':{'message':'Insufficient safety permissions'}}):
            result=classify_provider_failure(payload,http_status=403,operation='submit')
            self.assertEqual(result['category'],'credentials')
            self.assertEqual(result['submission_state'],'rejected')
            self.assertFalse(result['stop_generation'])
        result=classify_provider_failure({'error':{'code':'IMAGE_RECITATION'}},http_status=403,operation='submit')
        self.assertEqual(result['category'],'image_recitation')

    def test_poll_retry_contract_never_authorizes_another_generation(self):
        for category in ('rate_limit','connection_failed','download_failed','service_unavailable'):
            with self.subTest(category=category):
                result=classify_provider_failure(category=category)
                self.assertTrue(result['retry_same_task'])
                self.assertFalse(result['auto_retry'])
                self.assertFalse(result['new_submission_allowed'])
                self.assertFalse(result['rewrite_retry_allowed'])
                submission=classify_provider_failure(category=category,operation='submit')
                self.assertFalse(submission['retry_same_task'])

    def test_submit_timeout_is_unknown_and_only_attempted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            matrix=Matrix(tmp);matrix.credential=Mock(return_value=('sk-test-key','fixture'))
            opener=Mock();opener.open.side_effect=TimeoutError()
            with patch('app.provider.urllib.request.build_opener',return_value=opener):
                with self.assertRaises(ProviderError) as raised:
                    matrix.generate('original blue woven fabric')
            self.assertEqual(opener.open.call_count,1)
            result=failure_details(raised.exception)
            self.assertEqual(result['category'],'submission_unknown')
            self.assertEqual(result['submission_state'],'unknown')
            self.assertFalse(result['auto_retry'])

    def test_http_failure_reads_reason_once_but_does_not_resubmit_or_echo_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            matrix=Matrix(tmp);matrix.credential=Mock(return_value=('sk-test-key','fixture'))
            body=json.dumps({'error':{'code':'IMAGE_RECITATION','message':'sk-private-key'}}).encode()
            error=urllib.error.HTTPError('https://api-matrix.tuyoo.com/model',400,'Bad request',{},io.BytesIO(body))
            opener=Mock();opener.open.side_effect=error
            with patch('app.provider.urllib.request.build_opener',return_value=opener):
                with self.assertRaises(ProviderError) as raised:
                    matrix.generate_part_sheet('arms','https://assets.example/reference.png')
            self.assertEqual(opener.open.call_count,1)
            self.assertEqual(failure_details(raised.exception)['category'],'image_recitation')
            self.assertNotIn('sk-private-key',str(raised.exception))

    def test_invalid_submission_response_remains_unknown_without_second_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            matrix=Matrix(tmp)
            for response in ({'data':{}},{'data':['wrong']},{'data':{'gen_id':True}},{'data':{'gen_id':{}}}):
                with self.subTest(response=response):
                    matrix.request=Mock(return_value=response)
                    with self.assertRaises(ProviderError) as raised:matrix.generate_character('Original blue warrior')
                    self.assertEqual(failure_details(raised.exception)['submission_state'],'unknown')
                    matrix.request.assert_called_once()

    def test_missing_credentials_are_known_rejection_before_any_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            matrix=Matrix(tmp);matrix.credential=Mock(return_value=('','fixture'))
            with patch('app.provider.urllib.request.build_opener') as opener:
                with self.assertRaises(ProviderError) as raised:matrix.generate('original textile')
            opener.assert_not_called()
            self.assertEqual(failure_details(raised.exception)['category'],'credentials')
            self.assertEqual(failure_details(raised.exception)['submission_state'],'rejected')

    def test_unknown_submit_user_error_cannot_claim_safe_paid_retry(self):
        details=failure_details(UserError('untrusted message with private tokens'),operation='submit')
        self.assertEqual(details['submission_state'],'unknown')
        self.assertFalse(details['auto_retry'])
        self.assertNotIn('private tokens',json.dumps(details))

    def test_reskin_prompt_matches_artwork_language_and_preserves_original_geometry(self):
        english=Matrix.generation_input('blue woven fabric','https://assets.example/part.png')['prompt']
        chinese=Matrix.generation_input('冰雪蓝白织物，保留原纽扣','https://assets.example/part.png')['prompt']
        self.assertIn('mounting positions',english)
        self.assertNotIn('body-part',english)
        self.assertNotIn('joint',english)
        self.assertIn('保持原姿态、轮廓、连接位置',chinese)
        self.assertNotIn('Target style',chinese)
        self.assertIn('冰雪蓝白织物，保留原纽扣',chinese)

if __name__=='__main__':unittest.main()
