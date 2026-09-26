"""Firefox Picture of the Day wallpapers via Mozilla's Merino service.

Implemented by Google Antigravity.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from http.client import HTTPException
import logging
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bing import jpeg_dimensions
from fetcher import Fetcher, FetchError, Wallpaper

LOG = logging.getLogger(__name__)
MERINO_POTD_URL = "https://merino.services.mozilla.com/api/v1/rss/picture-of-the-day"
MERINO_CDN_BASE = "https://prod-images.merino.prod.webservices.mozgcp.net/wikimedia_potd"
MIME_MAP = {
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


def validate_image_url(url: str) -> None:
    """Allow only trusted HTTPS image hosts, including Merino's image CDN."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    domains = ("mozilla.net", "mozilla.com", "wikimedia.org")
    trusted = host == "prod-images.merino.prod.webservices.mozgcp.net" or any(
        host == domain or host.endswith("." + domain) for domain in domains)
    if (parsed.scheme != "https" or not trusted or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)):
        raise FetchError(f"Unexpected Firefox image URL: {url}")


class _ImageRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verified_dimensions(path: Path) -> tuple[int, int]:
    """Verify structure and decode pixels before admitting a downloaded image."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise FetchError("Firefox image validation requires Pillow (python3-pil)") from exc
    try:
        with Image.open(path) as img:
            if img.format not in {"WEBP", "JPEG", "PNG"}:
                raise FetchError("Unsupported Firefox image format")
            img.verify()
        with Image.open(path) as img:
            img.load()
            return img.size
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise FetchError(f"Invalid Firefox image: {exc}") from exc


def firefox_identity(url: str) -> str:
    """Extract date identity from Merino POTD URLs."""
    match = re.search(r"wikimedia_potd/(\d{4}-\d{2}-\d{2})", url)
    return match[1] if match else url


def webp_dimensions(path: Path) -> tuple[int, int]:
    """Parse WebP width and height from VP8, VP8L, or VP8X chunk headers."""
    with path.open("rb") as stream:
        header = stream.read(30)
        if len(header) < 30 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            raise FetchError("Not a valid WebP image")
        format_tag = header[12:16]
        if format_tag == b"VP8 ":
            width = int.from_bytes(header[26:28], "little") & 0x3FFF
            height = int.from_bytes(header[28:30], "little") & 0x3FFF
            return width, height
        if format_tag == b"VP8L":
            val = int.from_bytes(header[21:25], "little")
            width = (val & 0x3FFF) + 1
            height = ((val >> 14) & 0x3FFF) + 1
            return width, height
        if format_tag == b"VP8X":
            width = int.from_bytes(header[24:27], "little") + 1
            height = int.from_bytes(header[27:30], "little") + 1
            return width, height
        raise FetchError(f"Unsupported WebP format chunk: {format_tag!r}")


def png_dimensions(path: Path) -> tuple[int, int]:
    """Parse PNG width and height from IHDR chunk."""
    with path.open("rb") as stream:
        header = stream.read(24)
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            raise FetchError("Not a valid PNG image")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        return width, height


def image_dimensions(path: Path) -> tuple[int, int]:
    """Detect image format and return (width, height) without external dependencies."""
    with path.open("rb") as stream:
        magic = stream.read(12)
    if magic[:2] == b"\xff\xd8":
        return jpeg_dimensions(path)
    if magic[:8] == b"\x89PNG\r\n\x1a\n":
        return png_dimensions(path)
    if magic[:4] == b"RIFF" and magic[8:12] == b"WEBP":
        return webp_dimensions(path)
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception:
        raise FetchError("Unsupported or unrecognized image format")


class FirefoxFetcher(Fetcher):
    """Fetch daily Picture of the Day wallpapers from Mozilla Firefox / Merino."""

    def wallpaper_candidates(self, themes: tuple[str, ...] | None = None) -> Iterator[dict[str, Any]]:
        seen_dates: set[str] = set()
        succeeded = False

        # 1. First, fetch today's POTD from Merino's JSON API
        today_date = datetime.now(timezone.utc).date().isoformat()
        try:
            data = self._json(MERINO_POTD_URL)
            if isinstance(data, dict) and data.get("high_res_image_url"):
                succeeded = True
                pub_date = str(data.get("published_date") or today_date)
                seen_dates.add(pub_date)
                desc = str(data.get("description") or data.get("title") or f"Firefox POTD ({pub_date})")
                yield {
                    "title": desc,
                    "url": str(data["high_res_image_url"]),
                    "photographer": str(data.get("author") or "Unknown photographer"),
                    "copyright": str(data.get("author") or "Unknown photographer"),
                    "license": str(data.get("license_label") or "Creative Commons"),
                    "license_url": str(data.get("license_link") or ""),
                    "descriptionurl": str(data.get("file_page") or "https://commons.wikimedia.org"),
                    "theme": pub_date,
                    "quality": "Firefox daily",
                }
        except (OSError, ValueError, HTTPException, FetchError) as exc:
            LOG.warning("Could not reach Merino Picture of the Day endpoint: %s", exc)

        # 2. Iterate through recent days for queue candidates
        base_day = datetime.now(timezone.utc).date()
        for offset in range(1, 35):
            day_str = (base_day - timedelta(days=offset)).isoformat()
            if day_str in seen_dates:
                continue
            seen_dates.add(day_str)
            hi_res_url = f"{MERINO_CDN_BASE}/{day_str}/hi_res.webp"

            # Attempt to retrieve Commons metadata for the POTD of that day
            title = f"Firefox Picture of the Day ({day_str})"
            photographer = "Wikimedia Commons / Mozilla Firefox"
            license_name = "Creative Commons"
            license_url = ""
            desc_url = "https://commons.wikimedia.org"

            try:
                potd_file = self._potd_title(day_str)
                meta = self._metadata(potd_file)
                if meta:
                    ext = meta.get("extmetadata", {})
                    title = str(meta.get("title", "")).removeprefix("File:") or title
                    photographer = ext.get("Artist", {}).get("value", photographer)
                    # clean HTML from artist name
                    clean_artist = re.sub(r"<[^>]+>", "", photographer).strip()
                    if clean_artist:
                        photographer = clean_artist
                    license_name = ext.get("LicenseShortName", {}).get("value", license_name)
                    license_url = ext.get("LicenseUrl", {}).get("value", license_url)
                    desc_url = meta.get("descriptionurl", desc_url)
                    succeeded = True
            except (FetchError, OSError, ValueError):
                pass

            yield {
                "title": title,
                "url": hi_res_url,
                "photographer": photographer,
                "copyright": photographer,
                "license": license_name,
                "license_url": license_url,
                "descriptionurl": desc_url,
                "theme": day_str,
                "quality": "Firefox daily",
            }

        if not succeeded:
            raise FetchError("Cannot reach the Firefox Picture of the Day archive. Try again later.")

    def download_wallpaper(self, info: dict[str, Any]) -> Wallpaper:
        url = info["url"]
        parsed = urlsplit(url)
        validate_image_url(url)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with build_opener(_ImageRedirectHandler()).open(
                    Request(url, headers={"User-Agent": self.user_agent}), timeout=self.timeout) as response:
                validate_image_url(response.geturl())
                content_type = response.headers.get_content_type()
                suffix = MIME_MAP.get(content_type)
                if not suffix:
                    if parsed.path.endswith(".webp"):
                        suffix = ".webp"
                    elif parsed.path.endswith((".jpg", ".jpeg")):
                        suffix = ".jpg"
                    elif parsed.path.endswith(".png"):
                        suffix = ".png"
                    else:
                        raise FetchError(f"Firefox returned an unsupported image type: {content_type}")

                total = int(response.headers.get("Content-Length") or 0)
                if total > 50 * 1024 * 1024:
                    raise FetchError("Firefox image is too large")

                with tempfile.NamedTemporaryFile(dir=self.cache_dir, suffix=suffix, delete=False) as stream:
                    temporary = Path(stream.name)
                    count = 0
                    while block := response.read(1024 * 1024):
                        count += len(block)
                        if count > 50 * 1024 * 1024:
                            raise FetchError("Firefox image is too large")
                        stream.write(block)
                        if self.progress:
                            self.progress(count, total)

                if not count or (total and count != total):
                    raise FetchError("Incomplete Firefox download")

            width, height = verified_dimensions(temporary)
            if width < 1920 or height < 1080 or width <= height:
                raise FetchError("Firefox image does not meet the landscape resolution requirement")

            target = self.cache_dir / (sha256(url.encode()).hexdigest() + suffix)
            temporary.replace(target)
            temporary = None

            credit = info.get("photographer") or info.get("copyright") or "Mozilla Firefox"
            return Wallpaper(
                path=target,
                title=info["title"],
                photographer=credit,
                license=info.get("license", "Creative Commons"),
                license_url=info.get("license_url", ""),
                description_url=info.get("descriptionurl", "https://commons.wikimedia.org"),
                image_url=url,
                width=width,
                height=height,
                source="firefox",
                attribution=credit,
                theme=info.get("theme", "Firefox daily"),
                quality="Firefox daily",
            )
        except (OSError, ValueError, HTTPException) as exc:
            raise FetchError(f"Cannot download Firefox wallpaper: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
