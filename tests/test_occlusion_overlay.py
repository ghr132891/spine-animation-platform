import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
import numpy as np
from PIL import Image
from app.core import Project,UserError,png_bytes,read_image
from app.rig import RigProject,skins
from app.rig_automation import RigAutomation
from app.assembly_source import snapshot
ROOT=Path(__file__).resolve().parents[1]

class OcclusionOverlay(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        source=Project(self.root/'project',{p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()},'fixture')
        self.rig=RigProject.create(self.root/'rigs'/'source',source)
        slots=self.rig.state['skeleton']['slots'];self.source=slots[0]['name'];self.subject=slots[1]['name']
        _,items,_=next(iter(skins(self.rig.state['skeleton'])))
        attname,att=next(iter(items[self.source].items()))
        self.part=next(p for p in self.rig.state['parts'] if p['name']==att.get('path',attname))
        self.op={'action':'occlusion-overlay','part':self.part['id'],'source_slot':self.source,'subject_slot':self.subject,'name':'foreground','polygon':[[0,0],[1,0],[1,.6],[0,.6]],'reason':'Observed foreground boundary'}
        self.auto=RigAutomation(self.root,lambda _:self.rig)
    def tearDown(self):self.tmp.cleanup()
    def test_atomic_reusable_operation_preserves_geometry_and_motion(self):
        before=copy.deepcopy(self.rig.meta)
        request={'nonce':uuid.uuid4().hex,'source_rig_id':self.rig.id,'revision':0,'operations':[self.op]}
        result=self.auto.create(request);self.assertEqual(result,self.auto.create(request))
        self.assertEqual(before,self.rig.meta)
        output=RigProject(self.root/'rigs'/result['rig_id']);s=output.state['skeleton']
        self.assertEqual(s['animations'],before['state']['skeleton']['animations'])
        self.assertEqual(s['bones'],before['state']['skeleton']['bones'])
        _,items,_=next(iter(skins(s)));old=next(iter(items[self.source].values()));new=items['foreground']['foreground']
        self.assertEqual({**old,'path':'foreground'},new)
        order=[x['name'] for x in s['slots']];i=order.index(self.subject)
        self.assertEqual(order[i-1:i+2],[self.source,self.subject,'foreground'])
        a=np.array(read_image(self.rig.blob(self.part)).convert('RGBA'));b=np.array(read_image(output.blob(output.part('foreground'))).convert('RGBA'))
        np.testing.assert_array_equal(a[:,:,:3],b[:,:,:3]);self.assertTrue(np.all(b[:,:,3]<=a[:,:,3]))
        self.assertEqual(output.summary()['derived_from_rig'],{'id':self.rig.id,'revision':0})
        self.assertEqual(s['skeleton']['occlusion_overlays'][0]['visual_verdict'],'not_reviewed')
        with self.assertRaises(UserError):self.auto.create({**request,'operations':[{**self.op,'reason':'different'}]})
    def test_invalid_or_animated_sources_leave_original_unchanged(self):
        for patch in ({'polygon':None},{'subject_slot':'missing'},{'part':'missing'},{'name':'../bad'},{'reason':''}):
            before=copy.deepcopy(self.rig.meta)
            with self.assertRaises(UserError):self.rig.mutate('occlusion-overlay',dict(self.op,revision=0,**patch))
            self.assertEqual(self.rig.meta,before)
        for timeline in ({'drawOrder':[{'time':0}]},{'slots':{self.source:{'color':[{'time':0,'color':'ffffffff'}]}}},{'deform':{'default':{self.source:{'x':[]}}}}):
            self.rig.state['skeleton']['animations']={'test':timeline}
            before=copy.deepcopy(self.rig.meta)
            with self.assertRaises(UserError):self.rig.mutate('occlusion-overlay',{**self.op,'revision':0})
            self.assertEqual(self.rig.meta,before)

class AssemblyProvenance(unittest.TestCase):
    def test_hash_tracks_pixels_and_configuration(self):
        meta={'revision':4,'image_id':'original','part_layers':{}}
        parts=[{'name':'head','image':Image.new('RGBA',(2,2),'red')}]
        a=snapshot('character',meta,{}, {}, {}, {},parts)
        self.assertEqual(a,snapshot('character',meta,{}, {}, {}, {},parts))
        self.assertEqual(a['character_revision'],4)
        for b in (snapshot('character',meta,{'head':[2,3]}, {}, {}, {},parts),snapshot('character',meta,{}, {}, {}, {},[{'name':'head','image':Image.new('RGBA',(2,2),'blue')}] )):
            self.assertNotEqual(a['snapshot_sha256'],b['snapshot_sha256'])
