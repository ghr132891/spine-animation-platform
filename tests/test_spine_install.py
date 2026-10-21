import importlib.util,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];SCRIPTS=ROOT/'skills/spine-animation/scripts';sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('spine_installer',SCRIPTS/'install.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'shared package/spine-animation';self.dest=self.root/'recipient/.codex/skills/spine-animation';self.token=self.root/'private credential.token';self.token.write_text('private-test-token')
        shutil.copytree(SCRIPTS.parent,self.source,ignore=shutil.ignore_patterns('__pycache__'));(self.source/'settings.json').write_text('private config');(self.source/'should-not-copy.token').write_text('private credential')
        self.checked={'base':'http://server.test:8780','platform_version':'1.10.0','authenticated':True}

    def tearDown(self):self.tmp.cleanup()

    def test_only_skill_files_installed_and_command_arguments_preserved(self):
        with patch.object(installer,'probe',return_value=self.checked) as check,patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            result=installer.install(self.source,self.dest,'http://server.test:8780/',self.token,'/path with spaces/codex')
        check.assert_called_once_with('http://server.test:8780/','private-test-token');self.assertEqual(result['mcp_server'],'spine_animation')
        args=run.call_args.args[0];self.assertEqual(args[0],'/path with spaces/codex');self.assertEqual(args[-1],str(self.token.resolve()));self.assertNotIn('private-test-token',str(args))
        self.assertEqual(set(str(p.relative_to(self.dest)) for p in self.dest.rglob('*') if p.is_file()),set(installer.FILES)|{'settings.json'})
        config=json.loads((self.dest/'settings.json').read_text());self.assertEqual(config['base'],self.checked['base']);self.assertEqual(config['token_file'],str(self.token.resolve()));self.assertNotIn('private-test-token',(self.dest/'settings.json').read_text())

    def test_failed_precheck_does_not_write_installation(self):
        with patch.object(installer,'probe',side_effect=ValueError('bad credential')),patch.object(installer.subprocess,'run') as run:
            with self.assertRaises(ValueError):installer.install(self.source,self.dest,'http://server.test:8780',self.token,'codex')
        self.assertFalse(self.dest.exists());run.assert_not_called()

    def test_failed_registration_preserves_existing_connection_settings(self):
        self.dest.mkdir(parents=True);previous=self.dest/'settings.json';previous.write_text('previous config')
        with patch.object(installer,'probe',return_value=self.checked),patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','failed')):
            with self.assertRaises(ValueError):installer.install(self.source,self.dest,'http://server.test:8780',self.token,'codex')
        self.assertEqual(previous.read_text(),'previous config')

    def test_precheck_requires_refinement(self):
        with patch.object(installer.Bridge,'call',return_value={'characters':[],'capabilities':['codex-image-preflight','matrix-gpt-image-2.5-flare','matrix-reference-cache','server-reference-upload','runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-contact-diagnostics','smooth-gait-curves','character-asset-sheet-first','side-walk-preparation','joint-reviewed-assembly','neck-seam-closeups','scoped-texture-repair','turnaround-reference-review','multi-region-closeups','scoped-material-finish','contrast-material-review','asset-integrity-review','costume-completion','strict-layer-anchors','durable-task-resume','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling','platform-review-bundle','scoped-edge-cleanup']}),patch.object(installer.Bridge,'api',return_value={'paths':{},'info':{'version':'old'}}):
            with self.assertRaises(ValueError):installer.probe('http://server.test:8780','private-test-token')

    def test_windows_utf8_bom_token_file(self):
        self.token.write_text('private-test-token',encoding='utf-8-sig')
        with patch.object(installer,'probe',return_value=self.checked) as check,patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
            installer.install(self.source,self.dest,'http://server.test:8780',self.token,'codex')
        self.assertEqual(check.call_args.args[1],'private-test-token')

    def test_precheck_rejects_old_api_and_accepts_new_capabilities(self):
        paths={'/api/automation/agent/refine':{}}
        with patch.object(installer.Bridge,'call',return_value={'characters':[],'capabilities':['codex-image-preflight','matrix-gpt-image-2.5-flare','matrix-reference-cache','server-reference-upload','runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-contact-diagnostics','smooth-gait-curves','character-asset-sheet-first','side-walk-preparation','joint-reviewed-assembly','neck-seam-closeups','scoped-texture-repair','turnaround-reference-review','multi-region-closeups','scoped-material-finish','contrast-material-review','asset-integrity-review','costume-completion','strict-layer-anchors','durable-task-resume','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling','platform-review-bundle','scoped-edge-cleanup']}),patch.object(installer.Bridge,'api',return_value={'paths':paths}):
            with self.assertRaises(ValueError):installer.probe('http://server.test:8780','private-test-token')
        api=json.loads((ROOT/'agent/openapi.json').read_text())
        with patch.object(installer.Bridge,'call',return_value={'characters':[],'capabilities':['codex-image-preflight','matrix-gpt-image-2.5-flare','matrix-reference-cache','server-reference-upload','runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-contact-diagnostics','smooth-gait-curves','character-asset-sheet-first','side-walk-preparation','joint-reviewed-assembly','neck-seam-closeups','scoped-texture-repair','turnaround-reference-review','multi-region-closeups','scoped-material-finish','contrast-material-review','asset-integrity-review','costume-completion','strict-layer-anchors','durable-task-resume','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling','platform-review-bundle','scoped-edge-cleanup']}),patch.object(installer.Bridge,'api',return_value=api):
            result=installer.probe('http://server.test:8780','private-test-token')
        self.assertEqual(result['platform_version'],(ROOT/'VERSION').read_text().strip());self.assertTrue(result['authenticated'])

    def test_precheck_rejects_server_missing_layout_and_motion_capabilities(self):
        api=json.loads((ROOT/'agent/openapi.json').read_text())
        old={'capabilities':['codex-image-preflight','matrix-gpt-image-2.5-flare','matrix-reference-cache','server-reference-upload','character-asset-sheet-first','side-walk-preparation','joint-reviewed-assembly','neck-seam-closeups','scoped-texture-repair','turnaround-reference-review','multi-region-closeups','scoped-material-finish','contrast-material-review','asset-integrity-review','costume-completion','strict-layer-anchors','durable-task-resume','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling','platform-review-bundle','scoped-edge-cleanup']}
        with patch.object(installer.Bridge,'call',return_value=old),patch.object(installer.Bridge,'api',return_value=api):
            with self.assertRaisesRegex(ValueError,'1.10.0'):installer.probe('http://server.test:8780')

    def test_precheck_requires_character_asset_sheet_first_capability(self):
        api=json.loads((ROOT/'agent/openapi.json').read_text())
        old={'capabilities':['codex-image-preflight','matrix-gpt-image-2.5-flare','matrix-reference-cache','server-reference-upload','runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-contact-diagnostics','smooth-gait-curves']}
        with patch.object(installer.Bridge,'call',return_value=old),patch.object(installer.Bridge,'api',return_value=api):
            with self.assertRaisesRegex(ValueError,'1.10.0'):installer.probe('http://server.test:8780')

    def test_no_token_precheck_and_installation(self):
        api=json.loads((ROOT/'agent/openapi.json').read_text())
        with patch.object(installer.Bridge,'call',return_value={'characters':[],'capabilities':['codex-image-preflight','matrix-gpt-image-2.5-flare','matrix-reference-cache','server-reference-upload','runtime-motion-diagnostics','reviewed-part-layout','continuous-limb-mesh','pixel-contact-diagnostics','smooth-gait-curves','character-asset-sheet-first','side-walk-preparation','joint-reviewed-assembly','neck-seam-closeups','scoped-texture-repair','turnaround-reference-review','multi-region-closeups','scoped-material-finish','contrast-material-review','asset-integrity-review','costume-completion','strict-layer-anchors','durable-task-resume','garment-occlusion-review','isolated-limb-preview','runtime-preview-cache','parallel-runtime-sampling','platform-review-bundle','scoped-edge-cleanup'],'access':{'mode':'open','requires_token':False}}),patch.object(installer.Bridge,'api',return_value=api):
            checked=installer.probe('http://server.test:8780')
        self.assertFalse(checked['authenticated']);self.assertEqual(checked['access_mode'],'open')
        with patch.object(installer,'probe',return_value=checked) as check,patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            installer.install(self.source,self.dest,'http://server.test:8780',None,'codex')
        check.assert_called_once_with('http://server.test:8780','')
        self.assertNotIn('--token-file',run.call_args.args[0])
        self.assertEqual(json.loads((self.dest/'settings.json').read_text()),{'base':'http://server.test:8780'})
        self.assertFalse(list(self.dest.rglob('*.token')))


if __name__=='__main__':unittest.main()
