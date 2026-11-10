"""The "Corners" setting, shared by App Manager and Theme Manager.

"windows" (the default) follows Hyprland's window rounding, which Omarchy
sets in this order, the last one winning: Omarchy's default (square), the
current theme's hyprland.lua, then your own ~/.config/hypr/looknfeel.lua.
"slight", "rounded" and "square" ignore it. No process is started here: the caller
reads `hyprctl getoption decoration:rounding -j` its own bounded way and
passes the text to parse_rounding()."""
import json
import os
import re
from pathlib import Path

CHOICES = ("windows", "slight", "rounded", "square")
SLIGHT = 6        # "Slightly rounded": round controls beside square or tiled windows.
ROUNDED = 12      # "Rounded": Theme Manager's corners up to now.
MAX_RADIUS = 24
MAX_LUA_BYTES = 256 * 1024
THEME_LUA = Path.home() / ".local/state/omarchy/current/theme/hyprland.lua"
USER_LUA = Path.home() / ".config/hypr/looknfeel.lua"
# `rounding = 8` outside a comment; "gradient_rounding" and "rounding_power" don't count.
_ROUNDING = re.compile(r"(?<![\w.])rounding\s*=\s*(\d+)")


def choice(value):
    return value if value in CHOICES else "windows"


def parse_rounding(text):
    """Hyprland's window rounding from `hyprctl getoption … -j`, or None."""
    try:
        value = json.loads(text).get("int")
    except (ValueError, TypeError, AttributeError):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(value, MAX_RADIUS))


def radius(setting, window_rounding):
    """The corner radius in px for this setting and the windows' rounding."""
    setting = choice(setting)
    if setting == "slight":
        return SLIGHT
    if setting == "rounded":
        return ROUNDED
    if setting == "square":
        return 0
    return window_rounding if window_rounding is not None else 0


def control(radius):
    """Buttons, drop-downs, entries and search fields: the radius, at most 8 px."""
    return min(radius, 8)


def small(radius):
    """Check boxes, slider tracks, small badges and swatches: the radius, at most 4 px."""
    return min(radius, 4)


def pill(radius):
    """Chips and switches: round pills, or square with Square corners."""
    return 999 if radius else 0


def rounded_path(cr, x, y, width, height, radius):
    """A rectangle with corners of `radius` (cairo), for what is drawn rather than styled:
    the workspace pictures and the Ask AI colour swatches."""
    r = max(0.0, min(radius, width / 2, height / 2))
    if r <= 0:
        cr.rectangle(x, y, width, height)
        return
    from math import pi
    cr.new_sub_path()
    cr.arc(x + width - r, y + r, r, -pi / 2, 0)
    cr.arc(x + width - r, y + height - r, r, 0, pi / 2)
    cr.arc(x + r, y + height - r, r, pi / 2, pi)
    cr.arc(x + r, y + r, r, pi, 3 * pi / 2)
    cr.close_path()


def lua_rounding(path):
    """The last uncommented `rounding = N` in a Lua config file, or None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_LUA_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_LUA_BYTES:
        return None
    found = None
    for line in data.decode("utf-8", "replace").splitlines():
        code = line.split("--", 1)[0]
        for match in _ROUNDING.finditer(code):
            found = int(match.group(1))
    return found


def rounding_source(theme_lua=THEME_LUA, user_lua=USER_LUA):
    """Who set the windows' rounding: "user" (looknfeel.lua), "theme" or "default"."""
    if lua_rounding(user_lua) is not None:
        return "user"
    if lua_rounding(theme_lua) is not None:
        return "theme"
    return "default"
