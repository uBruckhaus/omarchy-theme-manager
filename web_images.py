"""Theme Manager - wallpaper pictures an Ask AI helper found on the web.

A helper with web tools (theme_ai.HELPERS[...]["web"]) may answer with links
to pictures on any site. They are untrusted, so:

- A link must be https, name a host (no address, no port, no user, no
  fragment, no private-network suffix) and end in a picture extension.
- The host is resolved here and EVERY address must be public (no loopback,
  private, link-local, shared/CGNAT, multicast, reserved, documentation,
  IPv4-mapped private, 6to4, Teredo or NAT64): a picture link can never
  reach this computer or the local network.
- curl is pinned to the checked address with --resolve (no second DNS answer),
  HTTPS only, no redirects, a byte cap (the wallpaper limit) and a deadline.
- Only JPEG, PNG or WebP bytes of sane pixel size are kept, in a small cache.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import theme_core
from i18n import _

MAX_CACHED = 40
MAX_URL = 600
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".home.arpa", ".localdomain",
                    ".intranet", ".corp")
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
BAD_CHARS = re.compile(r'[\s<>"\\]')


@dataclass(frozen=True)
class WebPicture:
    """A picture from the web as a tile: its link, the page it is on, a caption."""
    url: str
    page: str | None
    title: str

    @property
    def id(self) -> str:
        return "web-" + hashlib.sha256(self.url.encode()).hexdigest()[:16]

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""

    @property
    def file_name(self) -> str:
        return self.title or self.host


def public_https_url(raw) -> str | None:
    """An https URL to a public-looking host name, or None."""
    if not isinstance(raw, str) or len(raw) > MAX_URL or BAD_CHARS.search(raw):
        return None
    try:
        parts = urllib.parse.urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username is not None or parts.password is not None or \
            "@" in parts.netloc or (port is not None and port != 443) or parts.fragment:
        return None
    if not HOST_RE.match(host) or host.endswith(PRIVATE_SUFFIXES):
        return None
    return raw


def public_image_url(raw) -> str | None:
    """public_https_url whose path ends in a picture file extension, or None."""
    url = public_https_url(raw)
    if url is None:
        return None
    return url if urllib.parse.urlsplit(url).path.lower().endswith(IMAGE_SUFFIXES) else None


def verify_images(raw, limit: int) -> list[dict]:
    """An answer's picture links that look like direct public https image
    files: {url, page (only when public), title}, at most `limit`, no repeats."""
    out, seen = [], set()
    for entry in (raw if isinstance(raw, list) else [])[:30]:
        if not isinstance(entry, dict):
            continue
        url = public_image_url(entry.get("url"))
        if url is None or url in seen:
            continue
        seen.add(url)
        item = {"url": url, "title": theme_core.plain_text(str(entry.get("title") or ""), 80)}
        page = public_https_url(entry.get("page"))
        if page is not None:
            item["page"] = page
        out.append(item)
        if len(out) >= limit:
            break
    return out


def picture(item: dict) -> WebPicture:
    return WebPicture(item["url"], item.get("page"), item.get("title") or "")


def is_public_address(address) -> bool:
    """Whether an IP address (text or ipaddress object) is public unicast."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _public_v4(ip.ipv4_mapped)
        raw = ip.packed
        if raw[:12] == bytes(12):
            return False                          # ::, ::1, ::a.b.c.d
        if raw[:4] == b"\x00\x64\xff\x9b":
            return False                          # NAT64
        if (raw[0] & 0xE0) != 0x20:
            return False                          # only global unicast 2000::/3
        if raw[:4] == b"\x20\x01\x0d\xb8" or raw[:2] == b"\x20\x02" or raw[:4] == b"\x20\x01\x00\x00":
            return False                          # documentation, 6to4, Teredo
        return True
    return _public_v4(ip)


def _public_v4(ip: ipaddress.IPv4Address) -> bool:
    a, b, c, _d = ip.packed
    return not (a == 0 or a == 10 or a == 127 or (a == 100 and 64 <= b <= 127) or (a == 169 and b == 254)
                or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
                or (a == 192 and b == 0 and c in (0, 2)) or (a == 198 and b in (18, 19))
                or (a == 198 and b == 51 and c == 100) or (a == 203 and b == 0 and c == 113) or a >= 224)


RESOLVE_TIMEOUT = 5.0


