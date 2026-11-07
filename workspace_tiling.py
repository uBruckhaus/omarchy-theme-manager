#!/usr/bin/env python3
"""Workspace presets for Theme Manager: window limits with overflow, layout
presets, and App Presets (save, restore, autostart).

Moved here from the Workspace Presets plugin (ubruckhaus.simple-tile, its
simple_tile.py), which Theme Manager replaces. The settings live in
~/.config/omarchy/theme-manager/workspaces.json (same keys the plugin kept in
its bar entry); they are read from the plugin's bar entry once, the first time.
While that plugin is still on the bar it keeps moving windows and this helper
does not (never both).

Only newly opened windows trigger automatic overflow moves. Manually moving
windows (e.g. with Super+Shift+2) intentionally overrides the workspace window
limit. Workspaces with an App Preset keep their layout and never overflow.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

WP_PLUGIN_ID = "ubruckhaus.simple-tile"   # the plugin this replaces
APP_NAME = "Theme Manager"


def normalize_addr(value):
    address = str(value).lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]+", address):
        raise ValueError("invalid window address")
    return "0x" + address


def is_tiled(client):
    ws = client.get("workspace", {}).get("id", 0)
    return (type(ws) is int and ws > 0 and client.get("mapped", False)
            and not client.get("floating", False)
            and not client.get("fullscreen", 0)
            and not client.get("hidden", False))


def tiling_counts(clients):
    return Counter(c["workspace"]["id"] for c in clients if is_tiled(c))


def workspace_limit(ws_id, cap, workspace_caps=None, workspace_auto_presets=None,
                    default_auto_presets=None):
    limit = (workspace_caps or {}).get(ws_id, cap)
    preset = ((workspace_auto_presets or {}).get(ws_id)
              or (default_auto_presets or {}).get(str(limit)))
    # Native tiling, floating and scrolling workspaces take any number of windows.
    return None if preset in ("none", "floating", "scrolling") else limit


def pick_target_ws(workspaces, counts, current_ws_id, max_windows, workspace_caps=None,
                   workspace_auto_presets=None, default_auto_presets=None, workspace_modes=None):
    """Scan forward on this monitor; never reuse another monitor's ID."""
    if type(current_ws_id) is not int or current_ws_id <= 0:
        return None
    source = next((w for w in workspaces if w["id"] == current_ws_id), None)
    if source is None:
        return None
    same_monitor = sorted(w["id"] for w in workspaces
                          if w["monitor"] == source["monitor"] and w["id"] > 0)
    for candidate in same_monitor:
        if (workspace_modes or {}).get(candidate) == "manual":
            continue
        cand_cap = workspace_limit(candidate, max_windows, workspace_caps,
                                   workspace_auto_presets, default_auto_presets)
        if candidate > current_ws_id and (cand_cap is None or counts.get(candidate, 0) < cand_cap):
            return candidate
    used = {w["id"] for w in workspaces}
    used.update(ws for ws, mode in (workspace_modes or {}).items() if mode == "manual")
    candidate = max(same_monitor) + 1
    while candidate in used:
        candidate += 1
    return candidate


def plan_move(clients, workspaces, address, cap, workspace_caps=None, workspace_modes=None,
              workspace_auto_presets=None, default_auto_presets=None):
    window = next((c for c in clients if c.get("address") == address), None)
    # A group moves together; leave deliberate tab groups alone.
    if not window or not is_tiled(window) or window.get("grouped"):
        return None
    source = window["workspace"]["id"]
    if workspace_modes and workspace_modes.get(source) == "manual":
        return None
    counts = tiling_counts(clients)
    source_cap = workspace_limit(source, cap, workspace_caps,
                                 workspace_auto_presets, default_auto_presets)
    if source_cap is None or counts[source] <= source_cap:
        return None
    target = pick_target_ws(workspaces, counts, source, cap, workspace_caps,
                            workspace_auto_presets, default_auto_presets, workspace_modes)
    return {"address": address, "source": source, "target": target} if target else None


def config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def shell_config_path():
    return config_home() / "omarchy/shell.json"


def store_path():
    return config_home() / "omarchy/theme-manager/workspaces.json"


STORE_KEYS = ("maxWindows", "workspaceCaps", "workspaceAutoPresets", "defaultAutoPresets",
              "workspaceModes", "workspaceLayouts", "workspaceSplits", "defaultSplit")
LAYOUT_PRESETS = ("side-by-side", "stacked", "master-left", "master-right", "columns", "grid", "none",
                  "floating", "scrolling")
STORE_MAX_BYTES = 512 * 1024
MAX_WORKSPACE = 100
MAX_SLOTS = 32
_APP_TEXT_KEYS = ("name", "class", "icon", "desktop_id", "desktop_path", "argv_source")
_WINDOW_TEXT_KEYS = ("address", "class", "title", "initialClass")


def _ws_key(key):
    ws = int(key) if type(key) is int or (isinstance(key, str) and key.isdigit()) else 0
    return str(ws) if 0 < ws <= MAX_WORKSPACE else None


def _text(value, limit=1024):
    return isinstance(value, str) and len(value) <= limit and "\0" not in value


def _pair(value):
    return isinstance(value, list) and len(value) == 2 and \
        all(type(v) in (int, float) and abs(v) < 100000 for v in value)


def _clean_app(app):
    if not isinstance(app, dict):
        return None
    out = {k: app[k] for k in _APP_TEXT_KEYS if _text(app.get(k))}
    argv = app.get("argv")
    if isinstance(argv, list) and len(argv) <= 64 and all(_text(a, 4096) for a in argv):
        out["argv"] = list(argv)
    return out


def _clean_window(window):
    if not isinstance(window, dict):
        return None
    # Restore places a window by both: one without a valid position and a
    # positive size is not kept (it could only fail there).
    if not (_pair(window.get("at")) and _pair(window.get("size")) and min(window["size"]) > 0):
        return None
    out = {k: window[k] for k in _WINDOW_TEXT_KEYS if _text(window.get(k))}
    for key in ("at", "size"):
        out[key] = list(window[key])
    if isinstance(window.get("floating"), bool):
        out["floating"] = window["floating"]
    app = _clean_app(window.get("app"))
    if app:
        out["app"] = app
    return out


def _clean_layout(layout):
    if not isinstance(layout, dict):
        return None
    out = {"mode": "manual"} if layout.get("mode") == "manual" else {}
    for key in ("saveApps", "autostart"):
        if isinstance(layout.get(key), bool):
            out[key] = layout[key]
    for key, clean in (("apps", _clean_app), ("windows", _clean_window)):
        items = layout.get(key)
        if isinstance(items, list):
            out[key] = [item for item in map(clean, items[:MAX_SLOTS]) if item is not None]
    if type(layout.get("count")) is int and 0 <= layout["count"] <= 1000:
        out["count"] = layout["count"]
    if type(layout.get("savedAt")) in (int, float) and layout["savedAt"] >= 0:
        out["savedAt"] = layout["savedAt"]
    return out


def _clean_split(value):
    return round(float(value), 2) if type(value) in (int, float) and 0.2 <= value <= 0.8 else None


def clean_presets(data):
    """The preset keys with only values this module writes itself (a theme
    preset's snapshot, a backup, the plugin's old bar entry)."""
    if not isinstance(data, dict):
        raise ValueError("invalid workspace presets")
    out = {}
    if type(data.get("maxWindows")) is int and 1 <= data["maxWindows"] <= 4:
        out["maxWindows"] = data["maxWindows"]

    def per_workspace(key, valid):
        raw = data.get(key)
        if isinstance(raw, dict):
            cleaned = {}
            for k, v in raw.items():
                ws, value = _ws_key(k), valid(v)
                if ws is not None and value is not None:
                    cleaned[ws] = value
            out[key] = cleaned

    per_workspace("workspaceCaps", lambda v: v if type(v) is int and 1 <= v <= 4 else None)
    per_workspace("workspaceAutoPresets", lambda v: v if v in LAYOUT_PRESETS else None)
    per_workspace("workspaceModes", lambda v: v if v in ("auto", "manual") else None)
    per_workspace("workspaceLayouts", _clean_layout)
    per_workspace("workspaceSplits", _clean_split)
    if _clean_split(data.get("defaultSplit")) is not None:
        out["defaultSplit"] = _clean_split(data["defaultSplit"])
    raw = data.get("defaultAutoPresets")
    if isinstance(raw, dict):
        out["defaultAutoPresets"] = {str(k): v for k, v in raw.items()
                                     if str(k) in ("1", "2", "3", "4") and v in LAYOUT_PRESETS}
    # One place says whether an App Preset is in use: workspaceModes. A saved
    # layout that only marks itself "manual" (older plugin data) gets its entry.
    for ws, layout in (out.get("workspaceLayouts") or {}).items():
        if layout.get("mode") == "manual" and ws not in (out.get("workspaceModes") or {}):
            out.setdefault("workspaceModes", {})[ws] = "manual"
    return out


def clean_store(data):
    out = clean_presets(data)
    for key in ("active", "autoMoveOnRestore"):
        if isinstance(data.get(key), bool):
            out[key] = data[key]
    return out


def wp_entry(config=None):
    """The Workspace Presets plugin's bar entry, or None when it is not on the bar."""
    if config is None:
        try:
            config, _mode = read_shell_config()
        except (OSError, ValueError):
            return None
    # Every level is checked for its type: a malformed shell.json never raises here.
    bar = config.get("bar") if isinstance(config, dict) else None
    layout = bar.get("layout") if isinstance(bar, dict) else None
    if not isinstance(layout, dict):
        return None
    for section in ("left", "center", "right"):
        entries = layout.get(section)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("id") == WP_PLUGIN_ID:
                return entry
    return None


def wp_on_bar():
    return wp_entry() is not None


def _read_store_file():
    path = store_path()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > STORE_MAX_BYTES:
            raise ValueError("unsafe workspaces.json")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return json.loads(stream.read(STORE_MAX_BYTES + 1))
    finally:
        os.close(fd)


# While the Workspace Presets plugin is on the bar it is in charge: both
# plugins then share ONE set of settings, the plugin's own bar entry in
# shell.json. Theme Manager reads and edits that entry (and keeps its own
# workspaces.json as a mirror for when the plugin is gone).
WP_ENTRY_KEYS = STORE_KEYS + ("active", "autoMoveOnRestore")
SHELL_JSON_MAX = 2 * 1024 * 1024


def _wp_store(entry):
    store = clean_store(entry)
    store["active"] = entry.get("active", True) is True
    return store


