"""Theme Manager - your own themes, made from an existing one.

A custom theme is an ordinary user theme in ~/.config/omarchy/themes/<slug>/
that Omarchy builds every app's colors from:

- colors.toml: the colors you picked (Omarchy's templates derive terminals,
  editors, the shell, Hyprland borders, ... from it),
- backgrounds/: copies of the base theme's visible wallpapers (optional; add,
  hide or delete them later like in any user theme),
- icons.theme: taken over from the base theme,
- preview.png: drawn from the first wallpaper and the colors,
- theme-manager.json: what Theme Manager needs to edit it again (the base
  theme and the font).

Nothing that runs code is ever copied: no .lua, no terminal configs, no
app-specific files with the old colors baked in. The font is Omarchy's
system-wide monospace font (`omarchy font set`); a custom theme remembers
one, Theme Manager switches to it when the theme is applied and back to
your own font when a theme without one is applied.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import theme_core

MARKER = "theme-manager.json"
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,79}$")
MAX_COLORS_BYTES = 64 * 1024
MAX_WALLPAPERS = 60
MAX_NAME = 40

# The keys Omarchy's templates use, in the order colors.toml lists them,
# grouped for the editor. Derived keys follow their base color unless edited.
MAIN_KEYS = ("background", "foreground", "accent", "selection", "muted")
PALETTE_KEYS = ("red", "yellow", "orange", "green", "cyan", "blue", "magenta", "brown")
SHADE_KEYS = ("dark_background", "darker_background", "lighter_background",
              "dark_foreground", "light_foreground", "bright_foreground")
BRIGHT_KEYS = tuple(f"bright_{k}" for k in ("red", "yellow", "green", "cyan", "blue", "magenta"))
KNOWN_KEYS = MAIN_KEYS + SHADE_KEYS + PALETTE_KEYS + BRIGHT_KEYS
LABELS = {
    "background": "Background", "foreground": "Text", "accent": "Accent",
    "selection": "Selection", "muted": "Muted text",
    "dark_background": "Background, darker", "darker_background": "Background, darkest",
    "lighter_background": "Background, lighter", "dark_foreground": "Text, dim",
    "light_foreground": "Text, soft", "bright_foreground": "Text, bright",
}
DEFAULTS = {
    "background": "#1e1e2e", "foreground": "#cdd6f4", "accent": "#89b4fa", "selection": "#45475a",
    "muted": "#6c7086", "red": "#f38ba8", "yellow": "#f9e2af", "orange": "#fab387", "green": "#a6e3a1",
    "cyan": "#94e2d5", "blue": "#89b4fa", "magenta": "#cba6f7", "brown": "#7d5d3b",
}


def label(key: str) -> str:
    return LABELS.get(key) or key.replace("bright_", "bright ").replace("_", " ").capitalize()


# --- colors ---------------------------------------------------------------------

def _rgb(hex_code: str) -> tuple[float, float, float]:
    h = hex_code.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def mix(a: str, b: str, amount: float) -> str:
    """`a` moved `amount` (0..1) of the way towards `b`."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(x + (y - x) * amount for x, y in zip(ra, rb))


def luminance(hex_code: str) -> float:
    r, g, b = _rgb(hex_code)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def derived(colors: dict[str, str]) -> dict[str, str]:
    """Shades and bright variants computed from the main colors."""
    bg, fg = colors["background"], colors["foreground"]
    dark = luminance(bg) < 0.5
    edge = "#000000" if dark else "#ffffff"
    out = {
        "dark_background": mix(bg, edge, 0.25),
        "darker_background": mix(bg, edge, 0.5),
        "lighter_background": mix(bg, fg, 0.08),
        "dark_foreground": colors.get("muted") or mix(fg, bg, 0.5),
        "light_foreground": mix(fg, bg, 0.2),
        "bright_foreground": mix(fg, "#ffffff" if dark else "#000000", 0.3),
    }
    lift = "#ffffff" if dark else "#000000"
    for key in ("red", "yellow", "green", "cyan", "blue", "magenta"):
        out[f"bright_{key}"] = mix(colors[key], lift, 0.2)
    return out


