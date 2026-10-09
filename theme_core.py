"""Theme Manager - core logic.

Pure, testable functions over Omarchy's theme layout:

- discover themes (user and system, user copy wins on a slug clash)
- hide / unhide themes in the list (JSON, atomic 0600 writes)
- list, add and remove wallpapers per theme
- apply a theme, remove a user theme

Trust rules (mirroring the marketplace security review):

- Every path operation is bound to a small set of known roots. File names
  from disk, drop targets or the clipboard are never interpreted: they are
  sanitized, size-capped and copied byte-wise.
- Deletion only ever happens inside user-owned roots; system theme files
  (root-owned) are refused.
- No shell anywhere: subprocesses run as argv vectors with bounded output.
- Notification text is markup-stripped before it reaches notify-send.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
MAX_BACKGROUND_BYTES = 50 * 1024 * 1024   # one wallpaper, whatever its source
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024    # anything read from a subprocess
PREVIEW_NAMES = ("preview.png", "preview.jpg", "preview.jpeg",
                 "preview.webp", "preview.gif", "preview.bmp")

# A slug is what a directory is called: one path element, no control
# characters. Omarchy's own rule (omarchy-theme-install) allows `_ . +` after
# the first character, which is never `.` or `-`, so `.` and `..` cannot pass.
SLUG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]*$")

# Characters that are useless in a wallpaper file name and dangerous on some
# filesystems. Everything else (spaces, unicode) stays.
_UNSAFE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f<>\"'|:*?\\/]")


@dataclass(frozen=True)
class Roots:
    user_themes: Path        # ~/.config/omarchy/themes
    system_themes: Path      # $OMARCHY_PATH/themes (usually /usr/share/omarchy/themes)
    user_backgrounds: Path   # ~/.config/omarchy/backgrounds/<theme>/ (per-theme user wallpapers)
    state_current: Path      # ~/.local/state/omarchy/current


def default_roots() -> Roots:
    omarchy_path = Path(os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy")
    home = Path.home()
    return Roots(
        user_themes=home / ".config/omarchy/themes",
        system_themes=omarchy_path / "themes",
        user_backgrounds=home / ".config/omarchy/backgrounds",
        state_current=home / ".local/state/omarchy/current",
    )


def hidden_file() -> Path:
    return Path.home() / ".config/omarchy/theme-manager/hidden.json"


@dataclass(frozen=True)
class ThemeInfo:
    slug: str
    name: str
    path: Path
    scope: str            # "user" or "system"
    current: bool
    preview: str | None   # absolute path of the preview image, or None


@dataclass(frozen=True)
class WallpaperInfo:
    path: Path
    name: str
    source: str           # "user" (per-theme folder) or "theme" (shipped with the theme)
    deletable: bool
    hidden: bool = False
    # Why it cannot be deleted (shown in the menu); "" when it can.
    locked_reason: str = ""


# --- names -----------------------------------------------------------------

def display_name(slug: str) -> str:
    """Omarchy's own rule (omarchy-theme-list): capitalise the first letter
    of the slug and after every dash, then swap dashes for spaces.

    `japan-night` -> `Japan Night`, `retro-82` -> `Retro 82`.
    """
    def cap(match: re.Match) -> str:
        return match.group(1) + match.group(2).upper()
    return re.sub(r"(^|-)([a-z])", cap, slug).replace("-", " ")


def plain_text(text: str, limit: int = 300) -> str:
    """Text with no markup or control characters, for notifications.

    Browser pages and file names are untrusted: notification daemons render
    markup, so `<img src=...>` must never reach them.
    """
    text = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(text))
    return re.sub(r"[<>&]", "", text)[:limit]


# --- discovery ---------------------------------------------------------------

def current_theme(roots: Roots | None = None) -> str:
    roots = roots or default_roots()
    try:
        slug = (roots.state_current / "theme.name").read_text().strip()
    except OSError:
        return ""
    return slug if SLUG_RE.match(slug) else ""


def preview_of(theme_dir: Path) -> str | None:
    """The first preview image of a theme, case-insensitively, like the switcher."""
    try:
        entries = {entry.name.lower(): entry for entry in theme_dir.iterdir()}
    except OSError:
        return None
    for name in PREVIEW_NAMES:
        entry = entries.get(name)
        if entry is not None and entry.is_file():
            return str(entry)
    return None


def discover_themes(roots: Roots | None = None) -> list[ThemeInfo]:
    """All themes, user and system. A user copy wins over a system one with
    the same slug (that is how `omarchy theme dir` and the switcher behave).
    """
    roots = roots or default_roots()
    current = current_theme(roots)
    found: dict[str, ThemeInfo] = {}
    for scope, base in (("user", roots.user_themes), ("system", roots.system_themes)):
        try:
            entries = sorted(base.iterdir(), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            slug = entry.name
            if slug.startswith(".") or slug in found:
                continue
            if not entry.is_dir():   # follows symlinks; drops broken ones
                continue
            found[slug] = ThemeInfo(
                slug=slug,
                name=display_name(slug),
                path=entry,
                scope=scope,
                current=slug == current,
                preview=preview_of(entry),
            )
    return sorted(found.values(), key=lambda t: t.name.casefold())


# --- hidden list ---------------------------------------------------------------

def load_hidden(path: Path | None = None) -> set[str]:
    path = path or hidden_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    if isinstance(data, list):
        return {slug for slug in data if isinstance(slug, str) and SLUG_RE.match(slug)}
    return set()


def unhide_theme(slug: str, path: Path | None = None) -> bool:
    """Take a theme off the hidden list (it is being applied, so it is wanted).
    Returns True when it was hidden. Omarchy's picker cache is invalidated so
    the next filtered rebuild brings the theme back into the picker."""
    hidden = load_hidden(path)
    if slug not in hidden:
        return False
    hidden.discard(slug)
    save_hidden(hidden, path)
    if sync_enabled():
        invalidate_switcher_cache()
    return True


def save_hidden(hidden: set[str], path: Path | None = None) -> None:
    """Atomic 0600 write: unique temp file, chmod, fsync, replace.

    Never truncates an existing file, so a pre-planted symlink at the target
    cannot redirect the write.
    """
    path = path or hidden_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".hidden-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(sorted(hidden), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- Omarchy theme switcher sync ------------------------------------------------
#
# Omarchy's own picker (`omarchy-theme-switcher`) shows one preview symlink per
# theme from a cache directory it rebuilds when the theme folders change. A
# hidden theme is kept out of it by removing its symlink there; the plugin's
# `theme-switcher` wrapper re-applies that after every rebuild, and the Omarchy
# menu's Theme entry is pointed at the wrapper.

MENU_ENTRY_ID = "style.theme"
MENU_MARKER = "// theme-manager: hidden themes are filtered out of the theme picker"
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9/._-]+$")


def switcher_cache_dir() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "omarchy/theme-selector"


def menu_extension_file() -> Path:
    return Path.home() / ".config/omarchy/extensions/omarchy-menu.jsonc"


def image_selector_cache() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "omarchy/image-selector"


def prune_switcher_previews(hidden: set[str], preview_dir: Path | None = None,
                            keep: str | None = None) -> list[str]:
    """Remove the picker's preview *symlinks* for hidden themes.

    Only symlinks named `<slug>.<image ext>` directly in the preview dir are
    touched; the images they point to never are. The current theme (`keep`,
    by default the active one) always stays: an empty picker is not shown
    empty, Omarchy's image picker falls back to its previously loaded rows,
    i.e. to every theme."""
    preview_dir = preview_dir or switcher_cache_dir() / "previews"
    keep = current_theme() if keep is None else keep
    removed = []
    try:
        entries = list(preview_dir.iterdir())
    except OSError:
        return removed
    for entry in entries:
        stem, ext = os.path.splitext(entry.name)
        if entry.is_symlink() and ext.lower() in IMAGE_EXTS and stem in hidden and stem != keep:
            try:
                entry.unlink()
                removed.append(stem)
            except OSError:
                pass
    if removed:
        _forget_picker_rows(preview_dir)
    return removed


def _forget_picker_rows(preview_dir: Path) -> None:
    """Drop omarchy-menu-images' row cache for the theme picker. Its validity
    check is the directory's mtime in whole seconds, so a rebuild and a prune
    within one second would otherwise keep serving the unfiltered rows."""
    key = hashlib.md5(str(preview_dir).encode()).hexdigest()
    stamp = image_selector_cache() / f"{key}.fast-signature"
    if stamp.is_file() and not stamp.is_symlink():
        stamp.unlink(missing_ok=True)


def invalidate_switcher_cache(cache_dir: Path | None = None) -> None:
    """Make the next `omarchy-theme-switcher` run rebuild its previews, so an
    unhidden theme comes back."""
    cache_dir = cache_dir or switcher_cache_dir()
    (cache_dir / "fast-signature").unlink(missing_ok=True)


def menu_override_line(wrapper: str | Path) -> str:
    wrapper = str(wrapper)
    if not _SAFE_PATH_RE.match(wrapper):
        raise ValueError(f"unsafe wrapper path: {wrapper!r}")
    action = f'theme=$({wrapper}); [[ -n $theme ]] && omarchy-theme-set "$theme"'
    entry = {"icon": "\U000f0e0c", "label": "Theme",
             "aliases": ["theme", "themes"], "action": action}
    return f"  {json.dumps(MENU_ENTRY_ID)}: {json.dumps(entry, ensure_ascii=False)},"


def ensure_menu_override(wrapper: str | Path, path: Path | None = None) -> bool:
    """Point the Omarchy menu's Theme entry at the filtering wrapper.

    Idempotent. Only a line carrying our marker is ever rewritten; if the
    user already overrides `style.theme` themselves, the file is left alone.
    Returns True when the file changed."""
    path = path or menu_extension_file()
    line = menu_override_line(wrapper)
    try:
        text = path.read_text()
    except FileNotFoundError:
        text = "{\n}\n"
    lines = text.splitlines()
    for i, existing in enumerate(lines):
        if existing.strip() == MENU_MARKER and i + 1 < len(lines):
            if lines[i + 1] == line:
                return False
            lines[i + 1] = line
            break
    else:
        if f'"{MENU_ENTRY_ID}"' in text:
            return False  # the user's own override wins
        opening = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), None)
        if opening is None:
            return False  # not a file we understand; never guess
        lines[opening + 1:opening + 1] = ["  " + MENU_MARKER, line]
    _atomic_write(path, "\n".join(lines) + "\n")
    return True


HOOK_MARKER = "# theme-manager: keeps hidden wallpapers out of the theme just applied"


# --- opt-in: syncing with Omarchy's own pickers -------------------------------------
#
# Off by default. Only when the user switches it on does Theme Manager write
# into Omarchy's files (one marked menu line, one marked theme-set hook) and
# filter Omarchy's picker cache and current-theme copy. Switching it off
# removes exactly what it added and lets Omarchy rebuild its pickers.

def settings_file() -> Path:
    return Path.home() / ".config/omarchy/theme-manager/settings.json"


def sync_enabled(path: Path | None = None) -> bool:
    path = path or settings_file()
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("sync_omarchy"), bool):
            return data["sync_omarchy"]
    except (OSError, ValueError):
        pass
    # Installs from before the setting existed already carry our hook: keep them on.
    try:
        return HOOK_MARKER in theme_set_hook_file().read_text()
    except OSError:
        return False


def save_settings(sync_omarchy: bool, path: Path | None = None) -> None:
    path = path or settings_file()
    _atomic_write(path, json.dumps({"sync_omarchy": bool(sync_omarchy)}, indent=2) + "\n", mode=0o600)


def remove_menu_override(path: Path | None = None) -> bool:
    """Remove our marked line pair from the menu extension file, nothing else."""
    path = path or menu_extension_file()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return False
    for i, line in enumerate(lines):
        if line.strip() == MENU_MARKER:
            end = i + 2 if i + 1 < len(lines) and f'"{MENU_ENTRY_ID}"' in lines[i + 1] else i + 1
            del lines[i:end]
            _atomic_write(path, "\n".join(lines) + "\n")
            return True
    return False


def remove_theme_set_hook(path: Path | None = None) -> bool:
    """Delete our hook file, and only if it is ours."""
    path = path or theme_set_hook_file()
    try:
        if path.is_symlink() or HOOK_MARKER not in path.read_text():
            return False
    except OSError:
        return False
    path.unlink()
    return True


def set_sync_enabled(on: bool, wrapper: str | Path, sync_script: str | Path) -> None:
    save_settings(on)
    if on:
        ensure_menu_override(wrapper)
        ensure_theme_set_hook(sync_script)
        sync_current_theme()
        invalidate_switcher_cache()
        refresh_switcher_cache(wrapper)
    else:
        remove_menu_override()
        remove_theme_set_hook()
        # Give Omarchy its unfiltered pickers back: rebuild the theme picker,
        # and put hidden wallpapers back into its copy of the current theme.
        invalidate_switcher_cache()
        _forget_picker_rows(switcher_cache_dir() / "previews")
        sync_current_theme(hidden_override={})


def theme_set_hook_file() -> Path:
    return Path.home() / ".config/omarchy/hooks/theme-set.d/90-theme-manager"


def ensure_theme_set_hook(sync_script: str | Path, path: Path | None = None) -> bool:
    """Install Omarchy's documented theme-set hook that re-applies hidden
    wallpapers after `omarchy theme set` rebuilds the current theme. Only a
    file carrying our marker is ever rewritten. Returns True when written."""
    path = path or theme_set_hook_file()
    sync_script = str(sync_script)
    if not _SAFE_PATH_RE.match(sync_script):
        raise ValueError(f"unsafe script path: {sync_script!r}")
    text = (f"#!/bin/bash\n{HOOK_MARKER}\n"
            f"# Installed by the Theme Manager plugin; delete this file to undo.\n"
            f"[[ -f '{sync_script}' ]] && /usr/bin/python3 -I '{sync_script}' wallpapers\n")
    try:
        existing = path.read_text()
    except FileNotFoundError:
        existing = None
    if existing == text:
        return False
    if existing is not None and HOOK_MARKER not in existing:
        return False  # someone else's file: leave it
    _atomic_write(path, text, mode=0o644)
    return True


def refresh_switcher_cache(wrapper: str | Path, timeout: float = 60.0) -> bool:
    """Let Omarchy rebuild its theme-picker cache now, through the filtering
    wrapper, and wait for it. Once the cache matches the theme folders again,
    Omarchy's own (unfiltered) `omarchy-theme-switcher --preload` finds nothing
    to rebuild, so hidden themes stay out of the picker."""
    _, ok = read_bounded_output([str(wrapper), "--preload"], max_bytes=65536, timeout=timeout)
    return ok


def preload_switcher(wrapper: str | Path) -> None:
    """Let Omarchy rebuild its picker cache in the background, through the
    filtering wrapper (argv, no shell, detached)."""
    try:
        subprocess.Popen([str(wrapper), "--preload"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o644) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- wallpapers ----------------------------------------------------------------

def _validate_slug(slug: str) -> str:
    slug = str(slug).strip()
    if not slug or slug in (".", "..") or not SLUG_RE.match(slug):
        raise ValueError(f"invalid theme slug: {slug!r}")
    return slug


def _sanitize_name(name: str) -> str:
    """A safe file name from untrusted input: one element, no control
    characters, no path separators, always an image extension or rejected."""
    name = Path(str(name)).name  # never a path
    if not name or name in (".", ".."):
        name = "image"
    name = _UNSAFE_NAME_CHARS.sub("_", name).strip().strip(".")
    if not name:
        name = "image"
    return name


def _is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTS


def _unique_dest(roots: Roots, slug: str, name: str) -> Path:
    dest_dir = roots.user_backgrounds / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / name
    stem, ext = os.path.splitext(name)
    counter = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = dest_dir / f"{stem}-{counter}{ext}"
        counter += 1
    return candidate


def _copy_bounded(source: Path, dest: Path) -> None:
    """Byte-wise copy with a hard size cap; the partial file is removed on
    any violation, so a growing or crafted file cannot exhaust disk here."""
    total = 0
    # O_EXCL | O_NOFOLLOW: never write through a link planted at the target.
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with open(source, "rb") as src, os.fdopen(fd, "wb") as dst:
            while chunk := src.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BACKGROUND_BYTES:
                    raise ValueError("image too large (max 50 MiB)")
                dst.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def add_background_file(roots: Roots | None, slug: str, source: str | Path) -> Path:
    """Copy one image file into the theme's per-theme user background folder
    (`~/.config/omarchy/backgrounds/<slug>/`), which Omarchy merges with the
    theme's own backgrounds. Works for system themes, too."""
    roots = roots or default_roots()
    slug = _validate_slug(slug)
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"not a file: {source}")
    if not _is_image_name(source.name):
        raise ValueError(f"not an image: {source.name}")
    if source.stat().st_size > MAX_BACKGROUND_BYTES:
        raise ValueError("image too large (max 50 MiB)")
    dest = _unique_dest(roots, slug, _sanitize_name(source.name))
    _copy_bounded(source, dest)
    return dest


