"""Theme Manager - our plugins' popups follow the window style.

Off by default. Switched on in Settings ("Plugin popups follow window
style"), Theme Manager writes ~/.config/hypr/theme-manager-glass.lua and
requires it once from hyprland.lua (a marked line, as for the shortcuts).
That file is also the switch the other plugins look for:

- QML plugin panels (PopupGlass.qml) and the GTK layer popups (Theme
  Manager, App Manager) make their card see-through: glassy 0.6,
  transparent 0.9, opaque solid. The window style is Omarchy's own
  (omarchy-hyprland-window-transparency: Font & effects, or Omarchy's menu).
- The file gives the GTK layer popups Hyprland's blur while glassy (QML
  panels already get it from Omarchy's omarchy-keyboard-panel rule), and opts
  our normal app windows into Omarchy's window transparency.

Omarchy's own popups are not touched. Off deletes the file and the line.
"""
from __future__ import annotations

import sys
from pathlib import Path

import glass_style
from glass_style import enabled, popup_alpha, window_mode  # noqa: F401  (re-exported)

LUA_MODULE = "theme-manager-glass"
REQUIRE_LINE = f'require("hypr.{LUA_MODULE}")'
REQUIRE_MARKER = "-- Theme Manager: plugin popups follow the window style"

# Our own layer-shell popups: Theme Manager, App Manager launcher and dock (GTK), Bar Organizer (QML).
LAYER_NAMESPACES = ("theme-manager", "simple-launcher-for-fools", "app-manager-dock", "bar-organizer")
# Our plugins' normal app windows (AI Agents Map, AI Playground, Settings Manager).
APP_CLASSES = ("org.omarchy.aiagentsmap", "org.omarchy.settingsmanager")


def lua_file() -> Path:
    return glass_style.hypr_dir() / glass_style.GATE_NAME


def _lua_regex(names) -> str:
    return "^(" + "|".join(n.replace(".", "\\\\.") for n in names) + ")$"


def render_lua() -> str:
    namespaces = _lua_regex(LAYER_NAMESPACES)
    classes = _lua_regex(APP_CLASSES)
    return f"""-- Managed by Theme Manager (ubruckhaus.theme-manager): our plugins' popups follow
-- the window style. Switch it off in Theme Manager → Settings; edits here are overwritten.

-- Our app windows take part in Omarchy's window transparency (opaque/transparent/glassy).
o.transparent_window("{classes}")

local state = os.getenv("XDG_STATE_HOME") or ((os.getenv("HOME") or "") .. "/.local/state")
local glassy = io.open(state .. "/omarchy/toggles/hypr/window-glassy.lua", "r")
if glassy then
  glassy:close()
  -- Glassy: blur behind our GTK popups' see-through cards, as Omarchy does for its own surfaces.
  hl.layer_rule({{
    match = {{ namespace = "{namespaces}" }},
    blur = true,
    ignore_alpha = 0.3,
  }})
  -- GTK draws app backgrounds solid, so they fade as a whole (like Omarchy's Nautilus rule).
  o.window("{classes}", {{ opacity = "0.75 0.75" }})
end
"""


def _shortcuts():
    import theme_shortcuts          # its hyprland.lua helpers (backup, modes, bounded reads)
    return theme_shortcuts


HEADER = render_lua().splitlines()[0]
BLOCK = "\n" + REQUIRE_MARKER + "\n" + REQUIRE_LINE + "\n"


def _ours(text: str | None) -> bool:
    """A generated file starts with our header line; anything else is the user's."""
    return bool(text) and text.splitlines()[0] == HEADER


def _with_block(main_text: str) -> str:
    """hyprland.lua with our marked block at the end (unchanged when it is there)."""
    if REQUIRE_MARKER + "\n" + REQUIRE_LINE in main_text:
        return main_text
    suffix = "" if main_text.endswith("\n") else "\n"
    return main_text + suffix + BLOCK


def _without_block(main_text: str) -> str:
    """hyprland.lua without our marked block: only the marker line directly
    followed by the require line goes (with the blank line we put before it).
    A require line someone wrote without our marker stays."""
    lines = main_text.split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i] == REQUIRE_MARKER and i + 1 < len(lines) and lines[i + 1] == REQUIRE_LINE:
            if out and out[-1] == "":
                out.pop()
            i += 2
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _check() -> str:
    """Reload Hyprland and return its configuration errors ("" = none).
    RuntimeError when the check itself cannot run: nothing is changed then."""
    import theme_core
    _, ok = theme_core.read_bounded_output(["hyprctl", "reload"], max_bytes=65536, timeout=5.0)
    out, ok2 = theme_core.read_bounded_output(["hyprctl", "configerrors"], max_bytes=65536, timeout=5.0)
    if not (ok and ok2):
        raise RuntimeError("hyprctl could not check the configuration")
    text = out.decode("utf-8", "replace").strip()
    return "" if not text or text.lower() in {"no errors", "none"} else text


def set_enabled(on: bool) -> None:
    """Write or remove our file and our marked line in hyprland.lua as one
    change: Hyprland must be checkable before, and any failure or new
    configuration error afterwards puts both files back exactly as they were
    (RuntimeError). Files and lines that are not ours are never changed."""
    sc = _shortcuts()
    lua, main = lua_file(), sc.hypr_main()
    main_text = sc._bounded_read(main)
    if not main_text:
        raise RuntimeError(f"cannot read {main}")
    old_lua = sc._bounded_read(lua) if lua.exists() else None
    if old_lua is not None and not _ours(old_lua):
        raise RuntimeError(f"{lua} exists and was not written by Theme Manager; it is left alone")
    before = _check()
    new_main = _with_block(main_text) if on else _without_block(main_text)

    def restore():
        if sc._bounded_read(main) != main_text:
            sc._atomic_write(main, main_text)
        if old_lua is None:
            if _ours(sc._bounded_read(lua) if lua.exists() else None):
                lua.unlink(missing_ok=True)
        elif (sc._bounded_read(lua) if lua.exists() else None) != old_lua:
            sc._atomic_write(lua, old_lua)

    try:
        if on:
            backup = main.with_name("hyprland.lua.before-theme-manager-glass")
            if not backup.exists():
                sc._atomic_write(backup, main_text)
            sc._atomic_write(lua, render_lua())
        if new_main != main_text:
            sc._atomic_write(main, new_main)
        if not on and old_lua is not None:
            lua.unlink(missing_ok=True)
        errors = _check()
    except BaseException:
        restore()
        raise
    if errors and errors != before:
        restore()
        try:
            _check()
        except RuntimeError:
            pass
        raise RuntimeError(errors)


def refresh() -> None:
    """Rewrite the file when switched on (an update may change the names)."""
    if enabled():
        sc = _shortcuts()
        if sc._bounded_read(lua_file()) != render_lua():
            set_enabled(True)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("on", "off"):
        set_enabled(cmd == "on")
    print(f"{'on' if enabled() else 'off'} {window_mode()} alpha={popup_alpha()}")
