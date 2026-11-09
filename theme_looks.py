"""Theme looks: your own monospace font and window style for any theme.

A look is kept in ~/.config/omarchy/theme-manager/looks.json as
``{slug: {"mono_font": "JetBrains Mono", "windows": "glassy"}}``; the theme
files are never changed. When a theme is applied, each field comes from its
look, else from the theme itself (a theme made with Theme Manager brings its
font), else your own value comes back: the value you had before a theme's
value took over is kept in settings.json (``own_font``, ``own_windows``)
until it is given back.

Fonts go through Omarchy's own `omarchy font set` (only a listed font), the
window style through `omarchy-hyprland-window-transparency` (Omarchy 4.5:
opaque, transparent or glassy = blur behind see-through windows). Without
that command the window style is not offered.
"""
from __future__ import annotations

import json
import shutil

import theme_core
import theme_custom

LOOK_KEYS = ("mono_font", "windows")
WINDOW_MODES = theme_custom.WINDOW_MODES
WINDOWS_COMMAND = "omarchy-hyprland-window-transparency"
LOOKS_MAX = 256 * 1024
MAX_LOOKS = 500
OWN_KEYS = {"mono_font": "own_font", "windows": "own_windows"}
MAX_ROUNDS = 20       # theme switches followed while one apply_look runs (each is an `omarchy theme set`)


# --- looks.json ----------------------------------------------------------------------------

def looks_file():
    return theme_core.settings_file().parent / "looks.json"


def _locked():
    """One look change at a time (Save, Undo, rename, prune, a theme apply, a
    backup restore): from reading looks.json and the user's own values to
    writing them back. Order: this lock first, then the settings lock."""
    return theme_core.file_lock(looks_file().with_name("looks.lock"))


def check_look(value) -> dict | None:
    """A look as stored, or None when it does not fit (unknown keys, a bad
    font name, an unknown window style). Empty fields are left out."""
    if not isinstance(value, dict):
        return None
    out = {}
    for key, field in value.items():
        if field is None:
            continue
        if key == "mono_font":
            if not isinstance(field, str) or not theme_custom.FONT_RE.fullmatch(field):
                return None
        elif key == "windows":
            if field not in WINDOW_MODES:
                return None
        else:
            return None
        out[key] = field
    return out


def check_looks(value) -> dict:
    """Every valid entry of a looks.json value (a bad one is dropped)."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for slug, look in value.items():
        look = check_look(look)
        if look and isinstance(slug, str) and theme_core.SLUG_RE.match(slug) and len(out) < MAX_LOOKS:
            out[slug] = look
    return out


def load_looks() -> dict:
    try:
        return check_looks(json.loads(theme_core.read_small(looks_file(), LOOKS_MAX)))
    except (OSError, ValueError):
        return {}


def _save_looks(looks: dict) -> None:
    path = looks_file()
    if not looks:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    theme_core._atomic_write(path, json.dumps(looks, indent=2, sort_keys=True) + "\n", mode=0o600)


def look_of(slug: str) -> dict:
    return dict(load_looks().get(slug, {}))


def set_look(slug: str, look: dict) -> dict:
    """Replace a theme's look (an empty one removes it). Returns the old one."""
    slug = theme_core._validate_slug(slug)
    checked = check_look(look)
    if checked is None:
        raise ValueError("not a look")
    with _locked():
        looks = load_looks()
        previous = looks.pop(slug, {})
        if checked:
            if len(looks) >= MAX_LOOKS:
                raise ValueError("too many looks")
            looks[slug] = checked
        _save_looks(looks)
    return previous


def rename(old: str, new: str) -> None:
    with _locked():
        looks = load_looks()
        if old in looks:
            looks[new] = looks.pop(old)
            _save_looks(looks)


def prune_missing() -> list[str]:
    """Forget the looks of themes that are gone (after Undo is no longer offered)."""
    installed = {t.slug for t in theme_core.discover_themes()}
    with _locked():
        looks = load_looks()
        gone = [slug for slug in looks if slug not in installed]
        if gone:
            _save_looks({slug: look for slug, look in looks.items() if slug in installed})
    return gone


# --- the desktop ---------------------------------------------------------------------------

def windows_supported() -> bool:
    return shutil.which(WINDOWS_COMMAND) is not None


def current_windows() -> str | None:
    if not windows_supported():
        return None
    out, ok = theme_core.read_bounded_output([WINDOWS_COMMAND], max_bytes=4096, timeout=5.0)
    mode = out.decode("utf-8", "replace").strip() if ok else ""
    return mode if mode in WINDOW_MODES else None


def set_windows(mode: str) -> bool:
    """Through Omarchy's own command (it takes the theme lock, writes the
    terminals' glass and reloads Hyprland and the terminals)."""
    if mode not in WINDOW_MODES or not windows_supported():
        return False
    _, ok = theme_core.read_bounded_output([WINDOWS_COMMAND, mode], max_bytes=65536, timeout=60.0)
    return ok


def caps() -> list[str]:
    return ["mono_font", *(["windows"] if windows_supported() else [])]


