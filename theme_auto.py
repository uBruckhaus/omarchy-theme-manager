#!/usr/bin/env python3
"""Theme Manager automation: favorite and recently used themes, a wallpaper
per workspace, a wallpaper slideshow and day/night themes.

Also the command line the bar widget calls (argv only, never a shell):

  theme_auto.py workspace <id>   the focused workspace changed
  theme_auto.py tick             once a minute while the slideshow or day/night is on
  theme_auto.py bg next|prev     scrolling on the bar icon
  theme_auto.py apply <slug>     a theme picked in the bar's right-click menu (or its key)
  theme_auto.py theme next|prev the next favorite theme (a key)
  theme_auto.py menu             JSON for that menu

Everything is off until switched on in the popup's settings. All of it goes
through theme_core: wallpapers are only ever ones the theme already lists
(stored by file name, resolved at use), backgrounds are set with Omarchy's
own `omarchy-theme-bg-set`, themes applied with `omarchy theme set`.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

import theme_core
import theme_custom
import theme_looks
from i18n import _

SLIDESHOW_CHOICES = (0, 5, 10, 15, 30, 60, 120)   # minutes; 0 = off
FLIP_DELAY_CHOICES = (0, 0.5, 1, 1.5, 2, 3, 5, 10)  # seconds; 0 = arrows on preview, no auto-flip
DEFAULT_FLIP_DELAY = 1
SORT_CHOICES = ("name", "recent")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
DEFAULT_SCHEDULE = {"enabled": False, "day": "", "night": "", "day_at": "07:00", "night_at": "19:00"}
MAX_WORKSPACE = 99
MAX_RECENT = 50
MAX_FAVORITES = 100


# --- files -------------------------------------------------------------------

def config_dir() -> Path:
    return theme_core.settings_file().parent


def favorites_file() -> Path:
    return config_dir() / "favorites.json"


def recent_file() -> Path:
    return config_dir() / "recent.json"


def workspace_file() -> Path:
    return config_dir() / "workspace-wallpapers.json"


def workspace_themes_file() -> Path:
    return config_dir() / "workspace-themes.json"


def workspace_theme_state_file() -> Path:
    # The base theme must survive a reboot or cache cleanup while an assigned
    # workspace is active.
    return config_dir() / "workspace-theme-state.json"


def remember_base_theme(slug: str) -> None:
    """A manual or scheduled choice becomes the theme restored on exit."""
    slug = theme_core._validate_slug(slug)
    state = _read_json(workspace_theme_state_file(), {})
    if state.get("assigned_workspace") is not None:
        state["base_theme"] = slug
        _write_json(workspace_theme_state_file(), state)


def remember_base_wallpaper(slug: str, path: Path) -> None:
    state = _read_json(state_file(), {})
    state["theme"] = theme_core._validate_slug(slug)
    state["base"] = str(path)
    _write_json(state_file(), state)


def state_file() -> Path:
    """Short-lived state (the background in use before a workspace took
    over, the last slideshow change). Losing it is harmless: it is a cache."""
    return Path.home() / ".cache/theme-manager/auto-state.json"


STATE_MAX = 4 * 1024 * 1024     # a settings or state file larger than this is not read


def _read_json(path: Path, default):
    """A JSON file of ours: a regular file (a dotfiles link to one is fine; a
    FIFO or device never), at most STATE_MAX bytes, opened without blocking;
    else the default."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return default
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > STATE_MAX:
            return default
        raw = os.read(fd, STATE_MAX + 1)
        if len(raw) > STATE_MAX:
            return default
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return default
    finally:
        os.close(fd)
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, data) -> None:
    theme_core._atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o600)


def _valid_slug(slug) -> bool:
    return isinstance(slug, str) and bool(theme_core.SLUG_RE.match(slug))


# A step that applies a theme takes seconds; one still waiting after this
# gives up (exit 1) instead of hanging the bar's helper queue.
AUTOMATION_LOCK_SECONDS = 60.0


