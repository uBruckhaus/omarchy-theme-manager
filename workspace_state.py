"""What the Workspaces and Apps tabs show and change, moved from the
Workspace Presets popup (BarWidget.qml) so it can be tested without GTK.

Every change works on the workspaces.json data (workspace_tiling.load_store)
and returns what to run on the desktop afterwards:
("apply", ws, preset), ("remove", ws), ("remove_all",), ("reset_all",) or None.
"""
from __future__ import annotations

import time

FREE = ("floating", "scrolling")
TILING_BY_CAP = {2: ("side-by-side", "stacked"),
                 3: ("columns", "master-left", "master-right"),
                 4: ("columns", "master-left", "master-right", "grid")}
BUILTIN_DEFAULT = {2: "side-by-side", 3: "master-left", 4: "grid"}
SHOWN_WORKSPACES = range(1, 11)


def _map(store, key):
    value = store.get(key)
    if not isinstance(value, dict):
        value = {}
        store[key] = value
    return value


def global_cap(store) -> int:
    cap = store.get("maxWindows", 2)
    return cap if type(cap) is int and 1 <= cap <= 4 else 2


def has_custom_cap(store, ws) -> bool:
    value = (store.get("workspaceCaps") or {}).get(str(ws))
    return type(value) is int and 1 <= value <= 4


def layout_for(store, ws):
    return (store.get("workspaceLayouts") or {}).get(str(ws))


def is_manual(store, ws) -> bool:
    mode = (store.get("workspaceModes") or {}).get(str(ws))
    if mode in ("manual", "auto"):
        return mode == "manual"
    layout = layout_for(store, ws)
    return bool(layout and layout.get("mode") == "manual")


def saved_windows(store, ws) -> list:
    layout = layout_for(store, ws)
    return list(layout.get("windows") or []) if layout else []


def saved_apps(store, ws) -> list:
    layout = layout_for(store, ws)
    if not layout or layout.get("saveApps") is False:
        return []
    return list(layout.get("apps") or [])


def has_saved_layout(store, ws) -> bool:
    layout = layout_for(store, ws)
    return bool(layout and (layout.get("windows") or layout.get("apps")))


def save_apps_on(store, ws) -> bool:
    layout = layout_for(store, ws)
    return layout.get("saveApps") is not False if layout else True


def autostart_on(store, ws) -> bool:
    layout = layout_for(store, ws)
    return layout.get("autostart") is True if layout else False      # only an explicit on (off for new ones)


def effective_cap(store, ws) -> int:
    if ws <= 0:
        return global_cap(store)
    if has_custom_cap(store, ws):
        return store["workspaceCaps"][str(ws)]
    if is_manual(store, ws):
        count = len(saved_windows(store, ws))
        if 1 <= count <= 4:
            return count
    return global_cap(store)


def default_preset_for(store, cap) -> str:
    saved = (store.get("defaultAutoPresets") or {}).get(str(cap))
    return saved or BUILTIN_DEFAULT.get(cap, "side-by-side")


def own_preset(store, ws):
    return (store.get("workspaceAutoPresets") or {}).get(str(ws))


def has_custom_preset(store, ws) -> bool:
    return not is_manual(store, ws) and own_preset(store, ws) is not None


def omarchy_free_mode(modes, ws) -> str:
    mode = (modes or {}).get(str(ws)) or (modes or {}).get(ws)
    return mode if mode in FREE else ""


def preset_for(store, ws, modes=None) -> str:
    """The layout a workspace shows: its own, else Omarchy's floating or
    scrolling mode (Super + L), else the default for its limit."""
    if is_manual(store, ws):
        return ""
    own = own_preset(store, ws)
    if own and own != "none":
        return own
    if omarchy_free_mode(modes, ws):
        return omarchy_free_mode(modes, ws)
    if own:
        return own
    return default_preset_for(store, effective_cap(store, ws))


def mode_from_omarchy(store, ws, modes=None) -> bool:
    own = own_preset(store, ws)
    return not is_manual(store, ws) and bool(omarchy_free_mode(modes, ws)) and own in (None, "none")