@dataclass
class Palette:
    """Every color of a theme: the known keys (always complete) plus any
    extra keys the base theme had (kept as they were; plain colors among
    them can be edited too)."""
    colors: dict[str, str]
    extras: dict[str, str]

    def editable_extras(self) -> list[str]:
        return [k for k, v in self.extras.items() if HEX_RE.match(v)]

    def get(self, key: str) -> str:
        return self.colors.get(key) or self.extras.get(key, "")

    def set(self, key: str, value: str, edited: set[str] | None = None) -> None:
        """Change one color. Changing a main or palette color also moves the
        shades and bright variants that were not edited by hand."""
        if not HEX_RE.match(value):
            raise ValueError(f"not a color: {value!r}")
        value = value.lower()
        if key in KNOWN_KEYS:
            self.colors[key] = value
        elif key in self.extras and HEX_RE.match(self.extras[key]):
            self.extras[key] = value
        else:
            raise KeyError(key)
        if key in MAIN_KEYS or key in PALETTE_KEYS:
            fresh = derived(self.colors)
            for dep, color in fresh.items():
                if edited is None or dep not in edited:
                    self.colors[dep] = color

    @property
    def mode(self) -> str:
        return "dark" if luminance(self.colors["background"]) < 0.5 else "light"


def parse_colors(text: str) -> Palette:
    """A theme's colors.toml. Missing known keys are filled in (from the
    main colors, then defaults), so every template finds its value."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"colors.toml is not valid TOML: {error}") from error
    colors, extras = {}, {}
    for key, value in data.items():
        if not isinstance(value, str) or not re.match(r"^[a-z][a-z0-9_]{0,40}$", key) or key == "mode":
            continue
        if key in KNOWN_KEYS:
            if HEX_RE.match(value):
                colors[key] = value.lower()
        elif len(value) <= 200 and re.match(r"^[#A-Za-z0-9(),. ]*$", value):
            extras[key] = value
    for key in MAIN_KEYS + PALETTE_KEYS:
        colors.setdefault(key, DEFAULTS[key])
    for key, value in derived(colors).items():
        colors.setdefault(key, value)
    return Palette(colors, extras)


def render_colors(palette: Palette, name: str, base: str) -> str:
    lines = [f"# {name}: made with Theme Manager from {base}.",
             "# Omarchy builds every app's colors from this file.", "",
             f'mode = "{palette.mode}"', ""]
    for group in (MAIN_KEYS, SHADE_KEYS, PALETTE_KEYS, BRIGHT_KEYS):
        lines += [f"{key} = \"{palette.colors[key]}\"" for key in group] + [""]
    if palette.extras:
        lines += ["# Kept from the base theme."]
        lines += [f"{key} = \"{value}\"" for key, value in palette.extras.items()] + [""]
    return "\n".join(lines)


def load_palette(theme) -> Palette:
    path = Path(theme.path) / "colors.toml"
    try:
        if path.stat().st_size > MAX_COLORS_BYTES:
            raise ValueError("colors.toml is too large")
        return parse_colors(path.read_text("utf-8", "replace"))
    except FileNotFoundError:
        return parse_colors("")


# --- colors from a wallpaper ------------------------------------------------------

# Where each terminal color sits on the color wheel (degrees) when the
# picture offers nothing close to it.
ANSI_HUES = {"red": 355, "orange": 25, "yellow": 48, "green": 120, "cyan": 180,
             "blue": 215, "magenta": 295, "brown": 25}


def _hls(hex_code: str) -> tuple[float, float, float]:
    import colorsys
    return colorsys.rgb_to_hls(*_rgb(hex_code))


def _from_hls(h: float, l: float, s: float) -> str:
    import colorsys
    return _hex(colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s))))


def image_pixels(path, size: int = 64) -> list[tuple[float, float, float]]:
    """The picture scaled down to about size×size, as RGB triples 0..1.
    The header is checked first, like everywhere else."""
    if theme_core.image_size_problem(path):
        raise ValueError("the image is too large to read")
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    try:
        image = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
    except GLib.Error as error:
        raise ValueError(f"cannot read the image: {error.message}") from error
    data = image.get_pixels()
    channels, stride = image.get_n_channels(), image.get_rowstride()
    pixels = []
    for y in range(image.get_height()):
        row = y * stride
        for x in range(image.get_width()):
            i = row + x * channels
            if channels == 4 and data[i + 3] < 128:
                continue   # transparent
            pixels.append((data[i] / 255, data[i + 1] / 255, data[i + 2] / 255))
    if not pixels:
        raise ValueError("the image has no visible pixels")
    return pixels


def palette_from_pixels(pixels: list[tuple[float, float, float]]) -> Palette:
    """A theme palette that matches a picture: the background takes the
    picture's main hue (very dark, or very light for bright pictures), the
    accent its most vivid frequent color, and every terminal color the
    picture's own shade of that hue where it has one, all at one lightness
    so they stay readable on the background."""
    import colorsys
    hls = [colorsys.rgb_to_hls(*p) for p in pixels]
    brightness = sum(l for _h, l, _s in hls) / len(hls)
    dark = brightness < 0.6
    # Group colors into hue slices of 15°; greys go into their own group.
    groups: dict[int, list] = {}
    for h, l, s in hls:
        key = -1 if s < 0.12 or l < 0.06 or l > 0.96 else int(h * 24)
        groups.setdefault(key, []).append((h, l, s))

    def mean_hue(items):
        import math
        x = sum(math.cos(h * 2 * math.pi) for h, _l, _s in items)
        y = sum(math.sin(h * 2 * math.pi) for h, _l, _s in items)
        return (math.atan2(y, x) / (2 * math.pi)) % 1.0

    colored = {k: v for k, v in groups.items() if k >= 0}
    total = len(hls)
    # The main hue: the biggest colored group (any group if all is grey).
    if colored:
        main_items = max(colored.values(), key=len)
        main_hue = mean_hue(main_items)
        main_sat = min(0.35, sum(s for _h, _l, s in main_items) / len(main_items))
    else:
        main_hue, main_sat = 0.6, 0.0
    # The accent: vivid and not rare. Weighted by chroma (how colorful a
    # color really looks: a very dark navy is "saturated" but hardly
    # colorful) and by its share of the picture.
    def vividness(items):
        chroma = sum(s * (1 - abs(2 * l - 1)) for _h, l, s in items) / len(items)
        return chroma * (len(items) / total) ** 0.5
    accent_items = max(colored.values(), key=vividness) if colored else None
    accent_hue = mean_hue(accent_items) if accent_items else main_hue
    accent_sat = max(0.55, min(0.85, sum(s for _h, _l, s in accent_items) / len(accent_items))) \
        if accent_items else 0.0

    if dark:
        bg = _from_hls(main_hue, 0.11, main_sat * 0.6)
        fg = _from_hls(main_hue, 0.88, min(0.15, main_sat))
        accent = _from_hls(accent_hue, 0.66, accent_sat)
        lightness, saturation = 0.68, 0.62
    else:
        bg = _from_hls(main_hue, 0.95, main_sat * 0.5)
        fg = _from_hls(main_hue, 0.16, min(0.2, main_sat))
        accent = _from_hls(accent_hue, 0.42, accent_sat)
        lightness, saturation = 0.42, 0.65
    colors = {"background": bg, "foreground": fg, "accent": accent,
              "selection": mix(bg, accent, 0.28), "muted": mix(fg, bg, 0.5)}
    for key, hue_deg in ANSI_HUES.items():
        hue = hue_deg / 360
        # The picture's own version of this hue, if it has a fair amount of it.
        best = None
        for group, items in colored.items():
            h = mean_hue(items)
            distance = min(abs(h - hue), 1 - abs(h - hue)) * 360
            if distance <= 20 and len(items) >= total * 0.01 and (best is None or len(items) > len(best[1])):
                best = (h, items)
        if best is not None:
            hue = best[0]
        if key == "brown":
            colors[key] = _from_hls(hue, 0.35 if dark else 0.3, 0.45)
        else:
            colors[key] = _from_hls(hue, lightness, saturation)
    palette = parse_colors("")
    for key, value in colors.items():
        palette.colors[key] = value
    for key, value in derived(palette.colors).items():
        palette.colors[key] = value
    return palette


def palette_from_image(path) -> Palette:
    return palette_from_pixels(image_pixels(path))


DEFAULT_NAME = "New Custom Theme"
# File names that say nothing about the picture: cameras, downloads, pastes.
_MEANINGLESS = re.compile(r"^(wallhaven|img|dsc|dscn|pxl|photo|image|screenshot|pasted|dropped|wallpaper|"
                          r"download|unsplash|pexels|bg|background)$", re.IGNORECASE)


def name_from_file(file_name: str) -> str | None:
    """`2-coffee-beans.jpg` -> `Coffee Beans`; None when the file name says
    nothing (`wallhaven-xlqrwv.jpg`, `IMG_0937.HEIC`, `pasted-image.png`)."""
    stem = re.sub(r"^[0-9]+[-_ ]+", "", Path(file_name).stem)
    words = [w for w in re.split(r"[^A-Za-z0-9]+", stem) if w]
    # Keep real words: letters only, 3+ long, and with a vowel (not an id like "xlqrwv").
    real = [w for w in words if w.isalpha() and len(w) >= 3 and re.search(r"[aeiouy]", w, re.I)
            and not _MEANINGLESS.match(w)]
    if not real or len(real) < len(words) / 2:
        return None
    return " ".join(w.capitalize() for w in real)[:MAX_NAME].strip() or None


def unique_name(name: str, roots: theme_core.Roots | None = None) -> str:
    """`name`, or `name 2`, `name 3`, … if a theme with that name exists."""
    taken = {t.slug for t in theme_core.discover_themes(roots)}
    for n in range(1, 100):
        candidate = name if n == 1 else f"{name} {n}"
        if slug_for(candidate) not in taken:
            return candidate
    return f"{name} {int(time.time())}"


# --- names --------------------------------------------------------------------------

def slug_for(name: str) -> str:
    """`My Ristretto` -> `my-ristretto` (Omarchy shows it as "My Ristretto")."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:MAX_NAME].strip("-")
    if not slug:
        raise ValueError("give the theme a name (letters or digits)")
    return slug


