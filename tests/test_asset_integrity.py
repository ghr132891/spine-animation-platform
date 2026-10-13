import copy
import json
import unittest
import uuid
from pathlib import Path

from app.asset_integrity import ASSEMBLY_CHECKS, CONTRACT, checked_material_review, require_material_reviews, required_checks
from app.core import UserError, atomic_json, png_bytes
from app.part_prompts import generation_plan


class MaterialGateTests(unittest.TestCase):
    def test_ready_requires_all_observations_and_failures_can_be_recorded(self):
        for anchor in (None,'head','torso','l-upper-arm','r-upper-leg','__hair__'):
            good=dict.fromkeys(required_checks(anchor),True)
            self.assertEqual(checked_material_review({'verdict':'ready','material_checks':good},anchor),good)
            for field in good:
                bad={**good,field:False}
                with self.assertRaises(UserError):checked_material_review({'verdict':'ready','material_checks':bad},anchor)
                self.assertEqual(checked_material_review({'verdict':'needs_repair','material_checks':bad},anchor),bad)
        with self.assertRaises(UserError):checked_material_review({'verdict':'ready'})
        with self.assertRaises(UserError):checked_material_review({'verdict':'ready','material_checks':dict.fromkeys(ASSEMBLY_CHECKS,1)})

    def test_numeric_success_and_blanket_ready_cannot_pass_visual_delivery(self):
        record={'sha256':'current','numerical_checks_passed':True,'regions':{'walk':{'torso':{'review':{'verdict':'ready'}}}}}
        with self.assertRaises(UserError):require_material_reviews(record,'current',['walk'],['torso'])
        record['regions']['walk']['torso']['review']['material_checks']=dict.fromkeys(ASSEMBLY_CHECKS,True)
        require_material_reviews(record,'current',['walk'],['torso'])
        with self.assertRaises(UserError):require_material_reviews(record,'changed',['walk'],['torso'])
        with self.assertRaises(UserError):require_material_reviews(record,'current',['idle','walk'],['torso'])
        record['regions']['walk']['torso']['review']['material_checks']['garment_continuity']=False
        with self.assertRaises(UserError):require_material_reviews(record,'current',['walk'],['torso'])

    def test_costume_completion_is_scoped_reviewed_single_image(self):
        plan=generation_plan({'mode':'complete-parts','sheets':['costume'],'instructions_by_sheet':{'costume':'保留蓝色轻纱'}})
        self.assertEqual(plan['image_count'],1)
        item=plan['items'][0]
        self.assertEqual(item['parts'],['torso']);self.assertEqual(item['groups'],{'torso':'costume'})
        self.assertTrue(item['requires_reviewed_layout']);self.assertFalse(item['reference_only'])
        self.assertEqual(item['prompt_language'],'zh')
        for duplicate in ('body','character-assets'):
            with self.assertRaises(UserError):generation_plan({'mode':'complete-parts','sheets':['costume',duplicate]})


class ImportAndReviewTests(unittest.TestCase):
    def setUp(self):
        from tests.test_asset_sheet_workflow import AssetSheetWorkflowTests
        self.f=AssetSheetWorkflowTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_back_without_anchor_fails_before_any_import_mutation(self):
        c=self.f.character();before=copy.deepcopy(c.meta)
        parts=copy.deepcopy(self.f.layout)+[{'name':'back','box':[30,20,120,130]}]
        with self.assertRaisesRegex(UserError,'定位点'):
            c.apply_source_layout({'revision':c.meta['revision'],'parts':parts})
        self.assertEqual(c.meta,before)
        self.assertFalse((c.directory/'part-images').exists())
        guide={**c.meta['landmarks'],'back':[.5,.4]}
        c.update({'revision':c.meta['revision'],'landmarks':guide,'settings':c.meta['settings']})
        c.apply_source_layout({'revision':c.meta['revision'],'parts':parts})
        self.assertIn('back',[p['name'] for p in c.base_parts()])
        self.assertEqual(c.asset_status()['unbound_parts'],[])
        # Legacy saved data must also diagnose missing anchors, never drop the part.
        del c.meta['landmarks']['back'];c.cache={}
        self.assertFalse(c.asset_status()['ready'])
        self.assertEqual(c.asset_status()['unbound_parts'],['back'])
        with self.assertRaisesRegex(UserError,'定位点'):c.base_parts()

    def test_new_task_requires_structured_review_before_and_after_assembly(self):
        self.assertEqual(self.f.tasks.read(self.f.task_id)['integrity_contract'],CONTRACT)
        self.f.look();self.f.assess()
        with self.assertRaises(UserError):self.f.review_parts(material_checks=None)
        bad=dict.fromkeys(ASSEMBLY_CHECKS,True);bad['garment_continuity']=False
        with self.assertRaises(UserError):self.f.review_parts(material_checks=bad)
        self.f.review_parts(verdict='needs_adjustment',material_checks=bad)
        self.f.action('apply-source-layout',parts=self.f.layout);self.f.look('assembly')
        with self.assertRaises(UserError):self.f.assess(parts_reviewed=True,material_checks=bad)
        self.f.assess(parts_reviewed=True)

    def test_old_task_keeps_its_original_review_contract(self):
        task=self.f.tasks.read(self.f.task_id);task.pop('integrity_contract')
        self.f.tasks.save_tool_state(task)
        self.f.look();self.f.assess()
        review=self.f.review_parts(material_checks=None)
        self.assertEqual(review['verdict'],'ready')

    def test_costume_uses_additional_budget_never_free_baseline(self):
        tasks=self.f.tasks;t=tasks.read(self.f.task_id)
        tasks.reserve(t,'costume:'+uuid.uuid4().hex,1,{'sheets':['costume']},['costume'])
        reservation=list(t['reservations'].values())[-1]
        self.assertEqual(reservation['count'],1)
        self.assertEqual(reservation['baseline_count'],0)
        t['max_generations']=0
        with self.assertRaises(UserError):tasks.reserve(t,'costume:'+uuid.uuid4().hex,1,{'sheets':['costume']},['costume'])

    def test_candidate_pixel_change_invalidates_part_attestation(self):
        f=self.f;c=f.character();job=uuid.uuid4().hex;folder=c.directory/'jobs';folder.mkdir(exist_ok=True)
        atomic_json(folder/(job+'.json'),{'id':job,'sheet':'character-assets','mode':'complete-parts','candidate_ready':True,'source_image_id':c.meta['image_id']})
        (folder/(job+'.png')).write_bytes(png_bytes(f.source))
        f.look();f.assess();f.tasks.tool(f.task_id,'spine_image',{'kind':'character','id':f.entity,'view':'candidate','job_id':job})
        f.review_parts(job_id=job)
        (folder/(job+'.png')).write_bytes(png_bytes(f.source.transpose(0)))
        with self.assertRaises(UserError):f.review_parts(job_id=job)
        with self.assertRaisesRegex(UserError,'候选已变化'):
            f.action('apply-part-layout',job_id=job,parts=f.layout)

if __name__=='__main__':unittest.main()