def is_native(store, ws, modes=None) -> bool:
    return ws > 0 and preset_for(store, ws, modes) == "none"


def is_free(store, ws, modes=None) -> bool:
    return preset_for(store, ws, modes) in FREE


def workspace_list(store, existing=()) -> list[int]:
    found = set(SHOWN_WORKSPACES) | {ws for ws in existing if type(ws) is int and ws > 0}
    for key in ("workspaceCaps", "workspaceLayouts"):
        found |= {int(k) for k in (store.get(key) or {}) if str(k).isdigit() and int(k) > 0}
    return sorted(found)


def _to_auto(store, ws, keep_cap=True):
    """Leaving an App Preset: the workspace tiles again with the limit it showed."""
    if is_manual(store, ws):
        if keep_cap and not has_custom_cap(store, ws):
            _map(store, "workspaceCaps")[str(ws)] = effective_cap(store, ws)
        _map(store, "workspaceModes")[str(ws)] = "auto"
        return True
    return False


# --- Workspaces tab --------------------------------------------------------------------

def set_global_cap(store, cap):
    if type(cap) is int and 1 <= cap <= 4:
        store["maxWindows"] = cap
    return None


def set_default_preset(store, cap, preset):
    _map(store, "defaultAutoPresets")[str(cap)] = preset
    return None


def set_cap(store, ws, cap, modes=None):
    was_native = is_native(store, ws, modes) or is_free(store, ws, modes)
    was_free = bool(omarchy_free_mode(modes, ws)) and not is_manual(store, ws)
    was_manual = _to_auto(store, ws, keep_cap=False)
    _map(store, "workspaceCaps")[str(ws)] = cap
    if was_native:
        _map(store, "workspaceAutoPresets")[str(ws)] = default_preset_for(store, cap)
    if was_free:
        return ("apply", ws, default_preset_for(store, cap))
    if was_manual or was_native:
        preset = preset_for(store, ws, modes) or default_preset_for(store, cap)
        if preset and preset != "none" and cap > 1:
            return ("apply", ws, preset)
    return None


def reset_cap(store, ws, modes=None):
    was_manual = _to_auto(store, ws, keep_cap=False)
    _map(store, "workspaceCaps").pop(str(ws), None)
    if was_manual:
        preset = preset_for(store, ws, modes) or default_preset_for(store, global_cap(store))
        if preset and preset != "none" and global_cap(store) > 1:
            return ("apply", ws, preset)
    return None


SPLIT_PRESETS = ("master-left", "master-right", "side-by-side", "stacked", "columns")
SPLIT_MIN, SPLIT_MAX = 0.2, 0.8
COLUMNS_SPLIT_MAX = 0.6   # Columns (3 windows): the centre column's share, the outer two split the rest


def split_range(preset):
    """(lowest, highest) split a layout can take."""
    return (SPLIT_MIN, COLUMNS_SPLIT_MAX) if preset == "columns" else (SPLIT_MIN, SPLIT_MAX)


def has_divider(preset, cap) -> bool:
    """A split Hyprland reproduces: the master share (Master Left/Right, and
    Columns with 3 windows = a centred master) or two windows' split."""
    if preset == "columns":
        return cap == 3
    return preset in SPLIT_PRESETS and cap >= 2


def split_for(store, ws):
    """The share of the master window (Master Left/Right) or of the left/top
    window (Side-by-Side, Stacked) in 0.2…0.8; None = even (Hyprland's own)."""
    own = (store.get("workspaceSplits") or {}).get(str(ws)) if ws > 0 else None
    value = own if own is not None else store.get("defaultSplit")
    return value if type(value) in (int, float) and SPLIT_MIN <= value <= SPLIT_MAX else None


def set_split(store, ws, value):
    """value None: back to even (or to the default for a workspace)."""
    if value is not None:
        low, high = split_range(preset_for(store, ws) if ws > 0 else default_preset_for(store, global_cap(store)))
        value = round(min(high, max(low, float(value))), 2)
    if ws <= 0:
        if value is None:
            store.pop("defaultSplit", None)
        else:
            store["defaultSplit"] = value
        return None
    if value is None:
        _map(store, "workspaceSplits").pop(str(ws), None)
    else:
        _map(store, "workspaceSplits")[str(ws)] = value
    preset = preset_for(store, ws)
    return ("apply", ws, preset) if preset in SPLIT_PRESETS and not is_manual(store, ws) else None


