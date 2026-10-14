"""Theme Manager - global keyboard shortcuts through Hyprland, the way
Launch & Bind does them.

Shortcuts are stored in ~/.config/omarchy/theme-manager/shortcuts.json and
written to ~/.config/hypr/theme-manager-shortcuts.lua, which hyprland.lua
requires once (a marked line at the end, with a backup of hyprland.lua next
to it). Nothing else in your config is edited: taking over a key that
another binding uses is an `hl.unbind` in the generated file, so removing the
shortcut gives the key back. Every change is checked with
`hyprctl configerrors` and rolled back if Hyprland rejects it. Removing the
last shortcut deletes the generated file and the marked line again.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import theme_core
from i18n import _, N_

PLUGIN_DIR = Path(__file__).resolve().parent
HYPR_DIR = Path.home() / ".config/hypr"
LUA_MODULE = "theme-manager-shortcuts"
REQUIRE_LINE = f'require("hypr.{LUA_MODULE}")'
REQUIRE_MARKER = "-- Theme Manager shortcuts (keep last so they win over defaults)"
MAX_CONFIG_BYTES = 1024 * 1024

# The actions a key can run: label and the command (fixed arguments only).
# Besides these, "apply:<theme>" applies one theme (a key per theme).
ACTIONS = {
    "open": (N_("Open Theme Manager"), ["theme-toggle"]),
    "next-wallpaper": (N_("Next wallpaper"), ["/usr/bin/python3", "theme_auto.py", "bg", "next"]),
    "prev-wallpaper": (N_("Previous wallpaper"), ["/usr/bin/python3", "theme_auto.py", "bg", "prev"]),
    "next-favorite": (N_("Next favorite theme"), ["/usr/bin/python3", "theme_auto.py", "theme", "next"]),
}

MOD_BITS = {"SHIFT": 1, "CTRL": 4, "ALT": 8, "SUPER": 64}
MOD_ORDER = ("SUPER", "SHIFT", "CTRL", "ALT")
MOD_ALIASES = {"SUPER": "SUPER", "WIN": "SUPER", "LOGO": "SUPER", "MOD4": "SUPER", "META": "SUPER",
               "SHIFT": "SHIFT", "CTRL": "CTRL", "CONTROL": "CTRL", "ALT": "ALT", "MOD1": "ALT"}
MOD_LABELS = {"SUPER": "Super", "SHIFT": "Shift", "CTRL": "Ctrl", "ALT": "Alt"}
DEFAULT_MODS = ["SUPER", "CTRL"]

# Who has a key, from harmless to taken.
FREE, SAME, OURS, CUSTOM, OMARCHY = "free", "same", "ours", "custom", "omarchy"

_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_BIND_RE = re.compile(r'\b(?:o\.bind(?:_toggle)?|hl\.bind)\(\s*"([^"]+)"\s*(?:,\s*"([^"]*)")?')
_UNBIND_RE = re.compile(r'\bhl\.unbind\(\s*"([^"]+)"')
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9/._-]+$")


APPLY = "apply:"


def valid_action(action) -> bool:
    if action in ACTIONS:
        return True
    return isinstance(action, str) and action.startswith(APPLY) \
        and bool(theme_core.SLUG_RE.match(action[len(APPLY):]))


def theme_of(action: str) -> str | None:
    return action[len(APPLY):] if action.startswith(APPLY) else None


def action_label(action: str) -> str:
    slug = theme_of(action)
    return _("Apply {0}").format(theme_core.display_name(slug)) if slug else _(ACTIONS[action][0])


def action_argv(action: str) -> list[str]:
    slug = theme_of(action)
    if slug:
        return ["/usr/bin/python3", "theme_auto.py", "apply", slug]
    return list(ACTIONS[action][1])


def state_file() -> Path:
    return theme_core.settings_file().parent / "shortcuts.json"


def lua_file() -> Path:
    return HYPR_DIR / f"{LUA_MODULE}.lua"


def hypr_main() -> Path:
    return HYPR_DIR / "hyprland.lua"


# --- combos ------------------------------------------------------------------

def _clean_key(key) -> str | None:
    key = str(key or "").strip()
    if not _KEY_RE.match(key):
        return None
    return key.upper() if len(key) == 1 and key.isalpha() else key


def make_combo(mods, key) -> dict | None:
    key = _clean_key(key)
    if key is None:
        return None
    mods = {m for m in mods if m in MOD_BITS}
    return {"mods": [m for m in MOD_ORDER if m in mods], "key": key}


def parse_combo(text: str) -> dict | None:
    """"SUPER + SHIFT + B" -> a combo."""
    parts = [p.strip() for p in re.split(r"[+,]", text or "") if p.strip()]
    if not parts:
        return None
    mods = set()
    for part in parts[:-1]:
        mod = MOD_ALIASES.get(part.upper())
        if mod is None:
            return None
        mods.add(mod)
    return make_combo(mods, parts[-1])


def combo_string(combo: dict) -> str:
    return " + ".join([*combo["mods"], combo["key"]])


def key_label(key: str) -> str:
    pretty = {"RETURN": "Enter", "SPACE": "Space", "ESCAPE": "Esc", "BACKSPACE": "Backspace",
              "TAB": "Tab", "COMMA": ",", "PERIOD": ".", "SLASH": "/", "MINUS": "-",
              "EQUAL": "=", "SEMICOLON": ";", "APOSTROPHE": "'", "GRAVE": "`",
              "BRACKETLEFT": "[", "BRACKETRIGHT": "]", "PAGE_UP": "Page Up", "PAGE_DOWN": "Page Down"}
    return pretty.get(key.upper(), key if len(key) > 1 else key.upper())


def combo_label(combo: dict) -> str:
    return " + ".join([*(MOD_LABELS[m] for m in combo["mods"]), key_label(combo["key"])])


def modmask(combo: dict) -> int:
    return sum(MOD_BITS[m] for m in combo["mods"])


def combo_id(combo: dict) -> tuple[int, str]:
    return modmask(combo), combo["key"].casefold()


# --- state --------------------------------------------------------------------

def load_state() -> dict:
    """{action: {"mods": [...], "key": "T", "replaces": "..."}} for valid actions."""
    try:
        raw = json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    if isinstance(raw, dict):
        for action, value in raw.items():
            if valid_action(action) and isinstance(value, dict):
                combo = make_combo(value.get("mods") or [], value.get("key"))
                if combo:
                    replaces = value.get("replaces")
                    if isinstance(replaces, str) and replaces:
                        combo["replaces"] = theme_core.plain_text(replaces, 80)
                    out[action] = combo
    return out


# --- who has a key -----------------------------------------------------------------

def _bounded_read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return ""
        return path.read_text("utf-8", "replace")
    except OSError:
        return ""


def read_live_binds() -> list[dict]:
    """Keyboard binds active in Hyprland right now (not in submaps)."""
    out, ok = theme_core.read_bounded_output(["hyprctl", "binds"], max_bytes=4 * 1024 * 1024, timeout=3.0)
    return parse_live_binds(out.decode("utf-8", "replace")) if ok else []


def parse_live_binds(text: str) -> list[dict]:
    binds = []
    for block in re.split(r"\n\s*\n", text):
        fields = {}
        for line in block.splitlines():
            name, sep, value = line.strip().partition(":")
            if sep and name and name not in fields:
                fields[name] = value.strip()
        if "modmask" not in fields or fields.get("submap", ""):
            continue
        key = fields.get("key", "")
        if "code:" in key:
            key = key.rsplit("+", 1)[-1].strip()
        if not key or key.startswith("mouse"):
            continue
        try:
            mask = int(fields["modmask"])
        except ValueError:
            continue
        binds.append({"mask": mask, "key": key.casefold(), "description": fields.get("description", "")})
    return binds


def _strip_lua_comment(line: str) -> str:
    quote, i = None, 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif line.startswith("--", i):
            return line[:i]
        i += 1
    return line


def parse_user_binds(files=None) -> list[dict]:
    """Binds from your own Hyprland files (not the generated one)."""
    if files is None:
        try:
            files = sorted(p for p in HYPR_DIR.glob("*.lua") if p.name != lua_file().name)
        except OSError:
            files = []
    binds = []
    for path in files:
        for line in _bounded_read(path).splitlines():
            code = _strip_lua_comment(line)
            match = _BIND_RE.search(code)
            if match:
                combo = parse_combo(match.group(1))
                if combo:
                    binds.append({"combo": combo, "description": match.group(2) or "", "file": path.name})
                continue
            match = _UNBIND_RE.search(code)
            if match and (combo := parse_combo(match.group(1))):
                binds = [b for b in binds if combo_id(b["combo"]) != combo_id(combo)]
    return binds


class Bindings:
    """Who owns which key right now: Theme Manager, your files, Omarchy."""

    def __init__(self, state: dict, live=None, user=None):
        self.state = state
        self.live = read_live_binds() if live is None else live
        self.user = parse_user_binds() if user is None else user

    def classify(self, combo: dict, action: str) -> tuple[str, str]:
        """(level, who) for giving `combo` to `action`."""
        cid = combo_id(combo)
        for other, sc in self.state.items():
            if combo_id(sc) == cid:
                return (SAME, action_label(other)) if other == action else (OURS, action_label(other))
        mask, key = cid
        user = [b for b in self.user if combo_id(b["combo"]) == cid]
        live = [b for b in self.live if b["mask"] == mask and b["key"] == key]
        if user:
            return CUSTOM, user[-1]["description"] or f"your {user[-1]['file']}"
        if live:
            return OMARCHY, live[-1]["description"] or _("Omarchy binding")
        return FREE, ""


# --- writing -------------------------------------------------------------------------

def lua_string(value) -> str:
    out = ['"']
    for ch in str(value):
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def action_command(action: str) -> str:
    """The shell line a key runs. Only fixed words and this plugin's own
    files, whose path must be plain (no spaces or shell characters)."""
    argv = []
    slug = theme_of(action)
    for part in action_argv(action):
        if slug is not None and part == slug:
            if not theme_core.SLUG_RE.match(part) or not re.match(r"^[A-Za-z0-9._+-]+$", part):
                raise ValueError(f"unexpected theme name: {part}")
            argv.append(part)
            continue
        if not part.startswith("/") and ("." in part or "-" in part) and (PLUGIN_DIR / part).is_file():
            part = str(PLUGIN_DIR / part)
        if part.startswith("/") and not _SAFE_PATH_RE.match(part):
            raise ValueError(f"unsafe path for a key binding: {part}")
        if not part.startswith("/") and not re.match(r"^[a-z]+$", part):
            raise ValueError(f"unexpected argument: {part}")
        argv.append(part)
    return " ".join(argv)


CAPTURE_SUBMAP = "theme-manager-capture"
CAPTURE_LUA = (f'hl.define_submap("{CAPTURE_SUBMAP}", function() '
               'hl.bind("SUPER + Escape", hl.dsp.submap("reset")) end)')


def render_lua(state: dict) -> str:
    lines = ["-- Managed by Theme Manager (ubruckhaus.theme-manager).",
             "-- Change these keys in Theme Manager → Settings; edits here are overwritten.", ""]
    themes = {t.slug for t in theme_core.discover_themes()}
    order = [a for a in ACTIONS if a in state] + sorted(a for a in state if theme_of(a))
    for action in order:
        combo = state[action]
        if theme_of(action) and theme_of(action) not in themes:
            continue   # the theme was deleted: its key does nothing
        label = f"Theme Manager: {action_label(action)}"
        note = f" (replaces: {combo['replaces']})" if combo.get("replaces") else ""
        lines.append("-- " + "".join(" " if ord(c) < 32 else c for c in label + note))
        lines.append(f"hl.unbind({lua_string(combo_string(combo))})")
        lines.append(f"o.bind({lua_string(combo_string(combo))}, {lua_string(label)}, "
                     f"{lua_string(action_command(action))})")
        lines.append("")
    lines.append("-- Key capture while Theme Manager's shortcut page is open")
    lines.append(CAPTURE_LUA)
    return "\n".join(lines) + "\n"


def _config_mode() -> int:
    """The permissions of the user's hyprland.lua (0644 if it can't be read):
    our backup and generated file never expose more than the config itself."""
    try:
        return hypr_main().stat().st_mode & 0o777
    except OSError:
        return 0o644


def _tighten(path: Path) -> None:
    """Drop permission bits a file has beyond hyprland.lua's (own regular files only)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return
    try:
        st = os.fstat(fd)
        if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
            wanted = st.st_mode & _config_mode() & 0o777
            if wanted != st.st_mode & 0o777:
                os.fchmod(fd, wanted)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, text: str) -> None:
    # Existing files keep their mode (theme_core never loosens it); new ones
    # (the backup, our Lua file) get hyprland.lua's mode.
    theme_core._atomic_write(path, text, mode=_config_mode())


