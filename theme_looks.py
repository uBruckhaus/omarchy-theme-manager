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

The text colour (``"text_color": "#rrggbb"``) is the main text colour, the
theme's `foreground`, in the configs Omarchy renders from its templates when
the theme is applied. Omarchy renders every template line by line into the
theme it puts in place (~/.local/state/omarchy/current/theme/), so the lines
rendered from a ``{{ foreground }}`` token (also _strip and _rgb) are found by
matching the generated file against its template, and only there the colour
is swapped; files the theme ships itself, its own folder and your templates
stay as they are. colors.toml there (Omarchy's copy, read by the shell)
follows too, and the apps reading those configs are reloaded with Omarchy's
own commands. A theme without a text colour shows its own: Omarchy renders it
fresh on every switch, and for Reapply on the theme in use the colour
written last (kept with the switch it belongs to, text-colour-state.json) is
turned back. Its "own" value is the theme's foreground, not one of yours.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import theme_core
import theme_custom

LOOK_KEYS = ("mono_font", "windows", "text_color")
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
        elif key == "text_color":
            if not isinstance(field, str) or not theme_custom.HEX_RE.fullmatch(field):
                return None
            field = field.lower()
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
    return ["mono_font", *(["windows"] if windows_supported() else []),
            *(["text_color"] if text_color_supported() else [])]


def _current(key: str):
    if key == "text_color":
        return shown_text_color()
    return theme_custom.current_font() if key == "mono_font" else current_windows()


def _set(key: str, value: str) -> bool:
    if key == "text_color":
        return set_text_color(value)
    return theme_custom.set_font(value) if key == "mono_font" else set_windows(value)


def _own(settings: dict, key: str):
    if key == "text_color":
        slug = theme_core.current_theme()
        return theme_foreground(slug) if slug else None      # the theme's own, not kept in settings
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
    if key == "text_color":
        return None         # the theme's own text colour is its foreground: that is "your own" here
    if theme_custom.is_custom(theme):
        return theme_custom.font_of(theme) if key == "mono_font" else theme_custom.windows_of(theme)
    return None


def _apply_field(key: str, wanted) -> bool:
    """`wanted` on the desktop, keeping your own value to give it back later;
    or your own value back when `wanted` is None. True when it changed."""
    settings = theme_core._load_settings(theme_core.settings_file())
    own = _own(settings, key)
    current = _current(key)
    if key == "text_color":
        wanted = wanted or own          # the theme's own colour back; nothing kept in settings
        return wanted is not None and current != wanted and _set(key, wanted)
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
    if "text_color" in keys:
        # The theme's own text colour and background (for the swatch, the
        # contrast check and the sample), also for a theme not in use.
        own_fg = theme_foreground(slug)
        if own_fg is None:
            keys = [k for k in keys if k != "text_color"]
            current.pop("text_color", None)
        else:
            current["text_color"] = current["text_color"] if theme.current else own_fg
    try:
        palette = dict(theme_custom.load_palette(theme).colors)
    except (OSError, ValueError):
        palette = {}
    own_values = {key: _own(settings, key) or current[key] for key in keys}
    if "text_color" in keys:
        own_values["text_color"] = theme_foreground(slug)
    return {
        "slug": slug,
        "name": theme.name,
        "look": look_of(slug),
        "theme": {key: theme_value(theme, key, {}) for key in keys},
        # your own value: kept while a theme's value is in use, else the one in use
        "own": own_values,
        "colors": {k: palette.get(k) for k in ("background", "foreground", "muted")
                   if isinstance(palette.get(k), str) and theme_custom.HEX_RE.fullmatch(palette[k])},
        "current": current,
        "fonts": theme_custom.list_fonts(),
        "caps": keys,
        "in_use": theme.current,
        "pending": pending(slug),
    }


# --- text colour (Omarchy's generated configs) ------------------------------------------------

FOREGROUND_TOKEN = re.compile(r"\{\{\s*foreground(_strip|_rgb)?\s*\}\}")
ANY_TOKEN = re.compile(r"\{\{.*?\}\}")
MAX_CONFIG_BYTES = 1024 * 1024
TEXT_STATE = "text-colour-state.json"
# The apps that read a generated config, and Omarchy's command that reloads
# them (only the ones installed are run). Files read at an app's next start
# (Neovim, gum) need none.
RELOAD = {
    "alacritty.toml": "omarchy-restart-terminal", "kitty.conf": "omarchy-restart-terminal",
    "ghostty.conf": "omarchy-restart-terminal", "foot.ini": "omarchy-theme-set-foot",
    "btop.theme": "omarchy-restart-btop", "helix.toml": "omarchy-restart-helix",
    "hyprland.lua": "omarchy-restart-hyprctl", "hyprland-preview-share-picker.css": "omarchy-restart-hyprctl",
    "pi.json": "omarchy-theme-set-pi", "claude.json": "omarchy-theme-set-claude",
    "hermes.yaml": "omarchy-theme-set-hermes", "t3code.json": "omarchy-theme-set-t3code",
    "vscode-theme.json": "omarchy-theme-set-vscode", "obsidian.css": "omarchy-theme-set-obsidian",
    "fcitx5-theme.conf": "omarchy-theme-set-fcitx5",
}


def current_theme_dir() -> Path:
    return theme_core.default_roots().state_current / "theme"


def template_dirs() -> list[Path]:
    """Your templates first, then Omarchy's (as omarchy-theme-set-templates)."""
    omarchy = theme_core.default_roots().system_themes.parent      # $OMARCHY_PATH (themes/ beside default/)
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [config / "omarchy" / "themed", omarchy / "default" / "themed"]


def text_state_file() -> Path:
    return theme_core.settings_file().parent / TEXT_STATE


def _switch_id() -> str | None:
    """Which theme switch the current theme belongs to: Omarchy's generation
    token, else the folder's identity (a new switch gives a new one)."""
    current = current_theme_dir()
    try:
        token = theme_core.read_small(current.parent / "theme.generation", limit=256).strip()
    except OSError:
        token = ""
    try:
        st = current.stat()
    except OSError:
        return None
    return f"{token}|{st.st_dev}:{st.st_ino}"


def theme_foreground(slug: str) -> str | None:
    """The theme's own text colour, from its own colors.toml."""
    theme = _theme(slug)
    if theme is None:
        return None
    try:
        color = theme_custom.load_palette(theme).colors.get("foreground")
    except (OSError, ValueError):
        return None
    return color.lower() if isinstance(color, str) and theme_custom.HEX_RE.fullmatch(color) else None


def _literal_parts(template_line: str) -> list[str]:
    return ANY_TOKEN.split(template_line)


def _matches(template_line: str, line: str) -> bool:
    """Whether `line` can be the template line rendered: every literal part in order."""
    parts = _literal_parts(template_line)
    if len(parts) == 1:
        return line == template_line
    if not line.startswith(parts[0]) or not line.endswith(parts[-1]):
        return False
    at = len(parts[0])
    end = len(line) - len(parts[-1])
    for part in parts[1:-1]:
        found = line.find(part, at, end) if part else at
        if found < 0:
            return False
        at = found + len(part)
    return at <= end


def _forms(color: str) -> dict:
    h = color.lstrip("#").lower()
    return {None: "#" + h, "_strip": h, "_rgb": ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))}