def suggested_name(base_slug: str, roots: theme_core.Roots | None = None) -> str:
    taken = {t.slug for t in theme_core.discover_themes(roots)}
    for n in range(1, 100):
        slug = f"my-{base_slug}" if n == 1 else f"my-{base_slug}-{n}"
        if slug[:MAX_NAME] not in taken:
            return theme_core.display_name(slug[:MAX_NAME])
    return theme_core.display_name(f"my-{base_slug}-{int(time.time())}")


# --- the marker ------------------------------------------------------------------------

def marker_of(theme) -> dict | None:
    """Theme Manager's note in a custom theme, or None for any other theme."""
    if getattr(theme, "scope", "user") != "user":
        return None
    path = Path(theme.path) / MARKER
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_custom(theme) -> bool:
    return marker_of(theme) is not None and not theme_core.theme_from_git(Path(theme.path))


def font_of(theme) -> str | None:
    font = (marker_of(theme) or {}).get("font")
    return font if isinstance(font, str) and FONT_RE.match(font) else None


# --- fonts ----------------------------------------------------------------------------

def list_fonts() -> list[str]:
    """The monospace fonts Omarchy offers (`omarchy font list`)."""
    out, ok = theme_core.read_bounded_output(["omarchy-font-list"], max_bytes=65536, timeout=5.0)
    if not ok:
        return []
    fonts = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if FONT_RE.match(line) and line not in fonts:
            fonts.append(line)
    return fonts[:200]