# --- adding a window in the preview ---------------------------------------------------------

def add_zone_preset(cap, u, v):
    """The layout a window added in the preview asks for, by where it was
    dropped (u, v in 0…1 of the windows' area). It only chooses the layout
    for the new limit; Hyprland still decides where the next window opens.
    2: nearer the top/bottom than the sides = Stacked, else Side-by-Side;
    3/4: the outer 30 % on the left/right = Master Left/Right, the middle =
    Columns, and with 4 a corner = 2×2 Grid."""
    u, v = min(1.0, max(0.0, u)), min(1.0, max(0.0, v))
    if cap <= 1:
        return None
    if cap == 2:
        return "stacked" if min(v, 1 - v) < min(u, 1 - u) else "side-by-side"
    side = u < 0.3 or u > 0.7
    if cap >= 4 and side and (v < 0.3 or v > 0.7):
        return "grid"
    if u < 0.3:
        return "master-left"
    if u > 0.7:
        return "master-right"
    return "columns"


def tiles_freely(store, ws, modes=None) -> bool:
    """Native, Floating or Scrolling: no layout preset arranges it."""
    return ws > 0 and not is_manual(store, ws) and (is_native(store, ws, modes) or is_free(store, ws, modes))


def added_cap(store, ws, modes=None):
    """The window limit + gives: one more, or on a Native, Floating or
    Scrolling workspace tiling with 2 windows - that is what + means there
    ("Tile with 2 windows"). None when nothing fits."""
    if ws > 0 and is_manual(store, ws):
        return None
    cap = global_cap(store) if ws <= 0 else effective_cap(store, ws)
    if tiles_freely(store, ws, modes):
        return 2
    return cap + 1 if cap < 4 else None


def can_add_window(store, ws, modes=None) -> bool:
    """+ does something (an App Preset adds apps instead)."""
    return added_cap(store, ws, modes) is not None


def add_window(store, ws, preset=None, modes=None):
    """The window limit + gives (added_cap), with the layout chosen by the
    drop (or the one the workspace would get anyway)."""
    cap = added_cap(store, ws, modes)
    if cap is None:
        return None
    if preset is not None and preset not in TILING_BY_CAP.get(cap, ()):
        preset = None
    if ws <= 0:
        set_global_cap(store, cap)
        if preset:
            set_default_preset(store, cap, preset)
        return None
    own = own_preset(store, ws)
    action = set_cap(store, ws, cap, modes)
    if preset:
        return set_preset(store, ws, preset)
    if own and own not in ("none",) + FREE and own not in TILING_BY_CAP.get(cap, ()):
        # Its own layout has no version for the new limit (Side-by-Side with
        # 3): the limit's default instead. A workspace following the default stays so.
        return set_preset(store, ws, default_preset_for(store, cap))
    return action


def removed_cap(store, ws, modes=None):
    """The window limit − gives: one fewer; with one window left the default
    limit again (a workspace's own limit dropped; All Workspaces back to 2).
    None when − does nothing (an App Preset, Native/Floating/Scrolling)."""
    if ws > 0 and (is_manual(store, ws) or tiles_freely(store, ws, modes)):
        return None
    if ws <= 0:
        cap = global_cap(store)
        if cap > 1:
            return cap - 1
        return 2 if "maxWindows" in store else None
    cap = effective_cap(store, ws)
    if cap > 1:
        return cap - 1
    if not has_custom_cap(store, ws):
        return None
    return global_cap(store)


def can_remove_window(store, ws, modes=None) -> bool:
    return removed_cap(store, ws, modes) is not None


