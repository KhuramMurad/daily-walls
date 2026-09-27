"""Wikimedia wallpaper fetching, using only Python 3.10's standard library.

Requests use a descriptive project User-Agent by default; callers may override
it with their own contact identifier. No desktop changes occur here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from http.client import HTTPException
import json
import logging
import os
from pathlib import Path
import random
import re
import tempfile
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = "daily-walls/1.1.2 (https://github.com/KhuramMurad/daily-walls)"

LOG = logging.getLogger(__name__)
API_URL = "https://commons.wikimedia.org/w/api.php"
GATEWAY_URL = "https://api.wikimedia.org/core/v1/commons/"
SHARED_API_URL = "https://en.wikipedia.org/w/api.php"
FEATURED_CATEGORY = "Category:Featured_pictures_on_Wikimedia_Commons"
WALLPAPER_CATEGORIES = (
    "Commons featured desktop backgrounds",
    "Widescreen desktop backgrounds",
    "Computer wallpapers",
)
WALLPAPER_THEMES = {
    "Mountains": "mountain",
    "Coastlines": "coast",
    "Lakes & waterfalls": "lake",
    "Forests": "forest",
    "Deserts": "desert",
    "Architecture": "skyline",
    "Space": "nebula",
}
THEME_PATTERNS = {
    "Mountains": r"mountain|\bmount\b|alps|peak|glacier|himalaya|andes|massif",
    "Coastlines": r"coast|beach|ocean|shore|\bsea\b|seascape|\bbay\b|island|cliff",
    "Lakes & waterfalls": r"\blake\b|lakes|waterfall|\bfalls\b|cascade|cascada|cachoeira|lagoon",
    "Forests": r"forest|woodland|\bwoods\b|trees|rainforest|jungle|bosque|forêt",
    "Deserts": r"desert|dune|sossusvlei|sahara|namib|\bwadi\b|badlands",
    "Architecture": r"building|architecture|cityscape|skyline|castle|cathedral|church|mosque|temple|palace|bridge|skyscraper|\bcity\b",
    "Space": r"nebula|galaxy|galaxies|milky way|star cluster|astronomy|outer space|cosmos",
}


def matches_theme(info: dict[str, Any], theme: str) -> bool:
    metadata = info.get("extmetadata", {})
    categories = metadata.get("Categories", {}).get("value", "")
    description = metadata.get("ImageDescription", {}).get("value", "")
    # Search can match an author's name or boilerplate. Require subject evidence
    # and reject animal-centered results instead of relabeling them as scenery.
    categories = "|".join(name for name in str(categories).split("|")
                          if not re.search(r"by |user:|taken with|images from|photographs by", name, re.I))
    subject = str(info.get("title", "")) + " " + _plain(str(description)) + " " + str(categories)
    animals = (r"\b(aves|mammalia|insecta|animals|birds|mammals|canis|felis|panthera|sciuridae|xerus|rodents|"
               r"arthropods|butterflies|spiders|snakes|amphibians|reptiles|retriever|fox|foxes|vulpes|wolf|wolves|"
               r"bear|tiger|lion|horse|equus|canidae|felidae|carnivora|cervidae|primates|beetle|macrophotography|"
               r"zalophus|pinnipedia|seals|seal|sea lion|lobo marino)\b")
    animals += r"|\b(elephant\w*|loxodonta|elephantidae|zebra\w*|giraffe\w*|camel\w*|buffalo\w*|bovidae|hippo\w*|cattle|deer)\b"
    if theme == "Lakes & waterfalls" and not re.search(
            THEME_PATTERNS[theme] + r"|\blac\b|\blago\b|\bloch\b|\btarn\b|\w+see\b", str(info.get("title", "")), re.I):
        # A nearby lake named in location categories does not make a night-sky
        # portrait a lake photograph. Prefer an explicitly named water subject.
        return False
    return re.search(animals, subject, re.I) is None and re.search(THEME_PATTERNS[theme], subject, re.I) is not None


def premium_candidate(info: dict[str, Any]) -> bool:
    assessments = info.get("extmetadata", {}).get("Assessments", {}).get("value", "").split("|")
    categories = info.get("extmetadata", {}).get("Categories", {}).get("value", "")
    genre = str(info.get("title", "")) + " " + str(categories)
    excluded = re.search(r"\b(maps?|atlas|diagrams?|schematics?|engravings?|lithographs?|drawings?|"
                         r"scans?|posters?|screenshots?|collages?|charts?|newspapers?|paintings|illustrations)\b", genre, re.I)
    return (info.get("width", 0) >= 3840 and info.get("height", 0) >= 2160
            and info["width"] > info["height"]
            and bool({"featured", "quality"}.intersection(assessments))
            and info.get("size", 0) <= MAX_DOWNLOAD and excluded is None)


def quality_score(info: dict[str, Any]) -> tuple[bool, float, float]:
    assessments = info.get("extmetadata", {}).get("Assessments", {}).get("value", "").split("|")
    ratio = info["width"] / info["height"]
    return ("featured" in assessments, -abs(ratio - 16 / 9), min(info["width"] * info["height"], 32_000_000))
MIME_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_DOWNLOAD = 150 * 1024 * 1024


class FetchError(RuntimeError):
    """An API request, selection, download, or cache write failed."""


@dataclass(frozen=True)
class Wallpaper:
    path: Path
    title: str
    photographer: str
    license: str
    license_url: str
    description_url: str
    image_url: str
    width: int
    height: int
    source: str
    attribution: str = ""
    theme: str = ""
    quality: str = ""


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class _ArtistText(HTMLParser):
    """Prefer explicitly linked creator names to surrounding license notices."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.names: list[str] = []
        self.current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and (attributes.get("title") or "").startswith(("User:", "Creator:")):
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None:
            name = " ".join("".join(self.current).split())
            if name and name not in self.names:
                self.names.append(name)
            self.current = None