def add_background_bytes(roots: Roots | None, slug: str, data: bytes,
                         name: str = "image.png") -> Path:
    """Store image bytes (e.g. from the Wayland clipboard) the same way."""
    roots = roots or default_roots()
    slug = _validate_slug(slug)
    data = bytes(data)
    if not data:
        raise ValueError("empty image data")
    if len(data) > MAX_BACKGROUND_BYTES:
        raise ValueError("image too large (max 50 MiB)")
    name = _sanitize_name(name)
    if not _is_image_name(name):
        raise ValueError(f"not an image: {name}")
    dest = _unique_dest(roots, slug, name)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return dest


_MIME_EXTS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/x-bmp": ".bmp",
}


def extension_for_mime(mime: str) -> str | None:
    """File extension for a clipboard image type, or None if unsupported
    (e.g. SVG, which is not a wallpaper format)."""
    return _MIME_EXTS.get(str(mime).split(";", 1)[0].strip().lower())


HIDDEN_DIR = ".hidden"   # added wallpapers are hidden by moving them here

LOCK_SYSTEM = "Ships with a system theme (owned by root, restored by updates): hide it instead"
LOCK_GIT = "Part of a theme installed from git; deleting would break `omarchy theme update`: hide it instead"


def hidden_wallpapers_file() -> Path:
    return Path.home() / ".config/omarchy/theme-manager/hidden-wallpapers.json"


