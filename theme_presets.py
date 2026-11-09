"""Named theme and wallpaper snapshots for Theme Manager.

A preset sets no font (2.1.4): the theme brings its own through Font &
effects (theme_looks). An older preset's saved font is kept in the file,
so old presets and backups stay valid, but it is never applied."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import theme_auto
import theme_core
import theme_custom
import workspace_tiling

MAX_PRESETS = 50
MAX_BYTES = 1024 * 1024   # workspace snapshots: up to 64 KiB each
MAX_SNAPSHOT_BYTES = 64 * 1024


def file_path() -> Path:
    return theme_core.settings_file().parent / "presets.json"


def clean_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("preset name is required")
    if any(not char.isprintable() for char in value):
        raise ValueError("preset name must be 1–60 printable characters")
    name = " ".join(value.split())
    if not name or len(name) > 60:
        raise ValueError("preset name must be 1–60 printable characters")
    return name


def _valid(entry) -> dict | None:
    if not isinstance(entry, dict):
        return None
    try:
        name = clean_name(entry.get("name"))
    except ValueError:
        return None
    theme = entry.get("theme")
    if not isinstance(theme, str) or not theme_core.SLUG_RE.fullmatch(theme):
        return None
    wallpaper = entry.get("wallpaper")
    if wallpaper is not None and (not isinstance(wallpaper, str) or not wallpaper or
                                  len(wallpaper) > 255 or wallpaper != Path(wallpaper).name or
                                  theme_core._sanitize_name(wallpaper) != wallpaper or
                                  not theme_core._is_image_name(wallpaper)):
        return None
    font = entry.get("font")
    if font is not None and (not isinstance(font, str) or not theme_custom.FONT_RE.fullmatch(font)):
        return None
    out = {"name": name, "theme": theme, "wallpaper": wallpaper, "font": font}
    if "workspaces" in entry:
        try:
            # Cleaned as the workspace settings are (older snapshots gain
            # their "manual" entries); only foreign shapes are refused.
            snapshot = workspace_tiling.clean_presets(entry["workspaces"])
            if set(entry["workspaces"]) - set(workspace_tiling.STORE_KEYS) or \
                    len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
                return None
        except (ValueError, TypeError):
            return None
        out["workspaces"] = snapshot
    return out


def load(path: Path | None = None) -> list[dict]:
    path = path or file_path()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > MAX_BYTES:
                return []
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = json.loads(handle.read(MAX_BYTES + 1))
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    seen = set()
    for item in data[:MAX_PRESETS]:
        preset = _valid(item)
        if preset is not None and preset["name"].casefold() not in seen:
            out.append(preset)
            seen.add(preset["name"].casefold())
    return out


def save(presets: list[dict], path: Path | None = None) -> None:
    names = [preset.get("name", "").casefold() for preset in presets if isinstance(preset, dict)
             and isinstance(preset.get("name"), str)]
    if len(presets) > MAX_PRESETS or len(names) != len(presets) or len(names) != len(set(names)) or \
            any(_valid(preset) != preset for preset in presets):
        raise ValueError("invalid presets")
    text = json.dumps(presets, ensure_ascii=False, indent=2) + "\n"
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("presets file too large")   # load() would read it as empty
    path = path or file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    theme_core._atomic_write(path, text, mode=0o600)


def capture_current(name: str, include_workspaces: bool = False) -> dict:
    """include_workspaces: also the workspace presets and App Presets."""
    name = clean_name(name)
    slug = theme_core.current_theme()
    theme = next((item for item in theme_core.discover_themes() if item.slug == slug), None)
    if theme is None:
        raise FileNotFoundError("current theme is unavailable")
    background = theme_auto.current_background()
    wallpaper = background.name if background is not None and theme_auto.wallpaper_named(theme, background.name) else None
    preset = {"name": name, "theme": slug, "wallpaper": wallpaper, "font": None}
    if include_workspaces:
        preset["workspaces"] = workspace_tiling.snapshot()
    return preset


def add_current(name: str, path: Path | None = None, include_workspaces: bool = False) -> dict:
    presets = load(path)
    preset = capture_current(name, include_workspaces)
    if any(item["name"].casefold() == preset["name"].casefold() for item in presets):
        raise ValueError("a preset with this name already exists")
    presets.append(preset)
    save(presets, path)
    return preset


def replace_current(name: str, path: Path | None = None, include_workspaces: bool = False) -> dict:
    presets = load(path)
    preset = capture_current(name, include_workspaces)
    for index, item in enumerate(presets):
        if item["name"].casefold() == preset["name"].casefold():
            presets[index] = preset
            save(presets, path)
            return preset
    raise ValueError("preset does not exist")


def delete(name: str, path: Path | None = None) -> bool:
    presets = load(path)
    kept = [item for item in presets if item["name"].casefold() != name.casefold()]
    if len(kept) == len(presets):
        return False
    save(kept, path)
    return True


def apply(preset: dict) -> list[str]:
    """Apply the theme first (with its own look), then the saved wallpaper if available,
    then the Workspace Presets layouts and app presets saved with it."""
    preset = _valid(preset)
    if preset is None:
        raise ValueError("invalid preset")
    theme = next((item for item in theme_core.discover_themes() if item.slug == preset["theme"]), None)
    if theme is None:
        raise FileNotFoundError("preset theme is not installed")
    if not theme_auto.apply_theme(theme.slug):
        raise OSError("could not apply the preset theme")
    warnings = []
    if preset["wallpaper"]:
        wallpaper = theme_auto.wallpaper_named(theme, preset["wallpaper"])
        if wallpaper is None or not theme_core.set_desktop_background(wallpaper):
            warnings.append("wallpaper unavailable")
        else:
            theme_auto.remember_base_wallpaper(theme.slug, wallpaper)
    if "workspaces" in preset:
        warnings += apply_workspaces(preset["workspaces"])
    return warnings


def apply_workspaces(snapshot: dict) -> list[str]:
    try:
        result = workspace_tiling.apply_snapshot(snapshot)
    except (OSError, ValueError, RuntimeError):
        return ["workspace presets failed"]
    if any(item.get("failed") or item.get("status") not in ("ok", "no_apps", "no_layout")
           for item in result.get("restored") or []) or \
            any(item.get("result") != "ok" for item in result.get("layouts") or []):
        return ["some workspace apps failed"]
    return []


def assign_workspace(preset: dict, workspace: int, *, theme: bool, wallpaper: bool) -> None:
    """Assign either part of a preset to a workspace without switching now."""
    preset = _valid(preset)
    if preset is None or not (theme or wallpaper):
        raise ValueError("invalid workspace preset")
    installed = next((item for item in theme_core.discover_themes()
                      if item.slug == preset["theme"]), None)
    if installed is None:
        raise FileNotFoundError(preset["theme"])
    if wallpaper:
        name = preset["wallpaper"]
        if not name or theme_auto.wallpaper_named(installed, name) is None:
            raise FileNotFoundError("preset wallpaper is unavailable")
        if not theme and preset["theme"] != theme_core.current_theme() and \
                theme_auto.load_workspace_themes().get(workspace) != preset["theme"]:
            raise ValueError("wallpaper-only preset theme is not active")
    if wallpaper:
        theme_auto.set_workspace_wallpaper(preset["theme"], workspace, preset["wallpaper"])
        theme_auto.update_settings(workspace_wallpapers=True)
    if theme:
        theme_auto.set_workspace_theme(workspace, preset["theme"])
        theme_auto.update_settings(workspace_themes=True)


def rename_theme(old: str, new: str, path: Path | None = None) -> None:
    presets = load(path)
    changed = False
    for preset in presets:
        if preset["theme"] == old:
            preset["theme"] = new
            changed = True
    if changed:
        save(presets, path)
