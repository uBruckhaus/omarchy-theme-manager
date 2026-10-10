#!/usr/bin/env python3
"""Theme Manager popup: a Wayland layer-shell window over every Omarchy
theme (user and system).

List themes, apply one, hide it from the list (eye toggle unhides), delete
user themes, and manage each theme's wallpapers: add images by drag & drop or
by pasting an image copied from the web, remove the ones no longer wanted.
All file work goes through theme_core, which enforces the trust rules.
"""

import os
import queue
import re
import signal
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
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Gtk4LayerShell, Pango

# Once Gtk4LayerShell is loaded into this process by ld.so, remove it from
# LD_PRELOAD so child processes do not inherit it.
_preloads = [p for p in os.environ.get("LD_PRELOAD", "").split(":")
             if p and p != layer_shell_library]
if _preloads:
    os.environ["LD_PRELOAD"] = ":".join(_preloads)
else:
    os.environ.pop("LD_PRELOAD", None)

import atexit

import theme_catalog
import theme_core
import wallpaper_search
from palette import ThemePalette
import pidfile

# Outer size matches Simple Launcher: a 420 px card around a fixed 590 px
# list, the same on every page so switching pages never resizes the popup.
CARD_WIDTH = 420
LIST_HEIGHT = 590
SCREEN_MARGIN = 120    # space kept free above and below the card
PREVIEW_W, PREVIEW_H = 192, 108   # theme previews are 16:9
TILE = 176
SWITCHER_WRAPPER = Path(__file__).resolve().parent / "theme-switcher"
SYNC_SCRIPT = Path(__file__).resolve().parent / "switcher_sync.py"
PREVIEW_WORKERS = 4    # parallel preview downloads on the import page
CURSOR_SLACK = 24  # px the pointer may move after opening before the keyboard is released
MAX_DROPS = 10  # one drop gesture adds at most this many wallpapers
MAX_DROP_LIST_BYTES = 1024 * 1024  # a dropped uri-list or text, as for the clipboard
DROP_LIST_TIMEOUT = 10  # seconds a drop source gets to deliver that list


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


def hyprland_rounding() -> int:
    """Follow Hyprland's window rounding like the Omarchy shell does."""
    out, _ok = theme_core.read_bounded_output(["hyprctl", "getoption", "decoration:rounding", "-j"],
                                             max_bytes=4096, timeout=2.0)
    try:
        return max(0, min(64, int(re.search(r'"value":\s*([0-9]+)', out.decode("utf-8", "replace")).group(1))))
    except (ValueError, AttributeError):
        return 12


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
        if paintable is not None:
            self.set_paintable(paintable)
        self.set_content_fit(Gtk.ContentFit.COVER)
        self.set_can_shrink(True)
        self.set_hexpand(False)
        self.set_vexpand(False)

    def do_measure(self, orientation, _for_size):
        size = self._size[0] if orientation == Gtk.Orientation.HORIZONTAL else self._size[1]
        return size, size, -1, -1


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


class ImageDecoder:
    """Decodes previews and wallpaper thumbnails on worker threads and hands
    the result to a Gtk.Picture on the main loop, so neither a large image
    nor a slow disk ever freezes the popup."""

    def __init__(self, workers: int):
        self.jobs = queue.SimpleQueue()
        for _ in range(workers):
            threading.Thread(target=self.work, daemon=True).start()

    def request(self, picture, path: str, width: int, height: int):
        self.jobs.put((picture, str(path), width, height))

    def work(self):
        while True:
            picture, path, width, height = self.jobs.get()
            pixbuf = scaled_pixbuf(path, width, height)
            if pixbuf is not None:
                GLib.idle_add(self.show, picture, pixbuf)

    @staticmethod
    def show(picture, pixbuf):
        picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        return GLib.SOURCE_REMOVE


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