def load_hidden_wallpapers(path: Path | None = None) -> dict[str, set[str]]:
    """{theme slug: {file name, ...}} of shipped wallpapers hidden by the user."""
    path = path or hidden_wallpapers_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, set[str]] = {}
    if isinstance(data, dict):
        for slug, names in data.items():
            if isinstance(slug, str) and SLUG_RE.match(slug) and isinstance(names, list):
                clean = {n for n in names if isinstance(n, str) and n == _sanitize_name(n)}
                if clean:
                    out[slug] = clean
    return out


def save_hidden_wallpapers(hidden: dict[str, set[str]], path: Path | None = None) -> None:
    path = path or hidden_wallpapers_file()
    data = {slug: sorted(names) for slug, names in sorted(hidden.items()) if names}
    _atomic_write(path, json.dumps(data, indent=2) + "\n", mode=0o600)


def theme_from_git(theme_dir: Path) -> bool:
    """Omarchy's own rule (omarchy-theme-set): a real directory with .git."""
    return not theme_dir.is_symlink() and (theme_dir / ".git").is_dir()


def _theme_sources(roots: Roots, theme: ThemeInfo) -> list[tuple[Path, str]]:
    """Directories whose backgrounds Omarchy merges for a theme, highest
    priority first, with the lock reason for files in each ("" = deletable).
    `omarchy theme set` copies the system theme, then overlays the user one."""
    sources = []
    user_dir = roots.user_themes / theme.slug
    if user_dir.is_dir():
        sources.append((user_dir / "backgrounds", LOCK_GIT if theme_from_git(user_dir) else ""))
    system_dir = roots.system_themes / theme.slug
    if system_dir.is_dir():
        sources.append((system_dir / "backgrounds", LOCK_SYSTEM))
    return sources


