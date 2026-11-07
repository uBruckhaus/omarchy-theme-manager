"""Capture the Theme Manager popup with F12, without shell interpolation."""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


def default_folder() -> Path:
    return Path.home() / "Pictures" / "ThemeManager"


def key_enabled(settings: dict) -> bool:
    return settings.get("screenshot_key") is not False   # on unless turned off (as in Maprisma Launcher)


def valid_folder_setting(value) -> bool:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 4096 or \
            any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    path = Path(value)
    if any(part in (".", "..") for part in value.split("/")):
        return False
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
    return True


def folder_from_settings(settings: dict) -> Path:
    value = settings.get("screenshot_folder")
    return Path(value) if valid_folder_setting(value) else default_folder()


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def capture(geometry: tuple[int, int, int, int], folder: Path | None = None,
            now: datetime | None = None) -> Path:
    """Save one PNG, using a private temporary file and a unique final name."""
    x, y, width, height = geometry
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise ValueError("invalid screenshot geometry")
    folder = folder or default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("invalid screenshot folder")
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H-%M-%S")
    stem = f"theme-manager-{stamp}"
    name = folder / f"{stem}.png"
    suffix = 2
    while _entry_exists(name) or _entry_exists(name.with_suffix(".txt")):
        name = folder / f"{stem}-{suffix}.png"
        suffix += 1
    fd, temporary = tempfile.mkstemp(prefix=".theme-manager-shot-", suffix=".png", dir=folder)
    os.close(fd)
    try:
        result = subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", temporary],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=15, check=False)
        if result.returncode != 0:
            raise OSError(result.stderr.decode("utf-8", "replace").strip() or "grim failed")
        if not Path(temporary).is_file() or Path(temporary).stat().st_size == 0:
            raise OSError("grim produced no screenshot")
        # A hard link is atomic and refuses a name planted after the check.
        while True:
            try:
                os.link(temporary, name)
                break
            except FileExistsError:
                name = folder / f"{stem}-{suffix}.png"
                suffix += 1
                while _entry_exists(name) or _entry_exists(name.with_suffix(".txt")):
                    name = folder / f"{stem}-{suffix}.png"
                    suffix += 1
        return name
    finally:
        Path(temporary).unlink(missing_ok=True)


def capture_to_settings(geometry: tuple[int, int, int, int], settings: dict) -> Path:
    """A custom folder that cannot be written falls back to the default."""
    folder = folder_from_settings(settings)
    try:
        return capture(geometry, folder)
    except (OSError, ValueError):
        if folder == default_folder():
            raise
        return capture(geometry, default_folder())


def save_note(picture: Path, note: str) -> Path | None:
    """Write an optional short note beside the screenshot, never replacing one."""
    note = note.strip()
    if not note:
        return None
    if len(note) > 1000:
        raise ValueError("screenshot note is too long")
    path = picture.with_suffix(".txt")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(note + "\n")
    return path