def read_shell_config(path=None):
    """(shell.json as data, its mode): a regular file of this user (a symlink
    is followed to it), at most SHELL_JSON_MAX bytes."""
    path = path or shell_config_path().resolve()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > SHELL_JSON_MAX:
            raise ValueError("unsafe shell.json")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(SHELL_JSON_MAX + 1)
    finally:
        os.close(fd)
    if len(data) > SHELL_JSON_MAX:
        raise ValueError("shell.json too large")
    return json.loads(data), stat.S_IMODE(info.st_mode)


def _save_wp_entry(data):
    """Replace the managed keys of the plugin's bar entry; every other key
    of shell.json stays as it is. The caller holds runtime_lock(). A
    shell.json that is a symlink (dotfiles) keeps it: the file it points to
    is replaced. Only a regular file of this user, of sane size."""
    path = shell_config_path().resolve()
    config, mode = read_shell_config(path)
    entry = wp_entry(config)
    if entry is None:
        return False
    for key in WP_ENTRY_KEYS:
        if key in data:
            entry[key] = data[key]
        elif key not in ("active", "autoMoveOnRestore"):
            entry.pop(key, None)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(config, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return True


def load_store():
    """Theme Manager's workspace settings: the Workspace Presets plugin's own
    bar entry while it is on the bar, else workspaces.json. The first time
    (no file yet) they come from the plugin's entry, if it has one."""
    entry = wp_entry()
    if entry is not None:
        return _wp_store(entry)     # the mirror is written under the lock (save_store, mirror_wp_entry)
    try:
        return clean_store(_read_store_file())
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return {}
    return {}


def mirror_wp_entry():
    """workspaces.json as a copy of the plugin's entry (also of changes made
    in the plugin), for when it leaves the bar. The caller holds runtime_lock()."""
    entry = wp_entry()
    if entry is None:
        return
    store = _wp_store(entry)
    try:
        if clean_store(_read_store_file()) == store:
            return
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return
    _write_store_file(store)


def save_store(store):
    data = clean_store(store)
    if wp_entry() is not None:
        _save_wp_entry(data)
    return _write_store_file(data)


def _write_store_file(data):
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if len(text.encode("utf-8")) > STORE_MAX_BYTES:
        raise ValueError("workspace presets too large")   # _read_store_file would refuse it
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".workspaces.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return data




def read_settings():
    entry = load_store()
    cap = entry.get("maxWindows", 2)
    caps = {int(k): v for k, v in entry.get("workspaceCaps", {}).items()}
    modes = {int(k): v for k, v in entry.get("workspaceModes", {}).items()}
    layouts = {int(k): v for k, v in entry.get("workspaceLayouts", {}).items()}
    presets = {int(k): v for k, v in entry.get("workspaceAutoPresets", {}).items()}
    defaults = dict(entry.get("defaultAutoPresets", {}))
    splits = {int(k): v for k, v in entry.get("workspaceSplits", {}).items()}
    # Off until turned on here (or imported on from the plugin): Theme Manager
    # users who never had Workspace Presets get no window limits.
    return {"enabled": entry.get("active") is True and not wp_on_bar(),
            "cap": cap, "workspace_caps": caps,
            "workspace_modes": modes,
            "workspace_layouts": layouts,
            "workspace_auto_presets": presets,
            "default_auto_presets": defaults,
            "workspace_splits": splits,
            "default_split": entry.get("defaultSplit")}


HYPR_OUTPUT_MAX = 8 * 1024 * 1024     # window titles come from apps and pages: never unbounded


def bounded_run(argv, timeout=3.0, max_bytes=HYPR_OUTPUT_MAX) -> str:
    """stdout of argv (no shell) with a hard byte cap and one deadline. Raises
    TimeoutExpired, CalledProcessError (also when the cap is exceeded) or OSError."""
    import selectors
    process = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    chunks, size, deadline = [], 0, time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                if not selector.select(left):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise subprocess.CalledProcessError(-1, argv)
                chunks.append(chunk)
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, argv)
    return b"".join(chunks).decode("utf-8", "replace")


def hypr(*args):
    return bounded_run(["hyprctl", *args])


def move_expression(address, target, follow):
    # Only validated hexadecimal addresses and integers enter Lua code.
    address = normalize_addr(address)
    if type(target) is not int or target <= 0:
        raise ValueError("invalid target workspace")
    return ('hl.dispatch(hl.dsp.window.move({window="address:%s", '
            'workspace="%d", follow=%s}))' %
            (address, target, "true" if follow else "false"))


def get_runtime_dir():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        runtime_path = Path(runtime)
        try:
            st = runtime_path.lstat()
            if runtime_path.is_symlink():
                raise RuntimeError(f"unsafe symlinked XDG_RUNTIME_DIR: {runtime_path}")
            if not stat.S_ISDIR(st.st_mode):
                raise RuntimeError(f"XDG_RUNTIME_DIR is not a directory: {runtime_path}")
            if st.st_uid != os.getuid():
                raise RuntimeError(f"XDG_RUNTIME_DIR not owned by current user: {runtime_path}")
            if st.st_mode & 0o022 != 0:
                raise RuntimeError(f"insecure permissions on XDG_RUNTIME_DIR: {runtime_path}")
            return runtime_path
        except FileNotFoundError:
            pass

    fallback = Path(f"/tmp/omarchy-simple-tile-{os.getuid()}")
    try:
        st = fallback.lstat()
        if fallback.is_symlink():
            raise RuntimeError(f"unsafe pre-existing symlinked runtime directory: {fallback}")
        if not stat.S_ISDIR(st.st_mode):
            raise RuntimeError(f"unsafe pre-existing runtime path is not a directory: {fallback}")
        if st.st_uid != os.getuid():
            raise RuntimeError(f"unsafe runtime directory ownership: {fallback}")
        if st.st_mode & 0o077 != 0:
            raise RuntimeError(f"unsafe runtime directory permissions: {fallback}")
        return fallback
    except FileNotFoundError:
        fallback.mkdir(mode=0o700, exist_ok=False)
        return fallback


