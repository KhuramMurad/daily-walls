# Changes by Google Antigravity

> Historical implementation record. Subsequent fixes add strict CDN/redirect validation,
> full image decoding, accurate HD+ labeling, and asynchronous cached GUI previews.

This document provides a complete, isolated record of all additions, architectural decisions, and modifications implemented by **Google Antigravity** in this project to support **Firefox Picture of the Day** wallpapers.

---

## 1. Overview & Goal

The user requested the addition of **Firefox Picture of the Day** wallpapers to **Daily Walls** (an application originally built to curate and maintain 7-wallpaper queues from Wikimedia Commons and Bing).

Google Antigravity analyzed Mozilla Firefox's internal New Tab architecture, identified the underlying backend APIs powering Firefox's native "Picture of the Day" widget, and cleanly integrated it as a first-class wallpaper source alongside Commons and Bing.

---

## 2. Reverse Engineering & Source Discovery

Firefox's desktop browser (New Tab page) dynamically retrieves its daily background through Mozilla's **Merino** service:
- **Daily JSON API**: `https://merino.services.mozilla.com/api/v1/rss/picture-of-the-day`
  - Returns current published date, title, description, photographer/author, file page on Commons, license label (`CC BY-SA 4.0`), and image URLs.
- **High-Resolution CDN**: `https://prod-images.merino.prod.webservices.mozgcp.net/wikimedia_potd/{YYYY-MM-DD}/hi_res.webp`
  - Delivers 4K landscape WebP images (3840px wide).
- **Archive Traversal**:
  - Historical dates follow a deterministic CDN pattern (`wikimedia_potd/<date>/hi_res.webp`), cross-referenced with Commons metadata via `{{Potd/<date>}}` templates.

---

## 3. Summary of Files Changed & Created

| File | Status | Description |
|---|---|---|
| [`firefox.py`](firefox.py) | **Created** | Fetcher, WebP/PNG/JPEG dimension parsers, candidate generator, and download validator. |
| [`tests/test_firefox.py`](tests/test_firefox.py) | **Created** | Unit and integration test suite for the Firefox source and queue mechanics. |
| [`library.py`](library.py) | **Modified** | Multi-source queue isolation, Firefox identity tracking, and refill dispatching. |
| [`app.py`](app.py) | **Modified** | GTK UI source picker, dynamic header labels, and source attribution links. |
| [`main.py`](main.py) | **Modified** | CLI argument choices (`--source firefox`) and help strings. |
| [`README.md`](README.md) | **Modified** | Added source documentation and attribution section. |

---

## 4. Detailed Component Implementation

### A. Dedicated Fetcher Module: [`firefox.py`](firefox.py)

1. **Pure-Python WebP/PNG/JPEG Dimension Parsing**:
   - [`webp_dimensions(path)`](firefox.py#L35-L54): Decodes canvas width and height directly from standard WebP `VP8`, `VP8L`, and `VP8X` chunk headers using Python's standard library.
   - [`png_dimensions(path)`](firefox.py#L57-L65): Decodes dimensions from PNG `IHDR` chunks.
   - [`image_dimensions(path)`](firefox.py#L68-L82): Unified format detector that checks magic bytes and extracts dimensions without third-party binary dependencies.

2. **Deduplication Identity**:
   - [`firefox_identity(url)`](firefox.py#L29-L32): Extracts canonical dates (`YYYY-MM-DD`) from Merino URLs so that resolution variants and re-queries share a persistent identity.

3. **Fetcher Implementation**:
   - [`FirefoxFetcher`](firefox.py#L85-L228):
     - `wallpaper_candidates()` queries Merino's live JSON feed for today's image, then walks backward over recent dates to supply candidates for the 7-wallpaper queue.
     - `download_wallpaper()` verifies allowed HTTPS hosts (`*.mozilla.net`, `*.mozilla.com`, `*.wikimedia.org`), checks file size boundaries (up to 50 MiB), inspects dimensions (ensuring 4K landscape format: `width >= 1920`, `height >= 1080`, `width > height`), and generates [`Wallpaper`](fetcher.py#L101-L116) instances with `source="firefox"`.

---

### B. Library & Storage Engine: [`library.py`](library.py)

1. **Source Extension**:
   - Updated [`WallpaperLibrary.source`](library.py#L130-L135) and [`WallpaperLibrary.select_source`](library.py#L137-L141) to validate and store `"firefox"` in the SQLite settings table.

2. **Isolated Queue Management**:
   - Updated [`WallpaperLibrary.snapshot`](library.py#L143-L162) with `_match_source()` so each source maintains an independent 7-wallpaper queue without crosstalk:
     - Commons: `wallpaper.source not in {"bing", "firefox"}`
     - Bing: `wallpaper.source == "bing"`
     - Firefox: `wallpaper.source == "firefox"`

3. **Queue Refill**:
   - Added [`WallpaperLibrary._fill_firefox`](library.py#L329-L347) called by [`fill()`](library.py#L262-L323) to populate empty slots from `FirefoxFetcher`.
   - Prevented Commons theme-rebalancing logic from archiving Firefox wallpapers.

4. **Shared Safety & Protections**:
   - Firefox wallpapers automatically benefit from the app's existing 10-day retention cleanup, SHA-256 byte-duplicate prevention, and active desktop background protection.

---

### C. Graphical Interface: [`app.py`](app.py)

1. Added `firefox` ("Firefox · Picture of the day") to the header `Gtk.ComboBoxText` source picker.
2. Updated `_show_snapshot()` to display header label `"4K · FIREFOX PICTURE OF THE DAY"`.
3. Updated `_show_details()` to point the source link to Commons or Firefox based on attribution metadata.

---

### D. Command Line Interface: [`main.py`](main.py)

1. Extended `--source` choices to `("commons", "bing", "firefox")`.
2. Updated CLI description to: `"Daily Walls: unique landscape wallpapers from Bing, Firefox, and Wikimedia Commons"`.

---

### E. Test Suite: [`tests/test_firefox.py`](tests/test_firefox.py)

Created a comprehensive test suite covering:
- `test_identity_extracts_date`: Verifies date matching and URL variant deduplication.
- `test_webp_vp8x_dimensions` & `test_webp_vp8_dimensions`: Validates chunk parsing on synthesized WebP byte streams.
- `test_invalid_and_corrupt_images`: Validates rejection of non-WebP/truncated data.
- `test_wallpaper_candidates_from_merino`: Tests mocked Merino API JSON ingestion and fallback candidate generation.
- `test_firefox_source_separate_queue_and_retention`: Confirms that selecting the Firefox source creates an independent queue, preserves images when toggling sources, and retains active wallpapers past the 10-day mark.

All 39 unit tests in the repository pass cleanly:
```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

---

## 5. Usage Instructions

### Graphical Interface
```sh
/usr/bin/python3 main.py --gui
```
Select **Firefox · Picture of the day** from the top-left dropdown.

### CLI
```sh
# Apply an unused Firefox Picture of the Day wallpaper:
/usr/bin/python3 main.py --source firefox

# Download and refill the 7-wallpaper Firefox queue in the background:
/usr/bin/python3 main.py --source firefox --maintain
```