class ThemeManager(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.omarchy.thememanager")
        self.window = None
        self.theme = ThemePalette()
        self.hidden = self.load_hidden_list()
        self.show_hidden = False
        self.active_theme = None   # ThemeInfo while the wallpaper view is open
        self.style_provider = None
        self.theme_list = []       # latest discover_themes() result
        self.actions_popover = None
        self.cursor_watch = 0      # GLib source id while the keyboard is held on open
        self.position = theme_core.window_position()   # None: centered
        self.move_start = ((0, 0), None)
        self.moving = False
        self.menu_anchor = None
        self.decoder = ImageDecoder(PREVIEW_WORKERS)
        # Type-to-filter as in Simple Launcher: no search field, the title shows it.
        self.filter_query = ""     # theme list
        self.wp_filter = ""        # wallpaper page

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
            write_pid_file()
            try:
                if theme_core.sync_enabled():   # opt-in, see the settings button
                    theme_core.ensure_menu_override(SWITCHER_WRAPPER)
                    theme_core.ensure_theme_set_hook(SYNC_SCRIPT)
                    theme_core.sync_current_theme()
            except (OSError, ValueError):
                pass
        self.open()

    # --- window ----------------------------------------------------------------

    def build_window(self):
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
        card.set_size_request(CARD_WIDTH, -1)
        window.set_child(card)
        self.card = card
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
        drop.connect("drag-leave", lambda *_: self.drop_zone.remove_css_class("active"))
        drop.connect("drop", self.on_drop)
        card.add_controller(drop)
        list_height = max(320, min(LIST_HEIGHT, monitor_height() - 2 * SCREEN_MARGIN))

        header = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Theme Manager")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(1)
        self.title_label = title
        title.add_css_class("tm-title")
        title.set_hexpand(True)
        title.set_xalign(0)   # fill the header (START would shrink it to its 1-char natural width)
        header.append(title)
        # Dragging the title bar moves the popup (a layer surface: placed by
        # margins; Hyprland keeps Super + drag for itself). Double-click the
        # title to center it again.
        header.set_cursor_from_name("grab")
        title.set_tooltip_text("Drag to move · double-click to center")
        mover = Gtk.GestureDrag()
        mover.connect("drag-begin", self.on_move_begin)
        mover.connect("drag-update", self.on_move_update)
        mover.connect("drag-end", self.on_move_end)
        header.add_controller(mover)
        recenter = Gtk.GestureClick()
        recenter.connect("pressed", lambda _g, n, *_: n == 2 and self.move_to(None, save=True))
        header.add_controller(recenter)
        self.hidden_button = Gtk.Button(icon_name="view-reveal-symbolic")
        self.hidden_button.set_tooltip_text("Show hidden themes and wallpapers")
        self.hidden_button.add_css_class("flat")
        self.hidden_button.add_css_class("tm-header-btn")
        self.hidden_button.connect("clicked", lambda *_: self.toggle_show_hidden())
        import_button = Gtk.Button(icon_name="folder-download-symbolic")
        import_button.set_tooltip_text("Import themes from omarchy.org (Ctrl+I)")
        import_button.add_css_class("flat")
        import_button.add_css_class("tm-header-btn")
        import_button.connect("clicked", lambda *_: self.open_import())
        header.append(import_button)
        settings_button = Gtk.Button(icon_name="emblem-system-symbolic")
        settings_button.set_tooltip_text("Settings")
        settings_button.add_css_class("flat")
        settings_button.add_css_class("tm-header-btn")
        settings_button.connect("clicked", lambda button: self.show_menu(button, self.build_settings_menu))
        header.append(settings_button)
        header.append(self.hidden_button)
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
        scroller.set_size_request(CARD_WIDTH, -1)
        scroller.set_min_content_height(list_height)
        scroller.set_max_content_height(list_height)
        self.rows = Gtk.ListBox()
        self.rows.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.rows.add_css_class("tm-list")
        self.no_match = Gtk.Label(wrap=True, max_width_chars=36)
        self.no_match.add_css_class("tm-hint")
        self.no_match.set_margin_top(24)
        self.rows.set_placeholder(self.no_match)
        self.rows.connect("row-activated", lambda _rows, row: self.open_wallpapers_for(row.tm_theme))
        scroller.set_child(self.rows)
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
        stack.add_named(scroller, "themes")

        # --- wallpapers page
        wallpaper_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wallpaper_page.set_size_request(CARD_WIDTH, -1)

        wp_header = Gtk.Box(spacing=8)
        back_button = Gtk.Button()
        back_button.add_css_class("flat")
        back_button.add_css_class("tm-header-btn")
        back_box = Gtk.Box(spacing=6)
        back_box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        back_box.append(Gtk.Label(label="Back"))
        back_button.set_child(back_box)
        back_button.set_tooltip_text("Back to themes (Left, Backspace or Esc)")
        back_button.connect("clicked", lambda *_: self.show_themes())
        wp_header.append(back_button)
        self.wp_title = Gtk.Label(label="Wallpapers", xalign=0)
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
        paste_box.append(Gtk.Label(label="Paste image (Ctrl+V)"))
        self.paste_button.set_child(paste_box)
        self.paste_button.set_tooltip_text("Add the image copied to the clipboard (from the web or the file manager)")
        self.paste_button.connect("clicked", lambda *_: self.paste_clipboard())
        web_button = Gtk.Button()
        web_button.add_css_class("tm-paste")
        web_box = Gtk.Box(spacing=6)
        web_box.append(Gtk.Image.new_from_icon_name("system-search-symbolic"))
        web_box.append(Gtk.Label(label="Search the web"))
        web_button.set_child(web_box)
        web_button.set_tooltip_text("Find wallpapers on wallhaven.cc (Ctrl+F)")
        web_button.connect("clicked", lambda *_: self.open_web_search())
        buttons = Gtk.Box(spacing=8)
        buttons.append(self.paste_button)
        buttons.append(web_button)
        self.wp_toolbar.append(buttons)
        # A visible drop zone; it lights up while files are dragged over the page.
        drop_zone = Gtk.Box(spacing=10)
        drop_zone.add_css_class("tm-dropzone")
        drop_icon = Gtk.Image.new_from_icon_name("insert-image-symbolic")
        drop_icon.set_pixel_size(28)
        drop_zone.append(drop_icon)
        drop_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        drop_title = Gtk.Label(label="Drag & drop images here", xalign=0)
        drop_title.add_css_class("tm-dropzone-title")
        drop_text.append(drop_title)
        # A wrapping label asks for its one-line width unless capped, which
        # made the wallpaper page wider than the theme list.
        drop_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=38)
        drop_hint.add_css_class("tm-hint")
        drop_text.append(drop_hint)
        drop_zone.append(drop_text)
        self.wp_toolbar.append(drop_zone)
        wallpaper_page.append(self.wp_toolbar)
        self.drop_hint = drop_hint
        self.drop_zone = drop_zone

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
        self.wp_grid.set_min_children_per_line(2)
        self.wp_grid.set_max_children_per_line(2)
        wp_scroller.set_child(self.wp_grid)
        wallpaper_page.append(wp_scroller)
        stack.add_named(wallpaper_page, "wallpapers")

        # --- web wallpaper search, laid out like the wallpapers
        web_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        web_page.set_size_request(CARD_WIDTH, -1)
        ws_header = Gtk.Box(spacing=8)
        ws_back = Gtk.Button()
        ws_back.add_css_class("flat")
        ws_back.add_css_class("tm-header-btn")
        ws_back_box = Gtk.Box(spacing=6)
        ws_back_box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        ws_back_box.append(Gtk.Label(label="Back"))
        ws_back.set_child(ws_back_box)
        ws_back.set_tooltip_text("Back to the wallpapers (Backspace or Esc)")
        ws_back.connect("clicked", lambda *_: self.close_web_search())
        ws_header.append(ws_back)
        self.ws_title = Gtk.Label(label="Search wallpapers", xalign=0)
        self.ws_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.ws_title.set_max_width_chars(1)
        self.ws_title.add_css_class("tm-title")
        self.ws_title.set_hexpand(True)
        ws_header.append(self.ws_title)
        web_page.append(ws_header)
        self.web_search = Gtk.SearchEntry()
        self.web_search.set_placeholder_text("Search, e.g. misty forest, neon city")
        self.web_search.connect("activate", lambda *_: self.run_web_search())
        web_page.append(self.web_search)
        self.web_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=40)
        self.web_hint.add_css_class("tm-hint")
        web_page.append(self.web_hint)
        ws_scroller = Gtk.ScrolledWindow()
        ws_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        ws_scroller.set_vexpand(True)
        ws_scroller.connect("edge-reached", lambda _s, pos: pos == Gtk.PositionType.BOTTOM
                            and self.load_more_web_results())
        self.web_grid = Gtk.FlowBox()
        self.web_grid.add_css_class("tm-grid")
        self.web_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.web_grid.set_homogeneous(True)
        self.web_grid.set_column_spacing(8)
        self.web_grid.set_row_spacing(8)
        self.web_grid.set_valign(Gtk.Align.START)
        self.web_grid.set_min_children_per_line(2)
        self.web_grid.set_max_children_per_line(2)
        ws_scroller.set_child(self.web_grid)
        web_page.append(ws_scroller)
        stack.add_named(web_page, "websearch")
        self.web_query = ""
        self.web_page_no = 0
        self.web_last_page = 0
        self.web_loading = False
        self.web_generation = 0     # a new search makes late answers of an old one stale
        self.web_pictures = {}      # id -> Gtk.Picture waiting for its thumbnail

        # --- import page: the omarchy.org gallery, laid out like the wallpapers
        import_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        import_page.set_size_request(CARD_WIDTH, -1)
        im_header = Gtk.Box(spacing=8)
        im_back = Gtk.Button()
        im_back.add_css_class("flat")
        im_back.add_css_class("tm-header-btn")
        im_back_box = Gtk.Box(spacing=6)
        im_back_box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        im_back_box.append(Gtk.Label(label="Back"))
        im_back.set_child(im_back_box)
        im_back.set_tooltip_text("Back to themes (Left, Backspace or Esc)")
        im_back.connect("clicked", lambda *_: self.show_themes())
        im_header.append(im_back)
        im_title = Gtk.Label(label="Import themes — omarchy.org", xalign=0)
        im_title.set_ellipsize(Pango.EllipsizeMode.END)
        im_title.set_max_width_chars(1)
        im_title.add_css_class("tm-title")
        im_title.set_hexpand(True)
        im_header.append(im_title)
        import_page.append(im_header)

        im_toolbar = Gtk.Box(spacing=8)
        self.import_search = Gtk.SearchEntry()
        self.import_search.set_placeholder_text("Search themes")
        self.import_search.set_hexpand(True)
        self.import_search.connect("search-changed", lambda *_: self.import_grid.invalidate_filter())
        im_toolbar.append(self.import_search)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("tm-paste")
        refresh.set_tooltip_text("Reload the list from omarchy.org")
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
        self.import_grid.set_min_children_per_line(2)
        self.import_grid.set_max_children_per_line(2)
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
        answers = Gtk.Box(spacing=12)
        answers.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("tm-paste")
        cancel.connect("clicked", lambda *_: self.cancel_confirm())
        self.confirm_button = Gtk.Button(label="Delete")
        self.confirm_button.add_css_class("tm-paste")
        self.confirm_button.add_css_class("danger")
        self.confirm_button.connect("clicked", lambda *_: self.accept_confirm())
        answers.append(cancel)
        answers.append(self.confirm_button)
        confirm_page.append(answers)
        stack.add_named(confirm_page, "confirm")
        self.confirm_action = None
        self.confirm_return = "themes"

        # Capture phase: the list's own Up/Down handling would otherwise move
        # the selection a second time once a row has the keyboard focus.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        window.add_controller(keys)

        self.window = window
        self.stack = stack
        self.apply_theme_css()
        self.refresh_themes()

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

        radius = hyprland_rounding()
        sel_hex = colors.get("selection", "#45475a")
        bg_hex = colors.get("background", "#1e1e2e")
        fg_hex = colors.get("foreground", "#cdd6f4")
        muted_hex = colors.get("muted", "#888894")
        red_hex = colors.get("red", "#f04452")
        accent_hex = colors.get("accent", "#89b4fa")

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
        .tm-dropzone {{ border: 2px dashed alpha({fg_hex}, 0.30); border-radius: {radius}px;
            padding: 10px 12px; }}
        .tm-dropzone image {{ color: {muted_hex}; }}
        .tm-dropzone.active {{ border-color: {accent_hex}; background: alpha({accent_hex}, 0.12); }}
        .tm-dropzone.active image {{ color: {accent_hex}; }}
        .tm-dropzone-title {{ font-weight: 600; font-size: 15px; }}
        .tm-header-btn {{ padding: 4px; min-width: 0; min-height: 0; margin-right: 4px; }}
        .tm-list {{ background: transparent; }}
        .tm-list row {{ border-radius: {radius}px; padding: 6px 2px 6px 6px; min-height: 92px; }}
        .tm-list row:hover {{ background: alpha({fg_hex}, 0.06); }}
        .tm-list row:selected {{ background: {sel_hex}; color: {sel_fg}; }}
        .tm-row-name {{ font-size: 15px; font-weight: 600; }}
        .tm-row-hidden {{ opacity: 0.45; }}
        .tm-badge.hidden-badge {{ background: alpha({red_hex}, 0.25); }}
        .tm-badge.hidden-badge label {{ color: {fg_hex}; }}
        .tm-hint-row {{ min-height: 0; padding: 10px 8px; }}
        .tm-hint-row:hover {{ background: transparent; }}
        /* A light mat and hairline around previews so dark themes stand out
           from the (usually dark) card instead of melting into it. */
        .tm-preview {{ background: alpha({fg_hex}, 0.10); border: 1px solid alpha({fg_hex}, 0.28);
            border-radius: {max(4, radius - 4)}px; padding: 3px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.45); }}
        .tm-preview picture {{ border-radius: {max(2, radius - 6)}px; }}
        .tm-list row:hover .tm-preview {{ border-color: {accent_hex}; }}
        .tm-badge {{ background: alpha({fg_hex}, 0.10); border-radius: {max(4, radius - 6)}px;
            padding: 2px 10px; }}
        .tm-badge label {{ font-size: 12px; font-weight: 600; color: {muted_hex}; }}
        .tm-list row:selected .tm-badge:not(.current) label {{ color: {sel_fg}; }}
        .tm-badge.current {{ background: {accent_hex}; }}
        .tm-badge.current label {{ color: {bg_hex}; }}
        .tm-badge.danger {{ color: {red_hex}; }}
        button.tm-menu-btn {{ padding: 4px; min-width: 0; min-height: 0; margin-right: 4px; }}
        .tm-grid {{ background: transparent; }}
        .tm-tile {{ background: alpha({fg_hex}, 0.08); border: 1px solid alpha({fg_hex}, 0.22);
            border-radius: {radius}px; padding: 4px; }}
        .tm-tile picture {{ border-radius: {max(2, radius - 4)}px; }}
        .tm-tile-label {{ font-size: 13px; color: {muted_hex}; }}
        button.tm-tile-menu {{ padding: 3px; min-width: 0; min-height: 0; margin: 5px;
            background: rgba(0, 0, 0, 0.6); color: #ffffff; border-radius: {max(4, radius - 4)}px; }}
        button.tm-tile-menu:hover {{ background: {accent_hex}; color: {bg_hex}; }}
        .tm-tile-badge {{ margin: 6px; background: rgba(0, 0, 0, 0.6); }}
        .tm-grid flowboxchild {{ border-radius: {radius}px; padding: 0; outline: none; }}
        .tm-grid flowboxchild:focus .tm-tile {{ border-color: {accent_hex}; background: alpha({accent_hex}, 0.12); }}
        .tm-paste {{ font-weight: 600; font-size: 15px; padding: 8px 16px; }}
        button.tm-paste.danger {{ background: {red_hex}; color: #ffffff; }}
        button.tm-paste.danger:hover {{ background: shade({red_hex}, 1.1); }}
        popover.tm-popover contents {{ background-color: {bg_hex}; color: {fg_hex};
            border: 1px solid {accent_hex}; border-radius: {radius}px; padding: 5px;
            min-width: 190px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.7); }}
        popover.tm-popover arrow {{ background-color: {bg_hex}; border-color: {accent_hex}; }}
        .tm-menu-item {{ outline: none; background: transparent; border-radius: {radius}px; padding: 0; }}
        .tm-menu-item label {{ font-size: 13px; font-weight: 500; color: {fg_hex}; }}
        .tm-menu-item image {{ color: {fg_hex}; }}
        .tm-menu-item:focus {{ background: {sel_hex}; }}
        .tm-menu-item:focus label {{ color: {sel_fg}; font-weight: 600; }}
        .tm-menu-item:focus image {{ color: {sel_fg}; }}
        .tm-menu-item.destructive label, .tm-menu-item.destructive image {{ color: #ff9999; }}
        .tm-menu-item.destructive:focus {{ background: {red_hex}; }}
        .tm-menu-item.destructive:focus label, .tm-menu-item.destructive:focus image {{ color: #ffffff; }}
        .tm-menu-item:disabled {{ opacity: 0.5; }}
        separator.tm-menu-separator {{ background-color: {muted_hex}; background-image: none;
            opacity: 0.6; min-height: 1px; margin: 3px 6px; }}
        """
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
        first = self.rows.get_first_child()
        while first is not None:
            self.rows.remove(first)
            first = self.rows.get_first_child()
        selected = None
        query = self.filter_query.casefold().strip()
        self.update_title()
        self.no_match.set_label(f"No theme matches “{self.filter_query}”. Backspace or Esc clears the filter."
                                if query else "No themes.")
        hidden_out = 0
        for theme in self.theme_list:
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
        if hidden_out and not query:
            self.rows.append(self.hidden_hint_row(hidden_out))
        selected = selected or self.rows.get_first_child()
        if selected is not None:
            self.rows.select_row(selected)

    def hidden_hint_row(self, count: int) -> Gtk.ListBoxRow:
        """A note under the list: how many themes are hidden, and how to see them."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.set_focusable(False)
        row.add_css_class("tm-hint-row")
        label = Gtk.Label(xalign=0, wrap=True, max_width_chars=40)
        label.add_css_class("tm-hint")
        text = (f"{count} hidden theme{'s' if count != 1 else ''} not shown. "
                "The eye button in the header shows them.")
        if not theme_core.sync_enabled():
            text += " To hide them in Omarchy's own theme picker too, turn it on in Settings (⚙)."
        label.set_label(text)
        row.set_child(label)
        return row

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

    def build_settings_menu(self, popover) -> Gtk.Box:
        box = self.menu_box()
        on = theme_core.sync_enabled()
        self.menu_item(
            box, popover,
            "Also hide in Omarchy's pickers" if not on else "Also hiding in Omarchy's pickers ✓",
            lambda: self.set_omarchy_sync(not on),
            "object-select-symbolic" if on else "view-conceal-symbolic",
            tooltip=("Turn off: removes Theme Manager's menu entry and theme-set hook again"
                     if on else
                     "Turn on: hidden themes and wallpapers also leave Omarchy's theme and background "
                     "pickers. Adds one marked line to ~/.config/omarchy/extensions/omarchy-menu.jsonc "
                     "and the hook ~/.config/omarchy/hooks/theme-set.d/90-theme-manager."))
        return box

    def set_omarchy_sync(self, on: bool):
        def apply():
            theme_core.set_sync_enabled(on, SWITCHER_WRAPPER, SYNC_SCRIPT)
            if on:
                theme_core.prune_switcher_previews(theme_core.load_hidden())
        self._async(apply, lambda _r, error: theme_core.notify(
            "Could not change the Omarchy sync", str(error)) if error else theme_core.notify(
            "Omarchy sync " + ("on" if on else "off"),
            "Hidden themes and wallpapers now also leave Omarchy's pickers." if on else
            "Hiding now applies to Theme Manager only; Omarchy's pickers show everything."))
        self.refresh_themes()

    def update_title(self):
        label = "Theme Manager"
        if self.show_hidden:
            label += " (Showing Hidden)"
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
        self.filter_query = query
        self.refresh_themes(keep=None)
        self.select_row(self.rows.get_selected_row())

    def build_theme_row(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.tm_theme = theme
        if is_hidden:
            row.add_css_class("tm-row-hidden")
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
        text.append(name)
        badges = Gtk.Box(spacing=4)
        text.append(badges)
        line.append(text)

        if theme.current:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("current")
            badge.append(Gtk.Label(label="current"))
            badges.append(badge)
        if theme.scope == "system":
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.append(Gtk.Label(label="system"))
            badges.append(badge)
        if is_hidden:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("hidden-badge")
            badge.append(Gtk.Label(label="hidden"))
            badges.append(badge)

        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-menu-btn")
        actions.set_valign(Gtk.Align.CENTER)
        actions.set_tooltip_text("Theme actions (Right)")
        actions.connect("clicked", lambda button, target=theme: self.show_actions(button, target))
        line.append(actions)
        row.actions_button = actions

        row.set_child(line)
        return row

    def build_preview(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.Widget:
        frame = Gtk.Box()
        frame.add_css_class("tm-preview")
        frame.set_valign(Gtk.Align.CENTER)
        if theme.preview:
            picture = FixedPicture(PREVIEW_W, PREVIEW_H)
            self.decoder.request(picture, theme.preview, PREVIEW_W, PREVIEW_H)
            frame.append(picture)
        else:
            icon = Gtk.Image.new_from_icon_name("applications-graphics")
            icon.set_pixel_size(48)
            icon.set_size_request(PREVIEW_W, PREVIEW_H)
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
        popover.set_child(build(popover))
        popover.popup()
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
        popover.popdown()
        if self.stack.get_visible_child_name() == "wallpapers" and self.menu_anchor is not None:
            self.focus_tile_of(self.menu_anchor)
        else:
            row = self.rows.get_selected_row()
            if row is not None:
                row.grab_focus()
        return True

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
            item("Current theme", lambda: None, "object-select-symbolic", sensitive=False)
        else:
            item("Apply theme", lambda: self.apply_theme_action(theme), "media-playback-start-symbolic")
        item("Wallpapers", lambda: self.open_wallpapers_for(theme), "image-x-generic-symbolic",
             end_icon="go-next-symbolic")
        separator()
        if theme.slug in self.hidden:
            item("Unhide from list", lambda: self.set_theme_hidden(theme, False), "view-reveal-symbolic")
        else:
            item("Hide from list", lambda: self.set_theme_hidden(theme, True), "view-conceal-symbolic",
                 sensitive=not theme.current,
                 tooltip="This is the active theme: apply another theme first" if theme.current else None)
        separator()
        if theme.scope == "user" and theme.current:
            item("Delete theme…", lambda: None, "user-trash-symbolic", sensitive=False,
                 tooltip="This is the active theme: apply another theme first")
        elif theme.scope == "user":
            item("Delete theme…", lambda: self.confirm_delete_theme(theme), "user-trash-symbolic",
                 destructive=True)
        else:
            button = item("System theme (read-only)", lambda: None, "changes-prevent-symbolic",
                          sensitive=False)
            button.set_tooltip_text("System themes are owned by root and cannot be deleted; hide them instead")
        return box

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

    def on_list_pointer(self, x: float, y: float, moved: bool):
        last, self.pointer_at = self.pointer_at, (round(x), round(y))
        if not moved or last is None or last == self.pointer_at or self.actions_menu_open():
            return
        if self.stack.get_visible_child_name() != "themes":
            return
        inside = self.card.translate_coordinates(self.rows, x, y)
        if inside is None:
            return
        row = self.rows.pick(inside[0], inside[1], Gtk.PickFlags.DEFAULT)
        while row is not None and not isinstance(row, Gtk.ListBoxRow):
            row = row.get_parent()
        if row is not None and hasattr(row, "tm_theme") and row is not self.rows.get_selected_row():
            self.select_row(row)

    def select_row(self, row):
        if row is not None:
            self.rows.select_row(row)
            row.grab_focus()

    # --- list actions ----------------------------------------------------------------

    def toggle_show_hidden(self, *_):
        self.show_hidden = not self.show_hidden
        if self.show_hidden:
            self.hidden_button.set_icon_name("view-conceal-symbolic")
            self.hidden_button.set_tooltip_text("Hide hidden themes and wallpapers")
        else:
            self.hidden_button.set_icon_name("view-reveal-symbolic")
            self.hidden_button.set_tooltip_text("Show hidden themes and wallpapers")
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
        theme_core.prune_switcher_previews(self.hidden)
        if unhidden:
            # Rebuild the picker cache so the theme returns; the wrapper
            # prunes the still-hidden ones again after the rebuild.
            theme_core.invalidate_switcher_cache()
            theme_core.preload_switcher(SWITCHER_WRAPPER)

    def open_wallpapers_for(self, theme: theme_core.ThemeInfo):
        if theme is None:
            return
        self.active_theme = theme
        self.wp_filter = ""
        self.wp_title.set_label(f"{theme_core.plain_text(theme.name)} — wallpapers")
        system = theme.scope == "system"
        self.paste_button.set_tooltip_text(
            "Add the image copied to the clipboard (from the web or the file manager)"
            + (" — stored in your per-theme folder" if system else ""))
        self.drop_hint.set_label(
            "From the file manager, or copy and paste with Ctrl+V. "
            + ("Stored in the user wallpaper folder, next to the theme's own wallpapers."
               if system else
               "Stored in the theme's user wallpaper folder."))
        self.refresh_wallpapers()
        self.stack.set_visible_child_name("wallpapers")

    def show_themes(self):
        back_to = self.active_theme.slug if self.active_theme else None
        self.active_theme = None
        self.stack.set_visible_child_name("themes")
        self.refresh_themes(keep=back_to)
        self.select_row(self.rows.get_selected_row())

    # --- wallpapers from the web (wallhaven.cc) ----------------------------------------

    def open_web_search(self):
        theme = self.active_theme
        if theme is None:
            return
        self.browser = theme_catalog.default_browser()
        self.ws_title.set_label(f"{theme_core.plain_text(theme.name)} — search wallpapers")
        self.stack.set_visible_child_name("websearch")
        if not self.web_query:
            self.web_hint.set_label(self.web_hint_text())
        self.web_search.grab_focus()

    def close_web_search(self):
        self.stack.set_visible_child_name("wallpapers")
        self.refresh_wallpapers()

    def web_hint_text(self, status: str = "") -> str:
        text = status or ("Type what you are looking for and press Enter. Results come from "
                          f"{wallpaper_search.SOURCE}: safe for work, landscape, at least 1920×1080.")
        if self.browser is None:
            text += " No default web browser is set, so wallpaper pages can't be opened."
        return text

    def run_web_search(self):
        query = wallpaper_search.clean_query(self.web_search.get_text())
        if not query:
            return
        self.web_query = query
        self.web_page_no = 0
        self.web_last_page = 1
        self.web_generation += 1
        self.web_pictures = {}
        child = self.web_grid.get_first_child()
        while child is not None:
            self.web_grid.remove(child)
            child = self.web_grid.get_first_child()
        self.load_more_web_results()

    def load_more_web_results(self):
        if self.web_loading or not self.web_query or self.web_page_no >= self.web_last_page:
            return
        self.web_loading = True
        query, page, generation = self.web_query, self.web_page_no + 1, self.web_generation
        self.web_hint.set_label(self.web_hint_text(f"Searching {wallpaper_search.SOURCE} for “{query}”…"))
        self._async(lambda: wallpaper_search.search(query, page),
                    lambda result, error: self.show_web_results(generation, page, result, error))

    def show_web_results(self, generation, page, result, error):
        if generation != self.web_generation:
            return  # answer to an older search
        self.web_loading = False
        if error is not None or result is None:
            self.web_hint.set_label(self.web_hint_text(f"Search failed: {error}"))
            return
        results, last_page = result
        self.web_page_no, self.web_last_page = page, last_page
        theme = self.active_theme
        for item in results:
            added = theme is not None and wallpaper_search.already_added(None, theme.slug, item)
            self.web_grid.append(self.build_web_tile(item, added))
        shown = len(self.children_of(self.web_grid))
        if not shown:
            status = f"Nothing found for “{self.web_query}”. Try other words."
        else:
            more = " Scroll down for more." if page < last_page else ""
            status = f"{shown} wallpapers for “{self.web_query}”.{more} Enter on a tile adds it."
        self.web_hint.set_label(self.web_hint_text(status))
        self.load_web_thumbs([r for r in results if r.id in self.web_pictures], generation)

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
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(TILE, TILE * 9 // 16)
        self.web_pictures[item.id] = picture
        frame = Gtk.Overlay()
        frame.set_child(picture)
        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)
        actions.set_tooltip_text("Wallpaper actions (Enter)")
        actions.connect("clicked", lambda button, i=item: self.show_web_menu(button, i))
        frame.add_overlay(actions)
        if added:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.END)
            badge.append(Gtk.Label(label="added"))
            frame.add_overlay(badge)
        tile.append(frame)
        caption = Gtk.Label(label=item.resolution.replace("x", " × "), xalign=0.5)
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
                path = wallpaper_search.fetch_thumb(item)
                if path is not None:
                    GLib.idle_add(self.show_web_thumb, item.id, str(path))

        for _ in range(min(PREVIEW_WORKERS, len(queue))):
            threading.Thread(target=worker, daemon=True).start()

    def show_web_thumb(self, wid: str, path: str):
        picture = self.web_pictures.pop(wid, None)
        if picture is not None:
            self.decoder.request(picture, path, TILE, TILE * 9 // 16)
        return GLib.SOURCE_REMOVE

    def show_web_menu(self, button, item):
        self.show_menu(button, lambda popover: self.build_web_menu(popover, item))

    def build_web_menu(self, popover, item) -> Gtk.Box:
        theme = self.active_theme
        box = self.menu_box()

        def entry(label, handler, icon_name, **kwargs):
            return self.menu_item(box, popover, label, handler, icon_name, **kwargs)

        if theme is not None and wallpaper_search.already_added(None, theme.slug, item):
            entry("Already added", lambda: None, "object-select-symbolic", sensitive=False)
        elif theme is not None:
            entry(f"Add to {theme_core.plain_text(theme.name)}", lambda: self.add_web_wallpaper(item, False),
                  "list-add-symbolic", tooltip=f"Saved in ~/.config/omarchy/backgrounds/{theme.slug}/")
            if theme.current:
                entry("Add and set as background", lambda: self.add_web_wallpaper(item, True),
                      "preferences-desktop-wallpaper-symbolic")
        self.menu_separator(box)
        if self.browser is not None:
            entry(f"Open on {wallpaper_search.SOURCE}", lambda: wallpaper_search.open_page(item),
                  "web-browser-symbolic", tooltip=item.page)
        else:
            entry("Open page (no browser)", lambda: None, "web-browser-symbolic", sensitive=False,
                  tooltip="No default web browser is set")
        return box

    def add_web_wallpaper(self, item, set_background: bool):
        theme = self.active_theme
        if theme is None:
            return
        self.web_hint.set_label(self.web_hint_text(f"Downloading {item.file_name}…"))
        self._async(lambda: wallpaper_search.download(None, theme.slug, item),
                    lambda path, error: self.after_web_add(theme, item, path, error, set_background))

    def after_web_add(self, theme, item, path, error, set_background: bool):
        if error is not None:
            theme_core.notify("Could not add wallpaper", f"{item.file_name}: {error}")
            self.web_hint.set_label(self.web_hint_text(f"Could not add {item.file_name}: {error}"))
            return
        theme_core.notify("Wallpaper added", f"{path.name} → {theme_core.plain_text(theme.name)}")
        self.web_hint.set_label(self.web_hint_text(f"Added {path.name} to {theme_core.plain_text(theme.name)}."))
        for child in self.children_of(self.web_grid):
            if getattr(child, "tm_web", None) == item:
                index = child.get_index()
                self.web_grid.remove(child)
                self.web_pictures.pop(item.id, None)
                self.web_grid.insert(self.build_web_tile(item, True), index)
                thumb = wallpaper_search.thumb_path(item)
                if thumb.is_file():
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
        self.import_hint.set_label("Loading themes from omarchy.org…")
        self._async(lambda: theme_catalog.load_catalog(force=force),
                    lambda result, error: self.fill_catalog(*result) if result else
                    self.import_hint.set_label(f"Could not load the theme list: {error}"))

    def import_hint_text(self, count: int) -> str:
        text = f"{count} themes from omarchy.org. Enter opens a theme's menu: install, " \
               "install and apply, or open its page."
        if self.browser is None:
            text += " No default web browser is set, so theme pages can't be opened. " \
                    "Install one with Menu → Install → Web Browser, or set one with " \
                    "xdg-settings set default-web-browser <app>.desktop."
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
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(TILE, TILE * 9 // 16)
        cached = theme_catalog.preview_path(entry)
        if cached.is_file():
            self.decoder.request(picture, cached, TILE, TILE * 9 // 16)
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
        actions.set_tooltip_text("Theme actions (Enter)")
        actions.connect("clicked", lambda button, e=entry: self.show_catalog_menu(button, e))
        frame.add_overlay(actions)
        if installed:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.END)
            badge.append(Gtk.Label(label="installed"))
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
            self.decoder.request(picture, path, TILE, TILE * 9 // 16)
        return GLib.SOURCE_REMOVE

    def show_catalog_menu(self, button, entry):
        self.show_menu(button, lambda popover: self.build_catalog_menu(popover, entry))

    def build_catalog_menu(self, popover, entry) -> Gtk.Box:
        box = self.menu_box()
        installed = entry.slug in {t.slug for t in theme_core.discover_themes()}

        def item(label, handler, icon_name, **kwargs):
            return self.menu_item(box, popover, label, handler, icon_name, **kwargs)

        if installed:
            item("Installed", lambda: None, "object-select-symbolic", sensitive=False)
            item("Apply theme", lambda: self.apply_theme_action(
                next(t for t in theme_core.discover_themes() if t.slug == entry.slug)),
                "media-playback-start-symbolic")
        else:
            item("Install", lambda: self.install_catalog(entry, apply=False), "folder-download-symbolic",
                 tooltip=f"git clone into ~/.config/omarchy/themes/{entry.slug}")
            item("Install and apply", lambda: self.install_catalog(entry, apply=True),
                 "media-playback-start-symbolic")
        self.menu_separator(box)
        if self.browser is not None:
            item("Open theme page", lambda: theme_catalog.open_in_browser(entry.repo), "web-browser-symbolic",
                 tooltip=entry.repo)
        else:
            item("Open theme page (no browser)", lambda: None, "web-browser-symbolic", sensitive=False,
                 tooltip="No default web browser is set")
        return box

    def install_catalog(self, entry, apply: bool):
        self.import_hint.set_label(f"Installing {entry.name}…")

        def install():
            path = theme_catalog.install(entry)
            if theme_core.sync_enabled():
                theme_core.refresh_switcher_cache(SWITCHER_WRAPPER)  # see perform_delete_theme
            return path
        self._async(install,
                    lambda path, error: self.after_install(entry, apply, error))

    def after_install(self, entry, apply: bool, error):
        if error is not None:
            theme_core.notify("Could not install theme", f"{entry.name}: {error}")
            self.import_hint.set_label(self.import_hint_text(len(self.catalog)))
            return
        theme_core.notify("Theme installed", entry.name)
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
        self.close()
        self._async(lambda: theme_core.apply_theme(theme.slug),
                    lambda ok, error: self.after_apply(theme, ok, error))

    def after_apply(self, theme: theme_core.ThemeInfo, ok: bool, error: Exception | None):
        if ok:
            theme_core.notify("Theme applied", theme.name)
        else:
            theme_core.notify("Could not apply theme",
                              f"{theme.name}: {error or 'omarchy theme set failed'}")
        self.quit()  # The popup's job is done; the next toggle re-discovers state.

    def confirm_delete_theme(self, theme: theme_core.ThemeInfo):
        message = f"Delete theme “{theme_core.plain_text(theme.name)}”?"
        notes = []
        if (theme_core.default_roots().system_themes / theme.slug).is_dir():
            notes.append("Only your copy is removed; the system version stays.")
        notes.append("Wallpapers you added to it are kept.")
        self.ask_confirm(message + "\n\n" + "\n".join(notes), "Delete theme",
                         lambda: self.perform_delete_theme(theme))

    def perform_delete_theme(self, theme: theme_core.ThemeInfo):
        """The confirmed step: actually remove the theme (off-thread)."""
        self.close()
        def remove():
            path = theme_core.remove_theme(None, theme.slug)
            # The theme folder changed: rebuild Omarchy's picker cache now,
            # filtered, before anything else rebuilds it unfiltered.
            if theme_core.sync_enabled():
                theme_core.refresh_switcher_cache(SWITCHER_WRAPPER)
            return path
        self._async(remove, lambda _path, error: self.after_delete_theme(theme, error))

    def after_delete_theme(self, theme: theme_core.ThemeInfo, error: Exception | None):
        if error is None:
            theme_core.notify("Theme removed", theme.name)
        else:
            theme_core.notify("Could not delete theme", f"{theme.name}: {error}")
        self.quit()

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
        if not wallpapers:
            self.wp_grid.append(self._empty_tile(
                f"No wallpaper matches “{self.wp_filter}”" if query else None))
            return
        for wallpaper in wallpapers:
            self.wp_grid.append(self.build_wallpaper_tile(wallpaper))

    def update_wp_title(self):
        theme = self.active_theme
        if theme is None:
            return
        label = f"{theme_core.plain_text(theme.name)} — wallpapers"
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
        empty = Gtk.Label(label=text or "No wallpapers yet — drop or paste one",
                          wrap=True, max_width_chars=18, justify=Gtk.Justification.CENTER)
        empty.add_css_class("tm-tile-label")
        box.append(empty)
        box.set_halign(Gtk.Align.CENTER)
        return box

    def build_wallpaper_tile(self, wallpaper: theme_core.WallpaperInfo) -> Gtk.Widget:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")
        if wallpaper.hidden:
            tile.add_css_class("tm-row-hidden")

        picture = Gtk.Picture()
        self.decoder.request(picture, wallpaper.path, TILE, TILE * 9 // 16)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(TILE, TILE * 9 // 16)  # wallpapers are 16:9
        picture.set_valign(Gtk.Align.CENTER)

        frame = Gtk.Overlay()
        frame.set_child(picture)
        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)  # the tile itself takes the keyboard focus
        actions.set_tooltip_text("Wallpaper actions (Enter)")
        actions.connect("clicked", lambda button, w=wallpaper: self.show_wallpaper_menu(button, w))
        frame.add_overlay(actions)
        if wallpaper.hidden:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.END)
            badge.append(Gtk.Label(label="hidden"))
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
            item("Set as background", lambda: self.set_background(wallpaper),
                 "preferences-desktop-wallpaper-symbolic")
            self.menu_separator(box)
        if wallpaper.hidden:
            item("Unhide", lambda: self.set_wallpaper_hidden(wallpaper, False), "view-reveal-symbolic",
                 tooltip="Show it again here and in Omarchy's background picker")
        else:
            item("Hide", lambda: self.set_wallpaper_hidden(wallpaper, True), "view-conceal-symbolic",
                 tooltip="Keep it out of Omarchy's background picker and rotation; the file stays")
        self.menu_separator(box)
        if wallpaper.deletable:
            item("Delete…", lambda: self.confirm_delete_wallpaper(wallpaper), "user-trash-symbolic",
                 destructive=True)
        else:
            item("Can't delete: hide instead", lambda: None, "changes-prevent-symbolic",
                 sensitive=False, tooltip=wallpaper.locked_reason)
        return box

    def set_wallpaper_hidden(self, wallpaper: theme_core.WallpaperInfo, hide: bool):
        theme = self.active_theme
        if theme is None:
            return
        try:
            theme_core.set_wallpaper_hidden(None, theme, wallpaper, hide)
            if theme.current and theme_core.sync_enabled():
                theme_core.sync_current_theme()
        except (ValueError, OSError) as error:
            theme_core.notify("Could not " + ("hide" if hide else "unhide") + " wallpaper",
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
        self.ask_confirm(f"Delete wallpaper “{theme_core.plain_text(wallpaper.name)}” from "
                         f"“{theme_core.plain_text(theme.name) if theme else ''}”?",
                         "Delete wallpaper",
                         lambda: self.perform_delete_wallpaper(wallpaper))

    def ask_confirm(self, message: str, button_label: str, on_confirm):
        self.confirm_return = self.stack.get_visible_child_name() or "themes"
        self.confirm_action = on_confirm
        self.confirm_label.set_label(message)
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
        self._async(lambda: theme_core.remove_wallpaper(None, wallpaper.path),
                    lambda _path, error: self.after_delete_wallpaper(wallpaper, error))

    def after_delete_wallpaper(self, wallpaper: theme_core.WallpaperInfo,
                               error: Exception | None):
        theme = self.active_theme
        if error is None and theme is not None and theme.current:
            # Also moves the desktop off a deleted background.
            theme_core.sync_current_theme(hidden_override=None if theme_core.sync_enabled() else {})
        if error is None:
            theme_core.notify("Wallpaper deleted", wallpaper.name)
        else:
            theme_core.notify("Could not delete wallpaper",
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
            theme_core.notify("Could not read the clipboard", str(error))
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
            theme_core.notify("Wallpaper added",
                              f"{dest.name} → {theme_core.plain_text(theme.name)}")
        except (ValueError, OSError) as add_error:
            theme_core.notify("Could not add wallpaper", str(add_error))
            return
        self.refresh_wallpapers()

    def after_paste_files(self, paths, error: Exception | None):
        if error is not None or not paths:
            theme_core.notify("No image in the clipboard",
                              "Copy an image first (in the browser or the file manager).")
            return
        self.add_files(paths)

    def drop_allowed(self) -> bool:
        return self.active_theme is not None and \
            self.stack.get_visible_child_name() == "wallpapers"

    def on_drag_motion(self, _target, _drop, _x, _y):
        if not self.drop_allowed():
            return 0
        self.drop_zone.add_css_class("active")
        return Gdk.DragAction.COPY

    def on_drop(self, _target, drop, _x, _y):
        self.drop_zone.remove_css_class("active")
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
                    theme_core.notify("Nothing added", "The image could not be read.")
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
                theme_core.notify("Nothing added", "The drop could not be read.")
                return

            def got_data(data):
                done(data is not None)
                if data is None:
                    theme_core.notify("Nothing added", "The dropped file list was too long or too slow.")
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
            theme_core.notify("Nothing added", "The image could not be read (or is over 50 MiB).")
            return
        try:
            dest = theme_core.add_background_data(None, theme.slug, data, name=name)
        except (ValueError, OSError) as error:
            theme_core.notify("Could not add wallpaper", str(error))
            return
        theme_core.notify("Wallpaper added", f"{dest.name} → {theme_core.plain_text(theme.name)}")
        self.refresh_wallpapers()

    def drop_value(self, value) -> bool:
        """Add the files named by a dropped file list or text."""
        if not self.drop_allowed():
            return False
        paths = dropped_paths(value) if value is not None else []
        if not paths:
            theme_core.notify("Nothing added", "Drop local image files (JPG, PNG, WebP, GIF, BMP, HEIC).")
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
            theme_core.notify(f"Wallpaper{'s' if len(added) > 1 else ''} added",
                              ", ".join(added[:5]) + ("…" if len(added) > 5 else ""))
        if errors:
            theme_core.notify("Some files were skipped",
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
        page = self.stack.get_visible_child_name()
        back = keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace)
        if self.actions_menu_open() and keyval != Gdk.KEY_Escape:
            return False  # the menu handles its own keys
        if keyval == Gdk.KEY_Escape:
            if self.actions_menu_open():
                self.actions_popover.popdown()
            elif page == "confirm":
                self.cancel_confirm()
            elif page == "websearch":
                self.close_web_search()
            elif page == "wallpapers" and self.wp_filter:
                self.set_wp_filter("")
            elif page in ("wallpapers", "import"):
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
        if page == "wallpapers":
            tile = self.focused_tile()
            if tile is not None and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
                                               Gdk.KEY_space, Gdk.KEY_Menu):
                self.show_wallpaper_menu(tile.actions_button, tile.tm_wallpaper)
                return True
            if keyval == Gdk.KEY_BackSpace and self.wp_filter:
                self.set_wp_filter(self.wp_filter[:-1])
                return True
            # Left goes back from the left column; from the right one it moves left.
            if back and not (keyval != Gdk.KEY_BackSpace and tile is not None and tile.get_index() % 2):
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
            if char and not (char == " " and tile is not None):
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
        in_search = focus is not None and (focus is self.web_search or focus.is_ancestor(self.web_search))
        tile = self.focused_web_tile()
        if tile is not None and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
                                           Gdk.KEY_space, Gdk.KEY_Menu):
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
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and tile is not None and tile.get_index() % 2:
            return False  # right column: move left
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_BackSpace):
            self.close_web_search()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) and tile is not None and tile.get_index() < 2:
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
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and tile is not None and tile.get_index() % 2:
            return False  # right column: move left
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

    def focused_tile(self):
        widget = self.window.get_focus()
        while widget is not None and not isinstance(widget, Gtk.FlowBoxChild):
            widget = widget.get_parent()
        return widget if widget is not None and hasattr(widget, "tm_wallpaper") else None

    def on_list_key(self, keyval) -> bool:
        row = self.rows.get_selected_row()
        if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_Menu):
            if row is not None:
                self.show_actions(row.actions_button, row.tm_theme)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            if row is not None:
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
            step = moves[keyval]
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
        self.hidden = self.load_hidden_list()
        self.filter_query = ""
        if self.show_hidden:
            self.toggle_show_hidden()   # hidden themes are shown only on request, per open
        # Omarchy may have rebuilt its picker cache (e.g. a theme was installed).
        if theme_core.sync_enabled():
            theme_core.prune_switcher_previews(self.hidden)
        self.refresh_themes()
        self.grab_keyboard()
        self.move_to(self.position)
        self.window.present()
        self.select_row(self.rows.get_selected_row())

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
        self.moving = True
        self.move_start = (self.current_position(), theme_core.cursor_position())

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

    def close(self):
        if self.actions_menu_open():
            self.actions_popover.popdown()
        if self.stack.get_visible_child_name() == "confirm":
            self.cancel_confirm()  # never reopen onto a stale question
        self.window.hide()


def main():
    app = ThemeManager()
    atexit.register(cleanup_pid)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