@contextmanager
def runtime_lock():
    runtime_dir = get_runtime_dir()
    lock_path = runtime_dir / "omarchy-simple-tile.lock"

    try:
        st = lock_path.lstat()
        if lock_path.is_symlink():
            raise RuntimeError(f"unsafe pre-existing symlinked lock: {lock_path}")
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(f"unsafe pre-existing lock is not a regular file: {lock_path}")
        if st.st_uid != os.getuid():
            raise RuntimeError(f"unsafe lock file ownership: {lock_path}")
    except FileNotFoundError:
        pass

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(lock_path), flags, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(f"opened lock file descriptor is not a regular file: {lock_path}")
        if st.st_uid != os.getuid():
            raise RuntimeError(f"opened lock file descriptor not owned by current user: {lock_path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def get_desktop_directories():
    candidates = [
        Path.home() / ".local/share/applications",
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
    ]
    return [d for d in candidates if d.is_dir()]


DESKTOP_FILE_MAX = 256 * 1024     # a launcher file larger than this is skipped
DESKTOP_FILES_MAX = 4000          # and no more than this many are read


def read_launcher(path):
    """A launcher's text, or None. It may be a link (Flatpak exports are) but must lead to a
    regular file of at most DESKTOP_FILE_MAX bytes; opened without blocking and checked again on
    the open file, so a FIFO or device (also swapped in after the first check) is never read."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > DESKTOP_FILE_MAX:
            return None
        data = os.read(fd, DESKTOP_FILE_MAX + 1)
        return None if len(data) > DESKTOP_FILE_MAX else data.decode("utf-8", errors="ignore")
    except OSError:
        return None
    finally:
        os.close(fd)


def parse_desktop_files():
    apps, seen_ids = [], set()
    for directory in get_desktop_directories():
        for path in directory.glob("*.desktop"):
            if path.name in seen_ids:
                continue
            if len(seen_ids) >= DESKTOP_FILES_MAX:
                return apps
            seen_ids.add(path.name)
            values, section = {}, None
            try:
                text = read_launcher(path)
                if text is None:
                    continue
                for raw in text.splitlines():
                    line = raw.strip()
                    if line.startswith("[") and line.endswith("]"):
                        section = line.strip("[]")
                    elif section == "Desktop Entry" and "=" in line:
                        k, v = line.split("=", 1)
                        values[k.strip()] = v.strip()
            except OSError:
                continue
            name = values.get("Name")
            exec_cmd = values.get("Exec")
            if not name or not exec_cmd:
                continue
            if values.get("NoDisplay", "").lower() in ("true", "1"):
                continue
            apps.append({
                "name": name,
                "exec": exec_cmd,
                "icon": values.get("Icon", ""),
                "desktop_path": str(path),
                "desktop_id": path.stem,
                "startup_class": values.get("StartupWMClass", ""),
            })
    return apps


def valid_desktop_id(value):
    """A launcher name as an installed .desktop file has it: one file name (Omarchy's web apps
    have spaces), no "/", NUL or leading dot. A window class (chosen by the app itself) that is
    not one never becomes a launcher name."""
    return isinstance(value, str) and 0 < len(value) <= 255 and "/" not in value \
        and "\0" not in value and not value.startswith(".")


def installed_launcher(app, desktop_apps=None):
    """The installed .desktop file a saved app starts from, or "". Only the records of
    parse_desktop_files count: the saved desktop_path must be one of them, else the saved
    (validated) desktop_id must name one. A saved path or ID is never joined onto a directory."""
    records = parse_desktop_files() if desktop_apps is None else desktop_apps
    saved_path = app.get("desktop_path")
    if isinstance(saved_path, str) and saved_path:
        for record in records:
            if record["desktop_path"] == saved_path:
                return record["desktop_path"]
    desktop_id = app.get("desktop_id") or (Path(saved_path).stem if isinstance(saved_path, str) and saved_path else "")
    if valid_desktop_id(desktop_id):
        for record in records:
            if record["desktop_id"] == desktop_id:
                return record["desktop_path"]
    return ""


def resolve_app_for_client(client, desktop_apps):
    cls = client.get("class", "").strip()
    icls = client.get("initialClass", "").strip()
    pid = client.get("pid", 0)

    # A terminal window is saved as its terminal's own launcher (1–3 below): the
    # command running in it is never read from /proc, stored or started again.

    # 1. Match StartupWMClass
    for app in desktop_apps:
        sc = app.get("startup_class", "")
        if sc and (sc.casefold() == cls.casefold() or sc.casefold() == icls.casefold()):
            return app

    # 2. Match desktop_id
    for app in desktop_apps:
        did = app.get("desktop_id", "")
        if did and (did.casefold() == cls.casefold() or did.casefold() == icls.casefold()):
            return app

    # 2b. Match webapps / domain in class or Exec
    cls_lower = cls.casefold()
    for app in desktop_apps:
        did = app.get("desktop_id", "").casefold()
        name = app.get("name", "").casefold()
        exec_cmd = app.get("exec", "").casefold()
        # A launcher's ID or name inside the class counts only when it is a real word (at least
        # 4 characters): a "Name=R" launcher must not claim every window with an "r" in its class.
        if (len(did) >= 4 and did in cls_lower) or (len(name) >= 4 and name in cls_lower):
            return app
        if "omarchy-launch-webapp" in exec_cmd:
            url_part = exec_cmd.split()[-1].replace("https://", "").replace("http://", "").split("/")[0]
            if len(url_part) >= 4 and len(cls_lower) >= 4 and (url_part in cls_lower or cls_lower in url_part):
                return app

    # 3. Match /proc/<pid>/exe
    if pid:
        try:
            exe_target = Path(os.readlink(f"/proc/{pid}/exe")).name.casefold()
            for app in desktop_apps:
                did = app.get("desktop_id", "").casefold()
                if did == exe_target:
                    return app
        except OSError:
            pass

    # 4. No installed launcher matches: the window is remembered for the layout, but nothing is
    # recorded to start it. Its process's cmdline (and its class) are chosen by the app itself; a
    # sandboxed app could name a host command there, which a restore would run outside the sandbox.
    # Its class is no launcher name either: the window is matched again by its class.
    return {
        "name": cls.capitalize() if cls else "Application",
        "class": cls,
        "desktop_id": "",
        "desktop_path": "",
        "icon": cls.casefold() if cls else "application-x-executable",
        "argv": [],
    }


def detect_layout_preset(windows):
    tiled = [w for w in windows if not w.get("floating")]
    count = len(tiled)
    if count <= 1:
        return "none"
    if count == 2:
        dx = abs(tiled[0].get("at", [0, 0])[0] - tiled[1].get("at", [0, 0])[0])
        dy = abs(tiled[0].get("at", [0, 0])[1] - tiled[1].get("at", [0, 0])[1])
        return "side-by-side" if dx >= dy else "stacked"
    if count == 3:
        heights = [w.get("size", [0, 0])[1] for w in tiled]
        max_h = max(heights)
        min_h = min(heights)
        if max_h >= min_h * 1.3:
            master = max(tiled, key=lambda w: w.get("size", [0, 0])[1])
            slaves = [w for w in tiled if w is not master]
            slaves_x = [w.get("at", [0, 0])[0] for w in slaves]
            if master.get("at", [0, 0])[0] < min(slaves_x):
                return "master-left"
            else:
                return "master-right"
        return "columns"
    if count == 4:
        xs = sorted(w.get("at", [0, 0])[0] for w in tiled)
        if all(xs[i+1] - xs[i] > 80 for i in range(3)):
            return "columns"
        return "grid"
    return "none"


def capture_workspace(ws_id):
    if type(ws_id) is not int or ws_id <= 0:
        raise ValueError("invalid workspace id")
    clients = json.loads(hypr("clients", "-j"))
    ws_clients = [c for c in clients if c.get("workspace", {}).get("id") == ws_id and c.get("mapped", False)]

    desktop_apps = parse_desktop_files()
    apps = []
    seen_app_ids = set()
    windows = []

    for c in ws_clients:
        win_info = {
            "address": normalize_addr(c.get("address")),
            "class": c.get("class", ""),
            "title": c.get("title", ""),
            "at": c.get("at", [0, 0]),
            "size": c.get("size", [0, 0]),
            "floating": bool(c.get("floating", False)),
        }
        windows.append(win_info)

        app = resolve_app_for_client(c, desktop_apps)
        if app:
            win_info["app"] = dict(app)
        win_info["initialClass"] = c.get("initialClass", "")
        if app:
            app_key = app.get("desktop_id") or app.get("class") or app.get("name")
            if app_key and app_key not in seen_app_ids:
                seen_app_ids.add(app_key)
                apps.append({
                    "name": app.get("name", ""),
                    "class": app.get("class", c.get("class", "")),
                    "icon": app.get("icon", "application-x-executable"),
                    "desktop_id": app.get("desktop_id", ""),
                    "desktop_path": app.get("desktop_path", ""),
                    "argv": app.get("argv", []),
                    "argv_source": app.get("argv_source", ""),
                })

    session = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if session:
        try:
            marker = get_runtime_dir() / ("simple-tile-autostart-" + hashlib.sha256(session.encode()).hexdigest())
            if marker.exists():
                marker.unlink()
        except Exception:
            pass

    detected_preset = detect_layout_preset(windows)
    settings = read_settings()
    configured_preset = settings.get("workspace_auto_presets", {}).get(ws_id)
    preset = configured_preset or detected_preset

    return {
        "status": "ok",
        "workspace": ws_id,
        "count": len(windows),
        "preset": preset,
        "windows": windows,
        "apps": apps
    }


def trusted_argv(app):
    """A saved command line is never run (2.0.0, marketplace review): the store
    can be the Workspace Presets plugin's entry in shell.json, and a label such
    as argv_source "terminal" written there proves nothing. Every saved app
    starts from an installed launcher instead, a terminal as a plain terminal."""
    return []


def restorable(app):
    """The saved app as it may be started: never with a saved command line."""
    return {k: v for k, v in app.items() if k != "argv"}


def is_app_available(app):
    """Whether an app can be started: only through an installed launcher (a saved
    command line is never used, see restorable)."""
    app.pop("argv", None)
    # Only an installed launcher starts the app (installed_launcher), never a saved path or ID.
    desktop_path = app.get("desktop_path", "")
    launcher = installed_launcher(app) if desktop_path or app.get("desktop_id") else ""
    app["desktop_path"] = launcher
    if launcher:
        return True, "ok"
    if desktop_path:
        return False, "desktop_file_not_found"

    # No launcher and no trusted command: the window's class is chosen by the app itself, so it is
    # never looked up as a program to run.
    return False, "no_launcher"


def plain(text, limit=300) -> str:
    """No markup and no control characters: notification daemons render markup,
    and window classes come from the apps themselves."""
    text = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(text))
    text = text.replace("&", "+").replace("<", "‹").replace(">", "›")
    return text[:limit]


def send_notification(title, body, urgency="normal"):
    """Send a desktop notification via notify-send if available (plain text only)."""
    title, body = plain(title, 120), plain(body, 300)
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(
                ["notify-send", "-a", APP_NAME, "-u", urgency, title, body],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass


def find_next_free_workspace(preferred_start=1):
    """Find the next workspace number with no open windows."""
    try:
        live = json.loads(hypr("clients", "-j"))
        occupied = {
            c.get("workspace", {}).get("id")
            for c in live
            if c.get("mapped") and not c.get("hidden")
        }
    except Exception:
        occupied = set()

    for w in range(preferred_start, 11):
        if w not in occupied and w > 0:
            return w
    for w in range(1, preferred_start):
        if w not in occupied and w > 0:
            return w
    return max(occupied | {0}) + 1


def _clean_env():
    env = os.environ.copy()
    if "LD_PRELOAD" in env:
        parts = [p for p in env["LD_PRELOAD"].split(":") if p and "libgtk4-layer-shell" not in p]
        if parts:
            env["LD_PRELOAD"] = ":".join(parts)
        else:
            del env["LD_PRELOAD"]
    return env


def launch_app(app):
    """Start a saved app only from its installed launcher: uwsm-app with the
    .desktop file (in app-graphical.slice), Gio as the fallback. Never a saved
    command line, never a shell string."""
    desktop_path = app.get("desktop_path", "")
    # Second check where it is started: only an installed launcher's own file (is_app_available
    # put it there), so no other path can hand uwsm-app or Gio a file from elsewhere.
    if desktop_path and desktop_path not in {r["desktop_path"] for r in parse_desktop_files()}:
        return False

    # 1. Primary: uwsm-app
    if desktop_path and shutil.which("uwsm-app"):
        try:
            subprocess.Popen(
                ["uwsm-app", "--", desktop_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=_clean_env(),
            )
            return True
        except OSError:
            pass

    # 2. Native Gio fallback
    if desktop_path:
        try:
            from gi.repository import GioUnix
            info = GioUnix.DesktopAppInfo.new_from_filename(desktop_path)
            if info:
                return bool(info.launch_uris([], None))
        except Exception:
            pass

    return False


def matches_window(saved, client):
    classes = {str(saved.get(k, "")).casefold() for k in ("class", "initialClass")}
    classes.discard("")
    return bool(classes & {str(client.get(k, "")).casefold()
                           for k in ("class", "initialClass")})


def wait_for_window(saved, before, timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        clients = json.loads(hypr("clients", "-j"))
        candidates = [c for c in clients if c.get("mapped") and not c.get("hidden")
                      and c.get("address") not in before and matches_window(saved, c)]
        if candidates:
            return max(candidates, key=lambda c: c.get("title") == saved.get("title"))
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def wait_for_closed_windows(addresses, timeout=8):
    """A close dispatch is a request; apps may delay or show a save dialog."""
    deadline = time.monotonic() + timeout
    while True:
        clients = json.loads(hypr("clients", "-j"))
        remaining = [c for c in clients if c.get("mapped") and c.get("address") in addresses]
        if not remaining or time.monotonic() >= deadline:
            return clients, remaining
        time.sleep(0.1)


def layout_tree(windows):
    """Recover a slicing tree from saved rectangles, independent of app order."""
    if not windows or any(len(w.get("at", [])) != 2 or len(w.get("size", [])) != 2
                          or any(type(v) not in (int, float) for v in w["at"] + w["size"])
                          or min(w["size"]) <= 0 for w in windows):
        raise ValueError("Invalid saved window geometry; save the layout again")
    if len(windows) == 1:
        return windows[0]
    for axis in (0, 1):
        ordered = sorted(windows, key=lambda w: w["at"][axis])
        for i in range(1, len(ordered)):
            left, right = ordered[:i], ordered[i:]
            edge = max(w["at"][axis] + w["size"][axis] for w in left)
            start = min(w["at"][axis] for w in right)
            if edge > start:
                continue
            low = min(w["at"][axis] for w in ordered)
            high = max(w["at"][axis] + w["size"][axis] for w in ordered)
            return {"axis": axis, "ratio": 2 * ((edge + start) / 2 - low) / (high - low),
                    "left": layout_tree(left), "right": layout_tree(right)}
    raise ValueError("Saved tiled windows overlap; save an ungrouped tiled layout first")


def align_tiled_windows(ws_id, pairs):
    tiled_pairs = [(saved, client) for saved, client in pairs if not saved.get("floating")]
    if len(tiled_pairs) < 2:
        return
    try:
        live_clients = [c for c in json.loads(hypr("clients", "-j"))
                        if c.get("workspace", {}).get("id") == ws_id and is_tiled(c)]
    except Exception:
        return
    if len(live_clients) < 2:
        return

    # Map each saved window to the live slot closest to saved["at"]
    unassigned_slots = list(live_clients)
    desired_slot_for_client = {}

    for saved, client in tiled_pairs:
        s_at = saved.get("at", [0, 0])
        if not unassigned_slots:
            break
        closest = min(unassigned_slots, key=lambda c: (c.get("at", [0, 0])[0] - s_at[0])**2 + (c.get("at", [0, 0])[1] - s_at[1])**2)
        desired_slot_for_client[normalize_addr(client["address"])] = normalize_addr(closest["address"])
        unassigned_slots.remove(closest)

    current_occupant = {normalize_addr(c["address"]): normalize_addr(c["address"]) for c in live_clients}
    client_to_slot = {normalize_addr(c["address"]): normalize_addr(c["address"]) for c in live_clients}

    for client_addr, target_slot in desired_slot_for_client.items():
        curr_slot = client_to_slot.get(client_addr)
        if curr_slot and curr_slot != target_slot:
            other_client = current_occupant.get(target_slot)
            if other_client and other_client != client_addr:
                lua_expr = 'hl.dispatch(hl.dsp.window.swap({window="address:%s", with="address:%s"}))' % (client_addr, other_client)
                try:
                    hypr("eval", lua_expr)
                except Exception:
                    pass
                current_occupant[target_slot] = client_addr
                current_occupant[curr_slot] = other_client
                client_to_slot[client_addr] = target_slot
                client_to_slot[other_client] = curr_slot


def restore_geometry(ws_id, pairs):
    tiled = [dict(saved, address=client["address"]) for saved, client in pairs
             if not saved.get("floating")]
    if any(c.get("grouped") or c.get("fullscreen") for _, c in pairs):
        raise ValueError("Grouped or fullscreen windows must be ungrouped/unfullscreened before restoring")
    # Validate the complete tree before changing any window state.
    tree = layout_tree(tiled) if tiled else None
    live = json.loads(hypr("clients", "-j"))
    addresses = {c["address"] for _, c in pairs}
    if any(is_tiled(c) and c["workspace"]["id"] == ws_id
           and c["address"] not in addresses for c in live):
        raise ValueError("Workspace has extra tiled windows; move them away before restoring the saved layout")

    def dispatch(expr):
        result = hypr("eval", "hl.dispatch(hl.dsp." + expr + ")")
        if "error" in result.lower():
            raise RuntimeError(result.strip())

    def target(w):
        return 'window="address:%s"' % normalize_addr(w["address"])

    def first(node):
        return first(node["left"]) if "axis" in node else node

    def build(node):
        if "axis" not in node:
            return
        left, right = first(node["left"]), first(node["right"])
        dispatch('focus({' + target(left) + '})')
        dispatch('layout("preselect %s")' % ("r" if node["axis"] == 0 else "d"))
        dispatch('window.float({' + target(right) + ', action="off"})')
        dispatch('focus({' + target(left) + '})')
        dispatch('layout("splitratio %.8f exact")' % node["ratio"])
        build(node["left"])
        build(node["right"])

    hypr("eval", 'hl.dispatch(hl.dsp.focus({workspace=%d}))' % ws_id)
    # Use native dwindle tiles, including for rectangles originally made in master.
    if tiled:
        hypr("eval", 'hl.workspace_rule({workspace="%d", layout="dwindle"})' % ws_id)
    try:
        for saved, client in pairs:
            dispatch('window.float({' + target(client) + ', action="on"})')
        if tree:
            dispatch('window.float({' + target(first(tree)) + ', action="off"})')
            build(tree)
        for saved, client in pairs:
            if saved.get("floating"):
                dispatch('window.resize({' + target(client) + ', x=%d, y=%d, relative=false})' % tuple(saved["size"]))
                dispatch('window.move({' + target(client) + ', x=%d, y=%d, relative=false})' % tuple(saved["at"]))
    finally:
        # Never leave originally tiled windows floating after a failed rebuild.
        for window in tiled:
            dispatch('window.float({' + target(window) + ', action="off"})')
        if tiled:
            dispatch('layout("preselect none")')

    align_tiled_windows(ws_id, pairs)

    actual = {c["address"]: c for c in json.loads(hypr("clients", "-j"))}
    for saved, client in pairs:
        current = actual.get(client["address"])
        if (not current or current.get("workspace", {}).get("id") != ws_id
                or bool(current.get("floating")) != bool(saved.get("floating"))):
            raise RuntimeError("Window placement could not be verified")
    # Check relative slot order, rather than reporting success after IPC alone.
    for left in tiled:
        for right in tiled:
            if left is right:
                continue
            for axis in (0, 1):
                if left["at"][axis] + left["size"][axis] <= right["at"][axis]:
                    a, b = actual[left["address"]], actual[right["address"]]
                    if a["at"][axis] + a["size"][axis] > b["at"][axis] + 4:
                        raise RuntimeError("Saved tile positions could not be restored")


def restore_workspace(ws_id, dry_run=False, conflict_action="ask"):
    with runtime_lock():
        return _restore_workspace(ws_id, dry_run, conflict_action)


def _restore_workspace(ws_id, dry_run=False, conflict_action="ask"):
    if type(ws_id) is not int or ws_id <= 0:
        raise ValueError("invalid workspace id")
    if conflict_action not in ("ask", "move", "close", "keep"):
        raise ValueError("invalid conflict action: %s" % conflict_action)
    layouts = read_settings().get("workspace_layouts", {})
    layout = layouts.get(ws_id)
    if not layout:
        return {"status": "no_layout", "workspace": ws_id}
    apps = layout.get("apps", [])
    windows = layout.get("windows", [])
    if "apps" in layout and layout.get("saveApps") is not False:
        # Older remove buttons only edited apps. Do not resurrect their stale slots.
        windows = [w for w in windows if any(matches_window(w, app) for app in apps)]
    slots = windows or apps  # Legacy app-only snapshots remain usable.
    if not slots:
        return {"status": "no_apps", "workspace": ws_id}
    clients = json.loads(hypr("clients", "-j"))
    original_ws = json.loads(hypr("activeworkspace", "-j")).get("id")

    current_ws_clients = [c for c in clients if c.get("mapped") and not c.get("hidden")
                          and c.get("workspace", {}).get("id") == ws_id]

    matched_on_ws = set()
    for saved in slots:
        candidates = [c for c in current_ws_clients
                      if c.get("address") not in matched_on_ws and matches_window(saved, c)]
        if candidates:
            def candidate_score(c):
                score = 0
                if c.get("title") == saved.get("title"):
                    score += 1000
                elif saved.get("title") and saved.get("title") in c.get("title", ""):
                    score += 500
                c_at = c.get("at", [0, 0])
                s_at = saved.get("at", [0, 0])
                dist = (c_at[0] - s_at[0]) ** 2 + (c_at[1] - s_at[1]) ** 2
                score -= dist / 10000.0
                return score
            best = max(candidates, key=candidate_score)
            matched_on_ws.add(best["address"])

    extra_on_ws = [c for c in current_ws_clients if c["address"] not in matched_on_ws]

    if extra_on_ws and conflict_action == "ask":
        return {
            "status": "conflict",
            "workspace": ws_id,
            "conflict_count": len(extra_on_ws),
            "next_free_workspace": find_next_free_workspace(ws_id + 1),
            "conflicts": [
                {
                    "address": c["address"],
                    "title": c.get("title", ""),
                    "class": c.get("class", ""),
                    "initialClass": c.get("initialClass", ""),
                }
                for c in extra_on_ws
            ],
        }

    used, pairs = set(), []
    launched, already_running, failed = [], [], []
    focus_changed = False
    try:
        if extra_on_ws and not dry_run:
            if conflict_action == "move":
                next_free = find_next_free_workspace(ws_id + 1)
                for c in extra_on_ws:
                    hypr("eval", move_expression(c["address"], next_free, False))
                names = [c.get("class") or c.get("initialClass") or "Window" for c in extra_on_ws]
                send_notification(
                    APP_NAME,
                    "Moved %d window(s) (%s) from Workspace %d to Workspace %d" %
                    (len(extra_on_ws), ", ".join(names), ws_id, next_free)
                )
                clients = json.loads(hypr("clients", "-j"))
            elif conflict_action == "close":
                # Focus the target before closing so app confirmation dialogs
                # are visible. Never rebuild a layout around windows still closing.
                focus_changed = True
                hypr("eval", 'hl.dispatch(hl.dsp.focus({workspace=%d}))' % ws_id)
                closing = {normalize_addr(c["address"]) for c in extra_on_ws}
                for addr in sorted(closing):
                    output = hypr("eval", 'hl.dispatch(hl.dsp.window.close({window="address:%s"}))' % addr)
                    if "error" in output.lower():
                        raise RuntimeError("Could not close conflicting window: " + output.strip())
                clients, remaining = wait_for_closed_windows(closing)
                if remaining:
                    return {"status": "error", "workspace": ws_id,
                            "launched": [], "already_running": [], "geometry_restored": False,
                            "failed": [{"name": c.get("class") or c.get("initialClass") or "Window",
                                        "reason": "close_timeout"} for c in remaining]}
            elif conflict_action == "keep":
                for c in extra_on_ws:
                    if not c.get("floating"):
                        addr = normalize_addr(c["address"])
                        hypr("eval", 'hl.dispatch(hl.dsp.window.float({window="address:%s", action="on"}))' % addr)
                clients = json.loads(hypr("clients", "-j"))

        for saved in slots:
            app = saved.get("app") or next((a for a in apps if matches_window(saved, a)), saved)
            name = app.get("name") or saved.get("class") or "Application"
            candidates = [c for c in clients if c.get("mapped") and not c.get("hidden")
                          and c.get("address") not in used and matches_window(saved, c)
                          and c.get("workspace", {}).get("id") == ws_id]
            if not candidates:
                # Reuse a uniquely identifiable app on the wrong workspace, but
                # do not steal windows assigned to another saved workspace.
                elsewhere = [c for c in clients if c.get("mapped") and not c.get("hidden")
                             and c.get("address") not in used and matches_window(saved, c)
                             and not any(matches_window(slot, c) for slot in
                                 (layouts.get(c.get("workspace", {}).get("id"), {}).get("windows")
                                  or layouts.get(c.get("workspace", {}).get("id"), {}).get("apps", [])))]
                if len(elsewhere) == 1:
                    candidates = elsewhere
            def candidate_score(c):
                score = 0
                if c.get("title") == saved.get("title"):
                    score += 1000
                elif saved.get("title") and saved.get("title") in c.get("title", ""):
                    score += 500
                c_at = c.get("at", [0, 0])
                s_at = saved.get("at", [0, 0])
                dist = (c_at[0] - s_at[0]) ** 2 + (c_at[1] - s_at[1]) ** 2
                score -= dist / 10000.0
                return score

            client = max(candidates, key=candidate_score) if candidates else None
            if client:
                already_running.append(name)
            else:
                if layout.get("saveApps") is False:
                    failed.append({"name": name, "reason": "app_launching_disabled"})
                    continue
                app = restorable(app)
                available, reason = is_app_available(app)
                if not available:
                    failed.append({"name": name, "reason": reason})
                    continue
                if dry_run:
                    launched.append(name)
                    continue
                before = {c.get("address") for c in json.loads(hypr("clients", "-j"))}
                focus_changed = True
                hypr("eval", 'hl.dispatch(hl.dsp.focus({workspace=%d}))' % ws_id)
                if not launch_app(app):
                    failed.append({"name": name, "reason": "launch_failed"})
                    continue
                client = wait_for_window(saved, before)
                if not client:
                    failed.append({"name": name, "reason": "window_timeout"})
                    continue
                launched.append(name)
            used.add(client.get("address"))
            if not dry_run:
                address = normalize_addr(client["address"])
                if client.get("workspace", {}).get("id") != ws_id:
                    hypr("eval", move_expression(address, ws_id, False))
                clients = json.loads(hypr("clients", "-j"))
                client = next((c for c in clients if c.get("address") == address), None)
                if client is None or client.get("workspace", {}).get("id") != ws_id:
                    failed.append({"name": name, "reason": "workspace_move_failed"})
                    continue
                pairs.append((saved, client))
        if windows and not dry_run and not failed:
            preset = layout.get("preset") or detect_layout_preset(windows)
            if preset and preset != "none":
                try:
                    apply_layout_preset(ws_id, preset)
                except Exception:
                    pass
            try:
                restore_geometry(ws_id, pairs)
            except (ValueError, RuntimeError, subprocess.SubprocessError) as error:
                failed.append({"name": "Layout", "reason": str(error)})
    finally:
        if original_ws and not dry_run and (focus_changed or pairs):
            hypr("eval", 'hl.dispatch(hl.dsp.focus({workspace=%d}))' % original_ws)
    return {"status": ("partial" if launched or already_running else "error") if failed else "ok",
            "workspace": ws_id, "launched": launched, "already_running": already_running,
            "geometry_restored": bool(windows and not dry_run and not failed), "failed": failed}


def autostart_all(dry_run=False):
    # A bar exists on each monitor and is recreated on shell reloads.
    # Serialize the entire batch and mark it by compositor session, not process.
    if dry_run:
        return _autostart_all(True)
    with runtime_lock():
        # While the Workspace Presets plugin is still on the bar it starts the
        # apps (from its own settings); never both. "Tiling active" is about
        # window limits only: autostart is each App Preset's own switch, as
        # it was in that plugin.
        if wp_on_bar():
            return {"status": "skipped", "reason": "workspace presets plugin on the bar"}
        session = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not session:
            raise RuntimeError("Hyprland session is not ready")
        marker = get_runtime_dir() / ("simple-tile-autostart-" + hashlib.sha256(session.encode()).hexdigest())
        if marker.exists():
            return {"status": "already_started"}
        hypr("clients", "-j")  # Do not consume the session on an IPC readiness failure.
        result = _autostart_all(False)
        if result["workspaces"] and result.get("failed_count", 0) == 0:
            marker.write_text(json.dumps(result))
        return result


# --- autostart approval (the same file and fingerprint as Workspace Presets 0.7.0) ----
# The store can be the Workspace Presets plugin's shell.json entry, which anything
# that can write that file could edit; so a saved app list alone never starts apps
# at login: the user confirms it (saving the App Preset or switching its autostart
# on, here or in the plugin's panel), and a fingerprint of that list is kept in a
# private file both plugins read.

def approvals_path():
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "ubruckhaus.simple-tile" / "autostart.json"


def _launcher_pair(record):
    if not isinstance(record, dict):
        return None
    ident, path = record.get("desktop_id"), record.get("desktop_path")
    return (ident if isinstance(ident, str) else "", path if isinstance(path, str) else "")


def launcher_keys(layout):
    """Every launcher a restore of this layout can start from: the app list,
    each saved window and the app record inside it, as sorted unique pairs."""
    records = []
    for key in ("apps", "windows"):
        items = layout.get(key) if isinstance(layout.get(key), list) else []
        for item in items:
            records.append(item)
            if isinstance(item, dict):
                records.append(item.get("app"))
    return sorted({pair for pair in map(_launcher_pair, records) if pair and pair != ("", "")})


def app_fingerprint(layout):
    return hashlib.sha256(json.dumps(launcher_keys(layout)).encode()).hexdigest()


def load_approvals():
    try:
        fd = os.open(approvals_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return {}
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 65536:
            return {}
        data = json.loads(os.read(fd, 65537))
    except (OSError, ValueError):
        return {}
    finally:
        os.close(fd)
    return {k: v for k, v in data.items() if isinstance(k, str) and k.isdigit() and isinstance(v, str)} \
        if isinstance(data, dict) else {}


def save_approvals(data):
    path = approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = os.lstat(path.parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("unsafe state directory")
    os.chmod(path.parent, 0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".autostart.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def approve_autostart(ws_id, on, confirmed=None, locked=False):
    """The user's confirmation in the Presets page: the launchers of the layout
    the page itself saved (`confirmed`) may start at login, but only while the
    store still holds exactly those (on); or no longer (off)."""
    if type(ws_id) is not int or ws_id <= 0:
        raise ValueError("invalid workspace id")

    def work():
        approvals = load_approvals()
        layout = read_settings().get("workspace_layouts", {}).get(ws_id)
        if on and isinstance(layout, dict) and layout.get("autostart") is not False and \
                isinstance(confirmed, dict) and launcher_keys(confirmed) == launcher_keys(layout):
            approvals[str(ws_id)] = app_fingerprint(layout)
        else:
            approvals.pop(str(ws_id), None)
        save_approvals(approvals)
        return str(ws_id) in approvals
    if locked:
        return work()
    with runtime_lock():
        return work()


def _autostart_all(dry_run):
    started, unconfirmed = [], []
    approvals = load_approvals()
    settings = read_settings()
    modes = settings.get("workspace_modes", {})
    for ws_id, layout in sorted(settings.get("workspace_layouts", {}).items()):
        # Only an App Preset in use starts: one the workspace left (a layout
        # chosen, a reset) stays saved but quiet.
        in_use = modes.get(ws_id) == "manual"   # clean_presets writes it for every App Preset in use
        if layout.get("autostart") is True and in_use and approvals.get(str(ws_id)) != app_fingerprint(layout):
            unconfirmed.append(ws_id)        # never confirmed (or changed since): not started
            continue
        if layout.get("autostart") is True and in_use:
            # The batch already owns the runtime lock.
            restore = restore_workspace if dry_run else _restore_workspace
            kwargs = {"dry_run": dry_run}
            if not dry_run:
                kwargs["conflict_action"] = "move"
            started.append({"workspace": ws_id, "result": restore(ws_id, **kwargs)})
    failures = sum(len(w["result"].get("failed", [])) for w in started)
    if unconfirmed and not dry_run:
        send_notification(APP_NAME, "Autostart skipped for workspace %s: switch its autostart off and on once "
                                    "in App Presets to confirm the saved apps." % ", ".join(map(str, unconfirmed)))
    return {"status": "partial" if failures else "ok", "workspaces": started, "failed_count": failures,
            "unconfirmed": unconfirmed}


# Omarchy 4.5 gives every workspace a mode (Super + L): dwindle, scrolling or
# floating. It saves each one as a small Lua file that calls o.workspace_mode
# and loads them at every config load. The Floating and Scrolling presets are
# that mode, so the Omarchy bar, the dock, titlebars and Super + L agree.
OMARCHY_MODES = ("dwindle", "scrolling", "floating")
FREE_PRESETS = ("floating", "scrolling")    # Presets without a window limit or overflow.
MODE_FILE_MAX = 4096


def omarchy_modes_supported():
    return shutil.which("omarchy-hyprland-workspace-layout-current") is not None


def omarchy_modes_dir():
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "omarchy/workspace-layouts"


def read_mode_file(path, key):
    """The quoted value of key in one of Omarchy's saved mode files, or None.
    Only small regular files are read, never through a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        text = os.read(fd, MODE_FILE_MAX).decode("utf-8", "replace")
    finally:
        os.close(fd)
    match = re.search(r'\b%s\s*=\s*"([a-z]+)"' % key, text)
    return match.group(1) if match else None


def omarchy_default_mode():
    mode = read_mode_file(omarchy_modes_dir() / "default.lua", "default")
    if not mode:
        try:
            mode = json.loads(hypr("getoption", "general.layout", "-j")).get("str")
        except Exception:
            mode = None
    return mode if mode in OMARCHY_MODES else "dwindle"


def omarchy_workspace_modes(ws_ids):
    """{workspace: mode} as Omarchy shows it: the workspace's saved mode, else
    the default. Empty when this Omarchy has no workspace modes."""
    if not omarchy_modes_supported():
        return {}
    default = omarchy_default_mode()
    modes = {}
    for ws_id in ws_ids:
        if type(ws_id) is int and ws_id > 0:
            mode = read_mode_file(omarchy_modes_dir() / ("%d.lua" % ws_id), "mode")
            modes[ws_id] = mode if mode in OMARCHY_MODES else default
    return modes


def set_omarchy_mode(ws_id, mode, dry_run=False):
    """Give ws_id Omarchy's mode, like Super + L: apply it now and save it so
    it comes back after a reload or login. Returns False without Omarchy
    workspace modes."""
    if type(ws_id) is not int or ws_id <= 0 or mode not in OMARCHY_MODES:
        raise ValueError("invalid workspace mode")
    if not omarchy_modes_supported():
        return False
    if dry_run:
        return True
    output = hypr("eval", 'o.workspace_mode({ workspace = "%d", mode = "%s" })' % (ws_id, mode))
    if "error" in output.lower():
        raise RuntimeError("Omarchy did not take the workspace mode: " + output.strip())
    directory = omarchy_modes_dir()
    directory.mkdir(parents=True, exist_ok=True)
    st = directory.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError("unsafe Omarchy workspace-layouts directory")
    path = directory / ("%d.lua" % ws_id)
    if mode == omarchy_default_mode():
        # On the default the workspace follows it again, as Super + L does.
        path.unlink(missing_ok=True)
    else:
        fd, temp = tempfile.mkstemp(dir=directory, prefix=".%d." % ws_id, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write('o.workspace_mode({ workspace = "%d", mode = "%s" })\n' % (ws_id, mode))
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
    for target in ("omarchy.workspaces", "omarchy.dock"):
        try:
            subprocess.run(["omarchy-shell", "-q", target, "refresh"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
    # Titlebars come from a Hyprland plugin that only loads while the config
    # loads, so the first floating workspace of a session needs a reload.
    if mode == "floating":
        try:
            loaded = any(p.get("name") == "hyprbars" for p in json.loads(hypr("plugin", "list", "-j")))
        except Exception:
            loaded = True
        if not loaded:
            try:
                hypr("reload")
            except Exception:
                pass
    return True


FLOAT_TAG = "workspace-presets-floating"   # Windows this plugin floated (tiled again later).
FLOAT_WIDTH, FLOAT_HEIGHT = 0.6, 0.7       # A floated window's size, as part of the usable screen.
FLOAT_CASCADE = 32                         # Each further floated window: this much right and down.


def floating_geometry(monitor, index):
    """Size and position (layout pixels) for the index-th floated window on
    monitor (hyprctl monitors -j): centred, later ones cascading."""
    scale = monitor.get("scale") or 1
    left, top, right, bottom = (list(monitor.get("reserved") or []) + [0, 0, 0, 0])[:4]
    width = monitor["width"] / scale - left - right
    height = monitor["height"] / scale - top - bottom
    w, h = round(width * FLOAT_WIDTH), round(height * FLOAT_HEIGHT)
    step = FLOAT_CASCADE * (index % 6)
    x = monitor.get("x", 0) + left + round((width - w) / 2) - FLOAT_CASCADE * 2 + step
    y = monitor.get("y", 0) + top + round((height - h) / 2) - FLOAT_CASCADE * 2 + step
    return w, h, x, y


def floating_actions(ws_id, clients, monitors):
    """Float every tiled window on ws_id, tag it, and give it a desktop-like
    size and place. Already floating windows stay where they are."""
    actions = []
    already = sum(1 for c in clients if c.get("workspace", {}).get("id") == ws_id and c.get("floating"))
    by_id = {m.get("id"): m for m in monitors if isinstance(m, dict) and "width" in m and "height" in m}
    for index, c in enumerate(c for c in clients if c.get("workspace", {}).get("id") == ws_id and is_tiled(c)):
        addr = normalize_addr(c["address"])
        target = 'window="address:%s"' % addr
        actions.append(("eval", 'hl.dispatch(hl.dsp.window.float({%s, action="on"}))' % target))
        actions.append(("eval", 'hl.dispatch(hl.dsp.window.tag({%s, tag="+%s"}))' % (target, FLOAT_TAG)))
        monitor = by_id.get(c.get("monitor"))
        if monitor:
            w, h, x, y = floating_geometry(monitor, already + index)
            actions.append(("eval", 'hl.dispatch(hl.dsp.window.resize({%s, x=%d, y=%d, relative=false}))' % (target, w, h)))
            actions.append(("eval", 'hl.dispatch(hl.dsp.window.move({%s, x=%d, y=%d, relative=false}))' % (target, x, y)))
    return actions


def unfloat_actions(ws_id, clients):
    """Leaving the floating preset: tile again only the windows it floated."""
    actions = []
    for c in clients:
        if (c.get("workspace", {}).get("id") == ws_id and c.get("floating")
                and FLOAT_TAG in (c.get("tags") or [])):
            target = 'window="address:%s"' % normalize_addr(c["address"])
            actions.append(("eval", 'hl.dispatch(hl.dsp.window.float({%s, action="off"}))' % target))
            actions.append(("eval", 'hl.dispatch(hl.dsp.window.tag({%s, tag="-%s"}))' % (target, FLOAT_TAG)))
    return actions


def apply_layout_preset(ws_id, preset, dry_run=False, sync_mode=True):
    """Apply preset to ws_id. sync_mode=False (a window opening) never changes
    the workspace's Omarchy mode, so a Super + L choice is not undone."""
    if type(ws_id) is not int or ws_id <= 0:
        raise ValueError("invalid workspace id")
    preset = str(preset).lower()
    all_valid = {"side-by-side", "stacked", "master-left", "master-right", "columns", "grid", "none",
                 "floating", "scrolling"}
    if preset not in all_valid:
        raise ValueError(f"invalid preset: {preset}")

    clients = json.loads(hypr("clients", "-j"))

    if preset in FREE_PRESETS and sync_mode:
        # Omarchy floats or scrolls the workspace itself, new windows included.
        # Windows this plugin floated before Omarchy had modes are handed over.
        actions = unfloat_actions(ws_id, clients) if preset == "scrolling" else []
        if set_omarchy_mode(ws_id, preset, dry_run=dry_run):
            if not dry_run:
                for cmd, code in actions:
                    try:
                        hypr(cmd, code)
                    except Exception:
                        pass
            return {"status": "ok", "workspace": ws_id, "preset": preset, "actions": len(actions) + 1}
    if preset == "scrolling":
        # Without Omarchy workspace modes: Hyprland's own scrolling layout.
        if not dry_run:
            for cmd, code in unfloat_actions(ws_id, clients):
                try:
                    hypr(cmd, code)
                except Exception:
                    pass
            hypr("eval", 'hl.workspace_rule({ workspace = "%d", layout = "scrolling" })' % ws_id)
        return {"status": "ok", "workspace": ws_id, "preset": preset, "actions": 1}
    if sync_mode and omarchy_workspace_modes([ws_id]).get(ws_id) in FREE_PRESETS:
        # A tiling preset tiles a floating or scrolling workspace again.
        set_omarchy_mode(ws_id, "dwindle", dry_run=dry_run)
        if not dry_run:
            clients = json.loads(hypr("clients", "-j"))

    ws_clients = [c for c in clients if c.get("workspace", {}).get("id") == ws_id and is_tiled(c)]

    actions = []

    if preset == "floating":
        try:
            monitors = json.loads(hypr("monitors", "-j"))
        except Exception:
            monitors = []
        actions = floating_actions(ws_id, clients, monitors if isinstance(monitors, list) else [])
        if not dry_run:
            for cmd, code in actions:
                try:
                    hypr(cmd, code)
                except Exception:
                    pass
        return {"status": "ok", "workspace": ws_id, "preset": preset, "actions": len(actions)}
    actions += unfloat_actions(ws_id, clients)
    if actions:
        # The windows just tiled again take part in the layout below.
        floated = {c.get("address") for c in clients if FLOAT_TAG in (c.get("tags") or [])}
        ws_clients += [dict(c, floating=False) for c in clients
                       if c.get("address") in floated and c.get("workspace", {}).get("id") == ws_id
                       and c.get("mapped", False) and not c.get("fullscreen", 0) and not c.get("hidden", False)]

    try:
        settings = read_settings()
        ws_cap = settings.get("workspace_caps", {}).get(ws_id, settings.get("cap", 2))
        split = settings.get("workspace_splits", {}).get(ws_id, settings.get("default_split"))
    except Exception:
        ws_cap, split = 2, None
    split = _clean_split(split)

    # 1. Configure workspace rule for this workspace
    if preset == "none":
        rule_lua = f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "dwindle" }})'
    elif preset == "columns":
        # 4 or more windows (or cap=4 with <2 windows) need dwindle to arrange 4 side-by-side columns.
        # 3 windows use master layout with orientationcenter.
        if len(ws_clients) >= 4 or (len(ws_clients) < 2 and ws_cap == 4):
            rule_lua = f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "dwindle" }})'
        else:
            rule_lua = f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "master", layout_opts = {{ orientation = "center" }} }})'
    elif preset == "master-left":
        rule_lua = f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "master", layout_opts = {{ orientation = "left" }} }})'
    elif preset == "master-right":
        rule_lua = f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "master", layout_opts = {{ orientation = "right" }} }})'
    else:  # side-by-side, stacked, grid
        rule_lua = f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "dwindle" }})'
    actions.append(("eval", rule_lua))

    # 2. Live adjustments when windows exist on this workspace
    if ws_clients and preset != "none":
        try:
            active_info = json.loads(hypr("activeworkspace", "-j"))
            original_ws = active_info.get("id")
        except Exception:
            original_ws = None

        need_focus_switch = original_ws is not None and original_ws != ws_id
        if need_focus_switch:
            actions.append(("eval", f'hl.dispatch(hl.dsp.focus({{ workspace = {ws_id} }}))'))

        if len(ws_clients) == 2:
            if preset in ("side-by-side", "stacked", "columns"):
                actions.append(("eval", f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "dwindle" }})'))
                c1, c2 = ws_clients[0], ws_clients[1]
                dx = abs(c1.get("at", [0, 0])[0] - c2.get("at", [0, 0])[0])
                dy = abs(c1.get("at", [0, 0])[1] - c2.get("at", [0, 0])[1])
                is_side_by_side = dx > dy
                is_stacked = dy > dx

                if preset in ("side-by-side", "columns") and not is_side_by_side:
                    actions.append(("eval", 'hl.dispatch(hl.dsp.layout("togglesplit"))'))
                elif preset == "stacked" and not is_stacked:
                    actions.append(("eval", 'hl.dispatch(hl.dsp.layout("togglesplit"))'))
            elif preset == "master-left":
                actions.append(("eval", 'for _ = 1, 3 do pcall(function() hl.dispatch(hl.dsp.layout("removemaster")) end) end'))
                actions.append(("eval", 'hl.dispatch(hl.dsp.layout("orientationleft"))'))
            elif preset == "master-right":
                actions.append(("eval", 'for _ = 1, 3 do pcall(function() hl.dispatch(hl.dsp.layout("removemaster")) end) end'))
                actions.append(("eval", 'hl.dispatch(hl.dsp.layout("orientationright"))'))
        elif len(ws_clients) == 3:
            if preset == "columns":
                # Master in center with 1 master gives 3 side-by-side columns: Left | Center | Right
                actions.append(("eval", f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "master", layout_opts = {{ orientation = "center" }} }})'))
                actions.append(("eval", 'for _ = 1, 4 do pcall(function() hl.dispatch(hl.dsp.layout("removemaster")) end) end'))
                actions.append(("eval", 'hl.dispatch(hl.dsp.layout("orientationcenter"))'))
            elif preset == "master-left":
                actions.append(("eval", 'for _ = 1, 4 do pcall(function() hl.dispatch(hl.dsp.layout("removemaster")) end) end'))
                actions.append(("eval", 'hl.dispatch(hl.dsp.layout("orientationleft"))'))
            elif preset == "master-right":
                actions.append(("eval", 'for _ = 1, 4 do pcall(function() hl.dispatch(hl.dsp.layout("removemaster")) end) end'))
                actions.append(("eval", 'hl.dispatch(hl.dsp.layout("orientationright"))'))
            elif preset == "grid":
                actions.append(("eval", f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "dwindle" }})'))
        elif len(ws_clients) >= 4:
            if preset == "columns":
                # Dwindle layout with all horizontal splits produces 4 side-by-side columns
                actions.append(("eval", f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "dwindle" }})'))
                stacked = [c for c in ws_clients if c.get("size", [0, 0])[1] < 900]
                if stacked:
                    seen_x = set()
                    for s in stacked:
                        x = s.get("at", [0, 0])[0]
                        if not any(abs(x - prev_x) < 30 for prev_x in seen_x):
                            seen_x.add(x)
                            addr = normalize_addr(s.get("address")) if s.get("address") else ""
                            if addr:
                                actions.append(("eval", f'''(function()
                                    local w = hl.get_window("address:{addr}")
                                    if w then
                                        hl.dispatch(hl.dsp.focus({{ window = w }}))
                                        pcall(function() hl.dispatch(hl.dsp.layout("togglesplit")) end)
                                    end
                                end)()'''))
                else:
                    actions.append(("eval", 'pcall(function() hl.dispatch(hl.dsp.layout("togglesplit")) end)'))
            elif preset == "master-left":
                actions.append(("eval", 'for _ = 1, 4 do pcall(function() hl.dispatch(hl.dsp.layout("removemaster")) end) end'))
                actions.append(("eval", 'hl.dispatch(hl.dsp.layout("orientationleft"))'))
            elif preset == "master-right":
                actions.append(("eval", 'for _ = 1, 4 do pcall(function() hl.dispatch(hl.dsp.layout("removemaster")) end) end'))
                actions.append(("eval", 'hl.dispatch(hl.dsp.layout("orientationright"))'))
            elif preset == "grid":
                actions.append(("eval", f'hl.workspace_rule({{ workspace = "{ws_id}", layout = "dwindle" }})'))

        # The split dragged in the preview: the master's share, or the
        # left/top window's (dwindle's ratio 1 = even, so twice the share).
        # No split stored means even (as the preview draws it): sent too, so
        # "Even split" also takes back a size dragged earlier.
        if preset in ("master-left", "master-right") and len(ws_clients) >= 2:
            actions.append(("eval", 'hl.dispatch(hl.dsp.layout("mfact exact %.2f"))' % (split if split is not None else 0.5)))
        elif preset == "columns" and ws_cap == 3 and len(ws_clients) == 3:
            # Columns with 3 windows is a centred master: its share is the middle column's.
            actions.append(("eval", 'hl.dispatch(hl.dsp.layout("mfact exact %.2f"))' % (min(split, 0.6) if split is not None else 0.33)))
        elif preset in ("side-by-side", "stacked") and len(ws_clients) == 2:
            actions.append(("eval", 'hl.dispatch(hl.dsp.layout("splitratio %.2f exact"))' % (2 * (split if split is not None else 0.5))))

        if need_focus_switch:
            actions.append(("eval", f'hl.dispatch(hl.dsp.focus({{ workspace = {original_ws} }}))'))

    if not dry_run:
        for cmd, code in actions:
            try:
                hypr(cmd, code)
            except Exception:
                pass

        if len(ws_clients) >= 4 and preset == "columns":
            try:
                for _ in range(4):
                    time.sleep(0.08)
                    after_clients = json.loads(hypr("clients", "-j"))
                    still_stacked = [c for c in after_clients if c.get("workspace", {}).get("id") == ws_id and is_tiled(c) and c.get("size", [0, 0])[1] < 900]
                    if not still_stacked:
                        break
                    target = still_stacked[0]
                    addr = normalize_addr(target.get("address")) if target.get("address") else ""
                    if addr:
                        hypr("eval", f'''(function()
                            local w = hl.get_window("address:{addr}")
                            if w then
                                hl.dispatch(hl.dsp.focus({{ window = w }}))
                                pcall(function() hl.dispatch(hl.dsp.layout("togglesplit")) end)
                            end
                        end)()''')
                if need_focus_switch and original_ws is not None:
                    hypr("eval", f'hl.dispatch(hl.dsp.focus({{ workspace = {original_ws} }}))')
            except Exception:
                pass

    return {
        "status": "ok",
        "workspace": ws_id,
        "preset": preset,
        "actions": len(actions)
    }


