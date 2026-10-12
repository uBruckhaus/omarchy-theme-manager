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

import theme_auto
import theme_catalog
import theme_core
import theme_custom
import theme_shortcuts as shortcuts
import wallpaper_search
from palette import ThemePalette
import pidfile

# Default outer size matches Simple Launcher: a 420 px card around a 590 px
# list, the same on every page so switching pages never resizes the popup.
# The grip in the bottom-right corner resizes it; the grids then show more
# columns (the size is remembered in settings.json).
CARD_WIDTH = 420
LIST_HEIGHT = 590
TILE_STEP = 196        # one grid column: tile, its padding and border, the spacing
UNDO_SECONDS = 10      # how long Undo is offered after a delete
PREVIEW_SECONDS = 5    # a preview started from a menu
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


class ThemeGrid(Gtk.FlowBox):
    """The theme list: theme cards of one fixed width, one column at the
    default size and more as the popup gets wider (like the wallpaper
    grid). Keeps the few ListBox calls the rest of the popup uses."""

    def __init__(self):
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.set_homogeneous(True)
        self.set_activate_on_single_click(True)
        self.set_column_spacing(8)
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
        size = theme_core.window_size() or (CARD_WIDTH, LIST_HEIGHT)
        self.card_w, self.list_h = size
        self.columns = 2
        self.theme_columns = 1
        self.theme_keys = {}
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
        drops buffer the whole offer in memory with no limit or deadline."""
        text = entry.get_delegate()
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

    def on_field_paste(self, text, entry):
        text.stop_emission_by_name("paste-clipboard")

        def worker():
            pasted = theme_core.clipboard_text()
            GLib.idle_add(self.insert_pasted, entry, pasted)

        threading.Thread(target=worker, daemon=True).start()

    def insert_pasted(self, entry, pasted: str | None):
        if pasted is None:
            theme_core.notify("Theme Manager", "Clipboard text is too long to paste "
                              f"(max {theme_core.MAX_PASTE_TEXT_BYTES // 1024} KiB)")
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
        grip.set_tooltip_text("Drag to resize · double-click for the default size")
        sizer = Gtk.GestureDrag()
        sizer.connect("drag-begin", self.on_resize_begin)
        sizer.connect("drag-update", self.on_resize_update)
        sizer.connect("drag-end", self.on_resize_end)
        grip.add_controller(sizer)
        frame.add_overlay(grip)
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
        title.set_tooltip_text("Drag to move · double-click: default size, centered")
        mover = Gtk.GestureDrag()
        mover.connect("drag-begin", self.on_move_begin)
        mover.connect("drag-update", self.on_move_update)
        mover.connect("drag-end", self.on_move_end)
        header.add_controller(mover)
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
        settings_button.connect("clicked", lambda *_: self.open_settings())
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
        self.track_width(scroller)
        self.list_scroller = scroller
        self.rows = ThemeGrid()
        self.rows.add_css_class("tm-list")
        self.rows.connect("child-activated", lambda _rows, row: self.open_wallpapers_for(row.tm_theme))
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
        self.track_width(wallpaper_page)

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
        self.guard_text_field(self.web_search)
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
        self.track_width(import_page)
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
        self.guard_text_field(self.import_search)
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

        stack.add_named(self.build_settings_page(), "settings")
        stack.add_named(self.build_editor_page(), "editor")
        stack.add_named(self.build_shortcut_page(), "shortcut")

        # Undo after a delete: the file went to the trash and comes back from there.
        self.undo_bar = Gtk.Revealer()
        self.undo_bar.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        undo_line = Gtk.Box(spacing=8)
        undo_line.add_css_class("tm-undo")
        self.undo_label = Gtk.Label(xalign=0, hexpand=True)
        self.undo_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.undo_label.set_max_width_chars(1)
        undo_line.append(self.undo_label)
        undo_button = Gtk.Button(label="Undo")
        undo_button.add_css_class("tm-paste")
        undo_button.set_tooltip_text("Put it back (Ctrl+Z)")
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
        self.resize_to(self.card_w, self.list_h)
        self.apply_theme_css()
        self.refresh_themes()

    # --- size ----------------------------------------------------------------------

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
        for widget in self.sized:
            widget.set_size_request(self.card_w, -1)
        # Raise the maximum first: GTK refuses a minimum above the maximum.
        self.list_scroller.set_max_content_height(100000)
        self.list_scroller.set_min_content_height(self.list_h)
        self.list_scroller.set_max_content_height(self.list_h)
        self.columns = max(2, (self.card_w + 8) // TILE_STEP)
        # Themes: the list at the default width; any wider, tiles in columns
        # like the wallpapers.
        was = self.theme_columns
        self.theme_columns = 1 if self.card_w < CARD_WIDTH + 40 else self.columns
        self.rows.set_min_children_per_line(self.theme_columns)
        self.rows.set_max_children_per_line(self.theme_columns)
        self.rows.set_halign(Gtk.Align.FILL)
        self.rows.set_row_spacing(2 if self.theme_columns == 1 else 8)
        if (was > 1) != (self.theme_columns > 1) and self.theme_list:
            self.refresh_themes()   # switch between list rows and tiles
        for grid in (self.wp_grid, self.web_grid, self.import_grid):
            grid.set_min_children_per_line(self.columns)
            grid.set_max_children_per_line(self.columns)
        if self.window is not None:
            # A layer surface does not shrink by itself once content gets smaller.
            self.window.set_default_size(1, 1)
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
    def settings_heading(box, text: str):
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("tm-setting-heading")
        box.append(label)

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
        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("tm-header-btn")
        back_box = Gtk.Box(spacing=6)
        back_box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        back_box.append(Gtk.Label(label="Back"))
        back.set_child(back_box)
        back.set_tooltip_text("Back to themes (Esc)")
        back.connect("clicked", lambda *_: self.show_themes())
        header.append(back)
        title = Gtk.Label(label="Settings", xalign=0, hexpand=True)
        title.add_css_class("tm-title")
        header.append(title)
        page.append(header)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self.settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.settings_box.add_css_class("tm-settings")
        scroller.set_child(self.settings_box)
        page.append(scroller)
        self.settings_ready = False
        return page

    def open_settings(self):
        self.active_theme = None
        self.stop_preview()
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
        themes = sorted(theme_core.discover_themes(), key=lambda t: t.name.casefold())
        slugs = [t.slug for t in themes]
        names = [theme_core.plain_text(t.name, 60) for t in themes]

        self.settings_heading(box, "Themes")
        self.settings_row(box, "Sort the list", "Favorites (★ in a theme's menu) always come first.",
                          self.dropdown(["By name", "Recently used first"],
                                        theme_auto.SORT_CHOICES.index(theme_auto.sort_order()),
                                        lambda i: self.save_setting(sort=theme_auto.SORT_CHOICES[i])))
        self.settings_row(box, "Also hide in Omarchy's pickers",
                          "Hidden themes and wallpapers also leave Menu → Style. Adds one marked line to "
                          "omarchy-menu.jsonc and a theme-set hook; off removes them again.",
                          self.switch(theme_core.sync_enabled(), self.set_omarchy_sync))

        plan = theme_auto.schedule()
        self.settings_heading(box, "Day and night")
        self.settings_row(box, "Switch themes by time of day",
                          "At the times below. A theme you pick by hand stays until the next switch.",
                          self.switch(plan["enabled"], self.set_schedule_enabled))
        times = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
        for key, at_key, label in (("day", "day_at", "Day theme"), ("night", "night_at", "Night theme")):
            line = Gtk.Box(spacing=6)
            line.append(self.dropdown(["Choose…", *names],
                                      slugs.index(plan[key]) + 1 if plan[key] in slugs else 0,
                                      lambda i, k=key: i and self.save_schedule(**{k: slugs[i - 1]})))
            at = plan[at_key] if plan[at_key] in times else times[0]
            line.append(self.dropdown([f"from {t}" for t in times], times.index(at),
                                      lambda i, k=at_key: self.save_schedule(**{k: times[i]})))
            self.settings_row(box, label, "", line)

        self.settings_heading(box, "Wallpapers")
        self.settings_row(box, "A wallpaper for each workspace",
                          "Choose workspaces in a wallpaper's ☰ menu → Show on workspace. Workspaces "
                          "without one keep the theme's background.",
                          self.switch(theme_auto.workspace_wallpapers_enabled(),
                                      lambda on: self.save_setting(workspace_wallpapers=on)))
        labels = ["Never", "Every 5 minutes", "Every 10 minutes", "Every 15 minutes", "Every 30 minutes",
                  "Every hour", "Every 2 hours"]
        self.settings_row(box, "Change the wallpaper",
                          "Goes through the current theme's visible wallpapers.",
                          self.dropdown(labels, theme_auto.SLIDESHOW_CHOICES.index(theme_auto.slideshow_minutes()),
                                        lambda i: self.save_setting(
                                            slideshow_minutes=theme_auto.SLIDESHOW_CHOICES[i])))
        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        hint.add_css_class("tm-hint")
        hint.set_label("Scroll on the bar icon for the next or previous wallpaper; right-click it for your "
                       "favorite themes.")
        box.append(hint)

        self.settings_heading(box, "Keyboard shortcuts")
        state = shortcuts.load_state()
        for action in shortcuts.ACTIONS:
            self.shortcut_setting_row(box, action, state.get(action))
        themes = {t.slug: t for t in theme_core.discover_themes()}
        theme_keys = sorted((a for a in state if shortcuts.theme_of(a) in themes),
                            key=lambda a: shortcuts.action_label(a).casefold())
        for action in theme_keys:
            self.shortcut_setting_row(box, action, state[action])
        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        hint.add_css_class("tm-hint")
        hint.set_label("Give a theme its own key in its ⋮ menu → Shortcut…. Keys live in "
                       "~/.config/hypr/theme-manager-shortcuts.lua; removing the last one removes that file "
                       "and its line in hyprland.lua again.")
        box.append(hint)
        self.settings_ready = True

    def shortcut_setting_row(self, box, action: str, combo):
        line = Gtk.Box(spacing=6)
        if combo:
            line.append(self.keycaps(combo))
        button = Gtk.Button(label="Change…" if combo else "Set…")
        button.add_css_class("tm-paste")
        button.add_css_class("sc-small")
        button.connect("clicked", lambda *_: self.open_shortcut_page(action))
        line.append(button)
        slug = shortcuts.theme_of(action)
        note = "" if combo else "No key yet"
        if combo and slug and slug in self.hidden:
            note = "Hidden theme: the key still works and unhides it"
        self.settings_row(box, shortcuts.action_label(action), note, line)

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
        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("tm-header-btn")
        back_box = Gtk.Box(spacing=6)
        back_box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        back_box.append(Gtk.Label(label="Back"))
        back.set_child(back_box)
        back.set_tooltip_text("Back (Esc)")
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

        section("Current key")
        self.sc_current = Gtk.Box(spacing=8)
        box.append(self.sc_current)
        section("Choose a key")
        hint = Gtk.Label(label="Hold the modifiers and press a key, or toggle modifiers and click a key below.",
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
        self.sc_apply = Gtk.Button(label="Assign")
        self.sc_apply.add_css_class("tm-paste")
        self.sc_apply.add_css_class("suggested")
        self.sc_apply.connect("clicked", lambda *_: self.apply_shortcut())
        key_row.append(self.sc_apply)
        box.append(key_row)
        self.sc_status = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        self.sc_status.add_css_class("sc-status")
        box.append(self.sc_status)
        self.sc_letters_title = section("Keys")
        legend = Gtk.Box(spacing=10)
        for text, css in (("● free", "sc-free-key"), ("● taken (can be rebound)", "sc-yours-key"),
                          ("● Omarchy default", "sc-omarchy-key")):
            item = Gtk.Label(label=text)
            item.add_css_class("sc-legend")
            item.add_css_class(css)
            legend.append(item)
        box.append(legend)
        self.sc_letters = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                      column_spacing=4, row_spacing=4, min_children_per_line=7,
                                      max_children_per_line=13)
        box.append(self.sc_letters)
        section("Other keys")
        self.sc_others = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                     column_spacing=4, row_spacing=4, min_children_per_line=4,
                                     max_children_per_line=9)
        box.append(self.sc_others)
        self.sc_action = None
        self.sc_key = None
        self.sc_syncing = False
        self.sc_return = "themes"
        return page

    def open_shortcut_page(self, action: str):
        self.sc_return = self.stack.get_visible_child_name() or "themes"
        self.sc_action = action
        self.sc_state = shortcuts.load_state()
        self.sc_bindings = shortcuts.Bindings(self.sc_state)
        self.sc_title.set_label(f"Shortcut — {shortcuts.action_label(action)}")
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
            none = Gtk.Label(label="None yet — choose one below.", xalign=0)
            none.add_css_class("tm-hint")
            box.append(none)
            return
        box.append(self.keycaps(combo, large=True))
        if combo.get("replaces"):
            note = Gtk.Label(label=f"overrides “{combo['replaces']}”", xalign=0, hexpand=True)
            note.add_css_class("tm-hint")
            note.set_ellipsize(Pango.EllipsizeMode.END)
            note.set_max_width_chars(1)
            box.append(note)
        else:
            box.append(Gtk.Box(hexpand=True))
        remove = Gtk.Button(label="Remove")
        remove.add_css_class("tm-paste")
        remove.set_tooltip_text("Give the key back" + (f" to “{combo['replaces']}”" if combo.get("replaces") else ""))
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
        label, enabled = "Assign", combo is not None
        if combo is None:
            text = "Press a key." if mods else "Choose modifiers (Super, Shift, Ctrl, Alt), then a key."
        elif not mods:
            text, enabled = "A shortcut needs at least one modifier.", False
        elif level == shortcuts.FREE:
            text = f"{shortcuts.combo_label(combo)} is free."
            self.sc_status.add_css_class("sc-free")
        elif level == shortcuts.SAME:
            text, enabled = f"{shortcuts.combo_label(combo)} already does this.", False
        elif level == shortcuts.OURS:
            text, label = f"{shortcuts.combo_label(combo)} is the key for “{who}”: it moves here.", "Move here"
            self.sc_status.add_css_class("sc-warn")
        elif level == shortcuts.CUSTOM:
            text, label = (f"{shortcuts.combo_label(combo)} is your binding “{who}”. Rebinding turns it off "
                           "while this shortcut exists."), "Rebind"
            self.sc_status.add_css_class("sc-warn")
        else:
            text, label = (f"{shortcuts.combo_label(combo)} is Omarchy's “{who}”. Rebinding turns it off "
                           "while this shortcut exists."), "Rebind"
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
                    chip.set_tooltip_text(f"{name} — free" if level == shortcuts.FREE else
                                          f"{name} — already this shortcut" if level == shortcuts.SAME else
                                          f"{name} — “{who}”; click to rebind")
                else:
                    chip.set_tooltip_text("Choose modifiers first")
                if self.sc_key and key.casefold() == self.sc_key.casefold():
                    chip.add_css_class("sc-key-picked")
                chip.connect("clicked", lambda *_, k=key: self.pick_shortcut_key(k))
                grid.append(chip)
        self.sc_letters_title.set_label(
            "Keys with " + " + ".join(shortcuts.MOD_LABELS[m] for m in mods) if mods else "Keys")

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
            self.sc_status.set_label("That key cannot be used for a shortcut.")
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
        self.save_shortcuts(state, f"{shortcuts.action_label(self.sc_action)}: no key")

    def save_shortcuts(self, state, done_text):
        self.sc_apply.set_sensitive(False)
        self.sc_status.set_label("Saving and reloading Hyprland…")
        # Hyprland reloads its config: leave the capture submap first.
        self.leave_shortcut_capture()

        def after(_result, error):
            if self.sc_action is None:
                return
            shortcuts.enter_capture()
            if error is not None:
                self.sc_status.set_label(f"Hyprland refused the change, nothing was changed: {error}")
                self.update_shortcut_status()
                return
            theme_core.notify("Shortcut saved", done_text)
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

    def save_setting(self, **values):
        try:
            theme_auto.update_settings(**values)
        except OSError as error:
            theme_core.notify("Could not save the setting", str(error))
        if "sort" in values:
            self.refresh_themes()

    def save_schedule(self, **values):
        try:
            theme_auto.save_schedule(**values)
        except OSError as error:
            theme_core.notify("Could not save the setting", str(error))

    def set_schedule_enabled(self, on: bool):
        plan = theme_auto.schedule()
        if on and not (plan["day"] and plan["night"]):
            theme_core.notify("Choose a day and a night theme", "Switching starts once both are set.")
        self.save_schedule(enabled=on)

    # --- theme editor ------------------------------------------------------------------------

    def build_editor_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("tm-header-btn")
        back_box = Gtk.Box(spacing=6)
        back_box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        back_box.append(Gtk.Label(label="Back"))
        back.set_child(back_box)
        back.set_tooltip_text("Back to themes without saving (Esc)")
        back.connect("clicked", lambda *_: self.close_editor())
        header.append(back)
        self.ed_title = Gtk.Label(xalign=0, hexpand=True)
        self.ed_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.ed_title.set_max_width_chars(1)
        self.ed_title.add_css_class("tm-title")
        header.append(self.ed_title)
        page.append(header)
        # The sample stays above the controls, so every change is seen at once.
        self.ed_sample_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.ed_sample_holder)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self.ed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.ed_box.add_css_class("tm-settings")
        scroller.set_child(self.ed_box)
        page.append(scroller)
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
                    wallpaper: theme_core.WallpaperInfo | None = None):
        """Make a new theme from `base`, from one of its wallpapers (colors
        taken from the picture), or (edit=True) change a custom theme."""
        try:
            palette = theme_custom.palette_from_image(wallpaper.path) if wallpaper else \
                theme_custom.load_palette(base)
        except (OSError, ValueError) as error:
            theme_core.notify("Could not read the colors", str(error))
            return
        self.active_theme = None
        self.stop_preview()
        font = theme_custom.font_of(base) if edit else None
        self.ed = {"base": base, "edit": edit, "palette": palette, "edited": set(), "font": font,
                   "entries": {}, "wallpapers": wallpaper is None, "fonts": theme_custom.list_fonts(),
                   "wallpaper": wallpaper, "system_font": theme_custom.current_font()}
        name = theme_core.plain_text(base.name)
        self.ed_title.set_label(f"Edit {name}" if edit else
                                f"New theme from {theme_core.plain_text(wallpaper.name)}" if wallpaper else
                                f"New theme from {name}")
        self.ed_save.set_label("Save" if edit else "Create")
        self.ed_save_apply.set_label("Save and apply" if edit else "Create and apply")
        self.ed_hint.set_label("")
        self.fill_editor()
        self.stack.set_visible_child_name("editor")
        if not edit:
            self.ed_name.grab_focus()
            self.ed_name.select_region(0, -1)

    def close_editor(self, keep: str | None = None):
        base = self.ed["base"].slug if self.ed else None
        self.ed = None
        if self.ed_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.ed_provider)
            self.ed_provider = None
        self.stack.set_visible_child_name("themes")
        self.refresh_themes(keep=keep or base)
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
            self.ed_name.set_tooltip_text("Renaming keeps its wallpapers, favorites and other settings")
            self.settings_row(box, "Name", "", self.ed_name).add_css_class("tm-setting-compact")
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
            self.ed_name.set_tooltip_text("Shown in Omarchy's theme picker")
            self.settings_row(box, "Name", "", self.ed_name).add_css_class("tm-setting-compact")

        self.build_sample()

        # Name and font as compact rows (explanations in tooltips), so the
        # colors still start on the first screen at the default size.
        fonts = ed["fonts"]
        labels = ["Keep the current font", *fonts]
        selected_font = fonts.index(ed["font"]) + 1 if ed["font"] in fonts else 0
        font_drop = self.dropdown(labels, selected_font, self.set_editor_font)
        font_drop.set_tooltip_text("Omarchy's monospace font (terminals, bar, menus). It is switched when this "
                                   "theme is applied, and back to your own font with other themes.")
        self.settings_row(box, "Font", "", font_drop).add_css_class("tm-setting-compact")

        if not ed["edit"]:
            # Which wallpapers the new theme gets: a compact row up here too.
            count = sum(1 for w in theme_core.list_backgrounds(None, ed["base"]) if not w.hidden)
            base = theme_core.plain_text(ed["base"].name)
            if ed["wallpaper"] is not None:
                walls = self.switch(False, self.set_editor_wallpapers)
                walls.set_tooltip_text(f"{theme_core.plain_text(ed['wallpaper'].name)} is always included; "
                                       f"on: also {base}'s other {count - 1}. Change them later on the new "
                                       "theme's wallpaper page.")
                self.settings_row(box, f"Also {base}'s wallpapers", "", walls).add_css_class("tm-setting-compact")
            else:
                walls = self.switch(True, self.set_editor_wallpapers)
                walls.set_tooltip_text(f"Copies of {base}'s {count} visible wallpapers. Add, hide or delete "
                                       "them later on the new theme's wallpaper page.")
                self.settings_row(box, f"Take over its wallpapers ({count})", "", walls).add_css_class("tm-setting-compact")

        self.settings_heading(box, "Colors")
        for key in theme_custom.MAIN_KEYS + theme_custom.PALETTE_KEYS:
            box.append(self.color_row(key))
        more = Gtk.Expander(label="More colors: shades and bright variants")
        more.add_css_class("tm-setting-heading")
        more_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        more_box.set_margin_top(6)
        hint = Gtk.Label(label="These follow the colors above until you change them here.",
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
        wall = FixedPicture(1, 150)
        path = self.editor_wallpaper()
        if path is not None:
            self.decoder.request(wall, path, CARD_WIDTH, 150)
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
        for _ in self.SAMPLE_LINES:
            line = Gtk.Label(xalign=0)
            line.add_css_class("s-text")
            line.set_ellipsize(Pango.EllipsizeMode.END)
            line.set_max_width_chars(1)   # long lines must not widen the card
            window.append(line)
            lines.append(line)
        selected = Gtk.Label(label="selected line", xalign=0)
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
        swatch.set_tooltip_text("Pick a color")
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
            done = Gtk.Button(label=f"Use for {theme_custom.label(key).lower()}")
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
            self.ed_hint.set_label(f"“{value}” is not a color: use #rrggbb, e.g. #3a6ea5.")
            ed["entries"][key].set_text(ed["palette"].get(key))
            return
        if key not in theme_custom.MAIN_KEYS and key not in theme_custom.PALETTE_KEYS:
            ed["edited"].add(key)   # a shade picked by hand no longer follows its base color
        self.ed_hint.set_label("")
        for other, entry in ed["entries"].items():
            if entry.get_text().lower() != ed["palette"].get(other):
                entry.set_text(ed["palette"].get(other))
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
        css.append(f".tm-ed-desk {{ background: {c['darker_background']}; border-radius: 10px;"
                   " margin-bottom: 4px; }")
        css.append(f".tm-ed-desk label {{ {font} font-size: 11px; }}")
        css.append(f".tm-ed-desk .s-bar {{ background: alpha({c['background']}, 0.94); padding: 3px 10px; }}")
        css.append(f".tm-ed-desk .s-win {{ background: alpha({c['background']}, 0.97);"
                   f" border: 2px solid {c['accent']}; border-radius: 8px; margin: 8px 18px 10px 18px;"
                   " padding: 5px 10px; }")
        css.append(f".tm-ed-desk .s-text {{ color: {c['foreground']}; }}")
        css.append(f".tm-ed-desk .s-muted {{ color: {c['muted']}; }}")
        css.append(".tm-ed-desk .s-small { font-size: 10px; margin-bottom: 2px; }")
        css.append(f".tm-ed-desk .s-sel {{ background: {c['selection']}; color: {c['foreground']};"
                   " padding: 0 6px; border-radius: 3px; }")
        css.append(".tm-ed-desk .s-dot { border-radius: 4px; }")
        css.append("button.tm-swatch { border: 1px solid alpha(currentColor, 0.4); border-radius: 6px;"
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
                theme_custom.update_theme(theme, palette, font)
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
                return theme_custom.create_theme(base, name, palette, font, wallpapers,
                                                 extra_wallpapers=extra).name
        self.ed_hint.set_label("Saving…")
        self.ed_save.set_sensitive(False)
        self.ed_save_apply.set_sensitive(False)
        self._async(work, lambda slug, error: self.after_save_editor(slug, error, apply))

    def after_save_editor(self, slug, error, apply: bool):
        self.ed_save.set_sensitive(True)
        self.ed_save_apply.set_sensitive(True)
        if error is not None:
            self.ed_hint.set_label(f"Could not save: {error}")
            return
        edit = self.ed["edit"] if self.ed else False
        was_current = bool(self.ed and self.ed.get("was_current"))
        theme_core.notify("Theme saved" if edit else "Theme created", theme_core.display_name(slug))
        if theme_core.sync_enabled():
            self._async(lambda: theme_core.refresh_switcher_cache(SWITCHER_WRAPPER), lambda *_: None)
        self.close_editor(keep=slug)
        theme = next((t for t in theme_core.discover_themes() if t.slug == slug), None)
        # The theme in use is applied again (under its new name, if renamed),
        # so the new colors show everywhere.
        if theme is not None and (apply or theme.current or was_current):
            self.apply_theme_action(theme)

    # --- add wallpapers with a file chooser -------------------------------------------------

    def choose_files(self):
        """Pick images in a file chooser. Dialogs open beneath this overlay
        layer, so the popup steps aside until the dialog is closed."""
        theme = self.active_theme
        if theme is None:
            return
        dialog = Gtk.FileDialog(title=f"Add wallpapers to {theme_core.plain_text(theme.name)}", modal=False)
        images = Gtk.FileFilter(name="Images")
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
                theme_core.notify("Shortcut removed", "The key of " + ", ".join(
                    theme_core.display_name(t) for t in themes) + " is free again.")
        self._async(shortcuts.prune_missing, done)

    def undo_delete(self):
        receipts, self.undo_receipts = self.undo_receipts, []
        if self.undo_timer:
            GLib.source_remove(self.undo_timer)
            self.undo_timer = 0
        self.undo_bar.set_reveal_child(False)
        for receipt in receipts:
            try:
                theme_core.restore_from_trash(receipt)
            except OSError as error:
                theme_core.notify("Could not undo", str(error))
                continue
            theme_core.notify("Restored", receipt.original.name)
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
        self.web_hint.set_label(self.web_hint_text(f"Loading a preview of {item.file_name}…"))

        def show(path, _error):
            self.web_hint.set_label(self.web_hint_text(""))
            if path is None:
                self.preview = None
                theme_core.notify("No preview", f"{item.file_name} could not be loaded")
            elif self.preview == f"web:{item.id}":   # Space still held
                self.start_preview(path, seconds)
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

        radius = hyprland_rounding()
        sel_hex = colors.get("selection", "#45475a")
        bg_hex = colors.get("background", "#1e1e2e")
        fg_hex = colors.get("foreground", "#cdd6f4")
        muted_hex = colors.get("muted", "#888894")
        red_hex = colors.get("red", "#f04452")
        accent_hex = colors.get("accent", "#89b4fa")
        green_hex = colors.get("green", "#66c98b")
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
        .tm-list {{ background: transparent; }}
        .tm-list flowboxchild {{ border-radius: {radius}px; padding: 6px 2px 6px 6px; min-height: 92px;
            outline: none; }}
        .tm-list flowboxchild:hover {{ background: alpha({fg_hex}, 0.06); }}
        .tm-list flowboxchild:selected {{ background: {sel_hex}; color: {sel_fg}; }}
        .tm-row-name {{ font-size: 15px; font-weight: 600; }}
        .tm-row-hidden {{ opacity: 0.45; }}
        .tm-badge.hidden-badge {{ background: alpha({red_hex}, 0.25); }}
        .tm-badge.hidden-badge label {{ color: {fg_hex}; }}
        /* A light mat and hairline around previews so dark themes stand out
           from the (usually dark) card instead of melting into it. */
        .tm-preview {{ background: alpha({fg_hex}, 0.10); border: 1px solid alpha({fg_hex}, 0.28);
            border-radius: {max(4, radius - 4)}px; padding: 3px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.45); }}
        .tm-preview picture {{ border-radius: {max(2, radius - 6)}px; }}
        .tm-list flowboxchild:hover .tm-preview {{ border-color: {accent_hex}; }}
        .tm-badge {{ background: alpha({fg_hex}, 0.10); border-radius: {max(4, radius - 6)}px;
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
        .tm-tile picture {{ border-radius: {max(2, radius - 4)}px; }}
        .tm-tile-label {{ font-size: 13px; color: {muted_hex}; }}
        .tm-tile-name {{ font-size: 14px; font-weight: 600; }}
        .tm-list flowboxchild .tm-tile {{ margin: 0; }}
        button.tm-tile-menu {{ padding: 3px; min-width: 0; min-height: 0; margin: 5px;
            background: rgba(0, 0, 0, 0.6); color: #ffffff; border-radius: {max(4, radius - 4)}px; }}
        button.tm-tile-menu:hover {{ background: {accent_hex}; color: {bg_hex}; }}
        .tm-tile-badge {{ margin: 6px; background: rgba(0, 0, 0, 0.6); }}
        .tm-tile-badge:not(.current):not(.workspace) label {{ color: #ffffff; }}
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
        .tm-grip {{ color: {muted_hex}; margin: 3px; opacity: 0.7; }}
        .tm-grip:hover {{ color: {accent_hex}; opacity: 1; }}
        .tm-undo {{ background: {sel_hex}; color: {sel_fg}; border: 1px solid {accent_hex};
            border-radius: {radius}px; padding: 6px 6px 6px 12px; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6); }}
        .tm-ws-on label {{ color: {accent_hex}; font-weight: 700; }}
        .tm-ws-other label {{ color: {muted_hex}; }}
        .tm-undo button {{ padding: 4px 14px; }}
        .tm-settings {{ padding: 2px 4px 12px 0; }}
        .tm-setting {{ background: alpha({fg_hex}, 0.05); border-radius: {radius}px; padding: 10px 12px; }}
        .tm-setting-title {{ font-size: 14px; font-weight: 600; }}
        .tm-setting.tm-setting-compact {{ padding: 3px 4px 3px 12px; }}
        .tm-setting-compact entry, .tm-setting-compact dropdown button {{ min-height: 0; padding-top: 2px;
            padding-bottom: 2px; }}
        .tm-setting-heading {{ font-size: 13px; font-weight: 700; color: {accent_hex}; margin-top: 6px; }}
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
        .sc-cap {{ font-size: 12px; font-weight: 600; padding: 1px 7px; border-radius: {max(4, radius - 4)}px;
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
        self.favorites = theme_auto.load_favorites()
        self.theme_keys = {shortcuts.theme_of(a): c for a, c in shortcuts.load_state().items()
                           if shortcuts.theme_of(a)}
        ordered = theme_auto.order_themes(self.theme_list, self.favorites, theme_auto.load_recent(),
                                          theme_auto.sort_order())
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
            text = (f"{hidden_out} hidden theme{'s' if hidden_out != 1 else ''} not shown. "
                    "The eye button in the header shows them.")
            if not theme_core.sync_enabled():
                text += " To hide them in Omarchy's own theme picker too, turn it on in Settings (⚙)."
            self.hidden_hint.set_label(text)
        self.no_match.set_visible(self.rows.get_first_child() is None)
        selected = selected or self.rows.get_first_child()
        if selected is not None:
            self.rows.select_row(selected)

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
            theme_core.notify("Could not change favorites", str(error))
        self.refresh_themes(keep=theme.slug)
        self.focus_selected_theme()

    def set_schedule_theme(self, key: str, theme: theme_core.ThemeInfo):
        self.save_schedule(**{key: theme.slug})
        plan = theme_auto.schedule()
        if not plan["enabled"]:
            theme_core.notify(f"{theme_core.plain_text(theme.name)} is the {key} theme",
                              "Switch on “Switch themes by time of day” in Settings (⚙).")
        else:
            theme_core.notify(f"{theme_core.plain_text(theme.name)} is the {key} theme",
                              f"From {plan[key + '_at']}.")

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

    def build_theme_row(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.FlowBoxChild:
        if self.theme_columns > 1:
            return self.build_theme_tile(theme, is_hidden)
        row = Gtk.FlowBoxChild()
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
        name.set_hexpand(True)
        title = Gtk.Box(spacing=6)
        if theme.slug in self.favorites:
            star = Gtk.Label(label="★")
            star.add_css_class("tm-star")
            star.set_tooltip_text("Favorite: listed first and in the bar icon's right-click menu")
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
        key = self.theme_keys.get(theme.slug)
        if key:
            row.set_tooltip_text(f"{shortcuts.combo_label(key)} applies this theme")
            keyhint = Gtk.Label(label=shortcuts.combo_label(key).replace(" + ", "+"), xalign=0)
            keyhint.add_css_class("tm-keyhint")
            keyhint.set_ellipsize(Pango.EllipsizeMode.END)
            keyhint.set_max_width_chars(1)
            text.append(keyhint)

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

    def build_theme_tile(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.FlowBoxChild:
        """A theme as a tile, like a wallpaper: used when the popup is wider
        than the default and the themes are shown in columns."""
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")
        if is_hidden:
            tile.add_css_class("tm-row-hidden")
        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(TILE, TILE * 9 // 16)
        if theme.preview:
            self.decoder.request(picture, theme.preview, TILE, TILE * 9 // 16)
        frame = Gtk.Overlay()
        frame.set_child(picture)
        actions = Gtk.Button(icon_name="open-menu-symbolic")
        actions.add_css_class("flat")
        actions.add_css_class("tm-tile-menu")
        actions.set_halign(Gtk.Align.END)
        actions.set_valign(Gtk.Align.START)
        actions.set_focusable(False)
        actions.set_tooltip_text("Theme actions (Menu key)")
        actions.connect("clicked", lambda button, target=theme: self.show_actions(button, target))
        frame.add_overlay(actions)
        badges = Gtk.Box(spacing=4)
        badges.set_halign(Gtk.Align.START)
        badges.set_valign(Gtk.Align.END)
        for text, kind in (("current", "current") if theme.current else (None, None),
                           ("system", "") if theme.scope == "system" else (None, None),
                           ("hidden", "hidden-badge") if is_hidden else (None, None)):
            if text:
                badge = Gtk.Box()
                badge.add_css_class("tm-badge")
                badge.add_css_class("tm-tile-badge")
                if kind:
                    badge.add_css_class(kind)
                badge.append(Gtk.Label(label=text))
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
            child_tip = f"{shortcuts.combo_label(key)} applies this theme"
            name.set_tooltip_text(child_tip)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(1)
        name.set_hexpand(True)
        caption.set_hexpand(True)
        caption.append(name)
        tile.append(caption)
        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child.tm_theme = theme
        child.actions_button = actions
        return child

    def build_preview(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.Widget:
        frame = Gtk.Box()
        frame.add_css_class("tm-preview")
        frame.set_valign(Gtk.Align.CENTER)
        width, height = self.preview_size()
        if theme.preview:
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
        if self.typing_in(popover):
            return False   # editing text inside the menu (the color chooser's hex field)
        popover.popdown()
        if self.stack.get_visible_child_name() == "wallpapers" and self.menu_anchor is not None:
            self.focus_tile_of(self.menu_anchor)
        else:
            row = self.rows.get_selected_row()
            if row is not None:
                row.grab_focus()
        return True

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
            item("Current theme", lambda: None, "object-select-symbolic", sensitive=False)
        else:
            item("Apply theme", lambda: self.apply_theme_action(theme), "media-playback-start-symbolic")
        item("Wallpapers", lambda: self.open_wallpapers_for(theme), "image-x-generic-symbolic",
             end_icon="go-next-symbolic")
        item("New theme from this…", lambda: self.open_editor(theme), "list-add-symbolic",
             tooltip="Your own copy with other colors, a font and these wallpapers")
        if theme_custom.is_custom(theme):
            item("Edit theme…", lambda: self.open_editor(theme, edit=True), "document-edit-symbolic",
                 tooltip="Name, colors and font")
        key = shortcuts.load_state().get(shortcuts.APPLY + theme.slug)
        hidden = theme.slug in self.hidden
        label = f"Shortcut: {shortcuts.combo_label(key)}…" if key else "Shortcut…"
        if key and hidden:
            label = f"Shortcut: {shortcuts.combo_label(key)} (works while hidden)…"
        item(label, lambda: self.open_shortcut_page(shortcuts.APPLY + theme.slug), "input-keyboard-symbolic",
             tooltip="A key that applies this theme from anywhere"
             + (". It also works while the theme is hidden, and applying it unhides it." if hidden else ""))
        if theme.slug in self.favorites:
            item("Remove from favorites", lambda: self.set_favorite(theme, False), "starred-symbolic")
        else:
            item("Add to favorites", lambda: self.set_favorite(theme, True), "non-starred-symbolic",
                 tooltip="Listed first, and in the bar icon's right-click menu")
        plan = theme_auto.schedule()
        for key, label in (("day", "day"), ("night", "night")):
            if plan[key] == theme.slug:
                item(f"This is the {label} theme", lambda: None, "object-select-symbolic",
                     sensitive=False)
                break
        else:
            item("Use as day theme", lambda: self.set_schedule_theme("day", theme), "weather-clear-symbolic",
                 tooltip="Applied at the day time set in Settings (⚙)")
            item("Use as night theme", lambda: self.set_schedule_theme("night", theme),
                 "weather-clear-night-symbolic", tooltip="Applied at the night time set in Settings (⚙)")
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
        while row is not None and not isinstance(row, Gtk.FlowBoxChild):
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
        self.drop_note = ("Stored in your wallpaper folder for this theme, next to its own wallpapers."
                          if system else "Stored in the theme's user wallpaper folder.")
        self.refresh_wallpapers()
        self.stack.set_visible_child_name("wallpapers")

    def show_themes(self):
        self.stop_preview()
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
        self.stop_preview()
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
        entry("Preview on desktop", lambda: self.preview_web(item, PREVIEW_SECONDS), "view-reveal-symbolic",
              tooltip=f"Loads the full image and shows it for {PREVIEW_SECONDS} seconds; or hold Space on the tile")
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
        self._async(lambda: theme_core.apply_theme(theme.slug),
                    lambda ok, error: self.after_apply(theme, ok, error))

    def after_apply(self, theme: theme_core.ThemeInfo, ok: bool, error: Exception | None):
        if ok:
            theme_core.notify("Theme applied", theme.name)
            try:
                theme_auto.record_recent(theme.slug)
            except OSError:
                pass
            self._async(lambda: theme_custom.apply_theme_font(theme.slug),
                        lambda font, _e: font and theme_core.notify("Font", f"Now using {font}"))
        else:
            theme_core.notify("Could not apply theme",
                              f"{theme.name}: {error or 'omarchy theme set failed'}")
        # The popup stays open: take on the new theme's colors and mark it current.
        self.current_slug = theme_core.current_theme()
        if self.theme.reload():
            self.apply_theme_css()
        self.hidden = self.load_hidden_list()
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
        if "theme.name" not in names:
            return
        if self.theme_sync:
            GLib.source_remove(self.theme_sync)
        self.theme_sync = GLib.timeout_add(300, self.follow_theme_change)

    def follow_theme_change(self):
        self.theme_sync = 0
        slug = theme_core.current_theme()
        if not slug or slug == getattr(self, "current_slug", None):
            return GLib.SOURCE_REMOVE
        self.current_slug = slug
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
        message = f"Delete theme “{theme_core.plain_text(theme.name)}”?"
        notes = []
        if (theme_core.default_roots().system_themes / theme.slug).is_dir():
            notes.append("Only your copy is removed; the system version stays.")
        notes.append("Wallpapers you added to it are kept.")
        if theme_core.default_roots().trash is not None:
            notes.append("The theme goes to the trash: Undo brings it back.")
        self.ask_confirm(message + "\n\n" + "\n".join(notes), "Delete theme",
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
            theme_core.notify("Theme removed", theme.name)
            if receipts:
                self.offer_undo(f"“{theme_core.plain_text(theme.name)}” moved to the trash", list(receipts))
            else:
                self.prune_shortcuts()   # no undo: its key goes right away
        else:
            theme_core.notify("Could not delete theme", f"{theme.name}: {error}")
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
            self.wp_grid.append(self._empty_tile(f"No wallpaper matches “{self.wp_filter}”"))
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
        inner.set_size_request(TILE, TILE * 9 // 16)
        inner.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        icon.set_pixel_size(32)
        icon.set_vexpand(True)
        icon.set_valign(Gtk.Align.END)
        inner.append(icon)
        hint = Gtk.Label(label=("No wallpapers yet. " if empty else "") + "Drop images here, paste with "
                         "Ctrl+V or click to choose", wrap=True, max_width_chars=1,
                         justify=Gtk.Justification.CENTER)
        hint.add_css_class("tm-tile-label")
        hint.set_vexpand(True)
        hint.set_valign(Gtk.Align.START)
        inner.append(hint)
        tile.append(inner)
        caption = Gtk.Label(label="Add wallpapers…", xalign=0.5)
        caption.add_css_class("tm-tile-label")
        tile.append(caption)
        child = Gtk.FlowBoxChild()
        child.set_child(tile)
        child.set_tooltip_text("Choose image files (Enter), drag them anywhere onto this page, or paste "
                               "an image or file with Ctrl+V. HEIC photos are converted. " + self.drop_note)
        child.tm_add = True
        self.add_tile = tile
        return child

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

    def build_wallpaper_tile(self, wallpaper: theme_core.WallpaperInfo,
                             workspaces: list[int] = ()) -> Gtk.Widget:
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
        if workspaces:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("tm-tile-badge")
            badge.add_css_class("workspace")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.START)
            badge.append(Gtk.Label(label="ws " + " · ".join(map(str, workspaces))))
            badge.set_tooltip_text("Shown on workspace " + ", ".join(map(str, workspaces)))
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
        if not wallpaper.hidden:
            item("Preview on desktop", lambda: self.start_preview(wallpaper.path, PREVIEW_SECONDS),
                 "view-reveal-symbolic",
                 tooltip=f"Shows it for {PREVIEW_SECONDS} seconds; or hold Space on the tile")
        if theme is not None and not wallpaper.hidden:
            item("New theme from this wallpaper…", lambda: self.open_editor(theme, wallpaper=wallpaper),
                 "applications-graphics-symbolic",
                 tooltip="A theme in this picture's colors, with this wallpaper")
            on = theme_auto.workspaces_of(theme.slug, wallpaper.name)
            item("Show on workspace…" if not on else "On workspace " + ", ".join(map(str, on)) + "…",
                 lambda: self.show_workspace_menu(wallpaper), "view-grid-symbolic", end_icon="go-next-symbolic",
                 tooltip="This wallpaper appears whenever you switch to that workspace"
                 + ("" if theme.current else " (while this theme is applied)"))
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
            button.set_tooltip_text(f"Workspace {ws}: " + ("shows this wallpaper (click to clear)" if mine else
                                    f"shows {mapping[ws]} (click to use this one)" if other else
                                    "use this wallpaper"))
            self.focus_follows_pointer(button)
            button.connect("clicked", lambda _b, w=ws, m=mine: (
                popover.popdown(), self.assign_workspace(wallpaper, w, not m)))
            grid.attach(button, (ws - 1) % 5, (ws - 1) // 5, 1, 1)
        title = Gtk.Label(label="Show on workspace", xalign=0)
        title.add_css_class("tm-hint")
        title.set_margin_start(8)
        box.append(title)
        box.append(grid)
        if any(n == wallpaper.name for n in mapping.values()):
            self.menu_separator(box)
            self.menu_item(box, popover, "Not on any workspace",
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
                theme_core.notify("Wallpapers per workspace are on",
                                  "Switch it off again in Settings (⚙).")
        except (OSError, ValueError) as error:
            theme_core.notify("Could not change the workspace wallpaper", str(error))
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
        note = "\n\nIt goes to the trash: Undo brings it back." \
            if theme_core.default_roots().trash is not None else ""
        self.ask_confirm(f"Delete wallpaper “{theme_core.plain_text(wallpaper.name)}” from "
                         f"“{theme_core.plain_text(theme.name) if theme else ''}”?{note}",
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
            theme_core.notify("Wallpaper deleted", wallpaper.name)
            self.offer_undo(f"“{theme_core.plain_text(wallpaper.name)}” moved to the trash", list(receipts))
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
        if page == "shortcut":
            return self.on_shortcut_key(_controller, keyval, _keycode, state)
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
            elif page == "confirm":
                self.cancel_confirm()
            elif page == "websearch":
                self.close_web_search()
            elif page == "wallpapers" and self.wp_filter:
                self.set_wp_filter("")
            elif page == "editor":
                self.close_editor()
            elif page in ("wallpapers", "import", "settings"):
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
        if page == "settings":
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
        self.hidden = self.load_hidden_list()
        self.filter_query = ""
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
        # Seen here rather than by a click gesture, which the drag can win:
        # a second press within the double-click time resets the popup.
        now = time.monotonic()
        if now - getattr(self, "last_header_press", 0.0) < 0.4:
            self.last_header_press = 0.0
            self.moving = False
            self.reset_window()
            return
        self.last_header_press = now
        self.moving = True
        self.move_start = (self.current_position(), theme_core.cursor_position())

    def reset_window(self):
        """Default size, centered (double-click on the title bar)."""
        self.resize_to(CARD_WIDTH, LIST_HEIGHT, save=True)
        self.move_to(None, save=True)

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
        self.stop_preview()
        if self.stack.get_visible_child_name() == "shortcut":
            self.close_shortcut_page()
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