@contextlib.contextmanager
def locked():
    """One automation step at a time: the bar widget exists once per
    monitor, so the same event can arrive twice."""
    path = state_file().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PermissionError(f"{path} is not a private lock file")
        theme_core.flock_within(fd, AUTOMATION_LOCK_SECONDS)   # never waits forever
        yield
    finally:
        os.close(fd)


# --- settings -------------------------------------------------------------------

def settings() -> dict:
    return theme_core._load_settings(theme_core.settings_file())


def update_settings(**values) -> None:
    theme_core._update_settings(theme_core.settings_file(), **values)


def store_default_settings() -> None:
    """Defaults that are on stored as on, so settings.json and a backup show
    them: Search the web for wallpapers and 3D wallpaper flip (only missing
    values; yours stay). Which ones are missing is decided under the settings
    lock, so a choice saved meanwhile is never overwritten by a default."""
    path = theme_core.settings_file()
    try:
        with theme_core.settings_lock(path):
            data = theme_core._load_settings(path)
            missing: dict = {key: True for key in ("ask_ai_web", "preview_flip_3d", "preview_pin_apply_theme",
                                                   "preview_big_card")
                             if not isinstance(data.get(key), bool)}
            delay = data.get("preview_flip_delay")
            if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay not in FLIP_DELAY_CHOICES:
                missing["preview_flip_delay"] = DEFAULT_FLIP_DELAY
            if missing:
                data.update(missing)
                theme_core._atomic_write(path, json.dumps(data, indent=2) + "\n", mode=0o600)
    except OSError:
        pass


def preview_flip_enabled() -> bool:
    return settings().get("preview_flip_3d") is not False


def preview_pin_apply_theme_enabled() -> bool:
    return settings().get("preview_pin_apply_theme") is not False


def preview_big_card_enabled() -> bool:
    return settings().get("preview_big_card") is not False


def preview_flip_delay() -> int | float:
    value = settings().get("preview_flip_delay")
    if not isinstance(value, bool) and isinstance(value, (int, float)) and value in FLIP_DELAY_CHOICES:
        return int(value) if value == int(value) else float(value)
    return DEFAULT_FLIP_DELAY


def workspace_wallpapers_enabled() -> bool:
    return settings().get("workspace_wallpapers") is True


def workspace_themes_enabled() -> bool:
    return settings().get("workspace_themes") is True


def slideshow_minutes() -> int:
    value = settings().get("slideshow_minutes")
    return value if isinstance(value, int) and not isinstance(value, bool) and value in SLIDESHOW_CHOICES else 0


def sort_order() -> str:
    value = settings().get("sort")
    return value if value in SORT_CHOICES else "name"


def schedule() -> dict:
    """Day/night themes: {"enabled", "day", "night", "day_at", "night_at"}."""
    raw = settings().get("schedule")
    out = dict(DEFAULT_SCHEDULE)
    if isinstance(raw, dict):
        if isinstance(raw.get("enabled"), bool):
            out["enabled"] = raw["enabled"]
        for key in ("day", "night"):
            if _valid_slug(raw.get(key)):
                out[key] = raw[key]
        for key in ("day_at", "night_at"):
            if isinstance(raw.get(key), str) and TIME_RE.match(raw[key]):
                out[key] = raw[key]
    return out


def save_schedule(**values) -> dict:
    current = schedule()
    current.update(values)
    update_settings(schedule=current)
    # A changed plan takes effect at once, not at the next boundary.
    state = _read_json(state_file(), {})
    state.pop("period", None)
    _write_json(state_file(), state)
    return current


# --- favorites and recently used ------------------------------------------------------

def load_favorites() -> list[str]:
    raw = _read_json(favorites_file(), [])
    seen, out = set(), []
    for slug in raw:
        if _valid_slug(slug) and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out[:MAX_FAVORITES]