def _images(directory: Path) -> list[Path]:
    try:
        entries = sorted(directory.iterdir(), key=lambda e: e.name.casefold())
    except OSError:
        return []
    return [e for e in entries if e.is_file() and e.suffix.lower() in IMAGE_EXTS]


def list_backgrounds(roots: Roots | None, theme: ThemeInfo,
                     hidden: dict[str, set[str]] | None = None) -> list[WallpaperInfo]:
    """The wallpaper set Omarchy shows for a theme, plus the hidden ones
    (marked). A user file with the same name as a shipped one shadows it."""
    roots = roots or default_roots()
    hidden_names = (load_hidden_wallpapers() if hidden is None else hidden).get(theme.slug, set())
    merged: dict[str, WallpaperInfo] = {}
    added = roots.user_backgrounds / theme.slug
    for entry in _images(added):
        merged[entry.name.casefold()] = WallpaperInfo(entry, entry.name, "user", True)
    for entry in _images(added / HIDDEN_DIR):
        merged.setdefault(entry.name.casefold(),
                          WallpaperInfo(entry, entry.name, "user", True, hidden=True))
    for directory, lock in _theme_sources(roots, theme):
        for entry in _images(directory):
            merged.setdefault(entry.name.casefold(), WallpaperInfo(
                entry, entry.name, "theme", not lock,
                hidden=entry.name in hidden_names, locked_reason=lock))
    return sorted(merged.values(), key=lambda w: w.name.casefold())


