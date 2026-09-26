"""Regression tests for the live network failure and Commons gateway fallback."""
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from fetcher import API_URL, GATEWAY_URL, SHARED_API_URL, Fetcher, FetchError, _artist


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.fetcher = Fetcher('wiki-wallpaper-tests/1.0')

    def test_connection_failure_falls_back_and_stays_on_gateway(self):
        with patch.object(self.fetcher, '_json', side_effect=URLError('connection reset')), \
             patch.object(self.fetcher, '_gateway_api', return_value={'query': {}}) as fallback:
            self.fetcher._api(action='query')
            self.assertTrue(self.fetcher._use_gateway)
            self.fetcher._api(action='query')
            self.assertEqual(fallback.call_count, 2)

    def test_http_rate_limit_is_not_rerouted(self):
        error = HTTPError(API_URL, 429, 'Too Many Requests', {}, None)
        with patch.object(self.fetcher, '_json', side_effect=error), \
             patch.object(self.fetcher, '_gateway_api') as fallback:
            with self.assertRaises(FetchError):
                self.fetcher._api(action='query')
            fallback.assert_not_called()

    def test_potd_filename_from_template_source(self):
        self.fetcher._use_gateway = True
        source = '{{Potd filename|1= Lake.jpg\n<!-- COMMENT -->|2=2026|3=09|4=25}}'
        with patch.object(self.fetcher, '_json', return_value={'source': source}) as request:
            self.assertEqual(self.fetcher._potd_title('2026-09-25'), 'File:Lake.jpg')
            self.assertTrue(request.call_args.args[0].startswith(GATEWAY_URL))

    def test_shared_repository_excludes_wikipedia_local_uploads(self):
        pages = [{'title': 'File:Shared.jpg', 'imagerepository': 'shared'},
                 {'title': 'File:Local.jpg', 'imagerepository': 'local'}]
        with patch.object(self.fetcher, '_json', return_value={'query': {'pages': pages}}) as request:
            result = self.fetcher._gateway_api({'prop': 'imageinfo'})
            self.assertEqual(result['query']['pages'], pages[:1])
            self.assertTrue(request.call_args.args[0].startswith(SHARED_API_URL))

    def test_author_name_excludes_surrounding_notices(self):
        artist = 'This Photo was taken by <b><a title="User:Photographer">Alice Example</a></b>. Please credit me.'
        self.assertEqual(_artist(artist), 'Alice Example')
        self.assertEqual(_artist('Alice &amp; Bob'), 'Alice & Bob')


if __name__ == '__main__':
    unittest.main()