def handle_open(address, dry_run=False):
    # All bar instances share this lock. Re-query after acquiring it so
    # simultaneous windows and multi-monitor event delivery cannot overfill.
    with runtime_lock():
        for attempt in range(4):
            settings = read_settings()
            if not settings["enabled"]:
                return {"status": "paused"}
            clients = json.loads(hypr("clients", "-j"))
            window = next((c for c in clients if c.get("address") == address), None)
            if window and window.get("mapped"):
                break
            if attempt < 3:
                time.sleep(0.1)

        source = None
        if window:
            source = window.get("workspace", {}).get("id")
            if settings.get("workspace_modes", {}).get(source) == "manual":
                return {"status": "manual", "workspace": source}

        workspaces = json.loads(hypr("workspaces", "-j"))
        # A workspace Omarchy floats or scrolls (Super + L) counts as that
        # preset: no window limit, no overflow, and no tiling preset on top.
        omarchy = omarchy_workspace_modes(sorted(({w.get("id") for w in workspaces} | {source})
                                                 - {None}))
        free = {ws: mode for ws, mode in omarchy.items() if mode in FREE_PRESETS}
        if free:
            settings = dict(settings, workspace_auto_presets={
                **settings.get("workspace_auto_presets", {}), **free})
        plan = plan_move(clients, workspaces, address, settings["cap"],
                         settings.get("workspace_caps"), settings.get("workspace_modes"),
                         settings.get("workspace_auto_presets"), settings.get("default_auto_presets"))
        if plan is None:
            # Window stays on source workspace; apply auto preset if configured
            if source and settings.get("workspace_modes", {}).get(source) != "manual":
                cap = settings.get("workspace_caps", {}).get(source, settings.get("cap", 2))
                preset = (settings.get("workspace_auto_presets", {}).get(source)
                          or settings.get("default_auto_presets", {}).get(str(cap)))
                if preset and preset != "none" and not (omarchy and preset in FREE_PRESETS):
                    try:
                        apply_layout_preset(source, preset, dry_run=dry_run, sync_mode=False)
                    except Exception:
                        pass
            return {"status": "unchanged"}
        if dry_run:
            return {"status": "planned", **plan}
        output = hypr("eval", move_expression(address, plan["target"], True))
        # hyprctl can exit successfully while reporting a Lua error.
        after = json.loads(hypr("clients", "-j"))
        moved = next((c for c in after if c.get("address") == address), None)
        if not moved or moved.get("workspace", {}).get("id") != plan["target"]:
            raise RuntimeError("move was not confirmed: " + output.strip())

        # Apply auto preset to target workspace if configured
        target_ws = plan["target"]
        if target_ws and settings.get("workspace_modes", {}).get(target_ws) != "manual":
            cap = settings.get("workspace_caps", {}).get(target_ws, settings.get("cap", 2))
            preset = (settings.get("workspace_auto_presets", {}).get(target_ws)
                      or settings.get("default_auto_presets", {}).get(str(cap)))
            if preset and preset != "none" and not (omarchy and preset in FREE_PRESETS):
                try:
                    apply_layout_preset(target_ws, preset, dry_run=dry_run, sync_mode=False)
                except Exception:
                    pass

        return {"status": "moved", **plan}