def set_favorite(slug: str, on: bool) -> list[str]:
    favorites = [s for s in load_favorites() if s != slug]
    if on:
        favorites.append(theme_core._validate_slug(slug))
    _write_json(favorites_file(), favorites[:MAX_FAVORITES])
    return favorites


def load_recent() -> dict[str, float]:
    raw = _read_json(recent_file(), {})
    return {slug: float(when) for slug, when in raw.items()
            if _valid_slug(slug) and isinstance(when, (int, float)) and not isinstance(when, bool)}


def record_recent(slug: str, when: float | None = None) -> None:
    if not _valid_slug(slug):
        return
    recent = load_recent()
    recent[slug] = time.time() if when is None else when
    newest = sorted(recent.items(), key=lambda item: -item[1])[:MAX_RECENT]
    _write_json(recent_file(), dict(newest))


def note_current_theme() -> None:
    """Count the theme in use as recently used, also when it was applied
    outside Theme Manager (Omarchy's picker, `omarchy theme set`)."""
    slug = theme_core.current_theme()
    recent = load_recent()
    if slug and (not recent or max(recent, key=recent.get) != slug):
        record_recent(slug)


def order_themes(themes: list, favorites: list[str], recent: dict[str, float], sort: str) -> list:
    """Favorites first (in the order they were added), then the rest by
    name or by when they were last used."""
    position = {slug: i for i, slug in enumerate(favorites)}
    favs = sorted((t for t in themes if t.slug in position), key=lambda t: position[t.slug])
    rest = [t for t in themes if t.slug not in position]
    if sort == "recent":
        rest.sort(key=lambda t: (-recent.get(t.slug, 0.0), t.name.casefold()))
    return favs + rest


def rename_state(old: str, new: str) -> None:
    """Everything Theme Manager remembers about theme `old` now belongs to
    `new`: favorites, recently used, workspace wallpapers, day/night."""
    favorites = [new if slug == old else slug for slug in load_favorites()]
    if favorites != load_favorites():
        _write_json(favorites_file(), favorites)
    recent = load_recent()
    if old in recent:
        recent[new] = recent.pop(old)
        _write_json(recent_file(), recent)
    mapping = load_workspace_map()
    workspace_themes = load_workspace_themes()
    if old in workspace_themes.values():
        _write_json(workspace_themes_file(),
                    {str(ws): new if slug == old else slug for ws, slug in workspace_themes.items()})
    if old in mapping:
        mapping[new] = mapping.pop(old)
        save_workspace_map(mapping)
    plan = schedule()
    changed = {key: new for key in ("day", "night") if plan[key] == old}
    if changed:
        save_schedule(**changed)


# --- wallpapers per workspace ------------------------------------------------------------

def load_workspace_themes() -> dict[int, str]:
    raw = _read_json(workspace_themes_file(), {})
    return {int(ws): slug for ws, slug in raw.items()
            if isinstance(ws, str) and ws.isdigit() and 1 <= int(ws) <= MAX_WORKSPACE
            and _valid_slug(slug)}


def set_workspace_theme(workspace: int, slug: str | None) -> None:
    if not 1 <= int(workspace) <= MAX_WORKSPACE:
        raise ValueError(_("workspace out of range"))
    if slug is not None:
        slug = theme_core._validate_slug(slug)
        if slug not in {theme.slug for theme in theme_core.discover_themes()}:
            raise FileNotFoundError(slug)
    with locked():          # never between "Use on all workspaces" and its Undo
        mapping = load_workspace_themes()
        if slug is None:
            mapping.pop(int(workspace), None)
        else:
            mapping[int(workspace)] = slug
        _write_json(workspace_themes_file(), {str(ws): name for ws, name in sorted(mapping.items())})