def _swap(line: str, template_line: str, old: str, new: str) -> str:
    """The rendered line with the old colour's forms its foreground tokens
    produce turned into the new one's, between the line's fixed start and end."""
    parts = _literal_parts(template_line)
    start, end = len(parts[0]), len(line) - len(parts[-1])
    middle = line[start:end]
    before, after = _forms(old), _forms(new)
    for match in FOREGROUND_TOKEN.finditer(template_line):
        kind = match.group(1)
        flags = re.IGNORECASE
        # Whole values only: "30,30,46" never inside "130,30,46".
        pattern = r"(?<![0-9A-Fa-f])" + re.escape(before[kind]) + r"(?![0-9A-Fa-f])"
        middle = re.sub(pattern, lambda _m, value=after[kind]: value, middle, flags=flags)
    return line[:start] + middle + line[end:]


def rewrite_generated(folder: Path, old: str, new: str) -> list[str]:
    """Swap the text colour in the configs Omarchy rendered from its
    templates into `folder`. Returns the file names changed."""
    changed, seen = [], set()
    for directory in template_dirs():
        try:
            templates = sorted(directory.glob("*.tpl"))
        except OSError:
            continue
        for template in templates:
            name = template.name[:-4]
            if name in seen:
                continue
            seen.add(name)          # yours wins over Omarchy's, as when rendering
            target = folder / name
            if target.is_symlink() or not target.is_file():
                continue
            try:
                tpl = theme_core.read_small(template, limit=MAX_CONFIG_BYTES).split("\n")
                text = theme_core.read_small(target, limit=MAX_CONFIG_BYTES)
            except (OSError, ValueError):
                continue
            lines = text.split("\n")
            if len(lines) != len(tpl) or not any(FOREGROUND_TOKEN.search(t) for t in tpl):
                continue
            if not all(_matches(t, line) for t, line in zip(tpl, lines)):
                continue            # not rendered from this template (the theme ships its own)
            out = [_swap(line, t, old, new) if FOREGROUND_TOKEN.search(t) else line for t, line in zip(tpl, lines)]
            if out != lines:
                theme_core._atomic_write(target, "\n".join(out), mode=target.stat().st_mode & 0o777)
                changed.append(name)
    colors = folder / "colors.toml"
    if colors.is_file() and not colors.is_symlink():
        try:
            text = theme_core.read_small(colors, limit=MAX_CONFIG_BYTES)
        except (OSError, ValueError):
            text = None
        if text is not None:
            pattern = re.compile(r'^(\s*foreground\s*=\s*["\']?)' + re.escape(old) + r'(["\']?\s*(#.*)?)$',
                                 re.IGNORECASE | re.MULTILINE)
            out = pattern.sub(lambda m: m.group(1) + new + m.group(2), text)
            if out != text:
                theme_core._atomic_write(colors, out, mode=colors.stat().st_mode & 0o777)
                changed.append("colors.toml")
    return changed


