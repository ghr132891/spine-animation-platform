import copy, json, sys, unittest, uuid
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.seam_review import repair_texture,white_candidates,render_closeups
from app.core import UserError,sha,png_bytes


class PixelSeamTests(unittest.TestCase):
    def test_scoped_white_cleanup_preserves_white_detail_skin_and_rgb(self):
        image=Image.new('RGBA',(100,100),(100,70,55,255));d=ImageDraw.Draw(image)
        d.rectangle((10,10,19,19),fill='white');d.rectangle((10,60,19,69),fill='white');d.rectangle((60,60,69,69),fill=(241,200,168,255))
        polygon=[[.09,.59],[.20,.59],[.20,.70],[.09,.70]]
        fixed,report=repair_texture(image,{'reason':'实看确认颈部白底，保留眼白','white_regions':[polygon]},None)
        a=np.array(image);b=np.array(fixed);self.assertEqual(fixed.getpixel((15,15)),image.getpixel((15,15)));self.assertEqual(fixed.getpixel((65,65)),image.getpixel((65,65)));self.assertEqual(fixed.getpixel((15,65)),(0,0,0,0));self.assertEqual(report['removed_white_pixels'],100)
        self.assertTrue(np.array_equal(a[:50],b[:50]))

    def test_copy_reuses_exact_pixels_and_rejects_large_repair(self):
        source=Image.new('RGBA',(20,20),(220,177,133,255));target=Image.new('RGBA',(100,100))
        resolve=lambda item:(source,sha(png_bytes(source)))
        result,report=repair_texture(target,{'reason':'复用实际颈部','copies':[{'source_part':'ref','box':[0,0,20,20],'dest':[30,30,50,50]}]},resolve)
        self.assertEqual(result.getpixel((40,40)),source.getpixel((5,5)));self.assertEqual(report['changed_pixels'],400);self.assertEqual(len(report['sources']),1)
        with self.assertRaises(UserError):repair_texture(target,{'reason':'过大','copies':[{'source_part':'ref','box':[0,0,20,20],'dest':[0,0,80,80]}]},resolve)
        with self.assertRaises(UserError):repair_texture(target,{'reason':'越界','copies':[{'source_part':'ref','box':[0,0,25,20],'dest':[0,0,20,20]}]},resolve)

    def test_suspected_white_does_not_certify_art(self):
        image=Image.new('RGB',(80,80),'#e5ebf3');ImageDraw.Draw(image).rectangle((20,30,29,45),fill='white')
        self.assertEqual(white_candidates(image,[0,0,80,80])[0]['pixels'],160)
        self.assertEqual(white_candidates(image,[0,0,10,10]),[])
        with self.assertRaises(UserError):render_closeups([{'bones':[]}],ROOT,'head',None)