def use_everywhere() -> dict | None:
    """"Use on all workspaces": no workspace keeps a theme of its own, and the
    theme applied now becomes the one every workspace shows (also the one
    restored after a workspace's own theme). The theme-specific workspace
    wallpapers stay. Returns what Undo needs, or None when nothing was set."""
    with locked():
        mapping = load_workspace_themes()
        current = theme_core.current_theme()
        if not mapping or not current:
            return None                 # nothing set, or no theme to keep everywhere
        state = _read_json(workspace_theme_state_file(), {})
        new_state = dict(state)
        new_state["assigned_workspace"] = None
        new_state.pop("assigned_slug", None)
        new_state["base_theme"] = current
        _write_json(workspace_themes_file(), {})
        _write_json(workspace_theme_state_file(), new_state)
        return {"mapping": mapping, "state": state, "new_state": new_state, "theme": current}


def undo_use_everywhere(saved: dict) -> bool:
    """Puts the workspaces' own themes back, only while nothing changed since
    (a new assignment, a manual or scheduled theme). True if restored."""
    with locked():
        if load_workspace_themes() or \
                _read_json(workspace_theme_state_file(), {}) != saved["new_state"] or \
                theme_core.current_theme() != saved["theme"]:
            return False
        _write_json(workspace_themes_file(), {str(ws): slug for ws, slug in sorted(saved["mapping"].items())})
        _write_json(workspace_theme_state_file(), saved["state"])
    return True


def apply_workspace(workspace: int) -> Path | None:
    """Switch the global theme for a focused workspace, then its wallpaper."""
    state = _read_json(workspace_theme_state_file(), {})
    slug = load_workspace_themes().get(workspace) if workspace_themes_enabled() else None
    current = theme_core.current_theme()
    if slug:
        if state.get("assigned_workspace") is None:
            state["base_theme"] = current
        elif current not in (state.get("assigned_slug"), state.get("base_theme")):
            state["base_theme"] = current
        if slug != current:
            try:
                if not apply_theme(slug, track_base=False):
                    slug = None
            except (FileNotFoundError, OSError, ValueError):
                slug = None  # A removed theme must not break workspace events.
        if slug:
            state["assigned_workspace"] = workspace
            state["assigned_slug"] = slug
    if not slug:
        base = state.get("base_theme")
        assigned = state.get("assigned_slug")
        if state.get("assigned_workspace") is not None:
            if current not in (assigned, base):
                # A manual choice or the schedule supersedes the workspace override.
                base = current
            elif base and base != current:
                try:
                    apply_theme(base, track_base=False)
                except (FileNotFoundError, OSError, ValueError):
                    pass
        state["assigned_workspace"] = None
        state.pop("assigned_slug", None)
        state["base_theme"] = theme_core.current_theme()
    _write_json(workspace_theme_state_file(), state)
    return on_workspace(workspace)

def load_workspace_map() -> dict[str, dict[int, str]]:
    """{theme slug: {workspace id: wallpaper file name}}."""
    raw = _read_json(workspace_file(), {})
    out: dict[str, dict[int, str]] = {}
    for slug, assignments in raw.items():
        if not _valid_slug(slug) or not isinstance(assignments, dict):
            continue
        for ws, name in assignments.items():
            if isinstance(ws, str) and ws.isdigit() and 1 <= int(ws) <= MAX_WORKSPACE \
                    and isinstance(name, str) and theme_core._sanitize_name(name) == name and name:
                out.setdefault(slug, {})[int(ws)] = name
    return out


def save_workspace_map(mapping: dict[str, dict[int, str]]) -> None:
    _write_json(workspace_file(), {slug: {str(ws): name for ws, name in sorted(a.items())}
                                   for slug, a in mapping.items() if a})


def set_workspace_wallpaper(slug: str, workspace: int, name: str | None) -> None:
    """Show wallpaper `name` of theme `slug` on `workspace` (None: clear it)."""
    slug = theme_core._validate_slug(slug)
    if not 1 <= int(workspace) <= MAX_WORKSPACE:
        raise ValueError(_("workspace out of range"))
    mapping = load_workspace_map()
    assignments = mapping.setdefault(slug, {})
    if name is None:
        assignments.pop(int(workspace), None)
    else:
        if theme_core._sanitize_name(name) != name:
            raise ValueError(_("bad wallpaper name"))
        assignments[int(workspace)] = name
    save_workspace_map(mapping)


