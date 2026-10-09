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

# A slug is what a directory is called. Keep it boring: one path element,
# no control characters, no dots that could become traversal.
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")

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
    try:
        with open(source, "rb") as src, open(dest, "wb") as dst:
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


def list_backgrounds(roots: Roots | None, theme: ThemeInfo) -> list[WallpaperInfo]:
    """The wallpaper set Omarchy shows for a theme: its own backgrounds plus
    the per-theme user folder. A user file with the same name as a shipped
    one shadows it, so each name appears once."""
    roots = roots or default_roots()
    merged: dict[str, WallpaperInfo] = {}

    def scan(directory: Path, source: str, deletable: bool) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda e: e.name.casefold())
        except OSError:
            return
        for entry in entries:
            if not (entry.is_file() and entry.suffix.lower() in IMAGE_EXTS):
                continue
            key = entry.name.casefold()
            if source == "user":
                merged[key] = WallpaperInfo(entry, entry.name, "user", True)
            elif key not in merged:
                merged[key] = WallpaperInfo(entry, entry.name, "theme", deletable)

    scan(roots.user_backgrounds / theme.slug, "user", True)
    scan(theme.path / "backgrounds", "theme", theme.scope == "user")
    return sorted(merged.values(), key=lambda w: w.name.casefold())


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
            target.unlink()
            return target

    raise PermissionError("that wallpaper ships with a system theme and is read-only")


# --- themes --------------------------------------------------------------------

def remove_theme(roots: Roots | None, slug: str) -> Path:
    """Remove a *user* theme. System themes are refused; a slug that only
    exists in the system root raises FileNotFoundError.

    A symlinked user theme is unlinked as a link. A directory is removed only
    after proving it resolves inside the user theme root.
    """
    roots = roots or default_roots()
    slug = _validate_slug(slug)
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
                        timeout: float = 10.0) -> tuple[bytes, bool]:
    """Run argv (no shell) and read stdout with a hard byte cap and deadline.

    Returns ``(output, ok)``; ``ok`` is True only when the command exited 0
    without exceeding the cap or the timeout. A hostile or misbehaving
    command can therefore never exhaust memory or hang the popup.
    """
    try:
        proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
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


def clipboard_image(max_bytes: int = MAX_BACKGROUND_BYTES,
                    timeout: float = 8.0) -> tuple[str, bytes] | None:
    """``(mime, bytes)`` of the first image in the Wayland clipboard, else
    None. `wl-paste` output is bounded like any other subprocess output."""
    types_raw, _ = read_bounded_output(["wl-paste", "--list-types"],
                                       max_bytes=65536, timeout=3.0)
    mimes = [line.strip() for line in types_raw.decode("utf-8", "replace").splitlines()
             if line.strip()]
    for mime in mimes:
        if not mime.startswith("image/"):
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
