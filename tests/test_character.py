import copy,io,json,sys,tempfile,unittest,uuid,zipfile
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.character import CharacterProject,cut_background,guide_cutout,default_landmarks,split_image,make_skeleton,checked_landmarks,skin_family,PART_ORDER,fill_occlusion
from app.core import UserError,png_bytes,Project
from app.rig import RigProject
from app.jobs import JobHistory
from app.core import atomic_json

class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.storage=Path(self.tmp.name);self.c=CharacterProject.create(self.storage,'单图测试')
        self.image=Image.open(ROOT/'evidence/single-image/source.png').convert('RGBA');entry=json.loads((ROOT/'evidence/single-image/example.json').read_text());actual=CharacterProject(ROOT/'storage/characters'/entry['id']);self.landmarks=copy.deepcopy(actual.meta['landmarks']);self.masks=copy.deepcopy(actual.meta['masks'])
        self.c.set_image(png_bytes(self.image),0)
    def tearDown(self):self.tmp.cleanup()

    def test_border_background_preserve_interior_whites_and_transparency(self):
        image=Image.new('RGBA',(100,100),'white');draw=ImageDraw.Draw(image);draw.rectangle((20,20,80,80),fill='#123456');draw.rectangle((35,35,65,65),fill='white');out=cut_background(image)
        self.assertEqual(out.getpixel((0,0))[3],0);self.assertEqual(out.getpixel((50,50)),(255,255,255,255))
        alpha=image.copy();alpha.putpixel((0,0),(0,0,0,0));self.assertEqual(np.array(cut_background(alpha)).tolist(),np.array(alpha).tolist())

    def test_parts_cover_rest_image_and_have_joint_overlap(self):
        cut=guide_cutout(self.image,self.landmarks,self.masks,self.c.meta['settings']);parts=split_image(cut,self.landmarks,self.masks);canvas=Image.new('RGBA',cut.size);area=0
        for p in sorted(parts,key=lambda p:PART_ORDER.index(p['name'])):canvas.alpha_composite(p['image'],p['box'][:2]);area+=p['pixel_count']
        a=np.array(canvas);b=np.array(cut);self.assertTrue(np.array_equal(a,b));self.assertGreater(area,int((b[:,:,3]>0).sum()));self.assertEqual(len(parts),16)

    def test_occluded_outline_cannot_clone_entire_front_foot(self):
        source=np.full((100,100,4),255,dtype=np.uint8);visible=np.zeros((100,100),dtype=bool);visible[20:80,20:40]=True
        hidden=np.zeros_like(visible);hidden[20:80,40:90]=True;target=np.zeros_like(source);target[visible]=source[visible]
        fill_occlusion(target,source,visible,hidden)
        self.assertEqual(target[50,40,3],255)
        self.assertEqual(target[50,80,3],0)

    def test_build_persist_restore_export_and_same_request(self):
        data={'revision':self.c.meta['revision'],'landmarks':self.landmarks,'masks':self.masks,'settings':self.c.meta['settings'],'nonce':uuid.uuid4().hex}
        first=self.c.build(self.storage,data);self.assertEqual(len(first['versions']),1);self.assertEqual(self.c.build(self.storage,data),first)
        version=first['versions'][0];rig=RigProject(self.storage/'rigs'/version['rig_id']);self.assertTrue(rig.validate()['passed']);self.assertEqual(set(rig.state['skeleton']['animations']),{'idle','walk'})
        source=Project(self.storage/'projects'/version['source_id']);self.assertEqual(source.runtime,'4.2')
        self.assertEqual(rig.summary()['assembly_source']['character_revision'],data['revision']);self.assertEqual(rig.summary()['assembly_source']['character_id'],self.c.id)
        material=rig.summary()['material_sources'][0];self.assertEqual(rig.blob(material),png_bytes(self.image))
        from app.rig_automation import clone_rig
        clone=clone_rig(rig,self.storage/'rigs'/uuid.uuid4().hex);self.assertEqual(clone.blob(material),rig.blob(material));self.assertEqual(clone.state['skeleton'],rig.state['skeleton'])
        self.assertEqual(clone.summary()['assembly_source'],rig.summary()['assembly_source'])
        with zipfile.ZipFile(io.BytesIO(self.c.export(self.storage,version['id']))) as z:
            self.assertIn('single-image/source.png',z.namelist());self.assertIn('single-image/guide.json',z.namelist());self.assertIn('skeleton.json',z.namelist());self.assertEqual(len([n for n in z.namelist() if n.startswith('images/') and n.endswith('.png')]),16)
        self.c.update({'revision':self.c.meta['revision'],'landmarks':self.landmarks,'settings':{**self.c.meta['settings'],'period':1.7}})
        self.c.restore({'revision':self.c.meta['revision'],'version':version['id']});self.assertEqual(self.c.meta['settings']['period'],1);self.assertEqual(self.c.meta['masks'],self.masks)
        reopened=CharacterProject(self.c.directory);self.assertEqual(reopened.meta,self.c.meta)
        # Advanced rig edits are not overwritten by another single-image build.
        rig.mutate('bone',{'revision':rig.meta['revision'],'bone':'head','scaleX':1.1})
        self.c.build(self.storage,{**data,'nonce':uuid.uuid4().hex,'revision':self.c.meta['revision']});self.assertEqual(next(b for b in RigProject(rig.directory).state['skeleton']['bones'] if b['name']=='head')['scaleX'],1.1)

    def test_invalid_stale_or_degenerate_is_atomic(self):
        meta=copy.deepcopy(self.c.meta)
        with self.assertRaises(UserError):self.c.update({'revision':99,'landmarks':self.landmarks,'settings':meta['settings']})
        bad=copy.deepcopy(self.landmarks);bad['l-knee']=bad['l-hip']
        with self.assertRaises(UserError):self.c.build(self.storage,{'revision':meta['revision'],'landmarks':bad,'settings':meta['settings'],'nonce':uuid.uuid4().hex})
        self.assertEqual(meta,self.c.meta);self.assertEqual(list((self.storage/'rigs').glob('*')),[])
        with self.assertRaises(UserError):checked_landmarks({**self.landmarks,'hip':[float('nan'),.5]})

    def test_joint_seams_shared_only_within_limb_and_foreign_weights_excluded(self):
        result=self.c.build(self.storage,{'revision':self.c.meta['revision'],'landmarks':self.landmarks,'masks':self.masks,'settings':self.c.meta['settings'],'nonce':uuid.uuid4().hex})
        version=result['versions'][-1];rig=RigProject(self.storage/'rigs'/version['rig_id']);s=rig.state['skeleton'];groups={};shared=0
        for part in version['parts']:
            a=s['skins'][0]['attachments'][part['name']][part['name']];self.assertEqual(a['type'],'mesh');offset=0
            for i in range(len(a['uvs'])//2):
                x=part['box'][0]+a['uvs'][i*2]*(part['box'][2]-part['box'][0]);y=part['box'][1]+a['uvs'][i*2+1]*(part['box'][3]-part['box'][1]);family=skin_family(part['name']);key=(family,round(x,5),round(y,5));n=a['vertices'][offset];value=a['vertices'][offset:offset+1+n*4];offset+=1+n*4
                self.assertAlmostEqual(sum(value[4::4]),1,places=6)
                for j in range(n):
                    bone=s['bones'][value[1+j*4]]['name'];self.assertTrue(skin_family(bone)==family or bone==('torso' if family.endswith('arm') else 'hip'),(part['name'],bone))
                if key in groups:self.assertEqual(groups[key],value);shared+=1
                else:groups[key]=value
        self.assertGreater(shared,100);self.assertEqual(version['generator'],'chain-mesh-v2')

    def test_enclosed_leg_background_removed_without_erasing_boot_soles(self):
        cut=guide_cutout(self.image,self.landmarks,self.masks,self.c.meta['settings'])
        for point in [(460,850),(450,1000)]:self.assertEqual(cut.getpixel(point)[3],0)
        for point in [(500,1097),(420,1104)]:self.assertEqual(cut.getpixel(point),self.image.getpixel(point))
        disabled=guide_cutout(self.image,self.landmarks,self.masks,{**self.c.meta['settings'],'clear_gaps':False});self.assertEqual(disabled.getpixel((460,850))[3],255)

    def test_character_job_resume_download_once_no_resubmit(self):
        image=png_bytes(self.image)
        class Provider:
            count=0
            def poll(self,id,model=None):
                self.count+=1;return {'data':{'status':'completed','outputs':[{'data':{'image_url':'https://example.test/character.png'}}]}}
            def download_result(self,url):return image
            def generate(self,*args):raise AssertionError('must never resubmit')
        provider=Provider();worker=JobHistory(self.storage,provider,'characters');job_id=uuid.uuid4().hex;path=worker.path(self.c.id,job_id)
        atomic_json(path,{'id':job_id,'gen_id':'existing','status':'pending','mode':'character'})
        first=worker.refresh(path);self.assertTrue(first['candidate_ready']);self.assertEqual(path.with_suffix('.png').read_bytes(),image)
        restarted=JobHistory(self.storage,provider,'characters');self.assertEqual(restarted.refresh(path),first);self.assertEqual(provider.count,1)

if __name__=='__main__':unittest.main()
