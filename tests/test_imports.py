from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from fetcher import FetchError
from library import LibraryError, WallpaperLibrary, RETENTION_SECONDS


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000.0
        self.library = WallpaperLibrary(self.root / 'data', self.root / 'cache',
                                        clock=lambda: self.now, removal_notice=lambda _: None)
        self.library.select_source('local')
        self.desktop = patch.object(self.library, '_protected_paths', return_value=set())
        self.desktop.start()
        self.addCleanup(self.desktop.stop)

    def image(self, name='download.png', size=(1920, 1080), color='blue'):
        path = self.root / name
        Image.new('RGB', size, color).save(path)
        return path

    def test_import_copies_and_expires_only_managed_file(self):
        path = self.image()
        original = path.read_bytes()
        entry = self.library.import_local(path)
        self.assertNotEqual(entry.wallpaper.path, path)
        self.assertEqual(entry.wallpaper.path.read_bytes(), original)
        self.assertEqual(self.library.fill().queue, (entry,))
        self.now += RETENTION_SECONDS
        self.assertEqual(self.library.cleanup(), 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(entry.wallpaper.path.exists())

    def test_invalid_portrait_small_and_duplicate_imports(self):
        for size in ((1080, 1920), (1920, 1920), (1600, 900)):
            with self.assertRaisesRegex(LibraryError, 'landscape'):
                self.library.import_local(self.image(size=size))
        corrupt = self.root / 'corrupt.jpg'
        corrupt.write_bytes(b'not an image')
        with self.assertRaises(FetchError):
            self.library.import_local(corrupt)
        self.assertEqual(list(self.library.images_dir.iterdir()), [])
        path = self.image()
        self.library.import_local(path)
        with self.assertRaisesRegex(LibraryError, 'already'):
            self.library.import_local(path)
        self.assertEqual(len(list(self.library.images_dir.iterdir())), 1)

    def test_source_isolation_and_manual_refill(self):
        entry = self.library.import_local(self.image())
        for source in ('commons', 'bing', 'firefox'):
            self.library.select_source(source)
            self.assertFalse(self.library.snapshot().queue)
        self.library.select_source('local')
        with patch('library.Fetcher', side_effect=AssertionError('No network')):
            self.assertEqual(self.library.fill().queue, (entry,))
        self.library.apply(entry.id, setter=Mock())
        self.assertFalse(self.library.fill().queue)
        self.assertTrue(entry.wallpaper.path.exists())

    def test_imported_alternative_requires_explicit_choice(self):
        original = self.library.import_local(self.image())
        self.now += 30
        alternative = self.library.import_local(self.image('new.png', color='green'), original=original.id)
        self.assertEqual(self.library.snapshot().queue, (original,))
        self.library.choose_replacement(original.id, alternative.id, keep_new=True)
        self.assertEqual(self.library.snapshot().queue, (alternative,))
        self.assertTrue(original.wallpaper.path.exists())
        self.assertEqual(len(self.library.saved_images()), 2)

    def test_full_queue_rejects_extra_import_but_allows_comparison(self):
        for i in range(7):
            self.library.import_local(self.image(f'{i}.png', color=(i * 30, 100, 50)))
        path = self.image('extra.png', color='red')
        with self.assertRaisesRegex(LibraryError, 'full'):
            self.library.import_local(path)
        original = self.library.snapshot().queue[0]
        self.library.import_local(path, original=original.id)
        self.assertEqual(len(self.library.snapshot().queue), 7)

    def test_failed_storage_cleans_copy_without_touching_original(self):
        path = self.image()
        with patch.object(self.library, '_store', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.library.import_local(path)
        self.assertTrue(path.exists())
        self.assertFalse(list(self.library.images_dir.iterdir()))

    def test_previous_unsplash_imports_remain_available_as_local(self):
        entry = self.library.import_local(self.image())
        with self.library._db() as db:
            row = db.execute('SELECT wallpaper FROM images WHERE id=?', (entry.id,)).fetchone()
            value = json.loads(row[0])
            value.update(source='unsplash', theme='Unsplash',
                         photographer='Manually imported from Unsplash',
                         description_url='https://unsplash.com/wallpapers')
            db.execute('UPDATE images SET wallpaper=? WHERE id=?', (json.dumps(value), entry.id))
            db.execute("INSERT OR REPLACE INTO settings VALUES ('source', 'unsplash')")
        reopened = WallpaperLibrary(self.root / 'data', self.root / 'cache', clock=lambda: self.now)
        self.assertEqual(reopened.source, 'local')
        migrated = reopened.snapshot().queue[0]
        self.assertEqual(migrated.id, entry.id)
        self.assertEqual(migrated.saved_at, entry.saved_at)
        self.assertEqual(migrated.expires_at, entry.expires_at)
        self.assertEqual(migrated.wallpaper.path, entry.wallpaper.path)
        self.assertEqual(migrated.wallpaper.source, 'local')
        self.assertEqual(migrated.wallpaper.description_url, '')
        self.assertEqual(migrated.wallpaper.photographer, 'Imported from your computer')
        with self.assertRaisesRegex(LibraryError, 'already'):
            reopened.import_local(self.root / 'download.png')