def is_manual_workspace(settings, ws_id):
    mode = settings.get("workspace_modes", {}).get(ws_id)
    if mode in ("auto", "manual"):
        return mode == "manual"
    layout = settings.get("workspace_layouts", {}).get(ws_id)
    return isinstance(layout, dict) and layout.get("mode") == "manual"


def mode_sync_plan(settings, omarchy_modes):
    """At start: the presets set in this plugin win. A Floating or Scrolling
    preset gives the workspace that Omarchy mode; a tiling preset or an App
    Preset tiles a workspace Omarchy floats or scrolls. Workspaces without a
    preset of their own (or Native) keep Omarchy's mode and show it."""
    plan = []
    presets = settings.get("workspace_auto_presets", {})
    for ws_id, mode in sorted(omarchy_modes.items()):
        if is_manual_workspace(settings, ws_id):
            if mode in FREE_PRESETS:
                plan.append((ws_id, "dwindle"))
            continue
        preset = presets.get(ws_id)
        if preset in FREE_PRESETS:
            if mode != preset:
                plan.append((ws_id, preset))
        elif preset not in (None, "none") and mode in FREE_PRESETS:
            plan.append((ws_id, preset))
    return plan


def sync_modes(start=False, dry_run=False):
    """Report every workspace's Omarchy mode; with start, also apply the plan."""
    if not omarchy_modes_supported():
        return {"status": "ok", "supported": False, "modes": {}, "applied": []}
    settings = read_settings()
    ws_ids = set(range(1, 11)) | set(settings.get("workspace_auto_presets", {}))
    try:
        for path in omarchy_modes_dir().glob("*.lua"):
            if path.stem.isdigit() and 0 < int(path.stem) < 10000:
                ws_ids.add(int(path.stem))
    except OSError:
        pass
    modes = omarchy_workspace_modes(sorted(ws_ids))
    applied = []
    if start and settings.get("enabled"):
        # Every bar (one per monitor) starts this; the first one does the work.
        with runtime_lock():
            modes = omarchy_workspace_modes(sorted(ws_ids))
            for ws_id, preset in mode_sync_plan(settings, modes):
                try:
                    if preset == "dwindle":
                        set_omarchy_mode(ws_id, "dwindle", dry_run=dry_run)
                    else:
                        apply_layout_preset(ws_id, preset, dry_run=dry_run)
                    applied.append({"workspace": ws_id, "preset": preset})
                except Exception:
                    pass
            if applied and not dry_run:
                modes = omarchy_workspace_modes(sorted(ws_ids))
    return {"status": "ok", "supported": True, "applied": applied,
            "modes": {str(ws): mode for ws, mode in modes.items()}}


