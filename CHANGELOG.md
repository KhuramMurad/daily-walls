# Release history

## 1.2.0-alpha.1 — Windows preview — 2026-09-27

- First portable Windows x64 package, with GUI and CLI executables and bundled GTK/Python runtime.
- Native wallpaper backend, Windows storage and locking, WebP conversion, and optional per-user scheduled maintenance.
- Preserve wallpaper comparison, local imports, saved gallery, and ten-day retention.
- Activate Windows CI and verify both packaged launchers outside the MSYS2 environment, including the main window, Cairo previews, gallery, JPEG/PNG/WebP, TLS trust, imports, and locking.
- Correct cross-platform file identity checks and rotating GUI log output.

This is an unsigned preview targeting Windows 11. Automated validation runs on
Windows Server 2022; interactive wallpaper changes, notifications, and scheduling
still require Windows 11 desktop testing. Linux 1.1.3 remains the stable release.

[Windows release and ZIP](https://github.com/KhuramMurad/daily-walls/releases/tag/v1.2.0-alpha.1)

## 1.1.3 — Linux — 2026-09-27

- Keep Linux and Windows development together on `main`, with Windows-specific
  code, packaging, configuration, and tests under `windows/`.
- Add shared platform adapters while retaining Linux storage and locking behavior.
- Include the required shared adapter module in both Linux installers.
- Separate Linux outputs (`dist/linux/`) from Windows outputs (`dist/windows/`).
- Document platform status, direct downloads, builds, and detailed Linux usage.

At the time of Linux 1.1.3, Windows support was source-only development work.

## 1.1.2 — Linux — 2026-09-27

- Fix GNOME/Wayland dock icon matching by aligning application and launcher IDs.
- Set the window icon and X11 startup class.
- Include the features developed in local 1.1.0 and 1.1.1 builds below.

[Release and packages](https://github.com/KhuramMurad/daily-walls/releases/tag/v1.1.2)

### 1.1.1 — local build, not a separately tagged release

- Replace the Unsplash-specific manual category with **Locally saved wallpapers**.
- Preserve existing imports and expiry dates.
- Restore native dropdown menu styling.

### 1.1.0 — local build, not a separately tagged release

- Add the saved-wallpaper gallery and folder access.
- Compare a downloaded alternative before replacing the selected queue image.
- Retain both images until their original ten-day expiry.
- Add manual landscape-image imports and validation.

## 1.0.1 — Linux — 2026-09-27

- Fix fresh-install wallpaper downloads with a default project User-Agent.
- Preserve explicit, environment, and saved custom User-Agent settings.

[Release and packages](https://github.com/KhuramMurad/daily-walls/releases/tag/v1.0.1)

## 1.0.0 — Linux — 2026-09-26

- Initial packaged GTK wallpaper browser with Commons, Bing, and Firefox sources.
- Seven-image queues, duplicate history, background previews, and ten-day retention.
- Debian and RPM packages with optional user-level maintenance units.

[Release and packages](https://github.com/KhuramMurad/daily-walls/releases/tag/v1.0.0)
