"""Windows desktop API. COM objects are created and released on the calling thread.

Applies one image to all monitors; current wallpaper inspection checks each monitor.
No Windows DLL is loaded merely by importing this module.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tempfile
from uuid import UUID

APP_ID = 'KhuramMurad.DailyWalls.Windows'
HRESULT = ctypes.c_int32
CLSID_DESKTOP_WALLPAPER = 'C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD'
IID_DESKTOP_WALLPAPER = 'B92B56A9-8B55-4E14-9A89-0199BBB6F93B'


class GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, text):
        return cls((ctypes.c_ubyte * 16).from_buffer_copy(UUID(text).bytes_le))


def _check(result, operation):
    if result < 0:
        raise OSError(f'{operation} failed (HRESULT 0x{result & 0xffffffff:08X})')


class DesktopWallpaper:
    def __init__(self, pointer, ole32):
        self.pointer, self.ole32 = pointer, ole32

    def _method(self, index, *arguments, result=HRESULT):
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])

    def _string(self, index, argument_type, argument):
        value = ctypes.c_void_p()
        try:
            _check(self._method(index, argument_type, ctypes.POINTER(ctypes.c_void_p))(
                self.pointer, argument, ctypes.byref(value)), 'Read desktop wallpaper')
            return ctypes.wstring_at(value) if value.value else ''
        finally:
            if value.value:
                self.ole32.CoTaskMemFree(value)

    def current_paths(self):
        count = wintypes.UINT()
        _check(self._method(6, ctypes.POINTER(wintypes.UINT))(self.pointer, ctypes.byref(count)),
               'Enumerate monitors')
        paths = set()
        for index in range(count.value):
            monitor = self._string(5, wintypes.UINT, index)
            wallpaper = self._string(4, wintypes.LPCWSTR, monitor)
            if wallpaper:
                paths.add(Path(wallpaper).resolve())
        return paths

    def set_wallpaper(self, path):
        # NULL monitor means all monitors. Preserve the user's existing fit/fill mode.
        _check(self._method(3, wintypes.LPCWSTR, wintypes.LPCWSTR)(self.pointer, None, str(path)),
               'Set desktop wallpaper')

    def close(self):
        self._method(2, result=ctypes.c_uint32)(self.pointer)


@contextmanager
def desktop_session():
    if sys.platform != 'win32':
        raise OSError('Windows desktop APIs are only available on Windows')
    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = HRESULT
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = HRESULT
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    initialized = ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    # RPC_E_CHANGED_MODE means this thread already has another valid COM apartment.
    if initialized != -2147417850:
        _check(initialized, 'Initialize Windows COM')
    desktop = None
    try:
        pointer = ctypes.c_void_p()
        clsid, iid = GUID.parse(CLSID_DESKTOP_WALLPAPER), GUID.parse(IID_DESKTOP_WALLPAPER)
        _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)),
               'Open Windows desktop')
        desktop = DesktopWallpaper(pointer, ole)
        yield desktop
    finally:
        if desktop is not None:
            desktop.close()
        if initialized >= 0:
            ole.CoUninitialize()


def converted_path(path: Path) -> Path:
    return path.with_name(path.name + '.windows.png')


def prepare_image(path: Path) -> Path:
    """Windows need not have a WebP codec; keep a lossless PNG beside the original."""
    if path.suffix.lower() != '.webp':
        return path
    from PIL import Image
    target = converted_path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.png', delete=False) as stream:
            temporary = Path(stream.name)
        with Image.open(path) as image:
            image.load()
            image.save(temporary, 'PNG')
        temporary.replace(target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def set_wallpaper(path: Path) -> str:
    prepared = prepare_image(path)
    with desktop_session() as desktop:
        desktop.set_wallpaper(prepared)
    return 'windows'


def current_wallpapers() -> set[Path]:
    with desktop_session() as desktop:
        paths = desktop.current_paths()
    # Protect original WebP images whenever their converted copy is on a monitor.
    originals = {path.with_name(path.name.removesuffix('.windows.png')) for path in paths
                 if path.name.endswith('.webp.windows.png')}
    return paths | originals


def set_application_identity():
    shell = ctypes.WinDLL('shell32')
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = HRESULT
    _check(shell.SetCurrentProcessExplicitAppUserModelID(APP_ID), 'Set application identity')
