import copy,io,json,tempfile,unittest,uuid,zipfile
from pathlib import Path
import numpy as np
from PIL import Image
from app.character import CharacterProject
from app.core import png_bytes,UserError,read_image
from app.part_layers import aligned_part,divider_edges
from app.character import segments,family_weights
from app.rig import RigProject

ROOT=Path(__file__).resolve().parents[1]
class CompletePartTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.storage=Path(self.tmp.name);self.c=CharacterProject.create(self.storage,'完整部件测试')
        before=json.loads((ROOT/'evidence/single-image/complete-parts/before-guide.json').read_text());self.c.set_image((ROOT/'evidence/single-image/source.png').read_bytes(),0)
        self.c.update({'revision':self.c.meta['revision'],'landmarks':before['landmarks'],'masks':before['masks'],'settings':before['settings']})
        self.folder=ROOT/'evidence/single-image/complete-parts'
    def tearDown(self):self.tmp.cleanup()
    def job(self,sheet):
        jobid=json.loads((self.folder/(sheet+'.json')).read_text())['job_id'];job=json.loads((ROOT/'storage/characters/76a6aa6972a4466a976f798b115bac65/jobs'/(jobid+'.json')).read_text());job['source_image_id']=self.c.meta['image_id'];return job
    def apply(self,sheet):self.c.apply_part_sheet({'revision':self.c.meta['revision'],'mode':'redraw'},self.job(sheet),read_image((self.folder/(sheet+'.png')).read_bytes()))
    def test_complete_backpack_no_arm_shaped_holes_and_source_unchanged(self):
        original=png_bytes(self.c.image());self.apply('body');r=self.c.meta['part_layers']['back'];sprite=read_image((self.c.directory/'part-images'/(r['image_id']+'.png')).read_bytes());alpha=np.array(sprite)[:,:,3]
        self.assertTrue(np.all(alpha[sprite.height//3:2*sprite.height//3,sprite.width//3:2*sprite.width//3]>250))
        self.assertEqual(original,png_bytes(self.c.image()));self.assertEqual(len(self.c.meta['part_layers']),4)
    def test_sheet_source_and_revision_errors_are_atomic(self):
        meta=copy.deepcopy(self.c.meta);job=self.job('body');job['source_image_id']='0'*64
        with self.assertRaises(UserError):self.c.apply_part_sheet({'revision':meta['revision']},job,read_image((self.folder/'body.png').read_bytes()))
        with self.assertRaises(UserError):self.c.apply_part_sheet({'revision':999},self.job('body'),read_image((self.folder/'body.png').read_bytes()))
        self.assertEqual(meta,self.c.meta)
    def test_layers_survive_build_export_restore_and_reset(self):
        def build():return self.c.build(self.storage,{'revision':self.c.meta['revision'],'nonce':uuid.uuid4().hex,'landmarks':self.c.meta['landmarks'],'masks':self.c.meta['masks'],'settings':self.c.meta['settings']})
        baseline=build()['versions'][-1]
        for sheet in ('body','arms','legs'):self.apply(sheet)
        version=build()['versions'][-1];self.assertEqual(version['generator'],'complete-parts-v1');self.assertEqual(len(version['part_layers']),16)
        rig=RigProject(self.storage/'rigs'/version['rig_id']);self.assertTrue(rig.validate()['passed']);attachments=rig.state['skeleton']['skins'][0]['attachments']
        self.assertTrue(all(next(iter(a.values()))['type']=='region' for a in attachments.values()))
        with zipfile.ZipFile(io.BytesIO(self.c.export(self.storage,version['id']))) as z:self.assertEqual(len([n for n in z.namelist() if n.startswith('single-image/complete-parts/')]),16)
        self.c.restore({'revision':self.c.meta['revision'],'version':baseline['id']});self.assertEqual(self.c.meta['part_layers'],{})
        self.c.restore({'revision':self.c.meta['revision'],'version':version['id']});self.assertEqual(len(self.c.meta['part_layers']),16)
        self.c.reset_part({'revision':self.c.meta['revision'],'name':'back'});self.assertNotIn('back',self.c.meta['part_layers']);self.assertEqual(len(version['part_layers']),16)
        self.assertEqual(CharacterProject(self.c.directory).meta,self.c.meta)
    def test_foot_keeps_horizontal_sole_with_exact_joint_anchor_mapping(self):
        sprite=Image.new('RGBA',(120,100),(25,100,160,255));record={'start_pin':[.3,.18],'end_pin':[.86,.77]}
        part=aligned_part('l-foot',sprite,record,self.c.meta['landmarks'],self.c.image().size,segments);M=np.array(part['placement']['matrix']);offset=np.array(part['placement']['offset'])
        self.assertAlmostEqual(M[0,1],0);self.assertAlmostEqual(M[1,0],0)
        for pin,joint in [('start_pin','l-ankle'),('end_pin','l-toe')]:self.assertTrue(np.allclose(M@(np.array(record[pin])*sprite.size)+offset,np.array(self.c.meta['landmarks'][joint])*self.c.image().size))

    def test_default_completion_preserves_original_visible_arm_pixels(self):
        before={p['name']:p for p in self.c.parts()};landmarks=copy.deepcopy(self.c.meta['landmarks'])
        self.c.apply_part_sheet({'revision':self.c.meta['revision']},self.job('arms'),read_image((self.folder/'arms.png').read_bytes()))
        for name,r in self.c.meta['part_layers'].items():
            self.assertEqual(r['mode'],'occlusion-fill');part=before[name];a=Image.new('RGBA',self.c.image().size);a.paste(part['image'],part['box'][:2]);b=Image.new('RGBA',a.size);box=np.rint(np.array(r['source_box'])*[a.width,a.height,a.width,a.height]).astype(int);sprite=read_image((self.c.directory/'part-images'/(r['image_id']+'.png')).read_bytes());b.paste(sprite,tuple(box[:2]));A=np.array(a);B=np.array(b)
            self.assertTrue(np.array_equal(A[A[:,:,3]>0],B[A[:,:,3]>0]))
        self.assertEqual(landmarks,self.c.meta['landmarks'])

    def test_source_space_alignment_keeps_every_pixel_and_original_bounds(self):
        pixels=np.arange(19*23*4,dtype=np.uint8).reshape(19,23,4);sprite=Image.fromarray(pixels,'RGBA');W,H=self.c.image().size;box=[300,400,323,419]
        record={'source_box':(np.array(box)/[W,H,W,H]).tolist(),'source_start':self.c.meta['landmarks']['l-shoulder'],'source_end':self.c.meta['landmarks']['l-elbow']}
        part=aligned_part('l-upper-arm',sprite,record,self.c.meta['landmarks'],(W,H),segments)
        self.assertEqual(part['box'],box);self.assertTrue(np.array_equal(np.array(part['image']),pixels));self.assertTrue(part['preserve_mesh'])

    def test_shin_at_boot_collar_follows_only_its_own_rigid_foot(self):
        names=['hip','torso','head']+[side+'-'+kind for side in ('l','r') for kind in ('upper-leg','lower-leg','foot')];indices=list(range(len(names)));W,H=self.c.image().size
        for side in ('l','r'):
            ankle=self.c.meta['landmarks'][side+'-ankle'];weights=family_weights(np.ones(len(names)),indices,names,side+'-leg',ankle[0]*W,ankle[1]*H,self.c.meta['landmarks'],W,H,True)
            expected=np.zeros(len(names));expected[names.index(side+'-foot')]=1
            self.assertTrue(np.allclose(weights,expected))