def remove_window(store, ws, modes=None):
    """− in the preview (and the − key): one window fewer, or with one
    window left the default limit again."""
    new = removed_cap(store, ws, modes)
    if new is None:
        return None
    if ws <= 0:
        if global_cap(store) > 1:
            return set_global_cap(store, new)
        store.pop("maxWindows", None)
        return None
    if effective_cap(store, ws) > 1:
        return set_cap(store, ws, new, modes)
    own = own_preset(store, ws)
    reset_cap(store, ws, modes)
    new = effective_cap(store, ws)
    if own and own not in ("none",) + FREE and own not in TILING_BY_CAP.get(new, ()):
        return set_preset(store, ws, default_preset_for(store, new))   # no version for the default limit
    preset = preset_for(store, ws, modes)
    return ("apply", ws, preset) if preset and preset != "none" and new > 1 else None


def set_preset(store, ws, preset):
    _map(store, "workspaceAutoPresets")[str(ws)] = preset
    _to_auto(store, ws)
    return ("remove", ws) if preset == "none" else ("apply", ws, preset)


def remove_preset(store, ws):
    _map(store, "workspaceAutoPresets").pop(str(ws), None)
    _to_auto(store, ws)
    return ("apply", ws, default_preset_for(store, effective_cap(store, ws)))


def preset_selected(store, ws, preset, modes=None) -> bool:
    if has_custom_preset(store, ws):
        return own_preset(store, ws) == preset
    return mode_from_omarchy(store, ws, modes) and omarchy_free_mode(modes, ws) == preset


def toggle_preset(store, ws, preset, modes=None):
    """A second click on the chosen layout goes back to the default."""
    if not is_manual(store, ws) and preset_selected(store, ws, preset, modes):
        return remove_preset(store, ws)
    return set_preset(store, ws, preset)


def _keep_app_preset_off(store, ws):
    """A reset keeps a saved App Preset but leaves it unused: the workspace
    tiles again and the preset no longer starts at login (until chosen again)."""
    if layout_for(store, ws):
        _map(store, "workspaceModes")[str(ws)] = "auto"
    else:
        _map(store, "workspaceModes").pop(str(ws), None)


def reset_workspace(store, ws):
    for key in ("workspaceCaps", "workspaceAutoPresets"):
        _map(store, key).pop(str(ws), None)
    _keep_app_preset_off(store, ws)
    preset = default_preset_for(store, global_cap(store))
    if preset != "none" and global_cap(store) > 1:
        return ("apply", ws, preset)
    return None


def remove_all_presets(store):
    store["workspaceAutoPresets"] = {str(ws): "none" for ws in SHOWN_WORKSPACES}
    return ("remove_all",)


def reset_all(store):
    store["workspaceCaps"] = {}
    store["workspaceAutoPresets"] = {}
    modes = {k: v for k, v in (store.get("workspaceModes") or {}).items() if v != "manual"}
    store["workspaceModes"] = modes
    for ws in list(store.get("workspaceLayouts") or {}):
        modes[ws] = "auto"
    return ("reset_all",)


def clear_everything(store):
    for key in ("workspaceCaps", "workspaceAutoPresets", "workspaceModes", "workspaceLayouts"):
        store[key] = {}
    store["defaultAutoPresets"] = {str(cap): "none" for cap in (1, 2, 3, 4)}
    return ("remove_all",)


# --- Apps tab ----------------------------------------------------------------------------

def save_capture(store, ws, capture):
    """A capture (workspace_tiling.capture_workspace) becomes the App Preset."""
    existing = layout_for(store, ws) or {}
    _map(store, "workspaceLayouts")[str(ws)] = {
        "mode": "manual",
        "saveApps": existing.get("saveApps", True) is not False,
        "autostart": existing.get("autostart") is True,     # new App Presets: off until switched on
        "apps": capture.get("apps") or [],
        "windows": capture.get("windows") or [],
        "count": capture.get("count") or 0,
        "savedAt": int(time.time() * 1000),
    }
    _map(store, "workspaceModes")[str(ws)] = "manual"
    return None


def use_app_preset(store, ws):
    """A saved App Preset in use again (manual layout, autostart as set)."""
    if layout_for(store, ws):
        _map(store, "workspaceModes")[str(ws)] = "manual"
        return ("tile", ws)   # a floating or scrolling Omarchy mode would stay otherwise
    return None


