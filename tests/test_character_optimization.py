import copy,io,json,tempfile,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import Mock
from app.character import CharacterProject,DRAW_ORDER,PART_ORDER
from app.core import UserError,png_bytes
from app.rig import RigProject
from app.provider import Matrix
from app.part_prompts import generation_plan,part_prompt

ROOT=Path(__file__).resolve().parents[1]

class CharacterOptimizationTests(unittest.TestCase):
    def test_layer_variant_preserves_accepted_art_motion_and_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);c=CharacterProject.create(storage,'遮挡测试')
            c.set_image((ROOT/'evidence/single-image/source.png').read_bytes(),0)
            guide=json.loads((ROOT/'evidence/single-image/complete-parts/before-guide.json').read_text())
            v=c.build(storage,{'revision':c.meta['revision'],'nonce':uuid.uuid4().hex,**{k:guide[k] for k in ('landmarks','masks','settings')}})['versions'][-1]
            old=RigProject(storage/'rigs'/v['rig_id'])
            old.mutate('bone',{'revision':old.meta['revision'],'bone':'head','scaleX':1.03})
            before=copy.deepcopy(old.state);meta=copy.deepcopy(c.meta)
            self.assertEqual([s['name'] for s in before['skeleton']['slots']],DRAW_ORDER)
            self.assertEqual(c.review_order(DRAW_ORDER),DRAW_ORDER)
            self.assertLess(PART_ORDER.index('l-lower-leg'),PART_ORDER.index('l-foot'))
            req={'revision':c.meta['revision'],'version':v['id'],'nonce':uuid.uuid4().hex,'policy':'behind'}
            result=c.foot_layers(storage,req);new=result['versions'][-1];after=RigProject(storage/'rigs'/new['rig_id'])
            a=copy.deepcopy(before['skeleton']);b=copy.deepcopy(after.state['skeleton'])
            oldslots=a.pop('slots');newslots=b.pop('slots');self.assertEqual(a,b)
            self.assertEqual({s['name']:s for s in oldslots},{s['name']:s for s in newslots})
            order=[s['name'] for s in newslots]
            for side in ('l','r'):self.assertEqual(order.index(side+'-foot')+1,order.index(side+'-lower-leg'))
            self.assertEqual({p['name']:old.blob(p) for p in before['parts']},{p['name']:after.blob(p) for p in after.state['parts']})
            self.assertEqual(before,RigProject(old.directory).state)
            for key in ('image_id','landmarks','masks','settings','part_layers','part_review'):self.assertEqual(c.meta.get(key),meta.get(key))
            self.assertEqual(meta['versions'],c.meta['versions'][:-1]);self.assertEqual(c.foot_layers(storage,req),result)
            with self.assertRaises(UserError):c.foot_layers(storage,{**req,'version':'missing'})
            with self.assertRaises(UserError):c.foot_layers(storage,{**req,'nonce':uuid.uuid4().hex,'revision':-1})
            self.assertEqual(CharacterProject(c.directory).meta,c.meta)
            with zipfile.ZipFile(io.BytesIO(c.export(storage,new['id']))) as z:
                self.assertEqual([s['name'] for s in json.loads(z.read('skeleton.json'))['slots']],order)
                self.assertEqual(json.loads(z.read('single-image/assembly-review.json'))['draw_order'],order)
            # Animated draw-order offsets are slot-index-sensitive and cannot be silently changed.
            changed=copy.deepcopy(old.state);changed['skeleton']['animations']['walk']['drawOrder']=[{'time':0,'offsets':[]}]
            old.commit(changed,'动态层级');latest=copy.deepcopy(c.meta)
            with self.assertRaises(UserError):c.foot_layers(storage,{**req,'revision':c.meta['revision'],'nonce':uuid.uuid4().hex})
            self.assertEqual(latest,c.meta)

    def test_preview_matches_matrix_submission_without_network_or_billing(self):
        with tempfile.TemporaryDirectory() as tmp:
            matrix=Matrix(tmp);matrix.request=Mock(return_value={'data':{'gen_id':'qa'}})
            description='穿蓝白衣服的厨师，保留肤色'
            plan=generation_plan({'mode':'character','prompt':description})
            matrix.generate_character(description)
            self.assertEqual(matrix.request.call_args.args[1]['input']['prompt'],plan['items'][0]['prompt'])
            instructions='保留鞋带与背包通风口'
            plan=generation_plan({'mode':'complete-parts','instructions':instructions},'left')
            for item in plan['items']:
                matrix.generate_part_sheet(item['sheet'],'https://example.com/reference.png',instructions,'left')
                submitted=matrix.request.call_args.args[1]
                self.assertEqual(submitted['input']['prompt'],item['prompt'])
                self.assertEqual(submitted['input']['input_images'],['https://example.com/reference.png'])
                self.assertEqual(submitted['input']['aspect_ratio'],item['ratio'])
                self.assertEqual(submitted['request_num'],1)
                self.assertIn(instructions,item['prompt']);self.assertNotIn('orange',item['prompt']);self.assertNotIn('teal',item['prompt'])
            legs=part_prompt('legs',facing='left')
            self.assertIn('RIGHT COLUMN',legs);self.assertIn('pointing LEFT',legs)
            with self.assertRaises(UserError):part_prompt('arms','a'*1201)
            with self.assertRaises(UserError):generation_plan({'mode':'character','prompt':'短'})
            with self.assertRaises(UserError):generation_plan({'mode':'unknown'})

if __name__=='__main__':unittest.main()
