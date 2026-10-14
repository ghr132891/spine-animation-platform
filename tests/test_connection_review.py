import copy
import math
import unittest
import numpy as np
from PIL import Image
from app.core import UserError
from app.connection_review import CHECKS, anchors, checked, require
from app.reviewed_parts import chain_mesh
from app.seam_review import detail_view

class Connections(unittest.TestCase):
    def test_failed_and_stale_connections_cannot_pass(self):
        sk={'bones':[{'name':n} for n in ('head','hip','torso','l-upper-leg')], 'slots':[{'name':'back'}]}
        self.assertEqual(anchors(sk),['head','__hip__','__back__'])
        regions={clip:{anchor:{'report':{'frames':32,'isolated':False},'review':{'verdict':'ready','connection_checks':dict.fromkeys(CHECKS[anchor],True)}} for anchor in anchors(sk)} for clip in ('idle','walk')}
        good={'sha256':'current','regions':regions}; require(good,'current',['idle','walk'],sk)
        for anchor in anchors(sk):
            bad=copy.deepcopy(good);bad['regions']['walk'][anchor]['review']['connection_checks'][next(iter(CHECKS[anchor]))]=False
            with self.assertRaises(UserError):require(bad,'current',['idle','walk'],sk)
        for field,val in [('frames',8),('isolated',True)]:
            bad=copy.deepcopy(good);bad['regions']['walk']['__hip__']['report'][field]=val
            with self.assertRaises(UserError):require(bad,'current',['walk'],sk)
        with self.assertRaises(UserError):require(good,'changed',['idle'],sk)
        with self.assertRaises(UserError):checked({'verdict':'ready','connection_checks':{'neck_pin_aligned':1,'collar_occlusion':True}},'head')

    def test_hip_zoom_tracks_runtime_root_not_setup(self):
        def frame(x):return {'bones':[{'name':'hip','x':x,'y':200},{'name':'l-upper-leg','x':x,'y':200,'ex':x,'ey':100}]}
        a=detail_view(frame(0),'__hip__'); b=detail_view(frame(25),'__hip__')
        np.testing.assert_allclose(np.array(b)-a,[25,0,25,0])

    def test_leg_root_stays_on_pelvis_while_thigh_rotates(self):
        names=['hip','l-upper-leg','l-lower-leg','l-foot'];bones=[{'name':n} for n in names]
        poses={n:(np.array([0.,0.]),0.) for n in names}
        part={'name':'l-upper-leg','chain':'leg','box':[0,0,20,120],'image':Image.new('RGBA',(20,120)),
              'chain_points':{'l-hip':[10,10],'l-knee':[10,60],'l-ankle':[10,100],'l-toe':[20,115]}}
        mesh=chain_mesh(part,bones,poses,1,[0,0],120);v=mesh['vertices'];j=0
        for u,w in np.array(mesh['uvs']).reshape(-1,2):
            n=v[j];j+=1;influences=[v[j+4*k:j+4*k+4] for k in range(n)];j+=4*n
            self.assertAlmostEqual(sum(p[3] for p in influences),1)
            setup=sum(np.array(p[1:3])*p[3] for p in influences)
            np.testing.assert_allclose(setup,[u*20,120-w*120],atol=1e-8)
            if w==0:
                self.assertEqual(len(influences),1);self.assertEqual(influences[0][0],0)
            if .3<w<.4:self.assertNotIn(0,[p[0] for p in influences])

if __name__=='__main__':unittest.main()

class WorkflowConnectionChecks(unittest.TestCase):
    def test_task_review_requires_current_connection_fields(self):
        from app.agent_tasks import AgentTasks
        task={'connection_contract':'assembly-connections-v1','closeup_checks':{'rig:r':{'sha256':'current','regions':{'idle':{'head':{'report':{'frames':8}}}}}}}
        args={'kind':'rig','id':'r','animation':'idle','anchor':'head','verdict':'ready','reason':'visible collar opening'}
        agent=object.__new__(AgentTasks)
        with self.assertRaises(UserError):agent.record_closeup_review(task,args,'current',{})
        args['connection_checks']={'neck_pin_aligned':True,'collar_occlusion':True}
        item=agent.record_closeup_review(task,args,'current',{})
        self.assertEqual(item['review']['connection_checks'],args['connection_checks'])
        legacy=copy.deepcopy(task);legacy.pop('connection_contract');args.pop('connection_checks')
        agent.record_closeup_review(legacy,args,'current',{})