def remove_app_preset(store, ws):
    _map(store, "workspaceLayouts").pop(str(ws), None)
    _map(store, "workspaceModes").pop(str(ws), None)
    preset = own_preset(store, ws) or default_preset_for(store, effective_cap(store, ws))
    return ("apply", ws, preset or "none")


def remove_all_app_presets(store):
    """Every workspace that used an App Preset tiles again with its layout."""
    ids = set(store.get("workspaceModes") or {}) | set(store.get("workspaceLayouts") or {})
    manual = sorted(int(k) for k in ids if str(k).isdigit() and is_manual(store, int(k)))
    store["workspaceLayouts"] = {}
    store["workspaceModes"] = {k: v for k, v in (store.get("workspaceModes") or {}).items() if v != "manual"}
    # "none" too: Native also needs its desktop update (back to dwindle).
    plan = [(ws, preset_for(store, ws) or "none") for ws in manual]
    return ("apply_many", plan) if plan else None


def _matches(window, app) -> bool:
    saved = window.get("app") or window
    window_class = str(window.get("class") or saved.get("class") or "").lower()
    app_class = str(app.get("class") or "").lower()
    if window_class and app_class:
        return window_class == app_class
    for key in ("desktop_id", "desktop_path"):
        if saved.get(key) and app.get(key):
            return saved[key] == app[key]
    return False


def remove_app(store, ws, index):
    """Drop one app and its restore slots (LayoutState.removeApp)."""
    layout = layout_for(store, ws)
    apps = list((layout or {}).get("apps") or [])
    if not 0 <= index < len(apps):
        return None
    removed = apps.pop(index)
    updated = dict(layout, apps=apps)
    if isinstance(layout.get("windows"), list):
        updated["windows"] = [w for w in layout["windows"] if not _matches(w, removed)]
        updated["count"] = len(updated["windows"])
    else:
        updated["count"] = len(apps)
    if updated["count"] == 0:
        updated["autostart"] = False
    store["workspaceLayouts"][str(ws)] = updated
    return None


def toggle_layout_flag(store, ws, key):
    layout = dict(layout_for(store, ws) or {})
    current = save_apps_on(store, ws) if key == "saveApps" else autostart_on(store, ws)
    layout[key] = not current
    _map(store, "workspaceLayouts")[str(ws)] = layout
    return None


# --- a theme preset's snapshot -------------------------------------------------------------

def app_preset_workspaces(snapshot) -> list[int]:
    modes = snapshot.get("workspaceModes") or {}
    layouts = snapshot.get("workspaceLayouts") or {}
    return sorted(int(ws) for ws, mode in modes.items()
                  if mode == "manual" and ws in layouts and str(ws).isdigit())


def snapshot_counts(snapshot) -> tuple[int, int]:
    """(workspaces with a layout preset of their own, App Presets)."""
    apps = app_preset_workspaces(snapshot)
    own = [ws for ws in (snapshot.get("workspaceAutoPresets") or {})
           if str(ws).isdigit() and int(ws) not in apps]
    return len(own), len(apps)


def swap_saved(store, ws, first, second):
    """App Preset: two saved windows trade places (their position and size)."""
    layout = layout_for(store, ws)
    windows = list((layout or {}).get("windows") or [])
    if not (0 <= first < len(windows) and 0 <= second < len(windows)) or first == second:
        return None
    a, b = dict(windows[first]), dict(windows[second])
    for key in ("at", "size"):
        a[key], b[key] = windows[second].get(key), windows[first].get(key)
    windows[first], windows[second] = a, b
    store["workspaceLayouts"][str(ws)] = dict(layout, windows=windows)
    return None


# --- App Preset geometry: resize and add in the preview ------------------------------------
#
# Saved windows keep their real position and size (px), which "Restore App
# Preset" applies exactly. Tiled windows are only resized along an edge two of
# them share (both sides change, as in Hyprland); a floating one by its corner.

MIN_SHARE = 0.1          # no saved window gets narrower/lower than 10 % of the preset's extent