def current_font() -> str | None:
    out, ok = theme_core.read_bounded_output(["omarchy-font-current"], max_bytes=4096, timeout=5.0)
    font = out.decode("utf-8", "replace").strip() if ok else ""
    return font if FONT_RE.match(font) else None


def set_font(font: str) -> bool:
    """Through Omarchy's own command. Only a font from `omarchy font list`
    is ever passed on: that command edits config files with sed."""
    if not FONT_RE.match(font) or font not in list_fonts():
        return False
    _, ok = theme_core.read_bounded_output(["omarchy-font-set", font], max_bytes=65536, timeout=30.0)
    return ok


def apply_theme_font(slug: str) -> str | None:
    """After a theme was applied: switch to its font, or back to the font
    you had before a theme's font took over. Returns the font set, if any."""
    theme = next((t for t in theme_core.discover_themes() if t.slug == slug), None)
    wanted = font_of(theme) if theme is not None and is_custom(theme) else None
    settings = theme_core._load_settings(theme_core.settings_file())
    saved = settings.get("own_font")
    saved = saved if isinstance(saved, str) and FONT_RE.match(saved) else None
    current = current_font()
    if wanted:
        if saved is None and current and current != wanted:
            theme_core._update_settings(theme_core.settings_file(), own_font=current)
        if current != wanted and set_font(wanted):
            return wanted
        return None
    if saved:
        theme_core._update_settings(theme_core.settings_file(), own_font=None)
        if current != saved and set_font(saved):
            return saved
    return None


# --- create and edit -----------------------------------------------------------------------

