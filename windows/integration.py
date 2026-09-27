"""Windows shell integration using fixed, packaged PowerShell scripts."""
import json
from pathlib import Path
import subprocess
import sys

from platform_support import is_windows


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return base / 'windows' / name


def notify(title: str, message: str) -> bool:
    """Notification failure must never prevent downloading/applying wallpapers."""
    if not is_windows():
        return False
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(resource_path('notify.ps1'))],
            input=json.dumps({'title': title, 'message': message}, ensure_ascii=True),
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
