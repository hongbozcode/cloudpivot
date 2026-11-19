import json
import os
import unittest
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from core.web_settings import WebSettings
from main import create_app


class AccountChatEndpointTest(unittest.TestCase):
    def test_account_features_require_database_configuration(self):
        with TestClient(create_app(settings=WebSettings(database_url='', redis_url='', _env_file=None))) as client:
            response = client.get('/api/auth/me')

        self.assertEqual(503, response.status_code)

    def test_redis_configuration_never_selects_database_zero(self):
        with patch.dict(os.environ, {
            'CLOUDPIVOT_DATABASE_URL': '',
            'CLOUDPIVOT_REDIS_URL': '127.0.0.1:6379',
            'CLOUDPIVOT_REDIS_PASSWORD': '',
        }):
            self.assertEqual('redis://127.0.0.1:6379/1', WebSettings().redis_url)
        with self.assertRaisesRegex(ValueError, 'DB 1'):
            WebSettings(redis_url='redis://127.0.0.1:6379/0')


@unittest.skipUnless(os.getenv('CLOUDPIVOT_TEST_DATABASE_URL'), '需要 PostgreSQL 集成测试库')
class AccountChatIntegrationTest(unittest.TestCase):
    def setUp(self):
        import asyncio
        import threading

        from langchain.tools import tool
        from psycopg import connect, sql
        from psycopg.conninfo import make_conninfo

        from api.bootstrap import event_loop
        from api.manage import create_operator
        from core import agent_setting as config
        from core.security import hash_password
        from content.all_agent import AllAgent
        from content.workspace import get_workspace_root
        from support_chat import TestChatModel

        self.writer_started = threading.Event()
        self.finish_writing = threading.Event()

        @tool
        def write_slow_report() -> str:
            """Write a report after a controlled delay."""
            self.writer_started.set()
            if not self.finish_writing.wait(timeout=5):
                raise TimeoutError
            root = get_workspace_root()
            root.mkdir(parents=True, exist_ok=True)
            (root / 'slow-report.txt').write_text('慢速报告', encoding='utf-8')
            return '报告完成'

        class TestAgent(AllAgent):
            def _get_tools(self):
                return [*super()._get_tools(), write_slow_report]

        database_url = os.environ['CLOUDPIVOT_TEST_DATABASE_URL']
        schema_name = f'cloudpivot_test_{uuid4().hex}'
        schema = sql.Identifier(schema_name)
        with connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(schema))

        def cleanup():
            with connect(database_url, autocommit=True) as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(schema))

        self.addCleanup(cleanup)
        test_url = make_conninfo(database_url, options=f'-c search_path={schema_name}')

        with asyncio.Runner(loop_factory=event_loop) as runner:
            runner.run(create_operator(test_url, 'operator', hash_password('operator-test-pass')))
        self.enterContext(patch.multiple(
            config, USE_EXCEL=False, TAVILY_SEARCH_KEY='', ROOT_PATH_AGENT=self.enterContext(TemporaryDirectory()),
        ))
        settings = WebSettings(
            database_url=test_url,
            redis_url=os.getenv('CLOUDPIVOT_TEST_REDIS_URL', ''),
            cookie_secure=False,
            _env_file=None,
        )
        self.app = create_app(settings=settings, agent_factory=lambda saver: TestAgent(
            model=TestChatModel(), checkpointer=saver,
        ).agent, progress_model_factory=TestChatModel)
        self.clients = self.enterContext(ExitStack())
        self.client = self.clients.enter_context(TestClient(self.app, backend_options={'loop_factory': event_loop}))

    def login(self, username, password='member-test-pass'):
        self.client.cookies.clear()
        response = self.client.post('/api/auth/login', json={'username': username, 'password': password})
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn('httponly', response.headers['set-cookie'].lower())
        return response.json()

    def create_invite(self):
        response = self.client.post('/api/ops/invitations', json={})
        self.assertEqual(201, response.status_code, response.text)
        return response.json()['token']

    def register(self, username, token):
        return self.client.post('/api/auth/register', json={
            'username': username, 'password': 'member-test-pass', 'token': token,
        })

    def test_operator_imports_a_reviewed_legacy_workspace_for_the_target_account(self):
        from pathlib import Path

        from content.workspace import get_workspace_root

        operator = self.login('operator', 'operator-test-pass')
        self.register('import-owner', self.create_invite())
        owner = self.login('import-owner')
        body = {'source_type': 'session', 'source': 'sales-oct', 'account_id': owner['id']}
        endpoint = '/api/ops/workspace-imports'
        self.assertEqual(403, self.client.post(endpoint + '/preview', json=body).status_code)
        self.login(operator['username'], 'operator-test-pass')
        self.assertEqual(404, self.client.post(endpoint + '/preview', json=body).status_code)
        root = get_workspace_root({'configurable': {'thread_id': 'sales-oct'}})
        skill = root / 'skills' / 'sales' / 'SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: sales\ndescription: Sales checks\n---\nCheck revenue.', encoding='utf-8')
        (skill.parent / 'reference.md').write_text('Sales reference', encoding='utf-8')
        report = root / 'sales.txt'
        report.write_bytes(b'old report')
        history = root / 'conversation_history'
        history.mkdir()
        (history / 'old.txt').write_bytes(b'old chat')
        preview = self.client.post(endpoint + '/preview', json=body)
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual(404, self.client.post(endpoint + '/preview', json={**body, 'account_id': str(uuid4())}).status_code)
        self.assertEqual(['sales.txt'], preview.json()['files'])
        self.assertEqual(['skills/sales/SKILL.md'], preview.json()['skills'])
        self.assertEqual(['skills/sales/reference.md'], preview.json()['skill_files'])
        self.assertEqual(preview.json()['confirmation'], self.client.post(endpoint + '/preview', json={
            **body, 'source_type': 'directory', 'source': str(root),
        }).json()['confirmation'])
        self.assertEqual(422, self.client.post(endpoint + '/preview', json={
            **body, 'source_type': 'directory', 'source': '   ',
        }).status_code)
        report.write_bytes(b'updated report')
        self.assertEqual(409, self.client.post(endpoint, json={**body, 'confirmation': preview.json()['confirmation']}).status_code)
        preview = self.client.post(endpoint + '/preview', json=body).json()
        imported = self.client.post(endpoint, json={**body, 'confirmation': preview['confirmation']})
        self.assertEqual(201, imported.status_code, imported.text)
        session_id = imported.json()['session_id']
        self.assertEqual(session_id, self.client.post(
            endpoint, json={**body, 'confirmation': preview['confirmation']},
        ).json()['session_id'])
        session = f'/api/sessions/{session_id}'
        self.assertEqual(404, self.client.get(session + '/files').status_code)
        self.assertEqual(404, self.client.post(endpoint + '/preview', json={
            **body, 'source': session_id,
        }).status_code)
        self.login('import-owner')
        self.assertEqual([], self.client.get(session + '/messages').json())
        files = self.client.get(session + '/files').json()
        file_id = next(file['id'] for file in files if file['name'] == 'sales.txt')
        self.assertEqual(b'updated report', self.client.get(f'{session}/files/{file_id}/download').content)
        imported_root = get_workspace_root({'configurable': {'workspace_id': session_id}})
        self.assertEqual(skill.read_bytes(), (imported_root / Path('skills/sales/SKILL.md')).read_bytes())
        self.assertFalse((imported_root / 'conversation_history').exists())
        run = self.client.post(session + '/runs', json={'text': '请使用销售 Skill'})
        self.assertEqual(202, run.status_code, run.text)
        self.assertIn('已读取迁入的销售 Skill。', self.client.get(f"/api/runs/{run.json()['id']}/events").text)

    def test_uploaded_csv_drives_a_run_and_the_same_artifact_can_be_previewed_and_downloaded(self):
        self.login('operator', 'operator-test-pass')
        first, second = self.create_invite(), self.create_invite()
        self.assertEqual(201, self.register('file-owner', first).status_code)
        self.login('file-owner')
        session = self.client.post('/api/sessions').json()['id']
        files = f'/api/sessions/{session}/files'
        upload = self.client.post(files, params={'name': '明细.csv'}, content='地区,销售额\n华东,128000\n'.encode())
        self.assertEqual(201, upload.status_code, upload.text)
        source = upload.json()
        run = self.client.post(f'/api/sessions/{session}/runs', json={
            'text': '汇总上传的销售表', 'attachment_ids': [source['id']],
        })
        self.assertEqual(202, run.status_code, run.text)
        events = self.client.get(f"/api/runs/{run.json()['id']}/events").text
        self.assertIn('地区汇总.csv', events)
        listed = self.client.get(files).json()
        artifact, = [item for item in listed if item['kind'] == 'artifact']
        self.assertIn(source, listed)
        self.assertIn(artifact['id'], events)
        preview = f"{files}/{artifact['id']}/preview"
        download = f"{files}/{artifact['id']}/download"
        self.assertEqual([['地区', '销售额'], ['华东', '128000']], self.client.get(preview).json()['rows'])
        self.assertEqual('地区,销售额\n华东,128000\n', self.client.get(download).content.decode())
        self.assertEqual([source['id']], self.client.get(f'/api/sessions/{session}/messages').json()[0]['attachment_ids'])

        edited = self.client.post(f'/api/sessions/{session}/runs', json={
            'text': '修改上传的销售表', 'attachment_ids': [source['id']],
        })
        self.assertEqual(202, edited.status_code, edited.text)
        edited_events = self.client.get(f"/api/runs/{edited.json()['id']}/events").text
        self.assertIn('"status": "completed"', edited_events)
        copied, = [item for item in self.client.get(files).json() if item['kind'] == 'artifact' and item['name'] == '明细.csv']
        self.assertIn(copied['id'], edited_events)
        self.assertIn(b'128000', self.client.get(f"{files}/{source['id']}/download").content)
        self.assertIn(b'96000', self.client.get(f"{files}/{copied['id']}/download").content)

        self.assertEqual(201, self.register('file-other', second).status_code)
        self.login('file-other')
        for url in (files, preview, download):
            self.assertEqual(404, self.client.get(url).status_code)
        other_session = self.client.post('/api/sessions').json()['id']
        invalid = self.client.post(f'/api/sessions/{other_session}/runs', json={
            'text': '读取文件', 'attachment_ids': [source['id']],
        })
        self.assertEqual(422, invalid.status_code)

    def test_preview_formats_and_download_fallback(self):
        from base64 import b64decode
        from io import BytesIO
        from pathlib import Path

        from openpyxl import Workbook
        from PIL import Image
        from pptx import Presentation
        from pypandoc import convert_text

        self.login('operator', 'operator-test-pass')
        session = self.client.post('/api/sessions').json()['id']

        def upload(name, data):
            path = f'/api/sessions/{session}/files'
            response = self.client.post(path, params={'name': name}, content=data)
            self.assertEqual(201, response.status_code, response.text)
            return f"{path}/{response.json()['id']}"

        book = Workbook()
        book.active.append(['工号', '销售额'])
        book.create_sheet('明细').append(['0017', 128000])
        data = BytesIO()
        book.save(data)
        url = upload('销售.xlsx', data.getvalue())
        preview = self.client.get(f'{url}/preview', params={'sheet': 1}).json()
        self.assertEqual(['Sheet', '明细'], preview['sheets'])
        self.assertEqual([['0017', 128000]], preview['rows'])
        self.assertEqual(data.getvalue(), self.client.get(f'{url}/download').content)

        png = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        url = upload('图像.png', png)
        response = self.client.get(f'{url}/preview')
        self.assertEqual('image/png', response.headers['content-type'])
        self.assertEqual(png, response.content)

        pdf = BytesIO()
        with Image.new('RGB', (64, 64), 'red') as first, Image.new('RGB', (64, 64), 'blue') as second:
            first.save(pdf, format='PDF', save_all=True, append_images=[second])
        url = upload('双页.pdf', pdf.getvalue())
        response = self.client.get(f'{url}/preview', params={'page': 1})
        self.assertEqual('image/png', response.headers['content-type'])
        self.assertEqual('2', response.headers['X-CloudPivot-PDF-Pages'])
        with Image.open(BytesIO(response.content)) as rendered:
            red, _, blue = rendered.getpixel((32, 32))
            self.assertGreater(blue, red)
        self.assertEqual(pdf.getvalue(), self.client.get(f'{url}/download').content)

        for name, kind, body in [('笔记.txt', 'text', '会议纪要'), ('说明.md', 'markdown', '# 说明')]:
            url = upload(name, body.encode())
            self.assertEqual(kind, self.client.get(f'{url}/preview').json()['kind'])
        long_note = upload('长文本.txt', ('文本' * 80_000).encode('utf-16'))
        response = self.client.get(f'{long_note}/preview')
        self.assertEqual(200, response.status_code, response.text[:200])
        self.assertTrue(response.json()['truncated'])
        self.assertTrue(response.json()['text'].startswith('文本文本'))

        word = Path(self.enterContext(TemporaryDirectory())) / 'report.docx'
        convert_text('# 报告', 'docx', format='md', outputfile=str(word))
        presentation = BytesIO()
        Presentation().save(presentation)
        for name, body in [('报告.docx', word.read_bytes()), ('汇报.pptx', presentation.getvalue())]:
            url = upload(name, body)
            self.assertEqual(415, self.client.get(f'{url}/preview').status_code)
            self.assertEqual(body, self.client.get(f'{url}/download').content)

        broken = upload('损坏.xlsx', b'broken spreadsheet')
        self.assertEqual(422, self.client.get(f'{broken}/preview').status_code)
        self.assertEqual(b'broken spreadsheet', self.client.get(f'{broken}/download').content)
        broken_pdf = upload('损坏.pdf', b'%PDF-1.4\n%%EOF\n')
        self.assertEqual(422, self.client.get(f'{broken_pdf}/preview').status_code)

    def test_daily_limit_changes_apply_to_followers_and_accounts_can_restore_the_default(self):
        self.login('operator', 'operator-test-pass')
        invites = [self.create_invite(), self.create_invite()]
        cap = '/api/ops/quotas'
        self.assertEqual(200, self.client.patch(cap, json={'daily_limit': 1}).status_code)
        for name, invite in zip(('quota-default', 'quota-custom'), invites):
            self.assertEqual(201, self.register(name, invite).status_code)
        accounts = {row['username']: row for row in self.client.get(cap).json()['accounts']}
        custom = f"{cap}/{accounts['quota-custom']['id']}"
        self.assertEqual(200, self.client.patch(custom, json={'daily_limit': 3}).status_code)

        def run(url):
            response = self.client.post(url, json={'text': '测试用量'})
            self.assertEqual(202, response.status_code, response.text)
            self.assertIn('"status": "completed"', self.client.get(f"/api/runs/{response.json()['id']}/events").text)

        self.login('quota-default')
        first = '/api/sessions/' + self.client.post('/api/sessions').json()['id']
        run(first + '/runs')
        self.assertEqual(429, self.client.post(first + '/runs', json={'text': '再试一次'}).status_code)
        self.assertEqual(200, self.client.get(first + '/messages').status_code)
        self.assertEqual(200, self.client.get(first + '/files').status_code)
        self.assertEqual(403, self.client.patch(cap, json={'daily_limit': 5}).status_code)
        self.assertEqual(403, self.client.patch(custom, json={'daily_limit': None}).status_code)
        self.assertEqual(403, self.client.patch('/api/ops/models', json={'llm': 'test', 'vlm': 'test'}).status_code)

        self.login('quota-custom')
        second = '/api/sessions/' + self.client.post('/api/sessions').json()['id']
        run(second + '/runs')
        self.login('operator', 'operator-test-pass')
        self.assertEqual(200, self.client.patch(cap, json={'daily_limit': 0}).status_code)
        rows = {row['username']: row for row in self.client.get(cap).json()['accounts']}
        self.assertEqual((1, 0), (rows['quota-default']['used'], rows['quota-default']['effective_limit']))
        self.assertEqual((1, 3), (rows['quota-custom']['used'], rows['quota-custom']['effective_limit']))

        self.login('quota-custom')
        run(second + '/runs')
        self.login('operator', 'operator-test-pass')
        self.assertEqual(200, self.client.patch(custom, json={'daily_limit': None}).status_code)
        restored = next(row for row in self.client.get(cap).json()['accounts'] if row['username'] == 'quota-custom')
        self.assertEqual((2, 0, None), (restored['used'], restored['effective_limit'], restored['daily_limit']))
        self.login('quota-custom')
        self.assertEqual(429, self.client.post(second + '/runs', json={'text': '再试一次'}).status_code)

    def test_personal_models_use_independent_defaults_and_session_overrides(self):
        from io import BytesIO

        from PIL import Image

        from api.bootstrap import event_loop
        from support_chat import TestPlatformChat

        self.clients.close()
        settings = self.app.state.settings.model_copy(update={
            'platform_llms': ('platform-chat',), 'platform_vlms': ('platform-vision',),
        })
        self.enterContext(patch('connection.llm.get_llm', side_effect=lambda model: TestPlatformChat(
            model_id=model if isinstance(model, str) else f'{model.model_id}/{model.max_output_tokens}',
        )))
        self.app = create_app(settings=settings)
        self.client = self.clients.enter_context(TestClient(self.app, backend_options={'loop_factory': event_loop}))
        self.login('operator', 'operator-test-pass')
        for username in ('model-owner', 'other-model-user'):
            self.assertEqual(201, self.register(username, self.create_invite()).status_code)
        self.login('model-owner')
        settings_url = '/api/models'
        secret = 'test-personal-secret'
        params = {
            'base_url': 'https://8.8.8.8/v1', 'model_id': 'personal-chat', 'api_key': secret,
            'input_budget': 32768, 'max_output_tokens': 4096, 'supports_temperature': False, 'temperature': None,
        }
        self.assertEqual(422, self.client.post(settings_url, json={
            **params, 'kind': 'llm', 'base_url': 'https://127.0.0.1/v1',
        }).status_code)
        model = self.client.post(settings_url, json={**params, 'kind': 'llm'})
        self.assertEqual(201, model.status_code, model.text)
        self.assertTrue(model.json()['has_api_key'])
        self.assertNotIn(secret, model.text)
        llm = model.json()['id']
        vision = self.client.post(settings_url, json={
            **params, 'kind': 'vlm', 'model_id': 'personal-vision', 'input_budget': 4096,
        }).json()['id']
        session = '/api/sessions/' + self.client.post('/api/sessions').json()['id']
        self.assertEqual('platform-chat', self.client.get(session + '/models').json()['effective']['llm']['model_id'])
        self.assertEqual(200, self.client.patch(settings_url + '/defaults', json={'llm': llm, 'vlm': None}).status_code)
        self.assertEqual(200, self.client.patch(session + '/models', json={'vlm': vision}).status_code)
        effective = self.client.get(session + '/models').json()['effective']
        self.assertEqual(('account', 'session'), (effective['llm']['source'], effective['vlm']['source']))

        def start():
            response = self.client.post(session + '/runs', json={'text': '你好'})
            self.assertEqual(202, response.status_code, response.text)
            return f"/api/runs/{response.json()['id']}/events"

        earlier = start()
        saved = self.client.patch(f'{settings_url}/{llm}', json={
            **params, 'api_key': None, 'model_id': 'updated-chat', 'max_output_tokens': 128,
        })
        self.assertEqual(200, saved.status_code, saved.text)
        self.assertIn('personal-chat/4096', self.client.get(earlier).text)
        self.assertIn('updated-chat/128', self.client.get(start()).text)
        image = BytesIO()
        Image.new('RGB', (2112, 2112)).save(image, format='PNG')
        upload = self.client.post(session + '/files', params={'name': 'large.png'}, content=image.getvalue())
        self.assertEqual(201, upload.status_code, upload.text)
        started = self.client.post(session + '/runs', json={
            'text': '请识别图片', 'attachment_ids': [upload.json()['id']],
        })
        self.assertEqual(202, started.status_code, started.text)
        self.assertIn('图片理解输入超过配置的预算。', self.client.get(
            f"/api/runs/{started.json()['id']}/events",
        ).text)
        self.assertEqual(200, self.client.patch(session + '/models', json={'llm': llm}).status_code)
        self.assertEqual({'llm': llm, 'vlm': None}, self.client.patch(
            session + '/models', json={'vlm': None},
        ).json()['overrides'])
        self.assertEqual('platform', self.client.get(session + '/models').json()['effective']['vlm']['source'])
        self.login('other-model-user')
        self.assertEqual([], self.client.get(settings_url).json()['models'])
        self.assertEqual(404, self.client.patch(f'{settings_url}/{llm}', json=params).status_code)
        self.assertEqual(404, self.client.get(session + '/models').status_code)

    def test_personal_input_budget_summarizes_history_and_keeps_recent_requests(self):
        import httpx

        from api.bootstrap import event_loop
        from content.workspace import get_workspace_root

        self.clients.close()
        requests = []

        def provider(request):
            payload = json.loads(request.content)
            requests.append(payload)
            text = '模型回答' if payload.get('tools') else '较早对话的保留摘要'
            response = {
                'id': 'local-provider', 'created': 0, 'model': payload['model'],
                'choices': [{'index': 0, 'finish_reason': 'stop'}],
            }
            if payload.get('stream'):
                response['choices'][0]['delta'] = {'role': 'assistant', 'content': text}
                return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                      content=f'data: {json.dumps(response)}\n\ndata: [DONE]\n\n')
            response['choices'][0]['message'] = {'role': 'assistant', 'content': text}
            return httpx.Response(200, json=response)

        mock = httpx.MockTransport(provider)
        self.enterContext(patch('connection.llm.public_clients', side_effect=lambda _base: (
            httpx.Client(transport=mock), httpx.AsyncClient(transport=mock),
        )))
        settings = self.app.state.settings.model_copy(update={
            'platform_llms': ('platform',), 'platform_vlms': ('vision',),
        })
        self.app = create_app(settings=settings)
        self.client = self.clients.enter_context(TestClient(self.app, backend_options={'loop_factory': event_loop}))
        self.login('operator', 'operator-test-pass')
        profile = self.client.post('/api/models', json={
            'kind': 'llm', 'base_url': 'https://8.8.8.8/v1', 'model_id': 'compat-text', 'api_key': 'test-api-key',
            'input_budget': 12000, 'max_output_tokens': 256, 'supports_temperature': True, 'temperature': 0.4,
        })
        self.assertEqual(201, profile.status_code, profile.text)
        self.assertEqual(200, self.client.patch('/api/models/defaults', json={'llm': profile.json()['id']}).status_code)
        session = self.client.post('/api/sessions').json()['id']

        def turn(prompt):
            started = self.client.post(f'/api/sessions/{session}/runs', json={'text': prompt})
            self.assertEqual(202, started.status_code, started.text)
            events = self.client.get(f"/api/runs/{started.json()['id']}/events").text
            lifecycle = [json.loads(line[6:]) for line in events.splitlines() if line.startswith('data: ')]
            self.assertEqual('completed', lifecycle[-1]['status'], events)

        for index in range(6):
            turn(f'old-history-{index} ' + 'earlier details ' * 335)
        turn('最近一个请求')
        root = get_workspace_root({'configurable': {'workspace_id': session}})
        root.mkdir(parents=True, exist_ok=True)
        for index in range(600):
            (root / f'wide-workspace-entry-{index:04d}-{"x" * 45}.txt').touch()
        turn('当前最终任务')
        last = next(payload for payload in reversed(requests) if payload.get('tools'))
        messages = [str(message['content']) for message in last['messages']]
        self.assertTrue(any('较早对话的保留摘要' in message for message in messages))
        self.assertIn('最近一个请求', messages)
        self.assertIn('当前最终任务', messages)
        self.assertFalse(any(message.startswith('old-history-0') for message in messages))
        self.assertFalse(any('wide-workspace-entry-0000' in message for message in messages))
        self.assertEqual(256, last['max_tokens'])
        self.assertEqual(0.4, last['temperature'])
        self.assertNotIn('max_completion_tokens', last)

    def test_model_switch_applies_to_the_next_chat_and_image_run(self):
        from base64 import b64decode

        from api.bootstrap import event_loop
        from support_chat import TestPlatformChat, TestPlatformVision

        self.clients.close()
        settings = self.app.state.settings.model_copy(update={
            'platform_llms': ('chat-a', 'chat-b'), 'platform_vlms': ('vision-a', 'vision-b'),
        })
        self.enterContext(patch('connection.llm.get_llm', side_effect=lambda name: TestPlatformChat(model_id=name)))
        self.enterContext(patch('content.tools.vlm_tools.get_vlm', side_effect=lambda name: TestPlatformVision(model_id=name)))
        self.app = create_app(settings=settings)
        self.client = self.clients.enter_context(TestClient(self.app, backend_options={'loop_factory': event_loop}))
        self.login('operator', 'operator-test-pass')
        models = '/api/ops/models'
        self.assertEqual('chat-a', self.client.get(models).json()['llm'])
        self.assertEqual(422, self.client.patch(models, json={'llm': 'unknown', 'vlm': 'vision-a'}).status_code)

        session = '/api/sessions/' + self.client.post('/api/sessions').json()['id']
        png = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        upload = self.client.post(session + '/files', params={'name': '图.png'}, content=png)
        self.assertEqual(201, upload.status_code, upload.text)

        def start(text, attachments=None):
            response = self.client.post(session + '/runs', json={'text': text, 'attachment_ids': attachments or []})
            self.assertEqual(202, response.status_code, response.text)
            return response.json()['id']

        def events(run):
            return self.client.get(f'/api/runs/{run}/events').text

        self.assertIn('当前文本模型 chat-a', events(start('你好')))
        image = [upload.json()['id']]
        earlier = start('请识别图片', image)
        self.assertEqual(200, self.client.patch(models, json={'llm': 'chat-b', 'vlm': 'vision-b'}).status_code)
        self.assertIn('chat-a：视觉模型 vision-a 读取了 PNG', events(earlier))
        self.assertIn('chat-b：视觉模型 vision-b 读取了 PNG', events(start('请识别图片', image)))
        self.assertIn('当前文本模型 chat-b', events(start('你好')))

    def test_quota_is_shared_across_parallel_sessions_and_reconnecting_does_not_charge(self):
        from concurrent.futures import ThreadPoolExecutor

        self.login('operator', 'operator-test-pass')
        self.assertEqual(200, self.client.patch('/api/ops/quotas', json={'daily_limit': 1}).status_code)
        sessions = [self.client.post('/api/sessions').json()['id'] for _ in range(2)]

        def start(session):
            return self.client.post(f'/api/sessions/{session}/runs', json={'text': '请生成文件后等待'})

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(start, sessions))
        self.assertEqual([202, 429], sorted(response.status_code for response in responses))
        run = next(response.json() for response in responses if response.status_code == 202)
        duplicate = start(run['session_id'])
        self.assertEqual(202, duplicate.status_code, duplicate.text)
        self.assertFalse(duplicate.json()['created'])
        self.assertEqual(run['id'], duplicate.json()['id'])
        self.assertEqual(1, self.client.get('/api/ops/quotas').json()['accounts'][0]['used'])
        self.client.post(f"/api/runs/{run['id']}/stop")

    def test_invitation_is_single_use_and_operator_can_see_status(self):
        self.assertEqual(401, self.client.get('/api/auth/me').status_code)
        rejected = self.client.post('/api/auth/login', headers={'Origin': 'https://other.example'}, json={
            'username': 'operator', 'password': 'operator-test-pass',
        })
        self.assertEqual(403, rejected.status_code)
        self.assertEqual(400, self.register('newuser', 'invalid-token').status_code)
        self.assertEqual(401, self.client.post('/api/auth/login', json={
            'username': 'operator', 'password': 'wrong-password',
        }).status_code)
        self.login('operator', 'operator-test-pass')
        invite = self.create_invite()
        self.assertEqual(200, self.client.post('/api/auth/invitation', json={'token': invite}).status_code)
        self.assertEqual(201, self.register('alice', invite).status_code)
        self.assertEqual(400, self.register('bob', invite).status_code)
        row = self.client.get('/api/ops/invitations').json()[0]
        self.assertEqual(('registered', 'alice'), (row['status'], row['registered_username']))
        self.assertNotIn('token', row)
        pending = self.create_invite()
        self.assertEqual(409, self.register('Alice', pending).status_code)
        self.assertEqual(201, self.register('bob', pending).status_code)
        self.assertEqual('member', self.login('alice')['role'])
        self.assertEqual(403, self.client.get('/api/ops/invitations').status_code)
        self.assertEqual(403, self.client.post('/api/ops/invitations', json={}).status_code)
        cookie = self.client.cookies.get('cloudpivot_session')
        self.assertEqual(204, self.client.post('/api/auth/logout', json={}).status_code)
        self.client.cookies.set('cloudpivot_session', cookie)
        self.assertEqual(401, self.client.get('/api/auth/me').status_code)

    def test_streams_real_graph_and_retains_owned_session_and_history(self):
        self.login('operator', 'operator-test-pass')
        self.assertEqual(201, self.register('alice', self.create_invite()).status_code)
        self.assertEqual(201, self.register('bob', self.create_invite()).status_code)
        self.login('alice')
        session_id = self.client.post('/api/sessions', json={}).json()['id']
        second_id = self.client.post('/api/sessions', json={}).json()['id']
        path = f'/api/sessions/{session_id}'
        self.assertEqual('新标题', self.client.patch(path, json={'title': '新标题'}).json()['title'])
        run = self.client.post(f'{path}/runs', json={'text': '你好'}).json()
        repeated = self.client.post(f'{path}/runs', json={'text': '重复发送'}).json()
        self.assertEqual((run['id'], False), (repeated['id'], repeated['created']))
        stream = f'/api/runs/{run["id"]}/events'
        response = self.client.get(stream)
        self.assertEqual(200, response.status_code)
        self.assertIn('text/event-stream', response.headers['content-type'])
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertGreaterEqual(sum(event['type'] == 'text_delta' for event in events), 2)
        self.assertEqual('completed', events[-1]['status'])
        self.assertNotIn('hidden-reasoning', response.text)
        self.assertEqual('这是流式回复。', ''.join(event.get('text', '') for event in events))
        replayed = self.client.get(stream)
        self.assertEqual(
            [line for line in response.text.splitlines() if line.startswith(('id: ', 'data: '))],
            [line for line in replayed.text.splitlines() if line.startswith(('id: ', 'data: '))],
        )
        self.client.post('/api/auth/logout', json={})
        self.login('alice')
        messages = self.client.get(f'{path}/messages').json()
        self.assertEqual(['你好', '这是流式回复。'], [message['text'] for message in messages])
        tool_run = self.client.post(f'{path}/runs', json={'text': '请查询时间并回答'}).json()
        tool_events = self.client.get(f'/api/runs/{tool_run["id"]}/events').text
        self.assertIn('"summary": "查询时间"', tool_events)
        self.assertRegex(tool_events, r'"detail": "\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"')
        self.assertIn('"status": "completed"', tool_events)

        self.login('bob')
        self.assertEqual([], self.client.get('/api/sessions').json())
        for url in (path, f'{path}/messages', f'{path}/runs', stream):
            self.assertEqual(404, self.client.get(url).status_code)
        self.assertEqual(404, self.client.post(f'{path}/runs', json={'text': '越权'}).status_code)
        self.assertEqual(404, self.client.patch(path, json={'title': '越权'}).status_code)
        self.assertEqual(404, self.client.post(f'{path}/archive', json={}).status_code)

        self.login('alice')
        self.assertEqual(200, self.client.post(f'{path}/archive', json={}).status_code)
        self.assertEqual([second_id], [row['id'] for row in self.client.get('/api/sessions').json()])
        self.assertEqual(409, self.client.post(f'{path}/runs', json={'text': '归档后发送'}).status_code)

    def test_stop_keeps_partial_reply_and_replays_from_cursor(self):
        import time

        self.login('operator', 'operator-test-pass')
        session = self.client.post('/api/sessions', json={}).json()['id']
        path = f'/api/sessions/{session}'
        run = self.client.post(f'{path}/runs', json={'text': '请生成文件后等待'}).json()
        run_path = f'/api/runs/{run["id"]}'
        time.sleep(2)
        stopped = self.client.post(f'{run_path}/stop', json={})
        self.assertEqual(200, stopped.status_code, stopped.text)
        self.assertEqual('stopped', stopped.json()['status'])
        self.assertEqual(run['started_at'], stopped.json()['started_at'])
        self.assertEqual(['请生成文件后等待', '这是'], [
            message['text'] for message in self.client.get(f'{path}/messages').json()
        ])
        response = self.client.get(f'{run_path}/events')
        frames = [frame for frame in response.text.split('\n\n') if frame.startswith('id: ')]
        events = [json.loads(frame.split('\ndata: ', 1)[1]) for frame in frames]
        self.assertIn('progress', {event['type'] for event in events})
        self.assertIn('tool_result', {event['type'] for event in events})
        artifact = next(event for event in events if event['type'] == 'artifact')
        download = f'{run_path}/artifacts/{artifact["artifact_id"]}'
        self.assertEqual('已生成的内容', self.client.get(download).text)
        for secret in ('hidden-reasoning', '已生成的内容', '/report.txt'):
            self.assertNotIn(secret, response.text)
        self.assertEqual('stopped', events[-1]['status'])
        cursor = frames[-2].splitlines()[0][4:]
        replay = self.client.get(f'{run_path}/events?after={cursor}').text
        self.assertEqual([frames[-1]], [frame for frame in replay.split('\n\n') if frame.startswith('id: ')])
        self.assertEqual('stopped', self.client.post(f'{run_path}/stop', json={}).json()['status'])
        self.assertEqual(201, self.register('other', self.create_invite()).status_code)
        self.login('other')
        self.assertEqual(404, self.client.get(download).status_code)
        self.assertEqual(404, self.client.post(f'{run_path}/stop', json={}).status_code)
        self.assertEqual(404, self.client.post(f'{run_path}/retry', json={}).status_code)

    def test_stop_waits_for_an_inflight_writer_before_releasing_the_session(self):
        import time
        from concurrent.futures import ThreadPoolExecutor

        self.login('operator', 'operator-test-pass')
        session = self.client.post('/api/sessions', json={}).json()['id']
        path = f'/api/sessions/{session}/runs'
        run = self.client.post(path, json={'text': '请生成慢速报告'}).json()
        self.assertTrue(self.writer_started.wait(timeout=5))
        with ThreadPoolExecutor() as executor:
            stop = executor.submit(self.client.post, f'/api/runs/{run["id"]}/stop', json={})
            try:
                time.sleep(0.25)
                concurrent = self.client.post(path, json={'text': '并发发送'}).json()
                self.assertFalse(stop.done())
                self.assertEqual((run['id'], False), (concurrent['id'], concurrent['created']))
            finally:
                self.finish_writing.set()
            self.assertEqual('stopped', stop.result(timeout=5).json()['status'])
        [stopped] = self.client.get(path).json()
        [artifact] = stopped['artifacts']
        self.assertEqual('slow-report.txt', artifact['name'])
        self.assertEqual('慢速报告', self.client.get(
            f'/api/runs/{run["id"]}/artifacts/{artifact["artifact_id"]}'
        ).text)

    def test_failure_exposes_only_public_error_and_retry_preserves_history(self):
        self.login('operator', 'operator-test-pass')
        session = self.client.post('/api/sessions', json={}).json()['id']
        path = f'/api/sessions/{session}'
        run = self.client.post(f'{path}/runs', json={'text': '请让运行失败'}).json()
        initial = f'/api/runs/{run["id"]}'
        events = self.client.get(f'{initial}/events').text
        self.assertNotIn('secret-token', events)
        self.assertNotIn('RuntimeError', events)
        self.assertIn('run_failed', events)
        retry = self.client.post(f'{initial}/retry', json={}).json()
        self.assertTrue(retry['created'])
        self.assertNotEqual(run['id'], retry['id'])
        self.assertEqual(session, retry['session_id'])
        self.client.get(f'/api/runs/{retry["id"]}/events')
        history = self.client.get(f'{path}/runs').json()
        self.assertEqual([run['id'], retry['id']], [item['id'] for item in history])
        self.assertEqual(['failed', 'failed'], [item['status'] for item in history])
        self.assertEqual(['请让运行失败', '这是', '请让运行失败', '这是'], [
            message['text'] for message in self.client.get(f'{path}/messages').json()
        ])

    def test_restart_interrupts_live_run_and_keeps_saved_artifact(self):
        import time

        from api.bootstrap import event_loop

        self.login('operator', 'operator-test-pass')
        session = self.client.post('/api/sessions', json={}).json()['id']
        path = f'/api/sessions/{session}'
        run = self.client.post(f'{path}/runs', json={'text': '请生成文件后等待'}).json()
        cookie = self.client.cookies.get('cloudpivot_session')
        time.sleep(2)
        self.clients.close()
        self.client = self.clients.enter_context(TestClient(self.app, backend_options={'loop_factory': event_loop}))
        self.client.cookies.set('cloudpivot_session', cookie)
        row = self.client.get(f'{path}/runs').json()[0]
        self.assertEqual(run['id'], row['id'])
        self.assertEqual('interrupted', row['status'])
        self.assertEqual('run_interrupted', row['error']['code'])
        self.assertEqual('report.txt', row['artifacts'][0]['name'])
        messages = self.client.get(f'{path}/messages').json()
        self.assertEqual(['请生成文件后等待', '这是'], [message['text'] for message in messages])
