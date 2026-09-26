from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fetcher import FetchError, Fetcher, WALLPAPER_CATEGORIES, WALLPAPER_THEMES, Wallpaper, premium_candidate, matches_theme
from library import LibraryError, RETENTION_SECONDS, WallpaperLibrary, identities


class FakeFetcher:
    def __init__(self, folder, count=30, fail_after=None):
        self.folder = folder
        self.count = count
        self.fail_after = fail_after
        self.downloads = []

    def wallpaper_candidates(self, themes=None):
        for index in range(self.count):
            if self.fail_after is not None and index == self.fail_after:
                raise FetchError('network unavailable')
            theme = list(WALLPAPER_THEMES)[index % 7]
            if themes and theme not in themes:
                continue
            yield {'title': f'File:Image {index}.jpg', 'url': f'https://upload.wikimedia.org/{index}.jpg',
                   'sha1': f'content-{index}', 'index': index}

    def download_wallpaper(self, info):
        index = info['index']
        self.downloads.append(index)
        path = self.folder / (sha256(str(index).encode()).hexdigest() + '.jpg')
        path.write_bytes(f'image-bytes-{index}'.encode())
        return Wallpaper(path, info['title'].removeprefix('File:'), 'Author', 'CC BY-SA', '', '',
                         info['url'], 3840, 2160, 'wallpaper', theme=info.get('theme', ''), quality='featured')


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000.0
        self.notices = []
        self.library = WallpaperLibrary(self.root / 'data', self.root / 'cache',
                                        clock=lambda: self.now, removal_notice=self.notices.append)
        self.fetcher = FakeFetcher(self.library.images_dir)
        # These tests never query or change the real desktop.
        self.desktop = patch('subprocess.run', return_value=Mock(returncode=1, stdout=''))
        self.desktop.start()
        self.addCleanup(self.desktop.stop)

    def test_seven_persist_across_restart_and_refill_after_apply(self):
        first = self.library.fill(fetcher=self.fetcher)
        self.assertEqual(len(first.queue), 7)
        self.assertEqual(len({entry.id for entry in first.queue}), 7)
        again = WallpaperLibrary(self.root / 'data', self.root / 'cache', clock=lambda: self.now)
        self.assertEqual(again.snapshot().queue, first.queue)
        chosen = first.queue[0]
        self.library.apply(chosen.id, setter=Mock(return_value='gnome'))
        refreshed = self.library.fill(fetcher=self.fetcher)
        self.assertEqual(len(refreshed.queue), 7)
        self.assertNotIn(chosen.id, {entry.id for entry in refreshed.queue})
        self.assertTrue(chosen.wallpaper.path.exists())
        self.assertEqual(refreshed.retained_count, 8)
        with self.assertRaises(LibraryError):
            self.library.apply(chosen.id, setter=Mock())

    def test_ten_day_boundary_notice_current_protection_and_history(self):
        first = self.library.fill(fetcher=self.fetcher)
        current = first.queue[0]
        self.library.apply(current.id, setter=Mock(return_value='gnome'))
        self.now += RETENTION_SECONDS - 1
        self.assertEqual(self.library.cleanup(), 0)
        self.now += 1
        self.library.removal_notice = lambda count: self.assertEqual(
            sum(entry.wallpaper.path.exists() for entry in first.queue), 7)
        self.assertEqual(self.library.cleanup(), 6)
        self.assertTrue(current.wallpaper.path.exists())
        self.assertIn('permanently removed', self.library.snapshot().notice)
        new = self.library.fill(fetcher=self.fetcher)
        self.assertEqual(len(new.queue), 7)
        self.assertFalse({entry.id for entry in first.queue} & {entry.id for entry in new.queue})
        self.library.removal_notice = self.notices.append
        self.library.apply(new.queue[0].id, setter=Mock(return_value='gnome'))
        self.assertFalse(current.wallpaper.path.exists())
        self.assertEqual(self.notices, [1])

    def test_failed_apply_does_not_consume_queue(self):
        before = self.library.fill(fetcher=self.fetcher)
        with self.assertRaises(RuntimeError):
            self.library.apply(before.queue[0].id, setter=Mock(side_effect=RuntimeError('desktop unavailable')))
        self.assertEqual(self.library.snapshot().queue, before.queue)

    def test_partial_network_failure_preserves_downloads_and_can_resume(self):
        partial = self.library.fill(fetcher=FakeFetcher(self.library.images_dir, fail_after=3))
        self.assertEqual(len(partial.queue), 3)
        self.assertIn('network unavailable', partial.warning)
        full = self.library.fill(fetcher=self.fetcher)
        self.assertEqual(len(full.queue), 7)
        self.assertEqual(self.fetcher.downloads, [3, 4, 5, 6])

    def test_url_tracking_parameters_and_renamed_identical_files_are_duplicates(self):
        a = identities('File:A_B.jpg', 'https://upload.wikimedia.org/A.jpg?utm_source=a', 'digest')
        b = identities('File:A B.jpg', 'https://upload.wikimedia.org/A.jpg?utm_source=b', 'digest')
        self.assertEqual(a, b)
        self.library._remember({'sha1:content-0'})
        full = self.library.fill(fetcher=self.fetcher)
        self.assertEqual(len(full.queue), 7)
        self.assertNotIn(0, self.fetcher.downloads)

    def test_binary_duplicate_under_different_title_is_rejected(self):
        original = self.fetcher.download_wallpaper
        def download(info):
            wallpaper = original(info)
            if info['index'] == 1:
                wallpaper.path.write_bytes(b'image-bytes-0')
            return wallpaper
        self.fetcher.download_wallpaper = download
        full = self.library.fill(fetcher=self.fetcher)
        self.assertEqual(len(full.queue), 7)
        self.assertEqual(len({_entry.wallpaper.path.read_bytes() for _entry in full.queue}), 7)
        self.assertEqual(set(self.fetcher.downloads), {0, 1, 2, 3, 4, 5, 6, 8})

    def test_cleanup_never_deletes_outside_paths_or_symlinks(self):
        outside = self.root / 'important.jpg'
        outside.write_bytes(b'important')
        symlink = self.library.images_dir / ('a' * 64 + '.jpg')
        symlink.symlink_to(outside)
        for index, path in enumerate([outside, symlink]):
            wallpaper = Wallpaper(path, 'Unsafe', '', '', '', '', '', 1920, 1080, 'test')
            self.library._store(wallpaper, str(index), {str(index)}, self.now-RETENTION_SECONDS-1)
        self.assertEqual(self.library.cleanup(), 0)
        self.assertEqual(outside.read_bytes(), b'important')
        self.assertTrue(symlink.is_symlink())

    def test_concurrent_writer_is_rejected(self):
        with self.library._lock():
            with self.assertRaises(LibraryError):
                self.library.fill(fetcher=self.fetcher)

    def test_queue_has_one_per_theme_and_replacement_preserves_history(self):
        first = self.library.fill(fetcher=self.fetcher)
        self.assertEqual({entry.wallpaper.theme for entry in first.queue}, set(WALLPAPER_THEMES))
        chosen = first.queue[0]
        self.library.skip(chosen.id)
        self.assertTrue(chosen.wallpaper.path.exists())
        self.assertIsNone(chosen.applied_at)
        after = self.library.fill(fetcher=self.fetcher)
        self.assertEqual({entry.wallpaper.theme for entry in after.queue}, set(WALLPAPER_THEMES))
        self.assertNotIn(chosen.id, {entry.id for entry in after.queue})
        with self.assertRaises(LibraryError):
            self.library.apply(chosen.id, setter=Mock())

    def test_old_unbalanced_queue_is_archived_without_deleting_files(self):
        wallpaper = self.fetcher.download_wallpaper({'index':0, 'title':'File:Old.jpg',
                                                    'url':'https://upload.wikimedia.org/old.jpg'})
        self.library._store(wallpaper, 'legacy-id', {'legacy'}, self.now)
        result = self.library.fill(fetcher=self.fetcher)
        self.assertEqual(len(result.queue), 7)
        self.assertNotIn('legacy-id', {entry.id for entry in result.queue})
        self.assertTrue(wallpaper.path.exists())


