import copy,json,tempfile,unittest,uuid
from pathlib import Path
from app.agent_tasks import AgentTasks
from app.character import CharacterProject
from app.core import Project,UserError,png_bytes
from app.rig import RigProject
from app.web_workflows import WebWorkflows,WebError
from app.asset_integrity import ASSEMBLY_CHECKS
from app.orientation_review import ASSEMBLY_CHECKS as ORIENTATION_CHECKS
from app.image_preflight import JOINTS
from tests.test_asset_sheet_workflow import asset_fixture,assembly_guide

ROOT=Path(__file__).resolve().parents[1]

class MaterialAnimationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.storage=Path(self.temp.name)
        self.loaders={'character':lambda id:CharacterProject(self.storage/'characters'/id),'rig':lambda id:RigProject(self.storage/'rigs'/id),'project':lambda id:Project(self.storage/'projects'/id)}
        self.agent=AgentTasks(self.storage,ROOT,self.loaders,None);self.web=WebWorkflows(self.agent);self.owner,_=self.web.new_session(True)
        c=CharacterProject.create(self.storage,'fixture');image,layout=asset_fixture();c.set_image(png_bytes(image),0)
        c.update({'revision':c.meta['revision'],'landmarks':assembly_guide(),'settings':c.meta['settings']});c.apply_source_layout({'revision':c.meta['revision'],'parts':layout})
        parent=self.agent.create({'nonce':uuid.uuid4().hex,'requirements':'验收并交付完整素材','output_scope':'materials','image_preparation':'preserve','sources':[{'kind':'character','id':c.id}], '_web_session_id':self.owner['id']})
        self.parent=parent['id'];self.source=parent['sources'][0]['id'];c=self.loaders['character'](self.source);report,_=c.prebind_evidence()
        c.review_prebind({'revision':c.meta['revision'],'digest':report['digest'],'checks':dict.fromkeys(report['checks'],True),'verdict':'ready','reason':'Synthetic complete material fixture checked'})
        self.agent.update(self.parent,prebind_seen={c.id:{'prebind-'+n:report['digest'] for n in ('parts','assembly','spread')}})
        self.agent.finish(self.parent,{'outcome':'completed','summary':'fixture accepted','artifacts':[]})

    def test_web_handoff_uses_accepted_source_and_deduplicates(self):
        request={'nonce':uuid.uuid4().hex};before=(self.storage/'characters'/self.source/'character.json').read_bytes()
        a=self.web.animate_materials(self.owner,self.parent,request);b=self.web.animate_materials(self.owner,self.parent,request)
        self.assertEqual(a['id'],b['id']);raw=self.agent.read(a['id'])
        self.assertEqual(raw['max_generations'],0);self.assertTrue(raw['material_animation'])
        self.assertEqual(raw['sources'][0]['original_id'],self.source);self.assertEqual(a['workflow_id'],self.parent)
        self.assertEqual(raw['connection_contract'],'assembly-connections-v3');self.assertEqual(raw['detail_contract'],'articulation-v1')
        self.assertEqual((self.storage/'characters'/self.source/'character.json').read_bytes(),before)
        with self.assertRaises(WebError):self.web.animate_materials(self.owner,self.parent,{'nonce':uuid.uuid4().hex})

    def test_foreign_session_and_changed_material_are_rejected(self):
        other,_=self.web.new_session()
        with self.assertRaises(WebError):self.web.animate_materials(other,self.parent,{'nonce':uuid.uuid4().hex})
        c=self.loaders['character'](self.source);c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'head':{'dx':2}},'draw_order':c.review_order([p['name'] for p in c.parts()])}})
        with self.assertRaises(UserError):self.web.animate_materials(self.owner,self.parent,{'nonce':uuid.uuid4().hex})

    def test_direct_submission_of_accepted_material_uses_same_automatic_default(self):
        task=self.agent.create({'nonce':uuid.uuid4().hex,'requirements':'复用已验收素材制作待机行走','image_preparation':'preserve','max_generations':0,'sources':[{'kind':'character','id':self.source}]})
        self.assertTrue(self.agent.read(task['id'])['material_animation'])
        self.assertEqual(task['connection_contract'],'assembly-connections-v3')

    def test_assessment_automatically_builds_real_rig_once_and_publishes_draft(self):
        task=self.web.animate_materials(self.owner,self.parent,{'nonce':uuid.uuid4().hex});raw=self.agent.read(task['id']);cid=raw['sources'][0]['id'];c=self.loaders['character'](cid)
        def api(route,data=None,binary=False):
            action=route.rsplit('/',1)[-1];entity=self.loaders['character'](cid)
            if binary and action=='image':return png_bytes(entity.image())
            if binary and action=='assembly':return png_bytes(entity.assembly())
            raise AssertionError(route)
        self.agent.api=api
        from tests.stress_fixture import accept_synthetic_stress
        accept_synthetic_stress(self.loaders['character'](cid))
        for view in ('image','assembly'):self.agent.tool(task['id'],'spine_image',{'kind':'character','id':cid,'view':view})
        args={'id':cid,'verdict':'ready','source_kind':'character','parts_reviewed':True,'camera_view':'three-quarter-side','direction_consistent':True,
              'observed_view':'three-quarter side','target_view':'right side','reason':'Synthetic fixture has reviewed matching joints',
              'target_actions':['idle','walk'],'missing_parts':[],'joint_review':dict.fromkeys(JOINTS,True),
              'material_checks':dict.fromkeys(ASSEMBLY_CHECKS,True),'orientation_checks':dict.fromkeys(ORIENTATION_CHECKS,True)}
        incomplete=copy.deepcopy(args);incomplete.pop('orientation_checks')
        with self.assertRaises(UserError):self.agent.tool(task['id'],'spine_assess_image',incomplete)
        before={n:r['image_id'] for n,r in c.meta['part_layers'].items()}
        result=self.agent.tool(task['id'],'spine_assess_image',args);again=self.agent.tool(task['id'],'spine_assess_image',args)
        self.assertEqual(result['automatic_animation'],again['automatic_animation'])
        rid=result['automatic_animation']['rig_id'];rig=self.loaders['rig'](rid)
        self.assertTrue(rig.validate()['passed']);self.assertEqual(set(rig.state['skeleton']['animations']),{'idle','walk'})
        current=self.agent.read(task['id']);self.assertEqual(current['draft_artifacts'][0]['visual_acceptance']['status'],'draft')
        self.assertEqual(len(current['automatic_build_attempts']),1);self.assertEqual(current['reservations'],{})
        c=self.loaders['character'](cid);self.assertEqual(before,{n:r['image_id'] for n,r in c.meta['part_layers'].items()});self.assertEqual(len(c.meta['versions']),1)