def _write_marker(directory: Path, base: str, font: str | None, created: float) -> None:
    theme_core._atomic_write(directory / MARKER, json.dumps(
        {"base": base, "font": font, "created": created}, indent=2) + "\n", mode=0o644)


def _copy_icons(base_dir: Path, directory: Path) -> None:
    source = base_dir / "icons.theme"
    try:
        if source.is_symlink() or source.stat().st_size > 200:
            return
        text = source.read_text("utf-8", "replace").strip().splitlines()[0]
    except (OSError, IndexError):
        return
    if re.match(r"^[A-Za-z0-9._+-]{1,80}$", text):
        theme_core._atomic_write(directory / "icons.theme", text + "\n", mode=0o644)


def _copy_one(source: Path, directory: Path) -> bool:
    folder = directory / "backgrounds"
    folder.mkdir(mode=0o755, exist_ok=True)
    if source.suffix.lower() not in theme_core.IMAGE_EXTS or theme_core.image_size_problem(source):
        return False
    dest = folder / theme_core._sanitize_name(source.name)
    if dest.exists() or dest.is_symlink():
        return False
    try:
        theme_core._copy_bounded(source, dest)
    except (OSError, ValueError):
        return False
    return True


def _copy_wallpapers(base, directory: Path) -> int:
    folder = directory / "backgrounds"
    folder.mkdir(mode=0o755, exist_ok=True)
    copied = 0
    for wallpaper in theme_core.list_backgrounds(None, base):
        if copied >= MAX_WALLPAPERS:
            break
        if wallpaper.hidden or theme_core.image_size_problem(wallpaper.path):
            continue
        dest = folder / theme_core._sanitize_name(wallpaper.name)
        if dest.exists() or dest.is_symlink():
            continue
        try:
            theme_core._copy_bounded(Path(wallpaper.path), dest)
        except (OSError, ValueError):
            continue
        copied += 1
    return copied


def create_theme(base, name: str, palette: Palette, font: str | None,
                 wallpapers: bool = True, roots: theme_core.Roots | None = None,
                 extra_wallpapers: list | None = None) -> Path:
    """A new user theme made from `base` with these colors and font.
    `wallpapers` copies the base theme's visible wallpapers;
    `extra_wallpapers` are further image files to copy (e.g. the wallpaper
    the colors were taken from)."""
    roots = roots or theme_core.default_roots()
    slug = theme_core._validate_slug(slug_for(name))
    if font is not None and not FONT_RE.match(font):
        raise ValueError("bad font name")
    if any(t.slug == slug for t in theme_core.discover_themes(roots)):
        raise FileExistsError(f"a theme called {theme_core.display_name(slug)} already exists")
    roots.user_themes.mkdir(parents=True, exist_ok=True)
    directory = roots.user_themes / slug
    directory.mkdir(mode=0o755)   # fails if it appeared in between: never write into a foreign folder
    try:
        theme_core._atomic_write(directory / "colors.toml",
                                 render_colors(palette, theme_core.display_name(slug), base.name), mode=0o644)
        _copy_icons(Path(base.path), directory)
        if wallpapers:
            _copy_wallpapers(base, directory)
        for source in (extra_wallpapers or [])[:MAX_WALLPAPERS]:
            _copy_one(Path(source), directory)
        _write_marker(directory, base.slug, font, time.time())
        make_preview(directory, palette)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return directory


def update_theme(theme, palette: Palette, font: str | None) -> None:
    """Save new colors and font into a custom theme made by Theme Manager."""
    marker = marker_of(theme)
    if marker is None or theme_core.theme_from_git(Path(theme.path)):
        raise PermissionError("only themes made with Theme Manager can be edited here")
    if font is not None and not FONT_RE.match(font):
        raise ValueError("bad font name")
    directory = Path(theme.path)
    base = marker.get("base") if isinstance(marker.get("base"), str) else "?"
    theme_core._atomic_write(directory / "colors.toml",
                             render_colors(palette, theme.name, theme_core.display_name(base)), mode=0o644)
    created = marker.get("created") if isinstance(marker.get("created"), (int, float)) else time.time()
    _write_marker(directory, base, font, created)
    make_preview(directory, palette)