class IntegrityV2(unittest.TestCase):
    def test_incomplete_back_and_repeated_denim_cannot_pass(self):
        from app.connection_review import LEGACY_CHECKS
        for anchor,field in [('__back__','occluded_artwork_complete'),('__back__','complete_silhouette'),('__hip__','texture_continuity'),('__hip__','upper_edge_covered')]:
            values=dict.fromkeys(CHECKS[anchor],True);values[field]=False
            with self.assertRaises(UserError):checked({'verdict':'ready','connection_checks':values},anchor)
            checked({'verdict':'needs_repair','connection_checks':values},anchor)
        for anchor in ('__back__','__hip__'):
            old={'verdict':'ready','connection_checks':dict.fromkeys(LEGACY_CHECKS[anchor],True)}
            checked(old,anchor,'assembly-connections-v1')
            with self.assertRaises(UserError):checked(old,anchor)
    def test_back_walk_requires_full_cycle_samples(self):
        sk={'bones':[{'name':'torso'}],'slots':[{'name':'back'}]}
        item={'report':{'frames':8},'review':{'verdict':'ready','connection_checks':dict.fromkeys(CHECKS['__back__'],True)}}
        review={'sha256':'x','regions':{'walk':{'__back__':item}}}
        with self.assertRaises(UserError):require(review,'x',['walk'],sk)
        item['report']['frames']=32;require(review,'x',['walk'],sk)

class ConnectionsV3(unittest.TestCase):
    def test_front_back_and_runtime_are_independent_and_legacy_still_valid(self):
        from app.connection_review import V2_CHECKS
        old={'verdict':'ready','connection_checks':dict.fromkeys(V2_CHECKS['head'],True)}
        checked(old,'head','assembly-connections-v2')
        with self.assertRaises(UserError):checked(old,'head')
        for field in ('rear_collar_behind_neck','front_collar_in_front','runtime_pose_consistent'):
            values=dict.fromkeys(CHECKS['head'],True);values[field]=False
            with self.assertRaises(UserError):checked({'verdict':'ready','connection_checks':values},'head')
        item={'report':{'frames':8},'review':{'verdict':'ready','connection_checks':dict.fromkeys(CHECKS['head'],True)}}
        review={'sha256':'x','regions':{'walk':{'head':item}}}
        with self.assertRaises(UserError):require(review,'x',['walk'],{'bones':[{'name':'head'}]})
        item['report']['frames']=32;require(review,'x',['walk'],{'bones':[{'name':'head'}]})

class IntegratedBackpack(unittest.TestCase):
    def test_integrated_backpack_is_scheduled_and_rendered(self):
        sk={'bones':[{'name':'torso'}],'slots':[{'name':'torso'}]}
        self.assertNotIn('__back__',anchors(sk))
        sk['skeleton']={'assembly_features':{'back_accessory_part':'torso'}}
        self.assertIn('__back__',anchors(sk))
        f={'geometry':[{'slot':'torso','xy':[10,20,30,20,30,60,10,60]}]}
        self.assertEqual(detail_view(f,'__back__'),[0,10,40,70])
        item={'report':{'frames':8},'review':{'verdict':'ready','connection_checks':dict.fromkeys(CHECKS['__back__'],True)}}
        with self.assertRaises(UserError):require({'sha256':'x','regions':{'walk':{'__back__':item}}},'x',['walk'],sk)
        item['report']['frames']=32;require({'sha256':'x','regions':{'walk':{'__back__':item}}},'x',['walk'],sk)
