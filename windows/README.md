# Daily Walls for Windows — development preview

Linux and Windows share the `main` branch. Windows runtime modules, packaging,
configuration, and Windows-specific tests live in `windows/`. Windows builds
write only to `dist/windows/` and `build/windows/`, independently of the Linux
DEB/RPM packaging. Preview version:
**1.2.0-alpha.2**, targeting Windows 11 x64.

[Download the portable ZIP](https://github.com/KhuramMurad/daily-walls/releases/download/v1.2.0-alpha.2/daily-walls-1.2.0-alpha.2-windows-x64.zip) ·
[Release notes and SHA256SUMS](https://github.com/KhuramMurad/daily-walls/releases/tag/v1.2.0-alpha.2)

Both bundled executables passed the packaged GUI/runtime checks on Windows Server
2022 with MSYS2 removed from PATH. This unsigned preview still needs interactive
Windows 11 desktop validation. Linux downloads remain in the [root README](../README.md).

## Windows support

- Existing GTK interface, downloads, local imports, comparison, saved gallery, and SQLite history.
- `%LOCALAPPDATA%\DailyWalls\data`, `cache`, `config`, and `logs` instead of Linux directories.
- Native nonblocking Windows file locks.
- Windows `IDesktopWallpaper` COM backend, applying one image to all monitors while
  preserving the user's desktop sizing mode. It reads each monitor's wallpaper
  for retention protection. WebP files get a lossless PNG companion; both expire
  together. If monitor state cannot be read, deletion is deferred.
- Application identity and bundled icon, GUI and console launchers, TLS trust bundle.
- Optional hourly Task Scheduler maintenance, limited to the signed-in user.
- Best-effort Windows toast notifications; delivery for an unpackaged portable app
  needs interactive validation and may be suppressed. Failures never abort downloads.

## Build on Windows

Install MSYS2, open **UCRT64**, and install:

```sh
pacman -S --needed mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-gobject \
  mingw-w64-ucrt-x86_64-python-cairo mingw-w64-ucrt-x86_64-python-pillow \
  mingw-w64-ucrt-x86_64-python-certifi mingw-w64-ucrt-x86_64-gtk3 \
  mingw-w64-ucrt-x86_64-webp-pixbuf-loader mingw-w64-ucrt-x86_64-pyinstaller
python -m unittest discover -s tests -v
python -m unittest discover -s windows/tests -v
python windows/build.py
```

The active workflow `.github/workflows/windows.yml` (mirrored in
`windows/github-actions.yml`) runs these steps on Windows Server 2022 and
smoke-tests both launchers with MSYS2 removed from PATH. The packaged tests open
the main window and saved gallery, render a preview through Cairo, and verify
JPEG/PNG/WebP decoding, TLS trust, local imports, conversion, and file locking.
Successful builds upload a portable ZIP and SHA256SUMS as Actions artifacts;
successful main-branch builds also publish a versioned prerelease when that version does not already exist. Published assets are never overwritten; bump the preview version for a new package. This workflow does not run Linux packaging.

Build outputs:

```text
dist/windows/
  DailyWalls/                                  Unpacked application and scripts
  daily-walls-1.2.0-alpha.2-windows-x64.zip      Portable preview
  SHA256SUMS                                   ZIP integrity checksum
```

Extract the entire ZIP and run `DailyWalls.exe`. End users do not need Python or
MSYS2. Use `DailyWallsCLI.exe --help` for command-line operations. Logs are stored
at `%LOCALAPPDATA%\DailyWalls\logs\daily-walls.log`.

## Optional maintenance

From PowerShell in the extracted folder:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\maintenance.ps1
```

To remove only the scheduled task:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\maintenance.ps1 -Remove
```

No task is installed automatically. Register again if moving the app folder.
The app also performs maintenance while open. Original manually imported files
are never removed; managed copies follow the ten-day retention rule.

## Before a stable Windows release

- Validate the ZIP on an actual Windows 11 desktop without Python/MSYS2 installed.
- Apply JPEG, PNG, and WebP wallpapers; verify multiple monitors, current-image
  protection, fit/fill behavior, Unicode paths, high DPI, and native file opening.
- Test scheduling across sign-in/reboot and toast notifications.
- Add a per-user installer with shortcuts, uninstall, and notification registration.
- Decide on signing and test update/uninstall behavior without deleting user data.

Automated tests use mock wallpaper APIs and temporary image libraries. A successful
CI build is not evidence that interactive wallpaper changes or notifications have
been validated on an end-user desktop.
