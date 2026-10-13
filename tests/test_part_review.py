import copy,io,json,tempfile,unittest,uuid,zipfile
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from app.part_review import checked_review,apply_adjustment,component_stats,compose
from app.character import CharacterProject,PART_ORDER
from app.core import UserError,png_bytes
from app.rig import RigProject

ROOT=Path(__file__).resolve().parents[1]
class PartReviewTests(unittest.TestCase):
    def test_diagonal_connectivity_and_minor_details_are_reported_without_erasure(self):
        image=Image.new('RGBA',(32,32));d=ImageDraw.Draw(image);d.rectangle((1,1,8,8),fill='red');d.rectangle((9,9,16,16),fill='blue');d.rectangle((25,1,30,6),fill='green');d.point((0,30),fill='white');before=png_bytes(image)
        report=component_stats(image);self.assertEqual(report['component_count'],2);self.assertEqual(report['components'][0]['area'],128);self.assertEqual(report['minor_pixels'],1);self.assertEqual(before,png_bytes(image))
    def test_identity_exact_translation_and_rotation_about_pivot(self):
        image=Image.new('RGBA',(8,10),(20,70,130,255));p={'name':'head','box':[10,20,18,30],'image':image}
        self.assertIs(apply_adjustment(p,{},[10,20]),p)
        q=apply_adjustment(p,{'dx':12,'dy':-4},[10,20]);self.assertEqual(q['box'],[22,16,30,26]);self.assertEqual(png_bytes(image),png_bytes(q['image']))
        q=apply_adjustment(p,{'rotation':90},[10,20]);self.assertTrue(np.allclose(np.array(q['review_transform']['matrix'])@[10,20]+q['review_transform']['offset'],[10,20]));self.assertEqual(q['image'].size,(10,8))
    def test_schema_rejects_duplicate_order_nan_and_oversized_transforms(self):
        for review in [{'draw_order':['a','a']},{'adjustments':{'c':{}}},{'adjustments':{'a':{'dx':float('nan')}}},{'adjustments':{'a':{'scale':3}}},{'adjustments':{'a':{'dx':1000}}}]:
            with self.assertRaises(UserError):checked_review(review,['a','b'],(128,200),['a','b'])
    def test_composition_respects_order_and_clips_outside_canvas(self):
        a={'name':'a','box':[-2,-2,6,6],'image':Image.new('RGBA',(8,8),'red')};b={'name':'b','box':[0,0,4,4],'image':Image.new('RGBA',(4,4),'blue')}
        self.assertEqual(compose([a,b],(8,8),['a','b']).getpixel((0,0)),(0,0,255,255));self.assertEqual(compose([a,b],(8,8),['b','a']).getpixel((0,0)),(255,0,0,255))
    def test_review_build_restore_export_and_failed_update_are_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);c=CharacterProject.create(storage,'QA assembly');c.set_image((ROOT/'evidence/single-image/source.png').read_bytes(),0)
            before=json.loads((ROOT/'evidence/single-image/complete-parts/before-guide.json').read_text());c.update({'revision':c.meta['revision'],**{k:before[k] for k in ['landmarks','settings','masks']}})
            source=png_bytes(c.image());pixels={p['name']:png_bytes(p['image']) for p in c.parts()};report=c.review();self.assertEqual(len(report['parts']),16)
            def build():return c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,**{k:c.meta[k] for k in ['landmarks','settings','masks']}})['versions'][-1]
            baseline=build();order=list(reversed(PART_ORDER));review={'adjustments':{'l-upper-arm':{'dx':12,'dy':3,'rotation':6,'scale':1.02}},'draw_order':order}
            c.update_review({'revision':c.meta['revision'],'part_review':review});meta=copy.deepcopy(c.meta)
            with self.assertRaises(UserError):c.update_review({'revision':999,'part_review':review})
            with self.assertRaises(UserError):c.update_review({'revision':c.meta['revision'],'part_review':{'draw_order':['back']}})
            self.assertEqual(meta,c.meta);version=build();rig=RigProject(storage/'rigs'/version['rig_id']);self.assertTrue(rig.validate()['passed']);self.assertEqual([s['name'] for s in rig.state['skeleton']['slots']],order)
            self.assertEqual(source,png_bytes(c.image()));self.assertEqual(pixels,{p['name']:png_bytes(p['image']) for p in c.base_parts()})
            self.assertEqual(version['part_review']['adjustments']['l-upper-arm']['dx'],12)
            with zipfile.ZipFile(io.BytesIO(c.export(storage,version['id']))) as z:self.assertEqual(json.loads(z.read('single-image/assembly-review.json'))['draw_order'],order)
            c.restore({'revision':c.meta['revision'],'version':baseline['id']});self.assertEqual(c.meta['part_review']['adjustments'],{})
            c.restore({'revision':c.meta['revision'],'version':version['id']});self.assertEqual(c.meta['part_review'],version['part_review']);self.assertEqual(CharacterProject(c.directory).meta,c.meta)

if __name__=='__main__':unittest.main()
