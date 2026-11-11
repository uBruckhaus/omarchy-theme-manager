"""Portable, bounded backup of Theme Manager preferences and lists."""
from __future__ import annotations

import json
import math
import os
import stat
from datetime import datetime
from pathlib import Path

import theme_core
import theme_presets
import workspace_tiling
import theme_ai
import theme_shortcuts
import theme_auto
import theme_custom
import theme_looks
import i18n
import screenshots
import corners
import header_order

MAX_BACKUP = 4 * 1024 * 1024   # presets.json alone may reach 1 MiB, workspaces.json 512 KiB


def default_export_name(now: datetime | None = None) -> str:
    return "theme-manager-backup-" + (now or datetime.now()).strftime("%Y-%m-%d") + ".json"


def safety_copy_path(now: datetime | None = None) -> Path:
    folder = theme_core.settings_file().parent / "backups"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise PermissionError("unsafe backup folder")
    stem = "theme-manager.before-restore-" + (now or datetime.now()).strftime("%Y-%m-%d-%H-%M-%S")
    path = folder / (stem + ".json")
    suffix = 2
    while _entry_exists(path):
        path = folder / f"{stem}-{suffix}.json"
        suffix += 1
    return path


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
FILES = {
    "settings.json": dict,
    "hidden.json": list,
    "hidden-wallpapers.json": dict,
    "favorites.json": list,
    "recent.json": dict,
    "workspace-wallpapers.json": dict,
    "workspace-themes.json": dict,
    "presets.json": list,
    "shortcuts.json": dict,
    "workspaces.json": dict,   # workspace presets and App Presets (workspace_tiling)
    "looks.json": dict,        # font, window style and text colour per theme (theme_looks)
}


