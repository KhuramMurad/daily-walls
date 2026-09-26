"""Persistent seven-image queue, permanent duplicate history and ten-day retention."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterator
from urllib.parse import unquote, urlsplit, urlunsplit

from fetcher import FetchError, Fetcher, Wallpaper, WALLPAPER_THEMES, configured_user_agent
from bing import BingFetcher, bing_identity
from firefox import FirefoxFetcher, firefox_identity
from notifier import notify_removal
from setter import set_wallpaper

LOG = logging.getLogger(__name__)
QUEUE_SIZE = 7
RETENTION_SECONDS = 10 * 24 * 60 * 60


class LibraryError(RuntimeError):
    """The persistent library cannot be safely updated."""


@dataclass(frozen=True)
class QueueEntry:
    id: str
    wallpaper: Wallpaper
    saved_at: float
    applied_at: float | None

    @property
    def expires_at(self) -> float:
        return self.saved_at + RETENTION_SECONDS


@dataclass(frozen=True)
class Snapshot:
    queue: tuple[QueueEntry, ...]
    retained_count: int
    notice: str
    warning: str = ""


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identities(title: str, url: str, sha1: str = "") -> set[str]:
    parsed = urlsplit(url)
    if parsed.netloc == "www.bing.com" and parsed.path == "/th":
        return {"bing:" + bing_identity(url)}
    if "merino" in parsed.netloc or "wikimedia_potd" in parsed.path:
        return {"firefox:" + firefox_identity(url)}
    canonical = unquote(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
    keys = {"title:" + title.removeprefix("File:").replace("_", " ").casefold(), "url:" + canonical}
    if sha1:
        keys.add("sha1:" + sha1)
    return keys


class WallpaperLibrary:
    def __init__(self, data_dir: Path | None = None, cache_dir: Path | None = None,
                 clock: Callable[[], float] = time.time,
                 removal_notice: Callable[[int], object] = notify_removal) -> None:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        self.data_dir = (data_dir or base / "wiki-wallpaper").expanduser().resolve()
        self.cache_dir = (cache_dir or Path.home() / ".cache/wiki-wallpaper").expanduser().resolve()
        self.images_dir = self.cache_dir / "queue"
        self.clock = clock
        self.removal_notice = removal_notice
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.data_dir / "library.sqlite3"
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, wallpaper TEXT NOT NULL, saved_at REAL NOT NULL,
                    applied_at REAL, removed_at REAL);
                CREATE TABLE IF NOT EXISTS seen (identity TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, message TEXT NOT NULL);
            ''')
            columns = {row[1] for row in db.execute("PRAGMA table_info(images)")}
            if "archived_at" not in columns:
                db.execute("ALTER TABLE images ADD COLUMN archived_at REAL")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with (self.data_dir / "library.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LibraryError("The wallpaper queue is already updating. Please try again shortly.") from exc
            yield

    @staticmethod
    def _entry(row: sqlite3.Row) -> QueueEntry:
        value = json.loads(row["wallpaper"])
        value["path"] = Path(value["path"])
        return QueueEntry(row["id"], Wallpaper(**value), row["saved_at"], row["applied_at"])

    def _safe_path(self, path: Path) -> bool:
        return (not path.is_symlink() and path.resolve().parent in {self.cache_dir, self.images_dir}
                and re.fullmatch(r"[0-9a-f]{64}\.(jpg|png|webp)", path.name) is not None)

    @property
    def source(self) -> str:
        with self._db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='source'").fetchone()
        return row[0] if row and row[0] in {"commons", "bing", "firefox"} else "commons"

    def select_source(self, source: str) -> None:
        if source not in {"commons", "bing", "firefox"}:
            raise ValueError("Unknown wallpaper source")
        with self._lock(), self._db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('source', ?)", (source,))

    def snapshot(self, warning: str = "") -> Snapshot:
        with self._db() as db:
            rows = db.execute("SELECT * FROM images WHERE removed_at IS NULL ORDER BY saved_at, id").fetchall()
            latest = db.execute("SELECT message FROM events ORDER BY id DESC LIMIT 1").fetchone()
        source = self.source
        entries = [self._entry(row) for row in rows]
        present = [entry for entry in entries if entry.wallpaper.path.is_file()]
        archived = {row["id"] for row in rows if row["archived_at"] is not None}
        def _match_source(entry_source: str) -> bool:
            if source == "bing":
                return entry_source == "bing"
            if source == "firefox":
                return entry_source == "firefox"
            return entry_source not in {"bing", "firefox"}
        queue = tuple(sorted((entry for entry in present if entry.applied_at is None
                             and _match_source(entry.wallpaper.source)
                             and entry.expires_at > self.clock() and entry.id not in archived),
                            key=lambda entry: list(WALLPAPER_THEMES).index(entry.wallpaper.theme)
                            if entry.wallpaper.theme in WALLPAPER_THEMES else 99))
        return Snapshot(queue[:QUEUE_SIZE], len(present), latest[0] if latest else "", warning)

    def _known(self, keys: set[str]) -> bool:
        with self._db() as db:
            return any(db.execute("SELECT 1 FROM seen WHERE identity=?", (key,)).fetchone() for key in keys)

    def _remember(self, keys: set[str]) -> None:
        with self._db() as db:
            db.executemany("INSERT OR IGNORE INTO seen VALUES (?)", [(key,) for key in keys])

    def _store(self, wallpaper: Wallpaper, content: str, keys: set[str],
               saved_at: float, applied_at: float | None = None) -> None:
        serialized = json.dumps({**asdict(wallpaper), "path": str(wallpaper.path)})
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO images(id, wallpaper, saved_at, applied_at, removed_at) VALUES (?, ?, ?, ?, NULL)",
                       (content, serialized, saved_at, applied_at))
            db.executemany("INSERT OR IGNORE INTO seen VALUES (?)", [(key,) for key in keys])

    def _import_legacy(self) -> None:
        with self._db() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='legacy_imported'").fetchone():
                return
        records: dict[str, dict[str, Any]] = {}
        for name in ("potd.json", "featured.json"):
            try:
                value = json.loads((self.cache_dir / name).read_text())["wallpaper"]
                records[value["path"]] = value
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for path in self.cache_dir.iterdir():
            if not self._safe_path(path) or not path.is_file():
                continue
            value = records.get(str(path))
            if value:
                value = {**value, "path": path}
                wallpaper = Wallpaper(**value)
            else:
                wallpaper = Wallpaper(path, path.name, "Previously downloaded", "", "", "", "", 0, 0, "legacy")
            content = _digest(path)
            keys = identities(wallpaper.title, wallpaper.image_url) | {"sha256:" + content}
            saved = path.stat().st_mtime
            self._store(wallpaper, content, keys, saved, saved)
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('legacy_imported', '1')")

    def _protected_paths(self) -> set[Path]:
        # The persisted current image remains protected even if the user service
        # cannot connect to the desktop. GNOME/Cinnamon can also reveal a legacy
        # image selected outside this version of the app.
        protected: set[Path] = set()
        with self._db() as db:
            row = db.execute("SELECT i.* FROM images i JOIN settings s ON i.id=s.value WHERE s.key='current'").fetchone()
        if row:
            protected.add(self._entry(row).wallpaper.path.resolve())
        try:
            import subprocess
            for schema in ("org.gnome.desktop.background", "org.cinnamon.desktop.background"):
                for key in ("picture-uri", "picture-uri-dark"):
                    result = subprocess.run(["gsettings", "get", schema, key], capture_output=True,
                                            text=True, timeout=3)
                    value = result.stdout.strip().strip("'\"")
                    if result.returncode == 0 and value.startswith("file://"):
                        protected.add(Path(unquote(urlsplit(value).path)).resolve())
        except (OSError, subprocess.SubprocessError):
            pass
        return protected

    def _cleanup(self) -> int:
        protected = self._protected_paths()
        with self._db() as db:
            rows = db.execute("SELECT * FROM images WHERE removed_at IS NULL AND saved_at<=?",
                              (self.clock() - RETENTION_SECONDS,)).fetchall()
        expired = [self._entry(row) for row in rows
                   if self._entry(row).wallpaper.path.resolve() not in protected]
        expired = [entry for entry in expired if self._safe_path(entry.wallpaper.path)]
        present = [entry for entry in expired if entry.wallpaper.path.exists()]
        if present:
            self.removal_notice(len(present))
        removed = 0
        for entry in expired:
            try:
                existed = entry.wallpaper.path.exists()
                entry.wallpaper.path.unlink(missing_ok=True)
            except OSError as exc:
                LOG.warning("Cannot remove expired wallpaper %s: %s", entry.wallpaper.path, exc)
                continue
            with self._db() as db:
                db.execute("UPDATE images SET removed_at=? WHERE id=?", (self.clock(), entry.id))
            removed += int(existed)
        if removed:
            message = f"{removed} expired wallpaper(s) permanently removed after 10 days. Your current wallpaper was kept."
            with self._db() as db:
                db.execute("INSERT INTO events(timestamp,message) VALUES (?,?)", (self.clock(), message))
            LOG.info(message)
        return removed

    def cleanup(self) -> int:
        with self._lock():
            self._import_legacy()
            return self._cleanup()

    def fill(self, progress: Callable[[str], None] | None = None,
             fetcher: Fetcher | None = None) -> Snapshot:
        """Fill seven pending slots. Used images stay on disk until ten days old."""
        report = progress or (lambda message: None)
        with self._lock():
            self._import_legacy()
            self._cleanup()
            if self.source == "bing":
                return self._fill_bing(report, fetcher)
            if self.source == "firefox":
                return self._fill_firefox(report, fetcher)
            # Retire the old unbalanced queue without deleting saved files or
            # forgetting their duplicate identities. Existing themed slots stay.
            with self._db() as db:
                rows = db.execute("SELECT * FROM images WHERE applied_at IS NULL AND removed_at IS NULL AND archived_at IS NULL").fetchall()
                occupied: set[str] = set()
                for row in rows:
                    wallpaper = self._entry(row).wallpaper
                    if wallpaper.source in {"bing", "firefox"}:
                        continue
                    theme = wallpaper.theme
                    if (theme not in WALLPAPER_THEMES or theme in occupied
                            or wallpaper.quality not in {"featured", "quality"}
                            or wallpaper.width < 3840 or wallpaper.height < 2160):
                        db.execute("UPDATE images SET archived_at=? WHERE id=?", (self.clock(), row["id"]))
                    else:
                        occupied.add(theme)
            pending = len(self.snapshot().queue)
            if pending >= QUEUE_SIZE:
                return self.snapshot()
            report(f"Finding unique desktop wallpapers… {pending}/{QUEUE_SIZE} ready")
            fetcher = fetcher or Fetcher(configured_user_agent(), self.images_dir,
                progress=lambda done, total: report(
                    f"Downloading wallpaper {pending + 1}/{QUEUE_SIZE}: {done / 1048576:.1f}"
                    + (f" / {total / 1048576:.1f} MB" if total else " MB")))
            warning = ""
            try:
                needed = [theme for theme in WALLPAPER_THEMES
                          if theme not in {entry.wallpaper.theme for entry in self.snapshot().queue}]
                for theme in needed:
                    report(f"Finding a {theme.lower()} wallpaper… {pending}/7 ready")
                    for info in fetcher.wallpaper_candidates(themes=(theme,)):
                        if self._accept_candidate(fetcher, info, theme, report, pending):
                            pending += 1
                            report(f"{pending}/{QUEUE_SIZE} unique wallpapers saved")
                            break
                if pending < QUEUE_SIZE:
                    warning = f"{pending}/7 ready. Some subjects have no unseen matching images available; retry later."
            except FetchError as exc:
                warning = f"{pending}/7 ready. {exc}"
            return self.snapshot(warning)

    def _fill_bing(self, report: Callable[[str], None], fetcher: Fetcher | None) -> Snapshot:
        pending = len(self.snapshot().queue)
        if pending >= QUEUE_SIZE:
            return self.snapshot()
        fetcher = fetcher or BingFetcher(configured_user_agent(), self.images_dir)
        warning = ""
        try:
            for info in fetcher.wallpaper_candidates():
                if self._accept_candidate(fetcher, info, info.get("theme", "Bing daily"), report, pending):
                    pending += 1
                    report(f"{pending}/{QUEUE_SIZE} unique wallpapers saved")
                if pending >= QUEUE_SIZE:
                    break
            if pending < QUEUE_SIZE:
                warning = f"{pending}/7 Bing wallpapers ready. No more unseen 4K images in the recent archive; new images arrive daily."
        except FetchError as exc:
            warning = f"{pending}/7 Bing wallpapers ready. {exc}"
        return self.snapshot(warning)

    def _fill_firefox(self, report: Callable[[str], None], fetcher: Fetcher | None) -> Snapshot:
        pending = len(self.snapshot().queue)
        if pending >= QUEUE_SIZE:
            return self.snapshot()
        fetcher = fetcher or FirefoxFetcher(configured_user_agent(), self.images_dir)
        warning = ""
        try:
            for info in fetcher.wallpaper_candidates():
                if self._accept_candidate(fetcher, info, info.get("theme", "Firefox daily"), report, pending):
                    pending += 1
                    report(f"{pending}/{QUEUE_SIZE} unique wallpapers saved")
                if pending >= QUEUE_SIZE:
                    break
            if pending < QUEUE_SIZE:
                warning = f"{pending}/7 Firefox wallpapers ready. No more unseen images in recent daily pictures; new images arrive daily."
        except FetchError as exc:
            warning = f"{pending}/7 Firefox wallpapers ready. {exc}"
        return self.snapshot(warning)

    def _accept_candidate(self, fetcher: Fetcher, info: dict[str, Any], theme: str,
                          report: Callable[[str], None], pending: int) -> bool:
        keys = identities(info["title"], info["url"], info.get("sha1", ""))
        if self._known(keys):
            return False
        report(f"Downloading wallpaper {pending + 1}/{QUEUE_SIZE}…")
        try:
            wallpaper = fetcher.download_wallpaper({**info, "theme": theme})
        except FetchError as exc:
            LOG.warning("Skipping download: %s", exc)
            return False
        content = _digest(wallpaper.path)
        keys.add("sha256:" + content)
        if self._known(keys):
            self._remember(keys)
            with self._db() as db:
                exists = db.execute("SELECT wallpaper FROM images WHERE id=? AND removed_at IS NULL", (content,)).fetchone()
            old_path = Path(json.loads(exists[0])["path"]) if exists else None
            if self._safe_path(wallpaper.path) and wallpaper.path != old_path:
                wallpaper.path.unlink(missing_ok=True)
            return False
        self._store(wallpaper, content, keys, self.clock())
        return True

    def apply(self, image_id: str, setter: Callable[[Path], str] = set_wallpaper) -> Wallpaper:
        with self._lock():
            with self._db() as db:
                row = db.execute("SELECT * FROM images WHERE id=? AND removed_at IS NULL AND archived_at IS NULL", (image_id,)).fetchone()
            if row is None:
                raise LibraryError("This wallpaper is no longer saved. Refresh the queue.")
            entry = self._entry(row)
            if entry.applied_at is not None:
                raise LibraryError("This wallpaper has already been used. Choose an unused wallpaper.")
            if entry.expires_at <= self.clock():
                raise LibraryError("This wallpaper has expired. Refresh the queue.")
            setter(entry.wallpaper.path)
            with self._db() as db:
                db.execute("UPDATE images SET applied_at=? WHERE id=?", (self.clock(), image_id))
                db.execute("INSERT OR REPLACE INTO settings VALUES ('current', ?)", (image_id,))
            self._cleanup()
            return entry.wallpaper

    def skip(self, image_id: str) -> None:
        """Retire an unwanted preview without applying it or deleting its file."""
        with self._lock(), self._db() as db:
            result = db.execute("UPDATE images SET archived_at=? WHERE id=? AND applied_at IS NULL "
                                "AND removed_at IS NULL AND archived_at IS NULL", (self.clock(), image_id))
            if result.rowcount != 1:
                raise LibraryError("This image is no longer in the queue. Refresh the queue.")
