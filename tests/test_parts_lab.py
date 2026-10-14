import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app.core import atomic_json, png_bytes, UserError
from app.parts_lab import PartsLab, evidence_digest, extract_candidate
from app.see_through import run

class PartsLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.id='a'*32;self.lab=PartsLab(self.root,None)
        self.folder=self.lab.directory(self.id);self.folder.mkdir(parents=True)
        atomic_json(self.folder/'experiment.json',{'id':self.id,'routes':{},'animation_allowed':False})

    def test_unknown_template_submission_never_reposts(self):
        path=self.lab.jobs.path(self.id,self.id)
        atomic_json(path,{'id':self.id,'status':'submission_unknown'})
        self.lab._run(self.id,'template')
        self.assertEqual(self.lab.status(self.id)['jobs']['template']['status'],'submission_unknown')

    def test_unknown_seethrough_submission_never_reposts(self):
        from app.core import sha
        content=png_bytes(Image.new('RGBA',(20,20),'blue'))
        atomic_json(self.folder/'job.json',{'status':'submission_unknown','source_sha256':sha(content),'resolution':768})
        with patch('app.see_through.request',side_effect=AssertionError('Unexpected POST')):
            self.assertEqual(run(self.folder,content)['status'],'submission_unknown')

    def test_concurrent_route_lock_prevents_duplicate(self):
        import fcntl
        with (self.folder/'template.lock').open('a') as lease:
            fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.lab._run(self.id,'template')
        self.assertFalse(self.lab.jobs.path(self.id,self.id).exists())

    def test_missing_or_stale_evidence_cannot_pass(self):
        data={'route':'template','digest':'old','verdict':'ready','reason':'viewed',
              'checks':dict.fromkeys(['complete_parts','assembly_matches','spread_intact','connection_geometry','appearance_matches'],True)}
        with self.assertRaises(UserError):self.lab.review(self.id,data)
        for name in ('parts.png','assembly.png','spread.png'):Image.new('RGBA',(10,10),'blue').save(self.folder/name)
        digest=evidence_digest(self.folder);atomic_json(self.folder/'inspection.json',{'digest':digest,'blockers':['merged legs']})
        data['digest']=digest
        with self.assertRaises(UserError):self.lab.review(self.id,data)
        atomic_json(self.folder/'inspection.json',{'digest':digest,'blockers':[]})
        Image.new('RGBA',(10,10),'green').save(self.folder/'assembly.png')
        with self.assertRaises(UserError):self.lab.review(self.id,data)

    def test_template_geometry_edit_invalidates_review(self):
        atomic_json(self.folder/'template.json',{'pins':[[2,3]]});before=evidence_digest(self.folder)
        atomic_json(self.folder/'template.json',{'pins':[[3,3]]})
        self.assertNotEqual(before,evidence_digest(self.folder))

    def test_status_does_not_display_stale_acceptance(self):
        Image.new('RGBA',(10,10),'blue').save(self.folder/'assembly.png')
        exp=self.lab.read(self.id)
        exp['reviews']={'template':{'verdict':'ready','digest':evidence_digest(self.folder),'reason':'checked'}}
        atomic_json(self.folder/'experiment.json',exp)
        Image.new('RGBA',(10,10),'green').save(self.folder/'assembly.png')
        result=self.lab.status(self.id)['reviews']['template']
        self.assertTrue(result['stale'])
        self.assertEqual(result['verdict'],'needs_repair')

    def test_raw_alpha_not_forced_to_template(self):
        (self.folder/'reference-parts').mkdir()
        ref=Image.new('RGBA',(40,60));ref.paste((30,90,100,255),(15,0,25,60));ref.save(self.folder/'reference-parts/arm.png')
        im=Image.new('RGBA',(200,200),'white');im.paste((100,40,20,255),(40,30,150,160));im.save(self.folder/'candidate.png')
        atomic_json(self.folder/'template.json',{'canvas':[200,200],'slots':[{'name':'arm','cell':[0,0,200,200]}]})
        rows=extract_candidate(self.folder)
        self.assertGreater(rows[0]['aspect_ratio_relative_error'],1)
        with Image.open(self.folder/'parts/arm.png') as result:self.assertEqual(result.size,(110,130))

    def test_private_receipt_and_path_traversal_not_downloadable(self):
        for name in ('../secret.png','see-through/job.json','/etc/passwd'):
            with self.assertRaises(UserError):self.lab.file(self.id,name)

if __name__=='__main__':unittest.main()
