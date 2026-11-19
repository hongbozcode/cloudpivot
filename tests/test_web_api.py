import unittest

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import WebSettings


class HealthEndpointTest(unittest.TestCase):
    def test_health_returns_ok(self):
        with TestClient(create_app(settings=WebSettings())) as client:
            response = client.get('/api/health')

        self.assertEqual(200, response.status_code)
        self.assertEqual({'status': 'ok'}, response.json())
