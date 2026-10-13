import copy
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.core import Project, UserError, png_bytes, sha, decode_files
from app.rig import RigProject, validate_state, weighted_indices
import base64


class RigTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        files={p.name:p.read_bytes() for p in (ROOT/'demo').iterdir() if p.is_file()}
        self.source=Project(self.root/'source',files,'robot')
        self.rig=RigProject.create(self.root/'rig',self.source)

    def tearDown(self):self.temp.cleanup()

    def edit(self,action,**kw):return self.rig.mutate(action,dict(revision=self.rig.meta['revision'],**kw))

    def test_pixels_alpha_resize_pack_restore_and_export(self):
        before=self.source.export();p=self.rig.state['parts'][0];original=p['sha'];first=self.rig.meta['history'][0]['id']
        im=Image.new('RGBA',(170,99),(233,77,51,137));data=base64.b64encode(png_bytes(im)).decode()
        self.edit('texture',part=p['id'],image=data,resize_geometry=True)
        changed=self.rig.part(p['id']);self.assertEqual((changed['width'],changed['height']),(170,99));self.assertEqual(Image.open(io.BytesIO(self.rig.blob(changed))).getpixel((1,1)),(233,77,51,137))
        self.assertTrue(self.rig.validate()['passed']);self.assertTrue(self.rig.validate()['texture_pixels_preserved'])
        files=decode_files([{'name':'rig.zip','data':base64.b64encode(self.rig.export()).decode()}]);imported=Project(self.root/'roundtrip',files,'roundtrip');self.assertEqual(imported.animations,self.source.animations)
        self.edit('restore',snapshot_id=first);self.assertEqual(self.rig.part(p['id'])['sha'],original);self.assertEqual(len(self.rig.meta['history']),3)
        reopened=RigProject(self.root/'rig');self.assertEqual(reopened.state,self.rig.state)
        # ZIP timestamps can differ across the two exports. Check immutable
        # resources, rather than timing-dependent container bytes.
        with zipfile.ZipFile(io.BytesIO(before)) as a,zipfile.ZipFile(io.BytesIO(self.source.export())) as b:
            self.assertEqual({n:a.read(n) for n in a.namelist()},{n:b.read(n) for n in b.namelist()})

    def test_rename_preserves_motion_and_reparent_controller(self):
        self.edit('add-bone',name='controller',parent='root')
        before=copy.deepcopy(self.rig.state['skeleton']['animations'])
        self.edit('bone',bone='hip',name='pelvis-control',parent='controller',x=0,y=165,scaleX=1.1,scaleY=1.15,length=45)
        s=self.rig.state['skeleton'];self.assertLess(next(i for i,b in enumerate(s['bones']) if b['name']=='controller'),next(i for i,b in enumerate(s['bones']) if b['name']=='pelvis-control'))
        for name,anim in before.items():self.assertEqual(s['animations'][name]['bones'].get('pelvis-control'),anim['bones'].get('hip'))
        self.assertTrue(any(sl['bone']=='pelvis-control' for sl in s['slots']));self.assertTrue(self.rig.validate()['passed'])

    def test_invalid_cycle_stale_and_zero_scale_atomic(self):
        state=copy.deepcopy(self.rig.meta)
        for data in [dict(bone='hip',parent='head'),dict(bone='hip',scaleX=0),dict(bone='hip',name='head')]:
            with self.assertRaises(UserError):self.edit('bone',**data)
            self.assertEqual(self.rig.meta,state)
        with self.assertRaises(UserError):self.rig.mutate('save',{'revision':99})
        self.assertEqual(json.loads((self.root/'rig/rig.json').read_text()),state)

    def test_add_delete_attachment_and_leaf(self):
        im=Image.new('RGBA',(55,35),'#82d1bb');image=base64.b64encode(png_bytes(im)).decode()
        self.edit('add-bone',name='hat-control',parent='head',y=75)
        self.edit('add-part',name='hat-new',bone='hat-control',image=image)
        with self.assertRaises(UserError):self.edit('delete-bone',bone='hat-control')
        self.edit('delete-attachment',skin='default',slot='hat-new',attachment='hat-new')
        # Slots are retained to preserve draw-order timelines, and can be rebound.
        self.edit('slot',slot='hat-new',bone='head')
        self.edit('delete-bone',bone='hat-control');self.assertFalse(any(b['name']=='hat-control' for b in self.rig.state['skeleton']['bones']))
        self.assertTrue(self.rig.validate()['passed'])

    def test_actual_weighted_mesh_ik_remap_no_original_mutation(self):
        path=ROOT/'storage/projects/e97b3c3eb3894d498300639a6e272918'
        if not path.exists():self.skipTest('local user fixture unavailable')
        source=Project(path);source_before={n:sha(data) for n,data in source.files.items()};rev=source.revision
        rig=RigProject.create(self.root/'mesh',source);self.rig=rig
        before=copy.deepcopy(rig.state['skeleton']);names=[b['name'] for b in before['bones']]
        mappings=[]
        for _,items in [('default', before['skins'][0]['attachments'])] if isinstance(before['skins'],list) else before['skins'].items():
            for slot,atts in items.items():
                for name,a in atts.items():mappings.append((slot,name,[(pos,names[a['vertices'][pos]]) for pos in weighted_indices(a)]))
        self.edit('add-bone',name='controller',parent='root')
        self.edit('bone',bone='bone',parent='controller',name='character-control')
        s=rig.state['skeleton'];new_names=[b['name'] for b in s['bones']];items=s['skins'][0]['attachments'] if isinstance(s['skins'],list) else s['skins']['default']
        for slot,name,entries in mappings:
            for pos,bone in entries:self.assertEqual(new_names[items[slot][name]['vertices'][pos]],'character-control' if bone=='bone' else bone)
        self.assertEqual(s['ik'],before['ik']);self.assertTrue(rig.validate()['passed']);self.assertEqual(source.revision,rev);self.assertEqual({n:sha(data) for n,data in source.files.items()},source_before)
        with self.assertRaises(UserError):self.edit('bone',bone='bone8',parent='root')
        with self.assertRaises(UserError):self.edit('delete-bone',bone='bone19')
        c=s['ik'][0];self.edit('ik',name=c['name'],bones=c['bones'],target=c['target'],mix=.8,softness=12,bendPositive=False)
        self.assertTrue(rig.validate()['passed']);meta=copy.deepcopy(rig.meta)
        with self.assertRaises(UserError):self.edit('ik',name=c['name'],bones=c['bones'],target='missing',mix=1)
        self.assertEqual(meta,rig.meta)

    def test_new_skeleton_missing_refs_rejected_and_existing_animations_retained(self):
        s=copy.deepcopy(self.rig.state['skeleton']);s['slots'][0]['bone']='missing'
        with self.assertRaises(UserError):self.edit('skeleton',skeleton=s)
        s=copy.deepcopy(self.rig.state['skeleton']);s['skeleton']['spine']='3.8.99'
        with self.assertRaises(UserError):self.edit('skeleton',skeleton=s)
        self.assertEqual(self.rig.state['skeleton']['animations'],self.source.skeleton_data['animations'])

    def test_multi_page_and_blob_integrity(self):
        im=Image.new('RGBA',(1600,1200),(24,97,200,128));image=base64.b64encode(png_bytes(im)).decode()
        for i in range(3):self.edit('add-part',name='large-'+str(i),bone='root',image=image)
        self.assertGreaterEqual(self.rig.validate()['pages'],3);snapshot=self.rig.meta['history'][-1]['id'];p=self.rig.state['parts'][-1]
        (self.rig.directory/'blobs'/(p['sha']+'.png')).write_bytes(b'broken')
        with self.assertRaises(UserError):self.edit('restore',snapshot_id=snapshot)
        with self.assertRaises(UserError):self.rig.export()


if __name__=='__main__':unittest.main()
