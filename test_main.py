import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError
import main


class WeatherTests(unittest.TestCase):
    def test_auth_requires_both_credentials(self):
        for env in [{"MONGO_ROOT_USERNAME": "admin"}, {"MONGO_ROOT_PASSWORD": "test"}]:
            with self.assertRaisesRegex(ValueError, "Completa MONGO_ROOT_USERNAME"):
                main.mongo_auth_options(env)
        self.assertEqual(main.mongo_auth_options({}), {})

    def test_auth_preserves_special_characters_and_uses_admin(self):
        auth = main.mongo_auth_options({
            "MONGO_ROOT_USERNAME": "test-user", "MONGO_ROOT_PASSWORD": "p@ss:/?#%",
        })
        self.assertEqual(auth["password"], "p@ss:/?#%")
        self.assertEqual(auth["authSource"], "admin")

    @classmethod
    def tearDownClass(cls):
        main.client.close()

    def setUp(self):
        self.api = TestClient(main.app)

    def tearDown(self):
        self.api.close()

    def test_database_unavailable_returns_503(self):
        with patch.object(main, 'client') as mongo:
            mongo.admin.command.side_effect = ServerSelectionTimeoutError('offline')
            response = self.api.get('/health')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json()['mongo_connected'])
        with patch.object(main, 'collection') as collection:
            collection.find_one.side_effect = ServerSelectionTimeoutError('offline')
            self.assertEqual(self.api.get('/api/weather/latest?city_id=1').status_code, 503)

    def test_validates_ids_and_limits(self):
        for url in ['/api/weather?limit=0', '/api/weather?skip=-1',
                    '/api/weather/latest?city_id=0', '/api/weather/city/-1']:
            self.assertEqual(self.api.get(url).status_code, 422, url)

    def test_empty_collection_and_missing_city(self):
        with patch.object(main, 'collection') as collection:
            collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []
            self.assertEqual(self.api.get('/api/weather').json(), {'count': 0, 'data': []})
            collection.find_one.return_value = None
            self.assertEqual(self.api.get('/api/weather/latest?city_id=1').status_code, 404)

    def test_summary_consumes_ms2_and_weather(self):
        reading = {'city_id': 1, 'wind_speed_kmh': 10}
        with patch.object(main, 'urlopen', return_value=io.BytesIO(b'{"id":1,"name":"Lima"}')) as upstream:
            with patch.object(main, 'collection') as collection:
                collection.find_one.return_value = reading
                response = self.api.get('/api/weather/city/1/summary')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'city': {'id': 1, 'name': 'Lima'}, 'weather': reading})
                self.assertTrue(upstream.call_args.args[0].endswith('/api/cities/1'))

    def test_summary_handles_missing_or_unavailable_ms2(self):
        for error, expected in [(HTTPError('url', 404, 'missing', {}, None), 404),
                                (TimeoutError(), 502), (ValueError('invalid JSON'), 502)]:
            with patch.object(main, 'urlopen', side_effect=error):
                self.assertEqual(self.api.get('/api/weather/city/1/summary').status_code, expected)


if __name__ == '__main__':
    unittest.main()
