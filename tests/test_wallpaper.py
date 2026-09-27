import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fetcher import Fetcher, FetchError, DEFAULT_USER_AGENT, configured_user_agent
import setter


class UserAgentTests(unittest.TestCase):
    def test_fresh_install_uses_project_default(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}, clear=True):
                self.assertEqual(configured_user_agent(), DEFAULT_USER_AGENT)

    def test_override_precedence_and_blank_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "wiki-wallpaper" / "user-agent"
            config.parent.mkdir()
            config.write_text("saved/1.0 (https://example.org)\n")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}, clear=True):
                self.assertEqual(configured_user_agent(), "saved/1.0 (https://example.org)")
                with patch.dict(os.environ, {"WIKI_WALLPAPER_USER_AGENT": "environment/1.0"}):
                    self.assertEqual(configured_user_agent(), "environment/1.0")
                    self.assertEqual(configured_user_agent("explicit/1.0"), "explicit/1.0")
                config.write_text(" \n")
                self.assertEqual(configured_user_agent(), DEFAULT_USER_AGENT)

    def test_invalid_config_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "wiki-wallpaper" / "user-agent"
            config.mkdir(parents=True)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}, clear=True):
                with self.assertRaisesRegex(FetchError, "Cannot read User-Agent"):
                    configured_user_agent()


class Response(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.fetcher = Fetcher('wiki-wallpaper-tests/1.0', self.cache)
        self.info = dict(title='File:Example.jpg', width=3840, height=2160,
                         url='https://upload.wikimedia.org/example.jpg', size=3,
                         mime='image/jpeg', extmetadata={'Artist': {'value': '<b>Alice</b>'}})

    def test_metadata_rejects_portrait_and_small_images(self):
        for width, height in [(1080, 1920), (1919, 1080), (2000, 1079), (2000, 2000)]:
            info = dict(self.info, width=width, height=height)
            with patch.object(self.fetcher, '_api', return_value={'query': {'pages': [
                    {'title': info['title'], 'imageinfo': [info]}]}}):
                self.assertIsNone(self.fetcher._metadata(info['title']))

    def test_missing_headers_stream_and_incomplete_cleanup(self):
        with patch('fetcher.urlopen', return_value=Response(b'abc', {})):
            path = self.fetcher._download(self.info, False)
        self.assertEqual(path.read_bytes(), b'abc')
        with patch('fetcher.urlopen', return_value=Response(b'ab', {'Content-Length': '3'})):
            with self.assertRaises(FetchError):
                self.fetcher._download(self.info, True)
        self.assertEqual(path.read_bytes(), b'abc')
        self.assertEqual(list(self.cache.iterdir()), [path])

    def test_potd_fallback_cache_and_force(self):
        with patch.object(self.fetcher, '_potd_title', side_effect=FetchError('missing')), \
             patch.object(self.fetcher, '_featured_titles', return_value=['File:Example.jpg']), \
             patch.object(self.fetcher, '_metadata', return_value=self.info), \
             patch('fetcher.urlopen', side_effect=lambda *a, **k: Response(b'abc', {})) as download:
            wallpaper = self.fetcher.fetch()
            self.assertEqual(wallpaper.source, 'featured')
            self.assertEqual(wallpaper.photographer, 'Alice')
            self.assertEqual(self.fetcher.fetch(), wallpaper)
            self.assertEqual(download.call_count, 1)
            self.fetcher.fetch(force=True)
            self.assertEqual(download.call_count, 2)

    def test_category_continuation(self):
        with patch.object(self.fetcher, '_api', side_effect=[
            {'query': {'categorymembers': [{'title': 'File:A'}]},
             'continue': {'cmcontinue': 'next', 'continue': '-||'}},
            {'query': {'categorymembers': [{'title': 'File:B'}]}}
        ]) as api:
            self.assertEqual(set(self.fetcher._featured_titles()), {'File:A', 'File:B'})
            self.assertEqual(api.call_args.kwargs['cmcontinue'], 'next')


class SetterTests(unittest.TestCase):
    def test_detect_sessions(self):
        for name, expected in [('ubuntu:GNOME', 'gnome'), ('X-Cinnamon', 'cinnamon'),
                               ('KDE', 'kde'), ('XFCE', 'xfce'), ('sway', 'sway'),
                               ('Hyprland', 'hyprland')]:
            with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': name}, clear=True):
                self.assertEqual(setter.detect_desktop(), expected)
        with patch.dict(os.environ, {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0'}, clear=True):
            with self.assertRaises(setter.WallpaperError):
                setter.detect_desktop()

    def test_gnome_and_cinnamon_schema(self):
        for desktop, schema in [('gnome', 'org.gnome.desktop.background'),
                                ('cinnamon', 'org.cinnamon.desktop.background')]:
            with patch.object(setter, '_run', side_effect=['picture-uri\npicture-uri-dark', '', '']) as run:
                setter._gsettings(Path('/tmp/a b.jpg'), desktop)
                self.assertEqual(run.call_args_list[1].args[0][2], schema)
                self.assertEqual(run.call_args_list[2].args[0][3], 'picture-uri-dark')
                self.assertIn('a%20b.jpg', run.call_args_list[1].args[0][4])

    def test_xfce_all_monitors(self):
        properties = ['/backdrop/screen0/monitorA/workspace0/last-image',
                      '/backdrop/screen0/monitorB/workspace0/last-image']
        with patch.object(setter, '_run', side_effect=['\n'.join(properties), '', '']) as run:
            setter._xfce(Path('/tmp/image.jpg'))
            self.assertEqual(run.call_count, 3)

    def test_hyprpaper_current_and_legacy(self):
        for help_text in ['wallpaper listactive', 'preload wallpaper listactive']:
            results = [json.dumps([{'name': 'DP-1'}]), help_text]
            results += ['ok'] * (2 if 'preload' in help_text else 1)
            with patch.object(setter, '_run', side_effect=results) as run:
                setter._hyprland(Path('/tmp/image.jpg'))
                self.assertEqual(run.call_args.args[0],
                                 ['hyprctl', 'hyprpaper', 'wallpaper', 'DP-1,/tmp/image.jpg'])

    def test_command_failure_is_actionable(self):
        with patch('setter.shutil.which', return_value=None):
            with self.assertRaisesRegex(setter.WallpaperError, 'not installed: feh'):
                setter._run(['feh', '--bg-fill', '/tmp/image.jpg'])


if __name__ == '__main__':
    unittest.main()
