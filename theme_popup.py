#!/usr/bin/env python3
"""Theme Manager popup: a Wayland layer-shell window over every Omarchy
theme (user and system).

List themes, apply one, hide it from the list (eye toggle unhides), delete
user themes, and manage each theme's wallpapers: add images by drag & drop or
by pasting an image copied from the web, remove the ones no longer wanted.
All file work goes through theme_core, which enforces the trust rules.
"""

import os
import re
import signal
import subprocess
import sys
import threading
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
from gi.repository import Gdk, GLib, GObject, Gtk, Gtk4LayerShell, Pango

# Once Gtk4LayerShell is loaded into this process by ld.so, remove it from
# LD_PRELOAD so child processes do not inherit it.
_preloads = [p for p in os.environ.get("LD_PRELOAD", "").split(":")
             if p and p != layer_shell_library]
if _preloads:
    os.environ["LD_PRELOAD"] = ":".join(_preloads)
else:
    os.environ.pop("LD_PRELOAD", None)

import atexit

import theme_core
from palette import ThemePalette
import pidfile

CARD_WIDTH = 440
LIST_HEIGHT = 560
TILE = 104
MAX_DROPS = 10  # one drop gesture adds at most this many wallpapers


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
    try:
        result = subprocess.run(["hyprctl", "getoption", "decoration:rounding", "-j"],
                                capture_output=True, text=True, timeout=2)
        return max(0, int(re.search(r'"value":\s*([0-9]+)', result.stdout).group(1)))
    except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
        return 12


def parse_uris(text: str) -> list[Path]:
    """File paths from a `text/uri-list` drop. Non-file URIs are dropped;
    nothing here is ever executed."""
    paths: list[Path] = []
    for token in re.split(r"[\s\n]+", text or ""):
        if not token:
            continue
        if token.startswith("file://"):
            token = urllib.parse.unquote(urllib.parse.urlparse(token).path)
        else:
            continue  # http(s) and anything else: not a local file
        candidate = Path(token)
        if candidate.is_file():
            paths.append(candidate)
    return paths


