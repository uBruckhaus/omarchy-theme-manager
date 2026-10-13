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
from i18n import _

SLIDESHOW_CHOICES = (0, 5, 10, 15, 30, 60, 120)   # minutes; 0 = off
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


def state_file() -> Path:
    """Short-lived state (the background in use before a workspace took
    over, the last slideshow change). Losing it is harmless: it is a cache."""
    return Path.home() / ".cache/theme-manager/auto-state.json"


def _read_json(path: Path, default):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, data) -> None:
    theme_core._atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o600)


def _valid_slug(slug) -> bool:
    return isinstance(slug, str) and bool(theme_core.SLUG_RE.match(slug))


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
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# --- settings -------------------------------------------------------------------

def settings() -> dict:
    return theme_core._load_settings(theme_core.settings_file())


def update_settings(**values) -> None:
    theme_core._update_settings(theme_core.settings_file(), **values)


def workspace_wallpapers_enabled() -> bool:
    return settings().get("workspace_wallpapers") is True


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
    if old in mapping:
        mapping[new] = mapping.pop(old)
        save_workspace_map(mapping)
    plan = schedule()
    changed = {key: new for key in ("day", "night") if plan[key] == old}
    if changed:
        save_schedule(**changed)


# --- wallpapers per workspace ------------------------------------------------------------

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
        state = {"theme": theme.slug}   # a new theme brings its own background
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


def apply_theme(slug: str) -> bool:
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
        theme_custom.apply_theme_font(slug)
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
            if wanted != theme_core.current_theme():
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


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    if command == "menu":
        print(json.dumps(menu_data()))
        return 0
    with locked():
        if command == "workspace" and len(args) == 1 and args[0].isdigit():
            on_workspace(int(args[0]))
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
