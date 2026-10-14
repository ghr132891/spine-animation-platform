import copy
import unittest

from app.core import UserError
from app.orientation_review import (CONTRACT, ASSEMBLY_CHECKS, ARM_CHECKS, LEG_CHECKS,
                                    checked_orientation, require_orientation_reviews)


class OrientationTests(unittest.TestCase):
    def test_each_side_can_fail_without_certifying_ready(self):
        for anchor, fields in ((None, ASSEMBLY_CHECKS), ('l-upper-arm', ARM_CHECKS),
                               ('r-upper-arm', ARM_CHECKS), ('l-upper-leg', LEG_CHECKS),
                               ('r-upper-leg', LEG_CHECKS)):
            good = dict.fromkeys(fields, True)
            for field in fields:
                bad = {**good, field: False}
                with self.subTest(anchor=anchor, field=field):
                    with self.assertRaises(UserError):
                        checked_orientation({'verdict': 'ready', 'orientation_checks': bad}, anchor)
                    self.assertEqual(checked_orientation({'verdict': 'needs_repair', 'orientation_checks': bad}, anchor), bad)
            for invalid in (None, {}, dict.fromkeys(fields, 1), {**good, 'unrelated': True}):
                with self.assertRaises(UserError):
                    checked_orientation({'verdict': 'ready', 'orientation_checks': invalid}, anchor)

    def test_final_evidence_requires_both_sides_all_clips_current_composed_pixels(self):
        anchors = ['l-upper-arm', 'r-upper-arm', 'l-upper-leg', 'r-upper-leg']
        clips = ['idle', 'walk']
        regions = {clip: {anchor: {'report': {'frames': 8, 'isolated': False},
                    'review': {'verdict': 'ready', 'orientation_checks': dict.fromkeys(
                        ARM_CHECKS if 'arm' in anchor else LEG_CHECKS, True)}} for anchor in anchors} for clip in clips}
        evidence = {'sha256': 'current', 'regions': regions}
        require_orientation_reviews(evidence, 'current', clips, anchors)
        with self.assertRaises(UserError):require_orientation_reviews(evidence, 'changed', clips, anchors)
        for clip in clips:
            for anchor in anchors:
                for defect in ('missing', 'isolated', 'frames', 'false'):
                    bad = copy.deepcopy(evidence); item = bad['regions'][clip][anchor]
                    if defect == 'missing':item['review'].pop('orientation_checks')
                    if defect == 'isolated':item['report']['isolated'] = True
                    if defect == 'frames':item['report']['frames'] = 1
                    if defect == 'false':item['review']['orientation_checks'][next(iter(item['review']['orientation_checks']))] = False
                    with self.assertRaises(UserError):require_orientation_reviews(bad, 'current', clips, anchors)


class OrientationWorkflowTests(unittest.TestCase):
    def setUp(self):
        try:
            from tests.test_asset_sheet_workflow import AssetSheetWorkflowTests
        except ModuleNotFoundError:
            from test_asset_sheet_workflow import AssetSheetWorkflowTests
        self.fixture = AssetSheetWorkflowTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_real_import_and_assembly_cannot_skip_orientation_checks(self):
        f = self.fixture
        self.assertEqual(f.tasks.read(f.task_id)['orientation_contract'], CONTRACT)
        f.look(); f.assess()
        with self.assertRaises(UserError):f.review_parts(orientation_checks=None)
        wrong = dict.fromkeys(ASSEMBLY_CHECKS, True); wrong['right_palm_facing'] = False
        with self.assertRaises(UserError):f.review_parts(orientation_checks=wrong)
        f.review_parts(verdict='needs_adjustment', orientation_checks=wrong)
        f.action('apply-source-layout', parts=f.layout)
        from tests.test_asset_sheet_workflow import accept_fixture_prebind
        accept_fixture_prebind(f.character()); f.look('assembly')
        with self.assertRaises(UserError):f.assess(parts_reviewed=True, orientation_checks=wrong)
        f.assess(parts_reviewed=True)
        f.action('build')
        self.assertFalse(any('/generate' in call[0] for call in f.calls))

    def test_legacy_task_is_not_retroactively_blocked(self):
        f = self.fixture; t = f.tasks.read(f.task_id); t.pop('orientation_contract'); f.tasks.save_tool_state(t)
        f.look(); f.assess(); f.review_parts(orientation_checks=None)


if __name__ == '__main__':unittest.main()
