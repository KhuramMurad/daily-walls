"""Bing daily wallpapers from the public, undocumented homepage archive."""
from __future__ import annotations

from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from fetcher import Fetcher, FetchError, Wallpaper

MARKETS = ('en-US', 'en-GB', 'en-IN', 'en-CA', 'en-AU', 'de-DE', 'fr-FR', 'ja-JP')


def bing_identity(url: str) -> str:
    """Regional captions/resolutions of the same OHR photograph share an ID."""
    match = re.search(r'OHR\.([^&]+?)_[A-Z]{2}-[A-Z]{2}\d+', url)
    return match[1].casefold() if match else url


def jpeg_dimensions(path: Path) -> tuple[int, int]:
    with path.open('rb') as stream:
        if stream.read(2) != b'\xff\xd8':
            raise FetchError('Bing did not return a JPEG image')
        while True:
            if stream.read(1) != b'\xff':
                raise FetchError('Invalid JPEG header')
            marker = stream.read(1)
            while marker == b'\xff':
                marker = stream.read(1)
            if not marker or marker[0] in (0xD9, 0xDA):
                raise FetchError('JPEG has no image dimensions')
            size = int.from_bytes(stream.read(2), 'big')
            if size < 2:
                raise FetchError('Invalid JPEG segment')
            if marker[0] in (0xC0, 0xC1, 0xC2):
                data = stream.read(5)
                if len(data) != 5:
                    raise FetchError('Truncated JPEG dimensions')
                return int.from_bytes(data[3:5], 'big'), int.from_bytes(data[1:3], 'big')
            stream.seek(size - 2, 1)


class BingFetcher(Fetcher):
    def wallpaper_candidates(self, themes: tuple[str, ...] | None = None) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        succeeded = False
        for market in MARKETS:
            try:
                data = self._json('https://www.bing.com/HPImageArchive.aspx?' + urlencode(
                    {'format': 'js', 'idx': 0, 'n': 8, 'mkt': market}))
                images = data.get('images')
                if not isinstance(images, list):
                    raise FetchError('Bing returned an invalid image archive')
                succeeded = True
            except (OSError, ValueError, HTTPException, FetchError):
                continue
            for item in images:
                if not isinstance(item, dict) or item.get('wp') is not True:
                    continue
                base = str(item.get('urlbase', ''))
                if not re.fullmatch(r'/th\?id=OHR\.[A-Za-z0-9_.-]+', base):
                    continue
                url = 'https://www.bing.com' + base + '_UHD.jpg'
                identity = bing_identity(url)
                if identity in seen:
                    continue
                seen.add(identity)
                yield {'title': str(item.get('title') or item.get('copyright') or identity),
                       'url': url, 'copyright': str(item.get('copyright', 'Bing daily image')),
                       'descriptionurl': str(item.get('copyrightlink', 'https://www.bing.com')),
                       'theme': str(item.get('startdate', 'Bing daily'))}
        if not succeeded:
            raise FetchError('Cannot reach the Bing daily-image archive. Try again later.')

    def download_wallpaper(self, info: dict[str, Any]) -> Wallpaper:
        url = info['url']
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'www.bing.com' or parsed.path != '/th':
            raise FetchError('Unexpected Bing image URL')
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / (sha256(url.encode()).hexdigest() + '.jpg')
        temporary: Path | None = None
        try:
            with urlopen(Request(url, headers={'User-Agent': self.user_agent}), timeout=self.timeout) as response:
                if response.headers.get_content_type() != 'image/jpeg':
                    raise FetchError('Bing returned an unsupported image type')
                total = int(response.headers.get('Content-Length') or 0)
                if total > 50 * 1024 * 1024:
                    raise FetchError('Bing image is too large')
                with tempfile.NamedTemporaryFile(dir=self.cache_dir, delete=False) as stream:
                    temporary = Path(stream.name)
                    count = 0
                    while block := response.read(1024 * 1024):
                        count += len(block)
                        if count > 50 * 1024 * 1024:
                            raise FetchError('Bing image is too large')
                        stream.write(block)
                        if self.progress:
                            self.progress(count, total)
                if not count or (total and count != total):
                    raise FetchError('Incomplete Bing download')
            width, height = jpeg_dimensions(temporary)
            if width < 3840 or height < 2160 or width <= height:
                raise FetchError('Bing image does not meet the 4K landscape requirement')
            temporary.replace(target)
            credit = info['copyright']
            return Wallpaper(target, info['title'], credit, 'Wallpaper use only', '',
                             info['descriptionurl'], url, width, height, 'bing',
                             attribution=credit, theme=info['theme'], quality='Bing daily')
        except (OSError, ValueError, HTTPException) as exc:
            raise FetchError(f'Cannot download Bing wallpaper: {exc}') from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
