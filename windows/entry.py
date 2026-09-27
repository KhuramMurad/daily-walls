"""Windows launchers: GUI by default; CLI executable retains main.py semantics."""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from platform_support import data_directory


def run():
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    log_dir = data_directory().parent / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / 'daily-walls.log', maxBytes=2_000_000,
                                  backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[handler])
    # Windowed PyInstaller executables have no stdout/stderr handles.
    if sys.stdout is None:
        sys.stdout = handler.stream
    if sys.stderr is None:
        sys.stderr = handler.stream
    try:
        if '--self-test' in sys.argv:
            from windows.selftest import run as self_test
            self_test()
            return 0
        if len(sys.argv) == 1 and not Path(sys.executable).stem.lower().endswith('cli'):
            sys.argv.append('--gui')
        from main import main
        return main()
    except Exception:
        logging.exception('Daily Walls could not start')
        if '--gui' in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                f'Daily Walls could not start. Details are in:\n{log_dir / "daily-walls.log"}',
                'Daily Walls', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