def _artist(value: str) -> str:
    parser = _ArtistText()
    parser.feed(value)
    return ", ".join(parser.names) or _plain(value)


def _plain(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def _atomic_json(path: Path, value: object) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Fetcher:
    """Select landscape JPEG/PNG/WebP originals of at least 1920 by 1080.

    Daily selections are cached separately for POTD and featured mode. Force
    bypasses both selection and file caches; POTD may still be the same image.
    Dates use UTC, matching Commons. Featured selection shuffles all direct
    file members, following API continuation (not just the first page).
    """

    def __init__(self, user_agent: str, cache_dir: Path | None = None,
                 timeout: float = 30.0,
                 progress: Callable[[int, int], None] | None = None) -> None:
        if not user_agent.strip() or "\n" in user_agent or "\r" in user_agent:
            raise ValueError("Provide a descriptive User-Agent with contact information")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.user_agent = user_agent
        self.cache_dir = (cache_dir or Path.home() / ".cache/wiki-wallpaper").expanduser().resolve()
        self.timeout = timeout
        self._use_gateway = False
        self.progress = progress

    def _json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            raise FetchError("Wikimedia returned an invalid JSON response")
        if "error" in data:
            raise FetchError(f"Wikimedia API error: {data['error']}")
        return data

    def _gateway_api(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """Use Wikimedia's official gateway if the Commons host cannot connect.

        Metadata is served by Wikipedia's shared Commons repository, never by
        locally uploaded Wikipedia files. Search fallback samples up to 100
        featured category results; the normal API enumerates the full category.
        """
        if parameters.get("action") == "expandtemplates":
            match = re.fullmatch(r"\{\{Potd/(\d{4}-\d{2}-\d{2})\}\}", parameters["text"])
            if match is None:
                raise FetchError("Unsupported POTD template")
            title = "Template:Potd/" + match[1]
            data = self._json(GATEWAY_URL + "page/" + quote(title, safe=""))
            source = re.sub(r"<!--.*?-->", "", data.get("source", ""), flags=re.S)
            filename = re.search(r"\{\{\s*Potd filename\s*\|\s*(?:1\s*=\s*)?([^|{}]+)", source, re.I)
            if filename is None:
                raise FetchError("Cannot read the Commons POTD filename through the gateway")
            return {"expandtemplates": {"wikitext": filename[1].strip()}}
        if parameters.get("list") == "categorymembers":
            query = urlencode({"q": 'file: incategory:"Featured pictures on Wikimedia Commons"', "limit": 100})
            data = self._json(GATEWAY_URL + "search/page?" + query)
            members = [{"title": page["title"]} for page in data.get("pages", [])
                       if page.get("title", "").startswith("File:")]
            return {"query": {"categorymembers": members}}
        if parameters.get("list") == "search":
            query = urlencode({"q": "file: " + parameters["srsearch"], "limit": 100})
            data = self._json(GATEWAY_URL + "search/page?" + query)
            return {"query": {"search": data.get("pages", [])}}
        if parameters.get("prop") == "imageinfo":
            data = self._json(SHARED_API_URL + "?" + urlencode(parameters))
            pages = data.get("query", {}).get("pages", [])
            return {"query": {"pages": [page for page in pages if page.get("imagerepository") == "shared"]}}
        raise FetchError("Unsupported Commons gateway request")

    def _api(self, **parameters: Any) -> dict[str, Any]:
        params = {"format": "json", "formatversion": 2, "maxlag": 5, **parameters}
        try:
            if self._use_gateway:
                data = self._gateway_api(params)
            else:
                try:
                    data = self._json(API_URL + "?" + urlencode(params))
                except HTTPError:
                    # Preserve rate limits and server errors; do not reroute them.
                    raise
                except (URLError, ConnectionError, TimeoutError):
                    LOG.warning("Commons connection unavailable; using Wikimedia's official API gateway")
                    self._use_gateway = True
                    data = self._gateway_api(params)
        except (OSError, URLError, HTTPException, ValueError) as exc:
            raise FetchError(f"Wikimedia API request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise FetchError("Wikimedia returned an invalid JSON response")
        if "error" in data:
            raise FetchError(f"Wikimedia API error: {data['error']}")
        if "warnings" in data:
            LOG.warning("Wikimedia API warning: %s", data["warnings"])
        return data

    def _potd_title(self, day: str) -> str:
        data = self._api(action="expandtemplates", text="{{Potd/" + day + "}}", prop="wikitext")
        title = data.get("expandtemplates", {}).get("wikitext", "").strip()
        if not title or any(char in title for char in "{}<>[]|\n"):
            raise FetchError(f"No usable POTD filename for {day}")
        return title if title.startswith("File:") else "File:" + title

    def _featured_titles(self) -> list[str]:
        titles: list[str] = []
        continuation: dict[str, Any] = {}
        while True:
            data = self._api(action="query", list="categorymembers", cmtitle=FEATURED_CATEGORY,
                             cmtype="file", cmnamespace=6, cmlimit=500, **continuation)
            titles.extend(member["title"] for member in data.get("query", {}).get("categorymembers", []))
            continuation = data.get("continue", {})
            if not continuation:
                break
        random.SystemRandom().shuffle(titles)
        return titles

    def _metadata(self, title: str) -> dict[str, Any] | None:
        records = self._metadata_batch([title])
        return records[0] if records else None

    def _metadata_batch(self, titles: list[str]) -> list[dict[str, Any]]:
        data = self._api(action="query", titles="|".join(titles), prop="imageinfo", redirects=1,
                         iiprop="url|size|mime|extmetadata|sha1", iiextmetadatalanguage="en")
        records: list[dict[str, Any]] = []
        for page in data.get("query", {}).get("pages", []):
            for info in page.get("imageinfo", []):
                width, height = info.get("width", 0), info.get("height", 0)
                if (width > height and width >= 1920 and height >= 1080
                        and info.get("mime") in MIME_SUFFIXES and info.get("url")):
                    records.append({**info, "title": page["title"]})
        return records

    def wallpaper_candidates(self, themes: tuple[str, ...] | None = None) -> Iterator[dict[str, Any]]:
        """Search individual scenic subjects within verified wallpaper categories.

        Start with Commons' curated featured desktop backgrounds for each theme.
        Never fill an unavailable subject slot with more of another subject.
        """
        visited: set[str] = set()
        for theme in themes or tuple(WALLPAPER_THEMES):
            term = WALLPAPER_THEMES[theme]
            for category in WALLPAPER_CATEGORIES:
                continuation: dict[str, Any] = {}
                while True:
                    data = self._api(action="query", list="search", srnamespace=6,
                                     srsearch=f'incategory:"{category}" {term}',
                                     srlimit=100, srsort="relevance", **continuation)
                    titles = [page["title"] for page in data.get("query", {}).get("search", [])]
                    titles = [title for title in titles if title not in visited and not re.search(
                        r"\b(collage|diagram|screenshot|zebras?|elephants?|giraffes?|elk|moths?|birds?|bunker|map|atlas)\b", title, re.I)]
                    visited.update(titles)
                    for start in range(0, len(titles), 20):
                        records = self._metadata_batch(titles[start:start + 20])
                        eligible = []
                        for info in records:
                            membership = info.get("extmetadata", {}).get("Categories", {}).get("value", "")
                            names = {name.replace("_", " ").strip() for name in membership.split("|")}
                            if (names.intersection(WALLPAPER_CATEGORIES) and matches_theme(info, theme)
                                    and premium_candidate(info)):
                                eligible.append(info)
                        for info in sorted(eligible, key=quality_score, reverse=True):
                            yield {**info, "theme": theme}
                    continuation = data.get("continue", {})
                    if not continuation:
                        break

    def download_wallpaper(self, info: dict[str, Any]) -> Wallpaper:
        """Download an inspected queue candidate without writing a daily cache."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._download(info, False)
        return self._wallpaper(info, path, "wallpaper")

    def _download(self, info: dict[str, Any], force: bool) -> Path:
        url = str(info["url"])
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "upload.wikimedia.org":
            raise FetchError(f"Unexpected Wikimedia download URL: {url}")
        target = self.cache_dir / (sha256(url.encode()).hexdigest() + MIME_SUFFIXES[info["mime"]])
        expected = int(info.get("size", 0))
        if not force and target.is_file() and target.stat().st_size > 0:
            if not expected or target.stat().st_size == expected:
                return target
        temporary: Path | None = None
        try:
            request = Request(url, headers={"User-Agent": self.user_agent})
            with urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type and content_type != info["mime"]:
                    raise FetchError(f"Unexpected download Content-Type: {content_type}")
                length_header = response.headers.get("Content-Length")
                length = int(length_header) if length_header is not None else None
                if expected > MAX_DOWNLOAD or (length is not None and length > MAX_DOWNLOAD):
                    raise FetchError("Image exceeds the 150 MiB download limit")
                with tempfile.NamedTemporaryFile(dir=self.cache_dir, delete=False) as stream:
                    temporary = Path(stream.name)
                    total = 0
                    while chunk := response.read(128 * 1024):
                        total += len(chunk)
                        if total > MAX_DOWNLOAD:
                            raise FetchError("Image exceeds the 150 MiB download limit")
                        stream.write(chunk)
                        if self.progress is not None and total % (1024 * 1024) < len(chunk):
                            self.progress(total, expected or length or 0)
                if not total or (length is not None and total != length) or (expected and total != expected):
                    raise FetchError("Image download was empty or incomplete")
            temporary.replace(target)
            return target
        except (OSError, URLError, HTTPException, ValueError) as exc:
            raise FetchError(f"Image download failed: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def fetch(self, *, force: bool = False, featured: bool = False) -> Wallpaper:
        """Fetch a wallpaper, falling back to featured images when POTD fails."""
        try:
            return self._fetch(force=force, featured=featured)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise FetchError(f"Cannot fetch/cache wallpaper: {exc}") from exc

    def _fetch(self, *, force: bool, featured: bool) -> Wallpaper:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).date().isoformat()
        manifest = self.cache_dir / ("featured.json" if featured else "potd.json")
        if not force:
            try:
                record = json.loads(manifest.read_text(encoding="utf-8"))
                if record["day"] == day and record.get("version") == 2:
                    saved = record["wallpaper"]
                    saved["path"] = Path(saved["path"])
                    wallpaper = Wallpaper(**saved)
                    if (wallpaper.path.parent == self.cache_dir and wallpaper.path.is_file()
                            and wallpaper.path.stat().st_size == record["size"]):
                        return wallpaper
            except (OSError, ValueError, KeyError, TypeError):
                LOG.debug("No valid daily wallpaper cache", exc_info=True)
        if not featured:
            try:
                info = self._metadata(self._potd_title(day))
                if info is not None:
                    return self._finish(info, "potd", force, day, manifest)
                LOG.info("POTD is not a supported landscape image; using featured pictures")
            except FetchError as exc:
                LOG.warning("POTD unavailable; using featured pictures: %s", exc)
        titles = self._featured_titles()
        # Bound metadata/download attempts even for a pathological category.
        for title in titles[:100]:
            info = self._metadata(title)
            if info is not None and self._use_gateway:
                assessments = info.get("extmetadata", {}).get("Assessments", {}).get("value", "")
                if "featured" not in assessments.split("|"):
                    continue
            if info is not None:
                try:
                    return self._finish(info, "featured", force, day, manifest)
                except FetchError as exc:
                    LOG.warning("Skipping %s: %s", title, exc)
        raise FetchError("No downloadable landscape featured image found in 100 candidates")

    def _finish(self, info: dict[str, Any], source: str, force: bool,
                day: str, manifest: Path) -> Wallpaper:
        path = self._download(info, force)
        wallpaper = self._wallpaper(info, path, source)
        saved = {**asdict(wallpaper), "path": str(path)}
        _atomic_json(manifest, {"version": 2, "day": day, "size": path.stat().st_size, "wallpaper": saved})
        LOG.info("Downloaded %s (%sx%s)", wallpaper.title, wallpaper.width, wallpaper.height)
        return wallpaper

    @staticmethod
    def _wallpaper(info: dict[str, Any], path: Path, source: str) -> Wallpaper:
        metadata = info.get("extmetadata", {})

        def field(name: str, default: str = "") -> str:
            return _plain(str(metadata.get(name, {}).get("value", default)))

        return Wallpaper(
            path=path, title=str(info["title"]).removeprefix("File:"),
            photographer=_artist(str(metadata.get("Artist", {}).get("value", "Unknown photographer"))),
            license=field("LicenseShortName", "License not specified"),
            license_url=field("LicenseUrl"), description_url=info.get("descriptionurl", ""),
            image_url=info["url"], width=info["width"], height=info["height"], source=source,
            attribution=field("Artist", "Unknown photographer"),
            theme=info.get("theme", ""),
            quality=("featured" if "featured" in field("Assessments").split("|")
                     else "quality" if "quality" in field("Assessments").split("|") else ""),
        )


def fetch_wallpaper(*, force: bool = False, featured: bool = False,
                    user_agent: str | None = None, cache_dir: Path | None = None,
                    timeout: float = 30.0,
                    progress: Callable[[int, int], None] | None = None) -> Wallpaper:
    """Convenience API for argparse callers; raises FetchError on failure.

    Supply user_agent, set WIKI_WALLPAPER_USER_AGENT, or save the identifier in
    $XDG_CONFIG_HOME/wiki-wallpaper/user-agent (default: ~/.config).
    """
    agent = configured_user_agent(user_agent)
    return Fetcher(agent, cache_dir, timeout, progress).fetch(force=force, featured=featured)


def configured_user_agent(user_agent: str | None = None) -> str:
    """Use an explicit, environment, or saved identifier, then the project default."""
    agent = user_agent or os.environ.get("WIKI_WALLPAPER_USER_AGENT", "")
    if not agent.strip():
        config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        try:
            agent = (config_root / "wiki-wallpaper/user-agent").read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError) as exc:
            raise FetchError(f"Cannot read User-Agent configuration: {exc}") from exc
    if not agent.strip():
        return DEFAULT_USER_AGENT
    return agent
