"""Tests for Firefox Picture of the Day wallpaper source.
"""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fetcher import FetchError
from firefox import FirefoxFetcher, firefox_identity, image_dimensions, png_dimensions, webp_dimensions
from library import identities
import test_library
from test_library import FakeFetcher


class FirefoxTests(unittest.TestCase):
    def test_identity_extracts_date(self):
        url1 = "https://prod-images.merino.prod.webservices.mozgcp.net/wikimedia_potd/2026-09-25/hi_res.webp"
        url2 = "https://prod-images.merino.prod.webservices.mozgcp.net/wikimedia_potd/2026-09-25/thumbnail.jpeg"
        url3 = "https://prod-images.merino.prod.webservices.mozgcp.net/wikimedia_potd/2026-09-24/hi_res.webp"
        self.assertEqual(firefox_identity(url1), "2026-09-25")
        self.assertEqual(firefox_identity(url2), "2026-09-25")
        self.assertEqual(identities("Today", url1), identities("Today thumb", url2))
        self.assertNotEqual(identities("Today", url1), identities("Yesterday", url3))

    def test_webp_vp8x_dimensions(self):
        # RIFF .... WEBP VP8X (canvas width and height at 24..30)
        data = bytearray(30)
        data[0:4] = b"RIFF"
        data[4:8] = (22).to_bytes(4, "little")
        data[8:12] = b"WEBP"
        data[12:16] = b"VP8X"
        # width = 3840 (stored as 3839 at 24:27)
        data[24:27] = (3839).to_bytes(3, "little")
        # height = 2160 (stored as 2159 at 27:30)
        data[27:30] = (2159).to_bytes(3, "little")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.webp"
            path.write_bytes(bytes(data))
            self.assertEqual(webp_dimensions(path), (3840, 2160))
            self.assertEqual(image_dimensions(path), (3840, 2160))

    def test_webp_vp8_dimensions(self):
        # RIFF .... WEBP VP8 (width and height at 26..30)
        data = bytearray(30)
        data[0:4] = b"RIFF"
        data[4:8] = (22).to_bytes(4, "little")
        data[8:12] = b"WEBP"
        data[12:16] = b"VP8 "
        data[26:28] = (1920).to_bytes(2, "little")
        data[28:30] = (1080).to_bytes(2, "little")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "vp8.webp"
            path.write_bytes(bytes(data))
            self.assertEqual(webp_dimensions(path), (1920, 1080))

    def test_invalid_and_corrupt_images(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "corrupt.webp"
            path.write_bytes(b"not a webp")
            with self.assertRaises(FetchError):
                webp_dimensions(path)
            with self.assertRaises(FetchError):
                image_dimensions(path)

    def test_wallpaper_candidates_from_merino(self):
        fetcher = FirefoxFetcher("tests/1.0 (test@example.com)")
        sample_merino = {
            "title": "Wikimedia Commons Picture of the Day",
            "published_date": "2026-09-25",
            "description": "Scenic Coastal Bay",
            "author": "Alice",
            "file_page": "https://commons.wikimedia.org/wiki/File:Scenic_Bay.jpg",
            "license_label": "CC BY-SA 4.0",
            "license_link": "https://creativecommons.org/licenses/by-sa/4.0",
            "high_res_image_url": "https://prod-images.merino.prod.webservices.mozgcp.net/wikimedia_potd/2026-09-25/hi_res.webp",
        }
        with patch.object(fetcher, "_json", return_value=sample_merino), \
             patch.object(fetcher, "_potd_title", return_value="File:Historic_Tower.jpg"), \
             patch.object(fetcher, "_metadata", return_value={
                 "title": "File:Historic_Tower.jpg",
                 "extmetadata": {"Artist": {"value": "Bob"}, "LicenseShortName": {"value": "CC BY 3.0"}},
                 "descriptionurl": "https://commons.wikimedia.org/wiki/File:Historic_Tower.jpg",
             }):
            candidates = list(fetcher.wallpaper_candidates())
            self.assertGreaterEqual(len(candidates), 7)
            today = candidates[0]
            self.assertEqual(today["title"], "Scenic Coastal Bay")
            self.assertEqual(today["photographer"], "Alice")
            self.assertEqual(today["license"], "CC BY-SA 4.0")
            self.assertTrue(today["url"].endswith("hi_res.webp"))


class FirefoxLibraryTests(unittest.TestCase):
    setUp = test_library.LibraryTests.setUp

    def test_firefox_source_separate_queue_and_retention(self):
        commons = self.library.fill(fetcher=self.fetcher)
        self.library.select_source("firefox")
        fake = FakeFetcher(self.library.images_dir)
        original = fake.download_wallpaper

        def download(info):
            return replace(original(dict(info, index=info["index"] + 200)),
                           source="firefox", quality="Firefox daily")

        fake.download_wallpaper = download
        fake.wallpaper_candidates = lambda: iter([
            {
                "title": f"Firefox POTD {i}",
                "url": f"https://prod-images.merino.prod.webservices.mozgcp.net/wikimedia_potd/2026-09-{10+i:02d}/hi_res.webp",
                "index": i,
                "theme": f"2026-09-{10+i:02d}",
            }
            for i in range(8)
        ])

        firefox_snap = self.library.fill(fetcher=fake)
        self.assertEqual(len(firefox_snap.queue), 7)
        self.assertTrue(all(e.wallpaper.source == "firefox" for e in firefox_snap.queue))

        # Check that switching back to Commons retains original queue
        self.library.select_source("commons")
        self.assertEqual(self.library.snapshot().queue, commons.queue)

        # Check switching back to Firefox retains Firefox queue
        self.library.select_source("firefox")
        self.assertEqual(self.library.snapshot().queue, firefox_snap.queue)

        # Apply an image from Firefox queue
        current = firefox_snap.queue[0]
        self.library.apply(current.id, setter=Mock())

        # Verify retention cleanup protects current wallpaper
        self.now += 10 * 86400
        self.library.cleanup()
        self.assertTrue(current.wallpaper.path.exists())
        self.assertTrue(self.library._known(identities(current.wallpaper.title, current.wallpaper.image_url)))

class FirefoxDownloadTests(unittest.TestCase):
    def test_trusted_hosts_and_redirects(self):
        from firefox import validate_image_url, _ImageRedirectHandler, MERINO_CDN_BASE
        validate_image_url(MERINO_CDN_BASE + '/2026-09-25/hi_res.webp')
        validate_image_url('https://upload.wikimedia.org/image.png')
        for url in ('https://notmozilla.com/image.webp',
                    'https://mozilla.com.evil.example/image.webp',
                    'http://mozilla.com/image.webp',
                    'https://user@mozilla.com/image.webp'):
            with self.subTest(url=url), self.assertRaises(FetchError):
                validate_image_url(url)
            with self.subTest(redirect=url), self.assertRaises(FetchError):
                _ImageRedirectHandler().redirect_request(None, None, 302, '', {}, url)

    def test_download_validates_pixels_and_cleans_failed_downloads(self):
        from email.message import Message
        from io import BytesIO
        from PIL import Image
        from firefox import MERINO_CDN_BASE
        url = MERINO_CDN_BASE + '/2026-09-25/hi_res.webp'
        encoded = BytesIO()
        Image.new('RGB', (1920, 1080), 'blue').save(encoded, format='WEBP')
        portrait = BytesIO()
        Image.new('RGB', (1080, 1920), 'blue').save(portrait, format='WEBP')
        header = bytearray(30)
        header[:4] = b'RIFF'
        header[4:8] = (22).to_bytes(4, 'little')
        header[8:16] = b'WEBPVP8X'
        header[24:27] = (1919).to_bytes(3, 'little')
        header[27:30] = (1079).to_bytes(3, 'little')
        for data, valid in ((encoded.getvalue(), True), (bytes(header), False),
                            (encoded.getvalue()[:40], False), (portrait.getvalue(), False)):
            with self.subTest(valid=valid, size=len(data)), tempfile.TemporaryDirectory() as folder:
                response = BytesIO(data)
                response.headers = Message()
                response.headers['Content-Type'] = 'image/webp'
                response.headers['Content-Length'] = str(len(data))
                response.geturl = lambda: url
                fetcher = FirefoxFetcher('tests/1.0', Path(folder))
                with patch('firefox.build_opener') as opener:
                    opener.return_value.open.return_value = response
                    if valid:
                        wallpaper = fetcher.download_wallpaper({'url': url, 'title': 'Test'})
                        self.assertEqual((wallpaper.width, wallpaper.height), (1920, 1080))
                        self.assertTrue(wallpaper.path.is_file())
                    else:
                        with self.assertRaises(FetchError):
                            fetcher.download_wallpaper({'url': url, 'title': 'Test'})
                        self.assertEqual(list(Path(folder).iterdir()), [])