def ensure_required() -> None:
    """Add the marked require line at the end of hyprland.lua, once."""
    main = hypr_main()
    text = _bounded_read(main)
    if not text:
        raise RuntimeError(f"cannot read {main}")
    if REQUIRE_LINE in text:
        return
    backup = main.with_name("hyprland.lua.before-theme-manager-shortcuts")
    if not backup.exists():
        _atomic_write(backup, text)
    else:
        _tighten(backup)   # 1.4.0 wrote it 0644 even next to a private config
    suffix = "" if text.endswith("\n") else "\n"
    _atomic_write(main, text + suffix + "\n" + REQUIRE_MARKER + "\n" + REQUIRE_LINE + "\n")


def remove_required() -> bool:
    """Take out exactly the marked lines again."""
    main = hypr_main()
    text = _bounded_read(main)
    if REQUIRE_LINE not in text:
        return False
    lines = text.splitlines()
    kept = [line for line in lines if line.strip() not in (REQUIRE_LINE, REQUIRE_MARKER)]
    while kept and not kept[-1].strip() and len(kept) < len(lines):
        kept.pop()
    _atomic_write(main, "\n".join(kept) + "\n")
    return True


def rename_theme(old: str, new: str) -> None:
    """A renamed theme keeps its key."""
    state = load_state()
    if APPLY + old in state:
        state[APPLY + new] = state.pop(APPLY + old)
        save_and_apply(state)


