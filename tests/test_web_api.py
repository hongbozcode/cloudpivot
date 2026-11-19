import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from core.agent_setting import AgentSettings
from core.web_settings import WebSettings
from main import create_app


class SettingsTest(unittest.TestCase):
    def test_shared_dotenv_preserves_existing_formats_and_environment_overrides(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'BASE_LLM': 'process-model'}, clear=True):
            env_file = Path(directory) / '.env'
            env_file.write_text(
                'BASE_LLM=file-model\n'
                'CLOUDPIVOT_PLATFORM_LLMS=backup, process-model, backup\n'
                'CLOUDPIVOT_CORS_ORIGINS=https://a.example, https://b.example\n'
                'CLOUDPIVOT_DATABASE_URL=db.example:5433\n'
                'CLOUDPIVOT_DATABASE_USER=app\n'
                'CLOUDPIVOT_DATABASE_NAME=cloudpivot\n'
                'CLOUDPIVOT_REDIS_URL=cache.example:6379\n'
                'UVICORN_PORT=8002\n'
                'OPENAI_API_KEY=test-key\n'
                'AGENT_WORKSPACE_ROOT=\n'
                'USE_EXCEL=true\n',
                encoding='utf-8',
            )
            web = WebSettings(_env_file=env_file)
            agent = AgentSettings(_env_file=env_file)
            self.assertNotIn('OPENAI_API_KEY', os.environ)

        self.assertEqual(
            {'host': 'db.example', 'port': '5433', 'user': 'app', 'dbname': 'cloudpivot'},
            conninfo_to_dict(web.database_url),
        )
        self.assertEqual('redis://cache.example:6379/1', web.redis_url)
        self.assertEqual(('https://a.example', 'https://b.example'), web.cors_origins)
        self.assertEqual(('process-model', 'backup'), web.platform_llms)
        self.assertEqual(8002, web.uvicorn_port)
        self.assertEqual('process-model', agent.base_llm)
        self.assertEqual('test-key', agent.openai_api_key.get_secret_value())
        self.assertEqual(Path('/agent_files'), agent.workspace_root)
        self.assertTrue(agent.use_excel)


class HealthEndpointTest(unittest.TestCase):
    def test_health_returns_ok(self):
        with TestClient(create_app(settings=WebSettings(database_url='', redis_url='', _env_file=None))) as client:
            response = client.get('/api/health')

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'ok'}, response.json())
