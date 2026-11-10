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


MAX_SHOT = 256 * 1024 * 1024     # a PNG of the popup is far smaller


def _private_dir() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = runtime if runtime and os.path.isdir(runtime) else None
    return tempfile.mkdtemp(prefix="theme-manager-shot-", dir=base)     # 0700, ours


def capture(geometry: tuple[int, int, int, int], folder: Path | None = None,
            now: datetime | None = None) -> Path:
    """Save one PNG. grim writes into a new private folder; the finished file is
    then copied into the chosen folder through a handle on that folder (no
    path is reopened there): the folder must be yours and closed to others,
    the name is created exclusively and never follows a link."""
    x, y, width, height = geometry
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise ValueError("invalid screenshot geometry")
    folder = folder or default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(dir_fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("the screenshot folder must be yours and not writable by others")
        private = _private_dir()
        shot = os.path.join(private, "shot.png")
        try:
            result = subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", shot],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=15, check=False)
            if result.returncode != 0:
                raise OSError("grim failed")
            source = os.open(shot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                size = os.fstat(source).st_size
                if size == 0 or size > MAX_SHOT:
                    raise OSError("grim produced no usable screenshot")
                stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H-%M-%S")
                stem, suffix = f"theme-manager-{stamp}", 1
                while True:
                    name = f"{stem}.png" if suffix == 1 else f"{stem}-{suffix}.png"
                    suffix += 1
                    if _taken(dir_fd, name[:-4] + ".txt"):
                        continue
                    try:
                        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                         | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
                        break
                    except FileExistsError:
                        continue
                try:
                    copied = 0
                    while copied < size:
                        chunk = os.read(source, min(1024 * 1024, size - copied))
                        if not chunk:
                            raise OSError("the screenshot ended early")
                        _write_all(target, chunk)
                        copied += len(chunk)
                    os.fsync(target)
                except BaseException:
                    os.close(target)
                    os.unlink(name, dir_fd=dir_fd)      # nothing cut off is left or reported as saved
                    raise
                os.close(target)
                return folder / name
            finally:
                os.close(source)
        finally:
            for entry in os.listdir(private):
                os.unlink(os.path.join(private, entry))
            os.rmdir(private)
    finally:
        os.close(dir_fd)


def _write_all(fd: int, data: bytes):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _private_folder(folder: Path) -> int:
    """A handle on a screenshot folder that is yours and closed to others (no link)."""
    dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(dir_fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(dir_fd)
        raise ValueError("the screenshot folder must be yours and not writable by others")
    return dir_fd


def _taken(dir_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


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
    dir_fd = _private_folder(picture.parent)           # checked again, then written through its handle
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                     dir_fd=dir_fd)
        try:
            _write_all(fd, (note + "\n").encode("utf-8"))
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            os.unlink(path.name, dir_fd=dir_fd)
            raise
        os.close(fd)
    finally:
        os.close(dir_fd)
    return path