def drop_theme(slug: str) -> bool:
    """A deleted theme's key is given back."""
    state = load_state()
    if APPLY + slug not in state:
        return False
    del state[APPLY + slug]
    save_and_apply(state)
    return True


def prune_missing() -> list[str]:
    """Remove the keys of themes that no longer exist, so the keys are free
    again (and a binding they took over works again). Returns the themes."""
    state = load_state()
    installed = {t.slug for t in theme_core.discover_themes()}
    gone = [a for a in state if theme_of(a) and theme_of(a) not in installed]
    if gone:
        for action in gone:
            del state[action]
        save_and_apply(state)
    return [theme_of(a) for a in gone]


def config_errors() -> str:
    theme_core.read_bounded_output(["hyprctl", "reload"], max_bytes=65536, timeout=5.0)
    out, ok = theme_core.read_bounded_output(["hyprctl", "configerrors"], max_bytes=65536, timeout=5.0)
    text = out.decode("utf-8", "replace").strip()
    if not ok:
        return _("hyprctl configerrors failed")
    return "" if not text or text.lower() in {"no errors", "none"} else text


def save_and_apply(state: dict) -> None:
    """Write the shortcuts, reload Hyprland, and roll back if it reports a
    new configuration error (raised as RuntimeError)."""
    lua, store = lua_file(), state_file()
    old_lua = _bounded_read(lua) if lua.exists() else None
    old_state = _bounded_read(store) if store.exists() else None
    had_require = REQUIRE_LINE in _bounded_read(hypr_main())
    before = config_errors()
    if state:
        _atomic_write(lua, render_lua(state))
        ensure_required()
    else:
        remove_required()
        lua.unlink(missing_ok=True)
    theme_core._atomic_write(store, json.dumps(state, indent=2) + "\n", mode=0o600)
    errors = config_errors()
    if errors and errors != before:
        if old_lua is None:
            lua.unlink(missing_ok=True)
        else:
            _atomic_write(lua, old_lua)
        if not had_require:
            remove_required()
        elif state == {}:
            ensure_required()
        if old_state is None:
            store.unlink(missing_ok=True)
        else:
            theme_core._atomic_write(store, old_state, mode=0o600)
        config_errors()
        raise RuntimeError(errors)


def enter_capture() -> bool:
    """Switch Hyprland to an empty submap so every combo, even a bound one,
    reaches the shortcut page instead of running. Super + Escape leaves it."""
    enter = f'hl.dsp.submap("{CAPTURE_SUBMAP}")'
    out, ok = theme_core.read_bounded_output(["hyprctl", "dispatch", enter], max_bytes=4096, timeout=2.0)
    if ok and out.strip() == b"ok":
        return True
    theme_core.read_bounded_output(["hyprctl", "eval", CAPTURE_LUA], max_bytes=4096, timeout=2.0)
    out, ok = theme_core.read_bounded_output(["hyprctl", "dispatch", enter], max_bytes=4096, timeout=2.0)
    return ok and out.strip() == b"ok"


def leave_capture() -> None:
    out, _ok = theme_core.read_bounded_output(["hyprctl", "submap"], max_bytes=4096, timeout=2.0)
    if out.strip() == CAPTURE_SUBMAP.encode():
        theme_core.read_bounded_output(["hyprctl", "dispatch", 'hl.dsp.submap("reset")'],
                                       max_bytes=4096, timeout=2.0)