def clear_wallpaper_workspaces(slug: str, name: str) -> None:
    mapping = load_workspace_map()
    assignments = mapping.get(slug, {})
    for ws in [ws for ws, n in assignments.items() if n == name]:
        del assignments[ws]
    save_workspace_map(mapping)


def workspaces_of(slug: str, name: str, mapping: dict | None = None) -> list[int]:
    mapping = load_workspace_map() if mapping is None else mapping
    return sorted(ws for ws, n in mapping.get(slug, {}).items() if n == name)


def active_workspace() -> int | None:
    raw = theme_core._hyprland_query(b"j/activeworkspace")
    if raw is None:
        raw, ok = theme_core.read_bounded_output(["hyprctl", "activeworkspace", "-j"],
                                                 max_bytes=65536, timeout=1.0)
        if not ok:
            return None
    try:
        value = int(json.loads(raw)["id"])
    except (ValueError, KeyError, TypeError):
        return None
    return value if value >= 1 else None


# --- backgrounds -------------------------------------------------------------------

def current_theme_info():
    slug = theme_core.current_theme()
    return next((t for t in theme_core.discover_themes() if t.slug == slug), None)


def current_background() -> Path | None:
    try:
        return Path(os.readlink(theme_core.default_roots().state_current / "background"))
    except OSError:
        return None


def visible_wallpapers(theme) -> list[Path]:
    return [w.path for w in theme_core.list_backgrounds(None, theme) if not w.hidden]


def flip_wallpapers(theme, hidden: dict[str, set[str]] | None = None,
                    current_bg: Path | None = None) -> list[Path]:
    """The visible wallpapers cycled on a theme card's 3D flip: rotated so the
    active desktop wallpaper comes first when `theme` is current, or the
    theme's remembered wallpaper comes first otherwise."""
    visible = [w.path for w in theme_core.list_backgrounds(None, theme, hidden=hidden) if not w.hidden]
    if not visible:
        return []
    if getattr(theme, "current", False):
        bg = current_background() if current_bg is None else current_bg
        if bg is not None:
            index = next((i for i, p in enumerate(visible) if p == bg or _same_wallpaper(p, bg)), -1)
            if index > 0:
                visible = visible[index:] + visible[:index]
    else:
        remembered = remembered_omarchy_theme_background(theme.slug)
        if remembered:
            index = next((i for i, p in enumerate(visible) if p.name == remembered), -1)
            if index > 0:
                visible = visible[index:] + visible[:index]
    if len(visible) == 1 and getattr(theme, "preview", None):
        try:
            if os.path.samefile(theme.preview, visible[0]):
                return []
        except OSError:
            pass
    return visible


def flip_wallpaper(theme, hidden: dict[str, set[str]] | None = None,
                   current_bg: Path | None = None) -> Path | None:
    """The first wallpaper shown on the back of a theme card's 3D flip."""
    paths = flip_wallpapers(theme, hidden=hidden, current_bg=current_bg)
    return paths[0] if paths else None


def remembered_omarchy_theme_background(slug: str) -> str | None:
    """File name stored in Omarchy's per-theme background state for `slug`, if any."""
    try:
        slug = theme_core._validate_slug(slug)
    except ValueError:
        return None
    state_file_path = theme_core.default_roots().state_current.parent / "theme-backgrounds" / slug
    # Read on GTK's main loop while a card is built: bounded, never through a link, never waiting on
    # a FIFO, and checked on the opened file itself.
    try:
        fd = os.open(state_file_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, 4097)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(raw) > 4096:
        return None
    raw = raw.decode("utf-8", "replace").strip()
    if not raw:
        return None
    name = Path(raw.splitlines()[0]).name
    return name if theme_core._sanitize_name(name) == name else None