def set_wallpaper_hidden(roots: Roots | None, theme: ThemeInfo,
                         wallpaper: WallpaperInfo, hide: bool) -> None:
    """Hide or unhide one wallpaper, for Omarchy's pickers too.

    Added wallpapers move into `<backgrounds>/<slug>/.hidden/`, which Omarchy
    does not scan. Shipped ones are recorded in hidden-wallpapers.json and
    left untouched (they may be root-owned); `sync_current_theme()` keeps them
    out of the copy Omarchy reads. The last visible wallpaper cannot be hidden:
    Omarchy would have no background for the theme."""
    roots = roots or default_roots()
    slug = _validate_slug(theme.slug)
    if hide and wallpaper.hidden == hide:
        return
    if hide:
        visible = [w for w in list_backgrounds(roots, theme) if not w.hidden]
        if len(visible) <= 1:
            raise ValueError("this is the theme's last visible wallpaper")
    if wallpaper.source == "user":
        base = (roots.user_backgrounds / slug).resolve()
        src = Path(wallpaper.path)
        if src.is_symlink() or not src.parent.resolve().is_relative_to(base):
            raise PermissionError("not one of your added wallpapers")
        dest_dir = base / HIDDEN_DIR if hide else base
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        dest = dest_dir / _sanitize_name(src.name)
        if dest.exists() or dest.is_symlink():
            raise FileExistsError(f"{dest.name} already exists there")
        os.rename(src, dest)
        return
    hidden = load_hidden_wallpapers()
    names = hidden.setdefault(slug, set())
    (names.add if hide else names.discard)(wallpaper.name)
    save_hidden_wallpapers(hidden)


