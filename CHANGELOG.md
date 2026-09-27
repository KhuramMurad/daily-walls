# Release history

## 1.1.3 — Linux — 2026-09-27

- Keep Linux and Windows development together on `main`, with Windows-specific
  code, packaging, configuration, and tests under `windows/`.
- Add shared platform adapters while retaining Linux storage and locking behavior.
- Include the required shared adapter module in both Linux installers.
- Separate Linux outputs (`dist/linux/`) from Windows outputs (`dist/windows/`).
- Document platform status, direct downloads, builds, and detailed Linux usage.

Windows 1.2.0-alpha.1 is source-only development work, not a released executable.

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
