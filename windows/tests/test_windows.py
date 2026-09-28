from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

from PIL import Image
import platform_support
import windows.desktop
from library import WallpaperLibrary, RETENTION_SECONDS


class WindowsPathsTests(unittest.TestCase):
    def test_windows_paths_ignore_linux_xdg_settings(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(platform_support, 'is_windows', return_value=True), \
                patch.dict(os.environ, {'LOCALAPPDATA': directory, 'XDG_DATA_HOME': '/unrelated'}):
            root = Path(directory) / 'DailyWalls'
            self.assertEqual(platform_support.data_directory(), root / 'data')
            self.assertEqual(platform_support.cache_directory(), root / 'cache')
            self.assertEqual(platform_support.config_directory(), root / 'config')

    def test_lock_contention_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'library.lock'
            script = ('from pathlib import Path; from platform_support import exclusive_lock; '
                      'import sys\n'
                      'try:\n with exclusive_lock(Path(sys.argv[1])): pass\n'
                      'except BlockingIOError: sys.exit(17)\n')
            with platform_support.exclusive_lock(path):
                result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True)
                self.assertEqual(result.returncode, 17, result.stderr.decode())
            result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())


class WindowsWallpaperTests(unittest.TestCase):
    def test_com_guid_layout_and_hresult_failures(self):
        self.assertEqual(ctypes.sizeof(windows.desktop.GUID), 16)
        guid = windows.desktop.GUID.parse(windows.desktop.IID_DESKTOP_WALLPAPER)
        self.assertEqual(bytes(guid.data)[:4], bytes.fromhex('a9562bb9'))
        windows.desktop._check(1, 'Success with information')
        with self.assertRaisesRegex(OSError, '80004005'):
            windows.desktop._check(-2147467259, 'Set wallpaper')

    def test_com_session_balances_initialization_and_release(self):
        ole = Mock()
        ole.CoInitializeEx.return_value = 0
        ole.CoCreateInstance.return_value = 0
        desktop = Mock()
        with patch('windows.desktop.sys.platform', 'win32'), \
                patch('ctypes.WinDLL', return_value=ole, create=True), \
                patch('windows.desktop.DesktopWallpaper', return_value=desktop):
            with windows.desktop.desktop_session() as session:
                self.assertIs(session, desktop)
        desktop.close.assert_called_once()
        ole.CoUninitialize.assert_called_once()

    def test_com_creation_failure_still_uninitializes(self):
        ole = Mock()
        ole.CoInitializeEx.return_value = 0
        ole.CoCreateInstance.return_value = -2147467259
        with patch('windows.desktop.sys.platform', 'win32'), \
                patch('ctypes.WinDLL', return_value=ole, create=True):
            with self.assertRaises(OSError):
                with windows.desktop.desktop_session():
                    self.fail('Failed COM creation must not yield a desktop')
        ole.CoUninitialize.assert_called_once()

    def test_com_session_activates_out_of_process_wallpaper_server(self):
        ole = Mock()
        ole.CoInitializeEx.return_value = 0
        # Model Windows where DesktopWallpaper has no in-process registration.
        ole.CoCreateInstance.side_effect = lambda clsid, outer, context, iid, pointer: (
            0 if context == 4 else -2147221164)  # REGDB_E_CLASSNOTREG
        with patch('windows.desktop.sys.platform', 'win32'), \
                patch('ctypes.WinDLL', return_value=ole, create=True), \
                patch('windows.desktop.DesktopWallpaper') as desktop:
            with windows.desktop.desktop_session() as session:
                self.assertIs(session, desktop.return_value)
        desktop.return_value.close.assert_called_once()
        ole.CoUninitialize.assert_called_once()

    def test_existing_com_apartment_is_not_uninitialized(self):
        ole = Mock()
        ole.CoInitializeEx.return_value = -2147417850
        ole.CoCreateInstance.return_value = 0
        desktop = Mock()
        with patch('windows.desktop.sys.platform', 'win32'), \
                patch('ctypes.WinDLL', return_value=ole, create=True), \
                patch('windows.desktop.DesktopWallpaper', return_value=desktop):
            with windows.desktop.desktop_session():
                pass
        desktop.close.assert_called_once()
        ole.CoUninitialize.assert_not_called()

    def test_webp_conversion_and_apply_uses_native_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a space ü.webp'
            Image.new('RGB', (1920, 1080), 'blue').save(path)
            original = path.read_bytes()
            api = Mock()
            @contextmanager
            def session():
                yield api
            with patch.object(windows.desktop, 'desktop_session', session):
                self.assertEqual(windows.desktop.set_wallpaper(path), 'windows')
            converted = windows.desktop.converted_path(path)
            api.set_wallpaper.assert_called_once_with(converted)
            with Image.open(converted) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(path.read_bytes(), original)

    def test_all_monitors_and_conversion_originals_are_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'first.webp'
            second = Path(directory) / 'second.jpg'
            api = Mock()
            api.current_paths.return_value = {windows.desktop.converted_path(first), second}
            @contextmanager
            def session():
                yield api
            with patch.object(windows.desktop, 'desktop_session', session):
                self.assertEqual(windows.desktop.current_wallpapers(),
                                 {first, second, windows.desktop.converted_path(first)})

    def test_cleanup_removes_conversion_only_when_original_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [1_800_000_000.0]
            lib = WallpaperLibrary(root / 'data', root / 'cache', clock=lambda: now[0], removal_notice=lambda _: None)
            lib.select_source('local')
            download = root / 'source.webp'
            Image.new('RGB', (1920, 1080), 'red').save(download)
            entry = lib.import_local(download)
            converted = windows.desktop.prepare_image(entry.wallpaper.path)
            now[0] += RETENTION_SECONDS
            with patch('library.is_windows', return_value=True), \
                    patch('windows.desktop.current_wallpapers', return_value={entry.wallpaper.path}):
                self.assertEqual(lib.cleanup(), 0)
            self.assertTrue(converted.exists())
            with patch('library.is_windows', return_value=True), \
                    patch('windows.desktop.current_wallpapers', return_value=set()):
                self.assertEqual(lib.cleanup(), 1)
            self.assertFalse(converted.exists())
            self.assertTrue(download.exists())

    def test_inspection_failure_defers_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [1_800_000_000.0]
            lib = WallpaperLibrary(root / 'data', root / 'cache', clock=lambda: now[0], removal_notice=lambda _: None)
            lib.select_source('local')
            download = root / 'source.png'
            Image.new('RGB', (1920, 1080), 'red').save(download)
            entry = lib.import_local(download)
            now[0] += RETENTION_SECONDS
            with patch('library.is_windows', return_value=True), \
                    patch('windows.desktop.current_wallpapers', side_effect=OSError('No desktop session')):
                self.assertEqual(lib.cleanup(), 0)
            self.assertTrue(entry.wallpaper.path.exists())

    def test_setter_routes_windows_without_linux_commands(self):
        import setter
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wallpaper.png'
            path.write_bytes(b'fixture')
            with patch('setter.is_windows', return_value=True), \
                    patch('windows.desktop.set_wallpaper', return_value='windows') as apply, \
                    patch('setter._run', side_effect=AssertionError('Linux command')):
                self.assertEqual(setter.set_wallpaper(path), 'windows')
                apply.assert_called_once_with(path)