def _geometry(window):
    at, size = window.get("at"), window.get("size")
    if not (isinstance(at, list) and isinstance(size, list) and len(at) == 2 and len(size) == 2):
        return None
    if any(type(v) not in (int, float) for v in at + size) or min(size) <= 0:
        return None
    return at, size


def saved_bounds(windows):
    """(x, y, width, height) around the saved windows, or None."""
    boxes = [g for g in map(_geometry, windows) if g]
    if not boxes:
        return None
    x0, y0 = min(a[0] for a, _s in boxes), min(a[1] for a, _s in boxes)
    x1 = max(a[0] + s[0] for a, s in boxes)
    y1 = max(a[1] + s[1] for a, s in boxes)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def saved_cuts(windows, max_gap=None):
    """Edges two tiled saved windows share: (axis, edge, start), axis 0 = a
    vertical line (x), 1 = a horizontal one (y); edge is where the first
    window ends, start where the next begins (the gap between)."""
    bounds = saved_bounds(windows)
    if bounds is None:
        return []
    tiled = [g for w in windows if not w.get("floating") for g in [_geometry(w)] if g]
    cuts = set()
    for axis in (0, 1):
        other = 1 - axis
        gap = max_gap if max_gap is not None else max(40, 0.03 * bounds[2 + axis])
        for a_at, a_size in tiled:
            end = a_at[axis] + a_size[axis]
            for b_at, b_size in tiled:
                if not 0 <= b_at[axis] - end <= gap:
                    continue
                if min(a_at[other] + a_size[other], b_at[other] + b_size[other]) <= max(a_at[other], b_at[other]):
                    continue          # side by side on this axis, but not facing each other
                cuts.add((axis, end, b_at[axis]))
    return sorted(cuts)


def _overlap(a, b):
    (a_at, a_size), (b_at, b_size) = a, b
    return all(min(a_at[i] + a_size[i], b_at[i] + b_size[i]) > max(a_at[i], b_at[i]) for i in (0, 1))


def move_saved_cut(windows, axis, edge, start, middle):
    """The shared edge moved so the gap's middle is at `middle` (px). Every
    tiled window ending at `edge` grows or shrinks, every one starting at
    `start` moves with it. None if a window would get too small or two would
    overlap."""
    bounds = saved_bounds(windows)
    if bounds is None:
        return None
    delta = round(middle - (edge + start) / 2)
    least = MIN_SHARE * bounds[2 + axis]
    out = []
    for window in windows:
        geometry = _geometry(window)
        if geometry is None or window.get("floating"):
            out.append(window)
            continue
        at, size = list(geometry[0]), list(geometry[1])
        if abs(at[axis] + size[axis] - edge) <= 1:
            size[axis] += delta
        elif abs(at[axis] - start) <= 1:
            at[axis] += delta
            size[axis] -= delta
        else:
            out.append(window)          # not on this edge: unchanged, whatever its size
            continue
        if size[axis] < least:
            return None
        out.append(dict(window, at=at, size=size))
    tiled = [_geometry(w) for w in out if not w.get("floating") and _geometry(w)]
    if any(_overlap(tiled[i], tiled[j]) for i in range(len(tiled)) for j in range(i + 1, len(tiled))):
        return None
    return out


def resize_saved_floating(windows, index, width, height):
    """A saved floating window's size (its top left corner stays), at least
    10 % of the preset's extent each way."""
    if not 0 <= index < len(windows) or not windows[index].get("floating"):
        return None
    geometry, bounds = _geometry(windows[index]), saved_bounds(windows)
    if geometry is None or bounds is None:
        return None
    size = [max(round(width), round(MIN_SHARE * bounds[2])), max(round(height), round(MIN_SHARE * bounds[3]))]
    out = list(windows)
    out[index] = dict(windows[index], at=list(geometry[0]), size=size)
    return out


def set_saved_windows(store, ws, windows):
    """The App Preset's windows as changed in the preview (Restore applies them)."""
    layout = layout_for(store, ws)
    if not layout or not isinstance(windows, list):
        return None
    store["workspaceLayouts"][str(ws)] = dict(layout, windows=windows, count=len(windows))
    return None


