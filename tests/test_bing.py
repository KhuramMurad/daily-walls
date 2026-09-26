from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bing import BingFetcher, jpeg_dimensions
from fetcher import FetchError
from library import identities
import test_library
from test_library import FakeFetcher


class BingTests(unittest.TestCase):
    def test_regional_and_size_variants_share_identity(self):
        a = identities('English', 'https://www.bing.com/th?id=OHR.Peak_EN-US123_UHD.jpg')
        b = identities('German', 'https://www.bing.com/th?id=OHR.Peak_DE-DE456_1920x1080.jpg')
        c = identities('English', 'https://www.bing.com/th?id=OHR.Lake_EN-US123_UHD.jpg')
        self.assertEqual(a, b)
        self.assertFalse(a & c)

    def test_archive_skips_restricted_and_invalid_urls(self):
        fetcher = BingFetcher('tests/1.0 (test@example.com)')
        allowed = dict(wp=True, urlbase='/th?id=OHR.Peak_EN-US123', title='Peak')
        with patch.object(fetcher, '_json', return_value={'images': [allowed,
                dict(allowed, wp=False, urlbase='/th?id=OHR.Other_EN-US1'),
                dict(allowed, urlbase='https://evil.example/image')]}):
            items = list(fetcher.wallpaper_candidates())
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['url'].endswith('_UHD.jpg'))

    def test_jpeg_dimensions_and_invalid_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'image.jpg'
            path.write_bytes(b'\xff\xd8\xff\xc0\x00\x11\x08\x08\x70\x0f\x00')
            self.assertEqual(jpeg_dimensions(path), (3840, 2160))
            path.write_bytes(b'not a jpeg')
            with self.assertRaises(FetchError):
                jpeg_dimensions(path)


class BingLibraryTests(unittest.TestCase):
    setUp = test_library.LibraryTests.setUp
    def test_sources_keep_separate_queues(self):
        commons = self.library.fill(fetcher=self.fetcher)
        self.library.select_source('bing')
        fake = FakeFetcher(self.library.images_dir)
        original = fake.download_wallpaper
        def download(info):
            return replace(original(dict(info, index=info['index'] + 100)), source='bing', quality='Bing daily')
        fake.download_wallpaper = download
        fake.wallpaper_candidates = lambda: iter([
            {'title': f'Bing {i}', 'url': f'https://www.bing.com/th?id=OHR.Scene{i}_EN-US1_UHD.jpg',
             'index': i, 'theme': str(i)} for i in range(8)])
        bing = self.library.fill(fetcher=fake)
        self.assertEqual(len(bing.queue), 7)
        self.assertTrue(all(e.wallpaper.source == 'bing' for e in bing.queue))
        self.library.select_source('commons')
        self.assertEqual(self.library.fill(fetcher=self.fetcher).queue, commons.queue)
        self.library.select_source('bing')
        self.assertEqual(self.library.snapshot().queue, bing.queue)
        current = bing.queue[0]
        self.library.apply(current.id, setter=Mock())
        self.now += 10 * 86400
        self.library.cleanup()
        self.assertTrue(current.wallpaper.path.exists())
        self.assertTrue(self.library._known(identities(current.wallpaper.title, current.wallpaper.image_url)))
