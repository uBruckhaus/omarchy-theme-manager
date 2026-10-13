"""Theme Manager - search wallpapers on the web (wallhaven.cc).

A free-text search the user types is sent to Wallhaven's public search API.
Results are restricted to safe-for-work landscape images of at least
1920x1080. A chosen result is downloaded into the theme's user wallpaper
folder, like a pasted image.

Trust rules (see theme_core):

- Only Wallhaven's fixed hosts are contacted: the API on `wallhaven.cc`,
  thumbnails on `th.wallhaven.cc`, images on `w.wallhaven.cc`. Every URL in
  an API reply must match its exact expected shape, or the result is dropped.
- All requests go through `theme_core.fetch_https` (curl, HTTPS only, no
  redirects, byte cap, deadline). Downloads must be real image data and stay
  under the wallpaper size limit.
- The user's search text is only ever a URL-encoded query parameter.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import theme_core
from i18n import _

API = "https://wallhaven.cc/api/v1/search"
SOURCE = "wallhaven.cc"
MAX_QUERY = 100
MAX_API_BYTES = 1024 * 1024
MAX_THUMB_BYTES = 1024 * 1024
MAX_THUMBS_CACHED = 400

ID_RE = re.compile(r"^[a-z0-9]{1,12}$")
PAGE_RE = re.compile(r"^https://wallhaven\.cc/w/([a-z0-9]{1,12})$")
THUMB_RE = re.compile(r"^https://th\.wallhaven\.cc/(?:small|lg)/[a-z0-9]{2}/([a-z0-9]{1,12})\.(?:jpg|png)$")
FULL_RE = re.compile(r"^https://w\.wallhaven\.cc/full/[a-z0-9]{2}/wallhaven-([a-z0-9]{1,12})\.(jpg|png)$")
RESOLUTION_RE = re.compile(r"^[0-9]{3,5}x[0-9]{3,5}$")

# Fixed filters: safe for work only, general + anime, landscape, sharp enough
# for a desktop.
FILTERS = {"purity": "100", "categories": "110", "atleast": "1920x1080",
           "ratios": "landscape", "sorting": "relevance"}


@dataclass(frozen=True)
class WebWallpaper:
    id: str
    page: str           # https://wallhaven.cc/w/<id>
    thumb: str          # https://th.wallhaven.cc/lg/…
    full: str           # https://w.wallhaven.cc/full/…
    resolution: str
    file_size: int

    @property
    def file_name(self) -> str:
        return f"wallhaven-{self.id}{Path(self.full).suffix}"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "theme-manager" / "wallhaven"


def clean_query(text: str) -> str:
    return " ".join(theme_core.plain_text(text, limit=MAX_QUERY).split())


def search_url(query: str, page: int = 1) -> str:
    params = {"q": clean_query(query), **FILTERS, "page": str(max(1, min(int(page), 999)))}
    return API + "?" + urllib.parse.urlencode(params)


def parse_results(raw: bytes) -> tuple[list[WebWallpaper], int]:
    """(results, last page). Anything not in the exact expected shape is dropped."""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        items = data["data"]
        last_page = int(data.get("meta", {}).get("last_page", 1))
    except (ValueError, KeyError, TypeError, AttributeError):
        return [], 0
    results = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        wid = str(item.get("id", ""))
        page = str(item.get("url", ""))
        thumbs = item.get("thumbs") if isinstance(item.get("thumbs"), dict) else {}
        thumb = str(thumbs.get("lg") or thumbs.get("large") or "")
        full = str(item.get("path", ""))
        resolution = str(item.get("resolution", ""))
        page_m, thumb_m, full_m = PAGE_RE.match(page), THUMB_RE.match(thumb), FULL_RE.match(full)
        if not (ID_RE.match(wid) and page_m and thumb_m and full_m and RESOLUTION_RE.match(resolution)):
            continue
        if not page_m.group(1) == thumb_m.group(1) == full_m.group(1) == wid:
            continue
        if str(item.get("purity", "sfw")) != "sfw":
            continue
        try:
            size = int(item.get("file_size", 0))
        except (TypeError, ValueError):
            size = 0
        results.append(WebWallpaper(wid, page, thumb, full, resolution, size))
    return results, max(1, min(last_page, 999))


def search(query: str, page: int = 1) -> tuple[list[WebWallpaper], int]:
    if not clean_query(query):
        return [], 0
    raw = theme_core.fetch_https(search_url(query, page), MAX_API_BYTES)
    if raw is None:
        raise OSError(_("wallhaven.cc did not answer"))
    return parse_results(raw)


def thumb_path(result: WebWallpaper) -> Path:
    return cache_dir() / "thumbs" / (result.id + Path(result.thumb).suffix)


def fetch_thumb(result: WebWallpaper) -> Path | None:
    path = thumb_path(result)
    if path.is_file() and not path.is_symlink():
        return path
    if not THUMB_RE.match(result.thumb):
        return None
    data = theme_core.fetch_https(result.thumb, MAX_THUMB_BYTES)
    if not data or not theme_core.is_image_data(data):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    theme_core._atomic_write_bytes(path, data)
    _prune_thumbs(path.parent)
    return path


def _prune_thumbs(directory: Path) -> None:
    """Keep the thumbnail cache small: drop the oldest beyond the limit."""
    try:
        files = sorted((f for f in directory.iterdir() if f.is_file() and not f.is_symlink()),
                       key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    for old in files[:-MAX_THUMBS_CACHED]:
        old.unlink(missing_ok=True)


MAX_PREVIEWS_CACHED = 3


def preview_path(result: WebWallpaper) -> Path:
    return cache_dir() / "preview" / result.file_name


def fetch_preview(result: WebWallpaper) -> Path | None:
    """The full image, for a desktop preview before adding it. Kept in a
    small cache (the last few) under the same size and pixel limits as an
    added wallpaper."""
    path = preview_path(result)
    if path.is_file() and not path.is_symlink():
        return path
    if not FULL_RE.match(result.full) or result.file_size > theme_core.MAX_BACKGROUND_BYTES:
        return None
    data = theme_core.fetch_https(result.full, theme_core.MAX_BACKGROUND_BYTES, timeout=60.0)
    if not data or not theme_core.is_image_data(data):
        return None
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    theme_core._atomic_write_bytes(path, data, mode=0o600)
    if theme_core.image_size_problem(path):
        path.unlink(missing_ok=True)
        return None
    try:
        files = sorted((f for f in path.parent.iterdir() if f.is_file() and not f.is_symlink()),
                       key=lambda f: f.stat().st_mtime)
    except OSError:
        files = []
    for old in files[:-MAX_PREVIEWS_CACHED]:
        old.unlink(missing_ok=True)
    return path


def already_added(roots: theme_core.Roots | None, slug: str, result: WebWallpaper) -> bool:
    roots = roots or theme_core.default_roots()
    folder = roots.user_backgrounds / slug
    return any((d / result.file_name).exists() for d in (folder, folder / theme_core.HIDDEN_DIR))


def download(roots: theme_core.Roots | None, slug: str, result: WebWallpaper) -> Path:
    """Download the full image into the theme's user wallpaper folder."""
    if not FULL_RE.match(result.full):
        raise ValueError(_("unexpected image address"))
    if already_added(roots, slug, result):
        raise FileExistsError(_("{0} is already in this theme").format(result.file_name))
    if result.file_size > theme_core.MAX_BACKGROUND_BYTES:
        raise ValueError(_("image too large (max 50 MiB)"))
    data = theme_core.fetch_https(result.full, theme_core.MAX_BACKGROUND_BYTES, timeout=60.0)
    if not data or not theme_core.is_image_data(data):
        raise OSError(_("the image could not be downloaded"))
    return theme_core.add_background_bytes(roots, slug, data, name=result.file_name)


def open_page(result: WebWallpaper) -> bool:
    return bool(PAGE_RE.match(result.page)) and theme_core.launch_browser(result.page)
