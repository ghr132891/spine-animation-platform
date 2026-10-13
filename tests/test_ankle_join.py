import copy,io,json,tempfile,unittest,uuid,zipfile
from pathlib import Path
import numpy as np
from PIL import Image
from app.character import CharacterProject
from app.rig import RigProject
from app.core import atomic_json
from app.ankle_join import cuff_image
from app.part_prompts import generation_plan

ROOT=Path(__file__).resolve().parents[1]
class AnkleJoinTests(unittest.TestCase):
    def character(self,storage):
        original=CharacterProject(ROOT/'storage/characters/76a6aa6972a4466a976f798b115bac65')
        version=next(v for v in original.meta['versions'] if v['id']=='162dd522f39c43eea6ef095de6c35de8')
        c=CharacterProject.create(storage,'腿鞋接合测试')
        c.set_image((original.directory/'images'/(version['image_id']+'.png')).read_bytes(),0)
        meta=copy.deepcopy(c.meta)
        for k in ('landmarks','masks','settings','part_layers'):meta[k]=copy.deepcopy(version[k])
        folder=c.directory/'part-images';folder.mkdir()
        for record in meta['part_layers'].values():
            name=record['image_id']+'.png';(folder/name).write_bytes((original.directory/'part-images'/name).read_bytes())
        c.save(meta)
        v=c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,**{k:c.meta[k] for k in ('landmarks','masks','settings')}})['versions'][-1]
        return c,v

    def test_cuff_extracts_original_pixels_and_keeps_opening_on_rear_layer(self):
        im=Image.new('RGBA',(200,200),(15,50,80,255));out=np.array(cuff_image(im));original=np.array(im)
        self.assertTrue(np.array_equal(out[out[:,:,3]>0],original[out[:,:,3]>0]))
        self.assertEqual(out[5,50,3],0);self.assertEqual(out[40,50,3],255);self.assertEqual(out[150,150,3],0)

    def test_cuff_variant_and_restored_assembly_export_keep_bones_and_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);c,v=self.character(storage);old=RigProject(storage/'rigs'/v['rig_id']);before=copy.deepcopy(old.state);draft=copy.deepcopy(c.meta)
            request={'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'version':v['id'],'policy':'cuff'}
            result=c.foot_layers(storage,request);new=result['versions'][-1];rig=RigProject(storage/'rigs'/new['rig_id'])
            self.assertEqual(new['render_parts'],18);self.assertTrue(new['part_review']['shoe_cuffs'])
            self.assertEqual(rig.state['skeleton']['bones'],before['skeleton']['bones']);self.assertEqual(rig.state['skeleton']['animations'],before['skeleton']['animations'])
            order=[s['name'] for s in rig.state['skeleton']['slots']]
            for side in ('l','r'):
                self.assertLess(order.index(side+'-foot'),order.index(side+'-lower-leg'))
                self.assertEqual(order.index(side+'-lower-leg')+1,order.index(side+'-foot-cuff'))
            for p in before['parts']:
                q=next(q for q in rig.state['parts'] if q['name']==p['name']);self.assertEqual(old.blob(p),rig.blob(q))
            for key in ('image_id','landmarks','masks','settings','part_layers','part_review'):self.assertEqual(c.meta.get(key),draft.get(key))
            self.assertEqual(c.foot_layers(storage,request),result)
            c.restore({'revision':c.meta['revision'],'version':new['id']});self.assertTrue(c.review()['shoe_cuffs']);self.assertTrue(c.assembly().getbbox())
            # Guide saves must not silently turn the fix off, and manual foot offsets are preserved.
            review=copy.deepcopy(c.meta['part_review']);review['adjustments']['l-foot']={'dx':-4,'dy':2,'rotation':4,'scale':1}
            c.update_review({'revision':c.meta['revision'],'part_review':review})
            c.update({'revision':c.meta['revision'],**{k:c.meta[k] for k in ('landmarks','masks','settings')}})
            self.assertTrue(c.meta['part_review']['shoe_cuffs'])
            built=c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,**{k:c.meta[k] for k in ('landmarks','masks','settings')}})['versions'][-1]
            self.assertEqual(built['render_parts'],18);self.assertTrue(RigProject(storage/'rigs'/built['rig_id']).validate()['passed'])
            with zipfile.ZipFile(io.BytesIO(c.export(storage,built['id']))) as z:
                self.assertIn('images/l-foot-cuff.png',z.namelist());self.assertIn('images/r-foot-cuff.png',z.namelist());self.assertTrue(json.loads(z.read('single-image/assembly-review.json'))['shoe_cuffs'])
            # Switching back to plain ordering removes the helpers instead of duplicating them.
            reverted=c.foot_layers(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,'version':built['id'],'policy':'front'})['versions'][-1]
            self.assertEqual(reverted['render_parts'],16);self.assertNotIn('shoe_cuffs',reverted['part_review'])

    def test_generation_prompts_keep_continuous_trouser_shoe_join_in_both_languages(self):
        character=generation_plan({'prompt':'可爱的快递机器人'})['items'][0]['prompt']
        parts=generation_plan({'mode':'complete-parts'})
        legs=next(p['prompt'] for p in parts['items'] if p['sheet']=='legs')
        self.assertIn('裤装插接部分进入原鞋口',character);self.assertIn('鞋口前沿可读',character)
        self.assertIn('trouser insert seated inside its shoe',legs);self.assertIn('front cuff rim',legs)
        english=generation_plan({'prompt':'a cute courier robot'})['items'][0]['prompt']
        self.assertIn('trouser inserts inside the original shoe collars',english)
        self.assertNotIn('No exposed round ankle discs',english)

if __name__=='__main__':unittest.main()
