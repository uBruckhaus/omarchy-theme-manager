"""Theme Manager - the omarchy.org theme gallery.

Lists the themes on https://omarchy.org/themes/, fetches their previews and
installs one with git, the same way `omarchy theme install` does but without
replacing an existing theme or applying it on the spot.

Trust rules (see theme_core):

- Network access goes to one fixed host only, `https://omarchy.org`, through
  `curl` with HTTPS only, no redirects, and a byte cap and deadline on every
  request. Nothing from the page decides which host is contacted.
- The page is untrusted. A theme is accepted only with a repository of the
  form `https://github.com/<owner>/<repo>` and a preview under
  `/assets/themes/`; names are shown as plain text.
- Installing clones into `~/.config/omarchy/themes/<slug>` (argv, no shell,
  no prompts) after Omarchy's own `omarchy-git-url-check`; an existing theme
  is never replaced. `omarchy theme set` later stages only the files a theme
  from git may contribute.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import theme_core

SITE = "https://omarchy.org"
CATALOG_URL = SITE + "/themes/"
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_BYTES = 3 * 1024 * 1024
CACHE_TTL = 24 * 3600
CLONE_TIMEOUT = 180.0

REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
PREVIEW_RE = re.compile(r"^/assets/themes/([a-z0-9][a-z0-9._-]*\.(?:webp|png|jpe?g))$")
# One gallery card: <li><a href="REPO" …><img src="/assets/themes/X.webp" …/><span …>Name</span></a></li>
CARD_RE = re.compile(r'<li><a href="([^"]+)"[^>]*><img src="([^"]+)"[^>]*/?><span[^>]*>([^<]{1,120})</span></a></li>')
# Omarchy's own rule for a theme directory name (omarchy-theme-install).
INSTALL_SLUG_RE = re.compile(r"^[a-z0-9_][a-z0-9._+-]*$")


@dataclass(frozen=True)
class CatalogTheme:
    name: str
    repo: str
    slug: str
    preview: str    # file name under /assets/themes/


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "theme-manager"


# --- fetching --------------------------------------------------------------------

def https_get(url: str, max_bytes: int, timeout: float = 15.0) -> bytes | None:
    """GET a URL on omarchy.org: HTTPS only, no redirects, bounded."""
    if not url.startswith(SITE + "/"):
        raise ValueError(f"refusing to fetch outside {SITE}: {url!r}")
    return theme_core.fetch_https(url, max_bytes, timeout)


def slug_for_repo(repo: str) -> str | None:
    """The directory name `omarchy theme install` gives a repository."""
    match = REPO_RE.match(repo)
    if not match:
        return None
    name = match.group(2).lower()
    name = re.sub(r"^omarchy-", "", name)
    name = re.sub(r"-theme$", "", name)
    return name if INSTALL_SLUG_RE.match(name) else None


def parse_catalog(page: str) -> list[CatalogTheme]:
    themes: dict[str, CatalogTheme] = {}
    for repo, image, name in CARD_RE.findall(page):
        repo = html.unescape(repo).strip()
        preview = PREVIEW_RE.match(html.unescape(image).strip())
        slug = slug_for_repo(repo)
        title = theme_core.plain_text(html.unescape(name), limit=60).strip()
        if not (preview and slug and title):
            continue
        themes.setdefault(slug, CatalogTheme(title, repo.rstrip("/"), slug, preview.group(1)))
    return sorted(themes.values(), key=lambda t: t.name.casefold())


def load_catalog(force: bool = False) -> tuple[list[CatalogTheme], float]:
    """(themes, fetched_at). Served from a one-day cache unless `force`; a
    failed refresh falls back to the cache. Raises OSError if neither works."""
    path = cache_dir() / "catalog.json"
    cached: list[CatalogTheme] = []
    fetched_at = 0.0
    try:
        data = json.loads(path.read_text())
        fetched_at = float(data["fetched_at"])
        for item in data["themes"]:
            entry = CatalogTheme(**{k: str(item[k]) for k in ("name", "repo", "slug", "preview")})
            # The cache file is re-checked like the page itself.
            if slug_for_repo(entry.repo) == entry.slug and PREVIEW_RE.match("/assets/themes/" + entry.preview):
                cached.append(entry)
    except (OSError, ValueError, KeyError, TypeError):
        cached = []
    if cached and not force and time.time() - fetched_at < CACHE_TTL:
        return cached, fetched_at
    page = https_get(CATALOG_URL, MAX_PAGE_BYTES)
    themes = parse_catalog(page.decode("utf-8", "replace")) if page else []
    if themes:
        now = time.time()
        theme_core._atomic_write(path, json.dumps(
            {"fetched_at": now, "themes": [asdict(t) for t in themes]}) + "\n", mode=0o600)
        return themes, now
    if cached:
        return cached, fetched_at
    raise OSError("could not load the theme list from omarchy.org")


def preview_path(theme: CatalogTheme) -> Path:
    return cache_dir() / "previews" / theme.preview


def fetch_preview(theme: CatalogTheme) -> Path | None:
    """The cached preview, downloaded first if needed. Only real image data
    is kept."""
    path = preview_path(theme)
    if path.is_file() and not path.is_symlink():
        return path
    if not PREVIEW_RE.match("/assets/themes/" + theme.preview):
        return None
    data = https_get(f"{SITE}/assets/themes/{theme.preview}", MAX_PREVIEW_BYTES)
    if not data or not theme_core.is_image_data(data):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    theme_core._atomic_write_bytes(path, data)
    return path


# --- install ---------------------------------------------------------------------

def install(theme: CatalogTheme, roots: theme_core.Roots | None = None) -> Path:
    """Clone the theme into the user theme folder. Never replaces a theme."""
    roots = roots or theme_core.default_roots()
    slug = slug_for_repo(theme.repo)
    if slug is None or slug != theme.slug:
        raise ValueError(f"not a GitHub theme repository: {theme.repo}")
    dest = roots.user_themes / slug
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(f"a theme named “{slug}” is already installed")
    _, ok = theme_core.read_bounded_output(["omarchy-git-url-check", theme.repo],
                                           max_bytes=65536, timeout=10.0)
    if not ok:
        raise ValueError(f"Omarchy refuses this repository URL: {theme.repo}")
    roots.user_themes.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false"}
    _, ok = theme_core.read_bounded_output(
        ["git", "clone", "--depth", "1", "--quiet", "--", theme.repo, str(dest)],
        max_bytes=theme_core.MAX_COMMAND_OUTPUT_BYTES, timeout=CLONE_TIMEOUT, env=env)
    if not ok:
        # Remove only what this clone created, and only inside the theme root.
        if dest.is_dir() and not dest.is_symlink() and \
                dest.resolve().is_relative_to(roots.user_themes.resolve()):
            shutil.rmtree(dest, ignore_errors=True)
        raise OSError(f"git clone failed for {theme.repo}")
    return dest


# --- browser ---------------------------------------------------------------------

def default_browser() -> str | None:
    """The default web browser's desktop file, or None if none is set or it
    is not installed (the check omarchy-launch-browser relies on)."""
    for argv in (["xdg-settings", "get", "default-web-browser"],
                 ["xdg-mime", "query", "default", "x-scheme-handler/https"]):
        out, ok = theme_core.read_bounded_output(argv, max_bytes=4096, timeout=3.0)
        desktop = out.decode("utf-8", "replace").strip()
        if ok and re.match(r"^[A-Za-z0-9_.+-]+\.desktop$", desktop):
            for base in (Path.home() / ".local/share/applications",
                         Path.home() / ".nix-profile/share/applications",
                         Path("/usr/share/applications")):
                if (base / desktop).is_file():
                    return desktop
    return None


def open_in_browser(url: str) -> bool:
    """Open a theme's GitHub page or the gallery in the default browser."""
    if not (REPO_RE.match(url) or url == CATALOG_URL):
        return False
    return theme_core.launch_browser(url)