def remember_omarchy_theme_background(slug: str, path: Path) -> bool:
    """Record `path` in Omarchy's per-theme background state so `omarchy theme set`
    switches straight to the pinned wallpaper without flashing another first."""
    slug = theme_core._validate_slug(slug)
    roots = theme_core.default_roots()
    state_dir = roots.state_current.parent / "theme-backgrounds"
    if state_dir.is_symlink():
        return False
    try:
        user_bg = (roots.user_backgrounds / slug).resolve()
        is_user_bg = path.resolve().is_relative_to(user_bg)
    except OSError:
        is_user_bg = False
    recorded = path if is_user_bg else (roots.state_current / "theme" / "backgrounds" / path.name)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        theme_core._atomic_write(state_dir / slug, f"{recorded}\n", mode=0o600)
        return True
    except OSError:
        return False


def pin_current_wallpaper(slug: str, path: Path) -> bool:
    """Apply `path` on the already-active theme `slug`, updating workspace or
    base wallpaper state just like stepping backgrounds does."""
    slug = theme_core._validate_slug(slug)
    roots = theme_core.default_roots()
    staged = roots.state_current / "theme" / "backgrounds" / path.name
    try:
        is_user_bg = path.resolve().is_relative_to((roots.user_backgrounds / slug).resolve())
    except OSError:
        is_user_bg = False
    target = staged if (not is_user_bg and staged.is_file()) else path
    with locked():
        remember_omarchy_theme_background(slug, path)
        if workspace_wallpapers_enabled():
            workspace = active_workspace()
            assignments = load_workspace_map().get(slug, {})
            if workspace is not None and workspace in assignments:
                set_workspace_wallpaper(slug, workspace, path.name)
                return theme_core.set_desktop_background(target)
        ok = theme_core.set_desktop_background(target)
        if ok:
            remember_base_wallpaper(slug, target)
        return ok


def wallpaper_named(theme, name: str) -> Path | None:
    """The theme's visible wallpaper with this file name, if any: assignments
    never point anywhere else, and a hidden wallpaper is skipped."""
    return next((path for path in visible_wallpapers(theme) if path.name == name), None)


def _same_wallpaper(a: Path | None, b: Path | None) -> bool:
    """Omarchy's background link usually points at its staged copy of the
    theme (~/.local/state/omarchy/current/theme/backgrounds/), so the same
    wallpaper shows up under two paths: compare by file name."""
    return a is not None and b is not None and a.name == b.name


def _show(path: Path) -> bool:
    current = current_background()
    if _same_wallpaper(current, path) and current.exists():
        return True
    if theme_core.image_size_problem(path):
        return False   # never hand the shell an image it would have to decode at a huge size
    return theme_core.set_desktop_background(path)


def on_workspace(workspace: int) -> Path | None:
    """The focused workspace changed: show its wallpaper, or go back to the
    background in use before a workspace wallpaper took over."""
    if not workspace_wallpapers_enabled() or workspace < 1:
        return None
    theme = current_theme_info()
    if theme is None:
        return None
    assignments = load_workspace_map().get(theme.slug, {})
    state = _read_json(state_file(), {})
    if state.get("theme") != theme.slug:
        state = {key: state[key] for key in ("period", "slide_at") if key in state}
        state["theme"] = theme.slug   # a new theme brings its own background
    left = state.get("workspace")
    if not (isinstance(left, int) and left in assignments):
        # Leaving a workspace without its own wallpaper: remember what it showed.
        background = current_background()
        if background is not None:
            state["base"] = str(background)
    state["workspace"] = workspace
    target = wallpaper_named(theme, assignments[workspace]) if workspace in assignments else None
    if target is None:
        base = state.get("base")
        target = Path(base) if isinstance(base, str) and base and Path(base).is_file() else None
    _write_json(state_file(), state)
    if target is not None:
        _show(target)
    return target