class ThemeManager(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.omarchy.thememanager")
        self.window = None
        self.theme = ThemePalette()
        self.hidden = theme_core.load_hidden()
        self.show_hidden = False
        self.active_theme = None   # ThemeInfo while the wallpaper view is open
        self.style_provider = None
        self.theme_list = []       # latest discover_themes() result

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
        for edge in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM,
                     Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT):
            Gtk4LayerShell.set_anchor(window, edge, True)
        Gtk4LayerShell.set_exclusive_zone(window, 0)

        overlay = Gtk.Overlay()
        overlay.add_css_class("tm-overlay")
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        overlay.set_halign(Gtk.Align.FILL)
        overlay.set_valign(Gtk.Align.FILL)
        window.set_child(overlay)
        overlay.set_child(Gtk.Box())
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.close())  # Close on release so the bar never sees a half-finished click.
        overlay.add_controller(click)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("tm-card")
        card.set_size_request(CARD_WIDTH, -1)
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        card.set_margin_top(24)
        card.set_margin_bottom(24)
        overlay.add_overlay(card)

        header = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Theme Manager")
        title.add_css_class("tm-title")
        title.set_hexpand(True)
        title.set_halign(Gtk.Align.START)
        header.append(title)
        self.hidden_button = Gtk.Button(icon_name="view-reveal-symbolic")
        self.hidden_button.set_tooltip_text("Show hidden themes")
        self.hidden_button.add_css_class("flat")
        self.hidden_button.add_css_class("tm-header-btn")
        self.hidden_button.connect("clicked", lambda *_: self.toggle_show_hidden())
        header.append(self.hidden_button)
        card.append(header)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_transition_duration(140)
        stack.set_vhomogeneous(False)
        stack.set_interpolate_size(False)
        card.append(stack)

        # --- themes page
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        scroller.set_min_content_height(LIST_HEIGHT)
        scroller.set_max_content_height(LIST_HEIGHT)
        self.rows = Gtk.ListBox()
        self.rows.set_selection_mode(Gtk.SelectionMode.NONE)
        self.rows.add_css_class("tm-list")
        self.rows.connect("row-activated", lambda *_: self.open_wallpapers_for(self.rows.get_selected_row().tm_theme))
        scroller.set_child(self.rows)
        stack.add_named(scroller, "themes")

        # --- wallpapers page
        wallpaper_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wallpaper_page.set_margin_start(8)
        wallpaper_page.set_margin_end(8)

        wp_header = Gtk.Box(spacing=8)
        back_button = Gtk.Button()
        back_button.add_css_class("flat")
        back_button.add_css_class("tm-header-btn")
        back_box = Gtk.Box(spacing=6)
        back_box.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        back_box.append(Gtk.Label(label="Back"))
        back_button.set_child(back_box)
        back_button.set_tooltip_text("Back to themes (Esc)")
        back_button.connect("clicked", lambda *_: self.show_themes())
        wp_header.append(back_button)
        self.wp_title = Gtk.Label(label="Wallpapers", xalign=0)
        self.wp_title.add_css_class("tm-title")
        self.wp_title.set_hexpand(True)
        wp_header.append(self.wp_title)
        wallpaper_page.append(wp_header)

        self.wp_toolbar = Gtk.Box(spacing=8)
        self.paste_button = Gtk.Button()
        self.paste_button.add_css_class("tm-paste")
        paste_box = Gtk.Box(spacing=6)
        paste_box.append(Gtk.Image.new_from_icon_name("edit-paste-symbolic"))
        paste_box.append(Gtk.Label(label="Paste image (Ctrl+V)"))
        self.paste_button.set_child(paste_box)
        self.paste_button.set_tooltip_text("Add the image copied to the clipboard (e.g. copied from the web)")
        self.paste_button.connect("clicked", lambda *_: self.paste_clipboard())
        self.wp_toolbar.append(self.paste_button)
        drop_hint = Gtk.Label(xalign=0)
        drop_hint.set_hexpand(True)
        drop_hint.add_css_class("tm-hint")
        wallpaper_page.append(self.wp_toolbar)
        self.drop_hint = drop_hint

        wp_scroller = Gtk.ScrolledWindow()
        wp_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        wp_scroller.set_min_content_height(LIST_HEIGHT - 70)
        wp_scroller.set_max_content_height(LIST_HEIGHT - 70)
        self.wp_grid = Gtk.FlowBox()
        self.wp_grid.add_css_class("tm-grid")
        self.wp_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.wp_grid.set_homogeneous(True)
        self.wp_grid.set_column_spacing(8)
        self.wp_grid.set_row_spacing(8)
        self.wp_grid.set_max_children_per_line(3)
        wp_scroller.set_child(self.wp_grid)
        wallpaper_page.append(wp_scroller)
        stack.add_named(wallpaper_page, "wallpapers")

        # Drop images anywhere on the wallpaper page. GTK4 delivers
        # `text/uri-list` as a string payload (newline-separated file URIs).
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        wallpaper_page.add_controller(drop)

        keys = Gtk.EventControllerKey()
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
        window#thememanager-window, .tm-overlay {{ background: transparent; }}
        .tm-card {{ background: {bg_hex}; color: {fg_hex};
            border: 1px solid {accent_hex}; border-radius: {radius}px; padding: 16px; }}
        .tm-title {{ font-size: 15px; font-weight: 600; }}
        .tm-hint {{ color: {muted_hex}; font-size: 12px; }}
        .tm-header-btn {{ padding: 4px; min-width: 0; min-height: 0; }}
        .tm-list {{ background: transparent; }}
        .tm-list row {{ border-radius: {radius - 4}px; padding: 5px 4px 5px 8px; min-height: 46px; }}
        .tm-list row:selected {{ background: {sel_hex}; color: {sel_fg}; }}
        .tm-row-hidden {{ opacity: 0.75; }}
        .tm-badge {{ background: rgba(255, 255, 255, 0.08); border-radius: {max(4, radius - 6)}px;
            padding: 1px 7px; }}
        .tm-badge label {{ font-size: 10px; font-weight: 600; color: {muted_hex}; }}
        .tm-badge.current {{ background: {accent_hex}; }}
        .tm-badge.current label {{ color: {bg_hex}; }}
        .tm-badge.danger {{ color: {red_hex}; }}
        button.tm-menu-btn {{ padding: 4px; min-width: 0; min-height: 0; }}
        .tm-grid {{ background: transparent; }}
        .tm-tile {{ background: {sel_hex}; border-radius: {radius - 4}px; }}
        .tm-tile-label {{ font-size: 11px; color: {muted_hex}; }}
        button.tm-tile-delete {{ padding: 2px; min-width: 0; min-height: 0;
            background: rgba(0, 0, 0, 0.55); border-radius: 10px; }}
        button.tm-tile-delete:hover {{ background: {red_hex}; }}
        .tm-paste {{ font-weight: 600; }}
        .tm-menu-item {{ padding: 6px 10px; border-radius: {max(4, radius - 6)}px; font-size: 13px; }}
        .tm-menu-item:hover {{ background: {sel_hex}; color: {sel_fg}; }}
        .tm-menu-item.danger {{ color: {red_hex}; }}
        .tm-menu-item:disabled {{ opacity: 0.45; }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.style_provider = provider

    # --- theme list -----------------------------------------------------------------

    def refresh_themes(self):
        self.theme_list = theme_core.discover_themes()
        first = self.rows.get_first_child()
        while first is not None:
            self.rows.remove(first)
            first = self.rows.get_first_child()
        for theme in self.theme_list:
            is_hidden = theme.slug in self.hidden
            if is_hidden and not self.show_hidden:
                continue
            self.rows.append(self.build_theme_row(theme, is_hidden))

    def build_theme_row(self, theme: theme_core.ThemeInfo, is_hidden: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.tm_theme = theme
        if is_hidden:
            row.add_css_class("tm-row-hidden")
        line = Gtk.Box(spacing=8)

        if theme.preview:
            thumb = Gtk.Image.new_from_file(theme.preview)
        else:
            thumb = Gtk.Image.new_from_icon_name("applications-graphics")
        thumb.set_pixel_size(36)
        thumb.set_valign(Gtk.Align.CENTER)
        if is_hidden:
            thumb.add_css_class("tm-row-hidden")
        line.append(thumb)

        name = Gtk.Label(label=theme_core.plain_text(theme.name), xalign=0)
        name.add_css_class("tm-row-name")
        name.set_hexpand(True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        line.append(name)

        if theme.current:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.add_css_class("current")
            badge.append(Gtk.Label(label="current"))
            line.append(badge)
        if theme.scope == "system":
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.append(Gtk.Label(label="system"))
            line.append(badge)
        if is_hidden:
            badge = Gtk.Box()
            badge.add_css_class("tm-badge")
            badge.append(Gtk.Label(label="hidden"))
            line.append(badge)

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("view-more-symbolic")
        menu_button.add_css_class("flat")
        menu_button.add_css_class("tm-menu-btn")
        menu_button.set_tooltip_text("Theme actions")
        menu_button.set_popover(self.build_theme_menu(theme))
        line.append(menu_button)

        row.set_child(line)
        return row

    def build_theme_menu(self, theme: theme_core.ThemeInfo) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        def item(label, handler, danger=False, disabled=False):
            button = Gtk.Button(label=label)
            button.set_can_focus(False)
            button.add_css_class("flat")
            button.add_css_class("tm-menu-item")
            if danger:
                button.add_css_class("danger")
            button.connect("clicked", lambda *_: (popover.popdown(), handler()))
            button.set_sensitive(not disabled)
            box.append(button)
            return button

        if theme.current:
            item("Current theme", lambda: None, disabled=True)
        else:
            item("Apply theme", lambda: self.apply_theme_action(theme))
        item("Wallpapers…", lambda: self.open_wallpapers_for(theme))

        if theme.slug in self.hidden:
            item("Show in list", lambda: self.set_theme_hidden(theme, False))
        else:
            item("Hide from list", lambda: self.set_theme_hidden(theme, True))

        box.append(Gtk.Separator())
        if theme.scope == "user":
            item("Delete theme…", lambda: self.confirm_delete_theme(theme), danger=True)
        else:
            button = item("System theme — read-only", lambda: None, disabled=True)
            button.set_tooltip_text("System themes are owned by root and cannot be deleted; hide them instead")
        popover.set_child(box)
        return popover

    # --- list actions ----------------------------------------------------------------

    def toggle_show_hidden(self, *_):
        self.show_hidden = not self.show_hidden
        if self.show_hidden:
            self.hidden_button.set_icon_name("view-conceal-symbolic")
            self.hidden_button.set_tooltip_text("Hide hidden themes")
        else:
            self.hidden_button.set_icon_name("view-reveal-symbolic")
            self.hidden_button.set_tooltip_text("Show hidden themes")
        self.refresh_themes()

    def set_theme_hidden(self, theme: theme_core.ThemeInfo, hide: bool):
        if hide:
            self.hidden.add(theme.slug)
        else:
            self.hidden.discard(theme.slug)
        theme_core.save_hidden(self.hidden)
        if hide and self.show_hidden:
            pass  # keep the eye on so the row stays visible, now dimmed
        self.refresh_themes()

    def open_wallpapers_for(self, theme: theme_core.ThemeInfo):
        if theme is None:
            return
        self.active_theme = theme
        self.wp_title.set_label(f"{theme_core.plain_text(theme.name)} — wallpapers")
        system = theme.scope == "system"
        self.paste_button.set_tooltip_text(
            "Add the image copied to the clipboard (e.g. copied from the web)"
            + (" — stored in your per-theme folder" if system else ""))
        self.drop_hint.set_label(
            "Drop image files here, or paste one copied from the web. "
            + ("Stored in the user wallpaper folder, next to the theme's own wallpapers."
               if system else
               "Stored in the theme's user wallpaper folder."))
        self.refresh_wallpapers()
        self.stack.set_visible_child_name("wallpapers")

    def show_themes(self):
        self.active_theme = None
        self.stack.set_visible_child_name("themes")
        self.refresh_themes()

    def apply_theme_action(self, theme: theme_core.ThemeInfo):
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
        warnings = []
        if theme.current:
            warnings.append("This is the theme you are using right now.")
        if theme.slug in {t.slug for t in self.theme_list if t.scope == "system"}:
            pass  # a system copy with the same slug remains
        elif theme.scope == "user":
            # A system theme with the same slug (e.g. a re-skin) will remain.
            try:
                system_copy = (theme_core.default_roots().system_themes / theme.slug)
                if system_copy.is_dir():
                    warnings.append(f"Only your copy is removed; the system version “{theme_core.display_name(theme.slug)}” stays.")
            except OSError:
                pass
        message = f"Delete theme “{theme.name}” and its user wallpapers?"
        if warnings:
            message += "\n\n" + "\n".join(warnings)

        dialog = Gtk.Window(title="Delete theme")
        dialog.set_transient_for(self.window)
        dialog.set_modal(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        label = Gtk.Label(label=message, xalign=0, wrap=True)
        label.add_css_class("tm-title")
        content.append(label)
        answers = Gtk.Box(spacing=8)
        answers.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("tm-paste")
        delete = Gtk.Button(label="Delete")
        delete.add_css_class("tm-paste")
        delete.add_css_class("danger")
        answers.append(cancel)
        answers.append(delete)
        content.append(answers)
        dialog.set_child(content)

        cancel.connect("clicked", lambda *_: dialog.close())
        delete.connect("clicked", lambda *_: (dialog.close(),
                                              self.perform_delete_theme(theme)))
        dialog.set_default_size(400, -1)
        dialog.present()

    def perform_delete_theme(self, theme: theme_core.ThemeInfo):
        """The confirmed step: actually remove the theme (off-thread)."""
        self.close()
        self._async(lambda: theme_core.remove_theme(None, theme.slug),
                    lambda _path, error: self.after_delete_theme(theme, error))

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
        wallpapers = theme_core.list_backgrounds(None, theme)
        if not wallpapers:
            self.wp_grid.append(self._empty_tile())
            return
        for wallpaper in wallpapers:
            self.wp_grid.append(self.build_wallpaper_tile(wallpaper))

    def _empty_tile(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("tm-tile")
        box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("image-missing")
        icon.set_pixel_size(40)
        box.append(icon)
        box.append(Gtk.Label(label="No wallpapers yet — drop or paste one"))
        box.set_halign(Gtk.Align.CENTER)
        return box

    def build_wallpaper_tile(self, wallpaper: theme_core.WallpaperInfo) -> Gtk.Box:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.add_css_class("tm-tile")

        picture = Gtk.Picture.new_for_filename(str(wallpaper.path))
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(TILE, TILE)
        picture.set_valign(Gtk.Align.CENTER)

        frame = Gtk.Overlay()
        frame.set_child(picture)
        if wallpaper.deletable:
            delete = Gtk.Button(icon_name="window-close-symbolic")
            delete.add_css_class("flat")
            delete.add_css_class("tm-tile-delete")
            delete.set_halign(Gtk.Align.END)
            delete.set_valign(Gtk.Align.START)
            delete.set_tooltip_text(f"Delete {wallpaper.name}")
            delete.connect("clicked", lambda *_, w=wallpaper: self.confirm_delete_wallpaper(w))
            frame.add_overlay(delete)
        else:
            picture.set_tooltip_text("Ships with the system theme (read-only)")
        tile.append(frame)

        caption = Gtk.Label(label=wallpaper.name, xalign=0.5)
        caption.add_css_class("tm-tile-label")
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        tile.append(caption)
        return tile

    def confirm_delete_wallpaper(self, wallpaper: theme_core.WallpaperInfo):
        theme = self.active_theme
        dialog = Gtk.Window(title="Delete wallpaper")
        dialog.set_transient_for(self.window)
        dialog.set_modal(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        label = Gtk.Label(label=f"Delete wallpaper “{wallpaper.name}” from "
                                f"“{theme_core.plain_text(theme.name) if theme else ''}”?",
                          xalign=0, wrap=True)
        label.add_css_class("tm-title")
        content.append(label)
        answers = Gtk.Box(spacing=8)
        answers.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("tm-paste")
        delete = Gtk.Button(label="Delete")
        delete.add_css_class("tm-paste")
        delete.add_css_class("danger")
        answers.append(cancel)
        answers.append(delete)
        content.append(answers)
        dialog.set_child(content)

        cancel.connect("clicked", lambda *_: dialog.close())
        delete.connect("clicked", lambda *_: (dialog.close(),
                                              self.perform_delete_wallpaper(wallpaper)))
        dialog.set_default_size(380, -1)
        dialog.present()

    def perform_delete_wallpaper(self, wallpaper: theme_core.WallpaperInfo):
        """The confirmed step: actually remove the wallpaper (off-thread)."""
        self._async(lambda: theme_core.remove_wallpaper(None, wallpaper.path),
                    lambda _path, error: self.after_delete_wallpaper(wallpaper, error))

    def after_delete_wallpaper(self, wallpaper: theme_core.WallpaperInfo,
                               error: Exception | None):
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
            theme_core.notify("No image in the clipboard",
                              "Copy an image first (e.g. right-click → Copy image in the browser).")
            return
        mime, data = found
        ext = Path(mime.split("/", 1)[-1]).suffix or ".png"
        try:
            dest = theme_core.add_background_bytes(None, theme.slug, data,
                                                   name=f"pasted-image{ext}")
            theme_core.notify("Wallpaper added",
                              f"{dest.name} → {theme_core.plain_text(theme.name)}")
        except (ValueError, OSError) as add_error:
            theme_core.notify("Could not add wallpaper", str(add_error))
            return
        self.refresh_wallpapers()

    def on_drop(self, _drop_target, value, _x, _y):
        theme = self.active_theme
        if theme is None:
            return
        paths = parse_uris(value.get_string() or "")[:MAX_DROPS]
        if not paths:
            theme_core.notify("Nothing added", "Drop local image files (JPG, PNG, WebP, GIF, BMP).")
            return
        added = []
        errors = []
        for path in paths:
            try:
                dest = theme_core.add_background_file(None, theme.slug, path)
                added.append(dest.name)
            except (ValueError, OSError) as error:
                errors.append(f"{path.name}: {error}")
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
            GLib.idle_add(on_done, result, error)
        threading.Thread(target=worker, daemon=True).start()

    def on_key(self, _controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            if self.stack.get_visible_child_name() == "wallpapers":
                self.show_themes()
            else:
                self.close()
            return True
        # Ctrl+V pastes when the wallpaper page is open.
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and \
                (state & Gdk.ModifierType.CONTROL_MASK):
            if self.stack.get_visible_child_name() == "wallpapers":
                self.paste_clipboard()
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
        self.hidden = theme_core.load_hidden()
        self.refresh_themes()
        self.window.present()

    def close(self):
        self.window.hide()


def main():
    app = ThemeManager()
    atexit.register(cleanup_pid)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
