"""Platform paths and inter-process locking for the Windows development branch."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import sys


def is_windows() -> bool:
    return sys.platform == 'win32'


def windows_root() -> Path:
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'DailyWalls'


def data_directory() -> Path:
    if is_windows():
        return windows_root() / 'data'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'wiki-wallpaper'


def cache_directory() -> Path:
    return windows_root() / 'cache' if is_windows() else Path.home() / '.cache/wiki-wallpaper'


def config_directory() -> Path:
    if is_windows():
        return windows_root() / 'config'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'wiki-wallpaper'


@contextmanager
def exclusive_lock(path: Path):
    """Hold a nonblocking OS lock; closing/crashing releases it automatically."""
    with path.open('a+b') as stream:
        if is_windows():
            import msvcrt
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise BlockingIOError('Wallpaper library is already updating') from exc
                raise
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