def split_target(windows, index=None):
    """Which tiled saved window an added app halves: the one it was dropped
    on, else the largest."""
    tiled = [i for i, w in enumerate(windows) if not w.get("floating") and _geometry(w)]
    if index in tiled:
        return index
    if not tiled:
        return None
    return max(tiled, key=lambda i: windows[i]["size"][0] * windows[i]["size"][1])


def add_saved_app(store, ws, app, index=None, gap=10):
    """An app from the launchers joins the App Preset: the chosen (or the
    largest) tiled window is halved along its longer side and the app gets
    the second half. Saved only - Restore starts it."""
    layout = layout_for(store, ws)
    if not layout or not isinstance(app, dict) or not (app.get("desktop_id") or app.get("class")):
        return None
    windows = [dict(w) for w in layout.get("windows") or []]
    if len(windows) >= 8:
        return None
    # The window class Restore matches it by: the launcher's StartupWMClass, else its id.
    window_class = str(app.get("class") or app.get("startup_class") or app.get("desktop_id"))
    record = {k: app[k] for k in ("name", "icon", "desktop_id", "desktop_path") if app.get(k)}
    record["class"] = window_class
    target = split_target(windows, index)
    if target is None:
        bounds = saved_bounds(windows) or (0, 0, 1920, 1080)
        at, size = [bounds[0], bounds[1]], [bounds[2], bounds[3]]
        new_at, new_size = list(at), list(size)
    else:
        at, size = list(windows[target]["at"]), list(windows[target]["size"])
        axis = 0 if size[0] >= size[1] else 1
        first = (size[axis] - gap) // 2
        if first < 1:
            return None
        new_at, new_size = list(at), list(size)
        new_at[axis] = at[axis] + first + gap
        new_size[axis] = size[axis] - first - gap
        size[axis] = first
        windows[target] = dict(windows[target], at=at, size=size)
    windows.append({"class": window_class, "at": new_at, "size": new_size, "floating": False, "app": dict(record)})
    apps = list(layout.get("apps") or [])
    key = record.get("desktop_id") or record.get("class")
    if not any((a.get("desktop_id") or a.get("class")) == key for a in apps):
        apps.append(record)
    store["workspaceLayouts"][str(ws)] = dict(layout, apps=apps, windows=windows, count=len(windows))
    return None


# --- Undo of a preview change ----------------------------------------------------------------

_GONE = object()


def undo_merge(current, before, after):
    """Take back one change (before -> after) in `current`, keeping what
    changed since (e.g. in the Workspace Presets plugin, which shares these
    settings): a key (or one workspace's entry of a per-workspace map) goes
    back only if it is still as the change left it. True if anything went back."""
    changed = False

    def put(target, key, value):
        if value is _GONE:
            target.pop(key, None)
        else:
            target[key] = json_copy(value)

    for key in set(before) | set(after):
        was, now, there = before.get(key, _GONE), after.get(key, _GONE), current.get(key, _GONE)
        if was == now:
            continue
        # A per-workspace map the change created or removed (saved settings
        # leave empty maps out) is merged entry by entry like any other.
        was_map = {} if was is _GONE and isinstance(now, dict) else was
        now_map = {} if now is _GONE and isinstance(was, dict) else now
        if isinstance(now_map, dict) and isinstance(was_map, dict) and isinstance(there, dict):
            for sub in set(was_map) | set(now_map):
                sub_was, sub_now = was_map.get(sub, _GONE), now_map.get(sub, _GONE)
                if sub_was != sub_now and there.get(sub, _GONE) == sub_now:
                    put(there, sub, sub_was)
                    changed = True
            if was is _GONE and not there:
                current.pop(key, None)
            continue
        if now is _GONE and isinstance(was, dict) and there is _GONE:
            current[key] = json_copy(was)
            changed = True
            continue
        if there == now:
            put(current, key, was)
            changed = True
    return changed


def json_copy(value):
    import json
    return json.loads(json.dumps(value))
