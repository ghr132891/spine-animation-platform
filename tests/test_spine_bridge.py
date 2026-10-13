import base64,hashlib,io,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'agent'))
from spine_mcp import Bridge,PUBLIC_TOOLS


class BridgeTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.bridge=Bridge('http://127.0.0.1:8780','private-test-value');self.calls=[];self.bridge.api=self.api
    def tearDown(self):self.tmp.cleanup()
    def api(self,path,data=None):self.calls.append((path,data));return {'state':'completed','result_sha256':hashlib.sha256(b'zip-content').hexdigest(),'artifacts':[{'preview_url':'/rig.html?rig='+ 'a'*32}]}

    def test_no_token_read_write_and_download_headers(self):
        bridge=Bridge('http://127.0.0.1:8780')
        for body in (None,{'invalid_request':'validation only'}):
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'{}')) as network:
                bridge.api('/api/automation/agent/tasks',body)
            req=network.call_args.args[0]
            self.assertIsNone(req.get_header('Authorization'));self.assertEqual(req.get_header('X-spine-local'),'1')
        bridge.api=self.api
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'zip-content')) as network:
            bridge.call('spine_download',{'task_id':'a'*32,'output_path':str(self.root/'no-token.zip')})
        self.assertIsNone(network.call_args.args[0].get_header('Authorization'))

    def test_image_and_batch_are_prepared_on_caller_machine(self):
        path=self.root/'image.png';path.write_bytes(b'image-bytes');args={'nonce':'a'*32,'requirements':'制作行走动画','image_path':str(path)}
        self.bridge.call('spine_submit',args);route,data=self.calls[-1];self.assertEqual(data['max_generations'],8);self.assertEqual(data['image_preparation'],'asset-sheet');self.assertEqual(base64.b64decode(data['image']),b'image-bytes');self.assertNotIn('image_path',data);self.assertIn('image_path',args);self.assertNotIn('image_preparation',args)
        for policy,budget in [('asset-sheet',8),('auto',8),('preserve',0)]:
            self.bridge.call('spine_submit',{**args,'image_preparation':policy});prepared=self.calls[-1][1];self.assertEqual(prepared['image_preparation'],policy);self.assertEqual(prepared['max_generations'],budget)
        self.bridge.call('spine_submit',{**args,'max_generations':0});no_generation=self.calls[-1][1];self.assertEqual(no_generation['max_generations'],0);self.assertEqual(no_generation['image_preparation'],'asset-sheet')
        self.bridge.call('spine_submit',{**args,'max_generations':2});self.assertEqual(self.calls[-1][1]['max_generations'],2)
        self.bridge.call('spine_batch',{'requests':[args]});self.assertEqual(self.calls[-1][1]['requests'][0],data)
        with self.assertRaises(ValueError):self.bridge.call('spine_submit',{**args,'sources':[{'kind':'rig','id':'a'*32}]})
        with self.assertRaises(ValueError):self.bridge.call('spine_submit',{**args,'image_path':'relative.png'})

    def test_refine_and_result_routes(self):
        args={'task_id':'a'*32,'nonce':'b'*32,'requirements':'把行走慢一些'}
        self.bridge.call('spine_refine',args);self.assertEqual(self.calls[-1],('/api/automation/agent/refine',args))
        self.bridge.call('spine_preview',{'task_id':'a'*32,'animation':'walk'});self.assertEqual(self.calls[-1][1],{'view':'motion','animation':'walk'})
        self.bridge.call('spine_result',{'task_id':'a'*32});self.assertEqual(self.calls[-1][1],{'view':'inspect'})
        names={t['name']:t for t in PUBLIC_TOOLS};self.assertTrue(names['spine_preview']['annotations']['readOnlyHint']);self.assertFalse(names['spine_refine']['annotations']['readOnlyHint'])

    def test_verified_download_does_not_overwrite_other_file(self):
        target=self.root/'result.zip';args={'task_id':'a'*32,'output_path':str(target)}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'zip-content')):result=self.bridge.call('spine_download',args)
        self.assertEqual(target.read_bytes(),b'zip-content');self.assertEqual(result['output_file'],str(target))
        target.write_bytes(b'important-original')
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'zip-content')):
            with self.assertRaises(ValueError):self.bridge.call('spine_download',args)
        self.assertEqual(target.read_bytes(),b'important-original')
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'corrupted')):
            with self.assertRaises(ValueError):self.bridge.call('spine_download',{**args,'output_path':str(self.root/'another.zip')})
        self.assertFalse((self.root/'another.zip').exists())

    def test_supplemental_images_uploaded_without_mutating_request(self):
        part=self.root/'complete arm.png';part.write_bytes(b'png-bytes')
        args={'task_id':'a'*32,'nonce':'b'*32,'requirements':'绑定已补全手臂','part_images':[{'name':'fullarm','image_path':str(part)}]}
        self.bridge.call('spine_refine',args)
        wire=self.calls[-1][1]['part_images'][0]
        self.assertEqual(base64.b64decode(wire['image']),b'png-bytes');self.assertNotIn('image_path',wire)
        self.assertEqual(args['part_images'][0]['image_path'],str(part))
        with self.assertRaises(ValueError):self.bridge.call('spine_refine',{**args,'part_images':[{'name':'arm','image_path':'relative.png'}]})

    def test_detailed_preview_and_gait_route(self):
        self.bridge.call('spine_preview',{'task_id':'a'*32,'animation':'walk','frame_count':32})
        self.assertEqual(self.calls[-1][1]['frame_count'],32)
        args={'task_id':'a'*32,'animation':'walk','anchor':'hip','pairs':[{'arm':'hand','leg':'foot'}]}
        self.bridge.call('spine_gait',args)
        self.assertEqual(self.calls[-1][1],{'view':'gait',**{k:v for k,v in args.items() if k!='task_id'}})
        tool=next(t for t in PUBLIC_TOOLS if t['name']=='spine_gait');self.assertTrue(tool['annotations']['readOnlyHint'])

    def test_contact_can_diagnose_direct_delivery_or_exact_task_artifact(self):
        direct={'kind':'rig','id':'b'*32,'animation':'walk','options':{'samples':256}}
        self.bridge.call('spine_contact',direct)
        self.assertEqual(self.calls[-1],('/api/automation/agent/contact',direct))
        task={'task_id':'a'*32,'artifact_index':1,'animation':'walk','options':{'samples':128}}
        self.bridge.call('spine_contact',task)
        self.assertEqual(self.calls[-1],('/api/automation/agent/tasks/'+'a'*32+'/result-view',{'view':'contact',**{k:v for k,v in task.items() if k!='task_id'}}))
        tool=next(t for t in PUBLIC_TOOLS if t['name']=='spine_contact')
        self.assertTrue(tool['annotations']['readOnlyHint'])
        before=len(self.calls)
        for bad in ({}, {'kind':'character','id':'b'*32}, {'kind':'rig'}, {**direct,'task_id':'a'*32}, {**direct,'artifact_index':0}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.bridge.call('spine_contact',bad)
        self.assertEqual(len(self.calls),before)

    def test_worker_contact_stays_in_current_task(self):
        worker=Bridge(self.bridge.base,task='c'*32);worker.api=self.api
        args={'kind':'rig','id':'b'*32,'animation':'walk'}
        worker.call('spine_contact',args)
        self.assertEqual(self.calls[-1],('/api/automation/agent/tasks/'+'c'*32+'/tools',{'name':'spine_contact','arguments':args}))


if __name__=='__main__':unittest.main()