def _read_private(path: Path, limit: int = MAX_BACKUP) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise ValueError(f"unsafe backup input: {path.name}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"backup input too large: {path.name}")
        return data
    finally:
        os.close(fd)


def collect(broken: dict | None = None) -> dict:
    """The state as a backup. With `broken` (a dict), a damaged file does not
    stop it: its raw bytes go there instead (a restore keeps them for its
    rollback, so a bad current file never blocks restoring a good backup)."""
    base = theme_core.settings_file().parent
    files = {}
    for name, expected in FILES.items():
        data = _read_private(base / name)
        if data is None:
            continue
        try:
            value = json.loads(data)
            if not isinstance(value, expected):
                raise ValueError(f"invalid {name}")
        except ValueError:
            if broken is None:
                raise
            broken[name] = data
            continue
        files[name] = value
    return {"format": "theme-manager-backup", "version": 1, "files": files}


def _slug(value) -> bool:
    return isinstance(value, str) and bool(theme_core.SLUG_RE.fullmatch(value))


def _wallpaper(value) -> bool:
    return isinstance(value, str) and value == Path(value).name and \
        theme_core._sanitize_name(value) == value and theme_core._is_image_name(value)


def _validate_settings(value: dict) -> None:
    if "header_order" in value:
        value["header_order"] = header_order.normalize(value["header_order"])
    boolean = ("sync_omarchy", "workspace_wallpapers", "workspace_themes", "screenshot_key",
               "ask_ai", "ask_ai_web", "maprisma_hints", "preview_flip_3d", "preview_pin_apply_theme",
               "preview_big_card")
    if "ask_ai_helper" in value:
        helper = value["ask_ai_helper"]
        local = isinstance(helper, str) and helper.startswith("pi:") and bool(theme_ai.NAME_RE.match(helper[3:]))
        if helper not in theme_ai.HELPERS and not local:
            value.pop("ask_ai_helper")      # an unknown or retired helper (Antigravity): the first ready one
    if any(key in value and not isinstance(value[key], bool) for key in boolean):
        raise ValueError("invalid settings")
    if "wallpaper_count" in value and (isinstance(value["wallpaper_count"], bool)
                                       or value["wallpaper_count"] not in theme_ai.WALLPAPER_COUNTS):
        raise ValueError("invalid settings")
    if "ask_prompts" in value and value["ask_prompts"] is not None and \
            not theme_ai.valid_prompts(value["ask_prompts"]):
        raise ValueError("invalid settings")
    if "language" in value and value["language"] not in ("auto", *i18n.CODES):
        raise ValueError("invalid settings")
    if "sort" in value and value["sort"] not in theme_auto.SORT_CHOICES:
        raise ValueError("invalid settings")
    if "corners" in value and value["corners"] not in corners.CHOICES:
        raise ValueError("invalid settings")
    if "slideshow_minutes" in value and (type(value["slideshow_minutes"]) is not int or
                                         value["slideshow_minutes"] not in theme_auto.SLIDESHOW_CHOICES):
        raise ValueError("invalid settings")
    if "preview_flip_delay" in value and (isinstance(value["preview_flip_delay"], bool) or
                                          not isinstance(value["preview_flip_delay"], (int, float)) or
                                          value["preview_flip_delay"] not in theme_auto.FLIP_DELAY_CHOICES):
        raise ValueError("invalid settings")
    if "own_font" in value and value["own_font"] is not None and \
            (not isinstance(value["own_font"], str) or not theme_custom.FONT_RE.fullmatch(value["own_font"])):
        raise ValueError("invalid settings")
    if "own_windows" in value and value["own_windows"] is not None and \
            value["own_windows"] not in theme_looks.WINDOW_MODES:
        raise ValueError("invalid settings")
    if "screenshot_folder" in value and value["screenshot_folder"] is not None and \
            not screenshots.valid_folder_setting(value["screenshot_folder"]):
        raise ValueError("invalid settings")
    if "size" in value and value["size"] is not None:
        size = value["size"]
        if not isinstance(size, list) or len(size) != 2 or any(type(x) is not int for x in size) or \
                not (420 <= size[0] < 20000 and 320 <= size[1] < 20000):
            raise ValueError("invalid settings")
    if "position" in value and value["position"] is not None:
        pos = value["position"]
        if not isinstance(pos, list) or len(pos) != 2 or any(type(x) is not int or not 0 <= x < 100000 for x in pos):
            raise ValueError("invalid settings")
    if "schedule" in value:
        plan = value["schedule"]
        if not isinstance(plan, dict) or ("enabled" in plan and not isinstance(plan["enabled"], bool)) or \
                any(key in plan and (not isinstance(plan[key], str) or plan[key] and not _slug(plan[key]))
                    for key in ("day", "night")) or \
                any(key in plan and (not isinstance(plan[key], str) or not theme_auto.TIME_RE.fullmatch(plan[key]))
                    for key in ("day_at", "night_at")):
            raise ValueError("invalid settings")


def _validate_value(name: str, value):
    if not isinstance(value, FILES[name]):
        raise ValueError(f"invalid {name}")
    if name == "settings.json":
        _validate_settings(value)
    elif name == "hidden.json":
        if any(not _slug(slug) for slug in value):
            raise ValueError("invalid hidden themes")
        value = [slug for slug in value if slug != theme_core.current_theme()]
    elif name == "hidden-wallpapers.json":
        if any(not _slug(slug) or not isinstance(walls, list) or any(not _wallpaper(wall) for wall in walls)
               for slug, walls in value.items()):
            raise ValueError("invalid hidden wallpapers")
    elif name == "favorites.json":
        if len(value) > theme_auto.MAX_FAVORITES or any(not _slug(slug) for slug in value):
            raise ValueError("invalid favorites")
    elif name == "recent.json":
        if len(value) > theme_auto.MAX_RECENT or any(not _slug(slug) or type(when) not in (int, float)
                or not math.isfinite(when) for slug, when in value.items()):
            raise ValueError("invalid recent themes")
    elif name == "workspace-wallpapers.json":
        if any(not _slug(slug) or not isinstance(mapping, dict) or any(
                not str(ws).isdigit() or not 1 <= int(ws) <= theme_auto.MAX_WORKSPACE or not _wallpaper(wall)
                for ws, wall in mapping.items()) for slug, mapping in value.items()):
            raise ValueError("invalid workspace wallpapers")
    elif name == "workspace-themes.json":
        if any(not str(ws).isdigit() or not 1 <= int(ws) <= theme_auto.MAX_WORKSPACE or not _slug(slug)
               for ws, slug in value.items()):
            raise ValueError("invalid workspace themes")
    elif name == "presets.json":
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("workspaces"), dict):
                item["workspaces"] = workspace_tiling.without_commands(workspace_tiling.clean_presets(item["workspaces"]))
        names = [item.get("name", "").casefold() for item in value if isinstance(item, dict)
                 and isinstance(item.get("name"), str)]
        if len(value) > theme_presets.MAX_PRESETS or len(names) != len(value) or \
                len(set(names)) != len(names) or any(theme_presets._valid(item) != item for item in value):
            raise ValueError("invalid presets")
    elif name == "shortcuts.json":
        seen = set()
        cleaned = {}
        for action, combo in value.items():
            if not theme_shortcuts.valid_action(action) or not isinstance(combo, dict) or \
                    set(combo) - {"mods", "key", "replaces"} or \
                    not isinstance(combo.get("mods"), list) or \
                    any(not isinstance(mod, str) for mod in combo["mods"]) or \
                    not isinstance(combo.get("key"), str):
                raise ValueError("invalid shortcuts")
            normalized = theme_shortcuts.make_combo(combo["mods"], combo["key"])
            if normalized is None or normalized["mods"] != combo["mods"] or normalized["key"] != combo["key"]:
                raise ValueError("invalid shortcuts")
            combo_id = theme_shortcuts.combo_id(normalized)
            if combo_id in seen:
                raise ValueError("duplicate shortcuts")
            seen.add(combo_id)
            replaces = combo.get("replaces")
            if replaces is not None:
                if not isinstance(replaces, str) or len(replaces) > 80:
                    raise ValueError("invalid shortcuts")
                normalized["replaces"] = theme_core.plain_text(replaces, 80)
            cleaned[action] = normalized
        theme_shortcuts.render_lua(cleaned)
        value = cleaned
    elif name == "workspaces.json":
        value = workspace_tiling.without_commands(workspace_tiling.clean_store(value))
    elif name == "looks.json":
        if theme_looks.check_looks(value) != value:
            raise ValueError("invalid looks")
    return value