def step_background(direction: int) -> Path | None:
    """Next (+1) or previous (-1) visible wallpaper of the current theme. On
    a workspace with its own wallpaper, that workspace gets the new one."""
    theme = current_theme_info()
    if theme is None:
        return None
    wallpapers = visible_wallpapers(theme)
    if not wallpapers:
        return None
    current = current_background()
    index = next((i for i, path in enumerate(wallpapers) if _same_wallpaper(path, current)), -1)
    target = wallpapers[(index + direction) % len(wallpapers)] if index >= 0 or direction > 0 \
        else wallpapers[-1]
    if workspace_wallpapers_enabled():
        workspace = active_workspace()
        assignments = load_workspace_map().get(theme.slug, {})
        if workspace is not None and workspace in assignments:
            set_workspace_wallpaper(theme.slug, workspace, target.name)
        else:
            state = _read_json(state_file(), {})
            if state.get("theme") == theme.slug:
                state["base"] = str(target)
                _write_json(state_file(), state)
    _show(target)
    return target


# --- slideshow and day/night ----------------------------------------------------------------

def period_at(now: time.struct_time, day_at: str, night_at: str) -> str:
    minutes = now.tm_hour * 60 + now.tm_min
    day = int(day_at[:2]) * 60 + int(day_at[3:])
    night = int(night_at[:2]) * 60 + int(night_at[3:])
    if day == night:
        return "day"
    inside = day <= minutes < night if day < night else not (night <= minutes < day)
    return "day" if inside else "night"


def apply_theme(slug: str, *, track_base: bool = True) -> bool:
    """Apply a theme the way the popup does: a hidden theme is unhidden
    first, and it counts as recently used."""
    slug = theme_core._validate_slug(slug)
    if slug not in {t.slug for t in theme_core.discover_themes()}:
        raise FileNotFoundError(_("theme {0} is not installed").format(repr(slug)))
    try:
        theme_core.unhide_theme(slug)
    except OSError:
        pass
    ok = theme_core.apply_theme(slug)
    if ok:
        record_recent(slug)
        try:
            theme_looks.apply_look(slug)
        except Exception:  # noqa: BLE001 - the theme is applied; its look is extra
            pass
        if track_base:
            remember_base_theme(slug)
    return ok


def tick(now: float | None = None) -> dict:
    """Once a minute: switch between day and night theme at the set times,
    and move the slideshow on. Returns what was done (for tests)."""
    now = time.time() if now is None else now
    done = {}
    state = _read_json(state_file(), {})
    plan = schedule()
    if plan["enabled"] and plan["day"] and plan["night"]:
        period = period_at(time.localtime(now), plan["day_at"], plan["night_at"])
        # Only at a boundary: a theme picked by hand stays until the next one.
        if state.get("period") != period:
            state["period"] = period
            _write_json(state_file(), state)
            wanted = plan[period]
            active = active_workspace() if workspace_themes_enabled() else None
            assigned = active is not None and active in load_workspace_themes()
            if assigned:
                remember_base_theme(wanted)
            elif wanted != theme_core.current_theme():
                done["theme"] = wanted
                apply_theme(wanted)
                state = _read_json(state_file(), {})
    minutes = slideshow_minutes()
    if minutes:
        last = state.get("slide_at")
        if not isinstance(last, (int, float)) or now - last >= minutes * 60 - 5:
            state["slide_at"] = now
            _write_json(state_file(), state)
            theme = current_theme_info()
            workspace = active_workspace() if workspace_wallpapers_enabled() else None
            on_own = theme is not None and workspace is not None and \
                workspace in load_workspace_map().get(theme.slug, {})
            # A workspace with its own wallpaper keeps it.
            if isinstance(last, (int, float)) and not on_own:
                done["background"] = step_background(1)
    return done