def reload_apps(changed: list[str]) -> list[str]:
    """Omarchy's own commands for the apps that read the changed configs."""
    commands = []
    for name in changed:
        command = RELOAD.get(name)
        if command and command not in commands and shutil.which(command):
            commands.append(command)
    for command in commands:
        try:
            subprocess.run([command], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, start_new_session=True)
        except (OSError, subprocess.SubprocessError):
            pass
    if {"shell.toml", "colors.toml"} & set(changed) and shutil.which("omarchy-shell"):
        folder = current_theme_dir()
        payload = []
        for name in ("colors.toml", "shell.toml"):
            path = folder / name
            payload.append(base64.b64encode(path.read_bytes()).decode() if path.is_file() else "")
        try:
            subprocess.run(["omarchy-shell", "shell", "applyTheme", *payload], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            commands.append("omarchy-shell")
        except (OSError, subprocess.SubprocessError):
            pass
    return commands


def _text_state() -> dict:
    try:
        state = json.loads(theme_core.read_small(text_state_file(), 4096))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def shown_text_color(slug: str | None = None) -> str | None:
    """The text colour Omarchy's generated configs of the theme in use have
    now: the one written last for this very theme switch, else the theme's own."""
    slug = slug or theme_core.current_theme()
    own = theme_foreground(slug) if slug else None
    if own is None:
        return None
    state = _text_state()
    written = state.get("written")
    if state.get("theme") == slug and state.get("switch") == _switch_id() and \
            isinstance(written, str) and theme_custom.HEX_RE.fullmatch(written):
        return written
    return own


def set_text_color(wanted: str) -> bool:
    """`wanted` in the generated configs of the theme in use (the theme's own
    colour turns the change back). True when it was written."""
    slug = theme_core.current_theme()
    own = theme_foreground(slug) if slug else None
    shown = shown_text_color(slug)
    if own is None or shown is None or not theme_custom.HEX_RE.fullmatch(wanted or ""):
        return False
    wanted = wanted.lower()
    if shown == wanted:
        return False
    changed = rewrite_generated(current_theme_dir(), shown, wanted)
    if wanted == own:
        text_state_file().unlink(missing_ok=True)
    else:
        text_state_file().parent.mkdir(parents=True, exist_ok=True)
        theme_core._atomic_write(text_state_file(), json.dumps(
            {"theme": slug, "switch": _switch_id(), "written": wanted}) + "\n", mode=0o600)
    reload_apps(changed)
    return True


def text_color_supported() -> bool:
    """Omarchy's templates are there to find the generated lines by."""
    return template_dirs()[-1].is_dir()


MIN_CONTRAST = 3.0     # WCAG: below 3:1 text is hard to read


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 contrast ratio of two #rrggbb colours (1..21)."""
    def lum(hex_code: str) -> float:
        h = hex_code.lstrip("#")
        channels = []
        for i in (0, 2, 4):
            c = int(h[i:i + 2], 16) / 255
            channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        r, g, b_ = channels
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    high, low = sorted((lum(a), lum(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)