class SeamWorkflowTests(unittest.TestCase):
    def setUp(self):
        from tests.test_agent_tasks import AgentTests
        self.api=AgentTests.api.__get__(self);self.request=AgentTests.request.__get__(self)
        AgentTests.setUp(self)
    def tearDown(self):self.tmp.cleanup()
    def test_stale_closeups_and_wrong_owner_cannot_be_attested(self):
        task=self.tasks.create(self.request());entity=task['sources'][0]['id'];id=task['id'];args={'kind':'rig','id':entity,'animation':'walk','verdict':'ready','reason':'已查看实图'}
        with self.assertRaises(UserError):self.tasks.tool(id,'spine_review_closeup',args)
        with self.assertRaises(UserError):self.tasks.tool(id,'spine_closeup',{'kind':'rig','id':self.rig.id,'animation':'walk'})
        preview=self.tasks.tool(id,'spine_closeup',{'kind':'rig','id':entity,'animation':'walk'});report=json.loads(preview['content'][0]['text']);self.assertEqual(report['frames'],8)
        self.tasks.tool(id,'spine_review_closeup',args)
        rig=self.loaders['rig'](entity);rig.mutate('bone',{'bone':'head','scaleX':1.02,'revision':rig.meta['revision']})
        with self.assertRaises(UserError):self.tasks.tool(id,'spine_review_closeup',args)

    def test_material_finish_is_transactional_and_requires_current_review(self):
        task=self.tasks.create(self.request());entity=task['sources'][0]['id'];rig=self.loaders['rig'](entity)
        skeleton=copy.deepcopy(rig.state['skeleton']);part=rig.state['parts'][0];original=rig.blob(part)
        request={'source_rig_id':entity,'revision':rig.meta['revision'],'nonce':uuid.uuid4().hex,'operations':[{'action':'material-finish','part':part['id'],'reason':'实看半透明裙料','operations':[{'kind':'opacity','polygon':[[0,0],[1,0],[1,1],[0,1]],'top':.5,'bottom':1,'start':1,'end':.5,'reference_note':'已查看原图，仅测试指定部件'}]}]}
        with self.assertRaises(UserError):self.tasks.tool(task['id'],'spine_edit_rig',request)
        self.tasks.tool(task['id'],'spine_closeup',{'kind':'rig','id':entity,'animation':'walk'})
        result=self.tasks.tool(task['id'],'spine_edit_rig',request);fixed=self.loaders['rig'](result['rig_id'])
        self.assertEqual(skeleton,fixed.state['skeleton']);self.assertEqual(original,rig.blob(part));self.assertEqual(len(fixed.state['material_repairs']),1)
        self.assertEqual(fixed.state['parts'][0]['width'],part['width']);self.assertTrue(fixed.validate()['passed'])

    def test_repair_is_a_scoped_copy_and_never_changes_bones_or_animations(self):
        task=self.tasks.create(self.request());entity=task['sources'][0]['id'];rig=self.loaders['rig'](entity);before=copy.deepcopy(rig.state['skeleton']);part=rig.state['parts'][0]
        request={'source_rig_id':entity,'revision':rig.meta['revision'],'nonce':uuid.uuid4().hex,'operations':[{'action':'texture-repair','part':part['id'],'reason':'只清实看白底','white_regions':[[[0,0],[.05,0],[.05,.05],[0,.05]]]}]}
        with self.assertRaises(UserError):self.tasks.tool(task['id'],'spine_edit_rig',request)
        self.tasks.tool(task['id'],'spine_closeup',{'kind':'rig','id':entity,'animation':'walk'})
        result=self.tasks.tool(task['id'],'spine_edit_rig',request);fixed=self.loaders['rig'](result['rig_id']);self.assertEqual(before,fixed.state['skeleton']);self.assertEqual(len(fixed.state['texture_repairs']),1);self.assertTrue(fixed.validate()['passed'])


if __name__=='__main__':unittest.main()

class DiagnosticIsolationTests(unittest.TestCase):
    def test_isolated_render_filters_geometry_without_mutating_sample(self):
        bones=[{'name':name,'x':0,'y':y,'ex':0,'ey':y-10} for name,y in [('l-upper-leg',30),('l-lower-leg',20),('l-foot',10)]]
        frame={'bones':bones,'geometry':[{'slot':name} for name in ('torso','l-upper-leg','l-lower-leg','l-foot','r-upper-leg')]};before=copy.deepcopy(frame);seen=[]
        def render(pose,*args,**kwargs):
            seen.append({g['slot'] for g in pose['geometry']});return Image.new('RGBA',(400,400),'#e5ebf3')
        _,report=render_closeups([frame]*8,ROOT,'l-upper-leg',render,isolated=True)
        self.assertTrue(report['isolated']);self.assertEqual(report['frames'],8)
        self.assertEqual(seen,[{'l-upper-leg','l-lower-leg','l-foot'}]*12);self.assertEqual(frame,before)

    def test_isolated_worker_view_does_not_replace_composite_review(self):
        from unittest.mock import patch
        from tests.test_agent_tasks import AgentTests
        f=AgentTests();f.setUp();self.addCleanup(f.tearDown)
        task=f.tasks.create(f.request());entity=task['sources'][0]['id'];key='rig:'+entity
        t=f.tasks.read(task['id']);previous={'sha256':'before','regions':{'walk':{'head':{'review':{'verdict':'ready'}}}}};t['closeup_checks']={key:copy.deepcopy(previous)};f.tasks.save_tool_state(t)
        report={'isolated':True,'anchor':'head','animation':'walk','resource_sha256':'different','frames':8}
        with patch.object(f.tasks,'closeup_target',return_value=(report,b'png')):
            result=f.tasks.tool(task['id'],'spine_closeup',{'kind':'rig','id':entity,'animation':'walk','isolated':True})
        self.assertTrue(json.loads(result['content'][0]['text'])['isolated'])
        self.assertEqual(f.tasks.read(task['id'])['closeup_checks'][key],previous)