def remove_all_presets(dry_run=False):
    try:
        workspaces = json.loads(hypr("workspaces", "-j"))
        ws_ids = sorted([w["id"] for w in workspaces if type(w.get("id")) is int and w["id"] > 0])
    except Exception:
        ws_ids = []
    if not ws_ids:
        ws_ids = list(range(1, 11))
    for wid in ws_ids:
        try:
            apply_layout_preset(wid, "none", dry_run=dry_run)
        except Exception:
            pass
    return {"status": "ok", "action": "remove_all_presets", "workspaces": ws_ids}


def reset_all_presets(dry_run=False):
    try:
        settings = read_settings()
        cap = settings.get("cap", 2)
        default_preset = settings.get("default_auto_presets", {}).get(str(cap), "side-by-side" if cap == 2 else "master-left" if cap == 3 else "grid")
    except Exception:
        default_preset = "side-by-side"
    try:
        workspaces = json.loads(hypr("workspaces", "-j"))
        ws_ids = sorted([w["id"] for w in workspaces if type(w.get("id")) is int and w["id"] > 0])
    except Exception:
        ws_ids = []
    if not ws_ids:
        ws_ids = list(range(1, 11))
    for wid in ws_ids:
        try:
            apply_layout_preset(wid, default_preset, dry_run=dry_run)
        except Exception:
            pass
    return {"status": "ok", "action": "reset_all_presets", "default_preset": default_preset, "workspaces": ws_ids}


