"""Theme Manager - Theme colors from Omarchy."""

from pathlib import Path
import tomllib

DEFAULT_COLORS = {
    "background": "#1e1e2e",
    "foreground": "#cdd6f4",
    "accent": "#89b4fa",
    "selection": "#45475a",
    "muted": "#888894",
    "red": "#f04452",
    "yellow": "#f9e2af",
    "orange": "#fab387",
    "green": "#a6e3a1",
}


def _read_small(path, limit=1024 * 1024):
    """A theme's colors.toml: a regular file (a link to one is fine), read
    without blocking and at most `limit` bytes; else OSError."""
    import os
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise OSError(f"not a small regular file: {path}")
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise OSError(f"file too large: {path}")
        return data.decode("utf-8", "replace")
    finally:
        os.close(fd)



class ThemePalette:
    """Expose the current Omarchy theme colors to the Theme Manager."""

    def __init__(self):
        self.colors = DEFAULT_COLORS.copy()
        self.reload()

    def reload(self):
        try:
            path = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
            theme = tomllib.loads(_read_small(path))
            colors = {key: theme.get(key, fallback) for key, fallback in DEFAULT_COLORS.items()}
            if "muted" not in theme:
                colors["muted"] = theme.get("dark_foreground") or theme.get("color8") or DEFAULT_COLORS["muted"]
        except (OSError, ValueError):
            colors = DEFAULT_COLORS.copy()
        if colors == self.colors:
            return False
        self.colors = colors
        return True