def export(path: Path, broken: dict | None = None) -> None:
    payload = json.dumps(collect(broken), ensure_ascii=False, indent=2) + "\n"
    if len(payload.encode()) > MAX_BACKUP:
        raise ValueError("backup is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    theme_core._atomic_write(path, payload, mode=0o600)


def preview(path: Path) -> dict:
    data = _read_private(path)
    if data is None:
        raise FileNotFoundError(path)
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get("format") != "theme-manager-backup" or payload.get("version") != 1:
        raise ValueError("unsupported backup format")
    files = payload.get("files")
    if not isinstance(files, dict) or set(files) - set(FILES):
        raise ValueError("invalid backup files")
    checked = {name: _validate_value(name, value) for name, value in files.items()}
    # Every file must stay readable after the restore: its loader refuses larger ones.
    limits = {"presets.json": theme_presets.MAX_BYTES, "workspaces.json": workspace_tiling.STORE_MAX_BYTES,
              "looks.json": theme_looks.LOOKS_MAX}
    for name, value in checked.items():
        size = len((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if size > limits.get(name, MAX_BACKUP):
            raise ValueError(f"backup file too large: {name}")
    return checked


def restore(path: Path, previous: Path | None = None) -> Path:
    """Replace the state with a backup's. Under the workspace presets' lock,
    so a change on the Workspaces tab can't be written in between and lost."""
    # Lock order as everywhere: workspaces, then looks, then settings, so a
    # Save, Undo or theme apply can't write looks.json or own_font/own_windows
    # in between and be lost, or be overwritten.
    with workspace_tiling.runtime_lock(), theme_looks._locked(), theme_core.settings_lock():
        return _restore(path, previous)


def _restore(path: Path, previous: Path | None = None) -> Path:
    files = preview(path)
    broken = {}
    prior = collect(broken)["files"]
    old_sync = theme_core.sync_enabled()
    # Always keep an automatic rollback copy before replacing anything.
    previous = previous or safety_copy_path()
    previous.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(previous, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    try:
        export(previous, {})      # the safety copy: the readable files
    except Exception:
        previous.unlink(missing_ok=True)
        raise
    base = theme_core.settings_file().parent
    base.mkdir(parents=True, exist_ok=True)
    plugin = Path(__file__).resolve().parent
    wrapper, hook = plugin / "theme-switcher", plugin / "switcher_sync.py"
    shortcut_changed = "shortcuts.json" in files or "shortcuts.json" in prior
    try:
        for name in FILES:
            if name == "shortcuts.json":
                continue
            if name in files:
                theme_core._atomic_write(base / name, json.dumps(files[name], ensure_ascii=False, indent=2) + "\n", mode=0o600)
            else:
                (base / name).unlink(missing_ok=True)
        if shortcut_changed:
            theme_shortcuts.save_and_apply(files.get("shortcuts.json", {}))
            if "shortcuts.json" not in files:
                (base / "shortcuts.json").unlink(missing_ok=True)
        desired_sync = files.get("settings.json", {}).get("sync_omarchy") is True
        theme_core.set_sync_enabled(desired_sync, wrapper, hook)
        if "settings.json" not in files:
            (base / "settings.json").unlink(missing_ok=True)
        elif "sync_omarchy" not in files["settings.json"]:
            theme_core._atomic_write(base / "settings.json",
                                     json.dumps(files["settings.json"], ensure_ascii=False, indent=2) + "\n", mode=0o600)
    except Exception as original:
        rollback_errors = []
        for name in FILES:
            if name == "shortcuts.json":
                continue
            if name in prior:
                try:
                    theme_core._atomic_write(base / name, json.dumps(prior[name], ensure_ascii=False, indent=2) + "\n", mode=0o600)
                except OSError as error:
                    rollback_errors.append(error)
            elif name in broken:      # put the damaged file back as it was
                try:
                    theme_core._atomic_write_bytes(base / name, broken[name], mode=0o600)   # byte for byte
                except OSError as error:
                    rollback_errors.append(error)
            else:
                try:
                    (base / name).unlink(missing_ok=True)
                except OSError as error:
                    rollback_errors.append(error)
        if shortcut_changed:
            try:
                theme_shortcuts.save_and_apply(prior.get("shortcuts.json", {}))
                if "shortcuts.json" in broken:
                    theme_core._atomic_write_bytes(base / "shortcuts.json", broken["shortcuts.json"], mode=0o600)
                elif "shortcuts.json" not in prior:
                    (base / "shortcuts.json").unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError) as error:
                rollback_errors.append(error)
        try:
            theme_core.set_sync_enabled(old_sync, wrapper, hook)
            if "settings.json" in prior:
                theme_core._atomic_write(base / "settings.json",
                                         json.dumps(prior["settings.json"], ensure_ascii=False, indent=2) + "\n", mode=0o600)
            elif "settings.json" in broken:
                theme_core._atomic_write_bytes(base / "settings.json", broken["settings.json"], mode=0o600)
            else:
                (base / "settings.json").unlink(missing_ok=True)
        except (OSError, RuntimeError, ValueError) as error:
            rollback_errors.append(error)
        if rollback_errors:
            raise RuntimeError(f"restore failed: {original}; rollback failed: {rollback_errors[0]}") from original
        raise
    return previous
