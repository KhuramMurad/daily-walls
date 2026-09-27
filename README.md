# Daily Walls

![Daily Walls GUI showing a wallpaper preview, source selector, and seven-image queue](assets/screenshots/daily-walls.png)

[Photograph credits](assets/screenshots/CREDITS.md)

Open **Daily Walls** from the application menu, or run:

```sh
/usr/bin/python3 main.py --gui
```

## Install a package

Download the `.deb` or `.rpm` from [GitHub Releases](https://github.com/KhuramMurad/daily-walls/releases).

Debian/Ubuntu (Python 3.10 or newer):

```sh
sudo apt install ./daily-walls_1.0.1_all.deb
```

Fedora (Python 3.10 or newer):

```sh
sudo dnf install ./daily-walls-1.0.1-1.noarch.rpm
```

Open **Daily Walls** from the application menu, or run `daily-walls --gui`.
The packages install the system Python/GTK, Pillow, and WebP loader dependencies.
Desktop-specific wallpaper tools (such as `swaybg`, `hyprpaper`, or `feh`) must
be available for those desktops. GNOME and Cinnamon use their existing settings.
Packages do not change your wallpaper or start background jobs during installation.
To enable hourly maintenance for your user:

```sh
systemctl --user daemon-reload
systemctl --user enable --now wiki-wallpaper-maintenance.timer
```

If you previously installed a source-checkout service under
`~/.config/systemd/user/`, update its `ExecStart` to `/usr/bin/daily-walls --maintain`
and remove its checkout-specific `WorkingDirectory` before reloading systemd.
User service overrides take precedence over the packaged service.

Build both packages from source with `python3 packaging/build.py` after installing
`dpkg-deb` and `rpmbuild`. Outputs and SHA-256 checksums are written to `dist/`.
Packages are unsigned. No open-source license has been declared for this project.

## The seven-wallpaper queue

The app keeps **seven unused wallpapers** downloaded and ready. Click a thumbnail,
or Previous / Next, to preview a saved image. **Set as wallpaper** applies it,
marks it used, and downloads a new, unique image to refill its slot. Browsing
previews does not consume the queue. Previously used images cannot be applied
again through the queue. Refill queue retries missing slots after a network error.

Commons images must be landscape orientation (`width > height`) and at least **3840 × 2160**.
They must also have a Commons **Featured** or **Quality image** assessment.
Featured images are preferred, with desktop-friendly proportions used to rank
matching candidates. Resolution alone does not qualify an image.
Selections come only from these Commons computer-wallpaper categories, with
membership verified against the file's metadata:

- Computer wallpapers
- Commons featured desktop backgrounds
- Widescreen desktop backgrounds

The seven queue slots are balanced by subject: **mountains, coastlines, lakes and
waterfalls, forests, deserts, architecture, and space**. Each slot has one image;
using or replacing it searches for another in the same subject. The file title,
description, and subject categories are checked to reject unrelated keyword
matches and animal-centered images. If a subject has no qualifying unseen result,
its slot remains empty with a retry message rather than duplicating another topic.

These are categories for actual desktop backgrounds, not the physical wall
coverings in Commons' “Wallpapers” category. General POTD and unrelated featured
images are no longer used by the app or CLI. Landscape means horizontal
orientation. The current selection profile favours scenic landscapes and sky/space
imagery, rather than wildlife. Artistic appeal is subjective; **Find another**
replaces a preview without changing the desktop. Replaced files stay saved until
their normal ten-day expiry and remain in the no-repeat history.

The responsive main window adapts to the available monitor area. A large preview
and a compact details panel keep the queue and action buttons visible.
**Fit entire image** is the default and preserves all image edges without
stretching; unused space is expected when the image and frame have different
proportions. **Fill frame (crop edges)** is available as an explicit option.
Thumbnail previews also preserve the complete image. The original is never edited.
The author, license, quality assessment, and expiry appear in the details panel.
Full attribution is available in the author tooltip and the Commons file link.

## No repeated wallpapers

A persistent SQLite database remembers canonical file titles, original URLs
(with tracking parameters removed), Commons SHA-1 hashes, and downloaded SHA-256
hashes. This prevents repeated selections, including byte-identical files under
different names. The history survives app restarts and image deletion. It is not
a perceptual similarity detector for separately edited or recompressed versions.
Existing downloads from the previous app version are imported into this history.

If the network or category results cannot supply seven unseen qualifying files,
the app keeps the available queue and shows a retry message. It never fills missing
slots with duplicates or unrelated images. The official API gateway fallback
searches up to 100 results per subject/category; the primary Action API follows pagination.

## Ten-day retention

Every downloaded image expires **10 × 24 hours after it was saved**. Applying or
previewing it does not restart that clock. Used images remain saved until expiry,
so storage can contain more than the seven upcoming images.

Expired app-owned files are permanently deleted, with a desktop notification
before removal and a persistent message in the app afterward. Removal messages
are independent of the optional wallpaper-change notifications.

**Your current wallpaper is kept, even after ten days, until you change it.**
After you apply another wallpaper in the app, an expired previous background is
removed. The app never automatically changes your background as part of cleanup.
The last background applied by the app is conservatively protected when desktop
state cannot be inspected; GNOME/Cinnamon background settings are also checked.

Cleanup and refilling run on app startup, every five minutes while the app is
open, and through the installed hourly user timer while it is closed. An hourly
run can remove files up to about one hour after their ten-day expiry. Missed
scheduled runs are handled when the user manager resumes; nothing runs while
the computer is powered off.

```sh
systemctl --user status wiki-wallpaper-maintenance.timer
systemctl --user list-timers wiki-wallpaper-maintenance.timer
```

To disable background maintenance (in-app checks still run):

```sh
systemctl --user disable --now wiki-wallpaper-maintenance.timer
```

## Storage and commands

- Images: `~/.cache/wiki-wallpaper/queue/`
- Legacy images: `~/.cache/wiki-wallpaper/` (also managed by retention)
- Queue and permanent history: `~/.local/share/wiki-wallpaper/library.sqlite3`
  (`$XDG_DATA_HOME` is respected)
- Optional contact/User-Agent override: `~/.config/wiki-wallpaper/user-agent`
  (`$XDG_CONFIG_HOME` is respected)
- User timer/service: `~/.config/systemd/user/wiki-wallpaper-maintenance.*`
- Application launcher: `~/.local/share/applications/io.github.wikiwallpaper.App.desktop`

```sh
/usr/bin/python3 main.py --notify      # Apply an unused wallpaper and refill
/usr/bin/python3 main.py --maintain    # Cleanup/refill without changing wallpaper
/usr/bin/python3 main.py --cleanup     # Cleanup only; protect the current wallpaper
```

`--force` and `--featured` remain accepted for compatibility, but cannot bypass
queue uniqueness or wallpaper category filtering. The launcher and installed
service point to this project directory; update them if moving the project.

## Runtime and network

The GUI uses the system Python, GTK 3 and PyGObject. Debian/Ubuntu packages are
`python3-gi` and `gir1.2-gtk-3.0`. Firefox image validation also requires `python3-pil` (Pillow with WebP support).
The other sources use Python’s standard library. Desktop setting utilities and `notify-send` must be installed.

Downloads work without user configuration: the default User-Agent identifies Daily Walls
and its project URL. To override it, set `WIKI_WALLPAPER_USER_AGENT` or save your
identifier in the optional configuration file listed above.

The Commons Action API is primary. On a connection failure, the app uses
Wikimedia's official Commons REST gateway for category searches and Wikipedia's
shared Commons repository for metadata. Original images still download directly
from `upload.wikimedia.org`, with TLS verification enabled. The gateway is subject
to [Wikimedia's deprecation policy](https://wikitech.wikimedia.org/wiki/API_Portal/Deprecation).

## Checks

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

Tests cover 4K/quality/subject filtering, balanced slots, queue replacement,
aspect-preserving fit/fill geometry, downloads, desktop commands, gateway
fallback, persistent queue/refill, duplicate identities/content, partial failures,
concurrent writers, expiry boundaries, deletion notices, active-wallpaper
protection, and restricting deletion to app-owned paths. Expiry tests use a
simulated clock and temporary files, not your desktop or real saved images.

### Wallpaper sources: Commons, Bing, and Firefox

Use the source dropdown at the top of the app to choose **Firefox · Picture of the day**,
**Bing · Daily wallpapers**, or **Commons · Premium scenery**. Each source keeps its own
seven-image queue; switching sources preserves saved images. The selected source is remembered
and used by scheduled refills. CLI: `python3 main.py --source firefox --maintain`
(download only), or `--source firefox` to apply an image.

### Firefox picture of the day wallpapers

Firefox daily wallpapers are fetched through Mozilla's Merino service
(`merino.services.mozilla.com/api/v1/rss/picture-of-the-day`), which powers the native
Firefox New Tab "Picture of the Day" background feature. Merino delivers high-resolution
WebP images with attribution, descriptions, and Commons licensing details.
The Firefox queue accepts landscape images of at least 1920 × 1080; actual
dimensions appear in the details panel. Images are fully decoded before saving. Recent days are archived and refilled into the Firefox queue.
Ten-day retention, removal notifications, and active-wallpaper protection apply across all sources.

### Bing daily wallpapers

Use the source dropdown at the top of the app to choose **Bing · Daily wallpapers**
or **Commons · Premium scenery**. Each source keeps its own seven-image queue;
switching sources preserves saved images. The selected source is remembered and
used by scheduled refills. CLI: `python3 main.py --source bing --maintain`
(download only), or `--source bing` to apply an image.

Bing downloads use the public, undocumented homepage archive across several
regional editions. Only downloadable (`wp=true`) JPEG images whose actual dimensions
are at least 3840×2160 and landscape are accepted. Bing's editorial daily images
have their own subjects; they do not use Commons' seven scenery themes or assessments.
Credits and the original Bing information link accompany each image. Images are
labelled **Wallpaper use only**, not Creative Commons. See Microsoft's
[homepage guidance](https://support.microsoft.com/en-us/bing/explore-the-homepage).
The recent archive is finite: if fewer than seven unseen eligible images remain,
the app keeps the available queue and retries later instead of repeating images.
Regional/resolution variants share duplicate identities. Ten-day retention,
removal notifications, and current-wallpaper protection apply across all sources.
