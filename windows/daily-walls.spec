# Built using UCRT64 Python/GTK/PyInstaller on Windows.
from pathlib import Path
root = Path(SPECPATH).resolve().parent
a = Analysis([str(root / 'windows/entry.py')], pathex=[str(root)],
             binaries=[],
             datas=[(str(root / 'windows/assets/daily-walls.ico'), 'windows/assets'),
                    (str(root / 'windows/notify.ps1'), 'windows')],
             hiddenimports=['gi.repository.Gtk', 'gi.repository.Gdk', 'gi.repository.GdkPixbuf',
                            'gi.repository.Pango', 'gi.repository.Gio', 'gi.repository.GLib', 'cairo'],
             hookspath=[], hooksconfig={'gi': {'module-versions': {'Gtk': '3.0', 'Gdk': '3.0'},
                                              'themes': ['Adwaita'], 'icons': ['Adwaita']}},
             runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DailyWalls',
          console=False, icon=str(root / 'windows/assets/daily-walls.ico'), uac_admin=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DailyWallsCLI',
          console=True, icon=str(root / 'windows/assets/daily-walls.ico'), uac_admin=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='DailyWalls')