def _current(key: str):
    return theme_custom.current_font() if key == "mono_font" else current_windows()


def _set(key: str, value: str) -> bool:
    return theme_custom.set_font(value) if key == "mono_font" else set_windows(value)


def _own(settings: dict, key: str):
    value = settings.get(OWN_KEYS[key])
    if key == "mono_font":
        return value if isinstance(value, str) and theme_custom.FONT_RE.fullmatch(value) else None
    return value if value in WINDOW_MODES else None


# --- which value wins -------------------------------------------------------------------

def _theme(slug: str):
    return next((t for t in theme_core.discover_themes() if t.slug == slug), None)


def theme_value(theme, key: str, look: dict | None = None):
    """What a theme brings for `key`: its look's value, else its own (a
    theme made with Theme Manager brings its font and window style); None = none."""
    if theme is None:
        return None
    look = look_of(theme.slug) if look is None else look
    if look.get(key) is not None:
        return look[key]
    if theme_custom.is_custom(theme):
        return theme_custom.font_of(theme) if key == "mono_font" else theme_custom.windows_of(theme)
    return None


def _apply_field(key: str, wanted) -> bool:
    """`wanted` on the desktop, keeping your own value to give it back later;
    or your own value back when `wanted` is None. True when it changed."""
    settings = theme_core._load_settings(theme_core.settings_file())
    own = _own(settings, key)
    current = _current(key)
    if wanted is not None:
        if current == wanted:
            return False
        if own is None and current is not None:
            # kept before the change: a write that fails halfway can't lose it
            theme_core._update_settings(theme_core.settings_file(), **{OWN_KEYS[key]: current})
        return _set(key, wanted)
    if own is None:
        return False
    if current != own and not _set(key, own):
        return False      # kept until your own value is really back
    theme_core._update_settings(theme_core.settings_file(), **{OWN_KEYS[key]: None})
    return current != own


def apply_font_override(font: str, slug: str) -> bool:
    """A font from outside a theme (a preset for theme `slug`), through the
    same chain: your own font is kept to come back later (only when none is
    kept yet, never overwritten). Only while `slug` is still the theme in use
    (checked under the lock): after a switch meanwhile, nothing changes.
    True when `font` is on the desktop afterwards."""
    with _locked():
        if theme_core.current_theme() != slug:
            return False
        current = theme_custom.current_font()
        if current == font:
            return True
        own = _own(theme_core._load_settings(theme_core.settings_file()), "mono_font")
        if own is None and current is not None:
            theme_core._update_settings(theme_core.settings_file(), own_font=current)
        return theme_custom.set_font(font)


def apply_look(slug: str) -> list[str]:
    """After a theme was applied (or for Reapply): its font and window
    style, and your own values back for fields it leaves alone. Returns the
    look keys that changed. Only for the theme in use: nothing when `slug`
    is no longer in use, and when the theme changes while Omarchy's font or
    window-style command runs (a switch does not wait for our lock), the
    theme in use then gets its look right after, again and again while
    switches keep coming (up to MAX_ROUNDS switches during one call)."""
    changed: list[str] = []
    with _locked():
        target = slug
        if theme_core.current_theme() != target:
            return []
        for _round in range(MAX_ROUNDS):
            theme = _theme(target)
            if theme is None:
                break
            look = look_of(target)
            moved = None
            for key in caps():
                if _apply_field(key, theme_value(theme, key, look)) and key not in changed:
                    changed.append(key)
                now = theme_core.current_theme()
                if now != target:
                    moved = now
                    break
            if moved is None:
                break
            target = moved          # another theme came in meanwhile: its look now
    return changed


def pending(slug: str) -> list[str]:
    """The look's fields that are not on the desktop now for the theme in
    use (empty for any other theme): what Reapply would change."""
    if theme_core.current_theme() != slug:
        return []
    theme = _theme(slug)
    if theme is None:
        return []
    look = look_of(slug)
    settings = theme_core._load_settings(theme_core.settings_file())
    out = []
    for key in caps():
        wanted = theme_value(theme, key, look)
        if wanted is not None:
            if _current(key) != wanted:
                out.append(key)
        else:
            own = _own(settings, key)
            if own is not None and _current(key) != own:
                out.append(key)
    return out


def look_data(slug: str) -> dict:
    """Everything the Font & effects page shows (blocking: runs commands)."""
    theme = _theme(slug)
    if theme is None:
        raise FileNotFoundError(slug)
    settings = theme_core._load_settings(theme_core.settings_file())
    keys = caps()
    current = {key: _current(key) for key in keys}
    return {
        "slug": slug,
        "name": theme.name,
        "look": look_of(slug),
        "theme": {key: theme_value(theme, key, {}) for key in keys},
        # your own value: kept while a theme's value is in use, else the one in use
        "own": {key: _own(settings, key) or current[key] for key in keys},
        "current": current,
        "fonts": theme_custom.list_fonts(),
        "caps": keys,
        "in_use": theme.current,
        "pending": pending(slug),
    }
