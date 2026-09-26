"""CLI and scheduled maintenance for the seven-wallpaper library."""
from __future__ import annotations

import argparse
import logging
import sqlite3

from fetcher import FetchError
from library import LibraryError, WallpaperLibrary
from notifier import notify_wallpaper
from setter import WallpaperError


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Walls: unique landscape wallpapers from Bing, Firefox, and Wikimedia Commons")
    parser.add_argument("--source", choices=("commons", "bing", "firefox"), help="Select and remember the wallpaper source")
    parser.add_argument("--force", action="store_true", help="Refresh missing queue slots; duplicate history is always preserved")
    parser.add_argument("--featured", action="store_true", help="Compatibility flag; selections now always use desktop-wallpaper categories")
    parser.add_argument("--notify", action="store_true", help="Show attribution when applying a wallpaper")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--gui", action="store_true", help="Open the graphical application")
    modes.add_argument("--maintain", action="store_true", help="Delete expired files and fill seven queue slots without changing the desktop")
    modes.add_argument("--cleanup", action="store_true", help="Delete expired files, keeping the current wallpaper")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.source:
        WallpaperLibrary().select_source(args.source)
    if args.gui:
        from app import WallpaperApplication
        return WallpaperApplication().run([])
    try:
        library = WallpaperLibrary()
        if args.cleanup:
            print(f"Removed {library.cleanup()} expired wallpaper(s).")
            return 0
        snapshot = library.fill(progress=logging.info)
        if args.maintain:
            if snapshot.warning:
                logging.warning(snapshot.warning)
                return 1
            print(f"{len(snapshot.queue)}/7 unique wallpapers ready.")
            return 0
        if not snapshot.queue:
            raise LibraryError(snapshot.warning or "No wallpaper is available. Try again when connected.")
        wallpaper = library.apply(snapshot.queue[0].id)
        if args.notify:
            notify_wallpaper(wallpaper)
        print(f"{wallpaper.title}\n{wallpaper.photographer} · {wallpaper.license}")
        refreshed = library.fill(progress=logging.info)
        if refreshed.warning:
            logging.warning(refreshed.warning)
        return 0
    except (FetchError, WallpaperError, LibraryError, OSError, sqlite3.Error, ValueError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