class CategoryTests(unittest.TestCase):
    def test_unrelated_categories_and_low_quality_do_not_enter_candidates(self):
        fetcher = Fetcher('test/1.0')
        valid = {'title':'File:Mountain wallpaper.jpg', 'width':3840, 'height':2160,
                 'extmetadata': {'Categories': {'value': WALLPAPER_CATEGORIES[0]},
                                 'Assessments': {'value':'featured'}}}
        unrelated = {**valid, 'title':'File:Mountain mixed.jpg',
                     'extmetadata': {'Categories': {'value':'Landscapes|Featured pictures'},
                                     'Assessments': {'value':'featured'}}}
        small = {**valid, 'title':'File:Mountain small.jpg', 'width':1920, 'height':1080}
        with patch.object(fetcher, '_api', return_value={'query': {'search': [
            {'title':item['title']} for item in [valid, unrelated, small]]}}), \
             patch.object(fetcher, '_metadata_batch', return_value=[valid, unrelated, small]):
            results = list(fetcher.wallpaper_candidates(themes=('Mountains',)))
            self.assertEqual(results, [{**valid, 'theme':'Mountains'}])

    def test_premium_requires_4k_and_community_recognition(self):
        info = {'width':3840, 'height':2160, 'extmetadata': {'Assessments': {'value':'featured'}}}
        self.assertTrue(premium_candidate(info))
        self.assertFalse(premium_candidate({**info, 'width':3839}))
        self.assertFalse(premium_candidate({**info, 'height':2159}))
        self.assertFalse(premium_candidate({**info, 'extmetadata':{}}))
        self.assertFalse(premium_candidate({**info, 'height':4000}))
        self.assertFalse(premium_candidate({**info, 'title':'File:1936 Survey map of Tibet.jpg'}))
        self.assertFalse(premium_candidate({**info, 'title':'File:Landscape engraving.jpg'}))

    def test_subject_filter_excludes_animal_and_irrelevant_search_hits(self):
        self.assertFalse(matches_theme({'title':'File:Retriever in forest.jpg'}, 'Forests'))
        self.assertFalse(matches_theme({'title':'File:Bunker.jpg'}, 'Mountains'))
        self.assertTrue(matches_theme({'title':'File:Snowy mountain peaks.jpg'}, 'Mountains'))
        self.assertFalse(matches_theme({'title':'File:Sea lion on a sunny coast.jpg'}, 'Coastlines'))
        self.assertFalse(matches_theme({'title':'File:African elephants, Lake St Lucia.jpg'}, 'Lakes & waterfalls'))
        self.assertFalse(matches_theme({'title':'File:Human watching the Milky Way.jpg',
                                       'extmetadata':{'Categories':{'value':'Lake district'}}}, 'Lakes & waterfalls'))


if __name__ == '__main__':
    unittest.main()
