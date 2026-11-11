#!/usr/bin/env python3
"""Theme Manager popup: a Wayland layer-shell window over every Omarchy
theme (user and system).

List themes, apply one, hide it from the list (eye toggle unhides), delete
user themes, and manage each theme's wallpapers: add images by drag & drop or
by pasting an image copied from the web, remove the ones no longer wanted.
All file work goes through theme_core, which enforces the trust rules.
"""

import colorsys
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

# GTK4 layer shell must load before GTK's Wayland client library. The Python
# bindings load GTK first, so preload the layer-shell library on re-exec.
layer_shell_library = "libgtk4-layer-shell.so.0"
if layer_shell_library not in os.environ.get("LD_PRELOAD", "").split(":"):
    os.environ["LD_PRELOAD"] = ":".join(filter(None, (
        layer_shell_library, os.environ.get("LD_PRELOAD", ""))))
    os.execv(sys.executable, [sys.executable, *sys.argv])

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Gtk4LayerShell, Pango

# Once Gtk4LayerShell is loaded into this process by ld.so, remove it from
# LD_PRELOAD so child processes do not inherit it.
_preloads = [p for p in os.environ.get("LD_PRELOAD", "").split(":")
             if p and p != layer_shell_library]
if _preloads:
    os.environ["LD_PRELOAD"] = ":".join(_preloads)
else:
    os.environ.pop("LD_PRELOAD", None)

import atexit

import corners
import more_catalog
import more_plugins
import theme_auto
import theme_catalog
import theme_core
import theme_custom
import theme_looks
import theme_shortcuts as shortcuts
from layer_watch import LayerWatch
import wallpaper_search
import web_images
from palette import ThemePalette
import pidfile
import i18n
import screenshots
import theme_presets
import theme_health
import theme_backup
import header_order
import workspace_state
import theme_ai
import workspaces_page
from workspaces_page import WorkspacesPageMixin, clear, ws_wrap
import ask_page
from ask_page import AskPageMixin
from i18n import _, N_, ngettext

# Default outer size: a 420 px card around a 784 px list (Uwe's choice,
# 2026-11-06: room for the workspace cards and their controls), the same on every page so switching pages never resizes the popup.
# The grip in the bottom-right corner resizes it; the grids then show more
# columns (the size is remembered in settings.json).
CARD_WIDTH = 420
LIST_HEIGHT = 784
TILE_STEP = 196        # one grid column: tile, its padding and border, the spacing
UNDO_SECONDS = 10      # how long Undo is offered after a delete
PREVIEW_SECONDS = 5    # a preview started from a menu
SCREEN_MARGIN = 120    # space kept free above and below the card
PREVIEW_W, PREVIEW_H = 192, 108   # theme previews are 16:9
TILE = 170             # the smallest tile (two fit the default card); wider popups get larger ones (tile_w)
TILE_TARGET = 320      # a wide popup aims at columns about this wide
MAX_COLUMNS = 4        # more width then makes the tiles larger, not more of them
WIDE_EDITOR_FROM = 900 # from this card width the theme editor uses two columns
SWITCHER_WRAPPER = Path(__file__).resolve().parent / "theme-switcher"
SYNC_SCRIPT = Path(__file__).resolve().parent / "switcher_sync.py"
PREVIEW_WORKERS = 4    # parallel preview downloads on the import page
PEEK_DELAY_MS = 70     # brief skim guard so sweeping fast across cards stays still
SELECT_PEEK_MS = 90    # brief settle guard so holding Arrow keys stays calm
PEEK_PROGRESS = 0.19   # ~15.6° gentle 3D edge lift hinting that a wallpaper waits behind
FLIP_DELAY_MS = 1000   # default 1 s from selecting a theme until automatic 3D rotation starts
FLIP_DURATION = 0.42   # seconds for a 180° 3D card flip
FLIP_STEP_DURATION = 0.30  # seconds for rotating between faces via arrow buttons
CURSOR_SLACK = 24  # px the pointer may move after opening before the keyboard is released
MAX_DROPS = 10  # one drop gesture adds at most this many wallpapers
MAX_DROP_LIST_BYTES = 1024 * 1024  # a dropped uri-list or text, as for the clipboard
DROP_LIST_TIMEOUT = 10  # seconds a drop source gets to deliver that list
# Non-letter keys offered on the shortcut page (GDK/Hyprland key names).
OTHER_KEYS = ("Return", "space", "BackSpace", "Delete", "Home", "End", "F1", "F2", "F3", "F4",
              "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12")
KEY_STYLES = {shortcuts.FREE: "sc-free-key", shortcuts.SAME: "sc-own-key", shortcuts.OURS: "sc-yours-key",
              shortcuts.CUSTOM: "sc-yours-key", shortcuts.OMARCHY: "sc-omarchy-key"}


def write_pid_file():
    try:
        pidfile.write()
    except OSError:
        pass


def cleanup_pid():
    try:
        pidfile.remove_if_own()
    except OSError:
        pass


def hyprland_rounding() -> int | None:
    """The windows' rounding (theme or looknfeel.lua), or None if unknown."""
    out, _ok = theme_core.read_bounded_output(["hyprctl", "getoption", "decoration:rounding", "-j"],
                                             max_bytes=4096, timeout=2.0)
    return corners.parse_rounding(out.decode("utf-8", "replace"))


def monitor_height() -> int:
    """Logical height of the smallest monitor, so the card always fits."""
    display = Gdk.Display.get_default()
    heights = []
    if display is not None:
        monitors = display.get_monitors()
        for i in range(monitors.get_n_items()):
            heights.append(monitors.get_item(i).get_geometry().height)
    return min(heights) if heights else 1080


class FixedPicture(Gtk.Picture):
    """A picture that always measures exactly width × height.

    A plain Gtk.Picture asks for its image's own size as natural size, so in a
    row narrower than that GTK hands it whatever width the other children
    leave over: two themes with different badges got different previews."""

    def __init__(self, width: int, height: int, paintable=None):
        super().__init__()
        self._size = (width, height)
        self.set_overflow(Gtk.Overflow.HIDDEN)     # the picture is clipped to its corners (Settings › Corners)
        if paintable is not None:
            self.set_paintable(paintable)
        self.set_content_fit(Gtk.ContentFit.COVER)
        self.set_can_shrink(True)
        self.set_hexpand(False)
        self.set_vexpand(False)

    def do_measure(self, orientation, _for_size):
        size = self._size[0] if orientation == Gtk.Orientation.HORIZONTAL else self._size[1]
        return size, size, -1, -1


class FlipPicture(FixedPicture):
    """A theme preview that rotates 180° in 3D on hover to reveal the theme's
    wallpapers, with left/right arrow controls when a theme has several."""

    def __init__(self, width: int, height: int, wallpapers=(),
                 back_path: str | Path | None = None,
                 active_bg: str | Path | None = None,
                 theme_current: bool = False,
                 pin_apply_theme: bool = True,
                 delay_s: float = theme_auto.DEFAULT_FLIP_DELAY,
                 decoder=None, radius_fn=None):
        super().__init__(width, height)
        paths = [str(p) for p in wallpapers] if wallpapers else ([str(back_path)] if back_path else [])
        self._wallpapers = paths
        self._theme_current = bool(theme_current)
        self._pin_apply_theme = bool(pin_apply_theme)
        track_pinned = self._theme_current or not self._pin_apply_theme
        self._active_name = Path(active_bg).name if (active_bg and track_pinned) else None
        self._delay_ms = max(0, round(float(delay_s) * 1000))
        self._promote_name: str | None = None
        self._decoder = decoder
        self._radius_fn = radius_fn
        self._badges: Gtk.Widget | None = None
        self._pin_button: Gtk.Button | None = None
        self._nav_buttons: tuple[Gtk.Button, ...] = ()
        self._shown_pin_state = None
        self._textures: dict[str, Gdk.Paintable] = {}
        self._requested: set[str] = set()
        self._gone = False                    # unrealized: pending decodes are dropped
        self.connect("unrealize", self._on_card_gone)
        self.connect("realize", lambda *_: setattr(self, "_gone", False))
        self._selected = False
        self._hovered = False
        self._interactive = False  # True once rotation or manual arrow mode is active
        self._nav_steady = False   # True once arrow controls stay up across preview + wallpapers
        self._from_face = -1   # -1 = front preview, 0..N-1 = wallpaper index
        self._to_face = 0
        self._direction = 1.0
        self._target = 0.0
        self._progress = 0.0
        self._queued: tuple[int, float] | None = None
        self._pulse_back = False
        self._peek_id = 0
        self._delay_id = 0
        self._tick_id = 0
        self._last_frame_us = 0
        self.connect("unmap", lambda *_args: self.reset_flip())

    @property
    def _back_path(self) -> str | None:
        if not self._wallpapers:
            return None
        face = self.visible_face()
        return self._wallpapers[face if 0 <= face < len(self._wallpapers) else 0]

    def set_badges(self, badges: Gtk.Widget | None):
        self._badges = badges

    def set_pin_button(self, button: Gtk.Button | None):
        self._pin_button = button
        self._shown_pin_state = None
        self._sync_overlays()

    def set_nav_buttons(self, *buttons: Gtk.Button):
        self._nav_buttons = tuple(b for b in buttons if b is not None)
        self._sync_overlays()

    def set_back_paintable(self, paintable):
        if self._wallpapers:
            self._set_wallpaper_paintable(self._wallpapers[0], paintable)

    # Decoded wallpapers kept per card: the faces around the one shown (older ones are dropped and
    # decoded again when they come back), so cycling never piles up textures.
    TEXTURE_WINDOW = 1

    def _keep_paths(self) -> set:
        n = len(self._wallpapers)
        keep = set()
        for face in (self._from_face, self._to_face):
            if 0 <= face < n:
                for step in range(-self.TEXTURE_WINDOW, self.TEXTURE_WINDOW + 1):
                    keep.add(self._wallpapers[(face + step) % n])
        if n and self._from_face < 0 and self._to_face < 0:
            keep.update({self._wallpapers[0], self._wallpapers[-1]})
        return keep

    def _prune_textures(self):
        keep = self._keep_paths()
        for path in [p for p in self._textures if p not in keep]:
            del self._textures[path]
            self._requested.discard(path)

    def _still_wanted(self, path: str) -> bool:
        """Asked by the decoder's worker before decoding: the card still shown and the wallpaper still
        near the face shown (a plain read of a few fields; a stale answer only decodes one more)."""
        if not self._gone and path in self._keep_paths():
            return True
        # Requested again if it comes back into the window (the set is changed on GTK's loop only).
        GLib.idle_add(lambda: self._requested.discard(path) and False)
        return False

    def _on_card_gone(self, *_args):
        self._gone = True

    def _wallpaper_failed(self, path: str):
        """A wallpaper that cannot be decoded shows the preview (or an empty face) instead, so a
        flip waiting for it goes on."""
        fallback = self.get_paintable() or Gdk.Paintable.new_empty(max(1, self._size[0]), max(1, self._size[1]))
        self._set_wallpaper_paintable(path, fallback)

    def _set_wallpaper_paintable(self, path: str, paintable):
        self._textures[path] = paintable
        self._prune_textures()
        if ((self._hovered or self._selected) and self._delay_id == 0 and self._target == 1.0
                and 0 <= self._to_face < len(self._wallpapers)
                and self._wallpapers[self._to_face] == path):
            self._animate_to(1.0)
        self.queue_draw()

    def _face_paintable(self, face: int):
        if face == -1:
            return self.get_paintable()
        if 0 <= face < len(self._wallpapers):
            return self._textures.get(self._wallpapers[face])
        return None

    def _request_index(self, index: int):
        if self._decoder is None or not (0 <= index < len(self._wallpapers)):
            return
        path = self._wallpapers[index]
        if path in self._requested:
            return
        self._requested.add(path)
        self._decoder.request(self, path, self._size[0], self._size[1],
                              setter=lambda tex, p=path: self._set_wallpaper_paintable(p, tex),
                              failed=lambda p=path: self._wallpaper_failed(p),
                              wanted=lambda p=path: self._still_wanted(p))

    def _prefetch_around(self, index: int):
        n = len(self._wallpapers)
        if n == 0:
            return
        if index < 0:
            self._request_index(0)
            if n > 1:
                self._request_index(n - 1)
            return
        self._request_index(index % n)
        if n > 1:
            self._request_index((index + 1) % n)
            self._request_index((index - 1) % n)

    def request_back(self):
        self._prefetch_around(0)

    def visible_face(self) -> int:
        eased = 0.5 - 0.5 * math.cos(math.pi * self._progress)
        return self._from_face if eased <= 0.5 else self._to_face

    def is_showing_wallpaper(self) -> bool:
        return self.visible_face() >= 0

    def current_wallpaper_path(self) -> str | None:
        face = self.visible_face()
        return self._wallpapers[face] if 0 <= face < len(self._wallpapers) else None

    def is_current_wallpaper_pinned(self) -> bool:
        path = self.current_wallpaper_path()
        return bool(path and self._active_name and Path(path).name == self._active_name)

    def _promote(self, name: str):
        idx = next((i for i, p in enumerate(self._wallpapers) if Path(p).name == name), -1)
        if idx > 0:
            self._wallpapers = self._wallpapers[idx:] + self._wallpapers[:idx]

    def set_active_wallpaper(self, bg: str | Path | None, theme_current: bool = True,
                             pin_apply_theme: bool | None = None,
                             promote_on_reset: bool = False):
        self._theme_current = bool(theme_current)
        if pin_apply_theme is not None:
            self._pin_apply_theme = bool(pin_apply_theme)
        track_pinned = self._theme_current or not self._pin_apply_theme
        self._active_name = Path(bg).name if (bg and track_pinned) else None
        if promote_on_reset and self._active_name:
            if self._from_face == -1 and self._progress <= 0.0:
                self._promote(self._active_name)
            else:
                self._promote_name = self._active_name
        self._shown_pin_state = None
        self._sync_overlays()
        self.queue_draw()

    def schedule_select_peek(self):
        """A single gentle 3D edge lift & settle when a card is selected without
        pointer hover, hinting that there is more behind the card."""
        if not self._wallpapers or self._hovered:
            return
        if self._from_face != -1 or self._progress > 0.0:
            return
        if self._peek_id:
            GLib.source_remove(self._peek_id)
        self._peek_id = GLib.timeout_add(SELECT_PEEK_MS, self._on_select_peek)

    def _on_select_peek(self):
        self._peek_id = 0
        if self._hovered or not self._wallpapers or self._from_face != -1 or self._progress > 0.0:
            return GLib.SOURCE_REMOVE
        self._from_face = -1
        self._to_face = 0
        self._direction = 1.0
        self._pulse_back = True
        self._animate_to(PEEK_PROGRESS)
        return GLib.SOURCE_REMOVE

    def cancel_peek(self):
        if self._hovered:
            return
        if self._peek_id:
            GLib.source_remove(self._peek_id)
            self._peek_id = 0
        self._pulse_back = False
        if self._from_face == -1 and self._progress > 0.0:
            self._animate_to(0.0)

    def _activate(self):
        if not self._wallpapers:
            return
        self._pulse_back = False
        self.request_back()
        if self._delay_ms <= 0:
            if self._peek_id:
                GLib.source_remove(self._peek_id)
                self._peek_id = 0
            if self._delay_id:
                GLib.source_remove(self._delay_id)
                self._delay_id = 0
            self._interactive = True
            self._nav_steady = True
            self._sync_overlays()
            self.queue_draw()
            return
        if self._interactive or self._peek_id or self._delay_id:
            return
        peek_ms = min(PEEK_DELAY_MS, self._delay_ms)
        rem_ms = max(40, self._delay_ms - peek_ms)
        if self._from_face == -1 and self._progress <= 0.0:
            self._peek_id = GLib.timeout_add(peek_ms, self._on_hover_peek)
        elif self._from_face == -1 and self._to_face >= 0:
            self._queued = None
            if self._progress <= PEEK_PROGRESS + 0.02:
                self._animate_to(PEEK_PROGRESS)
                if self._delay_id == 0:
                    self._delay_id = GLib.timeout_add(rem_ms, self._on_delay_done)
            else:
                self._interactive = True
                self._animate_to(1.0)
        elif self._to_face == -1:
            self._interactive = True
            if self._progress < 0.5:
                self._queued = None
                self._animate_to(0.0)
            else:
                self._queued = (0, 1.0)
                self._animate_to(1.0)

    def _close_to_preview(self):
        self._interactive = False
        self._nav_steady = False
        self._pulse_back = False
        if self._peek_id:
            GLib.source_remove(self._peek_id)
            self._peek_id = 0
        if self._delay_id:
            GLib.source_remove(self._delay_id)
            self._delay_id = 0
        self._queued = None
        if self._from_face == -1 and self._progress <= 0.0:
            self._target = 0.0
            if self._promote_name:
                self._promote(self._promote_name)
                self._promote_name = None
            self._sync_overlays()
            return
        if self._from_face == -1:
            self._animate_to(0.0)
        elif self._to_face == -1:
            self._animate_to(1.0)
        elif self._progress >= 1.0:
            self._from_face = self._to_face
            self._to_face = -1
            self._direction = -1.0
            self._progress = 0.0
            self._animate_to(1.0)
        elif self._progress <= 0.0:
            self._to_face = -1
            self._direction = -1.0
            self._progress = 0.0
            self._animate_to(1.0)
        elif self._progress < 0.5 and self._target == 1.0:
            self._queued = (-1, -1.0)
            self._animate_to(0.0)
        else:
            self._queued = (-1, -1.0)
            self._animate_to(self._target)

    def set_selected(self, selected: bool):
        selected = bool(selected)
        if selected == self._selected:
            if selected:
                self._activate()
            return
        self._selected = selected
        if selected:
            self._activate()
        else:
            self._hovered = False
            self._close_to_preview()

    def on_hover_enter(self):
        if not self._wallpapers or self._hovered:
            return
        self._hovered = True
        self._activate()

    def _on_hover_peek(self):
        self._peek_id = 0
        if not (self._hovered or self._selected):
            return GLib.SOURCE_REMOVE
        self._from_face = -1
        self._to_face = 0
        self._direction = 1.0
        self._pulse_back = False
        self._animate_to(PEEK_PROGRESS)
        if self._delay_id == 0:
            rem_ms = max(40, self._delay_ms - min(PEEK_DELAY_MS, self._delay_ms))
            self._delay_id = GLib.timeout_add(rem_ms, self._on_delay_done)
        return GLib.SOURCE_REMOVE

    def _on_delay_done(self):
        self._delay_id = 0
        if self._hovered or self._selected:
            self._pulse_back = False
            self._interactive = True
            self._from_face = -1
            self._to_face = 0
            self._direction = 1.0
            self._target = 1.0
            if self._face_paintable(0) is not None:
                self._animate_to(1.0)
        return GLib.SOURCE_REMOVE

    def start_big_preview(self, preserve_face: str | None = None):
        """Activate immediately in the big preview overlay without automatic
        rotation: stays on the original preview with < / > arrows ready."""
        if not self._wallpapers:
            return
        self._selected = True
        self._interactive = True
        self._nav_steady = True
        self._pulse_back = False
        if preserve_face and preserve_face != "__preview__":
            idx = next((i for i, p in enumerate(self._wallpapers)
                        if p == preserve_face or Path(p).name == Path(preserve_face).name), -1)
            if idx >= 0:
                self._from_face = idx
                self._to_face = idx
                self._progress = 1.0
                self._target = 1.0
                self._prefetch_around(idx)
                self._sync_overlays()
                self.queue_draw()
                return
        self._from_face = -1
        self._to_face = 0
        self._progress = 0.0
        self._target = 0.0
        self.request_back()
        self._sync_overlays()
        self.queue_draw()

    def _next_face_in_cycle(self, face: int, delta: int) -> int:
        # Cycle through original preview (-1) and all wallpapers (0 .. n - 1).
        n = len(self._wallpapers)
        return ((face + 1 + delta) % (n + 1)) - 1

    def rotate_step(self, step_dir: int):
        if not self._wallpapers or not self._interactive:
            return
        if not self._nav_steady and not self.is_showing_wallpaper():
            return
        self._pulse_back = False
        if self._peek_id:
            GLib.source_remove(self._peek_id)
            self._peek_id = 0
        if self._delay_id:
            GLib.source_remove(self._delay_id)
            self._delay_id = 0
        self._nav_steady = True
        sign = 1.0 if step_dir >= 0 else -1.0
        delta = 1 if step_dir >= 0 else -1
        if 0.0 < self._target < 1.0:
            # Mid-peek (~16° toward wallpaper 0): > finishes flipping to 0; < settles and flips to last.
            if sign > 0:
                self._prefetch_around(self._to_face)
                self._queued = None
                self._animate_to(1.0)
            else:
                nxt = self._next_face_in_cycle(self._from_face, -1)
                self._prefetch_around(nxt)
                self._queued = (nxt, -1.0)
                self._animate_to(0.0)
            return
        if self._progress >= 1.0:
            cur = self._to_face
            nxt = self._next_face_in_cycle(cur, delta)
            self._prefetch_around(nxt)
            self._from_face = cur
            self._to_face = nxt
            self._direction = sign
            self._progress = 0.0
            self._animate_to(1.0)
            return
        if self._progress <= 0.0:
            cur = self._from_face
            nxt = self._next_face_in_cycle(cur, delta)
            self._prefetch_around(nxt)
            self._from_face = cur
            self._to_face = nxt
            self._direction = sign
            self._progress = 0.0
            self._animate_to(1.0)
            return
        if self._progress < 0.5:
            if self._target == 1.0 and (sign > 0) != (self._direction > 0):
                self._queued = None
                self._animate_to(0.0)
                return
            if self._target == 0.0 and (sign > 0) == (self._direction > 0):
                self._queued = None
                self._animate_to(1.0)
                return
        end_face = self._to_face if self._target == 1.0 else self._from_face
        nxt = self._next_face_in_cycle(end_face, delta)
        self._prefetch_around(nxt)
        self._queued = (nxt, sign)
        self._animate_to(self._target)

    def on_hover_leave(self):
        self._hovered = False
        if self._selected:
            return
        self._close_to_preview()

    def reset_flip(self):
        self._selected = False
        self._hovered = False
        self._interactive = False
        self._nav_steady = False
        self._pulse_back = False
        self._queued = None
        if self._peek_id:
            GLib.source_remove(self._peek_id)
            self._peek_id = 0
        if self._delay_id:
            GLib.source_remove(self._delay_id)
            self._delay_id = 0
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._from_face = -1
        self._to_face = 0
        self._direction = 1.0
        self._target = 0.0
        self._progress = 0.0
        self._last_frame_us = 0
        if self._promote_name:
            self._promote(self._promote_name)
            self._promote_name = None
        self._sync_overlays()
        self.queue_draw()

    def _face_and_angle(self) -> tuple[int, float]:
        eased = 0.5 - 0.5 * math.cos(math.pi * self._progress)
        if eased <= 0.5:
            return self._from_face, self._direction * (180.0 * eased)
        return self._to_face, self._direction * (180.0 * (eased - 1.0))

    def _sync_overlays(self):
        face, local_deg = self._face_and_angle()
        if self._badges is not None:
            if face == -1:
                # Stay crisp during the subtle ~16° 3D hint; fade out once full flip passes 20°.
                fade = max(0.0, (abs(local_deg) - 20.0) / 28.0)
                self._badges.set_opacity(max(0.0, 1.0 - fade))
            else:
                self._badges.set_opacity(0.0)
        face_opacity = max(0.0, 1.0 - abs(local_deg) / 55.0)
        show_nav = bool(self._wallpapers) and (
            (self._interactive and self._nav_steady) or face >= 0
        )
        nav_opacity = 1.0 if (self._interactive and self._nav_steady) else face_opacity
        for nav in self._nav_buttons:
            nav.set_visible(show_nav)
            nav.set_opacity(nav_opacity if show_nav else 0.0)
        if show_nav and len(self._nav_buttons) == 2:
            prev_btn, next_btn = self._nav_buttons
            prev_btn.set_tooltip_text(_("Preview") if self._next_face_in_cycle(face, -1) == -1
                                      else _("Previous wallpaper"))
            next_btn.set_tooltip_text(_("Preview") if self._next_face_in_cycle(face, 1) == -1
                                      else _("Next wallpaper"))
        pin = self._pin_button
        if pin is None:
            return
        if face < 0 or face >= len(self._wallpapers):
            pin.set_visible(False)
            pin.set_opacity(0.0)
            return
        pin.set_visible(True)
        pin.set_opacity(face_opacity)
        wp_name = Path(self._wallpapers[face]).name
        pinned = bool(self._active_name and wp_name == self._active_name)
        key = (face, pinned, self._theme_current, self._pin_apply_theme, len(self._wallpapers))
        if key != self._shown_pin_state:
            self._shown_pin_state = key
            (pin.add_css_class if pinned else pin.remove_css_class)("pinned")
            total = len(self._wallpapers)
            suffix = f" ({face + 1}/{total})" if total > 1 else ""
            if self._theme_current:
                base = _("Current background") if pinned else _("Set as background")
            elif self._pin_apply_theme:
                base = _("Apply theme with this wallpaper")
            else:
                base = _("Current wallpaper for this theme") if pinned else _("Set as wallpaper for this theme")
            pin.set_tooltip_text(base + suffix)

    def _animate_to(self, target: float):
        self._target = target
        if self._tick_id == 0:
            self._last_frame_us = 0
            self._tick_id = self.add_tick_callback(self._on_tick)

    def _step(self, dt: float) -> bool:
        dur = FLIP_STEP_DURATION if self._nav_steady else FLIP_DURATION
        if self._queued is not None:
            dur *= 0.6
        # Cushion into and out of the subtle ~16° 3D hint so it feels soft, never jerky.
        step_scale = 1.0
        if self._from_face == -1 and self._target == PEEK_PROGRESS:
            rem = max(0.0, PEEK_PROGRESS - self._progress)
            step_scale = max(0.32, min(1.0, rem / 0.09)) * 1.15
        elif self._from_face == -1 and self._target == 0.0 and self._progress <= PEEK_PROGRESS + 0.01:
            step_scale = max(0.32, min(1.0, self._progress / 0.09)) * 1.05
        if self._target > self._progress:
            nxt_p = min(self._target, self._progress + (dt / dur) * step_scale)
            if self._to_face >= 0 and self._target > PEEK_PROGRESS and self._face_paintable(self._to_face) is None and nxt_p > 0.48:
                nxt_p = max(self._progress, 0.48)
            self._progress = nxt_p
        elif self._target < self._progress:
            nxt_p = max(self._target, self._progress - (dt / dur) * step_scale)
            if self._from_face >= 0 and self._face_paintable(self._from_face) is None and nxt_p < 0.52:
                nxt_p = min(self._progress, 0.52)
            self._progress = nxt_p

        if self._progress == self._target:
            if self._target == PEEK_PROGRESS and self._pulse_back:
                self._pulse_back = False
                self._target = 0.0
            elif self._queued is not None:
                nxt_face, nxt_dir = self._queued
                self._queued = None
                cur_face = self._to_face if self._target == 1.0 else self._from_face
                if nxt_face != cur_face:
                    self._prefetch_around(nxt_face)
                    self._from_face = cur_face
                    self._to_face = nxt_face
                    self._direction = nxt_dir
                    self._progress = 0.0
                    self._target = 1.0
                    self._sync_overlays()
                    self.queue_draw()
                    return True
            if self._target == 1.0 and self._to_face == -1:
                self._from_face = -1
                self._to_face = 0
                self._direction = 1.0
                self._progress = 0.0
                self._target = 0.0
                if not self._hovered and self._promote_name:
                    self._promote(self._promote_name)
                    self._promote_name = None
            elif self._target == 0.0 and self._from_face == -1:
                if not self._hovered and self._promote_name:
                    self._promote(self._promote_name)
                    self._promote_name = None
            if self._interactive and (
                (self._target == 1.0 and self._progress >= 1.0)
                or (self._target == 0.0 and self._progress <= 0.0)
            ):
                self._nav_steady = True

        self._sync_overlays()
        self.queue_draw()
        if (self._target == 1.0 and self._to_face >= 0
                and self._face_paintable(self._to_face) is None and self._progress >= 0.48):
            return False  # pause edge-on without spinning CPU until decoder delivers texture
        return self._progress != self._target or self._queued is not None

    def _on_tick(self, _widget, clock):
        now_us = clock.get_frame_time() if clock is not None else 0
        if self._last_frame_us == 0 or now_us <= self._last_frame_us:
            dt = 1.0 / 60.0
        else:
            dt = min(0.1, (now_us - self._last_frame_us) / 1_000_000.0)
        self._last_frame_us = now_us
        if not self._step(dt):
            self._tick_id = 0
            self._last_frame_us = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def do_snapshot(self, snapshot):
        if self._from_face == -1 and self._progress <= 0.0:
            FixedPicture.do_snapshot(self, snapshot)
            return
        w = float(self.get_width() or self._size[0])
        h = float(self.get_height() or self._size[1])
        if w <= 0.0 or h <= 0.0:
            return
        face, local_deg = self._face_and_angle()
        paintable = self._face_paintable(face)
        if paintable is None:
            FixedPicture.do_snapshot(self, snapshot)
            return
        d = max(w * 1.8, 260.0)
        rad = math.radians(local_deg)
        scale = (d - (w / 2.0) * abs(math.sin(rad))) / d
        transform = Gsk.Transform.new()
        transform = transform.translate(Graphene.Point().init(w / 2.0, h / 2.0))
        transform = transform.perspective(d)
        transform = transform.rotate_3d(local_deg, Graphene.Vec3().init(0.0, 1.0, 0.0))
        transform = transform.scale(scale, scale)
        transform = transform.translate(Graphene.Point().init(-w / 2.0, -h / 2.0))

        snapshot.save()
        snapshot.transform(transform)
        bounds = Graphene.Rect().init(0.0, 0.0, w, h)
        radius = float(self._radius_fn()) if self._radius_fn else 0.0
        if radius > 0.0:
            rrect = Gsk.RoundedRect()
            rrect.init_from_rect(bounds, radius)
            snapshot.push_rounded_clip(rrect)

        pw = paintable.get_intrinsic_width()
        ph = paintable.get_intrinsic_height()
        if pw > 0 and ph > 0:
            cover = max(w / pw, h / ph)
            dw, dh = pw * cover, ph * cover
            dx, dy = (w - dw) / 2.0, (h - dh) / 2.0
        else:
            dx, dy, dw, dh = 0.0, 0.0, w, h
        if isinstance(paintable, Gdk.Texture):
            snapshot.append_texture(paintable, Graphene.Rect().init(dx, dy, dw, dh))
        else:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(dx, dy))
            paintable.snapshot(snapshot, dw, dh)
            snapshot.restore()

        shade = 0.38 * abs(math.sin(rad))
        if shade > 0.005:
            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue, rgba.alpha = 0.0, 0.0, 0.0, shade
            snapshot.append_color(rgba, bounds)
        rim_alpha = 0.42 * math.sin(min(math.pi * 0.5, abs(rad) * 2.4))
        if rim_alpha > 0.02 and abs(local_deg) < 84.0:
            rim = Gdk.RGBA()
            rim.red, rim.green, rim.blue, rim.alpha = 1.0, 1.0, 1.0, rim_alpha
            edge_x = 0.0 if local_deg > 0.0 else max(0.0, w - 2.0)
            snapshot.append_color(rim, Graphene.Rect().init(edge_x, 0.0, min(2.0, w), h))
        if radius > 0.0:
            snapshot.pop()
        snapshot.restore()


def scaled_pixbuf(path: str, width: int, height: int):
    """A pixbuf decoded at (twice) the display size, not the full 1800px
    preview, so a long list stays light on memory. None on failure.

    The header is checked first: GTK's loaders allocate the declared canvas
    before scaling, so a small crafted file with huge dimensions (or frames)
    is never handed to them."""
    if theme_core.image_size_problem(path):
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width * 2, height * 2, True)
    except (GLib.Error, TypeError):
        return None


class BackgroundNotSet(Exception):
    """A pinned theme was applied, but its wallpaper could not be set."""


class ImageDecoder:
    """Decodes previews and wallpaper thumbnails on worker threads and hands
    the result to a Gtk.Picture on the main loop, so neither a large image
    nor a slow disk ever freezes the popup."""

    def __init__(self, workers: int):
        self.jobs = queue.SimpleQueue()
        for _worker in range(workers):
            threading.Thread(target=self.work, daemon=True).start()

    def request(self, picture, path: str, width: int, height: int, setter=None, failed=None, wanted=None):
        """`failed`: called on the main loop when the picture cannot be decoded (a waiting animation
        must not wait for it forever). `wanted`: asked before decoding; a job no longer wanted (its
        card gone, its wallpaper rotated away) is dropped without decoding."""
        self.jobs.put((setter or picture.set_paintable, str(path), width, height, failed, wanted))

    def work(self):
        while True:
            setter, path, width, height, failed, wanted = self.jobs.get()
            if wanted is not None and not wanted():
                continue
            pixbuf = scaled_pixbuf(path, width, height)
            if pixbuf is not None:
                GLib.idle_add(self.show, setter, pixbuf)
            elif failed is not None:
                GLib.idle_add(self.fail, failed)

    @staticmethod
    def fail(failed):
        failed()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def show(setter, pixbuf):
        setter(Gdk.Texture.new_for_pixbuf(pixbuf))
        return GLib.SOURCE_REMOVE


class ThemeGrid(Gtk.FlowBox):
    """The theme list: theme cards of one fixed width, one column at the
    default size and more as the popup gets wider (like the wallpaper
    grid). Keeps the few ListBox calls the rest of the popup uses."""

    def __init__(self):
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.set_homogeneous(True)
        self.set_activate_on_single_click(True)
        self.set_column_spacing(6)
        self.set_row_spacing(2)
        self.set_valign(Gtk.Align.START)

    def select_row(self, row):
        self.select_child(row)

    def get_selected_row(self):
        selected = self.get_selected_children()
        return selected[0] if selected else None


def parse_uris(text: str) -> list[Path]:
    """File paths from a `text/uri-list` (or plain path list) drop. Non-file
    URIs are dropped; nothing here is ever executed."""
    return theme_core.parse_file_list(text, limit=MAX_DROPS)


IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp")


def drop_source_kind(drop) -> tuple[str, str] | None:
    """What to read from a drop: ("files", "text/uri-list"), ("bytes", mime)
    or ("text", "text/plain…"); None if nothing usable is offered."""
    mimes = list(drop.get_formats().get_mime_types() or [])
    if "text/uri-list" in mimes:
        return "files", "text/uri-list"
    for mime in mimes:
        base = mime.split(";", 1)[0].strip().lower()
        if base in IMAGE_MIMES or (base == "application/octet-stream"
                                   and theme_core.offered_file_name(mime)):
            return "bytes", mime
    for mime in ("text/plain;charset=utf-8", "text/plain"):
        if mime in mimes:
            return "text", mime
    return None


def dropped_paths(value) -> list[Path]:
    """Local files from a drop payload: a Gdk.FileList or a string."""
    if isinstance(value, Gdk.FileList):
        return [Path(path) for path in (f.get_path() for f in value.get_files())
                if path and Path(path).is_file()]
    return parse_uris(value if isinstance(value, str) else "")


class LookUndo:
    """Undo for Font & effects: the theme's look as it was (applied again
    only when the change was applied)."""

    def __init__(self, popup, slug: str, previous: dict, reapply: bool):
        self.popup, self.slug, self.previous, self.reapply = popup, slug, previous, reapply

    def undo(self):
        def work():
            theme_looks.set_look(self.slug, self.previous)
            if self.reapply and theme_core.current_theme() == self.slug:
                theme_looks.apply_look(self.slug)
        self.popup._async(work, lambda _r, error: (
            error is not None and theme_core.notify(_("Could not undo"), str(error)),
            self.popup.refresh_look_pending()))


class _PresetsPage:
    """The themes tab collects the old page's rows; .outer is the page."""

    def __init__(self, outer, themes_tab):
        self.outer = outer
        self.themes_tab = themes_tab

    def append(self, widget):
        self.themes_tab.append(widget)


class ThemeManager(WorkspacesPageMixin, AskPageMixin, Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.omarchy.thememanager")
        self.ws_init()
        self.presets_tab = "workspaces"
        self.window = None
        self.theme = ThemePalette()
        self.hidden = self.load_hidden_list()
        self.show_hidden = False
        self.active_theme = None   # ThemeInfo while the wallpaper view is open
        self.style_provider = None
        self.theme_list = []       # latest discover_themes() result
        self.actions_popover = None
        self.cursor_watch = 0      # GLib source id while the keyboard is held on open
        # Omarchy's menus (Super + Space, …) open underneath this popup: close for them.
        self.layer_watch = LayerWatch("theme-manager", self.on_menu_opened)
        self.position = theme_core.window_position()   # None: centered
        self.move_start = ((0, 0), None)
        self.moving = False
        self.header_drag_id = None
        self.menu_anchor = None
        self.decoder = ImageDecoder(PREVIEW_WORKERS)
        # Type-to-filter as in Simple Launcher: no search field, the title shows it.
        self.filter_query = ""     # theme list
        self.wp_filter = ""        # wallpaper page
        size = theme_core.window_size() or (CARD_WIDTH, LIST_HEIGHT)
        self.card_w, self.list_h = size
        self.settings_previous_size = None
        self.settings_previous_position = None
        self.settings_adjusted_position = None
        self.columns = 2
        self.theme_columns = 1
        self.tile_w = TILE
        self.theme_tile_w = TILE
        self.theme_keys = {}
        self.flip_3d = True
        self.flip_delay = theme_auto.DEFAULT_FLIP_DELAY
        self.pin_apply_theme = True
        self.hidden_wallpapers = {}
        self.current_bg = None
        self.pointer_moved = False
        self.sized = []            # widgets whose width follows the card
        self.resize_start = None
        self.preview = None        # what is previewed on the desktop right now
        self.preview_timer = 0
        self.undo_receipts = []    # what Undo puts back
        self.undo_timer = 0

    def do_shutdown(self):
        cleanup_pid()
        super().do_shutdown()

    def do_activate(self):
        if self.window is None:
            self.build_window()
            self.hold()  # Keep the singleton alive while its popup is hidden.
            signal.signal(signal.SIGUSR1, lambda *_: GLib.idle_add(self.toggle))
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, lambda *_: sys.exit(0))
            GLib.timeout_add(100, lambda: True)  # Let Python dispatch SIGUSR1.
            self.watch_current_theme()
            write_pid_file()
            # Under the shared settings lock (it may wait): on a worker, never at GTK's start.
            self._async(theme_auto.store_default_settings, lambda *_: None)
            try:
                if theme_core.sync_enabled():   # opt-in, see the settings button
                    theme_core.ensure_menu_override(SWITCHER_WRAPPER)
                    theme_core.ensure_theme_set_hook(SYNC_SCRIPT)
                    theme_core.sync_current_theme()
            except (OSError, ValueError):
                pass
        self.open()

    # --- window ----------------------------------------------------------------

    def guard_text_field(self, entry):
        """Route every way text can enter a search field from another client
        through bounded reads. GTK's own paste, middle-click paste and text
        drops buffer the whole offer in memory with no limit or deadline.
        `entry` is a Gtk.Entry/SearchEntry/SpinButton or a bare Gtk.Text."""
        text = entry.get_delegate() or entry
        text.set_max_length(theme_core.MAX_FIELD_CHARS)
        # Ctrl+V, Shift+Insert and the context menu's Paste all emit this.
        text.connect("paste-clipboard", self.on_field_paste, entry)
        # Middle-click pastes the primary selection; claim button 2 before
        # the field's own gesture sees it (primary paste is also off globally).
        middle = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)
        middle.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        middle.connect("pressed", lambda gesture, *_: gesture.set_state(Gtk.EventSequenceState.CLAIMED))
        entry.add_controller(middle)
        # Text dropped on the field: drop the field's own (unbounded) target,
        # so drops fall through to the window's bounded drop handling.
        controllers = text.observe_controllers()
        for i in reversed(range(controllers.get_n_items())):
            controller = controllers.get_item(i)
            if isinstance(controller, (Gtk.DropTarget, Gtk.DropTargetAsync)):
                text.remove_controller(controller)

    def guard_text_fields_in(self, root):
        """guard_text_field for every text field inside a widget GTK builds
        itself (the color chooser's hex field and its H/S/V spin buttons)."""
        guarded = 0
        stack = [root]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.Text):
                self.guard_text_field(widget)
                guarded += 1
                continue
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return guarded

    def on_field_paste(self, text, entry):
        text.stop_emission_by_name("paste-clipboard")

        def worker():
            pasted = theme_core.clipboard_text()
            GLib.idle_add(self.insert_pasted, entry, pasted)

        threading.Thread(target=worker, daemon=True).start()

    def insert_pasted(self, entry, pasted: str | None):
        if pasted is None:
            theme_core.notify("Theme Manager", _("Clipboard text is too long to paste (max {0} KiB)").format(
                theme_core.MAX_PASTE_TEXT_BYTES // 1024))
        elif pasted:
            entry.delete_selection()
            position = entry.insert_text(pasted, entry.get_position())
            entry.set_position(position)
        return GLib.SOURCE_REMOVE

    def build_window(self):
        settings = Gtk.Settings.get_default()
        if settings is not None:
            # No middle-click paste anywhere in the popup (see guard_text_field).
            settings.set_property("gtk-enable-primary-paste", False)
        window = Gtk.ApplicationWindow(application=self)
        window.set_decorated(False)
        window.set_title("Theme Manager")
        window.set_name("thememanager-window")
        Gtk4LayerShell.init_for_window(window)
        Gtk4LayerShell.set_namespace(window, "theme-manager")
        Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.ON_DEMAND)
        # No anchors: the surface is only as large as the card and centered,
        # so the rest of the screen stays usable (e.g. dragging an image in
        # from the file manager). Only the bar icon or Esc closes the popup.
        # -1: ignore the bar's reserved zone, so margins and the centered
        # position are both measured from the monitor's top-left corner.
        Gtk4LayerShell.set_exclusive_zone(window, -1)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("tm-card")
        frame = Gtk.Overlay()
        frame.set_child(card)
        window.set_child(frame)
        self.card = card
        self.card_frame = frame
        # Resize grip in the bottom-right corner (a layer surface has no
        # window frame to drag).
        grip = Gtk.Image.new_from_icon_name("pan-down-symbolic")
        grip.add_css_class("tm-grip")
        grip.set_pixel_size(14)
        grip.set_halign(Gtk.Align.END)
        grip.set_valign(Gtk.Align.END)
        grip.set_cursor_from_name("se-resize")
        grip.set_tooltip_text(_("Drag to resize · double-click for the default size"))
        sizer = Gtk.GestureDrag()
        sizer.connect("drag-begin", self.on_resize_begin)
        sizer.connect("drag-update", self.on_resize_update)
        sizer.connect("drag-end", self.on_resize_end)
        grip.add_controller(sizer)
        frame.add_overlay(grip)
        frame.add_overlay(self.build_ws_hover())          # a workspace card's large preview
        frame.add_overlay(self.build_drag_ghost())        # a dragged layout follows the pointer
        self.ws_hover_frame = frame
        frame.add_overlay(self.build_screenshot_dialog())
        # Drop images anywhere on the card while a wallpaper page is open:
        # files from a file manager (uri-list), the image bytes a browser
        # offers for a dragged picture, or file paths as text. The drop is read
        # by hand (DropTargetAsync) because browsers offer the bytes under
        # parameterised types like `application/octet-stream;name="x.jpg"`.
        # MOVE must be allowed too: Hyprland preselects it, and a COPY-only
        # target is refused before "enter". We always ask for COPY, so the
        # source never deletes its file.
        drop = Gtk.DropTargetAsync.new(None, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drop.connect("accept", lambda _t, d: self.drop_allowed() and drop_source_kind(d) is not None)
        drop.connect("drag-enter", self.on_drag_motion)
        drop.connect("drag-motion", self.on_drag_motion)
        drop.connect("drag-leave", lambda *_: self.light_add_tile(False))
        drop.connect("drop", self.on_drop)
        card.add_controller(drop)
        self.list_h = max(theme_core.MIN_WINDOW_SIZE[1], min(self.list_h, monitor_height() - 2 * SCREEN_MARGIN))

        header = Gtk.Box(spacing=8)
        self.main_header = header
        title = Gtk.Label(label="Theme Manager")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(30)   # a long filter query must not widen the card
        self.title_label = title
        title.add_css_class("tm-title")
        title.set_xalign(0)
        header.append(title)
        # Small, centred between the title and the header buttons: what a
        # double-click on the header does at this size. No natural width of
        # its own (the full text would widen the card): it takes the room
        # left and ends in "…".
        mouse_hint = Gtk.Label(xalign=0.5)
        mouse_hint.add_css_class("tm-header-hint")
        mouse_hint.set_ellipsize(Pango.EllipsizeMode.END)
        mouse_hint.set_max_width_chars(1)
        mouse_hint.set_hexpand(True)
        mouse_hint.set_margin_start(6)
        mouse_hint.set_margin_end(6)
        header.append(mouse_hint)
        self.mouse_hint = mouse_hint
        # Dragging the title bar moves the popup (a layer surface: placed by
        # margins; Hyprland keeps Super + drag for itself). Double-click:
        # see on_header_double_click.
        header.set_cursor_from_name("grab")
        title.set_tooltip_text(_("Drag to move"))
        right_clicks = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right_clicks.connect("pressed", lambda _g, count, _x, _y: count == 2
                             and self.on_header_double_click(Gdk.BUTTON_SECONDARY))
        header.add_controller(right_clicks)
        mover = Gtk.GestureDrag()
        mover.connect("drag-begin", self.on_move_begin)
        mover.connect("drag-update", self.on_move_update)
        mover.connect("drag-end", self.on_move_end)
        header.add_controller(mover)
        self.hidden_button = Gtk.Button(icon_name="view-reveal-symbolic")
        self.hidden_button.set_tooltip_text(_("Show hidden themes and wallpapers"))
        self.hidden_button.add_css_class("flat")
        self.hidden_button.add_css_class("tm-header-btn")
        self.hidden_button.connect("clicked", lambda *_: self.toggle_show_hidden())
        import_button = Gtk.Button(icon_name="folder-download-symbolic")
        import_button.set_tooltip_text(_("Import themes from omarchy.org (Ctrl+I)"))
        import_button.add_css_class("flat")
        import_button.add_css_class("tm-header-btn")
        import_button.connect("clicked", lambda *_: self.open_import())
        # The same Nerd Font gear (U+F0493) in a small bordered box as the other plugins.
        settings_button = Gtk.Button()
        gear = Gtk.Label(label="\U000F0493")
        gear.add_css_class("tm-gear")
        settings_button.set_child(gear)
        settings_button.set_tooltip_text(_("Settings"))
        settings_button.add_css_class("flat")
        settings_button.add_css_class("tm-header-btn")
        settings_button.add_css_class("tm-gear-btn")
        settings_button.connect("clicked", lambda *_: self.open_settings())
        # Information: the author's other plugins, left of the gear as in the other plugins.
        info_button = Gtk.Button()
        info_glyph = Gtk.Label(label=more_plugins.INFO_GLYPH)
        info_glyph.add_css_class("tm-info")
        info_button.set_child(info_glyph)
        info_button.set_tooltip_text(_("Information"))
        info_button.add_css_class("flat")
        info_button.add_css_class("tm-header-btn")
        info_button.connect("clicked", lambda *_: self.open_info())
        presets_button = Gtk.Button(icon_name="bookmark-new-symbolic")
        presets_button.set_tooltip_text(_("Presets: saved theme and wallpaper"))
        presets_button.add_css_class("flat")
        presets_button.add_css_class("tm-header-btn")
        presets_button.connect("clicked", lambda *_: self.open_presets())
        self.presets_button = presets_button
        # Ask AI, always there as in Maprisma Launcher: the same sparkle
        # (Material's auto_awesome = Nerd Font md-creation, U+F0674), accent
        # when a helper is ready, dim while Ask AI is off, a dot while it is
        # on but no helper is signed in. The page explains and switches it on.
        ask_button = Gtk.Button()
        ask_face = Gtk.Overlay()
        ask_glyph = Gtk.Label(label="\U000F0674")
        ask_glyph.add_css_class("tm-ask-glyph")
        ask_face.set_child(ask_glyph)
        ask_dot = Gtk.Box()
        ask_dot.add_css_class("tm-ask-dot")
        ask_dot.set_halign(Gtk.Align.END)
        ask_dot.set_valign(Gtk.Align.START)
        ask_dot.set_can_target(False)
        ask_face.add_overlay(ask_dot)
        ask_button.set_child(ask_face)
        ask_button.add_css_class("flat")
        ask_button.add_css_class("tm-header-btn")
        ask_button.connect("clicked", lambda *_: self.open_ask())
        self.ask_header_button = ask_button
        self.ask_header_glyph = ask_glyph
        self.ask_header_dot = ask_dot
        self.header_buttons = {"import": import_button, "hidden": self.hidden_button, "ask": ask_button,
                               "presets": presets_button, "info": info_button}
        self.header_tooltips = {
            "import": _("Import themes from omarchy.org (Ctrl+I)"),
            "hidden": _("Show hidden themes and wallpapers"),
            "presets": _("Presets: saved theme and wallpaper"),
            "info": _("Information"),
            "ask": _("Ask AI: find or make a theme"),
        }
        self.header_order = header_order.normalize(theme_auto.settings().get("header_order"))
        group = Gtk.Overlay()
        group.set_child(Gtk.Box(spacing=0, accessible_role=Gtk.AccessibleRole.GROUP))
        self.header_group = group
        self.header_group_row = group.get_child()
        self.header_group_row.add_css_class("tm-header-group")
        self.header_group_row.update_property([Gtk.AccessibleProperty.LABEL], [_('Header buttons')])
        marker = Gtk.Box()
        marker.add_css_class("tm-header-drop-marker")
        marker.set_halign(Gtk.Align.START)
        marker.set_valign(Gtk.Align.START)
        marker.set_can_target(False)
        marker.set_visible(False)
        group.add_overlay(marker)
        self.header_drop_marker = marker
        self.header_separators = {}
        for button_id, button in self.header_buttons.items():
            self.set_header_tooltip(button_id)
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self.on_header_drag_begin, button_id)
            drag.connect("drag-update", self.on_header_drag_update, button_id)
            drag.connect("drag-end", self.on_header_drag_end, button_id)
            drag.connect("cancel", self.on_header_drag_cancel, button_id)
            button.add_controller(drag)
        self.rebuild_header_group()
        header.append(group)
        header.append(settings_button)   # rightmost, like the other plugins
        self.settings_button = settings_button
        self.info_button = info_button
        card.append(header)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_transition_duration(140)
        stack.set_hhomogeneous(True)
        stack.set_vhomogeneous(True)
        stack.set_interpolate_size(False)
        card.append(stack)

        # --- themes page
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self.track_width(scroller)
        self.list_scroller = scroller
        self.rows = ThemeGrid()
        self.rows.add_css_class("tm-list")
        self.rows.connect("child-activated", lambda _rows, row: self.on_theme_row_activated(row))
        self.no_match = Gtk.Label(wrap=True, max_width_chars=1)
        self.no_match.add_css_class("tm-hint")
        self.no_match.set_margin_top(24)
        # How many themes are hidden, and how to see them: under the grid.
        self.hidden_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        self.hidden_hint.add_css_class("tm-hint")
        self.hidden_hint.set_margin_top(10)
        self.hidden_hint.set_margin_start(8)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_box.append(self.rows)
        list_box.append(self.no_match)
        list_box.append(self.hidden_hint)
        scroller.set_child(list_box)
        self.themes_overlay = Gtk.Overlay()
        self.themes_overlay.set_child(scroller)
        self.big_preview_slot = Gtk.Box(accessible_role=Gtk.AccessibleRole.DIALOG)
        self.big_preview_slot.set_halign(Gtk.Align.CENTER)
        self.big_preview_slot.set_valign(Gtk.Align.CENTER)
        self.big_preview_slot.set_visible(False)
        self.themes_overlay.add_overlay(self.big_preview_slot)
        self.themes_overlay.set_measure_overlay(self.big_preview_slot, False)
        self.themes_overlay.set_clip_overlay(self.big_preview_slot, True)
        self.big_preview_theme = None
        self.big_preview_picture = None
        self.big_preview_actions = None
        self.big_preview_size_px = (0, 0)
        self.big_preview_enabled = True
        self._overlay_press_outside = False
        outside_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        outside_click.connect("pressed", self.on_themes_overlay_pressed)
        outside_click.connect("released", self.on_themes_overlay_released)
        self.themes_overlay.add_controller(outside_click)
        # Hover selects like in Simple Launcher, but only when the pointer
        # really moves: a pointer resting on the list must not steal the
        # keyboard selection when a menu closes or the list is rebuilt.
        # Positions are taken on the card, which never scrolls: relative to
        # the list, a resting pointer "moves" whenever the keyboard scrolls it.
        self.pointer_at = None
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda _c, x, y: self.on_list_pointer(x, y, moved=False))
        hover.connect("motion", lambda _c, x, y: self.on_list_pointer(x, y, moved=True))
        hover.connect("leave", lambda *_: setattr(self, "pointer_at", None))
        card.add_controller(hover)
        # Keyboard: taken exclusively on open (a card-sized on-demand layer is
        # not focused by itself), then on demand as soon as the pointer moves.
        # From there it follows the pointer like any window, and other apps
        # can start drags again (an exclusive layer blocks that).
        focus = Gtk.EventControllerMotion()
        focus.connect("enter", lambda *_: self.release_keyboard())
        focus.connect("leave", lambda *_: self.release_keyboard())
        card.add_controller(focus)
        stack.add_named(self.themes_overlay, "themes")

        # --- wallpapers page
        wallpaper_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(wallpaper_page)

        wp_header = Gtk.Box(spacing=8)
        back_button = self.back_button()
        back_button.set_tooltip_text(_("Back to themes (Left, Backspace or Esc)"))
        back_button.connect("clicked", lambda *_: self.show_themes())
        wp_header.append(back_button)
        self.wp_title = Gtk.Label(label=_("Wallpapers"), xalign=0)
        self.wp_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.wp_title.set_max_width_chars(1)  # natural width stays small; hexpand fills the row
        self.wp_title.add_css_class("tm-title")
        self.wp_title.set_hexpand(True)
        wp_header.append(self.wp_title)
        wallpaper_page.append(wp_header)

        self.wp_toolbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.paste_button = Gtk.Button()
        self.paste_button.add_css_class("tm-paste")
        paste_box = Gtk.Box(spacing=6)
        paste_box.append(Gtk.Image.new_from_icon_name("edit-paste-symbolic"))
        paste_box.append(Gtk.Label(label=_("Paste image (Ctrl+V)")))
        self.paste_button.set_child(paste_box)
        self.paste_button.set_tooltip_text(_("Add the image copied to the clipboard (from the web or the file manager)"))
        self.paste_button.connect("clicked", lambda *_: self.paste_clipboard())
        web_button = Gtk.Button()
        web_button.add_css_class("tm-paste")
        web_box = Gtk.Box(spacing=6)
        web_box.append(Gtk.Image.new_from_icon_name("system-search-symbolic"))
        web_box.append(Gtk.Label(label=_("Search the web")))
        web_button.set_child(web_box)
        web_button.set_tooltip_text(_("Find wallpapers on wallhaven.cc (Ctrl+F)"))
        web_button.connect("clicked", lambda *_: self.open_web_search())
        # Ask AI for wallpapers: only while Ask AI is on and a helper is ready.
        self.wp_ask_button = Gtk.Button()
        self.wp_ask_button.add_css_class("tm-paste")
        ask_box = Gtk.Box(spacing=6)
        ask_box.append(Gtk.Image.new_from_icon_name("mail-send-symbolic"))
        ask_box.append(Gtk.Label(label=_("Ask AI…")))
        self.wp_ask_button.set_child(ask_box)
        self.wp_ask_button.set_tooltip_text(
            _("Describe the wallpaper you want: Ask AI turns it into searches on wallhaven.cc"))
        self.wp_ask_button.connect("clicked", lambda *_: self.open_web_search(ask=True))
        self.wp_ask_button.set_visible(False)
        buttons = Gtk.Box(spacing=8)
        buttons.append(self.paste_button)
        buttons.append(web_button)
        buttons.append(self.wp_ask_button)
        self.wp_toolbar.append(buttons)
        wallpaper_page.append(self.wp_toolbar)
        # Where dropped and pasted images are stored (the add tile's tooltip).
        self.drop_note = ""
        self.add_tile = None

        wp_scroller = Gtk.ScrolledWindow()
        wp_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        wp_scroller.set_vexpand(True)
        self.wp_grid = Gtk.FlowBox()
        self.wp_grid.add_css_class("tm-grid")
        self.wp_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.wp_grid.set_homogeneous(True)
        self.wp_grid.set_column_spacing(8)
        self.wp_grid.set_row_spacing(8)
        self.wp_grid.set_valign(Gtk.Align.START)  # tiles keep their height; no stretching
        self.wp_grid.set_activate_on_single_click(True)
        self.wp_grid.connect("child-activated", lambda _g, child: getattr(child, "tm_add", False)
                             and self.choose_files())
        wp_scroller.set_child(self.wp_grid)
        wallpaper_page.append(wp_scroller)
        stack.add_named(wallpaper_page, "wallpapers")

        # --- web wallpaper search, laid out like the wallpapers
        web_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(web_page)
        ws_header = Gtk.Box(spacing=8)
        ws_back = self.back_button()
        ws_back.set_tooltip_text(_("Back to the wallpapers (Backspace or Esc)"))
        ws_back.connect("clicked", lambda *_: self.close_web_search())
        ws_header.append(ws_back)
        self.ws_title = Gtk.Label(label=_("Search wallpapers"), xalign=0)
        self.ws_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.ws_title.set_max_width_chars(1)
        self.ws_title.add_css_class("tm-title")
        self.ws_title.set_hexpand(True)
        ws_header.append(self.ws_title)
        self.ws_header = ws_header                # the helper choice sits at its right end (Ask AI on)
        web_page.append(ws_header)
        self.web_search = Gtk.SearchEntry()
        self.web_search.set_placeholder_text(_("Search, e.g. misty forest, neon city"))
        self.web_search.connect("activate", lambda *_: self.run_web_search())
        self.guard_text_field(self.web_search)
        self.web_search.set_hexpand(True)
        # The search field, and (without Ask AI) the pictures-per-batch selector next to it.
        self.web_search_row = Gtk.Box(spacing=6)
        self.web_search_row.append(self.web_search)
        web_page.append(self.web_search_row)
        # Ask AI (only while it is on and a helper is ready): your words become
        # search phrases, and a helper with web tools also finds pictures on the web.
        self.web_ai_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ai_row = Gtk.Box(spacing=6)
        # Which helper asks (the same choice as on the Ask AI page, remembered).
        self.web_helper_drop = Gtk.DropDown.new_from_strings([])
        self.web_helper_drop.add_css_class("tm-helper")
        self.web_helper_drop.set_valign(Gtk.Align.CENTER)
        self.web_helper_drop.set_factory(ask_page.helper_name_factory(ellipsize=True))
        self.web_helper_drop.set_list_factory(ask_page.helper_name_factory(ellipsize=False))
        self.web_helper_drop.set_tooltip_text(_("Helper:"))
        self.web_helper_drop.update_property([Gtk.AccessibleProperty.LABEL], [_("Helper:")])
        self.web_helper_drop.connect("notify::selected", lambda *_: self.web_helper_changed())
        self.web_ai_ready = []
        self.web_helper_filling = False
        self.web_helper_drop.set_halign(Gtk.Align.END)
        self.web_helper_drop.set_visible(False)
        self.ws_header.append(self.web_helper_drop)
        self.web_ai_entry = Gtk.Entry(hexpand=True,
                                      placeholder_text=_("Ask AI, e.g. “calm and wintry, fits this theme”"))
        self.web_ai_entry.set_max_length(theme_ai.MAX_REQUEST)
        self.web_ai_entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Ask AI")])
        self.guard_text_field(self.web_ai_entry)
        self.web_ai_entry.connect("activate", lambda *_: self.web_ai_ask())
        ai_row.append(self.web_ai_entry)
        self.web_ai_button = Gtk.Button()
        self.web_ai_button.add_css_class("tm-paste")
        self.web_ai_button.add_css_class("suggested")
        self.web_ai_button.connect("clicked", lambda *_: self.web_ai_stop() if self.web_ai_busy else self.web_ai_ask())
        ai_row.append(self.web_ai_button)
        # Pictures per batch (remembered): what Ask asks for from the web, and what Next shows.
        self.web_count_drop = Gtk.DropDown.new_from_strings(
            [_("{0} pictures").format(n) for n in theme_ai.WALLPAPER_COUNTS])
        self.web_count_drop.add_css_class("tm-count")
        self.web_count_drop.set_valign(Gtk.Align.CENTER)
        self.web_count_drop.set_tooltip_text(
            _("Pictures per batch: from the web and from wallhaven.cc; Next shows the next batch"))
        self.web_count_drop.update_property([Gtk.AccessibleProperty.LABEL], [_("Pictures per batch")])
        self.web_count_drop.set_selected(theme_ai.WALLPAPER_COUNTS.index(theme_ai.wallpaper_count(theme_auto.settings())))
        self.web_count_drop.connect("notify::selected", lambda *_: self.web_count_changed())
        self.web_ai_row = ai_row
        ai_row.append(self.web_count_drop)
        self.web_ai_box.append(ai_row)
        self.web_ai_chips = ws_wrap()
        self.web_ai_chips.set_visible(False)
        self.web_ai_box.append(self.web_ai_chips)
        self.web_ai_box.set_visible(False)
        web_page.append(self.web_ai_box)
        self.web_ai_helper = None
        self.web_ai_web = False
        self.web_ai_busy = False
        self.web_ai_searched = []
        self.web_pics = []            # web_images.WebPicture Ask AI found, shown first
        self.web_ai_face(False)
        hint_row = Gtk.Box(spacing=8)
        # Busy: Ask AI is thinking or a search is loading.
        self.web_spinner = Gtk.Spinner()
        self.web_spinner.set_visible(False)
        self.web_spinner.set_valign(Gtk.Align.START)
        hint_row.append(self.web_spinner)
        self.web_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, hexpand=True)
        self.web_hint.add_css_class("tm-hint")
        hint_row.append(self.web_hint)
        web_page.append(hint_row)
        ws_scroller = Gtk.ScrolledWindow()
        ws_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        ws_scroller.set_vexpand(True)
        self.web_grid = Gtk.FlowBox()
        self.web_grid.add_css_class("tm-grid")
        self.web_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.web_grid.set_homogeneous(True)
        self.web_grid.set_column_spacing(8)
        self.web_grid.set_row_spacing(8)
        self.web_grid.set_valign(Gtk.Align.START)
        # One batch at a time; Next at the end of the grid shows (or asks for) the next one.
        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        results_box.append(self.web_grid)
        self.web_next = Gtk.Button()
        self.web_next.add_css_class("tm-paste")
        self.web_next.set_halign(Gtk.Align.CENTER)
        self.web_next.set_margin_top(4)
        self.web_next.set_margin_bottom(8)
        self.web_next.connect("clicked", lambda *_: self.web_cancel_next() if self.web_next_pending
                              else self.web_next_batch())
        self.web_next.set_visible(False)
        results_box.append(self.web_next)
        ws_scroller.set_child(results_box)
        web_page.append(ws_scroller)
        stack.add_named(web_page, "websearch")
        self.web_query = ""
        self.web_page_no = 0
        self.web_last_page = 0
        self.web_results = []       # wallhaven results loaded so far
        self.web_shown = 0          # of them shown (one batch more per Next)
        self.web_more = False       # the last web ask brought pictures: Next asks again
        self.web_next_token = 0     # a Cancel makes the answers of what Next started stale
        self.web_theme_slug = None  # the theme the wallpaper search page was opened for
        self.web_epoch = 0          # a new search or theme makes a running first Ask AI answer stale
        self.web_next_pending = set()   # what Next started and is still running: "web", "wallhaven"
        self.web_next_stop = threading.Event()  # ends Next's page loading between two pages
        self.web_ai_request = ""
        self.web_loading = False
        self.web_generation = 0     # a new search makes late answers of an old one stale
        self.web_pictures = {}      # id -> Gtk.Picture waiting for its thumbnail

        # --- import page: the omarchy.org gallery, laid out like the wallpapers
        import_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(import_page)
        im_header = Gtk.Box(spacing=8)
        im_back = self.back_button()
        im_back.set_tooltip_text(_("Back to themes (Left, Backspace or Esc)"))
        im_back.connect("clicked", lambda *_: self.show_themes())
        im_header.append(im_back)
        im_title = Gtk.Label(label=_("Import themes — omarchy.org"), xalign=0)
        im_title.set_ellipsize(Pango.EllipsizeMode.END)
        im_title.set_max_width_chars(1)
        im_title.add_css_class("tm-title")
        im_title.set_hexpand(True)
        im_header.append(im_title)
        import_page.append(im_header)

        im_toolbar = Gtk.Box(spacing=8)
        self.import_search = Gtk.SearchEntry()
        self.import_search.set_placeholder_text(_("Search themes"))
        self.import_search.set_hexpand(True)
        self.import_search.connect("search-changed", lambda *_: self.import_grid.invalidate_filter())
        self.guard_text_field(self.import_search)
        im_toolbar.append(self.import_search)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("tm-paste")
        refresh.set_tooltip_text(_("Reload the list from omarchy.org"))
        refresh.connect("clicked", lambda *_: self.load_catalog(force=True))
        im_toolbar.append(refresh)
        import_page.append(im_toolbar)
        self.import_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=40)
        self.import_hint.add_css_class("tm-hint")
        import_page.append(self.import_hint)

        im_scroller = Gtk.ScrolledWindow()
        im_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        im_scroller.set_vexpand(True)
        self.import_grid = Gtk.FlowBox()
        self.import_grid.add_css_class("tm-grid")
        self.import_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.import_grid.set_homogeneous(True)
        self.import_grid.set_column_spacing(8)
        self.import_grid.set_row_spacing(8)
        self.import_grid.set_valign(Gtk.Align.START)
        self.import_grid.set_filter_func(self.catalog_filter)
        im_scroller.set_child(self.import_grid)
        import_page.append(im_scroller)
        stack.add_named(import_page, "import")
        self.catalog = []
        self.catalog_pictures = {}   # slug -> Gtk.Picture still waiting for its preview
        self.browser = None

        # --- confirmation page. A separate Gtk.Window would open beneath
        # this overlay-layer surface, so confirmations live inside the card.
        confirm_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        confirm_page.set_valign(Gtk.Align.CENTER)
        confirm_page.set_margin_start(24)
        confirm_page.set_margin_end(24)
        self.confirm_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=36)
        self.confirm_label.add_css_class("tm-confirm")
        confirm_page.append(self.confirm_label)
        self.confirm_detail = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        self.confirm_detail.add_css_class("tm-hint")
        self.confirm_detail.set_visible(False)
        confirm_page.append(self.confirm_detail)
        answers = Gtk.Box(spacing=12)
        answers.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.add_css_class("tm-paste")
        cancel.connect("clicked", lambda *_: self.cancel_confirm())
        self.confirm_button = Gtk.Button(label=_("Delete"))
        self.confirm_button.add_css_class("tm-paste")
        self.confirm_button.add_css_class("danger")
        self.confirm_button.connect("clicked", lambda *_: self.accept_confirm())
        answers.append(cancel)
        answers.append(self.confirm_button)
        confirm_page.append(answers)
        stack.add_named(confirm_page, "confirm")
        self.confirm_action = None
        self.confirm_return = "themes"

        stack.add_named(self.build_settings_page(), "settings")
        stack.add_named(self.build_presets_page(), "presets")
        stack.add_named(self.build_ask_page(), "ask")
        stack.add_named(self.build_info_page(), "info")
        stack.add_named(self.build_editor_page(), "editor")
        stack.add_named(self.build_shortcut_page(), "shortcut")
        stack.add_named(self.build_look_page(), "look")
        self.look_pending: dict[str, list[str]] = {}   # the theme in use → its fields not on the desktop

        # Undo after a delete: the file went to the trash and comes back from there.
        self.undo_bar = Gtk.Revealer()
        self.undo_bar.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        undo_line = Gtk.Box(spacing=8)
        undo_line.add_css_class("tm-undo")
        self.undo_label = Gtk.Label(xalign=0, hexpand=True)
        self.undo_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.undo_label.set_max_width_chars(1)
        undo_line.append(self.undo_label)
        undo_button = Gtk.Button(label=_("Undo"))
        undo_button.add_css_class("tm-paste")
        undo_button.set_tooltip_text(_("Put it back (Ctrl+Z)"))
        undo_button.connect("clicked", lambda *_: self.undo_delete())
        undo_line.append(undo_button)
        self.undo_bar.set_child(undo_line)
        # Floats over the bottom of the card, so the popup never changes size.
        self.undo_bar.set_valign(Gtk.Align.END)
        self.undo_bar.set_margin_start(17)
        self.undo_bar.set_margin_end(17)
        self.undo_bar.set_margin_bottom(17)
        self.undo_bar.set_can_target(True)
        self.card_frame.add_overlay(self.undo_bar)

        # Capture phase: the list's own Up/Down handling would otherwise move
        # the selection a second time once a row has the keyboard focus.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        window.add_controller(keys)

        keys.connect("key-released", self.on_key_released)

        self.window = window
        self.stack = stack
        # Like the other plugins, the gear and (i) are gone while their pages are open.
        stack.connect("notify::visible-child-name", lambda st, _p: self.update_page_buttons(st))
        self.resize_to(self.card_w, self.list_h)
        self.apply_theme_css()
        self.refresh_themes()

    # --- size ----------------------------------------------------------------------

    def set_ask_ai(self, on: bool):
        self.save_setting(ask_ai=on)
        self.update_ask_symbol()

    def set_maprisma_hints(self, on: bool):
        self.save_setting(maprisma_hints=on)
        if self.stack.get_visible_child_name() == "settings":
            GLib.idle_add(self.fill_settings)    # the note: shown, or behind its ?

    def update_ask_symbol(self):
        """Accent when Ask AI is on and a helper is ready; dim when off; a dot
        when on but no helper is signed in (as Maprisma Launcher's)."""
        on = ask_page.enabled(theme_auto.settings())
        ready = bool(theme_ai.ready_helpers()) if on else False
        glyph = self.ask_header_glyph
        for css, active in (("tm-ask-ready", on and ready), ("tm-ask-off", not on)):
            (glyph.add_css_class if active else glyph.remove_css_class)(css)
        self.ask_header_dot.set_visible(on and not ready)
        tip = (_("Ask AI") if on and ready else
               _("Ask AI (set up a helper first)") if on else _("Ask AI (off: turn it on in Settings)"))
        self.header_tooltips["ask"] = tip
        self.set_header_tooltip("ask")

    def back_button(self) -> Gtk.Button:
        """Back as in Maprisma, on every page: accent text, no border,
        "← Back · ⌫" (Backspace and Esc go back too)."""
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("tm-back")
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        box.append(Gtk.Label(label=_("Back")))
        box.append(Gtk.Label(label="· ⌫"))
        button.set_child(box)
        button.update_property([Gtk.AccessibleProperty.LABEL], [_("Back")])
        return button

    def track_width(self, widget):
        """`widget` is as wide as the card's inside and follows it when the
        popup is resized."""
        self.sized.append(widget)
        widget.set_size_request(self.card_w, -1)

    def size_limits(self) -> tuple[int, int]:
        geo = self.monitor_geometry() if self.window is not None else None
        width = geo.width if geo is not None else 1920
        return max(CARD_WIDTH, width - 80), max(theme_core.MIN_WINDOW_SIZE[1], monitor_height() - 2 * SCREEN_MARGIN)

    def resize_to(self, width: int, height: int, save: bool = False):
        """Card width and list height, kept within the monitor. The grids
        show as many columns as fit."""
        max_w, max_h = self.size_limits()
        self.card_w = max(CARD_WIDTH, min(int(width), max_w))
        self.list_h = max(theme_core.MIN_WINDOW_SIZE[1], min(int(height), max_h))
        self.update_settings_navigation_layout()
        for widget in self.sized:
            widget.set_size_request(self.card_w, -1)
        if getattr(self, "ask_prompt_scroller", None) is not None:
            self.ask_prompt_height()                 # Ask AI's suggestions: 2 rows narrow, 4 wide
        # Raise the maximum first: GTK refuses a minimum above the maximum.
        self.list_scroller.set_max_content_height(100000)
        self.list_scroller.set_min_content_height(self.list_h)
        self.list_scroller.set_max_content_height(self.list_h)
        # Columns about TILE_TARGET wide, the tiles filling them: a large popup
        # shows larger previews, not just more small ones.
        self.columns = min(MAX_COLUMNS, max(2, (self.card_w + 8) // TILE_TARGET))
        # Less each tile's padding, border and the spacing, so N tiles always
        # fit the card (two at the default 420 px).
        tile_w = max(TILE, (self.card_w + 8) // self.columns - (TILE_STEP - TILE) - 6)
        # The theme grid's own tiles are slimmer (spacing 6, padding 2, tile
        # padding 3): two at the default 420 px get previews larger than the
        # old list rows (195 px against 192) and still fit the card exactly.
        theme_tile_w = max(TILE, (self.card_w - (self.columns - 1) * 6) // self.columns - 12)
        self.tiles_dirty = getattr(self, "tiles_dirty", False) or tile_w != self.tile_w \
            or theme_tile_w != getattr(self, "theme_tile_w", 0)
        self.tile_w = tile_w
        self.theme_tile_w = theme_tile_w
        # Themes: tiles in columns at every width
        # like the wallpapers.
        was = self.theme_columns
        # Tiles at every width (Uwe, 2026-11-06): two columns already at the
        # default size, no more list rows with empty space beside the name.
        self.theme_columns = self.columns
        self.rows.set_min_children_per_line(self.theme_columns)
        self.rows.set_max_children_per_line(self.theme_columns)
        self.rows.set_halign(Gtk.Align.FILL)
        self.rows.set_row_spacing(2 if self.theme_columns == 1 else 8)
        if (was > 1) != (self.theme_columns > 1) and self.theme_list:
            self.refresh_themes()   # switch between list rows and tiles
        elif self.tiles_dirty and getattr(self, "resize_start", None) is None and self.theme_list:
            # New tile size (not while the grip is dragged: once on release).
            self.tiles_dirty = False
            self.refresh_themes()
            if getattr(self, "active_theme", None) is not None:
                self.refresh_wallpapers()
            if getattr(self, "catalog", None):
                self.fill_catalog(self.catalog, None)          # import page tiles
            self.rebuild_web_tiles()
        self.ws_set_width(self.card_w)
        self.ed_set_width(self.card_w)
        for grid in (self.wp_grid, self.web_grid, self.import_grid):
            grid.set_min_children_per_line(self.columns)
            grid.set_max_children_per_line(self.columns)
        if self.theme_preview_open() and getattr(self, "resize_start", None) is None:
            self.refresh_theme_preview()
        if self.window is not None:
            # A layer surface does not shrink by itself once content gets smaller.
            self.window.set_default_size(1, 1)
        self.update_mouse_hint()
        if save:
            default = (self.card_w, self.list_h) == (CARD_WIDTH, LIST_HEIGHT)
            try:
                theme_core.save_window_size(None if default else (self.card_w, self.list_h))
            except OSError:
                pass

    def on_resize_begin(self, _gesture, _x, _y):
        # A second press on the grip within the double-click time: default size.
        now = time.monotonic()
        if now - getattr(self, "last_grip_press", 0.0) < 0.4:
            self.last_grip_press = 0.0
            self.resize_start = None
            self.resize_to(CARD_WIDTH, LIST_HEIGHT, save=True)
            return
        self.last_grip_press = now
        if self.position is None:
            # Centered: pin the top-left corner, so the card grows right and down.
            self.move_to(self.current_position())
        self.resize_start = ((self.card_w, self.list_h), theme_core.cursor_position())

    def on_resize_update(self, _gesture, dx, dy):
        if self.resize_start is None:
            return
        (width, height), cursor_start = self.resize_start
        cursor = theme_core.cursor_position() if cursor_start is not None else None
        if cursor is not None:
            dx, dy = cursor[0] - cursor_start[0], cursor[1] - cursor_start[1]
        self.resize_to(width + round(dx), height + round(dy))

    def on_resize_end(self, _gesture, _dx, _dy):
        if self.resize_start is None:
            return
        self.resize_start = None
        self.resize_to(self.card_w, self.list_h, save=True)
        if self.position is not None:
            self.move_to(self.position, save=True)

    @staticmethod
    def preview_size() -> tuple[int, int]:
        """Theme cards keep one size; a wider popup shows more of them."""
        return PREVIEW_W, PREVIEW_H

    def in_left_column(self, tile) -> bool:
        return tile is None or tile.get_index() % self.columns == 0

    def in_top_row(self, tile) -> bool:
        return tile is not None and tile.get_index() < self.columns

    # --- settings page ------------------------------------------------------------------

    def settings_row(self, box, title: str, hint: str, control) -> Gtk.Box:
        row = Gtk.Box(spacing=12)
        row.add_css_class("tm-setting")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.set_valign(Gtk.Align.CENTER)   # level with its control
        label = Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=1)
        label.add_css_class("tm-setting-title")
        label.set_hexpand(True)
        text.append(label)
        if hint:
            small = Gtk.Label(label=hint, xalign=0, wrap=True, max_width_chars=1)
            small.set_hexpand(True)
            small.add_css_class("tm-hint")
            text.append(small)
        row.append(text)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            row.append(control)
        box.append(row)
        return row

    @staticmethod
    def settings_heading(box, text: str, hint: str = ""):
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("tm-setting-heading")
        if hint:
            label.set_tooltip_text(hint)
        box.append(label)

    def setting_line(self, box, title: str, hint: str, control) -> Gtk.Box:
        """A settings-page row like the other plugins': dim label | control;
        the explanation is the tooltip of both."""
        row = Gtk.Box(spacing=10)
        row.add_css_class("tm-line")
        label = Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=1)
        label.add_css_class("tm-line-label")
        label.set_size_request(130, -1)
        label.set_valign(Gtk.Align.CENTER)
        row.append(label)
        if hint:
            label.set_tooltip_text(hint)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            control.set_halign(Gtk.Align.START)
            control.set_hexpand(True)
            if hint:
                control.set_tooltip_text(hint)
            row.append(control)
        row.tm_search = (title + " " + hint).casefold()
        box.append(row)
        return row

    def switch(self, active: bool, on_change) -> Gtk.Switch:
        switch = Gtk.Switch(active=active)
        switch.connect("notify::active", lambda sw, _p: self.settings_ready and on_change(sw.get_active()))
        return switch

    def dropdown(self, labels: list[str], selected: int, on_change) -> Gtk.DropDown:
        drop = Gtk.DropDown.new_from_strings(labels)
        drop.set_selected(max(0, selected))
        drop.connect("notify::selected", lambda d, _p: self.settings_ready and on_change(d.get_selected()))
        return drop

    def build_settings_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = self.back_button()
        back.set_tooltip_text(_("Back to themes (Backspace, Esc)"))
        back.connect("clicked", lambda *_: self.show_themes())
        header.append(back)
        title = Gtk.Label(label=_("Settings").upper(), xalign=0, hexpand=True)
        title.add_css_class("tm-section-title")
        header.append(title)
        page.append(header)
        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.add_css_class("tm-settings-rule")
        page.append(rule)
        body = Gtk.Box(spacing=12)
        body.set_vexpand(True)
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        nav.add_css_class("tm-settings-nav")
        nav.set_size_request(158, -1)
        body.append(nav)
        self.settings_nav = nav
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_hexpand(True)
        search = Gtk.SearchEntry(placeholder_text=_("Find in Settings"))
        search.add_css_class("tm-settings-search")
        self.guard_text_field(search)
        search.connect("search-changed", lambda *_: self.filter_settings())
        content.append(search)
        self.settings_search = search
        chips_scroller = Gtk.ScrolledWindow()
        chips_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        chips_scroller.set_min_content_width(1)
        chips_scroller.set_min_content_height(42)
        chips = Gtk.Box(spacing=5)
        chips.add_css_class("tm-settings-chips")
        chips_scroller.set_child(chips)
        content.append(chips_scroller)
        self.settings_chips_scroller = chips_scroller
        self.settings_chips = chips
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self.settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.settings_box.add_css_class("tm-settings")
        self.settings_box.add_css_class("tm-settings-page")
        scroller.set_child(self.settings_box)
        content.append(scroller)
        self.settings_scroller = scroller
        body.append(content)
        page.append(body)
        reset = Gtk.Button(label=_("Reset to defaults"))
        reset.add_css_class("sc-small")
        reset.set_halign(Gtk.Align.START)
        reset.set_tooltip_text(_("Resets language, corners, sorting, header button order, picker sync, schedule, wallpaper rotation, screenshot key and folder, and web search for wallpapers (on). Keeps favorites, hidden themes, shortcuts and saved workspace assignments; themes per workspace turn off."))
        reset.connect("clicked", lambda *_a: self.reset_settings())
        nav.append(Gtk.Box(vexpand=True))
        nav.append(reset)
        reset_narrow = Gtk.Button(label=_("Reset to defaults"))
        reset_narrow.add_css_class("sc-small")
        reset_narrow.set_halign(Gtk.Align.START)
        reset_narrow.set_tooltip_text(reset.get_tooltip_text())
        reset_narrow.connect("clicked", lambda *_a: self.reset_settings())
        content.append(reset_narrow)
        self.settings_reset_narrow = reset_narrow
        self.settings_selected = "general"
        self.settings_nav_last_button = None
        self.settings_chip_buttons = {}
        self.settings_ready = False
        return page

    def settings_section(self, key: str, title: str, hint: str = "") -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        section.tm_title = title
        section.add_css_class("tm-settings-section")
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("tm-setting-heading")
        heading.tm_search = title.casefold()
        section.append(heading)
        self.settings_box.append(section)
        self.settings_sections[key] = section
        button = Gtk.Button(label=title)
        button.add_css_class("tm-settings-nav-button")
        button.set_halign(Gtk.Align.FILL)
        button.get_child().set_xalign(0)
        if hint:
            button.set_tooltip_text(hint)
        button.connect("clicked", lambda *_: self.select_settings_section(key))
        self.settings_nav.insert_child_after(button, self.settings_nav_last_button)
        self.settings_nav_last_button = button
        self.settings_nav_buttons[key] = button
        chip = Gtk.Button(label=title)
        chip.add_css_class("tm-settings-chip")
        chip.connect("clicked", lambda *_: self.select_settings_section(key))
        self.settings_chips.append(chip)
        self.settings_chip_buttons[key] = chip
        return section

    def update_settings_navigation_layout(self):
        if not hasattr(self, "settings_nav"):
            return
        wide = self.card_w >= 700
        self.settings_nav.set_visible(wide)
        self.settings_chips_scroller.set_visible(not wide)
        self.settings_reset_narrow.set_visible(not wide)

    def select_settings_section(self, key: str):
        self.settings_selected = key
        self.settings_search.set_text("")
        self.filter_settings()

    def filter_settings(self):
        if not hasattr(self, "settings_sections"):
            return
        query = self.settings_search.get_text().strip().casefold()
        any_hits = False
        for key, section in self.settings_sections.items():
            title_match = bool(query and query in section.tm_title.casefold())
            hits = False
            child = section.get_first_child()
            while child is not None:
                match = not query or title_match or query in getattr(child, "tm_search", "")
                child.set_visible(match)
                hits = hits or match
                child = child.get_next_sibling()
            section.get_first_child().set_visible(hits)
            section.set_visible(hits if query else key == self.settings_selected)
            any_hits = any_hits or hits
            button = self.settings_nav_buttons[key]
            chip = self.settings_chip_buttons[key]
            if key == self.settings_selected and not query:
                button.add_css_class("tm-nav-active")
                chip.add_css_class("tm-nav-active")
                button.update_state([Gtk.AccessibleState.SELECTED], [True])
                chip.update_state([Gtk.AccessibleState.SELECTED], [True])
            else:
                button.remove_css_class("tm-nav-active")
                chip.remove_css_class("tm-nav-active")
                button.update_state([Gtk.AccessibleState.SELECTED], [False])
                chip.update_state([Gtk.AccessibleState.SELECTED], [False])
        if hasattr(self, "settings_no_match"):
            self.settings_no_match.set_visible(bool(query and not any_hits))
        self.settings_scroller.get_vadjustment().set_value(0)

    def update_page_buttons(self, stack):
        if stack.get_visible_child_name() != "presets":
            self.ws_hover_hide()
        if stack.get_visible_child_name() != "themes":
            self.hide_theme_preview()
        on_page = stack.get_visible_child_name() in ("settings", "info", "presets", "ask")
        self.settings_button.set_visible(not on_page)
        self.info_button.set_visible(not on_page)
        self.presets_button.set_visible(not on_page)
        self.ask_header_button.set_visible(not on_page)
        self.update_ask_symbol()
        self.update_header_separators()

    def set_header_tooltip(self, button_id):
        """Tooltip with the move hint; the accessible name stays the plain
        label (the tooltip would otherwise become the name, hint included)."""
        text = self.header_tooltips[button_id]
        hint = _('Drag to rearrange · Alt+←/→')
        button = self.header_buttons[button_id]
        button.set_tooltip_text(text + "\n" + hint)
        button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                               [text, hint])

    def rebuild_header_group(self):
        """Reparent the buttons only after a drag is over."""
        row = self.header_group_row
        child = row.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            row.remove(child)
            child = following
        for button_id in self.header_order:
            separator = self.header_separators.get(button_id)
            if separator is None:
                separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                separator.add_css_class("tm-header-sep")
                self.header_separators[button_id] = separator
            row.append(separator)
            row.append(self.header_buttons[button_id])
        self.update_header_separators()

    def update_header_separators(self):
        seen_visible = False
        for button_id in self.header_order:
            visible = self.header_buttons[button_id].get_visible()
            self.header_separators[button_id].set_visible(visible and seen_visible)
            seen_visible = seen_visible or visible

    def header_button_bounds(self, button):
        result = button.compute_bounds(self.header_group)
        if isinstance(result, tuple):
            if not result[0]:
                return None
            result = result[1]
        return result

    def header_drag_position(self, button_id, x):
        visible = [item for item in self.header_order if self.header_buttons[item].get_visible()]
        rectangles = [self.header_button_bounds(self.header_buttons[item]) for item in visible]
        if not rectangles or any(rect is None for rect in rectangles):
            return visible, None, None
        source = self.header_button_bounds(self.header_buttons[button_id])
        if source is None:
            return visible, None, None
        group_x = source.get_x() + x
        slot = header_order.insertion_index([(rect.get_x(), rect.get_width()) for rect in rectangles], group_x)
        edge = rectangles[slot].get_x() if slot < len(rectangles) else \
            rectangles[-1].get_x() + rectangles[-1].get_width()
        return visible, slot, (edge, rectangles[0].get_y(), rectangles[0].get_height())

    def on_header_drag_begin(self, _gesture, x, _y, button_id):
        # drag-begin fires on the press itself. Claiming here would deny the
        # button's own click gesture (same widget, other group), so ordinary
        # clicks would never fire: claim only once the pointer really moves.
        self.header_drag_id = None
        self.header_press = (button_id, x)
        self.header_drop_slot = None

    def on_header_drag_update(self, gesture, dx, dy, button_id):
        press = getattr(self, "header_press", None)
        if self.header_drag_id is None:
            if press is None or press[0] != button_id:
                return
            threshold = Gtk.Settings.get_default().get_property("gtk-dnd-drag-threshold")
            if max(abs(dx), abs(dy)) < max(1, threshold):
                return
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # no click on release
            self.header_drag_id = button_id
            self.header_drag_start_x = press[1]
        if self.header_drag_id == button_id:
            self.mark_header_drop(button_id, self.header_drag_start_x + dx)

    def on_header_drag_cancel(self, _gesture, _sequence, button_id):
        """A cancelled drag (popup hidden, grab lost) must not reorder."""
        if self.header_drag_id == button_id or (getattr(self, "header_press", None) or (None,))[0] == button_id:
            self.header_drag_id = None
            self.header_press = None
            self.header_drop_slot = None
            self.header_drop_marker.set_visible(False)

    def mark_header_drop(self, button_id, x):
        _visible, slot, position = self.header_drag_position(button_id, x)
        self.header_drop_slot = slot
        marker = self.header_drop_marker
        if position is None:
            marker.set_visible(False)
            return
        edge, top, height = position
        marker.set_margin_start(max(0, round(edge) - 1))
        marker.set_margin_top(round(top))
        marker.set_size_request(3, round(height))
        marker.set_visible(True)

    def on_header_drag_end(self, _gesture, _dx, _dy, button_id):
        if (getattr(self, "header_press", None) or (None,))[0] == button_id:
            self.header_press = None
        if self.header_drag_id != button_id:
            return
        slot = self.header_drop_slot
        visible = [item for item in self.header_order if self.header_buttons[item].get_visible()]
        self.header_drag_id = None
        self.header_drop_slot = None
        self.header_drop_marker.set_visible(False)  # drag-leave is not guaranteed
        if slot is None:
            return
        reordered = header_order.move_to_visible_slot(self.header_order, button_id, visible, slot)
        if reordered != self.header_order:
            # GTK must finish dispatching drag-end before its source is reparented.
            GLib.idle_add(self.save_header_order, reordered)

    def save_header_order(self, order):
        if order != self.header_order:
            self.header_order = list(order)
            self.save_setting(header_order=self.header_order)
            self.rebuild_header_group()
        return False

    def save_header_order_live(self, order):
        """Show an order already saved elsewhere (backup restore)."""
        if order != self.header_order:
            self.header_order = list(order)
            self.rebuild_header_group()

    def move_header_button(self, button_id, direction):
        """One visible place left or right; buttons hidden on this page keep
        their place (a swap with one would change nothing on screen)."""
        visible = [item for item in self.header_order if self.header_buttons[item].get_visible()]
        if button_id not in visible:
            return
        index = visible.index(button_id)
        slot = index - 1 if direction < 0 else index + 2
        if not 0 <= slot <= len(visible):
            return
        self.save_header_order(header_order.move_to_visible_slot(self.header_order, button_id, visible, slot))
        self.header_buttons[button_id].grab_focus()

    def build_presets_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = self.back_button()
        back.connect('clicked', lambda *_: self.show_themes())
        header.append(back)
        title = Gtk.Label(label=_('Presets').upper(), xalign=0, hexpand=True)
        title.add_css_class('tm-section-title')
        header.append(title)
        page.append(header)
        # Three tabs: theme presets, and what the Workspace Presets plugin did
        # (layouts per workspace, App Presets), which Theme Manager took over.
        # As Maprisma's tab rows: outlined buttons with an icon, natural width,
        # left-aligned; the active one has a 2 px accent border.
        tabs = Gtk.Box(spacing=8, accessible_role=Gtk.AccessibleRole.TAB_LIST)
        tabs.add_css_class('tm-tabs')
        tabs.set_halign(Gtk.Align.START)
        self.presets_tabs = {}
        inner = Gtk.Stack()
        inner.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        inner.set_transition_duration(120)
        inner.set_vexpand(True)
        self.presets_stack = inner
        for key, label, icon in (('workspaces', _('Workspaces'), 'view-grid-symbolic'),
                                 ('apps', _('App Presets'), 'view-app-grid-symbolic'),
                                 ('themes', _('Save/Load Presets'), 'document-save-symbolic')):
            button = Gtk.ToggleButton(accessible_role=Gtk.AccessibleRole.TAB)
            face = Gtk.Box(spacing=6)
            face.append(Gtk.Image.new_from_icon_name(icon))
            face.append(Gtk.Label(label=label))
            button.set_child(face)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.add_css_class('tm-tab')
            button.connect('toggled', lambda b, k=key: b.get_active() and self.show_presets_tab(k))
            if self.presets_tabs:
                button.set_group(next(iter(self.presets_tabs.values())))
            tabs.append(button)
            self.presets_tabs[key] = button
        page.append(tabs)
        page.append(self.build_ws_banner())   # Workspace Presets still on the bar: shown on every tab
        page.append(inner)
        themes_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.add_named(themes_tab, 'themes')
        inner.add_named(self.build_ws_tab('workspaces'), 'workspaces')
        inner.add_named(self.build_ws_tab('apps'), 'apps')
        hint = Gtk.Label(label=_('Save the current theme and wallpaper for later.'),
                         xalign=0, wrap=True)
        hint.add_css_class('tm-hint')
        themes_tab.append(hint)
        page = _PresetsPage(page, themes_tab)
        save_row = Gtk.Box(spacing=6)
        name = Gtk.Entry(placeholder_text=_('Preset name'), hexpand=True)
        self.guard_text_field(name)
        name.connect('activate', lambda *_: self.save_current_preset())
        save_row.append(name)
        self.preset_name = name
        save = Gtk.Button(label=_('Save current'))
        save.add_css_class('tm-paste')
        save.connect('clicked', lambda *_: self.save_current_preset())
        save.set_sensitive(False)
        name.connect('changed', lambda entry: save.set_sensitive(bool(entry.get_text().strip())))
        save_row.append(save)
        page.append(save_row)
        # The Workspaces and App Presets tabs' settings can go with a preset.
        include = Gtk.CheckButton(label=_('Include workspace presets and app presets'))
        include.set_active(True)
        page.append(include)
        self.preset_workspaces = include
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.presets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller.set_child(self.presets_box)
        page.append(scroller)
        self.presets_tabs['workspaces'].set_active(True)
        return page.outer

    def show_presets_tab(self, key):
        self.ws_drag_reset()          # a drag in the preview never crosses tabs
        self.presets_tab = key
        self.presets_stack.set_visible_child_name(key)
        self.ws_hover_hide()
        if getattr(self, 'stack', None) is None or self.stack.get_visible_child_name() != 'presets':
            return   # building the page: nothing to load yet
        self.ws_refresh_banner()
        if key == 'themes':
            self.preset_name.grab_focus()
        else:
            self.ws_reload()

    def open_presets(self, tab=None):
        self.active_theme = None
        self.ws_choose_current()      # the workspace you are on is the one chosen
        self.refresh_presets()
        self.stack.set_visible_child_name('presets')
        tab = tab or self.presets_tab
        if self.presets_tabs[tab].get_active():
            self.show_presets_tab(tab)
        else:
            self.presets_tabs[tab].set_active(True)

    def include_workspace_presets(self) -> bool:
        return self.preset_workspaces.get_active()

    def save_current_preset(self):
        try:
            name = theme_presets.clean_name(self.preset_name.get_text())
            exists = any(item['name'].casefold() == name.casefold() for item in theme_presets.load())
            if exists:
                self.ask_confirm(_('Replace “{0}”?').format(name), _('Replace'),
                                 lambda: self.write_current_preset(name, replace=True))
                return
        except (OSError, ValueError) as error:
            theme_core.notify(_('Could not save preset'), self.preset_error_text(error))
            return
        self.write_current_preset(name, replace=False)

    def write_current_preset(self, name, replace):
        try:
            include = self.include_workspace_presets()
            if replace:
                theme_presets.replace_current(name, include_workspaces=include)
            else:
                theme_presets.add_current(name, include_workspaces=include)
        except (OSError, ValueError) as error:
            theme_core.notify(_('Could not save preset'), self.preset_error_text(error))
            return
        self.preset_name.set_text('')
        self.refresh_presets()

    @staticmethod
    def preset_error_text(error):
        known = {
            'preset name is required': _('Enter a preset name.'),
            'preset name must be 1–60 printable characters': _('Use a name of up to 60 printable characters.'),
            'a preset with this name already exists': _('A preset with this name already exists.'),
            'current theme is unavailable': _('The current theme is unavailable.'),
            'preset theme is not installed': _('Theme missing'),
            'preset wallpaper is unavailable': _('Wallpaper missing'),
            'invalid preset': _('Invalid preset'),
            'presets file too large': _('The presets are too large to save: delete a preset with workspace presets first.'),
            'workspace presets too large': _('The presets are too large to save: delete a preset with workspace presets first.'),
            'invalid workspace preset': _('Invalid preset'),
            'could not apply the preset theme': _('Could not apply the preset theme.'),
            'wallpaper-only preset theme is not active': _('Apply the preset theme too, or choose a preset for the active theme.'),
        }
        return known.get(str(error), _('Theme missing') if isinstance(error, FileNotFoundError) else str(error))

    def refresh_presets(self):
        box = self.presets_box
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()
        presets = theme_presets.load()
        themes = {item.slug: item for item in theme_core.discover_themes()}
        if not presets:
            box.append(Gtk.Label(label=_('No presets yet.'), xalign=0))
        for preset in presets:
            row = Gtk.Box(spacing=6)
            row.add_css_class('tm-setting-row')
            detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            label = Gtk.Label(label=preset['name'], xalign=0,
                              ellipsize=Pango.EllipsizeMode.END)
            detail.append(label)
            theme = themes.get(preset['theme'])
            wallpaper = theme_auto.wallpaper_named(theme, preset['wallpaper']) if theme and preset['wallpaper'] else None
            parts = [theme_core.plain_text(theme.name, 60) if theme else _('Theme missing'),
                     preset['wallpaper'] if wallpaper else _('Wallpaper missing') if preset['wallpaper'] else _('No wallpaper')]
            summary = Gtk.Label(label=' · '.join(parts), xalign=0, ellipsize=Pango.EllipsizeMode.END)
            summary.add_css_class('tm-hint')
            detail.append(summary)
            if 'workspaces' in preset:
                layouts, apps = workspace_state.snapshot_counts(preset['workspaces'])
                spaces = Gtk.Label(label=_('Workspace presets: {0} · App presets: {1}').format(layouts, apps),
                                   xalign=0, ellipsize=Pango.EllipsizeMode.END)
                spaces.add_css_class('tm-hint')
                detail.append(spaces)
            row.append(detail)
            apply_button = Gtk.Button(label=_('Apply'))
            apply_button.add_css_class('tm-paste')
            apply_button.set_sensitive(theme is not None)
            apply_button.connect('clicked', lambda _b, p=preset: self.confirm_apply_preset(p))
            row.append(apply_button)
            workspace = Gtk.Button(icon_name='view-grid-symbolic')
            workspace.add_css_class('tm-paste')
            workspace.set_sensitive(theme is not None)
            workspace.set_tooltip_text(_('Assign theme and/or wallpaper to workspace'))
            workspace.update_property([Gtk.AccessibleProperty.LABEL],
                                      [_('Assign preset {0} to workspace').format(preset['name'])])
            workspace.connect('clicked', lambda b, p=preset: self.show_preset_workspace_menu(b, p))
            row.append(workspace)
            delete = Gtk.Button(icon_name='user-trash-symbolic')
            delete.add_css_class('tm-paste')
            delete.add_css_class('tm-destructive')
            delete.set_tooltip_text(_('Delete preset'))
            delete.update_property([Gtk.AccessibleProperty.LABEL],
                                   [_('Delete preset {0}').format(preset['name'])])
            delete.connect('clicked', lambda _b, p=preset: self.ask_confirm(
                _('Delete preset “{0}”?').format(p['name']), _('Delete'),
                lambda: (theme_presets.delete(p['name']), self.refresh_presets())))
            row.append(delete)
            box.append(row)

    def confirm_apply_preset(self, preset):
        """App presets start apps and move other windows away: ask first."""
        apps = workspace_state.app_preset_workspaces(preset.get('workspaces') or {})
        if not apps:
            self.apply_preset(preset)
            return
        self.ask_confirm(_('Apply “{0}”?').format(preset['name']), _('Apply'),
                         lambda: self.apply_preset(preset),
                         _('Missing apps on workspaces {0} start; other windows there move to a free workspace.')
                         .format(', '.join(str(ws) for ws in apps)))

    def apply_preset(self, preset):
        def done(warnings, error):
            if error:
                theme_core.notify(_('Could not apply preset'), self.preset_error_text(error))
            else:
                mapped = {'wallpaper unavailable': _('Wallpaper missing'),
                          'font unavailable': _('Font unavailable'),
                          'workspace presets failed': _('The workspace presets could not be applied.'),
                          'some workspace apps failed': _('Some workspace apps did not start.')}
                theme_core.notify(_('Preset applied'), ', '.join(mapped.get(item, item) for item in warnings or []))
                self.current_slug = theme_core.current_theme()
                if self.theme.reload():
                    self.apply_theme_css()
                self.refresh_themes()
        self._async(lambda: theme_presets.apply(preset), done)

    def show_preset_workspace_menu(self, anchor, preset):
        self.show_menu(anchor, lambda popover: self.build_preset_workspace_menu(popover, preset))

    def build_preset_workspace_menu(self, popover, preset):
        box = self.menu_box()
        box.append(Gtk.Label(label=_('Assign to workspace'), xalign=0))
        options = [(_('Theme'), True, False)]
        installed = next((item for item in theme_core.discover_themes()
                          if item.slug == preset['theme']), None)
        if installed and preset['wallpaper'] and theme_auto.wallpaper_named(installed, preset['wallpaper']):
            if theme_core.current_theme() == preset['theme']:
                options.append((_('Wallpaper'), False, True))
            options.append((_('Theme + wallpaper'), True, True))
        choice = Gtk.DropDown.new_from_strings([label for label, _theme, _wallpaper in options])
        choice.set_selected(0)
        box.append(choice)
        grid = Gtk.Grid(column_spacing=2, row_spacing=2, column_homogeneous=True)
        for ws in range(1, 11):
            button = Gtk.Button(label=str(ws))
            button.add_css_class('tm-menu-item')
            button.connect('clicked', lambda _b, w=ws: (
                popover.popdown(), self.assign_preset_workspace(
                    preset, w, options[choice.get_selected()][1],
                    options[choice.get_selected()][2])))
            grid.attach(button, (ws - 1) % 5, (ws - 1) // 5, 1, 1)
        box.append(grid)
        return box

    def assign_preset_workspace(self, preset, workspace, use_theme, use_wallpaper):
        try:
            theme_presets.assign_workspace(preset, workspace, theme=use_theme, wallpaper=use_wallpaper)
        except (OSError, ValueError) as error:
            theme_core.notify(_('Could not assign workspace'), self.preset_error_text(error))
            return
        theme_core.notify(_('Workspace {0} assigned').format(workspace), preset['name'])
        self.refresh_presets()
        self.refresh_focused_workspace(workspace)

    def refresh_focused_workspace(self, workspace):
        if theme_auto.active_workspace() == workspace:
            def apply():
                with theme_auto.locked():
                    theme_auto.apply_workspace(workspace)
            self._async(apply, lambda _value, error: error and theme_core.notify(
                _('Could not assign workspace'), str(error)))

    def build_screenshot_dialog(self) -> Gtk.Widget:
        """After F12, as in Maprisma Launcher: a small dialog over the card with
        where the picture went and an optional note. A separate Gtk.Window
        would open beneath this layer surface, so it is an overlay; the
        backdrop takes every click meant for the page under it."""
        backdrop = Gtk.Box()
        backdrop.add_css_class("tm-dialog-backdrop")
        backdrop.set_hexpand(True)
        backdrop.set_vexpand(True)
        backdrop.set_visible(False)
        dialog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         accessible_role=Gtk.AccessibleRole.DIALOG)
        dialog.add_css_class("tm-dialog")
        dialog.set_halign(Gtk.Align.CENTER)
        dialog.set_valign(Gtk.Align.CENTER)
        dialog.set_hexpand(True)
        dialog.set_margin_start(24)
        dialog.set_margin_end(24)
        path = Gtk.Label(xalign=0, wrap=True, selectable=True, max_width_chars=40)
        path.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        dialog.append(path)
        self.shot_path_label = path
        note = Gtk.Entry(placeholder_text=_("What is wrong? (optional)"))
        note.update_property([Gtk.AccessibleProperty.LABEL], [_("What is wrong? (optional)")])
        note.set_max_length(1000)
        self.guard_text_field(note)
        note.connect("activate", lambda *_: self.finish_screenshot_note(True))
        dialog.append(note)
        self.shot_note_entry = note
        actions = Gtk.Box(spacing=8)
        actions.set_halign(Gtk.Align.END)
        open_folder = Gtk.Button(label=_("Open folder"))
        open_folder.add_css_class("tm-paste")
        open_folder.connect("clicked", lambda *_: self.open_screenshot_folder())
        actions.append(open_folder)
        done = Gtk.Button(label=_("Done"))
        done.add_css_class("tm-paste")
        done.connect("clicked", lambda *_: self.finish_screenshot_note(True))
        actions.append(done)
        dialog.append(actions)
        backdrop.append(dialog)
        self.shot_dialog = backdrop
        self.shot_path = None
        return backdrop

    def shot_dialog_open(self) -> bool:
        return getattr(self, "shot_dialog", None) is not None and self.shot_dialog.get_visible()

    def show_screenshot_note(self, path: Path):
        self.shot_path = path
        self.shot_path_label.set_label(_("Screenshot saved: {0}").format(path))
        self.shot_note_entry.set_text("")
        self.shot_dialog.set_visible(True)
        for widget in (self.stack, self.main_header):   # no Tab into the page under it
            widget.set_sensitive(False)
        self.shot_note_entry.grab_focus()

    def finish_screenshot_note(self, save: bool):
        """Done or Enter keeps the note (none typed, no file); Esc skips it."""
        if save and self.shot_path is not None:
            try:
                screenshots.save_note(self.shot_path, self.shot_note_entry.get_text())
            except (OSError, ValueError) as error:
                theme_core.notify(_("Could not save screenshot note"), str(error))
        self.shot_path = None
        self.shot_dialog.set_visible(False)
        for widget in (self.stack, self.main_header):
            widget.set_sensitive(True)

    def open_screenshot_folder(self):
        if self.shot_path is None:
            return
        try:
            subprocess.Popen(["xdg-open", str(self.shot_path.parent)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError as error:
            theme_core.notify(_("Could not open folder"), str(error))

    def build_info_page(self) -> Gtk.Widget:
        """Information: the author's other marketplace plugins as cards."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = self.back_button()
        back.set_tooltip_text(_("Back to themes (Backspace, Esc)"))
        back.connect("clicked", lambda *_: self.show_themes())
        header.append(back)
        title = Gtk.Label(label=_("Information").upper(), xalign=0, hexpand=True)
        title.add_css_class("tm-section-title")
        header.append(title)
        page.append(header)
        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.add_css_class("tm-settings-rule")
        page.append(rule)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        # The author's plugins, from the marketplace (more_catalog): refreshed when the page opens.
        self.info_scroller = scroller
        self.info_shown = None
        self.fill_info_cards(more_catalog.cached())
        page.append(scroller)
        return page

    def fill_info_cards(self, plugins: list):
        """The cards for this list (Theme Manager itself left out); the built-in list when there is none yet."""
        cards = more_catalog.cards(plugins)
        if cards == self.info_shown:
            return
        self.info_shown = cards
        self.info_scroller.set_child(more_plugins.build_cards(more_catalog.OWN_ID, "tm", on_opened=self.close,
                                                              plugins=cards))

    def refresh_info_cards(self):
        """The marketplace's list (at most hourly, only if it changed), on a thread."""
        if getattr(self, "info_refreshing", False):
            return
        self.info_refreshing = True

        def work():
            plugins = None
            try:
                plugins = more_catalog.refresh()
            finally:
                # Always back on the main loop, so a failure never leaves the page refreshing.
                GLib.idle_add(done, plugins)

        def done(plugins):
            self.info_refreshing = False
            if plugins is not None:
                self.fill_info_cards(plugins)
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()

    def open_info(self):
        self.active_theme = None
        self.stop_preview()
        self.stack.set_visible_child_name("info")
        self.refresh_info_cards()

    def set_language(self, code: str):
        if code == i18n.language_setting():
            return
        try:
            i18n.save_language(code)
        except OSError as error:
            theme_core.notify(_("Could not save the setting"), str(error))
            return
        os.environ["THEME_MANAGER_PAGE"] = "settings"
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def open_settings(self):
        self.active_theme = None
        self.stop_preview()
        if hyprland_rounding() != getattr(self, "window_rounding", None):
            self.apply_theme_css()  # A theme or looknfeel.lua changed the windows' rounding.
        self.fill_settings()
        self.stack.set_visible_child_name("settings")

    def fill_settings(self):
        """(Re)build the settings controls from the saved settings."""
        self.settings_ready = False
        box = self.settings_box
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()
        for button in getattr(self, "settings_nav_buttons", {}).values():
            self.settings_nav.remove(button)
        for button in getattr(self, "settings_chip_buttons", {}).values():
            self.settings_chips.remove(button)
        self.settings_nav_buttons = {}
        self.settings_chip_buttons = {}
        self.settings_nav_last_button = None
        self.settings_sections = {}
        themes = sorted(theme_core.discover_themes(), key=lambda t: t.name.casefold())
        slugs = [t.slug for t in themes]
        names = [theme_core.plain_text(t.name, 60) for t in themes]
        box = self.settings_section("general", _("General"))

        # Interface language: the system's unless picked here. Switching
        # restarts the popup (same process id) so every label is rebuilt.
        codes = ["auto"] + [code for code, _name in i18n.LANGUAGES]
        setting = i18n.language_setting()
        self.setting_line(box, _("Language"), _("Theme Manager restarts in the new language."),
                          self.dropdown([_("System ({0})").format(i18n.language_name(i18n.system_language()))]
                                        + [name for _code, name in i18n.LANGUAGES],
                                        codes.index(setting) if setting in codes else 0,
                                        lambda i: self.set_language(codes[i])))

        self.corner_choices = corners.CHOICES
        self.corners_line = self.setting_line(
            box, _("Corners"), self.corners_tip(),
            self.dropdown(self.corner_labels(),
                          self.corner_choices.index(corners.choice(theme_auto.settings().get("corners"))),
                          lambda i: self.set_corners(self.corner_choices[i])))

        box = self.settings_section("themes", _("Themes"))
        self.setting_line(box, _("Sort the list"), _("Favorites (★ in a theme's menu) always come first."),
                          self.dropdown([_("By name"), _("Recently used first")],
                                        theme_auto.SORT_CHOICES.index(theme_auto.sort_order()),
                                        lambda i: self.save_setting(sort=theme_auto.SORT_CHOICES[i])))
        self.setting_line(box, _("3D flip to wallpaper on hover"),
                          _("Hovering a theme card flips its preview in 3D to show the theme's wallpaper."),
                          self.switch(theme_auto.preview_flip_enabled(),   # on by default
                                      lambda on: self.save_setting(preview_flip_3d=on)))
        delay_choices = theme_auto.FLIP_DELAY_CHOICES
        delay_labels = [_("0 s (arrows only)") if s == 0 else _("{0} s").format(s) for s in delay_choices]
        self.setting_line(box, _("3D rotation delay"),
                          _("Seconds before a selected theme card starts rotating. 0 keeps the preview and shows left/right arrows immediately."),
                          self.dropdown(delay_labels, delay_choices.index(theme_auto.preview_flip_delay()),
                                        lambda i: self.save_setting(preview_flip_delay=delay_choices[i])))
        self.setting_line(box, _("Apply theme when pinning wallpaper"),
                          _("On: pinning a wallpaper on another theme switches to that theme immediately. Off: only saves it as that theme’s current wallpaper."),
                          self.switch(theme_auto.preview_pin_apply_theme_enabled(),   # on by default
                                      lambda on: self.save_setting(preview_pin_apply_theme=on)))
        self.setting_line(box, _("Big preview on click"),
                          _("Clicking or pressing Enter on a theme card opens a large interactive preview overlay inside the window. Off opens the theme’s wallpapers page directly."),
                          self.switch(theme_auto.preview_big_card_enabled(),          # on by default
                                      lambda on: self.save_setting(preview_big_card=on)))
        self.setting_line(box, _("Also hide in Omarchy's pickers"),
                          _("Hidden themes and wallpapers also leave Menu → Style. Adds one marked line to "
                          "omarchy-menu.jsonc and a theme-set hook; off removes them again."),
                          self.switch(theme_core.sync_enabled(), self.set_omarchy_sync))

        plan = theme_auto.schedule()
        box = self.settings_section("day-night", _("Day and night"))
        self.setting_line(box, _("Switch themes by time of day"),
                          _("At the times below. A theme you pick by hand stays until the next switch."),
                          self.switch(plan["enabled"], self.set_schedule_enabled))
        times = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
        for key, at_key, label in (("day", "day_at", _("Day theme")), ("night", "night_at", _("Night theme"))):
            line = Gtk.Box(spacing=6)
            line.append(self.dropdown([_("Choose…"), *names],
                                      slugs.index(plan[key]) + 1 if plan[key] in slugs else 0,
                                      lambda i, k=key: i and self.save_schedule(**{k: slugs[i - 1]})))
            at = plan[at_key] if plan[at_key] in times else times[0]
            line.append(self.dropdown([_("from {0}").format(t) for t in times], times.index(at),
                                      lambda i, k=at_key: self.save_schedule(**{k: times[i]})))
            self.setting_line(box, label, "", line)

        box = self.settings_section("wallpapers", _("Wallpapers"),
                                    _("Scroll on the bar icon for the next or previous wallpaper; right-click it for your "
                                      "favorite themes."))
        self.setting_line(box, _("A wallpaper for each workspace"),
                          _("Choose workspaces in a wallpaper's ☰ menu → Show on workspace. Workspaces "
                          "without one keep the theme's background."),
                          self.switch(theme_auto.workspace_wallpapers_enabled(),
                                      lambda on: self.save_setting(workspace_wallpapers=on)))
        self.setting_line(box, _("A theme for each workspace"),
                          _("Switches Omarchy's global theme when you focus a workspace with an assigned theme.")
                          + " " + _("Switching takes a moment and changes every monitor. Leaving the workspace goes back to the theme you used before."),
                          self.switch(theme_auto.workspace_themes_enabled(),
                                      lambda on: self.save_setting(workspace_themes=on)))
        labels = [_("Never"), _("Every 5 minutes"), _("Every 10 minutes"), _("Every 15 minutes"), _("Every 30 minutes"),
                  _("Every hour"), _("Every 2 hours")]
        self.setting_line(box, _("Change the wallpaper"),
                          _("Goes through the current theme's visible wallpapers."),
                          self.dropdown(labels, theme_auto.SLIDESHOW_CHOICES.index(theme_auto.slideshow_minutes()),
                                        lambda i: self.save_setting(
                                            slideshow_minutes=theme_auto.SLIDESHOW_CHOICES[i])))

        box = self.settings_section("shortcuts", _("Keyboard shortcuts"),
                                    _("Give a theme its own key in its ⋮ menu → Shortcut…. Keys live in "
                                      "~/.config/hypr/theme-manager-shortcuts.lua; removing the last one removes that file "
                                      "and its line in hyprland.lua again."))
        state = shortcuts.load_state()
        for action in shortcuts.ACTIONS:
            self.shortcut_setting_row(box, action, state.get(action))
        themes = {t.slug: t for t in theme_core.discover_themes()}
        theme_keys = sorted((a for a in state if shortcuts.theme_of(a) in themes),
                            key=lambda a: shortcuts.action_label(a).casefold())
        for action in theme_keys:
            self.shortcut_setting_row(box, action, state[action])
        box = self.settings_section("screenshots", _("Screenshots"))
        self.setting_line(box, _("F12 saves a screenshot"),
                          _("Save a picture of the Theme Manager window while it is open."),
                          self.switch(screenshots.key_enabled(theme_auto.settings()),
                                      lambda on: self.save_setting(screenshot_key=on)))
        folder = screenshots.folder_from_settings(theme_auto.settings())
        folder_row = Gtk.Box(spacing=6)
        folder_label = Gtk.Label(label=str(folder), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        folder_label.set_max_width_chars(30)
        folder_label.set_tooltip_text(str(folder))
        folder_row.append(folder_label)
        if theme_auto.settings().get("screenshot_folder"):
            default = Gtk.Button(label=_("Default"))
            default.add_css_class("sc-small")
            default.connect("clicked", lambda *_: (self.save_setting(screenshot_folder=None),
                                                   GLib.idle_add(self.fill_settings)))
            folder_row.append(default)
        choose = Gtk.Button(icon_name="folder-open-symbolic")
        choose.add_css_class("sc-small")
        choose.set_tooltip_text(_("Choose folder…"))
        choose.update_property([Gtk.AccessibleProperty.LABEL], [_('Choose screenshot folder')])
        choose.connect("clicked", lambda *_: self.choose_screenshot_folder())
        folder_row.append(choose)
        self.setting_line(box, _("Screenshots go to"), str(folder), folder_row)
        box = self.settings_section("ask-ai", _("Ask AI"))
        ready = theme_ai.ready_helpers()
        self.setting_line(box, _("Ask AI"), _(ask_page.DESCRIPTION),
                          self.switch(ask_page.enabled(theme_auto.settings()), self.set_ask_ai))
        found = ", ".join(item["name"] + (" (" + _("on this PC") + ")" if item.get("local") else "")
                          for item in ready) if ready else _(ask_page.NO_HELPER)
        note = Gtk.Label(label=_("Helpers: {0}").format(found), xalign=0, wrap=True, max_width_chars=1, hexpand=True)
        note.add_css_class("tm-hint")
        hints = ask_page.maprisma_hints(theme_auto.settings())
        hint = self.maprisma_hint()
        hint.set_visible(False)                  # on: the ? next to the helpers opens it; off: neither
        line = Gtk.Box(spacing=6)
        line.append(note)
        if hints:
            line.append(self.maprisma_help_button(lambda: hint))
        box.append(line)
        self.setting_line(box, _("Search the web for wallpapers"),
                          _("A helper that can search the web (now Claude Code) also looks for wallpaper pictures "
                            "beyond wallhaven.cc. It takes longer and uses more of your plan or tokens. The pictures "
                            "are downloaded only from public https addresses and checked before they are shown."),
                          self.switch(theme_auto.settings().get("ask_ai_web") is not False,   # on by default
                                      lambda on: self.save_setting(ask_ai_web=on)))
        self.setting_line(box, _("Maprisma hints"),
                          _("A ? here and on the Ask AI page opens the note on easy AI setup with Maprisma."),
                          self.switch(hints, self.set_maprisma_hints))
        box.append(hint)
        box = self.settings_section("integration", _("Integration and backup"))
        health = theme_health.inspect()
        status = (_("Ready") if health["enabled"] and health["menu"] and health["hook"]
                  and not health["leaked"] else _("Needs attention") if health["enabled"] else _("Off"))
        problems = []
        if health["enabled"]:
            if not health["menu"]:
                problems.append(_("Menu line missing"))
            if not health["hook"]:
                problems.append(_("Theme hook missing"))
            if health["menu_custom"]:
                problems.append(_("Your own style.theme override is in omarchy-menu.jsonc"))
            if health["leaked"]:
                problems.append(_("{0} hidden themes are still in the picker").format(len(health["leaked"])))
        health_hint = "; ".join(problems) if problems else status
        repair = Gtk.Button(label=_("Repair sync"))
        repair.add_css_class("sc-small")
        repair.set_sensitive(health["enabled"])
        if not health["enabled"]:
            repair.set_tooltip_text(_("Turn on ‘Hidden in Omarchy's pickers’ first."))
        repair.connect("clicked", lambda *_: self.repair_sync())
        self.setting_line(box, _("Omarchy picker sync"), health_hint, repair)
        backup_actions = Gtk.Box(spacing=6)
        export = Gtk.Button(label=_("Export…"))
        export.add_css_class("sc-small")
        export.connect("clicked", lambda *_: self.export_backup())
        backup_actions.append(export)
        restore = Gtk.Button(label=_("Restore…"))
        restore.add_css_class("sc-small")
        restore.connect("clicked", lambda *_: self.choose_backup())
        backup_actions.append(restore)
        self.setting_line(box, _("Backup and restore"),
                          _("Includes settings, lists, workspace assignments, presets and shortcuts; themes and wallpapers remain in their folders."),
                          backup_actions)
        no_match = Gtk.Label(label=_("No setting matches your search."), xalign=0)
        no_match.add_css_class("tm-hint")
        self.settings_box.append(no_match)
        self.settings_no_match = no_match
        self.filter_settings()
        self.settings_ready = True

    def repair_sync(self):
        self._async(theme_health.repair, lambda _result, error: (
            theme_core.notify(_("Sync repair failed") if error else _("Sync repaired"), str(error) if error else ""),
            self.fill_settings()))

    def export_backup(self):
        dialog = Gtk.FileDialog(title=_("Export Theme Manager backup"), modal=False)
        dialog.set_initial_name(theme_backup.default_export_name())
        self.window.hide()
        def chosen(source, result):
            try:
                saved = source.save_finish(result)
            except GLib.Error:
                self.window.present()
                return
            self.window.present()
            if saved.get_path() is None:
                theme_core.notify(_("Backup failed"), _("Choose a local folder"))
                return
            target = Path(saved.get_path())
            self._async(lambda: theme_backup.export(target), lambda _value, error: theme_core.notify(
                _("Backup failed") if error else _("Backup saved"), str(error) if error else str(target)))
        dialog.save(None, None, chosen)

    def choose_backup(self):
        dialog = Gtk.FileDialog(title=_("Restore Theme Manager backup"), modal=False)
        self.window.hide()
        def chosen(source, result):
            try:
                opened = source.open_finish(result)
            except GLib.Error:
                self.window.present()
                return
            self.window.present()
            if opened.get_path() is None:
                theme_core.notify(_("Backup invalid"), _("Choose a local file"))
                return
            target = Path(opened.get_path())

            def previewed(_files, error):
                # Read and checked on a worker thread: a FIFO or a large file never holds the popup.
                if error is not None:
                    theme_core.notify(_("Backup invalid"), self.backup_error_text(error))
                    return
                try:
                    previous = theme_backup.safety_copy_path()
                except OSError as failure:
                    theme_core.notify(_("Backup failed"), str(failure))
                    return
                self.ask_confirm(_("Replace your settings, favorites, hidden themes, workspace assignments, presets and shortcuts with this backup? Your current state is saved first as {0}.").format(previous.name),
                                 _("Restore"), lambda: self.restore_backup(target, previous))
            self._async(lambda: theme_backup.preview(target), previewed)
        dialog.open(None, None, chosen)

    @staticmethod
    def backup_error_text(error):
        known = {
            'unsupported backup format': _('Unsupported backup format.'),
            'invalid backup files': _('Invalid backup files.'),
            'invalid presets': _('Invalid presets in backup.'),
            'invalid shortcuts': _('Invalid shortcuts in backup.'),
        }
        return known.get(str(error), str(error))

    def restore_backup(self, target, previous):
        old_language = i18n.language_setting()
        old_size = theme_core.window_size()
        def done(previous, error):
            theme_core.notify(_("Restore failed") if error else _("Backup restored"),
                              self.backup_error_text(error) if error else str(previous))
            if error is None and (old_language != i18n.language_setting() or old_size != theme_core.window_size()):
                os.environ["THEME_MANAGER_PAGE"] = "settings"
                sys.stdout.flush()
                sys.stderr.flush()
                os.execv(sys.executable, [sys.executable, *sys.argv])
            self.hidden = self.load_hidden_list()
            self.save_header_order_live(header_order.normalize(theme_auto.settings().get("header_order")))
            self.fill_settings()
            self.refresh_themes()
            self.refresh_presets()
        self._async(lambda: theme_backup.restore(target, previous), done)

    def shortcut_setting_row(self, box, action: str, combo):
        line = Gtk.Box(spacing=6)
        if combo:
            line.append(self.keycaps(combo))
        button = Gtk.Button(label=_("Change…") if combo else _("Set…"))
        button.add_css_class("tm-paste")
        button.add_css_class("sc-small")
        button.connect("clicked", lambda *_: self.open_shortcut_page(action))
        line.append(button)
        slug = shortcuts.theme_of(action)
        note = "" if combo else _("No key yet")
        if combo and slug and slug in self.hidden:
            note = _("Hidden theme: the key still works and unhides it")
        self.setting_line(box, shortcuts.action_label(action), note, line)

    # --- shortcut page (like Launch & Bind) --------------------------------------------------

    def keycaps(self, combo, large: bool = False) -> Gtk.Box:
        """[Super] [Ctrl] [T]"""
        caps = Gtk.Box(spacing=4)
        caps.set_valign(Gtk.Align.CENTER)
        for part in shortcuts.combo_label(combo).split(" + ") if combo else ["?"]:
            cap = Gtk.Label(label=part)
            cap.add_css_class("sc-cap")
            if large:
                cap.add_css_class("sc-cap-lg")
            caps.append(cap)
        return caps

    def build_shortcut_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = self.back_button()
        back.set_tooltip_text(_("Back (Esc)"))
        back.connect("clicked", lambda *_: self.close_shortcut_page())
        header.append(back)
        self.sc_title = Gtk.Label(xalign=0, hexpand=True)
        self.sc_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.sc_title.set_max_width_chars(1)
        self.sc_title.add_css_class("tm-title")
        header.append(self.sc_title)
        page.append(header)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("tm-settings")
        scroller.set_child(box)
        page.append(scroller)

        def section(text):
            label = Gtk.Label(label=text, xalign=0)
            label.add_css_class("tm-setting-heading")
            box.append(label)
            return label

        section(_("Current key"))
        self.sc_current = Gtk.Box(spacing=8)
        box.append(self.sc_current)
        section(_("Choose a key"))
        hint = Gtk.Label(label=_("Hold the modifiers and press a key, or toggle modifiers and click a key below."),
                         xalign=0, wrap=True, max_width_chars=1)
        hint.add_css_class("tm-hint")
        box.append(hint)
        mods_row = Gtk.Box(spacing=6, homogeneous=True)
        self.sc_toggles = {}
        for mod in shortcuts.MOD_ORDER:
            toggle = Gtk.ToggleButton(label=shortcuts.MOD_LABELS[mod])
            toggle.add_css_class("sc-mod")
            toggle.connect("toggled", lambda *_: not self.sc_syncing and self.update_shortcut_status())
            mods_row.append(toggle)
            self.sc_toggles[mod] = toggle
        box.append(mods_row)
        key_row = Gtk.Box(spacing=8)
        self.sc_key_box = Gtk.Box(hexpand=True)
        self.sc_key_box.add_css_class("sc-key")
        key_row.append(self.sc_key_box)
        self.sc_apply = Gtk.Button(label=_("Assign"))
        self.sc_apply.add_css_class("tm-paste")
        self.sc_apply.add_css_class("suggested")
        self.sc_apply.connect("clicked", lambda *_: self.apply_shortcut())
        key_row.append(self.sc_apply)
        box.append(key_row)
        self.sc_status = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        self.sc_status.add_css_class("sc-status")
        box.append(self.sc_status)
        self.sc_letters_title = section(_("Keys"))
        legend = Gtk.Box(spacing=10)
        for text, css in ((_("● free"), "sc-free-key"), (_("● taken (can be rebound)"), "sc-yours-key"),
                          (_("● Omarchy default"), "sc-omarchy-key")):
            item = Gtk.Label(label=text)
            item.add_css_class("sc-legend")
            item.add_css_class(css)
            legend.append(item)
        box.append(legend)
        self.sc_letters = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                      column_spacing=4, row_spacing=4, min_children_per_line=7,
                                      max_children_per_line=13)
        box.append(self.sc_letters)
        section(_("Other keys"))
        self.sc_others = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                     column_spacing=4, row_spacing=4, min_children_per_line=4,
                                     max_children_per_line=9)
        box.append(self.sc_others)
        self.sc_action = None
        self.sc_key = None
        self.sc_syncing = False
        self.sc_return = "themes"
        return page

    # --- Font & effects (theme_looks) ------------------------------------------------------

    WINDOW_LABELS = {"opaque": N_("Opaque"), "transparent": N_("Transparent"), "glassy": N_("Glassy (blur)")}
    LOOK_FIELDS = {"mono_font": N_("font"), "windows": N_("window style"), "text_color": N_("text colour")}

    def build_look_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = self.back_button()
        back.set_tooltip_text(_("Back (Esc)"))
        back.connect("clicked", lambda *_: self.close_look_page())
        header.append(back)
        self.lk_title = Gtk.Label(xalign=0, hexpand=True)
        self.lk_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.lk_title.set_max_width_chars(1)
        self.lk_title.add_css_class("tm-title")
        header.append(self.lk_title)
        page.append(header)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("tm-settings")
        scroller.set_child(box)
        page.append(scroller)
        hint = Gtk.Label(label=_("Used whenever this theme is applied; the theme files stay as they are. "
                                 "Themes without a choice give your own font and window style back."),
                         xalign=0, wrap=True, max_width_chars=1)
        hint.add_css_class("tm-hint")
        box.append(hint)
        self.lk_font = Gtk.DropDown.new_from_strings([""])
        self.lk_font.connect("notify::selected", lambda *_: self.update_look_buttons())
        self.settings_row(box, _("Font"), _("Omarchy's monospace font: terminals, the bar and menus"), self.lk_font)
        self.lk_windows = Gtk.DropDown.new_from_strings([""])
        self.lk_windows.connect("notify::selected", lambda *_: self.update_look_buttons())
        self.lk_windows_row = self.settings_row(
            box, _("Windows"), _("Glassy blurs what is behind see-through windows, the bar and menus, "
                                 "and gives terminals a see-through background"), self.lk_windows)
        # Text colour: a swatch with the colour chooser and the theme's own back;
        # a warning when it is hard to read, and a sample in the theme's colours.
        text = Gtk.Box(spacing=6)
        self.lk_text_swatch = Gtk.Button()
        self.lk_text_swatch.add_css_class("tm-swatch")
        self.lk_text_swatch.add_css_class("tm-lk-text-swatch")
        self.lk_text_swatch.set_size_request(34, 26)
        self.lk_text_swatch.set_valign(Gtk.Align.CENTER)
        self.lk_text_swatch.set_tooltip_text(_("Pick a color"))
        self.lk_text_swatch.update_property([Gtk.AccessibleProperty.LABEL], [_("Text colour")])
        self.lk_text_swatch.connect("clicked", lambda button: self.show_look_text_picker(button))
        text.append(self.lk_text_swatch)
        self.lk_text_own = Gtk.Button(label=_("Theme's own"))
        self.lk_text_own.add_css_class("sc-small")
        self.lk_text_own.set_valign(Gtk.Align.CENTER)
        self.lk_text_own.connect("clicked", lambda *_: self.set_look_text(None))
        text.append(self.lk_text_own)
        self.lk_text_row = self.settings_row(box, _("Text colour"), " ", text)
        self.lk_text_hint = self.lk_text_row.get_first_child().get_last_child()
        self.lk_text_warn = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        self.lk_text_warn.add_css_class("tm-hint")
        self.lk_text_warn.add_css_class("tm-lk-warn")
        box.append(self.lk_text_warn)
        self.lk_sample = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.lk_sample.add_css_class("tm-lk-sample")
        for line, css in ((_("The quick brown fox jumps over the lazy dog"), "s-text"),
                          ("~/.config/omarchy $ ls", "s-text"), (_("a comment, dimmer"), "s-muted")):
            label = Gtk.Label(label=line, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
            label.add_css_class(css)
            self.lk_sample.append(label)
        box.append(self.lk_sample)
        self.lk_text = None
        self.lk_provider = None
        self.lk_status = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        self.lk_status.add_css_class("tm-hint")
        box.append(self.lk_status)
        answers = Gtk.Box(spacing=12)
        answers.set_halign(Gtk.Align.END)
        self.lk_save = Gtk.Button(label=_("Apply"))
        self.lk_save.add_css_class("tm-paste")
        self.lk_save.connect("clicked", lambda *_: self.save_look())
        answers.append(self.lk_save)
        box.append(answers)
        self.lk_data = None
        self.lk_fonts: list = []
        self.lk_modes: list = []
        self.lk_busy = False
        self.lk_return = "themes"
        return page

    def open_look_page(self, theme: theme_core.ThemeInfo):
        self.lk_return = self.stack.get_visible_child_name() or "themes"
        self.lk_data = None
        self.lk_busy = True
        self.lk_slug = theme.slug
        self.lk_title.set_label(_("Font & effects — {0}").format(theme_core.plain_text(theme.name)))
        self.lk_status.set_label(_("Loading…"))
        for widget in (self.lk_font, self.lk_windows, self.lk_save, self.lk_text_swatch, self.lk_text_own):
            widget.set_sensitive(False)
        self.stack.set_visible_child_name("look")
        self.lk_save.grab_focus()
        slug = theme.slug
        self._async(lambda: theme_looks.look_data(slug), lambda data, error: self.fill_look_page(slug, data, error))

    def fill_look_page(self, slug: str, data, error):
        if self.stack.get_visible_child_name() != "look" or slug != getattr(self, "lk_slug", None):
            return                  # the page was left, or opened again for another theme
        self.lk_busy = False
        if error is not None:
            self.lk_status.set_label(_("Could not read the theme's look: {0}").format(error))
            return
        self.lk_data = data
        look, own, themed = data["look"], data["own"], data["theme"]

        def default_label(key, describe):
            if themed.get(key) is not None:
                return _("Theme default ({0})").format(describe(themed[key]))
            if own.get(key) is not None:
                return _("Theme default (yours: {0})").format(describe(own[key]))
            return _("Theme default")

        fonts = list(data["fonts"])
        chosen = look.get("mono_font")
        if chosen and chosen not in fonts:
            fonts.insert(0, chosen)        # a saved font no longer installed stays until another is chosen
        self.lk_fonts = [None, *fonts]
        labels = [default_label("mono_font", str)] + [
            _("{0} (not installed)").format(f) if f not in data["fonts"] else f for f in fonts]
        self.lk_font.set_model(Gtk.StringList.new(labels))
        self.lk_font.set_selected(self.lk_fonts.index(chosen) if chosen in self.lk_fonts else 0)
        windows = "windows" in data["caps"]
        self.lk_windows_row.set_visible(windows)
        mode_label = lambda mode: _(self.WINDOW_LABELS[mode])
        self.lk_modes = [None, *theme_looks.WINDOW_MODES]
        self.lk_windows.set_model(Gtk.StringList.new(
            [default_label("windows", mode_label)] + [mode_label(m) for m in theme_looks.WINDOW_MODES]))
        mode = look.get("windows")
        self.lk_windows.set_selected(self.lk_modes.index(mode) if mode in self.lk_modes else 0)
        self.lk_font.set_sensitive(True)
        self.lk_windows.set_sensitive(windows)
        text = "text_color" in data["caps"] and {"background", "foreground"} <= set(data.get("colors", {}))
        for widget in (self.lk_text_row, self.lk_text_warn, self.lk_sample):
            widget.set_visible(text)
        self.lk_text = look.get("text_color")
        self.lk_text_swatch.set_sensitive(text)
        if text:
            self.update_look_text()
        self.update_look_buttons()

    def look_text_color(self) -> str:
        return self.lk_text or self.lk_data["colors"]["foreground"]

    def show_look_text_picker(self, button):
        def build(popover):
            box = self.menu_box()
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                chooser = Gtk.ColorChooserWidget(use_alpha=False)
            self.guard_text_fields_in(chooser)
            color = Gdk.RGBA()
            color.parse(self.look_text_color())
            chooser.set_rgba(color)

            def use(*_):
                rgba = chooser.get_rgba()
                value = "#" + "".join(f"{round(c * 255):02x}" for c in (rgba.red, rgba.green, rgba.blue))
                popover.popdown()
                self.set_look_text(value)
            chooser.connect("color-activated", use)
            box.append(chooser)
            done = Gtk.Button(label=_("Use as text colour"))
            done.add_css_class("tm-paste")
            done.connect("clicked", use)
            box.append(done)
            return box
        self.show_menu(button, build)

    def set_look_text(self, value):
        data = self.lk_data
        if data is None or self.lk_busy or "foreground" not in data.get("colors", {}):
            return
        own = data["colors"]["foreground"].lower()
        value = value.lower() if isinstance(value, str) and theme_custom.HEX_RE.match(value) else None
        self.lk_text = None if value == own else value     # the theme's own colour is no override
        self.update_look_text()
        self.update_look_buttons()

    def update_look_text(self):
        """Swatch, hint, contrast warning and sample in the colour chosen."""
        colors, text = self.lk_data["colors"], self.look_text_color()
        self.lk_text_hint.set_label(_("{0} for this theme").format(text) if self.lk_text else
                                    _("Theme's own: {0}").format(colors["foreground"]))
        self.lk_text_own.set_sensitive(self.lk_text is not None)
        self.lk_text_own.set_tooltip_text(_("The theme's own text colour, {0}").format(colors["foreground"]))
        ratio = theme_looks.contrast_ratio(text, colors["background"])
        self.lk_text_warn.set_visible(ratio < theme_looks.MIN_CONTRAST)
        self.lk_text_warn.set_label(_("Hard to read on this theme's background: contrast {0}:1, at least 3:1 is "
                                      "better.").format(f"{ratio:.1f}"))
        muted = colors.get("muted") or colors["foreground"]
        radius = corners.radius(theme_auto.settings().get("corners"), getattr(self, "window_rounding", None))
        small, control = corners.small(radius), corners.control(radius)
        css = "\n".join((
            f".tm-lk-text-swatch {{ background: {text}; }}",
            f"button.tm-swatch {{ border: 1px solid alpha(currentColor, 0.4); border-radius: {small}px;"
            " min-height: 0; min-width: 0; padding: 0; }",
            f".tm-lk-sample {{ background: {colors['background']}; border-radius: {control}px; padding: 8px 12px; }}",
            f".tm-lk-sample label.s-text {{ color: {text}; }}",
            f".tm-lk-sample label.s-muted {{ color: {muted}; }}",
            f".tm-lk-warn {{ color: {self.theme.colors.get('red', '#f04452')}; }}",
        ))
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        display = Gdk.Display.get_default()
        if self.lk_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.lk_provider)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.lk_provider = provider

    def look_edited(self) -> dict:
        edited = {}
        font = self.lk_fonts[self.lk_font.get_selected()] if self.lk_font.get_selected() < len(self.lk_fonts) else None
        mode = self.lk_modes[self.lk_windows.get_selected()] if self.lk_windows.get_selected() < len(self.lk_modes) \
            else None
        if font:
            edited["mono_font"] = font
        if mode and "windows" in self.lk_data["caps"]:
            edited["windows"] = mode
        if "windows" not in self.lk_data["caps"] and self.lk_data["look"].get("windows"):
            edited["windows"] = self.lk_data["look"]["windows"]    # kept for an Omarchy that has it
        if self.lk_text:
            edited["text_color"] = self.lk_text
        return edited

    def update_look_buttons(self):
        data = self.lk_data
        if data is None or self.lk_busy:
            return
        changed = self.look_edited() != data["look"]
        self.lk_save.set_sensitive(changed)               # one button: enabled once something changed
        self.lk_save.set_tooltip_text(_("Saves the change and puts it on the desktop now") if data["in_use"] else
                                      _("Saves the change; it is used whenever this theme is applied"))
        if data["in_use"] and data["pending"] and not changed:
            self.lk_status.set_label(_("Not on the desktop yet: {0}").format(self.look_field_names(data["pending"])))
        else:
            self.lk_status.set_label("")

    def look_field_names(self, keys) -> str:
        return ", ".join(_(self.LOOK_FIELDS[k]) for k in keys if k in self.LOOK_FIELDS)

    def save_look(self):
        """Stores the change; the theme in use gets it on the desktop at once."""
        data = self.lk_data
        if data is None or self.lk_busy:
            return
        slug, name = data["slug"], theme_core.plain_text(data["name"])
        edited = self.look_edited()
        if edited == data["look"]:
            return
        apply = data["in_use"]
        self.lk_busy = True
        self.lk_save.set_sensitive(False)

        def work():
            previous = theme_looks.set_look(slug, edited)
            applied = theme_looks.apply_look(slug) if apply else []
            return previous, applied, theme_looks.pending(slug)

        def done(result, error):
            self.lk_busy = False
            if error is not None:
                theme_core.notify(_("Could not save Font & effects"), f"{name}: {error}")
                self.update_look_buttons()     # the edits stay for another try
                return
            previous, _applied, pending = result
            self.look_pending = {slug: pending} if data["in_use"] else self.look_pending
            text = _("Font & effects saved and applied") if apply else _("Font & effects saved for {0}").format(name)
            theme_core.notify(text, name)
            self.close_look_page()
            self.offer_undo(text, [LookUndo(self, slug, previous, apply)])
        self._async(work, done)

    def close_look_page(self):
        if self.stack.get_visible_child_name() != "look":
            return
        self.lk_data = None
        self.lk_slug = None
        back = self.lk_return
        if back == "settings":
            self.fill_settings()
            self.stack.set_visible_child_name("settings")
            return
        self.show_themes()

    def refresh_look_pending(self):
        """Whether the theme in use has a font or window style not on the
        desktop yet (its menu offers Reapply then); off the main loop."""
        slug = theme_core.current_theme()

        def done(keys, error):
            self.look_pending = {slug: keys} if error is None and keys else {}
        self._async(lambda: theme_looks.pending(slug), done)

    def reapply_look(self, theme: theme_core.ThemeInfo):
        def done(_applied, error):
            if error is not None:
                theme_core.notify(_("Could not reapply"), f"{theme_core.plain_text(theme.name)}: {error}")
            else:
                theme_core.notify(_("Reapplied: {0}").format(theme_core.plain_text(theme.name)), "")
            self.refresh_look_pending()
        self._async(lambda: theme_looks.apply_look(theme.slug), done)

    def open_shortcut_page(self, action: str):
        self.sc_return = self.stack.get_visible_child_name() or "themes"
        self.sc_action = action
        self.sc_state = shortcuts.load_state()
        self.sc_bindings = shortcuts.Bindings(self.sc_state)
        self.sc_title.set_label(_("Shortcut — {0}").format(shortcuts.action_label(action)))
        self.render_shortcut_current()
        current = self.sc_state.get(action)
        self.set_shortcut_combo(current["mods"] if current else shortcuts.DEFAULT_MODS, None)
        self.stack.set_visible_child_name("shortcut")
        # Bound combos must reach this page instead of running.
        shortcuts.enter_capture()
        try:
            self.window.get_surface().inhibit_system_shortcuts(None)
        except Exception:  # noqa: BLE001 - not every compositor offers it
            pass
        self.sc_apply.grab_focus()

    def close_shortcut_page(self):
        if self.sc_action is None:
            return
        self.sc_action = None
        self.leave_shortcut_capture()
        back = self.sc_return
        if back == "settings":
            self.fill_settings()
        self.stack.set_visible_child_name(back if back in ("settings", "themes") else "themes")
        if back != "settings":
            self.refresh_themes()
            self.select_row(self.rows.get_selected_row())

    def leave_shortcut_capture(self):
        shortcuts.leave_capture()
        try:
            self.window.get_surface().restore_system_shortcuts()
        except Exception:  # noqa: BLE001
            pass

    def render_shortcut_current(self):
        box = self.sc_current
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()
        combo = self.sc_state.get(self.sc_action)
        if combo is None:
            none = Gtk.Label(label=_("None yet — choose one below."), xalign=0)
            none.add_css_class("tm-hint")
            box.append(none)
            return
        box.append(self.keycaps(combo, large=True))
        if combo.get("replaces"):
            note = Gtk.Label(label=_("overrides “{0}”").format(combo['replaces']), xalign=0, hexpand=True)
            note.add_css_class("tm-hint")
            note.set_ellipsize(Pango.EllipsizeMode.END)
            note.set_max_width_chars(1)
            box.append(note)
        else:
            box.append(Gtk.Box(hexpand=True))
        remove = Gtk.Button(label=_("Remove"))
        remove.add_css_class("tm-paste")
        remove.set_tooltip_text(_("Give the key back to “{0}”").format(combo['replaces']) if combo.get("replaces") else _("Give the key back"))
        remove.connect("clicked", lambda *_: self.remove_shortcut())
        box.append(remove)

    def selected_mods(self) -> list[str]:
        return [mod for mod, toggle in self.sc_toggles.items() if toggle.get_active()]

    def set_shortcut_combo(self, mods, key):
        self.sc_syncing = True
        for mod, toggle in self.sc_toggles.items():
            toggle.set_active(mod in mods)
        self.sc_syncing = False
        self.sc_key = key
        self.update_shortcut_status()

    def current_combo(self):
        return shortcuts.make_combo(self.selected_mods(), self.sc_key) if self.sc_key else None

    def update_shortcut_status(self):
        mods = self.selected_mods()
        combo = self.current_combo()
        box = self.sc_key_box
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()
        if combo is not None:
            box.append(self.keycaps(combo, large=True))
        else:
            box.append(self.keycaps({"mods": mods, "key": "?"} if mods else None, large=True))
        level, who = self.sc_bindings.classify(combo, self.sc_action) if combo else (None, "")
        for css in ("sc-free", "sc-warn", "sc-omarchy"):
            self.sc_status.remove_css_class(css)
        label, enabled = _("Assign"), combo is not None
        if combo is None:
            text = _("Press a key.") if mods else _("Choose modifiers (Super, Shift, Ctrl, Alt), then a key.")
        elif not mods:
            text, enabled = _("A shortcut needs at least one modifier."), False
        elif level == shortcuts.FREE:
            text = _("{0} is free.").format(shortcuts.combo_label(combo))
            self.sc_status.add_css_class("sc-free")
        elif level == shortcuts.SAME:
            text, enabled = _("{0} already does this.").format(shortcuts.combo_label(combo)), False
        elif level == shortcuts.OURS:
            text, label = _("{0} is the key for “{1}”: it moves here.").format(shortcuts.combo_label(combo), who), _("Move here")
            self.sc_status.add_css_class("sc-warn")
        elif level == shortcuts.CUSTOM:
            text, label = _("{0} is your binding “{1}”. Rebinding turns it off while this shortcut exists.").format(
                shortcuts.combo_label(combo), who), _("Rebind")
            self.sc_status.add_css_class("sc-warn")
        else:
            text, label = _("{0} is Omarchy's “{1}”. Rebinding turns it off while this shortcut exists.").format(
                shortcuts.combo_label(combo), who), _("Rebind")
            self.sc_status.add_css_class("sc-omarchy")
        self.sc_status.set_label(text)
        self.sc_apply.set_label(label)
        self.sc_apply.set_sensitive(enabled)
        self.render_key_chips(mods)

    def render_key_chips(self, mods):
        for grid, keys in ((self.sc_letters, "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"), (self.sc_others, OTHER_KEYS)):
            child = grid.get_first_child()
            while child is not None:
                grid.remove(child)
                child = grid.get_first_child()
            for key in keys:
                combo = shortcuts.make_combo(mods, key)
                chip = Gtk.Button(label=shortcuts.key_label(key))
                chip.add_css_class("sc-keychip")
                if mods:
                    level, who = self.sc_bindings.classify(combo, self.sc_action)
                    chip.add_css_class(KEY_STYLES.get(level, "sc-free-key"))
                    name = shortcuts.combo_label(combo)
                    chip.set_tooltip_text(_("{0} — free").format(name) if level == shortcuts.FREE else
                                          _("{0} — already this shortcut").format(name) if level == shortcuts.SAME else
                                          _("{0} — “{1}”; click to rebind").format(name, who))
                else:
                    chip.set_tooltip_text(_("Choose modifiers first"))
                if self.sc_key and key.casefold() == self.sc_key.casefold():
                    chip.add_css_class("sc-key-picked")
                chip.connect("clicked", lambda *_, k=key: self.pick_shortcut_key(k))
                grid.append(chip)
        self.sc_letters_title.set_label(
            _("Keys with {0}").format(" + ".join(shortcuts.MOD_LABELS[m] for m in mods)) if mods else _("Keys"))

    def pick_shortcut_key(self, key):
        same = self.sc_key is not None and key.casefold() == self.sc_key.casefold()
        self.set_shortcut_combo(self.selected_mods(), None if same else key)

    def on_shortcut_key(self, controller, keyval, keycode, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close_shortcut_page()
            return True
        any_mod = (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.SHIFT_MASK
                   | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter) and not state & any_mod:
            if self.sc_apply.get_sensitive():
                self.apply_shortcut()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return False
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                      Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Meta_L,
                      Gdk.KEY_Meta_R, Gdk.KEY_Hyper_L, Gdk.KEY_Hyper_R, Gdk.KEY_ISO_Level3_Shift,
                      Gdk.KEY_Caps_Lock, Gdk.KEY_Num_Lock):
            return True
        base = Gdk.keyval_to_lower(keyval)
        try:
            # The key itself, not what Shift makes of it (Shift + 1 is "1", not "!").
            ok, keys, keyvals = self.window.get_display().map_keycode(keycode)
            group = controller.get_group() if controller is not None else 0
            if ok:
                for entry, value in zip(keys, keyvals):
                    if entry.level == 0 and entry.group == group:
                        base = value
                        break
        except Exception:  # noqa: BLE001
            pass
        name = Gdk.keyval_name(base) or ""
        if shortcuts.make_combo([], name) is None:
            self.sc_status.set_label(_("That key cannot be used for a shortcut."))
            return True
        held = {"SUPER": Gdk.ModifierType.SUPER_MASK, "SHIFT": Gdk.ModifierType.SHIFT_MASK,
                "CTRL": Gdk.ModifierType.CONTROL_MASK, "ALT": Gdk.ModifierType.ALT_MASK}
        mods = [mod for mod, mask in held.items() if state & mask] or self.selected_mods()
        self.set_shortcut_combo(mods, name)
        return True

    def apply_shortcut(self):
        combo = self.current_combo()
        if combo is None or self.sc_action is None:
            return
        level, who = self.sc_bindings.classify(combo, self.sc_action)
        state = dict(self.sc_state)
        if level == shortcuts.OURS:
            state = {a: c for a, c in state.items() if shortcuts.combo_id(c) != shortcuts.combo_id(combo)}
        elif level in (shortcuts.CUSTOM, shortcuts.OMARCHY):
            combo["replaces"] = theme_core.plain_text(who, 80)
        state[self.sc_action] = combo
        self.save_shortcuts(state, f"{shortcuts.combo_label(combo)}: {shortcuts.action_label(self.sc_action)}")

    def remove_shortcut(self):
        state = {a: c for a, c in self.sc_state.items() if a != self.sc_action}
        self.save_shortcuts(state, _("{0}: no key").format(shortcuts.action_label(self.sc_action)))

    def save_shortcuts(self, state, done_text):
        self.sc_apply.set_sensitive(False)
        self.sc_status.set_label(_("Saving and reloading Hyprland…"))
        # Hyprland reloads its config: leave the capture submap first.
        self.leave_shortcut_capture()

        def after(_result, error):
            if self.sc_action is None:
                return
            shortcuts.enter_capture()
            if error is not None:
                self.sc_status.set_label(_("Hyprland refused the change, nothing was changed: {0}").format(error))
                self.update_shortcut_status()
                return
            theme_core.notify(_("Shortcut saved"), done_text)
            self.sc_state = shortcuts.load_state()
            self.sc_bindings = shortcuts.Bindings(self.sc_state)
            self.render_shortcut_current()
            self.update_shortcut_status()
            self.sc_status.set_label(done_text)
        self._async(lambda: shortcuts.save_and_apply(state), after)

    def refresh_shortcuts(self):
        """Theme folders changed (deleted, restored): write the keys again,
        so a deleted theme's key does nothing and a restored one works."""
        if shortcuts.load_state():
            self._async(lambda: shortcuts.save_and_apply(shortcuts.load_state()), lambda *_: None)

    def flip_active_bg(self, theme, wallpapers):
        """The wallpaper a card shows as pinned: the desktop's for the theme in use; on another
        theme's card (pinning without applying) only the one Omarchy really remembers for it
        (flip_wallpapers puts it first), never a first wallpaper that was never chosen."""
        if theme.current:
            return self.current_bg
        if self.pin_apply_theme or not wallpapers:
            return None
        remembered = theme_auto.remembered_omarchy_theme_background(theme.slug)
        return wallpapers[0] if remembered and Path(wallpapers[0]).name == remembered else None

    FLIP_SETTINGS = ("preview_flip_3d", "preview_flip_delay", "preview_pin_apply_theme", "preview_big_card")

    def save_setting(self, **values):
        if any(key in values for key in self.FLIP_SETTINGS):
            # The 3D flip's settings: written by one settings worker in order (the shared lock may
            # wait), the cards rebuilt once it is stored, never a wait on GTK's loop.
            self._save_in_background(values)
            return
        try:
            theme_auto.update_settings(**values)
        except OSError as error:
            theme_core.notify(_("Could not save the setting"), str(error))
        if "sort" in values or any(key in values for key in self.FLIP_SETTINGS):
            self.refresh_themes()

    def _save_in_background(self, values: dict, then=None):
        """One worker writes these in order; `then` runs on GTK's loop once this write is stored."""
        jobs = self.__dict__.get("_settings_jobs")
        if jobs is None:
            jobs = self._settings_jobs = queue.SimpleQueue()

            def worker():
                while True:
                    job, after = jobs.get()
                    try:
                        theme_auto.update_settings(**job)
                        error = None
                    except OSError as failure:
                        error = failure
                    GLib.idle_add(lambda e=error, a=after: self._background_saved(e, a) and False)
            threading.Thread(target=worker, daemon=True).start()
        jobs.put((dict(values), then))

    def _background_saved(self, error, then=None):
        if error is not None:
            theme_core.notify(_("Could not save the setting"), str(error))
        if then is not None:
            then()
        else:
            self.refresh_themes()
        return False

    def corner_labels(self) -> list[str]:
        """In the order of corners.CHOICES."""
        rounding = getattr(self, "window_rounding", None)
        like = _("Like windows ({0} px)").format(rounding) if rounding is not None else _("Like windows")
        return [like, _("Slightly rounded ({0} px)").format(corners.SLIGHT),
                _("Rounded ({0} px)").format(corners.ROUNDED), _("Square")]

    def corners_tip(self) -> str:
        """Where the windows' rounding comes from: the theme, looknfeel.lua or Omarchy."""
        source = corners.rounding_source()
        if source == "user":
            origin = _("Your windows' rounding is set in ~/.config/hypr/looknfeel.lua.")
        elif source == "theme":
            origin = _("Your windows' rounding is set by the current theme (its hyprland.lua).")
        else:
            origin = _("Your windows use Omarchy's default rounding (square); a theme or ~/.config/hypr/looknfeel.lua can change it.")
        return _("Corners of the popup, its cards and every control (buttons, choice boxes, fields). “Like windows” follows Hyprland's window rounding.") + " " + origin

    def set_corners(self, value: str):
        self.save_setting(corners=corners.choice(value))
        self.apply_theme_css()
        self.redraw_pictures(self.window)      # the drawn pictures (workspaces, swatches) follow it

    def redraw_pictures(self, widget):
        if widget is None:
            return
        if isinstance(widget, (Gtk.DrawingArea, FlipPicture)):
            widget.queue_draw()
        child = widget.get_first_child()
        while child is not None:
            self.redraw_pictures(child)
            child = child.get_next_sibling()

    def reset_settings(self):
        """Back to the defaults (see the button's tooltip). A new language
        restarts the popup (on this page), so it comes last."""
        # Through the same ordered worker as the flip settings: a flip change just before stays before
        # it, and the rest of the reset (a language restart last) runs once this write is stored.
        self._save_in_background(dict(sort="name", workspace_wallpapers=True, workspace_themes=False,
                                      slideshow_minutes=0, corners="windows",
                                      screenshot_key=True, screenshot_folder=None,
                                      ask_ai_web=True, preview_flip_3d=True,
                                      preview_flip_delay=theme_auto.DEFAULT_FLIP_DELAY,
                                      preview_pin_apply_theme=True,
                                      preview_big_card=True,
                                      header_order=list(header_order.DEFAULT)),
                                 then=self._reset_after_saved)

    def _reset_after_saved(self):
        self.refresh_themes()
        self.header_order = list(header_order.DEFAULT)
        self.rebuild_header_group()
        self.save_schedule(enabled=False, day_at=theme_auto.DEFAULT_SCHEDULE["day_at"],
                           night_at=theme_auto.DEFAULT_SCHEDULE["night_at"])
        if not theme_core.sync_enabled():
            self.set_omarchy_sync(True)
        self.apply_theme_css()
        self.redraw_pictures(self.window)       # the drawn pictures follow Corners too
        if i18n.language_setting() != "auto":
            self.set_language("auto")
            return
        GLib.idle_add(self.fill_settings)  # Not from inside the button's own handler.

    def choose_screenshot_folder(self):
        dialog = Gtk.FileDialog(title=_("Choose screenshot folder"), modal=False)
        folder = screenshots.folder_from_settings(theme_auto.settings())
        if folder.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
        self.window.hide()  # File dialogs sit below the layer-shell surface.

        def done(_dialog, result):
            try:
                chosen = dialog.select_folder_finish(result)
            except GLib.Error:
                chosen = None
            self.window.present()
            if chosen is not None and chosen.get_path():
                self.save_setting(screenshot_folder=chosen.get_path())
                self.fill_settings()

        dialog.select_folder(None, None, done)

    def take_screenshot(self):
        if getattr(self, "screenshot_busy", False) or not self.window.is_visible() or self.shot_dialog_open():
            return
        geo = self.monitor_geometry()
        if geo is None:
            theme_core.notify(_("Could not save screenshot"), _("The monitor position is unavailable."))
            return
        x, y = self.current_position()
        rectangle = (geo.x + x, geo.y + y, self.window.get_width(), self.window.get_height())
        self.screenshot_busy = True

        def done(path, error):
            self.screenshot_busy = False
            if error is None:
                if self.window.is_visible():
                    self.show_screenshot_note(path)
                else:
                    theme_core.notify(_("Screenshot saved"), str(path))
            else:
                theme_core.notify(_("Could not save screenshot"), str(error))

        self._async(lambda: screenshots.capture_to_settings(rectangle, theme_auto.settings()), done)

    def save_schedule(self, **values):
        try:
            theme_auto.save_schedule(**values)
        except OSError as error:
            theme_core.notify(_("Could not save the setting"), str(error))

    def set_schedule_enabled(self, on: bool):
        plan = theme_auto.schedule()
        if on and not (plan["day"] and plan["night"]):
            theme_core.notify(_("Choose a day and a night theme"), _("Switching starts once both are set."))
        self.save_schedule(enabled=on)

    # --- theme editor ------------------------------------------------------------------------

    def build_editor_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = self.back_button()
        back.set_tooltip_text(_("Back to themes without saving (Esc)"))
        back.connect("clicked", lambda *_: self.close_editor())
        header.append(back)
        self.ed_title = Gtk.Label(xalign=0, hexpand=True)
        self.ed_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.ed_title.set_max_width_chars(1)
        self.ed_title.add_css_class("tm-title")
        header.append(self.ed_title)
        page.append(header)
        # The sample stays in view next to the controls: above them in a
        # narrow popup, large on the left of them in a wide one (ed_set_width).
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, vexpand=True)
        self.ed_sample_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.ed_sample_holder.set_valign(Gtk.Align.START)
        body.append(self.ed_sample_holder)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        self.ed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.ed_box.add_css_class("tm-settings")
        scroller.set_child(self.ed_box)
        body.append(scroller)
        page.append(body)
        self.ed_body = body
        self.ed_sample_size = (CARD_WIDTH, 150)
        self.ed_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        self.ed_hint.add_css_class("tm-hint")
        page.append(self.ed_hint)
        actions = Gtk.Box(spacing=8, homogeneous=True)
        self.ed_save = Gtk.Button()
        self.ed_save.add_css_class("tm-paste")
        self.ed_save.connect("clicked", lambda *_: self.save_editor(apply=False))
        self.ed_save_apply = Gtk.Button()
        self.ed_save_apply.add_css_class("tm-paste")
        self.ed_save_apply.add_css_class("suggested")
        self.ed_save_apply.connect("clicked", lambda *_: self.save_editor(apply=True))
        actions.append(self.ed_save)
        actions.append(self.ed_save_apply)
        page.append(actions)
        self.ed_provider = None
        self.ed = None
        return page

    def open_editor(self, base: theme_core.ThemeInfo, edit: bool = False,
                    wallpaper: theme_core.WallpaperInfo | None = None, palette=None):
        """Make a new theme from `base`, from one of its wallpapers (colors
        taken from the picture), or (edit=True) change a custom theme."""
        if wallpaper is not None and palette is None:
            # The picture is decoded on a worker thread, never on the UI thread;
            # the editor opens when its colors are known.
            def done(result, error):
                if error is not None or result is None:
                    theme_core.notify(_("Could not read the colors"), str(error or ""))
                    return
                self.open_editor(base, edit, wallpaper, palette=result)
            self._async(lambda: theme_custom.palette_from_image(wallpaper.path), done)
            return
        if palette is None:
            try:
                palette = theme_custom.load_palette(base)
            except (OSError, ValueError) as error:
                theme_core.notify(_("Could not read the colors"), str(error))
                return
        self.active_theme = None
        self.stop_preview()
        self.ed_return_page = None          # Ask AI's Use sets it after opening
        font = theme_custom.font_of(base) if edit else None
        self.ed = {"base": base, "edit": edit, "palette": palette, "edited": set(), "font": font,
                   "windows": theme_custom.windows_of(base) if edit else None,
                   "windows_ok": theme_looks.windows_supported(),
                   "entries": {}, "wallpapers": wallpaper is None, "fonts": theme_custom.list_fonts(),
                   "wallpaper": wallpaper, "system_font": theme_custom.current_font()}
        name = theme_core.plain_text(base.name)
        self.ed_title.set_label(_("Edit {0}").format(name) if edit else
                                _("New theme from {0}").format(theme_core.plain_text(wallpaper.name)) if wallpaper else
                                _("New theme from {0}").format(name))
        self.ed_save.set_label(_("Save") if edit else _("Create"))
        self.ed_save_apply.set_label(_("Save and apply") if edit else _("Create and apply"))
        self.ed_hint.set_label("")
        self.fill_editor()
        self.stack.set_visible_child_name("editor")
        if not edit:
            self.ed_name.grab_focus()
            self.ed_name.select_region(0, -1)

    def close_editor(self, keep: str | None = None):
        """Back (or Esc) returns to where the editor was opened from: the Ask
        AI page keeps its results; saving shows the theme in the list."""
        base = self.ed["base"].slug if self.ed else None
        self.ed = None
        if self.ed_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.ed_provider)
            self.ed_provider = None
        back_to = getattr(self, "ed_return_page", None) if keep is None else None
        self.ed_return_page = None
        self.refresh_themes(keep=keep or base)
        if back_to == "ask":
            self.stack.set_visible_child_name("ask")
            self.ask_entry.grab_focus()
            return
        self.stack.set_visible_child_name("themes")
        self.select_row(self.rows.get_selected_row())

    def fill_editor(self):
        ed, box = self.ed, self.ed_box
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()
        self.settings_ready = False
        palette = ed["palette"]

        if ed["edit"]:
            self.ed_name = Gtk.Entry(text=ed["base"].name)
            self.ed_name.set_max_length(theme_custom.MAX_NAME)
            self.guard_text_field(self.ed_name)
            self.ed_name.connect("activate", lambda *_: self.save_editor(apply=False))
            self.ed_name.set_size_request(180, -1)
            self.ed_name.set_tooltip_text(_("Renaming keeps its wallpapers, favorites and other settings"))
            self.settings_row(box, _("Name"), "", self.ed_name).add_css_class("tm-setting-compact")
        else:
            if ed["wallpaper"] is not None:
                suggestion = theme_custom.unique_name(theme_custom.name_from_file(ed["wallpaper"].name)
                                                      or theme_custom.DEFAULT_NAME)
            else:
                suggestion = theme_custom.suggested_name(ed["base"].slug)
            self.ed_name = Gtk.Entry(text=suggestion)
            self.ed_name.set_max_length(theme_custom.MAX_NAME)
            self.guard_text_field(self.ed_name)
            self.ed_name.connect("activate", lambda *_: self.save_editor(apply=False))
            self.ed_name.set_size_request(180, -1)
            self.ed_name.set_tooltip_text(_("Shown in Omarchy's theme picker"))
            self.settings_row(box, _("Name"), "", self.ed_name).add_css_class("tm-setting-compact")

        self.build_sample()

        # Name and font as compact rows (explanations in tooltips), so the
        # colors still start on the first screen at the default size.
        fonts = ed["fonts"]
        labels = [_("Keep the current font"), *fonts]
        selected_font = fonts.index(ed["font"]) + 1 if ed["font"] in fonts else 0
        font_drop = self.dropdown(labels, selected_font, self.set_editor_font)
        font_drop.set_tooltip_text(_("Omarchy's monospace font (terminals, bar, menus). It is switched when this "
                                   "theme is applied, and back to your own font with other themes."))
        self.settings_row(box, _("Font"), "", font_drop).add_css_class("tm-setting-compact")
        if ed["windows_ok"]:
            modes = [None, *theme_looks.WINDOW_MODES]
            windows_drop = self.dropdown([_("Keep the current window style")] + [
                _(self.WINDOW_LABELS[m]) for m in theme_looks.WINDOW_MODES],
                modes.index(ed["windows"]) if ed["windows"] in modes else 0, self.set_editor_windows)
            windows_drop.set_tooltip_text(_("Omarchy's window style. Glassy blurs what is behind see-through "
                                            "windows, the bar and menus. It is switched when this theme is applied, "
                                            "and back to your own with other themes."))
            self.settings_row(box, _("Windows"), "", windows_drop).add_css_class("tm-setting-compact")

        if not ed["edit"] and not ed.get("from_ask"):
            # Which wallpapers the new theme gets: a compact row up here too.
            # (An Ask AI theme: the card's picture, else its base's wallpapers, without a row.)
            count = sum(1 for w in theme_core.list_backgrounds(None, ed["base"]) if not w.hidden)
            base = theme_core.plain_text(ed["base"].name)
            if ed.get("ask_wallpaper") is not None and ed["wallpaper"] is None:
                walls = self.switch(ed["wallpapers"], self.set_editor_wallpapers)
                walls.set_tooltip_text(_("The picture from the Ask AI card is always included; on: also {0}'s {1} "
                                         "wallpapers. Change them later on the new theme's wallpaper page.").format(
                    base, count))
                self.settings_row(box, _("Also {0}'s wallpapers").format(base), "", walls).add_css_class("tm-setting-compact")
            elif ed["wallpaper"] is not None:
                walls = self.switch(False, self.set_editor_wallpapers)
                walls.set_tooltip_text(_("{0} is always included; on: also {1}'s other {2}. Change them later on the "
                                         "new theme's wallpaper page.").format(
                    theme_core.plain_text(ed['wallpaper'].name), base, count - 1))
                self.settings_row(box, _("Also {0}'s wallpapers").format(base), "", walls).add_css_class("tm-setting-compact")
            else:
                walls = self.switch(True, self.set_editor_wallpapers)
                walls.set_tooltip_text(_("Copies of {0}'s {1} visible wallpapers. Add, hide or delete them later on "
                                         "the new theme's wallpaper page.").format(base, count))
                self.settings_row(box, _("Take over its wallpapers ({0})").format(count), "", walls).add_css_class("tm-setting-compact")

        self.settings_heading(box, _("Colors"))
        for key in theme_custom.MAIN_KEYS + theme_custom.PALETTE_KEYS:
            box.append(self.color_row(key))
        more = Gtk.Expander(label=_("More colors: shades and bright variants"))
        more.add_css_class("tm-setting-heading")
        more_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        more_box.set_margin_top(6)
        hint = Gtk.Label(label=_("These follow the colors above until you change them here."),
                         xalign=0, wrap=True, max_width_chars=1)
        hint.add_css_class("tm-hint")
        more_box.append(hint)
        for key in theme_custom.SHADE_KEYS + theme_custom.BRIGHT_KEYS + tuple(palette.editable_extras()):
            more_box.append(self.color_row(key))
        more.set_child(more_box)
        box.append(more)


        self.update_editor_css()
        self.settings_ready = True

    # The sample: a small Omarchy desktop in the colors and font being edited.
    SAMPLE_LINES = (
        '<span foreground="{magenta}">def</span> <span foreground="{blue}">greet</span>(name):',
        '    <span foreground="{muted}"># say hello, in the theme\'s colors</span>',
        '    <span foreground="{yellow}">print</span>(<span foreground="{green}">"hello"</span>, '
        '<span foreground="{orange}">42</span>, <span foreground="{cyan}">True</span>)',
    )
    SAMPLE_PROMPT = ('<span foreground="{cyan}">~/code</span> <span foreground="{accent}">❯</span> '
                     'omarchy theme set  <span foreground="{red}">✗ 1 error</span>')
    SAMPLE_BAR = ('<span foreground="{accent}">●</span> <span foreground="{foreground}">2</span> '
                  '<span foreground="{muted}">3  4</span>')

    def editor_wallpaper(self):
        """The picture behind the sample: the wallpaper the colors came
        from, or the first one the theme has (or will get)."""
        ed = self.ed
        if ed["wallpaper"] is not None:
            return ed["wallpaper"].path
        if ed.get("ask_wallpaper") is not None:
            # The Ask AI card's picture: its thumbnail is already cached.
            wall = ed["ask_wallpaper"]
            thumb = web_images.cached(wall.url) if self.is_web_picture(wall) else wallpaper_search.thumb_path(wall)
            if thumb is not None and thumb.is_file() and not thumb.is_symlink():
                return thumb
        if ed["edit"] or ed["wallpapers"]:
            walls = [w.path for w in theme_core.list_backgrounds(None, ed["base"]) if not w.hidden]
            return walls[0] if walls else None
        return None

    def build_sample(self):
        holder = self.ed_sample_holder
        child = holder.get_first_child()
        while child is not None:
            holder.remove(child)
            child = holder.get_first_child()
        ed = self.ed
        desk = Gtk.Overlay()
        desk.add_css_class("tm-ed-desk")
        desk.set_overflow(Gtk.Overflow.HIDDEN)
        # One height whatever the width: a picture would otherwise grow with
        # the card and push the controls out of sight.
        width, height = self.ed_sample_size
        wall = FixedPicture(1 if width == CARD_WIDTH else width, height)
        path = self.editor_wallpaper()
        if path is not None:
            self.decoder.request(wall, path, width, height)
        desk.set_child(wall)
        front = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("s-bar")
        workspaces = Gtk.Label(xalign=0, hexpand=True)
        clock = Gtk.Label(label="12:30")
        clock.add_css_class("s-text")
        bar.append(workspaces)
        bar.append(clock)
        front.append(bar)
        window = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        window.add_css_class("s-win")
        title = Gtk.Label(label="~/code/hello.py — nvim", xalign=0)
        title.add_css_class("s-muted")
        title.add_css_class("s-small")
        window.append(title)
        lines = []
        for _n in self.SAMPLE_LINES:
            line = Gtk.Label(xalign=0)
            line.add_css_class("s-text")
            line.set_ellipsize(Pango.EllipsizeMode.END)
            line.set_max_width_chars(1)   # long lines must not widen the card
            window.append(line)
            lines.append(line)
        selected = Gtk.Label(label=_("selected line"), xalign=0)
        selected.add_css_class("s-sel")
        window.append(selected)
        prompt = Gtk.Label(xalign=0)
        prompt.add_css_class("s-text")
        prompt.set_ellipsize(Pango.EllipsizeMode.END)
        prompt.set_max_width_chars(1)
        window.append(prompt)
        front.append(window)
        desk.add_overlay(front)
        holder.append(desk)
        ed["sample"] = {"lines": lines, "prompt": prompt, "bar": workspaces}

    def ed_set_width(self, card_w):
        """Wide: the sample on the left, as large as 45 % of the card (16:9),
        the controls on the right; narrow: the sample 150 px high above them."""
        body = getattr(self, "ed_body", None)
        if body is None:
            return
        wide = card_w >= WIDE_EDITOR_FROM
        size = (round(card_w * 0.45), round(card_w * 0.45 * 9 / 16)) if wide else (CARD_WIDTH, 150)
        body.set_orientation(Gtk.Orientation.HORIZONTAL if wide else Gtk.Orientation.VERTICAL)
        body.set_spacing(18 if wide else 8)
        self.ed_sample_holder.set_size_request(size[0] if wide else -1, -1)
        if size != self.ed_sample_size:
            self.ed_sample_size = size
            self.ed_sample_dirty = True
        if getattr(self, "ed_sample_dirty", False) and self.ed is not None \
                and getattr(self, "resize_start", None) is None:
            self.ed_sample_dirty = False   # once, after a grip drag is over
            self.build_sample()
            self.update_sample()

    def set_editor_wallpapers(self, on: bool):
        self.ed["wallpapers"] = on
        self.build_sample()
        self.update_sample()

    def update_sample(self):
        sample = self.ed.get("sample")
        if not sample:
            return
        colors = dict(self.ed["palette"].colors)
        for label, template in zip(sample["lines"], self.SAMPLE_LINES):
            label.set_markup(template.format(**colors))
        sample["prompt"].set_markup(self.SAMPLE_PROMPT.format(**colors))
        sample["bar"].set_markup(self.SAMPLE_BAR.format(**colors))

    def color_row(self, key: str) -> Gtk.Widget:
        row = Gtk.Box(spacing=10)
        row.add_css_class("tm-color-row")
        swatch = Gtk.Button()
        swatch.add_css_class("tm-swatch")
        swatch.add_css_class(f"tm-sw-{key}")
        swatch.set_size_request(34, 26)
        swatch.set_tooltip_text(_("Pick a color"))
        swatch.connect("clicked", lambda button: self.show_color_picker(button, key))
        row.append(swatch)
        name = Gtk.Label(label=theme_custom.label(key), xalign=0, hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(1)
        row.append(name)
        entry = Gtk.Entry(text=self.ed["palette"].get(key), width_chars=8, max_width_chars=8)
        entry.set_max_length(7)
        entry.add_css_class("tm-hex")
        self.guard_text_field(entry)
        entry.connect("activate", lambda e: self.set_editor_color(key, e.get_text().strip()))
        leave = Gtk.EventControllerFocus()
        leave.connect("leave", lambda *_: self.ed and self.set_editor_color(key, entry.get_text().strip()))
        entry.add_controller(leave)
        row.append(entry)
        self.ed["entries"][key] = entry
        return row

    def show_color_picker(self, button, key: str):
        def build(popover):
            box = self.menu_box()
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                chooser = Gtk.ColorChooserWidget(use_alpha=False)
            # Its custom-color editor has its own hex field (and spin
            # buttons): paste and drops there go through the bounded reads too.
            self.guard_text_fields_in(chooser)
            color = Gdk.RGBA()
            color.parse(self.ed["palette"].get(key))
            chooser.set_rgba(color)

            def use(*_):
                rgba = chooser.get_rgba()
                value = "#" + "".join(f"{round(c * 255):02x}" for c in (rgba.red, rgba.green, rgba.blue))
                popover.popdown()
                self.set_editor_color(key, value)
            chooser.connect("color-activated", use)
            box.append(chooser)
            done = Gtk.Button(label=_("Use for {0}").format(theme_custom.label(key).lower()))
            done.add_css_class("tm-paste")
            done.connect("clicked", use)
            box.append(done)
            return box
        self.show_menu(button, build)

    def set_editor_color(self, key: str, value: str):
        ed = self.ed
        if ed is None or value.lower() == ed["palette"].get(key):
            return
        if not value.startswith("#"):
            value = "#" + value
        try:
            ed["palette"].set(key, value, ed["edited"])
        except (ValueError, KeyError):
            self.ed_hint.set_label(_("“{0}” is not a color: use #rrggbb, e.g. #3a6ea5.").format(value))
            ed["entries"][key].set_text(ed["palette"].get(key))
            return
        if key not in theme_custom.MAIN_KEYS and key not in theme_custom.PALETTE_KEYS:
            ed["edited"].add(key)   # a shade picked by hand no longer follows its base color
        self.ed_hint.set_label("")
        for other, entry in ed["entries"].items():
            if entry.get_text().lower() != ed["palette"].get(other):
                entry.set_text(ed["palette"].get(other))
        self.update_editor_css()

    def set_editor_windows(self, index: int):
        if self.ed is not None:
            modes = [None, *theme_looks.WINDOW_MODES]
            self.ed["windows"] = modes[index] if 0 <= index < len(modes) else None
            self.update_editor_css()

    def set_editor_font(self, index: int):
        if self.ed is not None:
            self.ed["font"] = self.ed["fonts"][index - 1] if index else None
            self.update_editor_css()

    def update_editor_css(self):
        """Swatches and the sample window in the colors being edited."""
        ed = self.ed
        colors = {key: ed["palette"].get(key) for key in ed["entries"]}
        colors.update(ed["palette"].colors)
        css = [f".tm-sw-{key} {{ background: {value}; }}" for key, value in colors.items()
               if theme_custom.HEX_RE.match(value)]
        # The sample shows the font the theme will have: its own, or the one in use.
        shown = ed["font"] or ed.get("system_font")
        font = f'font-family: "{shown}";' if shown and theme_custom.FONT_RE.match(shown) else ""
        c = ed["palette"].colors
        radius = getattr(self, "ui_radius", corners.ROUNDED)      # Settings › Corners
        control, small = corners.control(radius), corners.small(radius)
        css.append(f".tm-ed-desk {{ background: {c['darker_background']}; border-radius: {radius}px;"
                   " margin-bottom: 4px; }")
        css.append(f".tm-ed-desk label {{ {font} font-size: 11px; }}")
        css.append(f".tm-ed-desk .s-bar {{ background: alpha({c['background']}, 0.94); padding: 3px 10px; }}")
        # glassy: the sample window lets the wallpaper show through, as on the desktop
        see_through = 0.72 if ed.get("windows") == "glassy" else 0.97
        css.append(f".tm-ed-desk .s-win {{ background: alpha({c['background']}, {see_through});"
                   f" border: 2px solid {c['accent']}; border-radius: {control}px; margin: 8px 18px 10px 18px;"
                   " padding: 5px 10px; }")
        css.append(f".tm-ed-desk .s-text {{ color: {c['foreground']}; }}")
        css.append(f".tm-ed-desk .s-muted {{ color: {c['muted']}; }}")
        css.append(".tm-ed-desk .s-small { font-size: 10px; margin-bottom: 2px; }")
        css.append(f".tm-ed-desk .s-sel {{ background: {c['selection']}; color: {c['foreground']};"
                   f" padding: 0 6px; border-radius: {min(radius, 3)}px; }}")
        css.append(f".tm-ed-desk .s-dot {{ border-radius: {small}px; }}")
        css.append(f"button.tm-swatch {{ border: 1px solid alpha(currentColor, 0.4); border-radius: {small}px;"
                   " min-height: 0; min-width: 0; padding: 0; }")
        provider = Gtk.CssProvider()
        provider.load_from_data("\n".join(css).encode("utf-8"))
        display = Gdk.Display.get_default()
        if self.ed_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.ed_provider)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.ed_provider = provider
        self.update_sample()

    def save_editor(self, apply: bool):
        ed = self.ed
        if ed is None:
            return
        # A color still being typed counts.
        for key, entry in list(ed["entries"].items()):
            self.set_editor_color(key, entry.get_text().strip())
        if self.ed_hint.get_label():
            return
        palette, font, base = ed["palette"], ed["font"], ed["base"]
        windows = ed.get("windows")
        ask_wall = ed.get("ask_wallpaper")          # chosen on an Ask AI card
        self.ed_ask_downloaded = None
        if ed["edit"]:
            name = self.ed_name.get_text()
            try:
                theme_custom.slug_for(name)
            except ValueError as error:
                self.ed_hint.set_label(str(error).capitalize() + ".")
                return
            self.ed["was_current"] = base.current

            def work():
                slug = theme_custom.rename_theme(base, name)
                theme = next(t for t in theme_core.discover_themes() if t.slug == slug)
                theme_custom.update_theme(theme, palette, font, windows)
                self.ed_ask_downloaded = self.download_ask_wallpaper(slug, ask_wall)
                if self.ed_ask_downloaded is not None:      # its preview shows that picture
                    theme_custom.make_preview(theme.path, palette, self.ed_ask_downloaded)
                return slug
        else:
            name = self.ed_name.get_text()
            try:
                theme_custom.slug_for(name)
            except ValueError as error:
                self.ed_hint.set_label(str(error).capitalize() + ".")
                return
            wallpapers = ed["wallpapers"]
            extra = [ed["wallpaper"].path] if ed["wallpaper"] is not None else None

            def work():
                directory = theme_custom.create_theme(base, name, palette, font, wallpapers,
                                                      extra_wallpapers=extra, windows=windows)
                slug = directory.name
                self.ed_ask_downloaded = self.download_ask_wallpaper(slug, ask_wall)
                if self.ed_ask_downloaded is not None:      # its preview shows that picture
                    theme_custom.make_preview(directory, palette, self.ed_ask_downloaded)
                return slug
        self.ed_hint.set_label(_("Saving…"))
        self.ed_save.set_sensitive(False)
        self.ed_save_apply.set_sensitive(False)
        self._async(work, lambda slug, error: self.after_save_editor(slug, error, apply))

    @staticmethod
    def download_ask_wallpaper(slug, item):
        """The picture an Ask AI card showed (wallhaven or the web), into the saved theme."""
        if item is None:
            return None
        try:
            if isinstance(item, web_images.WebPicture):
                return web_images.download(None, slug, item)
            return wallpaper_search.download(None, slug, item)
        except FileExistsError:
            return None
        except (OSError, ValueError) as error:
            GLib.idle_add(lambda: theme_core.notify(_("Could not download the wallpaper"), str(error)) and False)
            return None

    def after_save_editor(self, slug, error, apply: bool):
        self.ed_save.set_sensitive(True)
        self.ed_save_apply.set_sensitive(True)
        if error is not None:
            self.ed_hint.set_label(_("Could not save: {0}").format(error))
            return
        edit = self.ed["edit"] if self.ed else False
        was_current = bool(self.ed and self.ed.get("was_current"))
        theme_core.notify(_("Theme saved") if edit else _("Theme created"), theme_core.display_name(slug))
        if theme_core.sync_enabled():
            self._async(lambda: theme_core.refresh_switcher_cache(SWITCHER_WRAPPER), lambda *_: None)
        self.close_editor(keep=slug)
        theme = next((t for t in theme_core.discover_themes() if t.slug == slug), None)
        downloaded, self.ed_ask_downloaded = getattr(self, "ed_ask_downloaded", None), None
        # The theme in use is applied again (under its new name, if renamed),
        # so the new colors show everywhere.
        if theme is not None and apply and downloaded is not None:
            # With the Ask AI card's wallpaper as its background.
            def apply_with_wallpaper():
                ok = theme_auto.apply_theme(theme.slug)
                if ok and theme_core.set_desktop_background(downloaded):
                    theme_auto.remember_base_wallpaper(theme.slug, downloaded)
                return ok
            self._async(apply_with_wallpaper, lambda ok, error: self.after_apply(theme, ok, error))
        elif theme is not None and (apply or theme.current or was_current):
            self.apply_theme_action(theme)

    # --- add wallpapers with a file chooser -------------------------------------------------

    def choose_files(self):
        """Pick images in a file chooser. Dialogs open beneath this overlay
        layer, so the popup steps aside until the dialog is closed."""
        theme = self.active_theme
        if theme is None:
            return
        dialog = Gtk.FileDialog(title=_("Add wallpapers to {0}").format(theme_core.plain_text(theme.name)), modal=False)
        images = Gtk.FileFilter(name=_("Images"))
        for ext in sorted(theme_core.IMAGE_EXTS | theme_core.HEIF_EXTS):
            images.add_suffix(ext.lstrip("."))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)
        dialog.set_default_filter(images)
        pictures = Path.home() / "Pictures"
        if pictures.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(pictures)))
        self.window.hide()

        def done(_dialog, result):
            try:
                files = dialog.open_multiple_finish(result)
            except GLib.Error:
                files = None
            self.window.present()
            if self.active_theme is not theme:
                return
            paths = [Path(f.get_path()) for f in (files or []) if f.get_path()]
            if paths:
                self.add_files(paths)

        dialog.open_multiple(None, None, done)

    # --- undo ----------------------------------------------------------------------------

    def offer_undo(self, text: str, receipts: list):
        if not receipts:
            return
        self.undo_receipts = receipts
        self.undo_label.set_label(text)
        self.undo_bar.set_reveal_child(True)
        if self.undo_timer:
            GLib.source_remove(self.undo_timer)

        def expire():
            self.undo_timer = 0
            self.end_undo()
            return GLib.SOURCE_REMOVE
        self.undo_timer = GLib.timeout_add_seconds(UNDO_SECONDS, expire)

    def end_undo(self):
        """Undo is no longer offered: a deleted theme is gone for good, so
        its key is removed and free again."""
        self.undo_bar.set_reveal_child(False)
        self.undo_receipts = []
        self.prune_shortcuts()

    def prune_shortcuts(self):
        def done(themes, error):
            if error is None and themes:
                theme_core.notify(_("Shortcut removed"), _("The key of {0} is free again.").format(", ".join(
                    theme_core.display_name(t) for t in themes)))
        self._async(lambda: (theme_looks.prune_missing(), shortcuts.prune_missing())[1], done)

    def undo_delete(self):
        receipts, self.undo_receipts = self.undo_receipts, []
        if self.undo_timer:
            GLib.source_remove(self.undo_timer)
            self.undo_timer = 0
        self.undo_bar.set_reveal_child(False)
        changes = [r for r in receipts if callable(getattr(r, "undo", None))]
        for change in changes:            # a workspace preview change, not a file
            change.undo()
        receipts = [r for r in receipts if r not in changes]
        if not receipts:
            return
        for receipt in receipts:
            try:
                theme_core.restore_from_trash(receipt)
            except OSError as error:
                theme_core.notify(_("Could not undo"), str(error))
                continue
            theme_core.notify(_("Restored"), receipt.original.name)
        if theme_core.sync_enabled():
            self._async(lambda: theme_core.refresh_switcher_cache(SWITCHER_WRAPPER), lambda *_: None)
        self.refresh_shortcuts()
        self.refresh_themes()
        self.refresh_wallpapers()

    # --- desktop preview ---------------------------------------------------------------------

    def start_preview(self, path, seconds: int = 0):
        """Show `path` on the desktop; stop_preview() (Space released, Esc,
        closing, or the timer) brings the real background back."""
        if path is None:
            return
        self.preview = str(path)
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
            self.preview_timer = 0
        if seconds:
            def expire():
                self.preview_timer = 0
                self.stop_preview()
                return GLib.SOURCE_REMOVE
            self.preview_timer = GLib.timeout_add_seconds(seconds, expire)
        self._async(lambda: theme_core.show_background(Path(path)), lambda *_: None)

    def stop_preview(self):
        if self.preview is None:
            return
        self.preview = None
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
            self.preview_timer = 0
        self._async(theme_core.restore_background, lambda *_: None)

    def preview_web(self, item, seconds: int = 0):
        """Web results are previewed with the full image, fetched first."""
        self.preview = f"web:{item.id}"
        self.web_hint.set_label(self.web_hint_text(_("Loading a preview of {0}…").format(item.file_name)))

        def show(path, _error):
            self.web_hint.set_label(self.web_hint_text(""))
            if path is None:
                self.preview = None
                theme_core.notify(_("No preview"), _("{0} could not be loaded").format(item.file_name))
            elif self.preview == f"web:{item.id}":   # Space still held
                self.start_preview(path, seconds)
        if self.is_web_picture(item):
            self._async(lambda: web_images.get(item.url), show)
        else:
            self._async(lambda: wallpaper_search.fetch_preview(item), show)

    def on_key_released(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_space and self.preview is not None and not self.preview_timer:
            self.stop_preview()

    # --- theming -----------------------------------------------------------------

    def apply_theme_css(self):
        colors = self.theme.colors
        display = Gdk.Display.get_default()
        if self.style_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.style_provider)

        def lum(hex_code):
            try:
                h = hex_code.lstrip("#")
                r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
                return 0.2126 * r + 0.7152 * g + 0.0722 * b
            except (ValueError, IndexError):
                return 0.5

        # Corners: Hyprland's window rounding like the Omarchy shell (the
        # default), or slightly rounded / rounded / square whatever the windows do.
        self.window_rounding = hyprland_rounding()
        radius = corners.radius(theme_auto.settings().get("corners"), self.window_rounding)
        # One Corners setting for everything, in three sizes: the card, cards, dialogs, pop-ups and
        # previews (radius); buttons, drop-downs and fields (control); check boxes, slider tracks,
        # badges and swatches (small). Chips and switches are pills, square with Square corners.
        control, small, pill = corners.control(radius), corners.small(radius), corners.pill(radius)
        self.ui_radius = radius
        workspaces_page.set_corner_radius(radius)    # the drawn workspace pictures follow it
        sel_hex = colors.get("selection", "#45475a")
        bg_hex = colors.get("background", "#1e1e2e")
        fg_hex = colors.get("foreground", "#cdd6f4")
        muted_hex = colors.get("muted", "#888894")
        # The settings labels' grey in the QML plugins: Qt.darker(foreground, 1.45),
        # i.e. the HSV value divided by 1.45.
        try:
            r, g, b = (int(fg_hex.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
            hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
            dim_hex = "#%02x%02x%02x" % tuple(round(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, val / 1.45))
        except (ValueError, IndexError):
            dim_hex = muted_hex
        red_hex = colors.get("red", "#f04452")
        accent_hex = colors.get("accent", "#89b4fa")
        green_hex = colors.get("green", "#66c98b")
        # The frame of the theme in use: its own green, its accent without one.
        current_hex = colors.get("green") or accent_hex
        yellow_hex = colors.get("yellow", "#e5c07b")
        orange_hex = colors.get("orange", colors.get("red", "#e08a5f"))

        sel_lum, fg_lum = lum(sel_hex), lum(fg_hex)
        if abs(sel_lum - fg_lum) < 0.25:
            sel_fg = "#ffffff" if sel_lum <= 0.45 else bg_hex
        else:
            sel_fg = fg_hex

        css = f"""
        window#thememanager-window {{ background: transparent; }}
        .tm-card {{ background: {bg_hex}; color: {fg_hex};
            border: 1px solid {accent_hex}; border-radius: {radius}px; padding: 17px; }}
        .tm-title {{ font-size: 15px; font-weight: 600; }}
        .tm-confirm {{ font-size: 18px; font-weight: 600; }}
        .tm-hint {{ color: {muted_hex}; font-size: 14px; }}
        .tm-header-btn {{ padding: 4px; min-width: 0; min-height: 0; margin-right: 4px; }}
        .tm-header-group {{ background: alpha({fg_hex}, 0.05); border-radius: {radius}px;
            padding: 2px 4px; }}
        .tm-header-group button.tm-header-btn {{ margin-right: 0; }}
        separator.tm-header-sep {{ background-color: alpha({fg_hex}, 0.18); background-image: none;
            min-width: 1px; margin: 5px 0; }}
        /* Buttons and tabs as in Maprisma: outlined, accent for state and icons. */
        .tm-card button.tm-tab, .tm-card button.tm-ws-group, .tm-card button.tm-ws-choice,
        .tm-card button.tm-ws-preset, .tm-card button.tm-action, .tm-card button.tm-paste,
        .tm-card button.tm-settings-nav-button, .tm-card button.tm-settings-chip {{
            background: transparent; background-image: none; box-shadow: none;
            border: 1px solid alpha({fg_hex}, 0.25); border-radius: {control}px; color: {fg_hex}; }}
        .tm-card button.tm-tab, .tm-card button.tm-action, .tm-card button.tm-paste {{ padding: 6px 12px; min-height: 0; }}
        .tm-card button.tm-ws-group {{ padding: 3px 10px; min-height: 0; font-size: 13px; }}
        .tm-card button.tm-tab:hover, .tm-card button.tm-ws-group:hover, .tm-card button.tm-ws-choice:hover,
        .tm-card button.tm-ws-preset:hover, .tm-card button.tm-action:hover, .tm-card button.tm-paste:hover,
        .tm-card button.tm-settings-nav-button:hover, .tm-card button.tm-settings-chip:hover {{
            background: alpha({accent_hex}, 0.08); }}
        .tm-card button.tm-tab:checked, .tm-card button.tm-ws-group:checked,
        .tm-card button.tm-ws-choice.tm-nav-active, .tm-card button.tm-ws-preset.tm-nav-active,
        .tm-card button.tm-settings-nav-button.tm-nav-active, .tm-card button.tm-settings-chip.tm-nav-active {{
            border: 2px solid {accent_hex}; color: {accent_hex}; background: alpha({accent_hex}, 0.08); }}
        .tm-card button.tm-tab image, .tm-card button.tm-action image, .tm-card button.tm-paste image {{ color: {accent_hex}; }}
        .tm-card button.sc-small {{ background: transparent; background-image: none; box-shadow: none;
            border: 1px solid alpha({fg_hex}, 0.25); border-radius: {control}px; color: {fg_hex}; }}
        .tm-card button.sc-small:hover {{ background: alpha({accent_hex}, 0.08); }}
        .tm-card button.tm-action.tm-destructive:hover, .tm-card button.tm-paste.tm-destructive:hover {{
            border-color: {red_hex}; color: {red_hex}; background: alpha({red_hex}, 0.08); }}
        .tm-card button.tm-action.tm-destructive:hover image, .tm-card button.tm-paste.tm-destructive:hover image {{
            color: {red_hex}; }}
        .tm-card button.tm-action:disabled, .tm-card button.tm-paste:disabled, .tm-card button.sc-small:disabled {{
            opacity: 0.45; }}
        .tm-card button.tm-back {{ color: {accent_hex}; padding: 4px 6px; min-height: 0; border: none; }}
        .tm-card button.tm-back:hover {{ background: alpha({accent_hex}, 0.10); }}
        flowboxchild.tm-ws-card {{ padding: 6px; border-radius: {radius}px; border: 1px solid alpha({fg_hex}, 0.12); }}
        flowboxchild.tm-ws-card:selected {{ background: alpha({accent_hex}, 0.16); border-color: {accent_hex}; }}
        flowboxchild.tm-ws-card:hover {{ border-color: alpha({accent_hex}, 0.6); }}
        .tm-ws-title {{ font-weight: 600; font-size: 12px; }}
        .tm-ws-title.tm-ws-current {{ color: {accent_hex}; }}
        .tm-ws-look {{ border-radius: {small}px; }}
        .tm-ws-look-name {{ background: alpha({bg_hex}, 0.75); color: {fg_hex}; font-size: 10px;
            padding: 1px 4px; border-radius: 0 0 {small}px {small}px; }}
        .tm-ws-heading {{ font-size: 11px; letter-spacing: 1px; color: {muted_hex}; margin-top: 4px; }}
        button.tm-ws-choice, button.tm-ws-preset {{ padding: 4px 6px; min-height: 0; }}
        .tm-ws-preset-name {{ font-size: 10px; }}
        .tm-ws-banner {{ background: alpha({accent_hex}, 0.10); border: 1px solid alpha({accent_hex}, 0.4);
            border-radius: {radius}px; padding: 8px; }}
        flowboxchild.tm-ws-card:selected .tm-hint {{ color: alpha({fg_hex}, 0.8); }}
        .tm-ws-banner label {{ font-size: 12px; }}
        .tm-ws-error {{ color: {red_hex}; }}
        .tm-ws-chip {{ background: alpha({fg_hex}, 0.07); border-radius: {pill}px; padding: 2px 4px 2px 8px; }}
        .tm-ws-chip.tm-ws-chip-failed {{ box-shadow: inset 0 0 0 1px {red_hex}; }}
        button.tm-ws-chip-remove {{ padding: 0 4px; min-height: 0; min-width: 0; }}
        .tm-ws-hover {{ background: {bg_hex}; border: 1px solid {accent_hex}; border-radius: {radius}px;
            padding: 8px; box-shadow: 0 6px 24px alpha(#000000, 0.45); }}
        .tm-ws-hover {{ margin: 24px; }}
        button.tm-ws-zoom {{ padding: 2px; min-height: 0; min-width: 0; margin: 3px;
            background: alpha({bg_hex}, 0.8); border-radius: {pill}px; }}
        button.tm-ws-zoom:hover {{ background: {accent_hex}; color: {bg_hex}; }}
        .tm-ws-hover-caption {{ font-size: 11px; color: {fg_hex}; }}
        flowboxchild.tm-ws-card.tm-ws-all {{ border: 1px dashed alpha({accent_hex}, 0.8);
            background: alpha({accent_hex}, 0.06); }}
        flowboxchild.tm-ws-card.tm-ws-drop {{ border: 2px dashed {accent_hex}; background: alpha({accent_hex}, 0.12); }}
        .tm-group-card {{ border: 1px solid alpha({fg_hex}, 0.16); border-radius: {radius}px; padding: 10px 12px;
            background: alpha({fg_hex}, 0.025); }}
        .tm-ask-glyph {{ font-family: "JetBrainsMono Nerd Font", "Symbols Nerd Font", monospace; font-size: 15px; }}
        .tm-ask-glyph.tm-ask-ready {{ color: {accent_hex}; }}
        .tm-ask-glyph.tm-ask-off {{ color: {dim_hex}; }}
        .tm-ask-dot {{ background: {accent_hex}; border-radius: 999px; min-width: 6px; min-height: 6px; }}
        .tm-card button.tm-action.tm-action-primary {{ border-color: {accent_hex}; color: {accent_hex}; }}
        .tm-ask-card {{ border: 1px solid alpha({fg_hex}, 0.18); border-radius: {radius}px; padding: 10px; }}
        .tm-ask-prompts {{ padding: 4px; }}
        .tm-ask-prompt {{ min-height: 34px; border-radius: {control}px; }}
        .tm-ask-prompt button {{ min-height: 30px; padding: 2px 8px; }}
        .tm-ask-handle {{ color: {dim_hex}; padding: 0 6px; }}
        .tm-ask-prompt.tm-ask-dragging {{ background: alpha({accent_hex}, 0.12); }}
        .tm-ask-prompt.tm-ask-drop-above {{ box-shadow: inset 0 2px {accent_hex}; }}
        .tm-ask-prompt.tm-ask-drop-below {{ box-shadow: inset 0 -2px {accent_hex}; }}
        .tm-ask-busy {{ background: alpha({bg_hex}, 0.85); color: {fg_hex}; border: 1px solid {accent_hex};
                        border-radius: {pill}px; padding: 6px 14px; }}
        .tm-dialog-backdrop {{ background: alpha(#000000, 0.45); border-radius: {radius}px; }}
        .tm-dialog {{ background: {bg_hex}; border: 1px solid {accent_hex}; border-radius: {radius}px;
            padding: 18px; }}
        .tm-header-drop-marker {{ background: {sel_hex}; border-radius: {min(radius, 2)}px; min-width: 3px; }}
        .tm-card button.tm-gear-btn {{ border: 1px solid alpha({fg_hex}, 0.35); border-radius: {control}px;
            padding: 1px 6px; margin-right: 0; }}
        .tm-gear {{ font-family: "JetBrainsMono Nerd Font", "Symbols Nerd Font", monospace; font-size: 13px; }}
        .tm-section-title {{ font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
        separator.tm-settings-rule {{ background-color: alpha({fg_hex}, 0.12); background-image: none;
            min-height: 1px; margin: 0; }}
        .tm-card label.tm-line-label {{ font-size: 11px; color: {dim_hex}; }}
        .tm-line dropdown > button {{ min-height: 0; padding: 2px 8px; border: 1px solid alpha({fg_hex}, 0.35);
            border-radius: {control}px; background: transparent; }}
        .tm-line dropdown label {{ font-size: 11px; }}
        dropdown.tm-count > button, dropdown.tm-helper > button {{ min-height: 0; padding: 4px 8px; border: 1px solid alpha({fg_hex}, 0.35);
            border-radius: {control}px; background: transparent; }}
        .tm-line switch {{ min-height: 16px; min-width: 36px; }}
        .tm-line switch slider {{ min-width: 14px; min-height: 14px; margin: 1px; }}
        .tm-line button.sc-small {{ font-size: 11px; min-height: 0; padding: 2px 8px;
            border: 1px solid alpha({fg_hex}, 0.35); border-radius: {control}px; background: transparent; }}
        .tm-list {{ background: transparent; }}
        .tm-list flowboxchild {{ border-radius: {radius}px; padding: 2px; outline: none; }}
        .tm-list .tm-tile {{ padding: 3px; }}
        .tm-list flowboxchild:hover {{ background: alpha({fg_hex}, 0.06); }}
        .tm-list flowboxchild:selected {{ background: {sel_hex}; color: {sel_fg}; }}
        .tm-row-name {{ font-size: 15px; font-weight: 600; }}
        .tm-row-hidden {{ opacity: 0.45; }}
        .tm-badge.hidden-badge {{ background: alpha({red_hex}, 0.25); }}
        .tm-badge.hidden-badge label {{ color: {fg_hex}; }}
        /* A light mat and hairline around previews so dark themes stand out
           from the (usually dark) card instead of melting into it. */
        .tm-preview {{ background: alpha({fg_hex}, 0.10); border: 1px solid alpha({fg_hex}, 0.28);
            border-radius: {max(4, radius - 4) if radius else 0}px; padding: 3px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.45); }}
        .tm-preview picture {{ border-radius: {max(2, radius - 6) if radius else 0}px; }}
        .tm-list flowboxchild:hover .tm-preview {{ border-color: {accent_hex}; }}
        /* The theme in use: a wider frame in its green (also under the pointer), the
           padding one less so the preview keeps its size. */
        .tm-list flowboxchild.tm-current-row .tm-preview,
        .tm-list flowboxchild.tm-current-row:hover .tm-preview {{ border: 2px solid {current_hex}; padding: 2px; }}
        .tm-badge {{ background: alpha({fg_hex}, 0.10); border-radius: {small}px;
            padding: 2px 10px; }}
        .tm-badge label {{ font-size: 12px; font-weight: 600; color: {muted_hex}; }}
        .tm-list flowboxchild:selected .tm-badge:not(.current) label {{ color: {sel_fg}; }}
        .tm-badge.current {{ background: {accent_hex}; }}
        .tm-badge.current label {{ color: {bg_hex}; }}
        .tm-badge.danger {{ color: {red_hex}; }}
        button.tm-menu-btn {{ padding: 4px; min-width: 0; min-height: 0; margin-right: 4px; }}
        .tm-grid {{ background: transparent; }}
        .tm-tile {{ background: alpha({fg_hex}, 0.08); border: 1px solid alpha({fg_hex}, 0.22);
            border-radius: {radius}px; padding: 4px; }}
        .tm-tile picture {{ border-radius: {max(2, radius - 4) if radius else 0}px; }}
        .tm-tile-label {{ font-size: 13px; color: {muted_hex}; }}
        .tm-tile-name {{ font-size: 14px; font-weight: 600; }}
        .tm-list flowboxchild .tm-tile {{ margin: 0; }}
        button.tm-tile-menu, button.tm-tile-pin, button.tm-tile-nav {{ padding: 3px; min-width: 0; min-height: 0; margin: 5px;
            background: rgba(0, 0, 0, 0.6); color: #ffffff; border-radius: {control}px; }}
        button.tm-tile-menu:hover, button.tm-tile-pin:hover, button.tm-tile-nav:hover {{ background: {accent_hex}; color: {bg_hex}; }}
        button.tm-tile-pin.pinned, button.tm-tile-pin.pinned:hover {{ background: {current_hex}; color: {bg_hex}; }}
        .tm-tile-badge {{ margin: 6px; background: rgba(0, 0, 0, 0.6); }}
        .tm-tile-badge:not(.current):not(.workspace) label {{ color: #ffffff; }}
        .tm-grid flowboxchild {{ border-radius: {radius}px; padding: 0; outline: none; }}
        .tm-grid flowboxchild:focus .tm-tile {{ border-color: {accent_hex}; background: alpha({accent_hex}, 0.12); }}
        .tm-list flowboxchild.tm-current-row .tm-tile,
        .tm-list flowboxchild.tm-current-row:hover .tm-tile,
        .tm-list flowboxchild.tm-current-row:focus .tm-tile {{ border: 2px solid {current_hex}; padding: 2px; }}
        .tm-big-preview {{ background: {bg_hex}; border: 1px solid {accent_hex}; border-radius: {radius}px;
            padding: 8px; box-shadow: 0 12px 36px rgba(0, 0, 0, 0.72); }}
        .tm-big-preview.tm-current-row {{ border: 2px solid {current_hex}; padding: 7px; }}
        .tm-big-preview .tm-tile-name {{ font-size: 15px; font-weight: 700; }}
        .tm-big-preview button.tm-tile-menu, .tm-big-preview button.tm-tile-pin,
        .tm-big-preview button.tm-tile-nav {{ padding: 5px; margin: 7px; background: rgba(0, 0, 0, 0.68); }}
        button.tm-big-btn {{ font-size: 12px; font-weight: 600; min-height: 0; padding: 3px 9px; }}
        button.tm-big-close {{ min-height: 0; min-width: 0; padding: 3px 5px; opacity: 0.8; }}
        button.tm-big-close:hover {{ opacity: 1.0; color: {accent_hex}; }}
        .tm-paste {{ font-weight: 500; font-size: 14px; }}
        button.tm-paste.danger {{ background: {red_hex}; color: #ffffff; }}
        button.tm-paste.danger:hover {{ background: shade({red_hex}, 1.1); }}
        popover.tm-popover contents {{ background-color: {bg_hex}; color: {fg_hex};
            border: 1px solid {accent_hex}; border-radius: {radius}px; padding: 5px;
            min-width: 190px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.7); }}
        popover.tm-popover arrow {{ background-color: {bg_hex}; border-color: {accent_hex}; }}
        .tm-menu-item {{ outline: none; background: transparent; border-radius: {control}px; padding: 0; }}
        .tm-menu-item label {{ font-size: 13px; font-weight: 500; color: {fg_hex}; }}
        .tm-menu-item image {{ color: {fg_hex}; }}
        .tm-menu-item:focus {{ background: {sel_hex}; }}
        .tm-menu-item:focus label {{ color: {sel_fg}; font-weight: 600; }}
        .tm-menu-item:focus image {{ color: {sel_fg}; }}
        .tm-menu-item.destructive label, .tm-menu-item.destructive image {{ color: #ff9999; }}
        .tm-menu-item.destructive:focus {{ background: {red_hex}; }}
        .tm-menu-item.destructive:focus label, .tm-menu-item.destructive:focus image {{ color: #ffffff; }}
        .tm-menu-item:disabled {{ opacity: 0.5; }}
        .tm-header-hint {{ font-size: 10px; color: {dim_hex}; }}
        .tm-grip {{ color: {muted_hex}; margin: 3px; opacity: 0.7; }}
        .tm-grip:hover {{ color: {accent_hex}; opacity: 1; }}
        .tm-undo {{ background: {sel_hex}; color: {sel_fg}; border: 1px solid {accent_hex};
            border-radius: {radius}px; padding: 6px 6px 6px 12px; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6); }}
        .tm-ws-on label {{ color: {accent_hex}; font-weight: 700; }}
        .tm-menu-item.tm-ws-here {{ box-shadow: inset 0 0 0 2px {accent_hex}; }}
        .tm-ws-other label {{ color: {muted_hex}; }}
        .tm-undo button {{ padding: 4px 14px; }}
        .tm-settings {{ padding: 2px 4px 12px 0; }}
        .tm-settings-nav {{ background: alpha({fg_hex}, 0.04); border-radius: {radius}px;
            padding: 8px; }}
        .tm-settings-nav-button {{ font-size: 12px; min-height: 0; padding: 8px 10px;
            border-radius: {control}px; text-align: left; }}
        .tm-settings-nav-button.tm-nav-active {{ background: {sel_hex}; color: {sel_fg};
            font-weight: 700; }}
        .tm-settings-chips {{ padding: 2px 2px 6px 0; }}
        .tm-settings-chip {{ font-size: 11px; min-height: 0; padding: 4px 9px;
            border-radius: {control}px; }}
        .tm-settings-chip.tm-nav-active {{ background: {sel_hex}; color: {sel_fg};
            font-weight: 700; }}
        .tm-settings-search {{ margin: 2px 4px 6px 0; }}
        .tm-setting {{ background: alpha({fg_hex}, 0.05); border-radius: {radius}px; padding: 10px 12px; }}
        .tm-setting-title {{ font-size: 14px; font-weight: 600; }}
        .tm-setting.tm-setting-compact {{ padding: 3px 4px 3px 12px; }}
        .tm-setting-compact entry, .tm-setting-compact dropdown button {{ min-height: 0; padding-top: 2px;
            padding-bottom: 2px; }}
        .tm-setting-heading {{ font-size: 13px; font-weight: 700; color: {accent_hex}; margin-top: 6px; }}
        .tm-settings-page label.tm-setting-heading {{ font-size: 11px; letter-spacing: 1px; color: {fg_hex};
            margin-top: 8px; }}
        .tm-settings switch:checked {{ background: {accent_hex}; }}
        .tm-settings dropdown button {{ padding: 4px 8px; }}
        .tm-settings dropdown label {{ font-size: 13px; }}
        dropdown popover contents {{ background-color: {bg_hex}; color: {fg_hex};
            border: 1px solid {accent_hex}; border-radius: {radius}px; }}
        dropdown popover row:selected, dropdown popover row:hover {{ background: {sel_hex}; color: {sel_fg}; }}
        .tm-color-row {{ padding: 2px 4px; }}
        .tm-color-row entry {{ font-family: monospace; min-height: 0; padding: 2px 6px; }}
        button.tm-paste.suggested {{ background: {accent_hex}; color: {bg_hex}; }}
        expander title {{ padding: 4px 0; }}
        .tm-star {{ color: {accent_hex}; font-size: 15px; }}
        .tm-keyhint {{ font-size: 12px; color: {muted_hex}; font-family: monospace; }}
        .sc-cap {{ font-size: 12px; font-weight: 600; padding: 1px 7px; border-radius: {small}px;
            border: 1px solid alpha({fg_hex}, 0.35); background: alpha({fg_hex}, 0.08); }}
        .sc-cap-lg {{ font-size: 15px; padding: 3px 10px; }}
        .sc-key {{ padding: 4px 0; }}
        button.sc-mod {{ padding: 4px 8px; min-height: 0; }}
        button.sc-mod:checked {{ background: {accent_hex}; color: {bg_hex}; }}
        button.sc-small {{ padding: 3px 10px; font-size: 13px; }}
        button.sc-keychip {{ padding: 2px 0; min-width: 0; min-height: 0; font-weight: 600;
            border: 1px solid alpha({fg_hex}, 0.25); }}
        button.sc-keychip.sc-free-key {{ color: {green_hex}; border-color: alpha({green_hex}, 0.55); }}
        button.sc-keychip.sc-yours-key {{ color: {yellow_hex}; border-color: alpha({yellow_hex}, 0.6); }}
        button.sc-keychip.sc-omarchy-key {{ color: {orange_hex}; border-color: alpha({orange_hex}, 0.6); }}
        button.sc-keychip.sc-own-key {{ color: {fg_hex}; border-color: {accent_hex}; }}
        button.sc-keychip.sc-key-picked {{ background: alpha({fg_hex}, 0.18); border-width: 2px; }}
        .sc-legend {{ font-size: 12px; }}
        .sc-legend.sc-free-key {{ color: {green_hex}; }}
        .sc-legend.sc-yours-key {{ color: {yellow_hex}; }}
        .sc-legend.sc-omarchy-key {{ color: {orange_hex}; }}
        .sc-status {{ font-size: 13px; }}
        .sc-status.sc-free {{ color: {green_hex}; }}
        .sc-status.sc-warn {{ color: {yellow_hex}; }}
        .sc-status.sc-omarchy {{ color: {orange_hex}; }}
        .tm-tile-badge.workspace {{ background: alpha({accent_hex}, 0.85); }}
        .tm-tile-badge.workspace label {{ color: {bg_hex}; }}
        .tm-add-tile {{ border-style: dashed; border-width: 2px; }}
        .tm-add-tile.active {{ border-color: {accent_hex}; background: alpha({accent_hex}, 0.12); }}
        .tm-add-tile.active image {{ color: {accent_hex}; }}
        .tm-add-tile image {{ color: {muted_hex}; }}
        .tm-grid flowboxchild:focus .tm-add-tile image, .tm-grid flowboxchild:hover .tm-add-tile image {{ color: {accent_hex}; }}
        separator.tm-menu-separator {{ background-color: {muted_hex}; background-image: none;
            opacity: 0.6; min-height: 1px; margin: 3px 6px; }}
        /* Every other control follows the same Corners setting: GTK's own buttons, drop-downs,
           fields, pop-ups and their rows, check boxes, sliders, progress bars and tooltips. Plain
           selectors, so the rules above (more specific) still win. */
        button, dropdown > button, menubutton > button, entry, entry.search, spinbutton {{ border-radius: {control}px; }}
        popover > contents {{ border-radius: {radius}px; }}
        popover modelbutton, popover listview > row, popover list > row, dropdown popover row {{
            border-radius: {small}px; }}
        checkbutton check {{ border-radius: {small}px; }}
        scale trough, scale highlight, scale fill, progressbar trough, progressbar progress {{
            border-radius: {small}px; }}
        scale slider {{ min-width: 14px; min-height: 14px; margin: -6px; border-radius: {pill}px; }}
        switch, switch slider {{ border-radius: {pill}px; }}
        tooltip, tooltip.background {{ border-radius: {control}px; }}
        """
        css += more_plugins.css("tm", fg_hex, dim_hex, accent_hex, radius)
        css += f".tm-more-icon {{ border-radius: {small}px; }}\n"      # its own 5 px: the setting's small size
        if radius == 0:
            # Square: GTK's own controls too (drop-downs, fields, switches, pop-ups, the colour
            # chooser); the Ask AI status dot stays a dot. Tooltips are windows of their own.
            css += "window#thememanager-window *:not(.tm-ask-dot) { border-radius: 0; }\n"
            css += "tooltip, tooltip * { border-radius: 0; }\n"
        if getattr(self, "ed", None) is not None:
            self.update_editor_css()               # the editor's sample and swatches follow it too
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.style_provider = provider

    # --- theme list -----------------------------------------------------------------

    def refresh_themes(self, keep: str | None = None):
        """Rebuild the list; `keep` is the slug to leave selected."""
        if keep is None:
            current = self.rows.get_selected_row()
            keep = current.tm_theme.slug if current is not None else None
        self.theme_list = theme_core.discover_themes()
        self._peek_row = None
        first = self.rows.get_first_child()
        while first is not None:
            self.rows.remove(first)
            first = self.rows.get_first_child()
        selected = None
        query = self.filter_query.casefold().strip()
        self.update_title()
        self.no_match.set_label(_("No theme matches “{0}”. Backspace or Esc clears the filter.").format(self.filter_query)
                                if query else _("No themes."))
        hidden_out = 0
        self.favorites = theme_auto.load_favorites()
        self.theme_keys = {shortcuts.theme_of(a): c for a, c in shortcuts.load_state().items()
                           if shortcuts.theme_of(a)}
        self.workspace_theme_map = theme_auto.load_workspace_themes()
        self.flip_3d = theme_auto.preview_flip_enabled()
        self.big_preview_enabled = theme_auto.preview_big_card_enabled()
        self.flip_delay = theme_auto.preview_flip_delay()
        self.pin_apply_theme = theme_auto.preview_pin_apply_theme_enabled()
        need_walls = self.flip_3d or self.big_preview_enabled
        self.hidden_wallpapers = theme_core.load_hidden_wallpapers() if need_walls else {}
        self.current_bg = theme_auto.current_background() if need_walls else None
        open_slug = self.big_preview_theme.slug if self.theme_preview_open() else None
        ordered = theme_auto.order_themes(self.theme_list, self.favorites, theme_auto.load_recent(),
                                          theme_auto.sort_order())
        # The theme in use first; the rest keep their order.
        ordered = [t for t in ordered if t.current] + [t for t in ordered if not t.current]
        for theme in ordered:
            is_hidden = theme.slug in self.hidden
            # The active theme is always listed (marked when hidden), as in
            # Omarchy's picker, so the list is never empty.
            if is_hidden and not self.show_hidden and not theme.current:
                hidden_out += 1
                continue
            if query and query not in theme.name.casefold() and query not in theme.slug.casefold():
                continue
            row = self.build_theme_row(theme, is_hidden)
            self.rows.append(row)
            if theme.slug == keep:
                selected = row
        self.hidden_hint.set_visible(bool(hidden_out and not query))
        if hidden_out and not query:
            text = (ngettext("{0} hidden theme not shown.", "{0} hidden themes not shown.", hidden_out).format(hidden_out)
                    + " " + _("The eye button in the header shows them."))
            if not theme_core.sync_enabled():
                text += " " + _("To hide them in Omarchy's own theme picker too, turn it on in Settings (⚙).")
            self.hidden_hint.set_label(text)
        self.no_match.set_visible(self.rows.get_first_child() is None)
        selected = selected or self.rows.get_first_child()
        if selected is not None:
            self.rows.select_row(selected)
            if (self.window is not None and self.window.get_visible()
                    and self.stack.get_visible_child_name() == "themes"):
                self._peek_row = selected
                if not open_slug or selected.tm_theme.slug != open_slug or not self.big_preview_enabled:
                    pic = self._row_flip_picture(selected)
                    if pic is not None:
                        pic.set_selected(True)
        if open_slug:
            if self.big_preview_enabled and selected is not None and selected.tm_theme.slug == open_slug:
                self.refresh_theme_preview(selected.tm_theme)
            else:
                self.hide_theme_preview()

    @staticmethod
    def load_hidden_list() -> set[str]:
        """The hidden list, minus the active theme: a theme in use is never
        hidden (applying unhides; this also repairs lists from before that)."""
        current = theme_core.current_theme()
        try:
            theme_core.unhide_theme(current)
        except OSError:
            pass
        return theme_core.load_hidden() - {current}

    def set_favorite(self, theme: theme_core.ThemeInfo, on: bool):
        try:
            theme_auto.set_favorite(theme.slug, on)
        except (OSError, ValueError) as error:
            theme_core.notify(_("Could not change favorites"), str(error))
        self.refresh_themes(keep=theme.slug)
        self.focus_selected_theme()

    def set_schedule_theme(self, key: str, theme: theme_core.ThemeInfo):
        self.save_schedule(**{key: theme.slug})
        plan = theme_auto.schedule()
        if not plan["enabled"]:
            theme_core.notify((_("{0} is the day theme") if key == "day" else _("{0} is the night theme")).format(
                theme_core.plain_text(theme.name)), _("Switch on “Switch themes by time of day” in Settings (⚙)."))
        else:
            theme_core.notify((_("{0} is the day theme") if key == "day" else _("{0} is the night theme")).format(
                theme_core.plain_text(theme.name)), _("From {0}.").format(plan[key + '_at']))

    def set_omarchy_sync(self, on: bool):
        def apply():
            theme_core.set_sync_enabled(on, SWITCHER_WRAPPER, SYNC_SCRIPT)
        self._async(apply, lambda _r, error: theme_core.notify(
            _("Could not change the Omarchy sync"), str(error)) if error else theme_core.notify(
            _("Omarchy sync on") if on else _("Omarchy sync off"),
            _("Hidden themes and wallpapers now also leave Omarchy's pickers.") if on else
            _("Hiding now applies to Theme Manager only; Omarchy's pickers show everything.")))
        self.refresh_themes()

    def update_title(self):
        label = "Theme Manager"
        if self.show_hidden:
            label += " (" + _("Showing hidden") + ")"
        if self.filter_query:
            label += f" — {self.filter_query}"
        self.title_label.set_label(label)

    @staticmethod
    def typed_char(keyval, state) -> str:
        """The printable character a key press types, or "" (modifiers, Tab, …)."""
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return ""
        code = Gdk.keyval_to_unicode(keyval)
        char = chr(code) if code else ""
        return char if char.isprintable() and keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) else ""

    def set_filter(self, query: str):
        self.hide_theme_preview()
        self.filter_query = query
        self.refresh_themes(keep=None)
        self.select_row(self.rows.get_selected_row())

    def build_theme_row(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.FlowBoxChild:
        if self.theme_columns > 1:
            return self.build_theme_tile(theme, is_hidden)
        row = Gtk.FlowBoxChild()
        row.tm_theme = theme
        if is_hidden:
            row.add_css_class("tm-row-hidden")
        if theme.current:
            row.add_css_class("tm-current-row")      # framed in the theme's own green
        line = Gtk.Box(spacing=8)

        line.set_spacing(12)
        line.append(self.build_preview(theme, is_hidden))

        # Name above its badges: the card is narrow next to the big preview.
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=theme_core.plain_text(theme.name), xalign=0)
        name.add_css_class("tm-row-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(1)  # a long name must not widen the card
        name.set_hexpand(True)
        title = Gtk.Box(spacing=6)
        if theme.slug in self.favorites:
            star = Gtk.Label(label="★")
            star.add_css_class("tm-star")
            star.set_tooltip_text(_("Favorite: listed first and in the bar icon's right-click menu"))
            title.append(star)
        title.append(name)
        text.append(title)
        badges = Gtk.Box(spacing=4)
        text.append(badges)
        line.append(text)

        if theme.current:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("current")
            badge.append(Gtk.Label(label=_("current")))
            badges.append(badge)
        if theme.scope == "system":
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.append(Gtk.Label(label=_("system")))
            badges.append(badge)
        if is_hidden:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("hidden-badge")
            badge.append(Gtk.Label(label=_("hidden")))
            badges.append(badge)
        assigned_workspaces = [ws for ws, slug in self.workspace_theme_map.items() if slug == theme.slug]
        if assigned_workspaces:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.append(Gtk.Label(label=_("ws {0}").format(" · ".join(map(str, assigned_workspaces)))))
            badges.append(badge)
        key = self.theme_keys.get(theme.slug)
        if key:
            row.set_tooltip_text(_("{0} applies this theme").format(shortcuts.combo_label(key)))
            keyhint = Gtk.Label(label=shortcuts.combo_label(key).replace(" + ", "+"), xalign=0)
            keyhint.add_css_class("tm-keyhint")
            keyhint.set_ellipsize(Pango.EllipsizeMode.END)
            keyhint.set_max_width_chars(1)
            text.append(keyhint)

        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-menu-btn")
        actions.set_valign(Gtk.Align.CENTER)
        actions.set_tooltip_text(_("Theme actions (Right)"))
        actions.connect("clicked", lambda button, target=theme: self.show_actions(button, target))
        line.append(actions)
        row.actions_button = actions

        row.set_child(line)
        return row

    def _tile_picture_radius(self, inset: int = 4) -> int:
        radius = getattr(self, "ui_radius", 0)
        return max(2, radius - inset) if radius else 0

    def _add_flip_overlays(self, overlay: Gtk.Overlay, theme: theme_core.ThemeInfo, picture: FlipPicture):
        pin = Gtk.Button(icon_name="view-pin-symbolic")
        pin.add_css_class("flat")
        pin.add_css_class("tm-tile-pin")
        pin.set_halign(Gtk.Align.START)
        pin.set_valign(Gtk.Align.START)
        pin.set_focusable(False)
        pin.set_visible(False)
        pin.connect("clicked", lambda _btn, target=theme, pic=picture: self.pin_theme_wallpaper(target, pic))
        picture.set_pin_button(pin)
        overlay.add_overlay(pin)
        if picture._wallpapers:
            prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
            prev_btn.add_css_class("flat")
            prev_btn.add_css_class("tm-tile-nav")
            prev_btn.set_halign(Gtk.Align.START)
            prev_btn.set_valign(Gtk.Align.CENTER)
            prev_btn.set_focusable(False)
            prev_btn.set_visible(False)
            prev_btn.set_tooltip_text(_("Previous wallpaper"))
            prev_btn.connect("clicked", lambda _btn, pic=picture: pic.rotate_step(-1))
            next_btn = Gtk.Button(icon_name="go-next-symbolic")
            next_btn.add_css_class("flat")
            next_btn.add_css_class("tm-tile-nav")
            next_btn.set_halign(Gtk.Align.END)
            next_btn.set_valign(Gtk.Align.CENTER)
            next_btn.set_focusable(False)
            next_btn.set_visible(False)
            next_btn.set_tooltip_text(_("Next wallpaper"))
            next_btn.connect("clicked", lambda _btn, pic=picture: pic.rotate_step(1))
            picture.set_nav_buttons(prev_btn, next_btn)
            overlay.add_overlay(prev_btn)
            overlay.add_overlay(next_btn)

    def pin_theme_wallpaper(self, theme: theme_core.ThemeInfo, picture: FlipPicture):
        """One pin at a time: a click while one is still being applied is kept (the latest only)
        and runs once that one has finished, so the last choice is the one that stays."""
        wp_path = picture.current_wallpaper_path()
        if not wp_path or picture.is_current_wallpaper_pinned():
            return
        if self.__dict__.get("_pin_busy"):
            self._pin_next = (theme, picture, wp_path)
            return
        self._pin_busy = True
        self._pin_next = None
        self._pin_wallpaper(theme, picture, Path(wp_path))

    def _pin_finished(self):
        self._pin_busy = False
        nxt, self._pin_next = self.__dict__.get("_pin_next"), None
        if nxt is not None:
            theme, picture, wp_path = nxt
            if picture.current_wallpaper_path() == wp_path:
                self.pin_theme_wallpaper(theme, picture)

    def _sync_theme_flip_wallpaper(self, slug: str, bg: Path, *, theme_current: bool, pin_apply_theme: bool | None = None):
        row = self.rows.get_first_child()
        while row is not None:
            t = getattr(row, "tm_theme", None)
            if t is not None and t.slug == slug:
                row_pic = self._flip_picture_in(row)
                if row_pic is not None:
                    row_pic.set_active_wallpaper(bg, theme_current=theme_current,
                                                 pin_apply_theme=pin_apply_theme, promote_on_reset=True)
            row = row.get_next_sibling()
        if self.theme_preview_open() and self.big_preview_theme.slug == slug and self.big_preview_picture is not None:
            self.big_preview_picture.set_active_wallpaper(bg, theme_current=theme_current,
                                                          pin_apply_theme=pin_apply_theme, promote_on_reset=True)

    def _pin_wallpaper(self, theme: theme_core.ThemeInfo, picture: FlipPicture, path: Path):
        if theme.current:
            def done(ok, error):
                try:
                    if not ok or error is not None:
                        theme_core.notify(_("Could not set background"), path.name)
                        return
                    self.current_bg = theme_auto.current_background() or path
                    picture.set_active_wallpaper(self.current_bg, theme_current=True, promote_on_reset=True)
                    self._sync_theme_flip_wallpaper(theme.slug, Path(self.current_bg), theme_current=True)
                finally:
                    self._pin_finished()
            self._async(lambda: theme_auto.pin_current_wallpaper(theme.slug, path), done)
            return
        if not self.pin_apply_theme:
            # A state file write (folder, atomic write, fsync): on a worker, never on GTK's loop.
            def remembered(ok, error):
                try:
                    if not ok or error is not None:
                        theme_core.notify(_("Could not set background"), path.name)
                        return
                    picture.set_active_wallpaper(path, theme_current=False, pin_apply_theme=False,
                                                 promote_on_reset=True)
                    self._sync_theme_flip_wallpaper(theme.slug, path, theme_current=False, pin_apply_theme=False)
                finally:
                    self._pin_finished()
            self._async(lambda: theme_auto.remember_omarchy_theme_background(theme.slug, path), remembered)
            return
        unhide = theme.slug in self.hidden
        if unhide:
            self.hidden.discard(theme.slug)

        def apply_with_wallpaper():
            if unhide:                                   # a file write: on this worker too
                try:
                    theme_core.unhide_theme(theme.slug)
                except OSError:
                    pass
            theme_auto.remember_omarchy_theme_background(theme.slug, path)
            ok = theme_auto.apply_theme(theme.slug)
            if ok:
                if not theme_auto._same_wallpaper(theme_auto.current_background(), path) \
                        and not theme_core.set_desktop_background(path):
                    raise BackgroundNotSet(path.name)   # the theme is applied, not the picture
                theme_auto.remember_base_wallpaper(theme.slug, path)
            return ok
        def applied(ok, error):
            try:
                if isinstance(error, BackgroundNotSet):
                    self.after_apply(theme, True, None)        # the theme is in use …
                    theme_core.notify(_("Could not set background"), path.name)   # … its picture is not
                else:
                    self.after_apply(theme, ok, error)
            finally:
                self._pin_finished()
        self._async(apply_with_wallpaper, applied)

    def _attach_flip_hover(self, target: Gtk.Widget, picture: FlipPicture):
        state = [None]

        def on_enter(_ctrl, x, y):
            state[0] = (round(x), round(y))
            if self.pointer_moved and not self.actions_menu_open():
                picture.on_hover_enter()

        def on_motion(_ctrl, x, y):
            pos = (round(x), round(y))
            if state[0] is not None and state[0] != pos:
                self.pointer_moved = True
                if not self.actions_menu_open():
                    picture.on_hover_enter()
            state[0] = pos

        def on_leave(_ctrl):
            state[0] = None
            picture.on_hover_leave()

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", on_enter)
        hover.connect("motion", on_motion)
        hover.connect("leave", on_leave)
        target.add_controller(hover)

    def build_theme_tile(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.FlowBoxChild:
        """A theme as a tile, like a wallpaper: used when the popup is wider
        than the default and the themes are shown in columns."""
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")
        if is_hidden:
            tile.add_css_class("tm-row-hidden")
        # One size whatever the badges and the name (a plain picture would
        # grow with its image and widen the card).
        tile_h = self.theme_tile_w * 9 // 16
        wallpapers = (theme_auto.flip_wallpapers(theme, hidden=self.hidden_wallpapers, current_bg=self.current_bg)
                      if self.flip_3d and theme.preview else [])
        if wallpapers:
            active_bg = self.flip_active_bg(theme, wallpapers)
            picture = FlipPicture(self.theme_tile_w, tile_h, wallpapers=wallpapers,
                                  active_bg=active_bg,
                                  theme_current=theme.current,
                                  pin_apply_theme=self.pin_apply_theme,
                                  delay_s=self.flip_delay,
                                  decoder=self.decoder,
                                  radius_fn=lambda: self._tile_picture_radius(4))
            self._attach_flip_hover(tile, picture)
        else:
            picture = FixedPicture(self.theme_tile_w, tile_h)
        if theme.preview:
            self.decoder.request(picture, theme.preview, self.theme_tile_w, tile_h)
        frame = Gtk.Overlay()
        frame.set_child(picture)
        if isinstance(picture, FlipPicture):
            self._add_flip_overlays(frame, theme, picture)
        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)
        actions.set_tooltip_text(_("Theme actions (Menu key)"))
        actions.connect("clicked", lambda button, target=theme: self.show_actions(button, target))
        frame.add_overlay(actions)
        badges = Gtk.Box(spacing=4)
        badges.set_halign(Gtk.Align.START)
        badges.set_valign(Gtk.Align.END)
        badges.set_can_target(False)
        if isinstance(picture, FlipPicture):
            picture.set_badges(badges)
        for text, kind in (("current", "current") if theme.current else (None, None),
                           ("system", "") if theme.scope == "system" else (None, None),
                           ("hidden", "hidden-badge") if is_hidden else (None, None)):
            if text:
                badge = Gtk.Box()
                badge.add_css_class("tm-badge")
                badge.add_css_class("tm-tile-badge")
                if kind:
                    badge.add_css_class(kind)
                badge.append(Gtk.Label(label=_(text)))
                badges.append(badge)
        assigned_workspaces = [ws for ws, slug in self.workspace_theme_map.items() if slug == theme.slug]
        if assigned_workspaces:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.append(Gtk.Label(label=_("ws {0}").format(" · ".join(map(str, assigned_workspaces)))))
            badges.append(badge)
        frame.add_overlay(badges)
        tile.append(frame)
        caption = Gtk.Box(spacing=4)
        caption.set_halign(Gtk.Align.FILL)
        caption.set_margin_start(6)
        caption.set_margin_end(6)
        if theme.slug in self.favorites:
            star = Gtk.Label(label="★")
            star.add_css_class("tm-star")
            caption.append(star)
        name = Gtk.Label(label=theme_core.plain_text(theme.name), xalign=0.5)
        name.add_css_class("tm-tile-name")
        key = self.theme_keys.get(theme.slug)
        if key:
            child_tip = _("{0} applies this theme").format(shortcuts.combo_label(key))
            name.set_tooltip_text(child_tip)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(1)
        name.set_hexpand(True)
        caption.set_hexpand(True)
        caption.append(name)
        tile.append(caption)
        if key:      # its shortcut, as the list rows showed it
            keyhint = Gtk.Label(label=shortcuts.combo_label(key).replace(" + ", "+"), xalign=0.5)
            keyhint.add_css_class("tm-keyhint")
            keyhint.set_ellipsize(Pango.EllipsizeMode.END)
            keyhint.set_max_width_chars(1)
            tile.append(keyhint)
        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child.tm_theme = theme
        if theme.current:
            child.add_css_class("tm-current-row")    # framed in the theme's own green
        child.actions_button = actions
        return child

    def build_preview(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.Widget:
        frame = Gtk.Box()
        frame.add_css_class("tm-preview")
        frame.set_valign(Gtk.Align.CENTER)
        width, height = self.preview_size()
        if theme.preview:
            wallpapers = (theme_auto.flip_wallpapers(theme, hidden=self.hidden_wallpapers, current_bg=self.current_bg)
                          if self.flip_3d else [])
            if wallpapers:
                active_bg = self.flip_active_bg(theme, wallpapers)
                picture = FlipPicture(width, height, wallpapers=wallpapers,
                                      active_bg=active_bg,
                                      theme_current=theme.current,
                                      pin_apply_theme=self.pin_apply_theme,
                                      delay_s=self.flip_delay,
                                      decoder=self.decoder,
                                      radius_fn=lambda: self._tile_picture_radius(6))
                self._attach_flip_hover(frame, picture)
                overlay = Gtk.Overlay()
                overlay.set_child(picture)
                self._add_flip_overlays(overlay, theme, picture)
                self.decoder.request(picture, theme.preview, width, height)
                frame.append(overlay)
            else:
                picture = FixedPicture(width, height)
                self.decoder.request(picture, theme.preview, width, height)
                frame.append(picture)
        else:
            icon = Gtk.Image.new_from_icon_name("applications-graphics")
            icon.set_pixel_size(48)
            icon.set_size_request(width, height)
            frame.append(icon)
        if is_hidden:
            frame.add_css_class("tm-row-hidden")
        return frame

    def actions_menu_open(self) -> bool:
        return self.actions_popover is not None and self.actions_popover.get_visible()

    def show_actions(self, button, theme: theme_core.ThemeInfo):
        """The row's action menu, as in Simple Launcher: Right opens it with
        the keyboard on its first entry, Left or Backspace closes it."""
        self.show_menu(button, lambda popover: self.build_theme_menu(popover, theme))

    def show_menu(self, button, build):
        if self.actions_menu_open():
            self.actions_popover.popdown()
        popover = Gtk.Popover()
        popover.add_css_class("tm-popover")
        popover.set_has_arrow(True)
        popover.set_parent(button)
        self.actions_popover = popover
        popover.connect("closed", self.on_actions_closed)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda _c, key, _code, _state: self.close_actions_on_key(popover, key))
        popover.add_controller(keys)
        self.menu_anchor = button
        popover.tm_focus = None              # a builder may name the entry to start on
        popover.set_child(build(popover))
        popover.popup()
        if popover.tm_focus is not None:
            self.focus_entry(popover.tm_focus)
        else:
            self.focus_first_entry(popover.get_child())

    def on_actions_closed(self, popover):
        if self.actions_popover is popover:
            self.actions_popover = None
        # Release the popover after GTK finishes dispatching the closing click.
        def cleanup():
            if popover.get_parent() is not None:
                popover.unparent()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(cleanup)

    def close_actions_on_key(self, popover, key) -> bool:
        if key not in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace):
            return False
        if self.typing_in(popover):
            return False   # editing text inside the menu (the color chooser's hex field)
        if key in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and self.left_moves_in_grid(popover):
            return False   # a workspace grid: ← moves to the button on the left
        popover.popdown()
        if self.stack.get_visible_child_name() == "wallpapers" and self.menu_anchor is not None:
            self.focus_tile_of(self.menu_anchor)
        else:
            row = self.rows.get_selected_row()
            if row is not None:
                row.grab_focus()
        return True

    @staticmethod
    def left_moves_in_grid(popover) -> bool:
        """The focus is on a grid button that has a neighbour on its left."""
        focus = popover.get_root().get_focus() if popover.get_root() is not None else None
        while focus is not None and not isinstance(focus.get_parent(), Gtk.Grid):
            focus = focus.get_parent()
        if focus is None:
            return False
        column = focus.get_parent().query_child(focus)[0]
        return column > 0

    @classmethod
    def typing_in(cls, widget) -> bool:
        """Whether a text field somewhere inside `widget` has the keyboard."""
        child = widget.get_first_child()
        while child is not None:
            if (isinstance(child, Gtk.Text) and child.has_focus()) or cls.typing_in(child):
                return True
            child = child.get_next_sibling()
        return False

    def menu_box(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(4)
        return box

    def menu_item(self, box, popover, label, handler, icon_name, destructive=False,
                  sensitive=True, end_icon=None, tooltip=None) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("tm-menu-item")
        if destructive:
            button.add_css_class("destructive")
        self.focus_follows_pointer(button)
        line = Gtk.Box(spacing=8)
        line.set_margin_start(8)
        line.set_margin_end(8)
        line.set_margin_top(6)
        line.set_margin_bottom(6)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(14)
        line.append(icon)
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        line.append(text)
        if end_icon:
            line.append(Gtk.Image.new_from_icon_name(end_icon))
        button.set_child(line)
        button.set_sensitive(sensitive)
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda *_: (popover.popdown(), handler()))
        box.append(button)
        return button

    @staticmethod
    def menu_separator(box):
        line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("tm-menu-separator")
        box.append(line)

    def build_theme_menu(self, popover, theme: theme_core.ThemeInfo) -> Gtk.Box:
        box = self.menu_box()

        def item(label, handler, icon_name, **kwargs):
            return self.menu_item(box, popover, label, handler, icon_name, **kwargs)

        def separator():
            self.menu_separator(box)

        if theme.current:
            item(_("Current theme"), lambda: None, "object-select-symbolic", sensitive=False)
            # only while its font or window style isn't on the desktop (saved in Font & effects, or changed elsewhere)
            pending = self.look_pending.get(theme.slug)
            if pending:
                item(_("Reapply"), lambda: self.reapply_look(theme), "view-refresh-symbolic",
                     tooltip=_("Not on the desktop yet: {0}").format(self.look_field_names(pending)))
        else:
            item(_("Apply theme"), lambda: self.apply_theme_action(theme), "media-playback-start-symbolic")
        item(_("Wallpapers"), lambda: self.open_wallpapers_for(theme), "image-x-generic-symbolic",
             end_icon="go-next-symbolic")
        item(_("Use on workspace…"), lambda: self.show_theme_workspace_menu(theme),
             "view-grid-symbolic", tooltip=_("Switches the global theme when that workspace gains focus"))
        if theme.current and theme_auto.load_workspace_themes():
            item(_("Use on all workspaces"), lambda: self.use_theme_everywhere(), "view-app-grid-symbolic",
                 tooltip=_("Removes the other workspaces' own themes: this one everywhere "
                           "(until the next day/night switch)"))
        item(_("New theme from this…"), lambda: self.open_editor(theme), "list-add-symbolic",
             tooltip=_("Your own copy with other colors, a font and these wallpapers"))
        if theme_custom.is_custom(theme):
            item(_("Edit theme…"), lambda: self.open_editor(theme, edit=True), "document-edit-symbolic",
                 tooltip=_("Name, colors and font"))
        item(_("Font & effects…"), lambda: self.open_look_page(theme), "preferences-desktop-font-symbolic",
             tooltip=_("Your own font and window style whenever this theme is applied"))
        key = shortcuts.load_state().get(shortcuts.APPLY + theme.slug)
        hidden = theme.slug in self.hidden
        label = _("Shortcut: {0}…").format(shortcuts.combo_label(key)) if key else _("Shortcut…")
        if key and hidden:
            label = _("Shortcut: {0} (works while hidden)…").format(shortcuts.combo_label(key))
        item(label, lambda: self.open_shortcut_page(shortcuts.APPLY + theme.slug), "input-keyboard-symbolic",
             tooltip=_("A key that applies this theme from anywhere")
             + (". " + _("It also works while the theme is hidden, and applying it unhides it.") if hidden else ""))
        if theme.slug in self.favorites:
            item(_("Remove from favorites"), lambda: self.set_favorite(theme, False), "starred-symbolic")
        else:
            item(_("Add to favorites"), lambda: self.set_favorite(theme, True), "non-starred-symbolic",
                 tooltip=_("Listed first, and in the bar icon's right-click menu"))
        plan = theme_auto.schedule()
        for key, label in (("day", _("This is the day theme")), ("night", _("This is the night theme"))):
            if plan[key] == theme.slug:
                item(label, lambda: None, "object-select-symbolic",
                     sensitive=False)
                break
        else:
            item(_("Use as day theme"), lambda: self.set_schedule_theme("day", theme), "weather-clear-symbolic",
                 tooltip=_("Applied at the day time set in Settings (⚙)"))
            item(_("Use as night theme"), lambda: self.set_schedule_theme("night", theme),
                 "weather-clear-night-symbolic", tooltip=_("Applied at the night time set in Settings (⚙)"))
        separator()
        if theme.slug in self.hidden:
            item(_("Unhide from list"), lambda: self.set_theme_hidden(theme, False), "view-reveal-symbolic")
        else:
            item(_("Hide from list"), lambda: self.set_theme_hidden(theme, True), "view-conceal-symbolic",
                 sensitive=not theme.current,
                 tooltip=_("This is the active theme: apply another theme first") if theme.current else None)
        separator()
        if theme.scope == "user" and theme.current:
            item(_("Delete theme…"), lambda: None, "user-trash-symbolic", sensitive=False,
                 tooltip=_("This is the active theme: apply another theme first"))
        elif theme.scope == "user":
            item(_("Delete theme…"), lambda: self.confirm_delete_theme(theme), "user-trash-symbolic",
                 destructive=True)
        else:
            button = item(_("System theme (read-only)"), lambda: None, "changes-prevent-symbolic",
                          sensitive=False)
            button.set_tooltip_text(_("System themes are owned by root and cannot be deleted; hide them instead"))
        return box

    def show_theme_workspace_menu(self, theme):
        anchor = self.menu_anchor
        if anchor is not None:
            self.show_menu(anchor, lambda popover: self.build_theme_workspace_menu(popover, theme))

    def build_theme_workspace_menu(self, popover, theme):
        box = self.menu_box()
        box.append(Gtk.Label(label=_("Use theme on workspace"), xalign=0))
        grid = Gtk.Grid(column_spacing=2, row_spacing=2, column_homogeneous=True)
        mapping = theme_auto.load_workspace_themes()
        current = theme_auto.active_workspace()
        for ws in range(1, 11):
            mine = mapping.get(ws) == theme.slug
            other = ws in mapping and not mine
            name = theme_core.display_name(mapping[ws]) if other else ""
            button = Gtk.Button()
            label = Gtk.Label(label=("✓ " if mine else "") + str(ws) + (" · " + name if other else ""),
                              ellipsize=Pango.EllipsizeMode.END)
            label.set_max_width_chars(14)
            button.set_child(label)
            button.add_css_class('tm-menu-item')
            if mine:
                button.add_css_class('tm-ws-on')
            elif other:
                button.add_css_class('tm-ws-other')
                button.set_tooltip_text(_('Replaces {0}').format(name))
            self.focus_follows_pointer(button)
            self.mark_current_workspace(popover, button, ws, current)
            button.connect('clicked', lambda _b, w=ws, on=mine: (
                popover.popdown(), self.assign_theme_workspace(theme, w, not on)))
            grid.attach(button, (ws - 1) % 5, (ws - 1) // 5, 1, 1)
        box.append(grid)
        return box

    def use_theme_everywhere(self):
        """The current theme on every workspace, with Undo."""
        try:
            saved = theme_auto.use_everywhere()
        except (OSError, ValueError) as error:
            theme_core.notify(_("Could not change the workspaces"), str(error))
            return
        if saved is None:
            return
        name = theme_core.display_name(saved["theme"]) if saved["theme"] else ""
        self.refresh_themes(keep=saved["theme"])
        app = self

        class Receipt:
            def undo(self):
                try:
                    restored = theme_auto.undo_use_everywhere(saved)
                except (OSError, ValueError) as error:
                    theme_core.notify(_("Could not undo"), str(error))
                    return
                if not restored:
                    theme_core.notify(_("Could not undo"), _("The workspaces' themes changed meanwhile."))
                    return
                app.refresh_themes(keep=saved["theme"])
                active = theme_auto.active_workspace()
                if active is not None:
                    app.refresh_focused_workspace(active)    # its own theme again, if it has one
        self.offer_undo(_("Every workspace uses {0}").format(name), [Receipt()])

    def assign_theme_workspace(self, theme, workspace, on):
        try:
            theme_auto.set_workspace_theme(workspace, theme.slug if on else None)
            if on and not theme_auto.workspace_themes_enabled():
                theme_auto.update_settings(workspace_themes=True)
        except (OSError, ValueError) as error:
            theme_core.notify(_("Could not assign workspace"), str(error))
            return
        theme_core.notify((_("Workspace {0} assigned") if on else _("Workspace {0} cleared")).format(workspace),
                          theme.name if on else "")
        self.refresh_themes(keep=theme.slug)
        self.refresh_focused_workspace(workspace)

    @staticmethod
    def focus_follows_pointer(item):
        """Pointing at a menu entry focuses it, so what looks selected is what
        Enter runs. The pointer merely resting where the menu opens does not."""
        shown = time.monotonic()

        def follow(*_):
            if time.monotonic() - shown > 0.3 and item.get_sensitive() and not item.has_focus():
                item.grab_focus()

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", follow)
        motion.connect("motion", follow)
        item.add_controller(motion)

    @staticmethod
    def focus_entry(button):
        """Open a menu with the keyboard on this entry (again after popup():
        a new popover moves the focus itself)."""
        def focus():
            if button.get_sensitive():
                button.grab_focus()
            return GLib.SOURCE_REMOVE
        focus()
        GLib.idle_add(focus)

    @staticmethod
    def mark_current_workspace(popover, button, ws, current):
        """The workspace you are on: outlined, named in the tooltip, and where
        the menu starts (Enter uses it). Not known (another monitor's special
        workspace, above 10, no Hyprland answer): the menu starts on 1."""
        if ws == 1 and current not in range(1, 11):
            popover.tm_focus = button
        if ws != current:
            return
        button.add_css_class("tm-ws-here")
        button.set_tooltip_text(((button.get_tooltip_text() or "") + " " + _("(current workspace)")).strip())
        popover.tm_focus = button

    @staticmethod
    def focus_first_entry(box):
        """Open every menu with the keyboard on its first usable entry."""
        def focus():
            child = box.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.Button) and child.get_sensitive():
                    child.grab_focus()
                    break
                child = child.get_next_sibling()
            return GLib.SOURCE_REMOVE
        focus()
        GLib.idle_add(focus)  # Again after popup(): a new popover moves the focus itself.

    def theme_preview_open(self) -> bool:
        slot = getattr(self, "big_preview_slot", None)
        return slot is not None and slot.get_visible() and getattr(self, "big_preview_theme", None) is not None

    def big_preview_size(self) -> tuple[int, int]:
        """16:9 picture size for the big theme preview overlay: ~half of the window
        (and noticeably larger than a grid tile when narrow), always inside the window
        with room around it so surrounding themes stay visible and selectable."""
        pad_w, pad_h = 20, 56
        max_pic_w = max(200, self.card_w - 48 - pad_w)
        max_card_h = min(self.list_h - 48, round(self.list_h * 0.68))
        max_pic_h = max(112, max_card_h - pad_h)
        tile_w = getattr(self, "theme_tile_w", TILE)
        want_w = max(round(self.card_w * 0.54), round(tile_w * 1.65))
        pic_w = min(want_w, max_pic_w)
        pic_h = round(pic_w * 9 / 16)
        if pic_h > max_pic_h:
            pic_h = max_pic_h
            pic_w = round(pic_h * 16 / 9)
        return pic_w, pic_h

    def hide_theme_preview(self):
        slot = getattr(self, "big_preview_slot", None)
        if slot is None or not slot.get_visible():
            self.big_preview_theme = None
            self.big_preview_picture = None
            self.big_preview_actions = None
            return
        if self.actions_menu_open() and self.menu_anchor is self.big_preview_actions:
            self.actions_popover.popdown()
        if self.big_preview_picture is not None:
            self.big_preview_picture.reset_flip()
        slot.set_visible(False)
        child = slot.get_first_child()
        while child is not None:
            slot.remove(child)
            child = slot.get_first_child()
        self.big_preview_theme = None
        self.big_preview_picture = None
        self.big_preview_actions = None
        self.big_preview_size_px = (0, 0)
        row = self.rows.get_selected_row()
        if (row is not None and self.window is not None and self.window.get_visible()
                and self.stack.get_visible_child_name() == "themes"):
            row.grab_focus()
            pic = self._row_flip_picture(row)
            if pic is not None:
                pic.set_selected(True)

    def refresh_theme_preview(self, theme: theme_core.ThemeInfo | None = None):
        if not self.theme_preview_open():
            return
        target = theme or self.big_preview_theme
        if target is None:
            self.hide_theme_preview()
            return
        preserve = None
        old_pic = self.big_preview_picture
        if old_pic is not None:
            preserve = "__preview__" if old_pic.visible_face() < 0 else old_pic.current_wallpaper_path()
        self.show_theme_preview(target, preserve_face=preserve)

    def show_theme_preview(self, theme: theme_core.ThemeInfo, preserve_face: str | None = None):
        """Show the large interactive theme card overlay with all features enabled:
        3D flip, < / > preview + wallpaper cycle, pin button, badges, ☰ menu,
        Apply/Reapply and Wallpapers."""
        if theme is None:
            return
        row = self.rows.get_selected_row()
        if row is not None:
            row_pic = self._row_flip_picture(row)
            if row_pic is not None:
                row_pic.reset_flip()
        pic_w, pic_h = self.big_preview_size()
        old_pic = self.big_preview_picture
        same_size = (self.big_preview_theme is not None
                     and self.big_preview_theme.slug == theme.slug
                     and self.big_preview_size_px == (pic_w, pic_h))
        old_front = old_pic.get_paintable() if (old_pic is not None and same_size) else None
        old_textures = dict(old_pic._textures) if (old_pic is not None and same_size) else {}
        if old_pic is not None:
            old_pic.reset_flip()

        is_hidden = theme.slug in self.hidden
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("tm-tile")
        card.add_css_class("tm-big-preview")
        if theme.current:
            card.add_css_class("tm-current-row")
        if is_hidden:
            card.add_css_class("tm-row-hidden")

        wallpapers = (theme_auto.flip_wallpapers(theme, hidden=self.hidden_wallpapers, current_bg=self.current_bg)
                      if theme.preview else [])
        frame = Gtk.Overlay()
        if wallpapers:
            active_bg = self.flip_active_bg(theme, wallpapers)
            picture = FlipPicture(pic_w, pic_h, wallpapers=wallpapers,
                                  active_bg=active_bg,
                                  theme_current=theme.current,
                                  pin_apply_theme=self.pin_apply_theme,
                                  delay_s=0,
                                  decoder=self.decoder,
                                  radius_fn=lambda: self._tile_picture_radius(4))
            if old_front is not None:
                picture.set_paintable(old_front)
            for wp_path, tex in old_textures.items():
                if wp_path in picture._wallpapers:
                    picture._textures[wp_path] = tex
            frame.set_child(picture)
            self._add_flip_overlays(frame, theme, picture)
            if theme.preview and old_front is None:
                self.decoder.request(picture, theme.preview, pic_w, pic_h)
        elif theme.preview:
            picture = FixedPicture(pic_w, pic_h)
            frame.set_child(picture)
            self.decoder.request(picture, theme.preview, pic_w, pic_h)
        else:
            picture = None
            icon = Gtk.Image.new_from_icon_name("applications-graphics")
            icon.set_pixel_size(64)
            icon.set_size_request(pic_w, pic_h)
            frame.set_child(icon)

        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)
        actions.set_tooltip_text(_("Theme actions (Menu key)"))
        actions.connect("clicked", lambda button, target=theme: self.show_actions(button, target))
        frame.add_overlay(actions)

        badges = Gtk.Box(spacing=4)
        badges.set_halign(Gtk.Align.START)
        badges.set_valign(Gtk.Align.END)
        badges.set_can_target(False)
        if isinstance(picture, FlipPicture):
            picture.set_badges(badges)
        for text, kind in (("current", "current") if theme.current else (None, None),
                           ("system", "") if theme.scope == "system" else (None, None),
                           ("hidden", "hidden-badge") if is_hidden else (None, None)):
            if text:
                badge = Gtk.Box()
                badge.add_css_class("tm-badge")
                badge.add_css_class("tm-tile-badge")
                if kind:
                    badge.add_css_class(kind)
                badge.append(Gtk.Label(label=_(text)))
                badges.append(badge)
        assigned_workspaces = [ws for ws, slug in self.workspace_theme_map.items() if slug == theme.slug]
        if assigned_workspaces:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.append(Gtk.Label(label=_("ws {0}").format(" · ".join(map(str, assigned_workspaces)))))
            badges.append(badge)
        frame.add_overlay(badges)
        card.append(frame)

        footer = Gtk.Box(spacing=6)
        footer.set_size_request(pic_w, -1)
        footer.set_margin_start(4)
        footer.set_margin_end(2)
        if theme.slug in self.favorites:
            star = Gtk.Label(label="★")
            star.add_css_class("tm-star")
            footer.append(star)
        name = Gtk.Label(label=theme_core.plain_text(theme.name), xalign=0)
        name.add_css_class("tm-tile-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(1)
        name.set_hexpand(True)
        key = self.theme_keys.get(theme.slug)
        if key:
            name.set_tooltip_text(_("{0} applies this theme").format(shortcuts.combo_label(key)))
        footer.append(name)
        if key:
            keyhint = Gtk.Label(label=shortcuts.combo_label(key).replace(" + ", "+"))
            keyhint.add_css_class("tm-keyhint")
            keyhint.set_ellipsize(Pango.EllipsizeMode.END)
            keyhint.set_max_width_chars(14)
            footer.append(keyhint)

        if not theme.current:
            apply_btn = Gtk.Button()
            apply_btn.add_css_class("tm-paste")
            apply_btn.add_css_class("suggested")
            apply_btn.add_css_class("tm-big-btn")
            apply_row = Gtk.Box(spacing=4)
            apply_row.append(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
            apply_row.append(Gtk.Label(label=_("Apply theme")))
            apply_btn.set_child(apply_row)
            apply_btn.connect("clicked", lambda *_, target=theme: self.apply_theme_action(target))
            footer.append(apply_btn)
        elif self.look_pending.get(theme.slug):
            reapply_btn = Gtk.Button()
            reapply_btn.add_css_class("tm-paste")
            reapply_btn.add_css_class("tm-big-btn")
            reapply_row = Gtk.Box(spacing=4)
            reapply_row.append(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
            reapply_row.append(Gtk.Label(label=_("Reapply")))
            reapply_btn.set_child(reapply_row)
            reapply_btn.set_tooltip_text(
                _("Not on the desktop yet: {0}").format(self.look_field_names(self.look_pending[theme.slug])))
            reapply_btn.connect("clicked", lambda *_, target=theme: self.reapply_look(target))
            footer.append(reapply_btn)

        wp_btn = Gtk.Button(icon_name="image-x-generic-symbolic")
        wp_btn.add_css_class("tm-paste")
        wp_btn.add_css_class("tm-big-btn")
        wp_btn.set_tooltip_text(_("Wallpapers"))
        wp_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Wallpapers")])
        wp_btn.connect("clicked", lambda *_, target=theme: self.open_wallpapers_for(target))
        footer.append(wp_btn)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("tm-big-close")
        close_btn.set_tooltip_text(_("Close preview (Esc)"))
        close_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Close preview (Esc)")])
        close_btn.connect("clicked", lambda *_: self.hide_theme_preview())
        footer.append(close_btn)
        card.append(footer)

        # Right-click anywhere on the big preview card opens ☰; left-clicks are left
        # unclaimed so every Gtk.Button inside the card (<, >, pin, ☰, Apply, Wallpapers, ×) works.
        right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        def on_card_right_click(gesture, _n, _x, _y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.show_actions(actions, theme)
        right_click.connect("released", on_card_right_click)
        card.add_controller(right_click)

        slot = self.big_preview_slot
        prev_child = slot.get_first_child()
        while prev_child is not None:
            slot.remove(prev_child)
            prev_child = slot.get_first_child()
        slot.append(card)
        slot.update_property([Gtk.AccessibleProperty.LABEL], [theme_core.plain_text(theme.name)])
        self.big_preview_theme = theme
        self.big_preview_picture = picture if isinstance(picture, FlipPicture) else None
        self.big_preview_actions = actions
        self.big_preview_size_px = (pic_w, pic_h)
        slot.set_visible(True)
        if isinstance(picture, FlipPicture):
            picture.start_big_preview(preserve_face=preserve_face)

    def on_theme_row_activated(self, row):
        if row is None or not hasattr(row, "tm_theme"):
            return
        if not getattr(self, "big_preview_enabled", True):
            self.open_wallpapers_for(row.tm_theme)
            return
        if self.theme_preview_open():
            self.hide_theme_preview()
            self.select_row(row)
            return
        self.select_row(row)
        self.show_theme_preview(row.tm_theme)

    def _point_in_big_preview(self, x: float, y: float) -> bool:
        if not self.theme_preview_open():
            return False
        found, bounds = self.big_preview_slot.compute_bounds(self.themes_overlay)
        return bool(found and bounds.contains_point(Graphene.Point().init(x, y)))

    def on_themes_overlay_pressed(self, _gesture, _n, x, y):
        self._overlay_press_outside = self.theme_preview_open() and not self._point_in_big_preview(x, y)

    def on_themes_overlay_released(self, _gesture, _n, x, y):
        if not self.theme_preview_open() or not self._overlay_press_outside:
            return
        self._overlay_press_outside = False
        if self._point_in_big_preview(x, y):
            return
        self.hide_theme_preview()

    def on_list_pointer(self, x: float, y: float, moved: bool):
        last, self.pointer_at = self.pointer_at, (round(x), round(y))
        if not moved or last is None or last == self.pointer_at or self.actions_menu_open() or self.theme_preview_open():
            return
        self.pointer_moved = True
        if self.stack.get_visible_child_name() != "themes":
            return
        inside = self.card.translate_coordinates(self.rows, x, y)
        if inside is None:
            return
        row = self.rows.pick(inside[0], inside[1], Gtk.PickFlags.DEFAULT)
        while row is not None and not isinstance(row, Gtk.FlowBoxChild):
            row = row.get_parent()
        if row is not None and hasattr(row, "tm_theme") and row is not self.rows.get_selected_row():
            self.select_row(row)

    @staticmethod
    def _row_flip_picture(row) -> FlipPicture | None:
        if row is None:
            return None
        child = row.get_child()
        first = child.get_first_child() if child is not None else None
        if first is None:
            return None
        inner = first.get_first_child()
        if isinstance(inner, FlipPicture):
            return inner
        if isinstance(first, FlipPicture):
            return first
        sub = inner.get_first_child() if inner is not None else None
        return sub if isinstance(sub, FlipPicture) else None

    def select_row(self, row):
        if self.theme_preview_open():
            slug = row.tm_theme.slug if (row is not None and hasattr(row, "tm_theme")) else None
            if slug != self.big_preview_theme.slug:
                self.hide_theme_preview()
        prev = getattr(self, "_peek_row", None)
        if prev is not None and prev is not row:
            prev_pic = self._row_flip_picture(prev)
            if prev_pic is not None:
                prev_pic.set_selected(False)
        self._peek_row = row
        if row is not None:
            self.rows.select_row(row)
            row.grab_focus()
            if (self.window is not None and self.window.get_visible()
                    and self.stack.get_visible_child_name() == "themes"
                    and not self.theme_preview_open()):
                pic = self._row_flip_picture(row)
                if pic is not None:
                    pic.set_selected(True)

    # --- list actions ----------------------------------------------------------------

    def toggle_show_hidden(self, *_args):
        self.show_hidden = not self.show_hidden
        if self.show_hidden:
            self.hidden_button.set_icon_name("view-conceal-symbolic")
            self.header_tooltips["hidden"] = _("Hide hidden themes and wallpapers")
        else:
            self.hidden_button.set_icon_name("view-reveal-symbolic")
            self.header_tooltips["hidden"] = _("Show hidden themes and wallpapers")
        self.set_header_tooltip("hidden")
        self.refresh_themes()
        self.refresh_wallpapers()

    def set_theme_hidden(self, theme: theme_core.ThemeInfo, hide: bool):
        if hide and theme.current:
            return  # the theme in use is never hidden
        if hide:
            self.hidden.add(theme.slug)
        else:
            self.hidden.discard(theme.slug)
        theme_core.save_hidden(self.hidden)
        self.sync_omarchy_switcher(unhidden=not hide)
        self.refresh_themes()

    def sync_omarchy_switcher(self, unhidden: bool = False):
        """Mirror the hidden list into Omarchy's own theme picker (opt-in)."""
        if not theme_core.sync_enabled():
            return
        hidden = set(self.hidden)
        def refresh():
            if unhidden:
                theme_core.invalidate_switcher_cache()
            theme_core.prune_switcher_previews(hidden)
            return theme_core.refresh_switcher_cache(SWITCHER_WRAPPER)
        self._async(refresh, lambda _ok, error: error and theme_core.notify(
            _("Could not change the Omarchy sync"), str(error)))

    def open_wallpapers_for(self, theme: theme_core.ThemeInfo):
        if theme is None:
            return
        self.active_theme = theme
        self.wp_filter = ""
        self.wp_title.set_label(_("{0} — wallpapers").format(theme_core.plain_text(theme.name)))
        system = theme.scope == "system"
        self.paste_button.set_tooltip_text(
            _("Add the image copied to the clipboard (from the web or the file manager)")
            + (" — " + _("stored in your per-theme folder") if system else ""))
        self.drop_note = (_("Stored in your wallpaper folder for this theme, next to its own wallpapers.")
                          if system else _("Stored in the theme's user wallpaper folder."))
        self.wp_ask_button.set_visible(self.wallpaper_ai_helper() is not None)
        self.refresh_wallpapers()
        self.stack.set_visible_child_name("wallpapers")

    def show_themes(self):
        self.stop_preview()
        if getattr(self, "ask_busy", None) is not None:
            self.ask_release()                      # leaving Ask AI: its local model's GPU memory back
        if getattr(self, "ask_prompt_drag", None) is not None:
            self.ask_prompt_drag_cancel()           # leaving Ask AI mid-drag: nothing moves
        back_to = self.active_theme.slug if self.active_theme else None
        self.active_theme = None
        self.stack.set_visible_child_name("themes")
        self.restore_settings_size()
        self.refresh_themes(keep=back_to)
        self.select_row(self.rows.get_selected_row())

    def restore_settings_size(self):
        previous = self.settings_previous_size
        if previous is not None:
            self.settings_previous_size = None
            self.resize_to(*previous)
            if self.position == self.settings_adjusted_position:
                self.move_to(self.settings_previous_position)
            self.settings_previous_position = None
            self.settings_adjusted_position = None

    # --- wallpapers from the web (wallhaven.cc) ----------------------------------------

    def open_web_search(self, ask: bool = False):
        theme = self.active_theme
        if theme is None:
            return
        if theme.slug != self.web_theme_slug:
            self.drop_web_requests()              # another theme's answers never land here (leaving drops them too)
        self.web_theme_slug = theme.slug
        self.browser = theme_catalog.default_browser()
        self.ws_title.set_label(_("{0} — search wallpapers").format(theme_core.plain_text(theme.name)))
        self.stack.set_visible_child_name("websearch")
        if not self.web_query:
            self.web_hint.set_label(self.web_hint_text())
        helper = self.wallpaper_ai_helper()
        self.fill_web_helpers(helper)
        self.use_web_helper(helper)
        self.web_ai_box.set_visible(helper is not None)
        self.web_helper_drop.set_visible(helper is not None)
        # One selector: in the Ask AI row with Ask AI, else next to the search field.
        place = self.web_ai_row if helper is not None else self.web_search_row
        if self.web_count_drop.get_parent() is not place:
            self.web_count_drop.get_parent().remove(self.web_count_drop)
            place.append(self.web_count_drop)
        self.web_count_drop.set_selected(theme_ai.WALLPAPER_COUNTS.index(self.web_count()))
        self.update_web_next()
        (self.web_ai_entry if ask and helper is not None else self.web_search).grab_focus()

    @staticmethod
    def wallpaper_ai_helper():
        """The helper wallpaper prompts use (the chosen one, else the first
        ready), or None while Ask AI is off or none is ready."""
        settings = theme_auto.settings()
        if not ask_page.enabled(settings):
            return None
        ready = theme_ai.ready_helpers()
        chosen = settings.get("ask_ai_helper")
        return next((h for h in ready if h["id"] == chosen), ready[0] if ready else None)

    def fill_web_helpers(self, helper):
        """The ready helpers in the drop-down, `helper` selected."""
        settings = theme_auto.settings()
        self.web_ai_ready = theme_ai.ready_helpers() if ask_page.enabled(settings) else []
        names = [h["name"] + (" · " + _("on this PC") if h.get("local") else "") for h in self.web_ai_ready]
        ids = [h["id"] for h in self.web_ai_ready]
        self.web_helper_filling = True                # filling is no choice of yours
        try:
            self.web_helper_drop.set_model(Gtk.StringList.new(names))
            if helper is not None and helper["id"] in ids:
                self.web_helper_drop.set_selected(ids.index(helper["id"]))
        finally:
            self.web_helper_filling = False

    def use_web_helper(self, helper):
        self.web_ai_helper = helper
        self.web_ai_web = helper is not None and theme_ai.searches_web(helper["id"], theme_auto.settings())
        if helper is not None:
            self.web_ai_button.set_tooltip_text(
                _("Ask {0} for search words: it gets your words and this theme's name and colors; the search "
                  "itself uses no tokens").format(helper["name"]))
        self.update_web_next()

    def web_helper_changed(self):
        """Another helper chosen: it asks from now on (an answer on its way
        stays), and the Ask AI page uses it too."""
        if self.web_helper_filling:
            return
        index = self.web_helper_drop.get_selected()
        if not 0 <= index < len(self.web_ai_ready):
            return
        helper = self.web_ai_ready[index]
        self.use_web_helper(helper)
        if theme_auto.settings().get("ask_ai_helper") != helper["id"]:
            self.save_setting(ask_ai_helper=helper["id"])

    def web_ai_face(self, busy: bool):
        """Ask, or Stop while the helper thinks (as the Ask AI page's button)."""
        face = Gtk.Box(spacing=6)
        face.append(Gtk.Image.new_from_icon_name("process-stop-symbolic" if busy else "mail-send-symbolic"))
        face.append(Gtk.Label(label=_("Stop") if busy else _("Ask")))
        self.web_ai_button.set_child(face)

    def web_busy(self):
        """The spinner while Ask AI thinks or a search loads."""
        on = self.web_ai_busy or self.web_loading
        self.web_spinner.set_visible(on)
        (self.web_spinner.start if on else self.web_spinner.stop)()

    def web_ai_ask(self):
        theme, helper = self.active_theme, self.web_ai_helper
        words = " ".join(self.web_ai_entry.get_text().split())
        if theme is None or helper is None or not words or self.web_ai_busy:
            return
        self.web_ai_busy = True
        self.web_ai_face(True)
        self.web_ai_entry.set_sensitive(False)
        self.web_busy()
        web = self.web_ai_web
        self.web_hint.set_label(self.web_hint_text(
            _("Asking {0} for wallpaper ideas and searching the web… (this can take a few minutes)").format(helper["name"])
            if web else _("Asking {0} for wallpaper ideas…").format(helper["name"])))
        colors = dict(theme_custom.load_palette(theme).colors)
        rejected = list(self.web_ai_searched[-20:])
        count = self.web_count()
        self.web_ai_request = words
        self.update_web_next()
        self.web_epoch += 1
        epoch = self.web_epoch
        self._async(lambda: theme_ai.ask_wallpapers(helper["id"], words, theme.name, colors, rejected, web=web,
                                                    count=count),
                    lambda result, error: self.web_ai_done(result, error, epoch))

    def web_ai_stop(self):
        theme_ai.cancel()

    def web_ai_done(self, result, error, epoch=None):
        if epoch is not None and epoch != self.web_epoch:
            return                                # a newer search or another theme: stale
        self.web_ai_busy = False
        self.web_ai_face(False)
        self.web_ai_entry.set_sensitive(True)
        self.web_busy()
        if error is not None or not result:
            self.web_hint.set_label(self.web_hint_text(_("Ask AI failed: {0}").format(
                error or _("no usable search words in the answer"))))
            self.update_web_next()
            return
        summary, queries, images = result
        self.web_more = bool(images)
        clear(self.web_ai_chips)
        if summary:
            label = Gtk.Label(label=summary, xalign=0, wrap=True, max_width_chars=40)
            label.add_css_class("tm-hint")
            self.web_ai_chips.append(label)
        for phrase in queries:
            chip = Gtk.Button(label=phrase)
            chip.add_css_class("tm-settings-chip")
            chip.set_tooltip_text(_("Search for “{0}”").format(phrase))
            chip.connect("clicked", lambda _b, q=phrase: self.web_ai_search_for(q))
            self.web_ai_chips.append(chip)
        self.web_ai_chips.set_visible(bool(summary or queries))
        self.web_pics = [web_images.picture(i) for i in images]
        if queries:
            self.web_ai_search_for(queries[0])
        else:
            self.web_query = ""
            self.run_web_search(force=True)
            self.web_hint.set_label(self.web_hint_text(_("{0} pictures from the web.").format(len(self.web_pics))))

    def web_ai_search_for(self, phrase: str):
        if phrase not in self.web_ai_searched:
            self.web_ai_searched.append(phrase)
        self.web_search.set_text(phrase)
        self.run_web_search()

    def close_web_search(self):
        self.drop_web_requests()                  # leaving the page: nothing it started lands later
        self.stop_preview()
        self.stack.set_visible_child_name("wallpapers")
        self.refresh_wallpapers()

    def web_hint_text(self, status: str = "") -> str:
        text = status or _("Type what you are looking for and press Enter. Results come from {0}: safe for work, "
                           "landscape, at least 1920×1080.").format(wallpaper_search.SOURCE)
        if self.browser is None:
            text += " " + _("No default web browser is set, so wallpaper pages can't be opened.")
        return text

    def run_web_search(self, force: bool = False):
        query = wallpaper_search.clean_query(self.web_search.get_text()) if not force else ""
        if not query and not force:
            return
        if "wallhaven" in self.web_next_pending:
            self.web_next_stop.set()              # a new search: Next's page loading for the old one ends
            self.web_next_pending.discard("wallhaven")
            self.web_loading = False
        self.drop_web_ask()                       # and Next's web ask for the old one
        self.drop_first_ask()                     # and a first Ask AI answer still on its way
        self.web_query = query
        self.web_page_no = 0
        self.web_last_page = 1 if query else 0
        self.web_results = []
        self.web_shown = 0
        self.web_generation += 1
        self.web_pictures = {}
        self.web_items = list(self.web_pics)       # Ask AI's pictures from the web first
        child = self.web_grid.get_first_child()
        while child is not None:
            self.web_grid.remove(child)
            child = self.web_grid.get_first_child()
        theme = self.active_theme
        for item in self.web_pics:
            added = theme is not None and web_images.already_added(None, theme.slug, item)
            self.web_grid.append(self.build_web_tile(item, added))
        if self.web_pics:
            self.load_web_thumbs(list(self.web_pics), self.web_generation)
        self.load_more_web_results()
        self.update_web_next()

    def rebuild_web_tiles(self):
        """The search results shown so far, again at the current tile size."""
        items = getattr(self, "web_items", [])
        if not items:
            return
        child = self.web_grid.get_first_child()
        while child is not None:
            self.web_grid.remove(child)
            child = self.web_grid.get_first_child()
        self.web_pictures = {}
        theme = self.active_theme
        for item in items:
            added = theme is not None and self.web_already_added(theme, item)
            self.web_grid.append(self.build_web_tile(item, added))
        self.load_web_thumbs([r for r in items if r.id in self.web_pictures], self.web_generation)

    @staticmethod
    def is_web_picture(item) -> bool:
        return isinstance(item, web_images.WebPicture)

    def web_already_added(self, theme, item) -> bool:
        if self.is_web_picture(item):
            return web_images.already_added(None, theme.slug, item)
        return wallpaper_search.already_added(None, theme.slug, item)

    def web_count(self) -> int:
        return theme_ai.wallpaper_count(theme_auto.settings())

    def web_count_changed(self):
        index = self.web_count_drop.get_selected()
        if 0 <= index < len(theme_ai.WALLPAPER_COUNTS):
            count = theme_ai.WALLPAPER_COUNTS[index]
            if count != self.web_count():
                self.save_setting(wallpaper_count=count)
        self.update_web_next()

    def web_more_results(self) -> bool:
        return bool(self.web_query) and (len(self.web_results) > self.web_shown or
                                         self.web_page_no < self.web_last_page)

    def update_web_next(self):
        """Next at the end of the grid: what it does, in its tooltip."""
        count = self.web_count()
        helper = self.web_ai_helper
        if self.web_next_pending:
            # While Next fetches it is Cancel (as the other stop buttons).
            face = Gtk.Box(spacing=6)
            face.append(Gtk.Image.new_from_icon_name("process-stop-symbolic"))
            face.append(Gtk.Label(label=_("Cancel")))
            self.web_next.set_child(face)
            self.web_next.set_tooltip_text(None)
            self.web_next.set_visible(True)
            return
        web = self.web_more and helper is not None and bool(self.web_ai_request)
        wallhaven = self.web_more_results()
        self.web_next.set_visible((web or wallhaven) and not self.web_ai_busy and not self.web_loading)
        face = Gtk.Box(spacing=6)
        face.append(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        face.append(Gtk.Label(label=_("Next {0}").format(count)))
        self.web_next.set_child(face)
        tips = []
        if web:
            tips.append(_("{0} more from the web (asks {1} again)").format(count, helper["name"]))
        if wallhaven:
            tips.append(_("{0} more from {1}").format(count, wallpaper_search.SOURCE))
        self.web_next.set_tooltip_text(" · ".join(tips) or None)

    def web_next_batch(self):
        """The next batch of each kind: pictures from the web (asks the helper
        again, without the ones shown; tokens), wallhaven results (no tokens)."""
        self.web_next_token += 1
        self.web_next_stop = threading.Event()
        token = self.web_next_token
        if self.web_more and self.web_ai_helper is not None and self.web_ai_request and not self.web_ai_busy:
            self.web_next_pending.add("web")
            self.web_more_from_web(token)
        if self.web_more_results() and not self.web_loading:
            self.web_next_pending.add("wallhaven")
            self.load_more_web_results(token)
        self.update_web_next()

    def web_next_done(self, kind: str, token) -> bool:
        """Whether an answer of what Next started still counts (not cancelled)."""
        if token is None:
            return True
        if token != self.web_next_token:
            return False
        self.web_next_pending.discard(kind)
        return True

    def drop_web_ask(self):
        """A web ask Next started no longer fits (new search, other theme):
        its answer is stale, the helper's request ends."""
        if "web" not in self.web_next_pending:
            return
        self.web_next_token += 1
        self.web_next_pending.discard("web")
        theme_ai.cancel()
        self.web_ai_busy = False
        self.web_ai_face(False)
        self.web_busy()

    def drop_first_ask(self):
        """The first Ask AI request no longer fits: its answer is stale."""
        self.web_epoch += 1
        if self.web_ai_busy and "web" not in self.web_next_pending:
            theme_ai.cancel()
            self.web_ai_busy = False
            self.web_ai_face(False)
            self.web_ai_entry.set_sensitive(True)
            self.web_busy()

    def drop_web_requests(self):
        """Another theme: every request still running for the page is stale."""
        self.web_generation += 1                  # wallhaven answers of the old search
        self.web_next_token += 1                  # what Next started
        self.web_next_stop.set()
        if "wallhaven" in self.web_next_pending or self.web_loading:
            self.web_next_pending.discard("wallhaven")
            self.web_loading = False
        if "web" in self.web_next_pending:
            self.web_next_pending.discard("web")
            theme_ai.cancel()
            self.web_ai_busy = False
            self.web_ai_face(False)
        self.drop_first_ask()
        self.web_busy()

    def web_cancel_next(self):
        """Cancel: stop the helper's web ask and the page loading Next started;
        what is shown stays, Next works again."""
        pending = set(self.web_next_pending)
        self.web_next_token += 1                 # their answers are stale now
        self.web_next_stop.set()
        self.web_next_pending.clear()
        if "web" in pending:
            theme_ai.cancel()
            self.web_ai_busy = False
            self.web_ai_face(False)
        if "wallhaven" in pending:
            self.web_loading = False
        self.web_busy()
        self.web_hint.set_label(self.web_hint_text(_("Cancelled.")))
        self.update_web_next()

    def web_more_from_web(self, token=None):
        theme, helper = self.active_theme, self.web_ai_helper
        if theme is None or helper is None:
            self.web_next_pending.discard("web")
            return
        self.web_ai_busy = True
        self.web_ai_face(True)
        self.web_busy()
        self.update_web_next()
        self.web_hint.set_label(self.web_hint_text(
            _("Asking {0} for more pictures from the web…").format(helper["name"])))
        colors = dict(theme_custom.load_palette(theme).colors)
        rejected = list(self.web_ai_searched[-20:])
        seen = [p.url for p in self.web_pics][:theme_ai.MAX_SEEN]
        words, count = self.web_ai_request, self.web_count()
        slug = theme.slug
        self._async(lambda: theme_ai.ask_wallpapers(helper["id"], words, theme.name, colors, rejected, web=True,
                                                    count=count, seen=seen),
                    lambda result, error: self.web_more_done(result, error, token, slug))

    def web_more_done(self, result, error, token=None, slug=None):
        if not self.web_next_done("web", token):
            return                                # cancelled: nothing it brings is shown
        if slug is not None and (self.active_theme is None or self.active_theme.slug != slug):
            self.web_ai_busy = False              # asked for another theme: not shown here
            self.web_ai_face(False)
            self.web_busy()
            return
        self.web_ai_busy = False
        self.web_ai_face(False)
        self.web_busy()
        if error is not None or not result:
            self.web_hint.set_label(self.web_hint_text(_("Ask AI failed: {0}").format(
                error or _("no usable search words in the answer"))))
            self.web_more = False
            self.update_web_next()
            return
        known = {p.url for p in self.web_pics}
        fresh = [web_images.picture(i) for i in result[2] if i["url"] not in known]
        theme = self.active_theme
        at = len(self.web_pics)                   # after the web pictures shown, before wallhaven's
        for offset, item in enumerate(fresh):
            added = theme is not None and web_images.already_added(None, theme.slug, item)
            self.web_grid.insert(self.build_web_tile(item, added), at + offset)
        self.web_pics.extend(fresh)
        self.web_items = list(self.web_pics) + self.web_results[:self.web_shown]
        self.web_more = bool(fresh)
        self.web_hint.set_label(self.web_hint_text(
            _("{0} more pictures from the web.").format(len(fresh)) if fresh else _("No more pictures from the web.")))
        if fresh:
            self.load_web_thumbs(fresh, self.web_generation)
        self.update_web_next()

    def load_more_web_results(self, token=None):
        """The next batch of wallhaven results, loading pages as needed."""
        if self.web_loading or not self.web_more_results():
            self.web_next_pending.discard("wallhaven")
            return
        self.web_loading = True
        self.web_busy()
        self.update_web_next()
        want = self.web_shown + self.web_count()
        query, generation = self.web_query, self.web_generation
        have, page, last = len(self.web_results), self.web_page_no, self.web_last_page
        known = {r.id for r in self.web_results}
        stop = self.web_next_stop if token is not None else threading.Event()
        self.web_hint.set_label(self.web_hint_text(
            _("Searching {0} for “{1}”…").format(wallpaper_search.SOURCE, query)))

        def fetch():
            found, error, p, lp = [], None, page, last
            while have + len(found) < want and p < lp and not stop.is_set():
                try:
                    results, lp = wallpaper_search.search(query, p + 1)
                except (OSError, ValueError) as exc:
                    error = exc
                    break
                p += 1
                fresh = [r for r in results if r.id not in known]
                known.update(r.id for r in fresh)
                found += fresh
                if not fresh:
                    break
            return found, p, lp, error
        self._async(fetch, lambda result, error: self.show_web_results(generation, want, result, error, token))

    def show_web_results(self, generation, want, result, error, token=None):
        if generation != self.web_generation or not self.web_next_done("wallhaven", token):
            return  # answer to an older search, or Next was cancelled
        self.web_loading = False
        self.web_busy()
        found, page, last_page, fetch_error = result if result else ([], self.web_page_no, self.web_page_no, error)
        error = error or fetch_error
        self.web_page_no, self.web_last_page = page, last_page
        self.web_results += found
        batch = self.web_results[self.web_shown:want]
        self.web_shown = min(want, len(self.web_results))
        theme = self.active_theme
        for item in batch:
            added = theme is not None and wallpaper_search.already_added(None, theme.slug, item)
            self.web_grid.append(self.build_web_tile(item, added))
        self.web_items = list(self.web_pics) + self.web_results[:self.web_shown]
        if error is not None and not batch:
            status = _("Search failed: {0}").format(error)
        elif not self.web_results:
            status = (_("{0} pictures from the web.").format(len(self.web_pics)) if self.web_pics
                      else _("Nothing found for “{0}”. Try other words.").format(self.web_query))
        else:
            more = _(" Next shows more.") if self.web_more_results() else ""
            status = _("{0} of {1}+ wallpapers for “{2}”.{3}").format(
                self.web_shown, len(self.web_results), self.web_query, more) + " " + _("Enter on a tile adds it.")
        self.web_hint.set_label(self.web_hint_text(status))
        self.load_web_thumbs([r for r in batch if r.id in self.web_pictures], generation)
        self.update_web_next()

    @staticmethod
    def children_of(widget) -> list:
        out, child = [], widget.get_first_child()
        while child is not None:
            out.append(child)
            child = child.get_next_sibling()
        return out

    def build_web_tile(self, item, added: bool) -> Gtk.Widget:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")
        picture = Gtk.Picture()
        picture.set_overflow(Gtk.Overflow.HIDDEN)     # clipped to its corners (Settings › Corners)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(self.tile_w, self.tile_w * 9 // 16)
        self.web_pictures[item.id] = picture
        frame = Gtk.Overlay()
        frame.set_child(picture)
        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)
        actions.set_tooltip_text(_("Wallpaper actions (Enter)"))
        actions.connect("clicked", lambda button, i=item: self.show_web_menu(button, i))
        frame.add_overlay(actions)
        badges = Gtk.Box(spacing=4)
        badges.set_halign(Gtk.Align.START)
        badges.set_valign(Gtk.Align.END)
        for text, shown in ((_("web"), self.is_web_picture(item)), (_("added"), added)):
            if shown:
                badge = Gtk.Box()
                badge.add_css_class("tm-badge")
                badge.add_css_class("tm-tile-badge")
                badge.append(Gtk.Label(label=text))
                badges.append(badge)
        if self.is_web_picture(item):
            badges.set_tooltip_text(item.url)
        frame.add_overlay(badges)
        tile.append(frame)
        caption = Gtk.Label(label=item.file_name if self.is_web_picture(item) else item.resolution.replace("x", " × "),
                            xalign=0.5)
        caption.add_css_class("tm-tile-label")
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        caption.set_max_width_chars(1)
        tile.append(caption)
        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child.tm_web = item
        child.tm_added = added
        child.actions_button = actions
        return child

    def load_web_thumbs(self, items, generation):
        queue = list(items)
        lock = threading.Lock()

        def worker():
            while generation == self.web_generation:
                with lock:
                    if not queue:
                        return
                    item = queue.pop(0)
                if self.is_web_picture(item):
                    try:
                        path = web_images.get(item.url)
                    except (OSError, ValueError):
                        path = None
                        GLib.idle_add(self.drop_web_picture, item, generation)   # no usable picture: left out
                else:
                    path = wallpaper_search.fetch_thumb(item)
                if path is not None:
                    GLib.idle_add(self.show_web_thumb, item.id, str(path))

        for _ in range(min(PREVIEW_WORKERS, len(queue))):
            threading.Thread(target=worker, daemon=True).start()

    def drop_web_picture(self, item, generation=None):
        if generation is not None and generation != self.web_generation:
            return GLib.SOURCE_REMOVE             # an older search's failure: the new tiles stay
        if item in self.web_pics:
            self.web_pics.remove(item)
        self.web_items = [i for i in getattr(self, "web_items", []) if i != item]
        self.web_pictures.pop(item.id, None)
        for child in self.children_of(self.web_grid):
            if getattr(child, "tm_web", None) == item:
                self.web_grid.remove(child)
                break
        return GLib.SOURCE_REMOVE

    def show_web_thumb(self, wid: str, path: str):
        picture = self.web_pictures.pop(wid, None)
        if picture is not None:
            self.decoder.request(picture, path, self.tile_w, self.tile_w * 9 // 16)
        return GLib.SOURCE_REMOVE

    def show_web_menu(self, button, item):
        self.show_menu(button, lambda popover: self.build_web_menu(popover, item))

    def build_web_menu(self, popover, item) -> Gtk.Box:
        theme = self.active_theme
        box = self.menu_box()

        def entry(label, handler, icon_name, **kwargs):
            return self.menu_item(box, popover, label, handler, icon_name, **kwargs)

        web = self.is_web_picture(item)
        if theme is not None:
            name = theme_core.plain_text(theme.name)
            # Apply in one step: the picture into the theme (or its copy there), as its
            # wallpaper, and the theme applied when it is not in use.
            entry(_("Apply"), lambda: self.apply_web_wallpaper(item), "object-select-symbolic",
                  tooltip=_("Adds it to {0} and sets it as the background").format(name) if theme.current
                  else _("Adds it to {0}, applies {0} and sets it as the background").format(name))
        if theme is not None and self.web_already_added(theme, item):
            entry(_("Already added"), lambda: None, "object-select-symbolic", sensitive=False)
        elif theme is not None:
            entry(_("Add to {0}").format(theme_core.plain_text(theme.name)), lambda: self.add_web_wallpaper(item, False),
                  "list-add-symbolic", tooltip=_("Saved in {0}").format(f"~/.config/omarchy/backgrounds/{theme.slug}/"))
        entry(_("Preview on desktop"), lambda: self.preview_web(item, PREVIEW_SECONDS), "view-reveal-symbolic",
              tooltip=_("Loads the full image and shows it for {0} seconds; or hold Space on the tile").format(PREVIEW_SECONDS))
        self.menu_separator(box)
        if self.browser is not None and web:
            entry(_("Open on {0}").format(item.host), lambda: web_images.open_page(item),
                  "web-browser-symbolic", tooltip=item.page or item.url)
        elif self.browser is not None:
            entry(_("Open on {0}").format(wallpaper_search.SOURCE), lambda: wallpaper_search.open_page(item),
                  "web-browser-symbolic", tooltip=item.page)
        else:
            entry(_("Open page (no browser)"), lambda: None, "web-browser-symbolic", sensitive=False,
                  tooltip=_("No default web browser is set"))
        return box

    def web_added_copy(self, theme, item) -> Path | None:
        """The picture's copy among the theme's wallpapers (by file name), or None."""
        if self.is_web_picture(item):
            cached = web_images.cached(item.url)
            if cached is None:
                return None
            name = web_images.file_name_of(item.url, cached)
        else:
            name = item.file_name
        return next((w.path for w in theme_core.list_backgrounds(None, theme) if w.name == name), None)

    def apply_web_wallpaper(self, item):
        """Apply: add the picture to the theme (or use its copy there), apply the
        theme when it is not in use, then make the picture its background
        (remembered as the theme's wallpaper, as the editor's Save and apply does)."""
        theme = self.active_theme
        if theme is None:
            return
        self.web_hint.set_label(self.web_hint_text(_("Applying {0}…").format(item.file_name)))

        def work():
            path = self.web_added_copy(theme, item)
            added = path is None
            if added:
                fetch = web_images.download if self.is_web_picture(item) else wallpaper_search.download
                path = fetch(None, theme.slug, item)
            if not theme.current and not theme_auto.apply_theme(theme.slug):
                raise RuntimeError(_("omarchy theme set failed"))
            if not theme_core.set_desktop_background(path):
                raise RuntimeError(_("Could not set background"))
            theme_auto.remember_base_wallpaper(theme.slug, path)
            return path, added
        self._async(work, lambda result, error: self.after_web_apply(theme, item, result, error))

    def after_web_apply(self, theme, item, result, error):
        name = theme_core.plain_text(theme.name)
        if error is not None or not result:
            text = _("Could not apply {0}: {1}").format(item.file_name, error)
            self.web_hint.set_label(self.web_hint_text(text))
            theme_core.notify(_("Could not apply theme"), text)
            return
        path, added = result
        text = _("Applied {0} with {1}.").format(name, item.file_name)
        self.web_hint.set_label(self.web_hint_text(text))
        theme_core.notify(_("Theme applied"), text)
        if added:
            self.mark_web_added(item)
        if not theme.current:
            # The popup takes on the theme's colors, and it is the theme in use now.
            self.current_slug = theme_core.current_theme()
            if self.theme.reload():
                self.apply_theme_css()
            self.active_theme = next((t for t in theme_core.discover_themes() if t.slug == theme.slug), theme)

    def mark_web_added(self, item):
        for child in self.children_of(self.web_grid):
            if getattr(child, "tm_web", None) == item:
                index = child.get_index()
                self.web_grid.remove(child)
                self.web_pictures.pop(item.id, None)
                self.web_grid.insert(self.build_web_tile(item, True), index)
                thumb = web_images.cached(item.url) if self.is_web_picture(item) else wallpaper_search.thumb_path(item)
                if thumb is not None and thumb.is_file():
                    self.show_web_thumb(item.id, str(thumb))
                break

    def add_web_wallpaper(self, item, set_background: bool):
        theme = self.active_theme
        if theme is None:
            return
        self.web_hint.set_label(self.web_hint_text(f"Downloading {item.file_name}…"))
        fetch = web_images.download if self.is_web_picture(item) else wallpaper_search.download
        self._async(lambda: fetch(None, theme.slug, item),
                    lambda path, error: self.after_web_add(theme, item, path, error, set_background))

    def after_web_add(self, theme, item, path, error, set_background: bool):
        if error is not None:
            theme_core.notify(_("Could not add wallpaper"), f"{item.file_name}: {error}")
            self.web_hint.set_label(self.web_hint_text(_("Could not add {0}: {1}").format(item.file_name, error)))
            return
        theme_core.notify(_("Wallpaper added"), f"{path.name} → {theme_core.plain_text(theme.name)}")
        self.web_hint.set_label(self.web_hint_text(_("Added {0} to {1}.").format(path.name, theme_core.plain_text(theme.name))))
        for child in self.children_of(self.web_grid):
            if getattr(child, "tm_web", None) == item:
                index = child.get_index()
                self.web_grid.remove(child)
                self.web_pictures.pop(item.id, None)
                self.web_grid.insert(self.build_web_tile(item, True), index)
                thumb = web_images.cached(item.url) if self.is_web_picture(item) else wallpaper_search.thumb_path(item)
                if thumb is not None and thumb.is_file():
                    self.show_web_thumb(item.id, str(thumb))
                break
        if set_background and theme.current:
            self.set_background(theme_core.WallpaperInfo(path, path.name, "user", True))

    def focused_web_tile(self):
        widget = self.window.get_focus()
        while widget is not None and not isinstance(widget, Gtk.FlowBoxChild):
            widget = widget.get_parent()
        return widget if widget is not None and hasattr(widget, "tm_web") else None

    # --- import from omarchy.org -------------------------------------------------------

    def open_import(self):
        self.active_theme = None
        self.stack.set_visible_child_name("import")
        self.browser = theme_catalog.default_browser()
        self.import_search.set_text("")
        self.import_search.grab_focus()
        if self.catalog:
            self.fill_catalog(self.catalog, None)
        self.load_catalog(force=False)

    def load_catalog(self, force: bool):
        self.import_hint.set_label(_("Loading themes from omarchy.org…"))
        self._async(lambda: theme_catalog.load_catalog(force=force),
                    lambda result, error: self.fill_catalog(*result) if result else
                    self.import_hint.set_label(_("Could not load the theme list: {0}").format(error)))

    def import_hint_text(self, count: int) -> str:
        text = _("{0} themes from omarchy.org. Enter opens a theme's menu: install, install and apply, or open "
                 "its page.").format(count)
        if self.browser is None:
            text += " " + _("No default web browser is set, so theme pages can't be opened. Install one with Menu → "
                            "Install → Web Browser, or set one with xdg-settings set default-web-browser <app>.desktop.")
        return text

    def fill_catalog(self, themes, _fetched_at):
        self.catalog = themes
        installed = {t.slug for t in theme_core.discover_themes()}
        child = self.import_grid.get_first_child()
        while child is not None:
            self.import_grid.remove(child)
            child = self.import_grid.get_first_child()
        self.catalog_pictures = {}
        for entry in themes:
            self.import_grid.append(self.build_catalog_tile(entry, entry.slug in installed))
        self.import_hint.set_label(self.import_hint_text(len(themes)))
        self.load_previews([t for t in themes if t.slug in self.catalog_pictures])
        return GLib.SOURCE_REMOVE

    def build_catalog_tile(self, entry, installed: bool) -> Gtk.Widget:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")
        picture = Gtk.Picture()
        picture.set_overflow(Gtk.Overflow.HIDDEN)     # clipped to its corners (Settings › Corners)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(self.tile_w, self.tile_w * 9 // 16)
        cached = theme_catalog.preview_path(entry)
        if cached.is_file():
            self.decoder.request(picture, cached, self.tile_w, self.tile_w * 9 // 16)
        else:
            self.catalog_pictures[entry.slug] = picture
        frame = Gtk.Overlay()
        frame.set_child(picture)
        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)
        actions.set_tooltip_text(_("Theme actions (Enter)"))
        actions.connect("clicked", lambda button, e=entry: self.show_catalog_menu(button, e))
        frame.add_overlay(actions)
        if installed:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.END)
            badge.append(Gtk.Label(label=_("installed")))
            frame.add_overlay(badge)
        tile.append(frame)
        caption = Gtk.Label(label=entry.name, xalign=0.5)
        caption.add_css_class("tm-tile-label")
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        caption.set_max_width_chars(1)
        tile.append(caption)
        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child.tm_catalog = entry
        child.tm_installed = installed
        child.actions_button = actions
        return child

    def catalog_filter(self, child) -> bool:
        query = self.import_search.get_text().strip().casefold()
        entry = getattr(child, "tm_catalog", None)
        return entry is None or not query or query in entry.name.casefold() \
            or query in entry.repo.casefold()

    def load_previews(self, entries):
        """Download missing previews a few at a time; each lands on its tile."""
        queue = list(entries)
        lock = threading.Lock()

        def worker():
            while True:
                with lock:
                    if not queue:
                        return
                    entry = queue.pop(0)
                path = theme_catalog.fetch_preview(entry)
                if path is not None:
                    GLib.idle_add(self.show_preview, entry.slug, str(path))

        for _ in range(min(PREVIEW_WORKERS, len(queue))):
            threading.Thread(target=worker, daemon=True).start()

    def show_preview(self, slug: str, path: str):
        picture = self.catalog_pictures.pop(slug, None)
        if picture is not None:
            self.decoder.request(picture, path, self.tile_w, self.tile_w * 9 // 16)
        return GLib.SOURCE_REMOVE

    def show_catalog_menu(self, button, entry):
        self.show_menu(button, lambda popover: self.build_catalog_menu(popover, entry))

    def build_catalog_menu(self, popover, entry) -> Gtk.Box:
        box = self.menu_box()
        installed = entry.slug in {t.slug for t in theme_core.discover_themes()}

        def item(label, handler, icon_name, **kwargs):
            return self.menu_item(box, popover, label, handler, icon_name, **kwargs)

        if installed:
            item(_("Installed"), lambda: None, "object-select-symbolic", sensitive=False)
            item(_("Apply theme"), lambda: self.apply_theme_action(
                next(t for t in theme_core.discover_themes() if t.slug == entry.slug)),
                "media-playback-start-symbolic")
        else:
            item(_("Install"), lambda: self.install_catalog(entry, apply=False), "folder-download-symbolic",
                 tooltip=_("git clone into {0}").format(f"~/.config/omarchy/themes/{entry.slug}"))
            item(_("Install and apply"), lambda: self.install_catalog(entry, apply=True),
                 "media-playback-start-symbolic")
        self.menu_separator(box)
        if self.browser is not None:
            item(_("Open theme page"), lambda: theme_catalog.open_in_browser(entry.repo), "web-browser-symbolic",
                 tooltip=entry.repo)
        else:
            item(_("Open theme page (no browser)"), lambda: None, "web-browser-symbolic", sensitive=False,
                 tooltip=_("No default web browser is set"))
        return box

    def install_catalog(self, entry, apply: bool):
        self.import_hint.set_label(_("Installing {0}…").format(entry.name))

        def install():
            path = theme_catalog.install(entry)
            if theme_core.sync_enabled():
                theme_core.refresh_switcher_cache(SWITCHER_WRAPPER)  # see perform_delete_theme
            return path
        self._async(install,
                    lambda path, error: self.after_install(entry, apply, error))

    def after_install(self, entry, apply: bool, error):
        if error is not None:
            theme_core.notify(_("Could not install theme"), f"{entry.name}: {error}")
            self.import_hint.set_label(self.import_hint_text(len(self.catalog)))
            return
        theme_core.notify(_("Theme installed"), entry.name)
        if apply:
            theme = next((t for t in theme_core.discover_themes() if t.slug == entry.slug), None)
            if theme is not None:
                self.apply_theme_action(theme)
                return
        self.fill_catalog(self.catalog, None)

    def focused_catalog_tile(self):
        widget = self.window.get_focus()
        while widget is not None and not isinstance(widget, Gtk.FlowBoxChild):
            widget = widget.get_parent()
        return widget if widget is not None and hasattr(widget, "tm_catalog") else None

    def first_visible_catalog_tile(self):
        child = self.import_grid.get_first_child()
        while child is not None and not child.get_child_visible():
            child = child.get_next_sibling()
        return child

    def apply_theme_action(self, theme: theme_core.ThemeInfo):
        # Applying a hidden theme means it is wanted again: unhide it first, so
        # the theme-set hook's picker rebuild already includes it.
        if theme.slug in self.hidden:
            self.hidden.discard(theme.slug)
            try:
                theme_core.unhide_theme(theme.slug)
            except OSError:
                pass
        self._async(lambda: theme_auto.apply_theme(theme.slug),
                    lambda ok, error: self.after_apply(theme, ok, error))

    def after_apply(self, theme: theme_core.ThemeInfo, ok: bool, error: Exception | None):
        if ok:
            theme_core.notify(_("Theme applied"), theme.name)
        else:
            theme_core.notify(_("Could not apply theme"),
                              f"{theme.name}: {error or _('omarchy theme set failed')}")
        # The popup stays open: take on the new theme's colors and mark it current.
        self.current_slug = theme_core.current_theme()
        if self.theme.reload():
            self.apply_theme_css()
        self.hidden = self.load_hidden_list()
        self.refresh_look_pending()
        self.refresh_themes(keep=theme.slug)
        if self.stack.get_visible_child_name() == "import":
            self.fill_catalog(self.catalog, None)
        else:
            self.focus_selected_theme()

    # --- following theme changes made elsewhere ---------------------------------------------

    def watch_current_theme(self):
        """Notice when the theme changes outside the popup (a theme's key,
        the bar menu, day/night, Omarchy's picker): `omarchy theme set`
        writes theme.name last, once the new theme is in place."""
        self.current_slug = theme_core.current_theme()
        self.theme_sync = 0
        folder = theme_core.default_roots().state_current
        try:
            self.state_monitor = Gio.File.new_for_path(str(folder)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            self.state_monitor = None
            return
        self.state_monitor.connect("changed", self.on_state_changed)

    def on_state_changed(self, _monitor, file, other, _event):
        names = {f.get_basename() for f in (file, other) if f is not None}
        if "theme.name" in names:
            if self.theme_sync:
                GLib.source_remove(self.theme_sync)
            self.theme_sync = GLib.timeout_add(300, self.follow_theme_change)
        elif "background" in names and (getattr(self, "flip_3d", False) or self.theme_preview_open()):
            GLib.idle_add(self.follow_background_change)

    def follow_background_change(self):
        self.current_bg = theme_auto.current_background()
        row = self.rows.get_first_child()
        while row is not None:
            theme = getattr(row, "tm_theme", None)
            if theme is not None and theme.current:
                pic = self._flip_picture_in(row)          # wide tile or list row (with its overlay)
                if pic is not None:
                    pic.set_active_wallpaper(self.current_bg, theme_current=True, promote_on_reset=True)
            row = row.get_next_sibling()
        if self.theme_preview_open() and self.big_preview_theme.current and self.big_preview_picture is not None:
            self.big_preview_picture.set_active_wallpaper(self.current_bg, theme_current=True, promote_on_reset=True)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _flip_picture_in(widget, depth: int = 6):
        """The card's FlipPicture, a few levels below the row (whatever the layout puts between)."""
        stack = [(widget, 0)]
        while stack:
            node, level = stack.pop()
            if isinstance(node, FlipPicture):
                return node
            if level >= depth or node is None:
                continue
            child = node.get_first_child()
            while child is not None:
                stack.append((child, level + 1))
                child = child.get_next_sibling()
        return None

    def follow_theme_change(self):
        self.theme_sync = 0
        slug = theme_core.current_theme()
        if not slug or slug == getattr(self, "current_slug", None):
            return GLib.SOURCE_REMOVE
        self.current_slug = slug
        self.refresh_look_pending()
        if self.theme.reload():
            self.apply_theme_css()
        self.hidden = self.load_hidden_list()
        selected = self.rows.get_selected_row()
        self.refresh_themes(keep=selected.tm_theme.slug if selected is not None else slug)
        page = self.stack.get_visible_child_name()
        if self.active_theme is not None:
            # "Set as background" and friends depend on which theme is current.
            self.active_theme = next((t for t in self.theme_list if t.slug == self.active_theme.slug),
                                     self.active_theme)
            if page == "wallpapers":
                self.refresh_wallpapers()
        if page == "import":
            self.fill_catalog(self.catalog, None)
        elif page == "themes":
            self.focus_selected_theme()
        return GLib.SOURCE_REMOVE

    def confirm_delete_theme(self, theme: theme_core.ThemeInfo):
        message = _("Delete theme “{0}”?").format(theme_core.plain_text(theme.name))
        notes = []
        if (theme_core.default_roots().system_themes / theme.slug).is_dir():
            notes.append(_("Only your copy is removed; the system version stays."))
        notes.append(_("Wallpapers you added to it are kept."))
        if theme_core.default_roots().trash is not None:
            notes.append(_("The theme goes to the trash: Undo brings it back."))
        self.ask_confirm(message + "\n\n" + "\n".join(notes), _("Delete theme"),
                         lambda: self.perform_delete_theme(theme))

    def perform_delete_theme(self, theme: theme_core.ThemeInfo):
        """The confirmed step: actually remove the theme (off-thread)."""
        receipts: list = []

        def remove():
            path = theme_core.remove_theme(None, theme.slug, receipt=receipts)
            # The theme folder changed: rebuild Omarchy's picker cache now,
            # filtered, before anything else rebuilds it unfiltered.
            if theme_core.sync_enabled():
                theme_core.refresh_switcher_cache(SWITCHER_WRAPPER)
            return path
        self._async(remove, lambda _path, error: (self.after_delete_theme(theme, error, receipts),
                                                  self.refresh_shortcuts()))

    def after_delete_theme(self, theme: theme_core.ThemeInfo, error: Exception | None,
                           receipts: list = ()):
        if error is None:
            theme_core.notify(_("Theme removed"), theme.name)
            if receipts:
                self.offer_undo(_("“{0}” moved to the trash").format(theme_core.plain_text(theme.name)), list(receipts))
            else:
                self.prune_shortcuts()   # no undo: its key goes right away
        else:
            theme_core.notify(_("Could not delete theme"), f"{theme.name}: {error}")
        self.refresh_themes(keep=self.neighbor_slug(theme.slug) if error is None else theme.slug)
        self.focus_selected_theme()

    def focus_selected_theme(self):
        if self.stack.get_visible_child_name() == "themes":
            self.select_row(self.rows.get_selected_row())

    def neighbor_slug(self, slug: str) -> str | None:
        """The theme listed after `slug` (or before it, at the end): where the
        selection goes once `slug` is gone."""
        slugs = []
        row = self.rows.get_first_child()
        while row is not None:
            if hasattr(row, "tm_theme"):
                slugs.append(row.tm_theme.slug)
            row = row.get_next_sibling()
        if slug not in slugs:
            return None
        i = slugs.index(slug)
        rest = slugs[i + 1:] or slugs[:i][-1:]
        return rest[0] if rest else None

    # --- wallpapers --------------------------------------------------------------------

    def refresh_wallpapers(self):
        theme = self.active_theme
        if theme is None:
            return
        first = self.wp_grid.get_first_child()
        while first is not None:
            self.wp_grid.remove(first)
            first = self.wp_grid.get_first_child()
        query = self.wp_filter.casefold().strip()
        self.update_wp_title()
        wallpapers = [w for w in theme_core.list_backgrounds(None, theme)
                      if (self.show_hidden or not w.hidden) and query in w.name.casefold()]
        if query and not wallpapers:
            self.wp_grid.append(self._empty_tile(_("No wallpaper matches “{0}”").format(self.wp_filter)))
            return
        self.add_tile = None
        if not query:
            self.wp_grid.append(self.build_add_tile(empty=not wallpapers))
        assigned = theme_auto.load_workspace_map().get(theme.slug, {})
        for wallpaper in wallpapers:
            workspaces = sorted(ws for ws, name in assigned.items() if name == wallpaper.name)
            self.wp_grid.append(self.build_wallpaper_tile(wallpaper, workspaces))

    def build_add_tile(self, empty: bool) -> Gtk.Widget:
        """The first tile: choose image files (drag & drop and paste work
        anywhere on the page too)."""
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")
        tile.add_css_class("tm-add-tile")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_size_request(self.tile_w, self.tile_w * 9 // 16)
        inner.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        icon.set_pixel_size(32)
        icon.set_vexpand(True)
        icon.set_valign(Gtk.Align.END)
        inner.append(icon)
        hint = Gtk.Label(label=(_("No wallpapers yet.") + " " if empty else "") + _("Drop images here, paste with "
                         "Ctrl+V or click to choose"), wrap=True, max_width_chars=1,
                         justify=Gtk.Justification.CENTER)
        hint.add_css_class("tm-tile-label")
        hint.set_vexpand(True)
        hint.set_valign(Gtk.Align.START)
        inner.append(hint)
        tile.append(inner)
        caption = Gtk.Label(label=_("Add wallpapers…"), xalign=0.5)
        caption.add_css_class("tm-tile-label")
        tile.append(caption)
        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child.set_tooltip_text(_("Choose image files (Enter), drag them anywhere onto this page, or paste "
                               "an image or file with Ctrl+V. HEIC photos are converted. ") + self.drop_note)
        child.tm_add = True
        self.add_tile = tile
        return child

    def update_wp_title(self):
        theme = self.active_theme
        if theme is None:
            return
        label = _("{0} — wallpapers").format(theme_core.plain_text(theme.name))
        if self.wp_filter:
            label += f" — {self.wp_filter}"
        self.wp_title.set_label(label)

    def set_wp_filter(self, query: str):
        self.wp_filter = query
        self.refresh_wallpapers()

    def _empty_tile(self, text: str | None = None) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("tm-tile")
        box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("image-missing")
        icon.set_pixel_size(40)
        box.append(icon)
        empty = Gtk.Label(label=text or _("No wallpapers yet — drop or paste one"),
                          wrap=True, max_width_chars=18, justify=Gtk.Justification.CENTER)
        empty.add_css_class("tm-tile-label")
        box.append(empty)
        box.set_halign(Gtk.Align.CENTER)
        return box

    def build_wallpaper_tile(self, wallpaper: theme_core.WallpaperInfo,
                             workspaces: list[int] = ()) -> Gtk.Widget:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")
        if wallpaper.hidden:
            tile.add_css_class("tm-row-hidden")

        picture = Gtk.Picture()
        picture.set_overflow(Gtk.Overflow.HIDDEN)     # clipped to its corners (Settings › Corners)
        self.decoder.request(picture, wallpaper.path, self.tile_w, self.tile_w * 9 // 16)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(self.tile_w, self.tile_w * 9 // 16)  # wallpapers are 16:9
        picture.set_valign(Gtk.Align.CENTER)

        frame = Gtk.Overlay()
        frame.set_child(picture)
        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)  # the tile itself takes the keyboard focus
        actions.set_tooltip_text(_("Wallpaper actions (Enter)"))
        actions.connect("clicked", lambda button, w=wallpaper: self.show_wallpaper_menu(button, w))
        frame.add_overlay(actions)
        if wallpaper.hidden:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.END)
            badge.append(Gtk.Label(label=_("hidden")))
            frame.add_overlay(badge)
        if workspaces:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.add_css_class("workspace")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.START)
            badge.append(Gtk.Label(label=_("ws {0}").format(" · ".join(map(str, workspaces)))))
            badge.set_tooltip_text(_("Shown on workspace {0}").format(", ".join(map(str, workspaces))))
            frame.add_overlay(badge)
        tile.append(frame)

        caption = Gtk.Label(label=wallpaper.name, xalign=0.5)
        caption.add_css_class("tm-tile-label")
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        caption.set_max_width_chars(1)  # long file names must not widen the grid
        tile.append(caption)

        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child.tm_wallpaper = wallpaper
        child.actions_button = actions
        return child

    def show_wallpaper_menu(self, button, wallpaper: theme_core.WallpaperInfo):
        self.show_menu(button, lambda popover: self.build_wallpaper_menu(popover, wallpaper))

    def build_wallpaper_menu(self, popover, wallpaper: theme_core.WallpaperInfo) -> Gtk.Box:
        theme = self.active_theme
        box = self.menu_box()

        def item(label, handler, icon_name, **kwargs):
            return self.menu_item(box, popover, label, handler, icon_name, **kwargs)

        if theme is not None and theme.current and not wallpaper.hidden:
            item(_("Set as background"), lambda: self.set_background(wallpaper),
                 "preferences-desktop-wallpaper-symbolic")
        if not wallpaper.hidden:
            item(_("Preview on desktop"), lambda: self.start_preview(wallpaper.path, PREVIEW_SECONDS),
                 "view-reveal-symbolic",
                 tooltip=_("Shows it for {0} seconds; or hold Space on the tile").format(PREVIEW_SECONDS))
        if theme is not None and not wallpaper.hidden:
            item(_("New theme from this wallpaper…"), lambda: self.open_editor(theme, wallpaper=wallpaper),
                 "applications-graphics-symbolic",
                 tooltip=_("A theme in this picture's colors, with this wallpaper"))
            on = theme_auto.workspaces_of(theme.slug, wallpaper.name)
            item(_("Show on workspace…") if not on else _("On workspace {0}…").format(", ".join(map(str, on))),
                 lambda: self.show_workspace_menu(wallpaper), "view-grid-symbolic", end_icon="go-next-symbolic",
                 tooltip=_("This wallpaper appears whenever you switch to that workspace")
                 + ("" if theme.current else " (" + _("while this theme is applied") + ")"))
        self.menu_separator(box)
        if wallpaper.hidden:
            item(_("Unhide"), lambda: self.set_wallpaper_hidden(wallpaper, False), "view-reveal-symbolic",
                 tooltip=_("Show it again here and in Omarchy's background picker"))
        else:
            item(_("Hide"), lambda: self.set_wallpaper_hidden(wallpaper, True), "view-conceal-symbolic",
                 tooltip=_("Keep it out of Omarchy's background picker and rotation; the file stays"))
        self.menu_separator(box)
        if wallpaper.deletable:
            item(_("Delete…"), lambda: self.confirm_delete_wallpaper(wallpaper), "user-trash-symbolic",
                 destructive=True)
        else:
            item(_("Can't delete: hide instead"), lambda: None, "changes-prevent-symbolic",
                 sensitive=False, tooltip=wallpaper.locked_reason)
        return box

    def show_workspace_menu(self, wallpaper: theme_core.WallpaperInfo):
        anchor = next((c.actions_button for c in self.children_of(self.wp_grid)
                       if getattr(c, "tm_wallpaper", None) == wallpaper), None)
        if anchor is not None:
            self.show_menu(anchor, lambda popover: self.build_workspace_menu(popover, wallpaper))

    def build_workspace_menu(self, popover, wallpaper: theme_core.WallpaperInfo) -> Gtk.Box:
        theme = self.active_theme
        box = self.menu_box()
        mapping = theme_auto.load_workspace_map().get(theme.slug, {}) if theme else {}
        grid = Gtk.Grid(column_spacing=2, row_spacing=2, column_homogeneous=True)
        current = theme_auto.active_workspace()
        for ws in range(1, 11):
            mine = mapping.get(ws) == wallpaper.name
            other = ws in mapping and not mine
            button = Gtk.Button(label=f"{ws}")
            button.add_css_class("flat")
            button.add_css_class("tm-menu-item")
            if mine:
                button.add_css_class("tm-ws-on")
                button.set_child(Gtk.Label(label=f"✓ {ws}"))
            elif other:
                button.add_css_class("tm-ws-other")
            button.set_tooltip_text(_("Workspace {0}:").format(ws) + " " + (_("shows this wallpaper (click to clear)") if mine else
                                    _("shows {0} (click to use this one)").format(mapping[ws]) if other else
                                    _("use this wallpaper")))
            self.focus_follows_pointer(button)
            self.mark_current_workspace(popover, button, ws, current)
            button.connect("clicked", lambda _b, w=ws, m=mine: (
                popover.popdown(), self.assign_workspace(wallpaper, w, not m)))
            grid.attach(button, (ws - 1) % 5, (ws - 1) // 5, 1, 1)
        title = Gtk.Label(label=_("Show on workspace"), xalign=0)
        title.add_css_class("tm-hint")
        title.set_margin_start(8)
        box.append(title)
        box.append(grid)
        if any(n == wallpaper.name for n in mapping.values()):
            self.menu_separator(box)
            self.menu_item(box, popover, _("Not on any workspace"),
                           lambda: self.clear_workspaces(wallpaper), "edit-clear-symbolic")
        return box

    def assign_workspace(self, wallpaper: theme_core.WallpaperInfo, workspace: int, on: bool):
        theme = self.active_theme
        if theme is None:
            return
        try:
            theme_auto.set_workspace_wallpaper(theme.slug, workspace, wallpaper.name if on else None)
            if on and not theme_auto.workspace_wallpapers_enabled():
                theme_auto.update_settings(workspace_wallpapers=True)
                theme_core.notify(_("Wallpapers per workspace are on"),
                                  _("Switch it off again in Settings (⚙)."))
        except (OSError, ValueError) as error:
            theme_core.notify(_("Could not change the workspace wallpaper"), str(error))
        self.after_workspace_change(theme)

    def clear_workspaces(self, wallpaper: theme_core.WallpaperInfo):
        theme = self.active_theme
        if theme is None:
            return
        theme_auto.clear_wallpaper_workspaces(theme.slug, wallpaper.name)
        self.after_workspace_change(theme)

    def after_workspace_change(self, theme: theme_core.ThemeInfo):
        self.refresh_wallpapers()
        if theme.current:
            # The workspace in front may have just got (or lost) its wallpaper.
            def show():
                with theme_auto.locked():
                    workspace = theme_auto.active_workspace()
                    if workspace is not None:
                        theme_auto.on_workspace(workspace)
            self._async(show, lambda *_: None)

    def set_wallpaper_hidden(self, wallpaper: theme_core.WallpaperInfo, hide: bool):
        theme = self.active_theme
        if theme is None:
            return
        try:
            theme_core.set_wallpaper_hidden(None, theme, wallpaper, hide)
            if theme.current and theme_core.sync_enabled():
                theme_core.sync_current_theme()
        except (ValueError, OSError) as error:
            theme_core.notify(_("Could not hide wallpaper") if hide else _("Could not unhide wallpaper"),
                              f"{wallpaper.name}: {error}")
        self.refresh_wallpapers()

    def set_background(self, wallpaper: theme_core.WallpaperInfo):
        self._async(lambda: theme_core.set_desktop_background(wallpaper.path),
                    lambda ok, _error: ok or theme_core.notify("Could not set background", wallpaper.name))

    def focus_tile_of(self, widget):
        while widget is not None and not isinstance(widget, Gtk.FlowBoxChild):
            widget = widget.get_parent()
        if widget is not None:
            widget.grab_focus()

    def confirm_delete_wallpaper(self, wallpaper: theme_core.WallpaperInfo):
        theme = self.active_theme
        note = "\n\n" + _("It goes to the trash: Undo brings it back.") \
            if theme_core.default_roots().trash is not None else ""
        self.ask_confirm(_("Delete wallpaper “{0}” from “{1}”?").format(
                             theme_core.plain_text(wallpaper.name), theme_core.plain_text(theme.name) if theme else "") + note,
                         _("Delete wallpaper"),
                         lambda: self.perform_delete_wallpaper(wallpaper))

    def ask_confirm(self, message: str, button_label: str, on_confirm, detail: str = ""):
        self.confirm_return = self.stack.get_visible_child_name() or "themes"
        self.confirm_action = on_confirm
        self.confirm_label.set_label(message)
        self.confirm_detail.set_label(detail)
        self.confirm_detail.set_visible(bool(detail))
        self.confirm_button.set_label(button_label)
        self.stack.set_visible_child_name("confirm")

    def cancel_confirm(self):
        self.confirm_action = None
        self.stack.set_visible_child_name(self.confirm_return)

    def accept_confirm(self):
        action, self.confirm_action = self.confirm_action, None
        self.stack.set_visible_child_name(self.confirm_return)
        if action is not None:
            action()

    def perform_delete_wallpaper(self, wallpaper: theme_core.WallpaperInfo):
        """The confirmed step: actually remove the wallpaper (off-thread)."""
        receipts: list = []
        self._async(lambda: theme_core.remove_wallpaper(None, wallpaper.path, receipt=receipts),
                    lambda _path, error: self.after_delete_wallpaper(wallpaper, error, receipts))

    def after_delete_wallpaper(self, wallpaper: theme_core.WallpaperInfo,
                               error: Exception | None, receipts: list = ()):
        theme = self.active_theme
        if error is None and theme is not None and theme.current:
            # Also moves the desktop off a deleted background.
            theme_core.sync_current_theme(hidden_override=None if theme_core.sync_enabled() else {})
        if error is None:
            theme_core.notify(_("Wallpaper deleted"), wallpaper.name)
            self.offer_undo(_("“{0}” moved to the trash").format(theme_core.plain_text(wallpaper.name)), list(receipts))
        else:
            theme_core.notify(_("Could not delete wallpaper"),
                              f"{wallpaper.name}: {error}")
        self.refresh_wallpapers()

    # --- add wallpapers ------------------------------------------------------------------

    def paste_clipboard(self):
        theme = self.active_theme
        if theme is None:
            return
        self.paste_button.set_sensitive(False)
        self._async(lambda: theme_core.clipboard_image(),
                    lambda found, error: self.after_paste(theme, found, error))

    def after_paste(self, theme: theme_core.ThemeInfo, found, error: Exception | None):
        self.paste_button.set_sensitive(True)
        if error is not None:
            theme_core.notify(_("Could not read the clipboard"), str(error))
            return
        if found is None:
            # No image bytes: maybe image files copied in the file manager.
            self._async(lambda: theme_core.clipboard_files(),
                        lambda paths, files_error: self.after_paste_files(paths, files_error))
            return
        mime, data = found
        ext = theme_core.extension_for_mime(mime) or ".png"
        try:
            dest = theme_core.add_background_bytes(None, theme.slug, data,
                                                   name=f"pasted-image{ext}")
            theme_core.notify(_("Wallpaper added"),
                              f"{dest.name} → {theme_core.plain_text(theme.name)}")
        except (ValueError, OSError) as add_error:
            theme_core.notify(_("Could not add wallpaper"), str(add_error))
            return
        self.refresh_wallpapers()

    def after_paste_files(self, paths, error: Exception | None):
        if error is not None or not paths:
            theme_core.notify(_("No image in the clipboard"),
                              _("Copy an image first (in the browser or the file manager)."))
            return
        self.add_files(paths)

    def drop_allowed(self) -> bool:
        return self.header_drag_id is None and self.active_theme is not None and \
            self.stack.get_visible_child_name() == "wallpapers"

    def on_drag_motion(self, _target, _drop, _x, _y):
        if not self.drop_allowed():
            return 0
        self.light_add_tile(True)
        return Gdk.DragAction.COPY

    def light_add_tile(self, on: bool):
        """The add tile lights up while images are dragged over the page."""
        if self.add_tile is not None:
            (self.add_tile.add_css_class if on else self.add_tile.remove_css_class)("active")

    def on_drop(self, _target, drop, _x, _y):
        self.light_add_tile(False)
        kind = drop_source_kind(drop) if self.drop_allowed() else None
        if kind is None:
            return False
        what, mime = kind

        def done(ok: bool):
            drop.finish(Gdk.DragAction.COPY if ok else 0)

        if what == "bytes":
            def got_stream(_drop, result):
                try:
                    stream, _mime = drop.read_finish(result)
                except GLib.Error:
                    done(False)
                    theme_core.notify(_("Nothing added"), _("The image could not be read."))
                    return
                name = theme_core.offered_file_name(mime) or "dropped-image"
                self.read_stream(stream, theme_core.MAX_BACKGROUND_BYTES,
                                 lambda data: (done(data is not None), self.add_image_data(data, name)))
            drop.read_async([mime], GLib.PRIORITY_DEFAULT, None, got_stream)
            return True

        # File lists and text are read as raw streams too, never through
        # read_value_async: GTK would buffer the whole payload before any
        # limit applies, so a foreign drag source could exhaust memory.
        def got_list(_drop, result):
            try:
                stream, _mime = drop.read_finish(result)
            except GLib.Error:
                done(False)
                theme_core.notify(_("Nothing added"), _("The drop could not be read."))
                return

            def got_data(data):
                done(data is not None)
                if data is None:
                    theme_core.notify(_("Nothing added"), _("The dropped file list was too long or too slow."))
                    return
                self.drop_value(data.decode("utf-8", "replace"))
            self.read_stream(stream, MAX_DROP_LIST_BYTES, got_data, timeout_s=DROP_LIST_TIMEOUT)
        drop.read_async([mime], GLib.PRIORITY_DEFAULT, None, got_list)
        return True

    def read_stream(self, stream, limit: int, on_done, timeout_s: int = 30):
        """Read a GInputStream to the end without blocking the UI; None if it
        fails, exceeds `limit` bytes or takes longer than `timeout_s`."""
        chunks: list[bytes] = []
        cancel = Gio.Cancellable()
        state = {"done": False, "timer": 0}

        def finish(data):
            if state["done"]:
                return
            state["done"] = True
            if state["timer"]:
                GLib.source_remove(state["timer"])
                state["timer"] = 0
            if data is None:
                cancel.cancel()
                stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
            on_done(data)

        def next_chunk(_stream=None, result=None):
            if result is not None:
                try:
                    chunk = stream.read_bytes_finish(result).get_data() or b""
                except GLib.Error:
                    finish(None)
                    return
                if not chunk:
                    finish(b"".join(chunks))
                    return
                chunks.append(chunk)
                if sum(map(len, chunks)) > limit:
                    finish(None)
                    return
            if not state["done"]:
                stream.read_bytes_async(1024 * 1024, GLib.PRIORITY_DEFAULT, cancel, next_chunk)

        def expired():
            state["timer"] = 0   # this source ends by returning False
            finish(None)
            return False

        state["timer"] = GLib.timeout_add_seconds(timeout_s, expired)
        next_chunk()

    def add_image_data(self, data: bytes | None, name: str):
        theme = self.active_theme
        if theme is None:
            return
        if data is None:
            theme_core.notify(_("Nothing added"), _("The image could not be read (or is over 50 MiB)."))
            return
        try:
            dest = theme_core.add_background_data(None, theme.slug, data, name=name)
        except (ValueError, OSError) as error:
            theme_core.notify(_("Could not add wallpaper"), str(error))
            return
        theme_core.notify(_("Wallpaper added"), f"{dest.name} → {theme_core.plain_text(theme.name)}")
        self.refresh_wallpapers()

    def drop_value(self, value) -> bool:
        """Add the files named by a dropped file list or text."""
        if not self.drop_allowed():
            return False
        paths = dropped_paths(value) if value is not None else []
        if not paths:
            theme_core.notify(_("Nothing added"), _("Drop local image files (JPG, PNG, WebP, GIF, BMP, HEIC)."))
            return False
        return self.add_files(paths)

    def add_files(self, paths: list[Path]) -> bool:
        theme = self.active_theme
        if theme is None:
            return False
        paths = paths[:MAX_DROPS]

        def copy_all():
            added, errors = [], []
            for path in paths:
                try:
                    added.append(theme_core.add_background_file(None, theme.slug, path).name)
                except (ValueError, OSError) as error:
                    errors.append(f"{path.name}: {error}")
            return added, errors

        # Up to MAX_DROPS × 50 MiB: copy off the main loop so the UI stays live.
        self._async(copy_all, lambda result, error: self.after_drop(result, error))
        return True

    def after_drop(self, result, error: Exception | None):
        added, errors = result if result is not None else ([], [str(error)])
        if added:
            theme_core.notify(ngettext("Wallpaper added", "Wallpapers added", len(added)),
                              ", ".join(added[:5]) + ("…" if len(added) > 5 else ""))
        if errors:
            theme_core.notify(_("Some files were skipped"),
                              "; ".join(errors[:3]) + ("…" if len(errors) > 3 else ""))
        self.refresh_wallpapers()

    # --- shared plumbing ---------------------------------------------------------------------

    def _async(self, fn, on_done):
        """Run a blocking core call off the main loop and hand the result
        back as (result, error) via GLib.idle_add."""
        def worker():
            try:
                result, error = fn(), None
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a message
                result, error = None, exc
            # One call, whatever on_done returns: a truthy result would make
            # idle_add run it again and again.
            GLib.idle_add(lambda: on_done(result, error) and False)
        threading.Thread(target=worker, daemon=True).start()

    def on_key(self, _controller, keyval, _keycode, state):
        if state & Gdk.ModifierType.ALT_MASK and not state & (Gdk.ModifierType.CONTROL_MASK |
                                                             Gdk.ModifierType.SUPER_MASK):
            focused = self.window.get_focus()
            for button_id, button in self.header_buttons.items():
                if focused is button or (focused is not None and button.is_ancestor(focused)):
                    if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_Right, Gdk.KEY_KP_Right):
                        self.move_header_button(button_id, -1 if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) else 1)
                        return True
        page = self.stack.get_visible_child_name()
        back = keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace)
        if page == "shortcut":
            return self.on_shortcut_key(_controller, keyval, _keycode, state)
        # F12 alone (as in AI Playground: no Shift, Ctrl, Alt or Super), also while typing in a field
        # or with a pop-up list open; never on the shortcut page (F12 can be the key being set
        # there) nor while the note of the last screenshot is open.
        if keyval == Gdk.KEY_F12 and not state & (Gdk.ModifierType.SHIFT_MASK |
                                                  Gdk.ModifierType.CONTROL_MASK |
                                                  Gdk.ModifierType.ALT_MASK |
                                                  Gdk.ModifierType.SUPER_MASK) \
                and not self.shot_dialog_open() and screenshots.key_enabled(theme_auto.settings()):
            GLib.idle_add(self.take_screenshot)
            return True
        if page == "look" and keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace):
            self.close_look_page()
            return True
        if self.ws_app_list_open():
            # The app list of an App Preset: its search field gets the keys; Esc closes only the list.
            if keyval == Gdk.KEY_Escape:
                self.ws_app_popover.popdown()
                return True
            return False
        if keyval == Gdk.KEY_Escape and self.ws_drag_cancel():
            return True        # Esc cancels a drag on the Workspaces tab, nothing else
        if keyval == Gdk.KEY_Escape and page == "ask" and self.ask_prompt_drag_cancel():
            return True        # Esc cancels dragging a suggestion, nothing else
        if keyval == Gdk.KEY_Escape and page == "ask" and self.ask_finish_edit(False):
            return True        # Esc cancels editing a suggestion, nothing else
        if page == "ask" and self.ask_prompt_key(keyval, state):
            return True        # Alt+↑/↓ moves the focused suggestion
        if self.ws_preview_open() and keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace, Gdk.KEY_space, Gdk.KEY_Return):
            self.ws_hover_hide()
            return True
        if self.shot_dialog_open():
            if keyval == Gdk.KEY_Escape:
                self.finish_screenshot_note(False)
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.finish_screenshot_note(True)
                return True
            return False
        if self.actions_menu_open() and keyval != Gdk.KEY_Escape:
            return False  # the menu handles its own keys
        if keyval in (Gdk.KEY_z, Gdk.KEY_Z) and state & Gdk.ModifierType.CONTROL_MASK and self.undo_receipts:
            self.undo_delete()
            return True
        if keyval == Gdk.KEY_space and self.preview is not None and page in ("wallpapers", "websearch"):
            return True   # auto-repeat while Space is held for a preview
        if keyval == Gdk.KEY_Escape:
            if self.preview is not None:
                self.stop_preview()
            elif self.actions_menu_open():
                self.actions_popover.popdown()
            elif self.theme_preview_open():
                self.hide_theme_preview()
            elif page == "confirm":
                self.cancel_confirm()
            elif page == "websearch":
                self.close_web_search()
            elif page == "wallpapers" and self.wp_filter:
                self.set_wp_filter("")
            elif page == "editor":
                self.close_editor()
            elif page in ("wallpapers", "import", "settings", "info", "presets", "ask"):
                self.show_themes()
            elif self.filter_query:
                self.set_filter("")
            else:
                self.close()
            return True
        if page == "confirm":
            if keyval == Gdk.KEY_BackSpace:
                self.cancel_confirm()
                return True
            return False  # Left/Right move between Cancel and Delete
        if page == "editor":
            return False   # typing goes to the name and color fields
        if page == "ask":
            focus = self.window.get_focus()
            in_text = isinstance(focus, (Gtk.Text, Gtk.Entry))
            in_prompt = focus is not None and (focus is self.ask_entry or focus.is_ancestor(self.ask_entry))
            # Backspace goes back, except while typing (in the prompt: once it is empty).
            if keyval == Gdk.KEY_BackSpace and (not in_text or (in_prompt and not self.ask_entry.get_text())):
                self.show_themes()
                return True
            # Enter asks: in the prompt and anywhere on the page but a refine field
            # (which sends its own words) or a button (Enter presses it).
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter) and \
                    (in_prompt or not (in_text or isinstance(focus, Gtk.Button))):
                self.ask_now()
                return True
            if in_text:
                return False
            char = self.typed_char(keyval, state)
            if char:
                self.ask_entry.grab_focus()
                self.ask_entry.set_text(self.ask_entry.get_text() + char)
                self.ask_entry.set_position(-1)
                return True
            return False
        if page == "presets":
            focus = self.window.get_focus()
            in_name = focus is not None and (focus is self.preset_name or focus.is_ancestor(self.preset_name))
            if in_name:
                if keyval == Gdk.KEY_BackSpace and not self.preset_name.get_text():
                    self.show_themes()
                    return True
                return False
            if keyval == Gdk.KEY_BackSpace:
                self.show_themes()
                return True
            char = self.typed_char(keyval, state) if self.presets_tab == "themes" else None
            if char:   # type-to-name only on the Save/Load tab, where the field is
                self.preset_name.grab_focus()
                self.preset_name.set_text(self.preset_name.get_text() + char)
                self.preset_name.set_position(-1)
                return True
            return False
        if page == "settings":
            focus = self.window.get_focus()
            in_search = focus is not None and (focus is self.settings_search or focus.is_ancestor(self.settings_search))
            if in_search:
                if keyval == Gdk.KEY_BackSpace and not self.settings_search.get_text():
                    self.show_themes()
                    return True
                return False
            if keyval == Gdk.KEY_BackSpace:
                self.show_themes()
                return True
            char = self.typed_char(keyval, state)
            if char:
                self.settings_search.grab_focus()
                self.settings_search.set_text(self.settings_search.get_text() + char)
                self.settings_search.set_position(-1)
                return True
            return False
        if page == "info":
            if keyval == Gdk.KEY_BackSpace:
                self.show_themes()
                return True
            return False  # Tab and the arrows move between the controls
        if page == "wallpapers":
            tile = self.focused_tile()
            if self.focused_add_tile() and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
                                                      Gdk.KEY_space):
                self.choose_files()
                return True
            if tile is not None and keyval == Gdk.KEY_space and not tile.tm_wallpaper.hidden:
                self.start_preview(tile.tm_wallpaper.path)   # until Space is released
                return True
            if tile is not None and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
                                               Gdk.KEY_Menu):
                self.show_wallpaper_menu(tile.actions_button, tile.tm_wallpaper)
                return True
            if keyval == Gdk.KEY_BackSpace and self.wp_filter:
                self.set_wp_filter(self.wp_filter[:-1])
                return True
            # Left goes back from the left column; from the right one it moves left.
            current = tile or self.focused_add_tile()
            if back and (keyval == Gdk.KEY_BackSpace or self.in_left_column(current)):
                self.show_themes()
                return True
            if keyval in (Gdk.KEY_f, Gdk.KEY_F) and state & Gdk.ModifierType.CONTROL_MASK:
                self.open_web_search()
                return True
            # Ctrl+V pastes an image copied from the web.
            if keyval in (Gdk.KEY_v, Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK:
                self.paste_clipboard()
                return True
            char = self.typed_char(keyval, state)
            if char and not (char == " " and current is not None):
                self.set_wp_filter(self.wp_filter + char)
                return True
            return False
        if page == "import":
            return self.on_import_key(keyval)
        if page == "websearch":
            return self.on_web_key(keyval)
        if keyval in (Gdk.KEY_i, Gdk.KEY_I) and state & Gdk.ModifierType.CONTROL_MASK:
            self.open_import()
            return True
        if keyval == Gdk.KEY_BackSpace and self.filter_query:
            self.set_filter(self.filter_query[:-1])
            return True
        char = self.typed_char(keyval, state)
        if char and (char != " " or self.filter_query):
            self.set_filter(self.filter_query + char)
            return True
        return self.on_list_key(keyval)

    def on_web_key(self, keyval) -> bool:
        focus = self.window.get_focus()
        if focus is not None and (focus is self.web_ai_entry or focus.is_ancestor(self.web_ai_entry)):
            return False          # the Ask AI field takes its own keys (Enter asks, Esc leaves)
        in_search = focus is not None and (focus is self.web_search or focus.is_ancestor(self.web_search))
        tile = self.focused_web_tile()
        if tile is not None and keyval == Gdk.KEY_space:
            self.preview_web(tile.tm_web)   # until Space is released
            return True
        if tile is not None and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
                                           Gdk.KEY_Menu):
            self.show_web_menu(tile.actions_button, tile.tm_web)
            return True
        if in_search:
            if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
                first = self.web_grid.get_first_child()
                if first is not None:
                    first.grab_focus()
                return True
            # Enter searches (the entry's "activate"); Backspace edits until the field is empty.
            if keyval == Gdk.KEY_BackSpace and not self.web_search.get_text():
                self.close_web_search()
                return True
            return False
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and not self.in_left_column(tile):
            return False  # not the left column: move left
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace):
            self.close_web_search()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) and self.in_top_row(tile):
            self.web_search.grab_focus()
            return True
        char = Gdk.keyval_to_unicode(keyval)
        if char and chr(char).isprintable():
            self.web_search.grab_focus()
            self.web_search.set_text(self.web_search.get_text() + chr(char))
            self.web_search.set_position(-1)
            return True
        return False

    def on_import_key(self, keyval) -> bool:
        in_search = self.import_search.has_focus() or \
            (self.window.get_focus() is not None and self.window.get_focus().is_ancestor(self.import_search))
        tile = self.focused_catalog_tile()
        if tile is not None and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
                                           Gdk.KEY_space, Gdk.KEY_Menu):
            self.show_catalog_menu(tile.actions_button, tile.tm_catalog)
            return True
        if in_search:
            if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                first = self.first_visible_catalog_tile()
                if first is not None:
                    first.grab_focus()
                return True
            # In the search field Left and Backspace edit; with it empty they go back.
            if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace) and not self.import_search.get_text():
                self.show_themes()
                return True
            return False
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and not self.in_left_column(tile):
            return False  # not the left column: move left
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace):
            self.show_themes()
            return True
        # Typing anywhere on the page searches.
        char = Gdk.keyval_to_unicode(keyval)
        if char and chr(char).isprintable():
            self.import_search.grab_focus()
            self.import_search.set_text(self.import_search.get_text() + chr(char))
            self.import_search.set_position(-1)
            return True
        return False

    def focused_add_tile(self):
        widget = self.window.get_focus()
        while widget is not None and not isinstance(widget, Gtk.FlowBoxChild):
            widget = widget.get_parent()
        return widget if widget is not None and getattr(widget, "tm_add", False) else None

    def focused_tile(self):
        widget = self.window.get_focus()
        while widget is not None and not isinstance(widget, Gtk.FlowBoxChild):
            widget = widget.get_parent()
        return widget if widget is not None and hasattr(widget, "tm_wallpaper") else None

    def on_list_key(self, keyval) -> bool:
        if self.theme_preview_open():
            if keyval == Gdk.KEY_BackSpace:
                self.hide_theme_preview()
                return True
            if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and self.big_preview_picture is not None \
                    and self.big_preview_picture._wallpapers:
                self.big_preview_picture.rotate_step(-1)
                return True
            if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right) and self.big_preview_picture is not None \
                    and self.big_preview_picture._wallpapers:
                self.big_preview_picture.rotate_step(1)
                return True
            if keyval == Gdk.KEY_Menu and self.big_preview_actions is not None:
                self.show_actions(self.big_preview_actions, self.big_preview_theme)
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_space):
                focus = self.window.get_focus() if self.window is not None else None
                if isinstance(focus, Gtk.Button) and focus.is_ancestor(self.big_preview_slot):
                    return False
                self.hide_theme_preview()
                return True
        row = self.rows.get_selected_row()
        columns = self.theme_columns
        column = row.get_index() % columns if row is not None else 0
        # With several columns ← and → move between cards; → on the last
        # card of a line (or with one column) opens the menu, as before.
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and columns > 1:
            if row is not None and column > 0:
                self.select_row(row.get_prev_sibling())
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right) and columns > 1 and row is not None \
                and column < columns - 1 and row.get_next_sibling() is not None:
            self.select_row(row.get_next_sibling())
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_Menu):
            if row is not None:
                self.show_actions(row.actions_button, row.tm_theme)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            if row is not None:
                if getattr(self, "big_preview_enabled", True):
                    self.show_theme_preview(row.tm_theme)
                else:
                    self.open_wallpapers_for(row.tm_theme)
            return True
        moves = {Gdk.KEY_Down: 1, Gdk.KEY_KP_Down: 1, Gdk.KEY_Up: -1, Gdk.KEY_KP_Up: -1,
                 Gdk.KEY_Page_Down: 4, Gdk.KEY_KP_Page_Down: 4,
                 Gdk.KEY_Page_Up: -4, Gdk.KEY_KP_Page_Up: -4}
        if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            self.select_row(self.rows.get_first_child())
            return True
        if keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            last = self.rows.get_last_child()
            while last is not None and not hasattr(last, "tm_theme"):
                last = last.get_prev_sibling()
            self.select_row(last)
            return True
        if keyval in moves:
            target = row or self.rows.get_first_child()
            step = moves[keyval] * columns
            for _ in range(abs(step)):
                nxt = target.get_next_sibling() if step > 0 else target.get_prev_sibling()
                if nxt is None or not hasattr(nxt, "tm_theme"):   # the hint row is not a theme
                    break
                target = nxt
            self.select_row(target)
            return True
        return False

    def toggle(self):
        if self.window.is_visible():
            self.close()
        else:
            self.open()

    def open(self):
        # Fresh data on every open: themes, hidden set and current theme may
        # have changed while the singleton slept.
        self.pointer_moved = False
        self.hidden = self.load_hidden_list()
        self.filter_query = ""
        # A header drag that never reported its end must not block drops.
        self.header_drag_id = self.header_press = self.header_drop_slot = None
        self.header_drop_marker.set_visible(False)
        try:
            theme_auto.note_current_theme()
        except OSError:
            pass
        if self.show_hidden:
            self.toggle_show_hidden()   # hidden themes are shown only on request, per open
        # Omarchy may have rebuilt its picker cache (e.g. a theme was installed).
        if theme_core.sync_enabled():
            theme_core.prune_switcher_previews(self.hidden)
        self.refresh_themes()
        self.refresh_look_pending()
        self.grab_keyboard()
        self.move_to(self.position)
        self.window.present()
        self.layer_watch.start()
        self.select_row(self.rows.get_selected_row())
        # Reopened on the Presets page: again on the workspace you are on now.
        if self.stack.get_visible_child_name() == 'presets' and self.ws_choose_current():
            self.ws_reload()
        # Restarted after a language change: back on the settings page.
        if os.environ.pop("THEME_MANAGER_PAGE", "") == "settings":
            GLib.idle_add(self.open_settings)

    # --- moving the popup -----------------------------------------------------

    def monitor_geometry(self):
        display = Gdk.Display.get_default()
        surface = self.window.get_surface()
        monitor = display.get_monitor_at_surface(surface) if display and surface else None
        if monitor is None and display is not None and display.get_monitors().get_n_items():
            monitor = display.get_monitors().get_item(0)
        return monitor.get_geometry() if monitor is not None else None

    def current_position(self) -> tuple[int, int]:
        if self.position is not None:
            return self.position
        geo = self.monitor_geometry()
        if geo is None:
            return 0, 0
        return (max(0, (geo.width - self.window.get_width()) // 2),
                max(0, (geo.height - self.window.get_height()) // 2))

    def move_to(self, position, save: bool = False):
        """Place the card's top-left at `position` (monitor-relative, kept on
        screen), or center it for None."""
        if position is not None:
            geo = self.monitor_geometry()
            if geo is not None:
                position = (min(max(0, int(position[0])), max(0, geo.width - self.window.get_width())),
                            min(max(0, int(position[1])), max(0, geo.height - self.window.get_height())))
        self.position = position
        for edge in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.LEFT):
            Gtk4LayerShell.set_anchor(self.window, edge, position is not None)
        if position is not None:
            Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.LEFT, position[0])
            Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.TOP, position[1])
        if save:
            try:
                theme_core.save_window_position(position)
            except OSError:
                pass

    def on_move_begin(self, _gesture, _x, _y):
        picked = self.main_header.pick(_x, _y, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked is not self.main_header:
            if picked in self.header_buttons.values():
                _gesture.set_state(Gtk.EventSequenceState.DENIED)
                self.moving = False
                return
            picked = picked.get_parent()
        # Seen here rather than by a click gesture, which the drag can win:
        # a second press within the double-click time resets the popup.
        now = time.monotonic()
        if now - getattr(self, "last_header_press", 0.0) < 0.4:
            self.last_header_press = 0.0
            self.moving = False
            self.on_header_double_click(Gdk.BUTTON_PRIMARY)
            return
        self.last_header_press = now
        self.moving = True
        self.move_start = (self.current_position(), theme_core.cursor_position())

    def reset_window(self):
        """Default size, centered."""
        self.resize_to(CARD_WIDTH, LIST_HEIGHT, save=True)
        self.move_to(None, save=True)

    def maximize_window(self):
        """As large as the monitor allows, centered."""
        self.resize_to(*self.size_limits(), save=True)
        self.move_to(None, save=True)

    def size_state(self) -> str:
        """"default", "max" or "between"."""
        if (self.card_w, self.list_h) == (CARD_WIDTH, LIST_HEIGHT):
            return "default"
        max_w, max_h = self.size_limits()
        if (self.card_w, self.list_h) == (max(CARD_WIDTH, max_w), max(theme_core.MIN_WINDOW_SIZE[1], max_h)):
            return "max"
        return "between"

    def on_header_double_click(self, button):
        """At the default size either button maximizes, at the largest either
        one goes back; in between left = default size, right = maximize."""
        size = self.size_state()
        if size == "max" or (size == "between" and button == Gdk.BUTTON_PRIMARY):
            self.reset_window()
        else:
            self.maximize_window()

    def update_mouse_hint(self):
        if getattr(self, "mouse_hint", None) is None or self.window is None:
            return
        size = self.size_state()
        self.mouse_hint.set_label(_("Double-click: maximize") if size == "default" else
                                  _("Double-click: default size") if size == "max" else
                                  _("Double-click: default size · right double-click: maximize"))
        self.mouse_hint.set_tooltip_text(self.mouse_hint.get_label())

    def on_move_update(self, gesture, dx, dy):
        if not self.moving:
            return
        # The gesture's offsets are relative to the surface, which moves with
        # the pointer; Hyprland's global cursor position does not drift.
        (x, y), cursor_start = self.move_start
        cursor = theme_core.cursor_position() if cursor_start is not None else None
        if cursor is not None:
            dx, dy = cursor[0] - cursor_start[0], cursor[1] - cursor_start[1]
        else:
            x, y = self.current_position()   # fallback: remaining distance to the grab point
        self.move_to((x + round(dx), y + round(dy)))

    def on_move_end(self, gesture, dx, dy):
        if not self.moving:
            return
        self.moving = False
        if self.position is not None:
            self.move_to(self.position, save=True)

    def grab_keyboard(self):
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
        if self.cursor_watch:
            GLib.source_remove(self.cursor_watch)
        start = theme_core.cursor_position()

        def watch():
            # The pointer may leave for another window without touching the card.
            if not self.window.is_visible():
                self.cursor_watch = 0
                return False
            now = theme_core.cursor_position()
            if start is None or now is None or \
                    abs(now[0] - start[0]) + abs(now[1] - start[1]) > CURSOR_SLACK:
                self.cursor_watch = 0
                self.release_keyboard()
                return False
            return True

        self.cursor_watch = GLib.timeout_add(200, watch)

    def release_keyboard(self):
        if self.cursor_watch:
            GLib.source_remove(self.cursor_watch)
            self.cursor_watch = 0
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.ON_DEMAND)

    def on_menu_opened(self, _name):
        """Another menu wants the keyboard: get out of its way at once."""
        if self.window is not None and self.window.is_visible():
            self.close()

    def close(self):
        self.layer_watch.stop()
        self.stop_preview()
        self.ws_drag_reset()          # a preview drag never survives closing
        if getattr(self, "ask_prompt_drag", None) is not None:
            self.ask_prompt_drag_cancel()            # nor does a suggestion drag
        if getattr(self, "ask_busy", None) is not None:
            self.ask_release()                       # hidden: the local model's GPU memory back
        if self.stack.get_visible_child_name() == "shortcut":
            self.close_shortcut_page()
        if self.actions_menu_open():
            self.actions_popover.popdown()
        if self.stack.get_visible_child_name() == "confirm":
            self.cancel_confirm()  # never reopen onto a stale question
        if self.shot_dialog_open():
            self.finish_screenshot_note(True)      # a note already typed is kept (none typed, no file)
        self.ws_hover_hide()
        self.hide_theme_preview()
        if self.settings_previous_size is not None:
            self.stack.set_visible_child_name("themes")
        self.restore_settings_size()
        self.window.hide()


def main():
    app = ThemeManager()
    atexit.register(cleanup_pid)
    atexit.register(lambda: theme_ai.release_local(wait=1.0))   # ended (also by a signal): never hangs
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