def resolve(host: str) -> list[str]:
    """The host's addresses (system resolver), within RESOLVE_TIMEOUT seconds:
    the lookup runs in a daemon thread, a stalled resolver raises OSError
    (the process-wide socket timeout is never touched)."""
    box = {}

    def lookup():
        try:
            box["infos"] = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as error:
            box["error"] = error

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(RESOLVE_TIMEOUT)
    if "error" in box:
        raise box["error"]
    if "infos" not in box:
        raise OSError(f"looking up {host} took too long")
    infos = box["infos"]
    out = []
    for info in infos:
        address = info[4][0]
        if address not in out:
            out.append(address)
    return out


def fetch_pinned(url: str, address: str, max_bytes: int, timeout: float = 60.0) -> bytes | None:
    """One GET of `url` from `address` (already checked): HTTPS only, no
    redirects, size- and time-bounded."""
    host = urllib.parse.urlsplit(url).hostname
    ip = f"[{address}]" if ":" in address else address
    data, ok = theme_core.read_bounded_output(
        ["curl", "--silent", "--fail", "--proto", "=https", "--max-redirs", "0",
         "--noproxy", "*", "--resolve", f"{host}:443:{ip}",   # no proxy: the checked address is the one used
         "--max-time", str(int(timeout)), "--max-filesize", str(max_bytes), "--", url],
        max_bytes=max_bytes, timeout=timeout + 2)
    return data if ok and data else None


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "theme-manager" / "web-images"


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def cached(url: str) -> Path | None:
    for ext in (".jpg", ".png", ".webp"):
        path = cache_dir() / (_key(url) + ext)
        if path.is_file() and not path.is_symlink():
            return path if theme_core.image_size_problem(path) is None else None
    return None


def get(raw_url, resolver=None, fetch=None) -> Path:
    """The picture's local copy (downloaded when missing). Raises ValueError
    (not a public picture link, or no usable picture) or OSError (network)."""
    url = public_image_url(raw_url)
    if url is None:
        raise ValueError(_("not a public https image address"))
    found = cached(url)
    if found is not None:
        return found
    host = urllib.parse.urlsplit(url).hostname
    try:
        addresses = (resolver or resolve)(host)
    except OSError:
        raise OSError(_("{0} could not be found").format(host)) from None
    if not addresses or not all(is_public_address(a) for a in addresses):
        raise ValueError(_("{0} is not a public web address").format(host))
    data = (fetch or fetch_pinned)(url, addresses[0], theme_core.MAX_BACKGROUND_BYTES)
    if not data:
        raise OSError(_("the image could not be downloaded"))
    ext = theme_core.image_extension(data)
    if not theme_core.is_image_data(data) or ext not in (".jpg", ".png", ".webp"):
        raise ValueError(_("the link is not a JPEG, PNG or WebP picture"))
    path = cache_dir() / (_key(url) + ext)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    theme_core._atomic_write_bytes(path, data, mode=0o600)
    problem = theme_core.image_size_problem(path)
    if problem is not None:
        path.unlink(missing_ok=True)
        raise ValueError(problem)
    _prune()
    return path


def file_name_of(url: str, path: Path) -> str:
    """A file name from the link: its last path segment, made safe."""
    segments = [s for s in urllib.parse.unquote(urllib.parse.urlsplit(url).path).split("/") if s]
    last = segments[-1] if segments else "web-wallpaper"
    stem = last.rsplit(".", 1)[0] if "." in last else last
    clean = re.sub(r"^[-.]+|[-.]+$", "", re.sub(r"[^A-Za-z0-9._-]+", "-", stem))[:60]
    return "web-" + (clean or "web-wallpaper") + path.suffix


def already_added(roots, slug: str, item: WebPicture) -> bool:
    path = cached(item.url)
    if path is None:
        return False
    roots = roots or theme_core.default_roots()
    folder = roots.user_backgrounds / slug
    name = file_name_of(item.url, path)
    return any((d / name).exists() for d in (folder, folder / theme_core.HIDDEN_DIR))


def download(roots, slug: str, item: WebPicture) -> Path:
    """The picture added to the theme's user wallpaper folder."""
    path = get(item.url)
    name = file_name_of(item.url, path)
    if already_added(roots, slug, item):
        raise FileExistsError(_("{0} is already in this theme").format(name))
    return theme_core.add_background_bytes(roots, slug, path.read_bytes(), name=name)


def open_page(item: WebPicture) -> bool:
    url = public_https_url(item.page or item.url)
    return url is not None and theme_core.launch_browser(url)


def _prune() -> None:
    try:
        files = sorted((f for f in cache_dir().iterdir() if f.is_file() and not f.is_symlink()),
                       key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    for old in files[:-MAX_CACHED]:
        old.unlink(missing_ok=True)
