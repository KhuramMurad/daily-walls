"""Build the Windows preview; never builds or publishes Linux packages."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.2.0-alpha.1'


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build with Windows UCRT64 Python. Cross-compilation from Linux is not supported.')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(ROOT / 'dist/windows'), '--workpath', str(ROOT / 'build/windows'),
                    str(ROOT / 'windows/daily-walls.spec')], cwd=ROOT, check=True)
    folder = ROOT / 'dist/windows/DailyWalls'
    for name in ('maintenance.ps1', 'README.txt'):
        shutil.copy2(ROOT / 'windows' / name, folder / name)
    shutil.copy2(ROOT / 'windows/README.md', folder / 'WINDOWS.md')
    archive = Path(shutil.make_archive(str(ROOT / f'dist/windows/daily-walls-{VERSION}-windows-x64'),
                                     'zip', folder.parent, folder.name))
    (ROOT / 'dist/windows/SHA256SUMS').write_text(
        f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n', encoding='utf-8')
    print(archive)


if __name__ == '__main__':
    main()
