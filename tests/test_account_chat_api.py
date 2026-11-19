import json
import os
import unittest
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

        from psycopg import connect, sql
        from psycopg.conninfo import make_conninfo

        from api.bootstrap import event_loop
        from api.manage import create_operator
        from core import config
        from core.security import hash_password
        from content.all_agent import AllAgent
        from support_chat import TestChatModel

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
        self.client = self.enterContext(TestClient(
            create_app(settings=settings, agent_factory=lambda saver: AllAgent(
                model=TestChatModel(), checkpointer=saver,
            ).agent),
            backend_options={'loop_factory': event_loop},
        ))

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

        self.login('bob')
        self.assertEqual([], self.client.get('/api/sessions').json())
        for url in (path, f'{path}/messages', stream):
            self.assertEqual(404, self.client.get(url).status_code)
        self.assertEqual(404, self.client.post(f'{path}/runs', json={'text': '越权'}).status_code)
        self.assertEqual(404, self.client.patch(path, json={'title': '越权'}).status_code)
        self.assertEqual(404, self.client.post(f'{path}/archive', json={}).status_code)

        self.login('alice')
        self.assertEqual(200, self.client.post(f'{path}/archive', json={}).status_code)
        self.assertEqual([second_id], [row['id'] for row in self.client.get('/api/sessions').json()])
        self.assertEqual(409, self.client.post(f'{path}/runs', json={'text': '归档后发送'}).status_code)
