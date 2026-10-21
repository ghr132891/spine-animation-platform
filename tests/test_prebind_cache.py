import copy
import json
import tempfile
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from app.character import CharacterProject
from app.core import png_bytes
from app.prebind import inspect
from tests.test_asset_sheet_workflow import asset_fixture,assembly_guide


class PrebindCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c=CharacterProject.create(Path(self.temp.name),'cache regression')
        image,layout=asset_fixture();self.c.set_image(png_bytes(image),0)
        self.c.update({'revision':self.c.meta['revision'],'landmarks':assembly_guide(),'settings':self.c.meta['settings']})
        self.c.apply_source_layout({'revision':self.c.meta['revision'],'parts':layout})
        # Exercise a cold read independently of automatic import preparation.
        shutil.rmtree(self.c.directory/'prebind-cache')

    def test_simultaneous_panels_and_restart_render_once(self):
        c=self.c
        with patch('app.prebind.inspect',wraps=inspect) as render:
            with ThreadPoolExecutor(max_workers=3) as pool:
                results=list(pool.map(lambda _:c.prebind_evidence(),range(3)))
            restored=CharacterProject(c.directory).prebind_evidence()
            self.assertEqual(render.call_count,1)
        for report,images in [*results,restored]:
            self.assertEqual(report,results[0][0])
            for name in images:self.assertEqual(png_bytes(images[name]),png_bytes(results[0][1][name]))
        self.assertNotIn('prebind_review',c.meta)

    def test_acceptance_reuses_but_placement_invalidates(self):
        c=self.c
        with patch('app.prebind.inspect',wraps=inspect) as render:
            report,_=c.prebind_evidence()
            c.review_prebind({'revision':c.meta['revision'],'digest':report['digest'],'checks':dict.fromkeys(report['checks'],True),'verdict':'ready','reason':'Synthetic complete fixture inspected.'})
            c.prebind_evidence();self.assertEqual(render.call_count,1)
            c.update_review({'revision':c.meta['revision'],'part_review':{'adjustments':{'head':{'dx':3}},'draw_order':c.review_order([p['name'] for p in c.parts()])}})
            new,_=c.prebind_evidence();self.assertNotEqual(new['digest'],report['digest'])
            self.assertEqual(render.call_count,2)

    def test_damaged_panel_rebuilds_and_output_mutation_is_isolated(self):
        c=self.c;report,images=c.prebind_evidence();expected=copy.deepcopy(report)
        report['checks'].clear();images['assembly'].paste((255,0,0,255),(0,0,*images['assembly'].size))
        folder=next((c.directory/'prebind-cache').iterdir());(folder/'assembly.png').write_bytes(b'broken')
        with patch('app.prebind.inspect',wraps=inspect) as render:
            repaired,_=c.prebind_evidence();self.assertEqual(render.call_count,1)
        self.assertEqual(repaired,expected)
