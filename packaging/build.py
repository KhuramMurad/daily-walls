#!/usr/bin/env python3
"""Build architecture-independent Debian and RPM packages without root."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = '1.0.1'
OUT = ROOT / 'dist'
WORK = ROOT / 'build' / 'packages'
STAGE = WORK / 'payload'


def run(*args):
    subprocess.run(args, check=True)


def put(name, content, mode=0o644):
    path = STAGE / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(mode)


def main():
    for tool in ('dpkg-deb', 'rpmbuild'):
        if not shutil.which(tool):
            raise SystemExit(f'Missing build tool: {tool}')
    if WORK.exists():
        shutil.rmtree(WORK)
    STAGE.mkdir(parents=True)
    OUT.mkdir(exist_ok=True)
    for name in ('app', 'bing', 'fetcher', 'firefox', 'layout', 'library', 'main', 'notifier', 'setter'):
        put(f'usr/share/daily-walls/{name}.py', (ROOT / f'{name}.py').read_text())
    put('usr/bin/daily-walls', '#!/bin/sh\nexec /usr/bin/python3 /usr/share/daily-walls/main.py "$@"\n', 0o755)
    put('usr/share/applications/io.github.KhuramMurad.daily-walls.desktop', '''[Desktop Entry]
Type=Application
Name=Daily Walls
Comment=Browse and apply daily wallpapers from Commons, Bing, and Firefox
Exec=daily-walls --gui
Icon=daily-walls
Terminal=false
Categories=Utility;GTK;
Keywords=wallpaper;background;desktop;
StartupNotify=true
''')
    put('usr/share/icons/hicolor/scalable/apps/daily-walls.svg', (ROOT / 'assets/wiki-wallpaper.svg').read_text())
    put('usr/share/doc/daily-walls/README.md', (ROOT / 'README.md').read_text())
    for name in ('wiki-wallpaper-maintenance.service', 'wiki-wallpaper-maintenance.timer'):
        text = (ROOT / 'systemd' / name).read_text()
        text = text.replace('ExecStart=/usr/bin/python3 /home/ubuntu-hp/wiki-wallpaper/main.py --maintain',
                            'ExecStart=/usr/bin/daily-walls --maintain')
        text = text.replace('WorkingDirectory=/home/ubuntu-hp/wiki-wallpaper\n', '')
        put('usr/lib/systemd/user/' + name, text)

    # RPM payload contains only runtime files, never Debian control metadata.
    top = WORK / 'rpm'
    for directory in ('SOURCES', 'SPECS', 'BUILD', 'BUILDROOT', 'RPMS', 'SRPMS'):
        (top / directory).mkdir(parents=True)
    with tarfile.open(top / 'SOURCES/payload.tar.gz', 'w:gz') as archive:
        archive.add(STAGE / 'usr', arcname='usr')
    spec = f'''Name: daily-walls
Version: {VERSION}
Release: 1
Summary: Daily desktop wallpapers from Commons, Bing, and Firefox
# Upstream has not declared an open-source license.
License: LicenseRef-Proprietary
URL: https://github.com/KhuramMurad/daily-walls
Source0: payload.tar.gz
BuildArch: noarch
AutoReqProv: no
Requires: python3 >= 3.10
Requires: python3-gobject, python3-cairo, python3-pillow, gtk3
Requires: webp-pixbuf-loader, libnotify, glib2, /bin/sh
%description
GTK wallpaper browser with separate seven-image queues, duplicate protection,
and ten-day retention. Run inside a supported Linux graphical desktop session.
%prep
%build
%install
mkdir -p %{{buildroot}}
tar -xzf %{{SOURCE0}} -C %{{buildroot}}
%files
/usr/bin/daily-walls
/usr/share/daily-walls
/usr/share/applications/io.github.KhuramMurad.daily-walls.desktop
/usr/share/icons/hicolor/scalable/apps/daily-walls.svg
%doc /usr/share/doc/daily-walls/README.md
/usr/lib/systemd/user/wiki-wallpaper-maintenance.service
/usr/lib/systemd/user/wiki-wallpaper-maintenance.timer
'''
    specpath = top / 'SPECS/daily-walls.spec'
    specpath.write_text(spec)
    run('rpmbuild', '--define', f'_topdir {top}', '--define', f'_dbpath {top / "rpmdb"}',
        '--define', '__os_install_post %{nil}', '-bb', str(specpath))
    rpm = next((top / 'RPMS/noarch').glob('*.rpm'))
    shutil.copy2(rpm, OUT / rpm.name)
    put('DEBIAN/control', f'''Package: daily-walls
Version: {VERSION}
Section: graphics
Priority: optional
Architecture: all
Maintainer: Khuram Murad <KhuramMurad@users.noreply.github.com>
Homepage: https://github.com/KhuramMurad/daily-walls
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, python3-cairo, python3-pil, gir1.2-gtk-3.0, webp-pixbuf-loader, libnotify-bin, libglib2.0-bin
Description: Daily wallpapers from Commons, Bing, and Firefox
 GTK wallpaper browser with separate seven-image queues, duplicate
 protection, and ten-day retention for Linux graphical desktops.
''')
    deb = OUT / f'daily-walls_{VERSION}_all.deb'
    run('dpkg-deb', '--root-owner-group', '--build', str(STAGE), str(deb))
    artifacts = (deb, OUT / rpm.name)
    (OUT / 'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in artifacts))
    for path in artifacts:
        print(path)


if __name__ == '__main__':
    main()