def staged_backgrounds(roots: Roots) -> Path:
    """Omarchy's per-user copy of the current theme's backgrounds."""
    return roots.state_current / "theme" / "backgrounds"


def sync_current_theme(roots: Roots | None = None,
                       set_background=None, hidden_override: dict | None = None) -> list[str]:
    """Make Omarchy's copy of the *current* theme match the hidden list:
    remove hidden wallpapers from it, restore unhidden ones, and move the
    desktop off a hidden wallpaper. Returns the names removed.

    Safe by construction: only regular files directly inside the staging
    directory (a real, user-owned directory Omarchy rebuilds on every theme
    switch) are removed or created; originals are never touched."""
    roots = roots or default_roots()
    slug = current_theme(roots)
    staged = staged_backgrounds(roots)
    if not slug or staged.is_symlink() or not staged.is_dir() or staged.stat().st_uid != os.getuid():
        return []
    hidden = (load_hidden_wallpapers() if hidden_override is None else hidden_override).get(slug, set())
    removed = []
    for entry in _images(staged):
        if entry.name in hidden and not entry.is_symlink():
            entry.unlink()
            removed.append(entry.name)
    theme = next((t for t in discover_themes(roots) if t.slug == slug), None)
    if theme is not None:
        present = {e.name for e in _images(staged)}
        # Lowest priority first, so the user theme's file wins like in Omarchy.
        for directory, _lock in reversed(_theme_sources(roots, theme)):
            for entry in _images(directory):
                if entry.name not in hidden and entry.name not in present:
                    dest = staged / entry.name
                    if not dest.exists() and not dest.is_symlink():
                        _copy_bounded(entry, dest)
    _leave_hidden_background(roots, slug, staged, set_background)
    return removed


