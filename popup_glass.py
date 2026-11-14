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


def _ensure_required() -> None:
    sc = _shortcuts()
    main = sc.hypr_main()
    text = sc._bounded_read(main)
    if not text:
        raise RuntimeError(f"cannot read {main}")
    if REQUIRE_LINE in text:
        return
    backup = main.with_name("hyprland.lua.before-theme-manager-glass")
    if not backup.exists():
        sc._atomic_write(backup, text)
    suffix = "" if text.endswith("\n") else "\n"
    sc._atomic_write(main, text + suffix + "\n" + REQUIRE_MARKER + "\n" + REQUIRE_LINE + "\n")


def _remove_required() -> None:
    sc = _shortcuts()
    main = sc.hypr_main()
    text = sc._bounded_read(main)
    if REQUIRE_LINE not in text:
        return
    lines = text.splitlines()
    kept = [line for line in lines if line.strip() not in (REQUIRE_LINE, REQUIRE_MARKER)]
    while kept and not kept[-1].strip() and len(kept) < len(lines):
        kept.pop()
    sc._atomic_write(main, "\n".join(kept) + "\n")


def set_enabled(on: bool) -> None:
    """Write or remove the file and the require line; reload Hyprland and roll
    back (RuntimeError) if it reports a new configuration error."""
    sc = _shortcuts()
    lua = lua_file()
    old_lua = sc._bounded_read(lua) if lua.exists() else None
    had_require = REQUIRE_LINE in sc._bounded_read(sc.hypr_main())
    before = sc.config_errors()
    if on:
        sc._atomic_write(lua, render_lua())
        _ensure_required()
    else:
        _remove_required()
        lua.unlink(missing_ok=True)
    errors = sc.config_errors()
    if errors and errors != before:
        if old_lua is None:
            lua.unlink(missing_ok=True)
        else:
            sc._atomic_write(lua, old_lua)
        if had_require:
            _ensure_required()
        else:
            _remove_required()
        sc.config_errors()
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