def clear_app_preset(ws_id):
    if type(ws_id) is not int or ws_id <= 0:
        raise ValueError("invalid workspace id")
    with runtime_lock():
        store = load_store()
        found = False
        for key in ("workspaceLayouts", "workspaceModes"):
            if str(ws_id) in store.get(key, {}):
                del store[key][str(ws_id)]
                found = True
        if found:
            save_store(store)
        return {"status": "ok", "workspace": ws_id, "cleared": found}


def clear_all_app_presets():
    with runtime_lock():
        store = load_store()
        found = bool(store.get("workspaceLayouts")) or "manual" in store.get("workspaceModes", {}).values()
        store["workspaceLayouts"] = {}
        store["workspaceModes"] = {k: v for k, v in store.get("workspaceModes", {}).items() if v != "manual"}
        if found:
            save_store(store)
        return {"status": "ok", "cleared": "all"}


def swap_slots(ws_id, rects, first, second, dry_run=False):
    """Swap the live windows in two slots of a preview (rects in 0…1 of the
    workspace). Each slot gets the tiled window whose center is nearest."""
    if type(ws_id) is not int or ws_id <= 0:
        raise ValueError("invalid workspace id")
    if not (0 <= first < len(rects) and 0 <= second < len(rects)) or first == second:
        return {"status": "noop"}
    clients = [c for c in json.loads(hypr("clients", "-j"))
               if c.get("workspace", {}).get("id") == ws_id and c.get("mapped") and not c.get("floating")]
    if len(clients) < 2:
        return {"status": "noop"}
    left = min(c["at"][0] for c in clients)
    top = min(c["at"][1] for c in clients)
    width = max(1, max(c["at"][0] + c["size"][0] for c in clients) - left)
    height = max(1, max(c["at"][1] + c["size"][1] for c in clients) - top)

    def nearest(index):
        x, y, w, h = rects[index]
        cx, cy = x + w / 2, y + h / 2
        return min(clients, key=lambda c: ((c["at"][0] + c["size"][0] / 2 - left) / width - cx) ** 2 +
                   ((c["at"][1] + c["size"][1] / 2 - top) / height - cy) ** 2)
    a, b = nearest(first), nearest(second)
    if a is b:
        return {"status": "noop"}
    expression = 'hl.dispatch(hl.dsp.window.swap({window="address:%s", with="address:%s"}))' % (
        normalize_addr(a["address"]), normalize_addr(b["address"]))
    if not dry_run:
        hypr("eval", expression)
    return {"status": "ok", "swapped": [normalize_addr(a["address"]), normalize_addr(b["address"])]}