def _leave_hidden_background(roots: Roots, slug: str, staged: Path, set_background) -> None:
    link = roots.state_current / "background"
    try:
        target = Path(os.readlink(link))
    except OSError:
        return
    if target.exists():
        return
    # Like omarchy-theme-bg-next: added wallpapers and the theme copy, sorted.
    candidates = sorted([*_images(roots.user_backgrounds / slug), *_images(staged)], key=str)
    if candidates:
        (set_background or set_desktop_background)(candidates[0])


def set_desktop_background(path: Path) -> bool:
    """Through Omarchy's own command (argv, no shell)."""
    _, ok = read_bounded_output(["omarchy-theme-bg-set", str(path)], max_bytes=65536, timeout=10.0)
    return ok


def remove_wallpaper(roots: Roots | None, path: str | Path) -> Path:
    """Delete one wallpaper file.

    Allowed only inside `~/.config/omarchy/backgrounds/<slug>/` or a *user*
    theme's `backgrounds/` directory; system theme wallpapers are refused.
    A symlink is unlinked as a link - the target is never followed.
    """
    roots = roots or default_roots()
    target = Path(path)
    if target.is_dir():
        raise PermissionError("refusing to remove a directory")
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(str(target))

    try:
        parent_real = target.parent.resolve()
    except OSError as error:
        raise PermissionError("cannot resolve the wallpaper path") from error

    user_bg = roots.user_backgrounds.resolve()
    if parent_real.is_relative_to(user_bg):
        target.unlink()
        return target

    user_themes = roots.user_themes.resolve()
    if parent_real.is_relative_to(user_themes):
        parts = parent_real.relative_to(user_themes).parts
        if len(parts) >= 2 and parts[1] == "backgrounds":
            if theme_from_git(user_themes / parts[0]):
                raise PermissionError(LOCK_GIT)
            target.unlink()
            return target

    raise PermissionError(LOCK_SYSTEM)


# --- themes --------------------------------------------------------------------

def remove_theme(roots: Roots | None, slug: str) -> Path:
    """Remove a *user* theme. System themes are refused; a slug that only
    exists in the system root raises FileNotFoundError.

    A symlinked user theme is unlinked as a link. A directory is removed only
    after proving it resolves inside the user theme root.
    """
    roots = roots or default_roots()
    slug = _validate_slug(slug)
    if slug == current_theme(roots):
        # Omarchy would keep running on a staged copy of a theme that no
        # longer exists, and nothing could re-apply it.
        raise PermissionError("this is the active theme: apply another theme first")
    base = roots.user_themes
    base_real = base.resolve()
    target = base / slug
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(f"theme {slug!r} not found")
    if target.is_symlink():
        target.unlink()
        return target
    if not target.is_dir():
        raise FileNotFoundError(f"theme {slug!r} not found")
    target_real = target.resolve()
    if not target_real.is_relative_to(base_real):
        raise PermissionError(f"refusing to remove {target}: escapes the user theme root")
    shutil.rmtree(target)
    return target


