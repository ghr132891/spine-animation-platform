import base64,copy,io,json,math,os,subprocess,sys,tempfile,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core import Project,UserError,sha,atomic_json
from app.rig import RigProject
from app.rig_automation import RigAutomation,mapped_animation
from app.agent_tasks import AgentTasks,resource_digest


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.storage=Path(self.tmp.name)
        self.project=Project(self.storage/'projects'/uuid.uuid4().hex,{p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()},'robot')
        self.rig=RigProject.create(self.storage/'rigs'/uuid.uuid4().hex,self.project)
        self.loaders={'project':lambda id:Project(self.storage/'projects'/id),'rig':lambda id:RigProject(self.storage/'rigs'/id)}
        self.edits=RigAutomation(self.storage,self.loaders['rig']);self.tasks=AgentTasks(self.storage,ROOT,self.loaders,self.edits)
        self.tasks.api=self.api

    def tearDown(self):self.tmp.cleanup()

    def api(self,route,data=None,binary=False):
        bits=route.strip('/').split('/');rig=self.loaders['rig'](bits[2])
        if len(bits)==3:return rig.summary()
        if bits[3]=='export':return rig.export()
        if bits[3]=='validate':return rig.validate()
        raise AssertionError(route)

    def request(self,**kw):return {'nonce':uuid.uuid4().hex,'requirements':'保留贴图，调整头部比例并输出行走动画','sources':[{'kind':'rig','id':self.rig.id}],'max_generations':0,**kw}

    def test_open_by_default_and_optional_protected_mode(self):
        with patch.dict(os.environ,{'SPINE_AGENT_REQUIRE_TOKEN':'0'}):
            opened=AgentTasks(self.storage,ROOT,self.loaders,self.edits)
        self.assertTrue(opened.authorized(None));self.assertTrue(opened.authorized('irrelevant'))
        with patch.dict(os.environ,{'SPINE_AGENT_REQUIRE_TOKEN':'1'}):
            protected=AgentTasks(self.storage,ROOT,self.loaders,self.edits)
        self.assertFalse(protected.authorized(None));self.assertFalse(protected.authorized('Bearer wrong'))
        self.assertTrue(protected.authorized('Bearer '+protected.token))
        self.assertEqual(protected.token,opened.token)

    def test_mcp_initializes_without_token(self):
        env={k:v for k,v in os.environ.items() if k not in ('SPINE_AGENT_TOKEN','SPINE_AGENT_TOKEN_FILE')}
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','id':2,'method':'tools/list'}]
        client=self.storage/'client/spine_mcp.py';client.parent.mkdir();client.write_bytes((ROOT/'agent/spine_mcp.py').read_bytes())
        p=subprocess.run([sys.executable,str(client)],input='\n'.join(map(json.dumps,requests))+'\n',text=True,capture_output=True,env=env)
        self.assertEqual(p.returncode,0,p.stderr)
        results=list(map(json.loads,p.stdout.splitlines()));self.assertEqual(len(results),2)
        self.assertEqual(results[0]['result']['serverInfo']['version'],(ROOT/'VERSION').read_text().strip())
        self.assertEqual(len(results[1]['result']['tools']),16)
        submit=next(tool for tool in results[1]['result']['tools'] if tool['name']=='spine_submit')
        preparation=submit['inputSchema']['properties']['image_preparation']
        self.assertEqual(preparation['default'],'asset-sheet');self.assertEqual(set(preparation['enum']),{'asset-sheet','auto','preserve'})

    def test_atomic_batch_idempotency_failure_and_source_preserved(self):
        original=(self.rig.directory/'rig.json').read_bytes()
        req={'source_rig_id':self.rig.id,'revision':0,'nonce':uuid.uuid4().hex,'operations':[{'action':'bone','bone':'head','scaleX':1.1},{'action':'animation','name':'nod','animation':{'bones':{'head':{'rotate':[{'time':0,'value':0},{'time':.5,'value':10},{'time':1,'value':0}]}}}}]}
        result=self.edits.create(req);self.assertEqual(result,self.edits.create(req));self.assertEqual((self.rig.directory/'rig.json').read_bytes(),original)
        changed=self.loaders['rig'](result['rig_id']);self.assertIn('nod',changed.state['skeleton']['animations']);self.assertTrue(changed.validate()['passed'])
        self.assertEqual(self.tasks.runtime_check(changed.export())['passed'],True)
        before=set((self.storage/'rigs').glob('*'))
        with self.assertRaises(UserError):self.edits.create({**req,'nonce':uuid.uuid4().hex,'operations':[req['operations'][0],{'action':'bone','bone':'hip','parent':'head'}]})
        self.assertEqual(before,set((self.storage/'rigs').glob('*')))
        with self.assertRaises(UserError):self.edits.create({**req,'operations':[req['operations'][0]]})

    def test_mapping_does_not_silently_drop_constraints(self):
        source=copy.deepcopy(self.rig.meta);self.rig.meta['state']['skeleton']['animations']['bad']={'bones':{},'ik':{'foo':[]}}
        with self.assertRaises(UserError):mapped_animation(self.rig,self.rig,{'source_animation':'bad'})
        self.rig.meta=source
        clip=mapped_animation(self.rig,self.rig,{'source_animation':'walk','time_scale':2})
        self.assertEqual(set(clip['bones']),set(self.rig.state['skeleton']['animations']['walk']['bones']))

    def test_snapshot_deduplication_budget_and_scope(self):
        req=self.request();t=self.tasks.create(req);self.assertEqual(t,self.tasks.create(req))
        entity=t['sources'][0]['id'];self.assertNotEqual(entity,self.rig.id)
        with self.assertRaises(UserError):self.tasks.tool(t['id'],'spine_inspect',{'kind':'rig','id':self.rig.id})
        with self.assertRaises(UserError):self.tasks.reserve(self.tasks.read(t['id']),'paid',1,{})
        self.assertEqual(self.tasks.summary(t['id'])['generation_budget']['used_or_reserved'],0)
        with self.assertRaises(UserError):self.tasks.create({**req,'requirements':'改成另一个完全不同的人物'})
        record=self.tasks.read(t['id']);record['max_generations']=3;atomic_json(self.tasks.path(t['id']),record)
        self.tasks.reserve(record,'paid',3,{'prompt':'a'});self.tasks.reserve(record,'paid',3,{'prompt':'a'})
        with self.assertRaises(UserError):self.tasks.reserve(record,'new',1,{})
        self.assertEqual(sum(v['count'] for v in record['reservations'].values()),3)
        reloaded=AgentTasks(self.storage,ROOT,self.loaders,self.edits);self.assertEqual(reloaded.read(t['id'])['reservations'],record['reservations'])

    def test_batch_partial_errors_and_cancel_durable(self):
        req=self.request();result=self.tasks.batch({'requests':[req,req,{**self.request(),'max_generations':-1}]})
        self.assertEqual([v['ok'] for v in result['results']],[True,True,False]);id=result['results'][0]['task']['id']
        stale=self.tasks.read(id);self.tasks.cancel(id);self.tasks.save_tool_state(stale)
        self.assertEqual(self.tasks.read(id)['state'],'cancelled')
        with self.assertRaises(UserError):self.tasks.tool(id,'spine_context',{})

    def test_delivery_requires_exact_motion_preview_and_exports(self):
        task=self.tasks.create(self.request());id=task['id'];entity=task['sources'][0]['id']
        result={'outcome':'completed','summary':'done','artifacts':[{'kind':'rig','id':entity,'version':'','summary':'robot'}]}
        with self.assertRaises(UserError):self.tasks.finish(id,result)
        rig=self.loaders['rig'](entity)
        for name in rig.state['skeleton']['animations']:
            preview=self.tasks.tool(id,'spine_motion',{'kind':'rig','id':entity,'animation':name,'frame_count':16})
            self.assertEqual(preview['content'][1]['type'],'image')
        with self.assertRaises(UserError):self.tasks.finish(id,result)
        for name in rig.state['skeleton']['animations']:
            self.tasks.tool(id,'spine_closeup',{'kind':'rig','id':entity,'animation':name})
            self.tasks.tool(id,'spine_review_closeup',{'kind':'rig','id':entity,'animation':name,'verdict':'ready','reason':'实图头颈连接连续，无疑似白底截口'})
        self.tasks.update(id,state='failed',error='earlier attempt failed')
        self.tasks.finish(id,result);final=self.tasks.summary(id);self.assertEqual(final['state'],'completed')
        self.assertIsNone(final.get('error'))
        with zipfile.ZipFile(io.BytesIO(self.tasks.download(id))) as z:self.assertIn('manifest.json',z.namelist())
        self.assertTrue(final['artifacts'][0]['runtime_validation']['samples']>1)

    def test_default_four_repairs_and_explicit_limit(self):
        task=self.tasks.create(self.request())
        self.assertEqual(task['max_repairs'],4)
        self.assertEqual(self.tasks.create(self.request(max_repairs=1))['max_repairs'],1)
        context=self.tasks.tool(task['id'],'spine_context',{})
        self.assertEqual(context['edit_budget'],{'used':0,'limit':5})

    def test_failed_visual_result_delivers_loadable_draft(self):
        task=self.tasks.create(self.request());id=task['id'];entity=task['sources'][0]['id']
        result={'outcome':'failed','summary':'肩缘仍待修','artifacts':[{'kind':'rig','id':entity,'summary':'比例已调好，肩缘仍待修'}]}
        self.tasks.finish(id,result)
        final=self.tasks.read(id)
        self.assertEqual(final['state'],'failed');self.assertEqual(final['error'],'肩缘仍待修')
        self.assertEqual(final['edits'],0)
        self.assertEqual(self.tasks.result_target(id)[3].id,entity)
        self.assertEqual(final['draft_artifacts'][0]['visual_acceptance']['status'],'draft')
        path=self.storage/'workflow-deliveries'/(id+'.json')
        delivery=json.loads(path.read_text());self.assertEqual(delivery['rig_id'],entity)
        self.tasks.finish(id,result)
        self.assertEqual(json.loads(path.read_text())['history'],[])
        from app.web_workflows import WebWorkflows
        web=WebWorkflows(self.tasks)
        visible=web.read({'owner':True,'id':'local'},id)
        self.assertEqual(visible['artifacts'][0]['rig_id'],entity)
        self.assertEqual(visible['state'],'failed');self.assertEqual(visible['delivery_task_id'],id)

    def test_draft_tool_scoped_and_cancelled(self):
        task=self.tasks.create(self.request());id=task['id'];entity=task['sources'][0]['id']
        with self.assertRaises(UserError):self.tasks.tool(id,'spine_deliver_draft',{'id':self.rig.id,'summary':'unowned'})
        self.assertFalse((self.storage/'workflow-deliveries'/(id+'.json')).exists())
        draft=self.tasks.tool(id,'spine_deliver_draft',{'id':entity,'summary':'肩缘待修'})
        self.assertEqual(draft['visual_acceptance']['status'],'draft')
        self.assertEqual(self.tasks.read(id)['state'],'queued')
        self.tasks.cancel(id)
        with self.assertRaises(UserError):self.tasks.publish_draft(id,entity,'不应发布')

    def test_runtime_38_and_broken_timeline(self):
        with zipfile.ZipFile(ROOT/'tests/fixtures/robot38.zip') as z:files={n:z.read(n) for n in z.namelist()}
        p=Project(self.storage/'projects'/uuid.uuid4().hex,files,'3.8');rig=RigProject.create(self.storage/'rigs'/uuid.uuid4().hex,p)
        self.assertTrue(self.tasks.runtime_check(rig.export())['passed'])
        files,_=self.rig.files();s=json.loads(files['skeleton.json']);s['animations']['broken']={'bones':{'head':{'rotate':[{'time':0,'value':float('nan')}]}}};files={**files,'skeleton.json':json.dumps(s).encode()}
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for n,data in files.items():z.writestr(n,data)
        with self.assertRaises(UserError):self.tasks.runtime_check(out.getvalue())

    def test_zip_digest_ignores_container_timestamps(self):
        def package(year):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:z.writestr(zipfile.ZipInfo('file',(year,1,1,0,0,0)),b'content')
            return out.getvalue()
        self.assertNotEqual(sha(package(2020)),sha(package(2021)));self.assertEqual(resource_digest(package(2020)),resource_digest(package(2021)))

    def test_contact_is_readonly_and_worker_cannot_read_other_rig(self):
        task=self.tasks.create(self.request());entity=task['sources'][0]['id']
        rig=self.loaders['rig'](entity);before=(rig.directory/'rig.json').read_bytes()
        assessed={'status':'assessed','animation':'walk','checks':{'contact':True},'limits':['not a natural-gait certification']}
        with patch('app.contact_diagnostics.diagnose_contact',return_value=assessed) as diagnose:
            result=self.tasks.tool(task['id'],'spine_contact',{'kind':'rig','id':entity,'animation':'walk','options':{'samples':128}})
            self.assertEqual(diagnose.call_args.args[2],'walk');self.assertEqual(diagnose.call_args.args[3]['samples'],128)
            self.assertEqual(diagnose.call_args.args[3]['skeleton_name'],'skeleton.json')
            with self.assertRaises(UserError):self.tasks.tool(task['id'],'spine_contact',{'kind':'rig','id':self.rig.id})
        self.assertEqual(result['resource_sha256'],resource_digest(rig.export()))
        self.assertEqual((rig.directory/'rig.json').read_bytes(),before)
        self.assertEqual(self.tasks.summary(task['id'])['generation_budget']['used_or_reserved'],0)

    def test_contact_rejects_unsafe_export_before_running_runtime(self):
        for name in ('../outside.json','/absolute.json','bad\\name.json'):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:z.writestr(name,'{}')
            with patch('app.contact_diagnostics.diagnose_contact') as diagnose:
                with self.subTest(name=name),self.assertRaises(UserError):self.tasks.contact_check(out.getvalue())
                diagnose.assert_not_called()

    def test_mcp_stdio_protocol_and_no_secret_output(self):
        env={**os.environ,'SPINE_AGENT_TOKEN':'test-not-a-real-secret'}
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'}, {'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'provider-connect','arguments':{}}}]
        p=subprocess.run([sys.executable,str(ROOT/'agent/spine_mcp.py')],input='\n'.join(map(json.dumps,requests))+'\n',text=True,capture_output=True,env=env)
        results=list(map(json.loads,p.stdout.splitlines()));self.assertEqual(len(results),3)
        self.assertEqual(results[0]['result']['protocolVersion'],'2024-11-05');self.assertTrue(results[2]['result']['isError'])
        self.assertNotIn(env['SPINE_AGENT_TOKEN'],p.stdout+p.stderr)

    def test_refinement_uses_final_rig_and_preserves_parent(self):
        parent=self.tasks.create(self.request());rig_id=parent['sources'][0]['id']
        for name in self.loaders['rig'](rig_id).state['skeleton']['animations']:
            self.tasks.tool(parent['id'],'spine_motion',{'kind':'rig','id':rig_id,'animation':name,'frame_count':16})
        self.review_neck(parent['id'],rig_id)
        self.tasks.finish(parent['id'],{'outcome':'completed','summary':'已完成','artifacts':[{'kind':'rig','id':rig_id,'version':''}]})
        original=(self.storage/'rigs'/rig_id/'rig.json').read_bytes()
        req={'nonce':uuid.uuid4().hex,'task_id':parent['id'],'requirements':'保持已有贴图和动作，只把头放大2%'}
        child=self.tasks.refine(req);self.assertEqual(child,self.tasks.refine(req));self.assertEqual(child['parent_task_id'],parent['id'])
        self.assertEqual(child['max_generations'],0);self.assertEqual(len(child['sources']),1)
        clone_id=child['sources'][0]['id'];self.assertNotEqual(clone_id,rig_id)
        self.assertEqual(resource_digest(self.loaders['rig'](clone_id).export()),resource_digest(self.loaders['rig'](rig_id).export()))
        self.assertEqual((self.storage/'rigs'/rig_id/'rig.json').read_bytes(),original)
        inspected=self.tasks.result_view(parent['id'],{'view':'inspect'});self.assertEqual(inspected['id'],rig_id)
        with self.assertRaises(UserError):self.tasks.refine({**req,'requirements':'换成另一个人物'})
        with self.assertRaises(UserError):self.tasks.refine({**req,'nonce':uuid.uuid4().hex,'artifact_index':3})
        changed=self.loaders['rig'](rig_id);state=copy.deepcopy(changed.state);state['skeleton']['bones'][0]['x']=20;changed.commit(state,'external edit')
        with self.assertRaises(UserError):self.tasks.refine({**req,'nonce':uuid.uuid4().hex})
        self.assertEqual(self.tasks.refine(req)['id'],child['id'])
        with self.assertRaises(UserError):self.tasks.result_view(child['id'],{'view':'inspect'})

    def review_neck(self,task,entity):
        for name in self.loaders['rig'](entity).state['skeleton']['animations']:
            self.tasks.tool(task,'spine_closeup',{'kind':'rig','id':entity,'animation':name})
            self.tasks.tool(task,'spine_review_closeup',{'kind':'rig','id':entity,'animation':name,'verdict':'ready','reason':'查看实图后确认连接连续'})

    def completed_parent(self):
        t=self.tasks.create(self.request());entity=t['sources'][0]['id']
        for name in self.loaders['rig'](entity).state['skeleton']['animations']:
            self.tasks.tool(t['id'],'spine_motion',{'kind':'rig','id':entity,'animation':name,'frame_count':16})
        self.review_neck(t['id'],entity)
        self.tasks.finish(t['id'],{'outcome':'completed','summary':'已完成','artifacts':[{'kind':'rig','id':entity,'version':''}]})
        return t,entity

    def test_import_supplemental_parts_into_refinement_only(self):
        from PIL import Image
        from app.core import png_bytes
        parent,entity=self.completed_parent();original=self.loaders['rig'](entity);before=(original.directory/'rig.json').read_bytes()
        blob=png_bytes(Image.new('RGBA',(10,20),(100,120,130,255)))
        req={'task_id':parent['id'],'nonce':uuid.uuid4().hex,'requirements':'绑定补全手臂，原待机不变','part_images':[{'name':'complete_arm','image':base64.b64encode(blob).decode()}]}
        child=self.tasks.refine(req);self.assertEqual(child,self.tasks.refine(req))
        clone=self.loaders['rig'](child['sources'][0]['id'])
        self.assertEqual(clone.state['skeleton'],original.state['skeleton'])
        self.assertEqual(clone.state['parts'][:-1],original.state['parts'])
        self.assertEqual(clone.blob(clone.state['parts'][-1]),blob)
        self.assertEqual((original.directory/'rig.json').read_bytes(),before)
        self.assertEqual(child['parent_task_id'],parent['id']);self.assertEqual(child['generation_budget']['used_or_reserved'],0)
        self.assertNotIn(req['part_images'][0]['image'],json.dumps(child))
        for bad in [[{'name':'../arm','image':req['part_images'][0]['image']}],[{'name':'arm','image':'not-base64'}],req['part_images']*2]:
            with self.assertRaises(UserError):self.tasks.refine({**req,'nonce':uuid.uuid4().hex,'part_images':bad})
        parts_before=set((self.storage/'rigs').iterdir())
        with self.assertRaises(UserError):self.tasks.refine({**req,'nonce':uuid.uuid4().hex,'part_images':[{'name':original.state['parts'][0]['name'],'image':req['part_images'][0]['image']}]})
        self.assertEqual(set((self.storage/'rigs').iterdir()),parts_before)

    def test_frame_count_and_phase_diagnostics(self):
        from PIL import Image
        report,png=self.tasks.runtime_check(self.rig.export(),'walk',16)
        self.assertEqual(Image.open(io.BytesIO(png)).size,(1200,1600));self.assertTrue(report['passed'])
        for count in [0,3,33,True]:
            with self.assertRaises(UserError):self.tasks.runtime_check(self.rig.export(),'walk',count)
        state=copy.deepcopy(self.rig.state);s=state['skeleton']
        s['bones'].extend([{'name':'audit-arm','parent':'root','length':10},{'name':'audit-leg','parent':'root','length':10}])
        gait={'anchor':'root','pairs':[{'arm':'audit-arm','leg':'audit-leg'}]}
        for mode,phase,sign,expected in [('opposed',0,-1,'opposed'),('same',0,1,'same-direction'),('quarter',math.pi/2,-1,'phase-offset-or-small-motion')]:
            s['animations']['test-gait']={'bones':{bone:{'translate':[{'time':i/16,'x':round(20*direction*math.cos(math.tau*i/16+p),6)} for i in range(17)]} for bone,p,direction in [('audit-arm',phase,1),('audit-leg',0,sign)]}}
            self.rig.commit(state,'synthetic '+mode)
            result,_=self.tasks.runtime_check(self.rig.export(),'test-gait',8,gait)
            pair=result['gait']['pairs'][0];self.assertEqual(pair['assessment'],expected)
            self.assertLess(result['gait']['loopMaxVertexError'],.001)
            if mode=='quarter':self.assertEqual(pair['armShiftSamples'],24)
        with self.assertRaises(UserError):self.tasks.runtime_check(self.rig.export(),'walk',8,{'anchor':'missing','pairs':gait['pairs']})


if __name__=='__main__':unittest.main()