def without_commands(data):
    """Workspace settings from a backup file: saved terminal commands are
    dropped, so those apps start from their launcher (or not at all), never a
    command line nobody saved on this machine."""
    copy = json.loads(json.dumps(data))
    for layout in (copy.get("workspaceLayouts") or {}).values():
        if not isinstance(layout, dict):
            continue
        for app in layout.get("apps") or []:
            if isinstance(app, dict):
                app.pop("argv", None)
        for window in layout.get("windows") or []:
            if isinstance(window, dict) and isinstance(window.get("app"), dict):
                window["app"].pop("argv", None)
    return clean_store(copy)   # canonical again (an app left empty is dropped)


def snapshot():
    """The workspace presets and App Presets a theme preset keeps."""
    return clean_presets(load_store())


def default_preset_for(cap, defaults):
    """The popup's default per limit: a saved one, else the built-in one."""
    return defaults.get(str(cap)) or {3: "master-left", 4: "grid"}.get(cap, "side-by-side")


def apply_snapshot(data, restore_apps=True, conflict_action="move", dry_run=False):
    """Save a theme preset's snapshot as the workspace settings, then line the
    workspaces up with it: layouts everywhere, App Presets restored."""
    if conflict_action not in ("move", "close", "keep"):
        raise ValueError("invalid conflict action: %s" % conflict_action)
    incoming = clean_presets(data)
    with runtime_lock():
        store = load_store()
        # Every preset key as the snapshot has it: one it lacks goes back to
        # empty, so no App Preset or limit of the old state survives.
        # "active" and "autoMoveOnRestore" are switches, not part of a preset.
        before = {key: store.get(key) for key in STORE_KEYS}
        for key in STORE_KEYS:
            if key in incoming:
                store[key] = incoming[key]
            else:
                store.pop(key, None)
        changed = before != {key: store.get(key) for key in STORE_KEYS}
        if changed and not dry_run:
            save_store(store)
    result = {"status": "ok", "changed": changed, "layouts": [], "restored": []}
    settings = read_settings()
    try:
        workspaces = json.loads(hypr("workspaces", "-j"))
        ws_ids = {w["id"] for w in workspaces if type(w.get("id")) is int and w["id"] > 0}
    except Exception:
        ws_ids = set()
    manual = {ws for ws, mode in settings["workspace_modes"].items() if mode == "manual"}
    ws_ids |= set(settings["workspace_auto_presets"]) | manual
    # As the popup shows it: a workspace without a preset of its own keeps the
    # mode Omarchy gives it (Super + L) if that floats or scrolls.
    free = {ws for ws, mode in omarchy_workspace_modes(sorted(ws_ids)).items() if mode in FREE_PRESETS}
    for ws in sorted(ws_ids - manual):
        own = settings["workspace_auto_presets"].get(ws)
        if not own and ws in free:
            continue
        cap = settings["workspace_caps"].get(ws, settings["cap"])
        preset = own or default_preset_for(cap, settings["default_auto_presets"])
        try:
            outcome = apply_layout_preset(ws, preset, dry_run=dry_run)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            outcome = {"status": "error", "message": str(error)}
        result["layouts"].append({"workspace": ws, "preset": preset, "result": outcome.get("status")})
    if restore_apps:
        for ws in sorted(manual):
            if ws not in settings["workspace_layouts"]:
                continue
            try:
                outcome = restore_workspace(ws, dry_run, conflict_action)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                outcome = {"status": "error", "message": str(error)}
            result["restored"].append({"workspace": ws, "status": outcome.get("status"),
                                       "failed": len(outcome.get("failed", []) or [])})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", required=False, type=normalize_addr, help="handle window open event")
    parser.add_argument("--dry-run", action="store_true", help="report actions without dispatching")
    parser.add_argument("--capture-workspace", type=int, metavar="WS_ID", help="capture windows and apps for workspace")
    parser.add_argument("--restore-workspace", type=int, metavar="WS_ID", help="launch missing saved apps for workspace")
    parser.add_argument("--conflict-action", choices=["ask", "move", "close", "keep"], default="ask",
                        help="action to take if extra windows are found on workspace when restoring")
    parser.add_argument("--autostart", action="store_true", help="launch saved apps for all autostart workspaces")
    parser.add_argument("--apply-preset", nargs=2, metavar=("WS_ID", "PRESET"), help="apply layout preset to workspace")
    parser.add_argument("--remove-preset", type=int, metavar="WS_ID", help="remove layout preset from workspace")
    parser.add_argument("--remove-all-presets", action="store_true", help="remove layout presets from all workspaces (native layout)")
    parser.add_argument("--reset-all-presets", action="store_true", help="reset all workspaces to default layout preset")
    parser.add_argument("--clear-app-preset", type=int, metavar="WS_ID", help="remove saved app preset for workspace")
    parser.add_argument("--clear-all-app-presets", action="store_true", help="remove all saved app presets across all workspaces")
    parser.add_argument("--init-store", action="store_true",
                        help="take the Workspace Presets plugin's settings once, while it is on the bar")
    parser.add_argument("--sync-modes", choices=["read", "start"],
                        help="report Omarchy workspace modes; 'start' also lines them up with the presets")
    args = parser.parse_args(argv)

    try:
        if args.init_store:
            with runtime_lock():
                store = load_store()      # imports the plugin's entry if there is no file yet
                try:
                    mirror_wp_entry()     # at shell start: the copy follows the plugin's entry
                except (OSError, ValueError):
                    pass
            print(json.dumps({"status": "ok", "keys": len(store)}))
            return 0
        if args.sync_modes:
            print(json.dumps(sync_modes(args.sync_modes == "start", args.dry_run)))
            return 0
        if args.capture_workspace is not None:
            print(json.dumps(capture_workspace(args.capture_workspace)))
            return 0
        if args.restore_workspace is not None:
            print(json.dumps(restore_workspace(args.restore_workspace, args.dry_run, args.conflict_action)))
            return 0
        if args.autostart:
            print(json.dumps(autostart_all(args.dry_run)))
            return 0
        if args.remove_preset is not None:
            print(json.dumps(apply_layout_preset(args.remove_preset, "none", args.dry_run)))
            return 0
        if args.remove_all_presets:
            print(json.dumps(remove_all_presets(args.dry_run)))
            return 0
        if args.reset_all_presets:
            print(json.dumps(reset_all_presets(args.dry_run)))
            return 0
        if args.clear_app_preset is not None:
            print(json.dumps(clear_app_preset(args.clear_app_preset)))
            return 0
        if args.clear_all_app_presets:
            print(json.dumps(clear_all_app_presets()))
            return 0
        if args.apply_preset:
            ws_id = int(args.apply_preset[0])
            preset = args.apply_preset[1]
            print(json.dumps(apply_layout_preset(ws_id, preset, args.dry_run)))
            return 0
        if args.window:
            print(json.dumps(handle_open(args.window, args.dry_run)))
            return 0
        parser.print_help()
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "error", "message": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