def apply_theme(slug: str, binary: str = "omarchy", timeout: float = 120.0) -> bool:
    """Apply a theme through Omarchy's own CLI (argv vector, no shell)."""
    slug = _validate_slug(slug)
    _, ok = read_bounded_output([binary, "theme", "set", slug],
                                max_bytes=MAX_COMMAND_OUTPUT_BYTES, timeout=timeout)
    return ok


# --- bounded subprocess ----------------------------------------------------------

def read_bounded_output(argv: list[str], max_bytes: int = MAX_COMMAND_OUTPUT_BYTES,
                        timeout: float = 10.0, env: dict | None = None) -> tuple[bytes, bool]:
    """Run argv (no shell) and read stdout with a hard byte cap and deadline.

    Returns ``(output, ok)``; ``ok`` is True only when the command exited 0
    without exceeding the cap or the timeout. A hostile or misbehaving
    command can therefore never exhaust memory or hang the popup.
    """
    try:
        proc = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env)
    except (OSError, ValueError):
        return b"", False

    chunks: list[bytes] = []
    total = 0
    truncated = False
    read_error = False

    def reader() -> None:
        nonlocal total, truncated, read_error
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    truncated = True
                    break
                chunks.append(chunk)
        except (OSError, ValueError):
            read_error = True

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        proc.kill()
        thread.join(2.0)
        truncated = True
    try:
        returncode = proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait()
    ok = returncode == 0 and not truncated and not read_error
    return b"".join(chunks)[:max_bytes], ok


def fetch_https(url: str, max_bytes: int, timeout: float = 15.0) -> bytes | None:
    """GET through curl: HTTPS only, no redirects, size- and time-bounded.
    Callers decide which URLs are allowed; this never follows one elsewhere."""
    if not url.startswith("https://"):
        raise ValueError(f"not an https URL: {url!r}")
    data, ok = read_bounded_output(
        ["curl", "--silent", "--fail", "--proto", "=https", "--max-redirs", "0",
         "--max-time", str(int(timeout)), "--max-filesize", str(max_bytes), "--", url],
        max_bytes=max_bytes, timeout=timeout + 2)
    return data if ok and data else None


def is_image_data(data: bytes) -> bool:
    """WebP, PNG or JPEG by magic bytes."""
    return (data[:4] == b"RIFF" and data[8:12] == b"WEBP") or data[:8] == b"\x89PNG\r\n\x1a\n" \
        or data[:3] == b"\xff\xd8\xff"


def launch_browser(url: str) -> bool:
    """Open a URL in the default browser (Omarchy's launcher, else xdg-open).
    Callers validate the URL first."""
    launcher = shutil.which("omarchy-launch-browser")
    argv = [launcher, url] if launcher else ["xdg-open", url]
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True


def clipboard_image(max_bytes: int = MAX_BACKGROUND_BYTES,
                    timeout: float = 8.0) -> tuple[str, bytes] | None:
    """``(mime, bytes)`` of the first image in the Wayland clipboard, else
    None. `wl-paste` output is bounded like any other subprocess output."""
    types_raw, _ = read_bounded_output(["wl-paste", "--list-types"],
                                       max_bytes=65536, timeout=3.0)
    mimes = [line.strip() for line in types_raw.decode("utf-8", "replace").splitlines()
             if line.strip()]
    for mime in mimes:
        if extension_for_mime(mime) is None:
            continue
        data, ok = read_bounded_output(["wl-paste", "--type", mime],
                                       max_bytes=max_bytes, timeout=timeout)
        if ok and data:
            return mime, data
    return None


def notify(title: str, body: str = "") -> None:
    """Notification with all markup stripped (names and paths are untrusted)."""
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Theme Manager", plain_text(title), plain_text(body)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except OSError:
        pass
