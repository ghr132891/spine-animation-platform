import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app.worker_config import model_configuration
from app.agent_tasks import AgentTasks
from app.core import UserError

class WorkerModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.env={'CODEX_HOME':str(self.home)}

    def config(self,text):
        (self.home/'config.toml').write_text(text)
        return model_configuration(self.env)

    def test_isolated_worker_retains_custom_endpoint_and_model(self):
        config,env=self.config('''model="chosen-model"
model_provider="company"
model_reasoning_effort="high"
approval_policy="never"
[model_providers.company]
name="Company"
base_url="https://models.example/v1"
wire_api="responses"
requires_openai_auth=true
[mcp_servers.unrelated]
command="unrelated-executable"
''')
        self.assertEqual(config['model_providers.company.base_url'],'https://models.example/v1')
        self.assertEqual(config['model'],'chosen-model');self.assertEqual(env,{})
        self.assertNotIn('approval_policy',config)
        agent=object.__new__(AgentTasks);agent.root=self.home;agent.base='http://127.0.0.1:8780'
        with patch('app.agent_tasks.shutil.which',return_value='/bin/codex'):
            cmd=agent.command('a'*32,self.home/'output.json',config)
        self.assertIn('--ignore-user-config',cmd)
        self.assertIn('model_providers.company.base_url="https://models.example/v1"',cmd)
        self.assertNotIn('unrelated-executable',' '.join(cmd))

    def test_inline_credentials_are_environment_only(self):
        config,env=self.config('''model_provider="company"
[model_providers.company]
base_url="https://models.example/v1"
experimental_bearer_token="PRIVATE_TOKEN"
[model_providers.company.http_headers]
X-Api-Key="PRIVATE_HEADER"
''')
        self.assertNotIn('PRIVATE',json.dumps(config))
        self.assertIn('PRIVATE_TOKEN',env.values());self.assertIn('PRIVATE_HEADER',env.values())

    def test_profile_override_keeps_only_selected_provider(self):
        config,_=self.config('''profile="work"
model="base"
model_provider="one"
[model_providers.one]
base_url="https://one.example"
[model_providers.two]
base_url="https://two.example"
[profiles.work]
model="profile-model"
model_provider="two"
''')
        self.assertEqual(config['model'],'profile-model')
        self.assertIn('model_providers.two.base_url',config)
        self.assertNotIn('model_providers.one.base_url',config)

    def test_file_profile_and_no_config(self):
        self.assertEqual(model_configuration(self.env),({},{}))
        (self.home/'work.config.toml').write_text('model="profile-model"')
        config,_=self.config('profile="work"\nmodel="base"')
        self.assertEqual(config['model'],'profile-model')

    def test_invalid_custom_config_never_falls_back(self):
        for text in ('model_provider="missing"','invalid=[','model_provider="x"\n[model_providers.x]\nbase_url="https://secret@example.com"'):
            with self.subTest(text=text),self.assertRaises(UserError):self.config(text)