def rename_theme(theme, new_name: str, roots: theme_core.Roots | None = None) -> str:
    """Give a custom theme a new name: its folder, its added wallpapers'
    folder and every list that names it move along. Returns the new slug.
    The caller applies it again if it is the theme in use."""
    import theme_auto   # (theme_auto imports this module)
    roots = roots or theme_core.default_roots()
    if not is_custom(theme):
        raise PermissionError("only themes made with Theme Manager can be renamed here")
    old = theme_core._validate_slug(theme.slug)
    new = theme_core._validate_slug(slug_for(new_name))
    if new == old:
        return old
    if any(t.slug == new for t in theme_core.discover_themes(roots)):
        raise FileExistsError(f"a theme called {theme_core.display_name(new)} already exists")
    source = roots.user_themes / old
    if source.is_symlink() or not source.is_dir():
        raise PermissionError("not a theme folder of yours")
    old_walls, new_walls = roots.user_backgrounds / old, roots.user_backgrounds / new
    if new_walls.exists() or new_walls.is_symlink():
        raise FileExistsError(f"{new_walls} already exists")
    os.rename(source, roots.user_themes / new)
    if old_walls.is_dir() and not old_walls.is_symlink():
        os.rename(old_walls, new_walls)
    hidden = theme_core.load_hidden()
    if old in hidden:
        theme_core.save_hidden((hidden - {old}) | {new})
    walls = theme_core.load_hidden_wallpapers()
    if old in walls:
        walls[new] = walls.pop(old)
        theme_core.save_hidden_wallpapers(walls)
    theme_auto.rename_state(old, new)
    try:
        import theme_shortcuts
        theme_shortcuts.rename_theme(old, new)
    except (OSError, RuntimeError, ValueError):
        pass   # the key stays with the old name; it can be set again
    return new


# --- preview ----------------------------------------------------------------------------

PREVIEW_SIZE = (1280, 720)


def make_preview(directory: Path, palette: Palette) -> Path | None:
    """preview.png: the theme's first wallpaper with a window drawn in the
    theme's colors (background, title in the accent, text lines, the
    palette), so the theme can be told apart in every picker."""
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib
    except (ImportError, ValueError):
        return None
    width, height = PREVIEW_SIZE
    colors = palette.colors

    def rgba(hex_code: str) -> int:
        return int(hex_code.lstrip("#"), 16) << 8 | 0xFF

    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
    canvas.fill(rgba(colors["darker_background"]))
    walls = sorted((p for p in (directory / "backgrounds").glob("*")
                    if p.is_file() and not p.is_symlink() and p.suffix.lower() in theme_core.IMAGE_EXTS),
                   key=lambda p: p.name.casefold()) if (directory / "backgrounds").is_dir() else []
    for wall in walls[:1]:
        if theme_core.image_size_problem(wall):
            continue
        try:
            image = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(wall), width * 2, height * 2, True)
        except GLib.Error:
            continue
        scale = max(width / image.get_width(), height / image.get_height())
        scaled = image.scale_simple(max(width, round(image.get_width() * scale)),
                                    max(height, round(image.get_height() * scale)),
                                    GdkPixbuf.InterpType.BILINEAR)
        scaled.copy_area((scaled.get_width() - width) // 2, (scaled.get_height() - height) // 2,
                         width, height, canvas, 0, 0)

    def box(x, y, w, h, color):
        canvas.new_subpixbuf(x, y, w, h).fill(rgba(color))

    # A window in the theme's colors, with a border in the accent.
    x, y, w, h = 300, 150, 680, 420
    box(x - 4, y - 4, w + 8, h + 8, colors["accent"])
    box(x, y, w, h, colors["background"])
    box(x, y, w, 46, colors["dark_background"])
    for i, key in enumerate(("red", "yellow", "green")):
        box(x + 20 + i * 30, y + 16, 16, 16, colors[key])
    box(x + 40, y + 80, 260, 22, colors["accent"])
    box(x + 40, y + 124, 520, 14, colors["foreground"])
    box(x + 40, y + 154, 440, 14, colors["foreground"])
    box(x + 40, y + 184, 480, 14, colors["muted"])
    box(x + 30, y + 220, w - 60, 40, colors["selection"])
    box(x + 40, y + 234, 300, 12, colors["foreground"])
    for i, key in enumerate(PALETTE_KEYS):
        box(x + 40 + i * 76, y + 300, 64, 64, colors[key])
    dest = directory / "preview.png"
    tmp = directory / ".preview.png.tmp"
    try:
        canvas.savev(str(tmp), "png", [], [])
        os.replace(tmp, dest)
    except (GLib.Error, OSError):
        tmp.unlink(missing_ok=True)
        return None
    return dest