def step_theme(direction: int) -> str | None:
    """Apply the next (or previous) favorite theme; without favorites, the
    next theme that is not hidden."""
    themes = [t.slug for t in theme_core.discover_themes()]
    pool = [s for s in load_favorites() if s in themes]
    if not pool:
        hidden = theme_core.load_hidden()
        pool = [s for s in themes if s not in hidden]
    if not pool:
        return None
    current = theme_core.current_theme()
    index = pool.index(current) if current in pool else (-1 if direction > 0 else 0)
    target = pool[(index + direction) % len(pool)]
    if target != current:
        apply_theme(target)
    return target


# --- bar menu ---------------------------------------------------------------------------

def menu_data() -> dict:
    """Favorite themes for the bar's right-click menu; the five most recently
    used when there are no favorites yet."""
    themes = {t.slug: t for t in theme_core.discover_themes()}
    current = theme_core.current_theme()
    favorites = [s for s in load_favorites() if s in themes]
    source = "favorites"
    if not favorites:
        recent = load_recent()
        favorites = [s for s in sorted(recent, key=lambda s: -recent[s]) if s in themes][:5]
        source = "recent"
    return {"source": source,
            "themes": [{"slug": s, "name": theme_core.plain_text(themes[s].name, 60), "current": s == current}
                       for s in favorites]}


def forget_dead_key(slug: str) -> int:
    """A theme's key was pressed but the theme is gone (deleted outside
    Theme Manager): say so and give the key back."""
    import theme_shortcuts
    name = theme_core.display_name(slug)
    try:
        removed = theme_shortcuts.drop_theme(slug)
    except (OSError, RuntimeError, ValueError) as error:
        theme_core.notify(_("{0} no longer exists").format(name), _("Its key could not be removed: {0}").format(error))
        return 1
    theme_core.notify(_("{0} no longer exists").format(name),
                      _("Its key was removed and is free again.") if removed else "")
    return 1


def bar_state() -> dict:
    """What the bar widget needs, from settings.json and workspaces.json read
    bounded here (the shell never loads either file itself)."""
    data = settings()
    tiling = _read_json(config_dir() / "workspaces.json", {})
    layouts = tiling.get("workspaceLayouts") if isinstance(tiling.get("workspaceLayouts"), dict) else {}
    plan = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
    minutes = data.get("slideshow_minutes")
    language = data.get("language")
    return {
        "workspace_wallpapers": data.get("workspace_wallpapers") is True,
        "workspace_themes": data.get("workspace_themes") is True,
        "timed": (type(minutes) is int and minutes > 0) or plan.get("enabled") is True,
        "language": language if isinstance(language, str) and len(language) <= 8 else "auto",
        "tiling_active": tiling.get("active") is True,
        "has_autostart": any(isinstance(v, dict) and v.get("autostart") is True for v in layouts.values()),
        "has_workspace_presets": len(tiling) > 0,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    if command == "menu":
        print(json.dumps(menu_data()))
        return 0
    if command == "bar-state" and not args:
        print(json.dumps(bar_state()))
        return 0
    try:
        with locked():
            return _locked_command(command, args)
    except TimeoutError:
        return 1                                  # another step kept the lock too long


def _locked_command(command: str, args: list[str]) -> int:
    if command == "workspace" and len(args) == 1 and args[0].isdigit():
        apply_workspace(int(args[0]))
    elif command == "tick" and not args:
        tick()
    elif command == "bg" and args in (["next"], ["prev"]):
        step_background(1 if args[0] == "next" else -1)
    elif command == "theme" and args in (["next"], ["prev"]):
        step_theme(1 if args[0] == "next" else -1)
    elif command == "apply" and len(args) == 1 and _valid_slug(args[0]):
        try:
            ok = apply_theme(args[0])
        except FileNotFoundError:
            return forget_dead_key(args[0])
        if not ok:
            theme_core.notify(_("Could not apply theme"), args[0])
            return 1
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
