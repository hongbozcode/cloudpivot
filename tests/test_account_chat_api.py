import json
import os
import unittest
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api.settings import WebSettings
from main import create_app


class AccountChatEndpointTest(unittest.TestCase):
    def test_account_features_require_database_configuration(self):
        with TestClient(create_app(settings=WebSettings())) as client:
            response = client.get('/api/auth/me')

        self.assertEqual(503, response.status_code)

    def test_redis_configuration_never_selects_database_zero(self):
        with patch.dict(os.environ, {
            'CLOUDPIVOT_DATABASE_URL': '',
            'CLOUDPIVOT_REDIS_URL': '127.0.0.1:6379',
            'CLOUDPIVOT_REDIS_PASSWORD': '',
        }):
            self.assertEqual('redis://127.0.0.1:6379/1', WebSettings.from_env().redis_url)
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
        from core import config
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
