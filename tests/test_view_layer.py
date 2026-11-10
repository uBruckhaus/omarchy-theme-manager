#!/usr/bin/env python3
"""Functional tests of the theme_popup view layer.

Builds the real GTK window (never presented) and drives the same handler
methods the UI would, asserting on widget state and filesystem effects.
All core state is isolated in a temp dir by patching theme_core's
default_roots()/hidden_file(); theme mutations that would touch the real
system (apply_theme, clipboard) are stubbed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import theme_core  # noqa: E402
import theme_popup  # noqa: E402
import i18n  # noqa: E402

i18n._lang = "en"  # the assertions below read English texts

from images import gif, jpeg, png

PNG = png()


class ViewLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.roots = theme_core.Roots(
            user_themes=root / "themes-user",
            system_themes=root / "themes-system",
            user_backgrounds=root / "backgrounds",
            state_current=root / "state",
        )
        for d in (self.roots.user_themes, self.roots.system_themes,
                  self.roots.user_backgrounds):
            d.mkdir(parents=True)
        self.hidden_file = root / "hidden.json"
        self.state_current = root / "state" / "theme.name"

        # --- isolate core state ------------------------------------------
        self._orig_roots = theme_core.default_roots
        self._orig_hidden = theme_core.hidden_file
        self._orig_apply = theme_core.apply_theme
        self._orig_notify = theme_core.notify
        self._orig_clipboard = theme_core.clipboard_image
        self._orig_clipboard_files = theme_core.clipboard_files
        self._orig_cursor = theme_core.cursor_position
        theme_core.cursor_position = lambda: None
        theme_core.clipboard_files = lambda **kw: []
        theme_core.default_roots = lambda: self.roots
        theme_core.hidden_file = lambda: self.hidden_file
        self.applied: list[str] = []
        def fake_apply(slug, binary="omarchy", timeout=120.0):
            # as `omarchy theme set`: the theme is in use afterwards
            self.applied.append(slug)
            self.state_current.parent.mkdir(parents=True, exist_ok=True)
            self.state_current.write_text(slug + "\n")
            return True
        theme_core.apply_theme = fake_apply
        self.notices: list[tuple[str, str]] = []
        theme_core.notify = lambda title, body="": self.notices.append((title, body))
        # Omarchy picker sync: never touch the real cache or menu file.
        self._orig_switcher = (theme_core.switcher_cache_dir,
                               theme_core.menu_extension_file,
                               theme_core.preload_switcher)
        self.switcher_dir = root / "theme-selector"
        (self.switcher_dir / "previews").mkdir(parents=True)
        theme_core.switcher_cache_dir = lambda: self.switcher_dir
        theme_core.menu_extension_file = lambda: root / "omarchy-menu.jsonc"
        self.preloads: list[str] = []
        theme_core.preload_switcher = lambda wrapper: self.preloads.append(str(wrapper))
        self._orig_settings = theme_core.settings_file
        theme_core.settings_file = lambda: root / "settings.json"
        theme_core.save_settings(True)   # most tests cover the Omarchy sync; one turns it off
        self._orig_refresh = theme_core.refresh_switcher_cache
        self.refreshes: list[str] = []
        theme_core.refresh_switcher_cache = lambda wrapper, timeout=60.0: self.refreshes.append(str(wrapper)) or True
        # Hidden wallpapers, the theme-set hook and the desktop background.
        self._orig_wallpaper = (theme_core.hidden_wallpapers_file,
                                theme_core.theme_set_hook_file,
                                theme_core.set_desktop_background)
        theme_core.hidden_wallpapers_file = lambda: root / "hidden-wallpapers.json"
        theme_core.theme_set_hook_file = lambda: root / "hooks" / "90-theme-manager"
        self.backgrounds_set: list[str] = []
        theme_core.set_desktop_background = lambda path: self.backgrounds_set.append(str(path)) or True
        # The omarchy.org gallery: no network, no browser.
        import theme_catalog
        self.catalog_mod = theme_catalog
        self._orig_catalog = (theme_catalog.load_catalog, theme_catalog.fetch_preview,
                              theme_catalog.default_browser, theme_catalog.install,
                              theme_catalog.open_in_browser, theme_catalog.cache_dir)
        self.catalog_entries = [
            theme_catalog.CatalogTheme("Ash", "https://github.com/a/omarchy-ash-theme", "ash", "ash.webp"),
            theme_catalog.CatalogTheme("Alpha", "https://github.com/a/omarchy-alpha-theme", "alpha", "alpha.webp"),
            theme_catalog.CatalogTheme("Frost", "https://github.com/a/omarchy-frost-theme", "frost", "frost.webp")]
        theme_catalog.load_catalog = lambda force=False: (self.catalog_entries, 0.0)
        theme_catalog.fetch_preview = lambda entry: None
        self.browser = "google-chrome.desktop"
        theme_catalog.default_browser = lambda: self.browser
        self.installed: list[str] = []
        def fake_install(entry, roots=None):
            (self.roots.user_themes / entry.slug).mkdir()
            self.installed.append(entry.slug)
            return self.roots.user_themes / entry.slug
        theme_catalog.install = fake_install
        self.opened: list[str] = []
        theme_catalog.open_in_browser = lambda url: self.opened.append(url) or True
        theme_catalog.cache_dir = lambda: root / "cache"
        import wallpaper_search
        self.ws = wallpaper_search
        self._orig_ws = (wallpaper_search.search, wallpaper_search.fetch_thumb,
                         wallpaper_search.download, wallpaper_search.open_page)
        self.web_results = wallpaper_search.parse_results(json.dumps({"data": [
            {"id": wid, "url": f"https://wallhaven.cc/w/{wid}",
             "path": f"https://w.wallhaven.cc/full/{wid[:2]}/wallhaven-{wid}.jpg",
             "thumbs": {"large": f"https://th.wallhaven.cc/lg/{wid[:2]}/{wid}.jpg"},
             "resolution": "2560x1440", "file_size": 10, "purity": "sfw"}
            for wid in ("aa1111", "bb2222", "cc3333")], "meta": {"last_page": 2}}).encode())[0]
        self.searches: list[tuple[str, int]] = []
        def fake_search(query, page=1):
            self.searches.append((query, page))
            return (self.web_results if page == 1 else []), 2
        wallpaper_search.search = fake_search
        wallpaper_search.fetch_thumb = lambda item: None
        def fake_download(roots, slug, item):
            folder = self.roots.user_backgrounds / slug
            folder.mkdir(parents=True, exist_ok=True)
            (folder / item.file_name).write_bytes(PNG)
            return folder / item.file_name
        wallpaper_search.download = fake_download
        wallpaper_search.open_page = lambda item: self.opened.append(item.page) or True

        # Desktop previews, the clipboard and workspace automation: recorded, never real.
        import theme_auto
        self.auto = theme_auto
        self._orig_extra = (theme_core.show_background, theme_core.restore_background,
                            theme_auto.state_file, theme_auto.active_workspace)
        self.previews: list[str] = []
        theme_core.show_background = lambda path: self.previews.append(Path(path).name) or True
        theme_core.restore_background = lambda roots=None: self.previews.append("<restore>") or True
        theme_auto.state_file = lambda: root / "cache" / "auto-state.json"
        self.active_ws = 1
        theme_auto.active_workspace = lambda: self.active_ws
        import theme_custom
        self.custom = theme_custom
        self._orig_fonts = (theme_custom.list_fonts, theme_custom.current_font, theme_custom.set_font)
        self.font = "Mono A"
        self.fonts_set: list[str] = []
        theme_custom.list_fonts = lambda: ["Mono A", "Mono B"]
        theme_custom.current_font = lambda: self.font

        def set_font(font):
            self.fonts_set.append(font)
            self.font = font
            return True
        theme_custom.set_font = set_font
        # Omarchy's window style: recorded, never the real command.
        import theme_looks
        self.looks = theme_looks
        self._orig_looks = (theme_looks.windows_supported, theme_looks.current_windows, theme_looks.set_windows)
        self.windows = "transparent"
        theme_looks.windows_supported = lambda: True
        theme_looks.current_windows = lambda: self.windows
        theme_looks.set_windows = lambda mode: setattr(self, "windows", mode) or True
        # Keyboard shortcuts: a private ~/.config/hypr, no real hyprctl.
        import theme_shortcuts
        self.sc = theme_shortcuts
        self._orig_sc = {name: getattr(theme_shortcuts, name) for name in
                         ("HYPR_DIR", "config_errors", "read_live_binds", "enter_capture", "leave_capture")}
        self.hypr = root / "hypr"
        self.hypr.mkdir()
        (self.hypr / "hyprland.lua").write_text('require("default.hypr.omarchy")\n')
        (self.hypr / "bindings.lua").write_text('o.bind("SUPER + CTRL + N", "Notes", "obsidian")\n')
        theme_shortcuts.HYPR_DIR = self.hypr
        theme_shortcuts.config_errors = lambda: ""
        theme_shortcuts.read_live_binds = lambda: [{"mask": 64, "key": "return", "description": "Terminal"}]
        self.captures: list[str] = []
        theme_shortcuts.enter_capture = lambda: self.captures.append("enter") or True
        theme_shortcuts.leave_capture = lambda: self.captures.append("leave")

        # --- build the real window ----------------------------------------
        self.app = theme_popup.ThemeManager()
        # Deterministic, in-process async (no GLib main loop in the test).
        def sync_async(self_, fn, on_done):
            try:
                result, error = fn(), None
            except Exception as exc:  # noqa: BLE001
                result, error = None, exc
            on_done(result, error)
        self._orig_async = theme_popup.ThemeManager._async
        theme_popup.ThemeManager._async = sync_async

        self.app.build_window()
        # Theme is currently "alpha" (a user theme).
        self.state_current.parent.mkdir(parents=True, exist_ok=True)
        self.state_current.write_text("alpha\n")

    def tearDown(self):
        theme_core.default_roots = self._orig_roots
        theme_core.hidden_file = self._orig_hidden
        theme_core.apply_theme = self._orig_apply
        theme_core.notify = self._orig_notify
        theme_core.clipboard_image = self._orig_clipboard
        theme_core.clipboard_files = self._orig_clipboard_files
        theme_core.cursor_position = self._orig_cursor
        (theme_core.switcher_cache_dir, theme_core.menu_extension_file,
         theme_core.preload_switcher) = self._orig_switcher
        theme_core.refresh_switcher_cache = self._orig_refresh
        theme_core.settings_file = self._orig_settings
        (theme_core.hidden_wallpapers_file, theme_core.theme_set_hook_file,
         theme_core.set_desktop_background) = self._orig_wallpaper
        (self.catalog_mod.load_catalog, self.catalog_mod.fetch_preview,
         self.catalog_mod.default_browser, self.catalog_mod.install,
         self.catalog_mod.open_in_browser, self.catalog_mod.cache_dir) = self._orig_catalog
        (self.ws.search, self.ws.fetch_thumb, self.ws.download, self.ws.open_page) = self._orig_ws
        theme_popup.ThemeManager._async = self._orig_async
        (theme_core.show_background, theme_core.restore_background,
         self.auto.state_file, self.auto.active_workspace) = self._orig_extra
        (self.custom.list_fonts, self.custom.current_font, self.custom.set_font) = self._orig_fonts
        (self.looks.windows_supported, self.looks.current_windows, self.looks.set_windows) = self._orig_looks
        for name, value in self._orig_sc.items():
            setattr(self.sc, name, value)
        self.tmp.cleanup()

    # -- helpers -------------------------------------------------------------
    def make_theme(self, scope: str, slug: str,
                   wallpapers: tuple[str, ...] = ()) -> theme_core.ThemeInfo:
        base = self.roots.user_themes if scope == "user" else self.roots.system_themes
        tdir = base / slug
        tdir.mkdir(parents=True, exist_ok=True)
        if wallpapers:
            bg = tdir / "backgrounds"
            bg.mkdir(exist_ok=True)
            for name in wallpapers:
                (bg / name).write_bytes(PNG)
        themes = theme_core.discover_themes()
        return next(t for t in themes if t.slug == slug)

    @staticmethod
    def children(widget) -> list:
        out, child = [], widget.get_first_child()
        while child is not None:
            out.append(child)
            child = child.get_next_sibling()
        return out

    def wallpaper_tiles(self) -> list:
        """Wallpaper tiles only (not the "Add wallpapers" tile)."""
        return [t for t in self.children(self.app.wp_grid) if hasattr(t, "tm_wallpaper")]

    def rows_list(self) -> list:
        """Theme rows only (not the "N hidden themes" hint)."""
        return [r for r in self.children(self.app.rows) if hasattr(r, "tm_theme")]

    def row_count(self) -> int:
        return len(self.rows_list())

    def make_wallpaper(self, slug: str, name: str = "user.png") -> Path:
        dest = self.roots.user_backgrounds / slug / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG)
        return dest

    # -- tests ------------------------------------------------------------------
    def test_rows_show_all_visible_themes(self):
        self.make_theme("user", "alpha", ("a1.png",))
        self.make_theme("user", "beta")
        self.make_theme("system", "gamma", ("g1.png",))
        self.app.refresh_themes()
        self.assertEqual(self.row_count(), 3)
        # Current theme gets the "current" badge row styling.
        rows = {row.tm_theme.slug: row for row in self.rows_list()}
        self.assertTrue(rows["alpha"].tm_theme.current)
        self.assertFalse(rows["beta"].tm_theme.current)

    def test_hidden_theme_hidden_and_revealed_by_eye(self):
        self.make_theme("user", "alpha")
        self.make_theme("user", "beta")
        theme_core.save_hidden({"beta"})
        self.app.hidden = theme_core.load_hidden()
        self.app.refresh_themes()
        visible = {row.tm_theme.slug for row in self.rows_list()}
        self.assertEqual(visible, {"alpha"})

        self.app.toggle_show_hidden()
        visible = {row.tm_theme.slug for row in self.rows_list()}
        self.assertEqual(visible, {"alpha", "beta"})
        beta_row = next(r for r in self.rows_list() if r.tm_theme.slug == "beta")
        self.assertTrue(beta_row.get_child().has_css_class("tm-row-hidden"))

        # Eye toggles back off: hidden row disappears again.
        self.app.toggle_show_hidden()
        visible = {row.tm_theme.slug for row in self.rows_list()}
        self.assertEqual(visible, {"alpha"})
        # The hidden set itself is unchanged.
        self.assertEqual(theme_core.load_hidden(), {"beta"})

    def test_menu_hide_unhide_persists(self):
        beta = self.make_theme("user", "beta")     # not the active theme
        theme_core.save_hidden(set())
        self.app.refresh_themes()
        self.app.set_theme_hidden(beta, True)
        self.assertEqual(theme_core.load_hidden(), {"beta"})
        self.app.set_theme_hidden(beta, False)
        self.assertEqual(theme_core.load_hidden(), set())

    def test_wallpapers_page_lists_theme_and_user_wallpapers(self):
        theme = self.make_theme("user", "alpha", ("ship1.png", "ship2.png"))
        self.make_wallpaper("alpha", "user1.png")
        self.app.open_wallpapers_for(theme)
        self.assertEqual(self.app.stack.get_visible_child_name(), "wallpapers")
        tiles = [t.get_child() for t in self.wallpaper_tiles()]
        names = sorted(tile.get_last_child().get_property("label")
                       for tile in tiles if isinstance(tile.get_last_child(), gi.repository.Gtk.Label))
        self.assertEqual(names, ["ship1.png", "ship2.png", "user1.png"])
        # Back button returns to the list.
        self.app.show_themes()
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")

    def test_oversized_images_are_never_decoded(self):
        # A theme can ship a tiny file that declares a 60000×60000 canvas;
        # neither its tile nor its preview may reach GTK's decoder.
        theme = self.make_theme("system", "bomb", ("huge.png", "fine.png"))
        (theme.path / "backgrounds" / "huge.png").write_bytes(png(60000, 60000))
        (theme.path / "preview.png").write_bytes(gif(1920, 1080, frames=40))
        decoded, requested = [], []
        pixbuf = theme_popup.GdkPixbuf.Pixbuf
        orig_load = pixbuf.new_from_file_at_scale
        orig_request = self.app.decoder.request
        pixbuf.new_from_file_at_scale = lambda path, *a: decoded.append(Path(path).name) or orig_load(path, *a)
        self.app.decoder.request = lambda picture, path, w, h: requested.append(str(path))
        try:
            self.app.refresh_themes()
            self.app.open_wallpapers_for(self.make_theme("system", "bomb"))
            self.assertEqual({Path(p).name for p in requested}, {"huge.png", "fine.png", "preview.png"})
            for path in requested:
                theme_popup.scaled_pixbuf(path, 100, 100)   # what the worker thread runs
        finally:
            pixbuf.new_from_file_at_scale = orig_load
            self.app.decoder.request = orig_request
        self.assertEqual(decoded, ["fine.png"])

    def test_empty_wallpaper_state(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        tiles = self.children(self.app.wp_grid)
        self.assertEqual(len(tiles), 1)  # the add tile, saying there are none yet
        self.assertTrue(tiles[0].tm_add)
        self.assertEqual(self.app.wp_title.get_property("label"), "Alpha — wallpapers")

    def test_on_drop_adds_image(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "dropped.png"
        src.write_bytes(PNG)
        self.app.open_wallpapers_for(theme)
        uri = GLib.filename_to_uri(str(src), None)
        self.app.drop_value(uri)
        dest = list((self.roots.user_backgrounds / "alpha").iterdir())
        self.assertEqual(len(dest), 1)
        self.assertIn("added", " ".join(t for t, _ in self.notices).lower())

    def test_on_drop_multiple_uris(self):
        theme = self.make_theme("user", "alpha")
        paths = []
        for i in range(3):
            p = Path(self.tmp.name) / f"multi-{i}.png"
            p.write_bytes(PNG)
            paths.append(p)
        self.app.open_wallpapers_for(theme)
        uris = "\n".join(GLib.filename_to_uri(str(p), None) for p in paths)
        self.app.drop_value(uris)
        self.assertEqual(len(list((self.roots.user_backgrounds / "alpha").iterdir())), 3)

    def test_on_drop_file_list_from_file_manager(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "from nautilus.png"
        src.write_bytes(PNG)
        self.app.open_wallpapers_for(theme)
        files = Gdk.FileList.new_from_list([Gio.File.new_for_path(str(src))])
        self.assertTrue(self.app.drop_value(files))
        dest = list((self.roots.user_backgrounds / "alpha").iterdir())
        self.assertEqual([d.name for d in dest], ["from nautilus.png"])

    def test_browser_image_bytes_are_added(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        self.app.add_image_data(PNG, "43lk53.jpg")
        dest = list((self.roots.user_backgrounds / "alpha").iterdir())
        self.assertEqual([d.name for d in dest], ["43lk53.png"])
        self.app.add_image_data(b"<html>", "page.html")
        self.assertEqual(len(list((self.roots.user_backgrounds / "alpha").iterdir())), 1)
        self.assertTrue(any("could not" in t.lower() for t, _ in self.notices))

    def run_stream(self, stream, limit, timeout_s=5):
        result = []
        loop = GLib.MainLoop()
        self.app.read_stream(stream, limit, lambda data: (result.append(data), loop.quit()),
                             timeout_s=timeout_s)
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        return result

    def test_read_stream_bounds(self):
        ok = self.run_stream(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(b"a" * 3000)), 4096)
        self.assertEqual(ok, [b"a" * 3000])
        big = self.run_stream(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(b"a" * 5000)), 4096)
        self.assertEqual(big, [None])

    def test_read_stream_stalled_source_times_out(self):
        import os as oslib
        import time as timelib
        read_fd, write_fd = oslib.pipe()
        self.addCleanup(oslib.close, write_fd)
        gi.require_version("GioUnix", "2.0")
        from gi.repository import GioUnix
        stream = GioUnix.InputStream.new(read_fd, True)
        start = timelib.monotonic()
        self.assertEqual(self.run_stream(stream, 4096, timeout_s=1), [None])
        self.assertLess(timelib.monotonic() - start, 3)

    def fake_drop(self, mime, stream):
        """A foreign drag source offering `mime` with `stream` as payload. It
        fails the test if anything reads it through read_value_async."""
        test = self

        class Formats:
            def get_mime_types(self):
                return [mime]

        class FakeDrop:
            finished = []

            def get_formats(self):
                return Formats()

            def read_async(self, mimes, _prio, _cancel, callback):
                test.assertEqual(mimes, [mime])
                GLib.idle_add(lambda: callback(self, None) and False)

            def read_finish(self, _result):
                return stream, mime

            def read_value_async(self, *_args):
                test.fail("drops must not be read through read_value_async")

            def finish(self, action):
                self.finished.append(action)
        return FakeDrop()

    def run_drop(self, drop, seconds=5):
        loop = GLib.MainLoop()
        self.assertTrue(self.app.on_drop(None, drop, 0, 0))

        def check():
            if drop.finished:
                loop.quit()
                return False
            return True
        GLib.timeout_add(20, check)
        GLib.timeout_add_seconds(seconds, loop.quit)
        loop.run()

    def test_dropped_uri_list_is_read_bounded(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "listed.png"
        src.write_bytes(PNG)
        self.app.open_wallpapers_for(theme)
        uri = GLib.filename_to_uri(str(src), None).encode()
        drop = self.fake_drop("text/uri-list",
                              Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(uri + b"\r\n")))
        self.run_drop(drop)
        self.assertEqual(drop.finished, [Gdk.DragAction.COPY])
        self.assertEqual([d.name for d in (self.roots.user_backgrounds / "alpha").iterdir()],
                         ["listed.png"])

    def test_oversized_uri_list_drop_is_refused(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "big.png"
        src.write_bytes(PNG)
        self.app.open_wallpapers_for(theme)
        uri = GLib.filename_to_uri(str(src), None).encode() + b"\n"
        payload = uri + b"#" * (2 * 1024 * 1024)   # 2 MiB, over MAX_DROP_LIST_BYTES
        drop = self.fake_drop("text/uri-list",
                              Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(payload)))
        self.run_drop(drop)
        self.assertEqual(drop.finished, [0])
        d = self.roots.user_backgrounds / "alpha"
        self.assertFalse(d.exists() and any(d.iterdir()))
        self.assertTrue(any("too long" in b for _, b in self.notices))

    def test_text_drop_is_read_bounded(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "as text.png"
        src.write_bytes(PNG)
        self.app.open_wallpapers_for(theme)
        drop = self.fake_drop("text/plain;charset=utf-8",
                              Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(str(src).encode())))
        self.run_drop(drop)
        self.assertEqual(drop.finished, [Gdk.DragAction.COPY])
        self.assertEqual(len(list((self.roots.user_backgrounds / "alpha").iterdir())), 1)

    def test_stalled_uri_list_drop_times_out(self):
        import os as oslib
        import time as timelib
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        read_fd, write_fd = oslib.pipe()
        self.addCleanup(oslib.close, write_fd)
        oslib.write(write_fd, b"file:///tmp/x\n")   # some data, then nothing
        gi.require_version("GioUnix", "2.0")
        from gi.repository import GioUnix
        drop = self.fake_drop("text/uri-list", GioUnix.InputStream.new(read_fd, True))
        old = theme_popup.DROP_LIST_TIMEOUT
        theme_popup.DROP_LIST_TIMEOUT = 1
        self.addCleanup(setattr, theme_popup, "DROP_LIST_TIMEOUT", old)
        start = timelib.monotonic()
        self.run_drop(drop)
        self.assertEqual(drop.finished, [0])
        self.assertLess(timelib.monotonic() - start, 3)

    def test_uri_list_stops_after_max_drops(self):
        paths = []
        for i in range(theme_popup.MAX_DROPS + 5):
            p = Path(self.tmp.name) / f"many-{i}.png"
            p.write_bytes(PNG)
            paths.append(GLib.filename_to_uri(str(p), None))
        self.assertEqual(len(theme_popup.parse_uris("\n".join(paths))), theme_popup.MAX_DROPS)

    def test_on_drop_ignores_plain_text(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        self.app.drop_value("hello world, no uris")
        d = self.roots.user_backgrounds / "alpha"
        self.assertFalse(d.exists() and any(d.iterdir()))
        self.assertIn("nothing", " ".join(t for t, _ in self.notices).lower())

    def test_on_drop_rejects_non_image(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "notes.txt"
        src.write_text("not an image")
        self.app.open_wallpapers_for(theme)
        self.app.drop_value(GLib.filename_to_uri(str(src), None))
        d = self.roots.user_backgrounds / "alpha"
        self.assertFalse(d.exists() and any(d.iterdir()))
        self.assertTrue(any("skipped" in t.lower() for t, _ in self.notices))

    def pump_until(self, condition, seconds: float = 3.0):
        import time
        deadline = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while not condition() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)
        return condition()

    def test_search_fields_never_use_gtks_unbounded_paste(self):
        orig = theme_core.clipboard_text
        reads = []
        theme_core.clipboard_text = lambda **kw: reads.append(1) or "misty forest"
        try:
            for entry in (self.app.web_search, self.app.import_search):
                text = entry.get_delegate()
                self.assertEqual(text.get_max_length(), theme_core.MAX_FIELD_CHARS)
                # No drop target of its own: GTK would buffer dropped text unbounded.
                controllers = text.observe_controllers()
                kinds = {type(controllers.get_item(i)).__name__ for i in range(controllers.get_n_items())}
                self.assertFalse(kinds & {"DropTarget", "DropTargetAsync"})
                # Ctrl+V, Shift+Insert and the context menu's clipboard.paste all emit this signal.
                entry.set_text("")
                text.emit("paste-clipboard")
                self.assertTrue(self.pump_until(lambda: entry.get_text() == "misty forest"))
                # Pasting into a selection replaces it, at the cursor.
                entry.set_text("a b c")
                entry.select_region(2, 3)
                text.emit("paste-clipboard")
                self.assertTrue(self.pump_until(lambda: entry.get_text() == "a misty forest c"))
        finally:
            theme_core.clipboard_text = orig
        self.assertEqual(len(reads), 4)
        settings = theme_popup.Gtk.Settings.get_default()
        self.assertFalse(settings.get_property("gtk-enable-primary-paste"))

    def test_color_chooser_fields_never_use_gtks_unbounded_paste(self):
        """The hex field and spin buttons GTK puts inside its color chooser
        get the same bounded paste and no drop target of their own."""
        Gtk = theme_popup.Gtk
        chooser = Gtk.ColorChooserWidget(use_alpha=False)
        texts = []
        stack = [chooser]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.Text):
                texts.append(widget)
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        self.assertTrue(texts, "GTK's color editor should contain a hex field")
        self.assertEqual(self.app.guard_text_fields_in(chooser), len(texts))
        orig = theme_core.clipboard_text
        reads = []
        theme_core.clipboard_text = lambda **kw: reads.append(1) or "#3a6ea5"
        try:
            for text in texts:
                self.assertEqual(text.get_max_length(), theme_core.MAX_FIELD_CHARS)
                controllers = text.observe_controllers()
                kinds = {type(controllers.get_item(i)).__name__ for i in range(controllers.get_n_items())}
                self.assertFalse(kinds & {"DropTarget", "DropTargetAsync"})
                text.set_text("")
                text.emit("paste-clipboard")
                self.assertTrue(self.pump_until(lambda: text.get_text() == "#3a6ea5"))
        finally:
            theme_core.clipboard_text = orig
        self.assertEqual(len(reads), len(texts))

    def test_oversized_clipboard_text_is_refused(self):
        orig = theme_core.clipboard_text
        theme_core.clipboard_text = lambda **kw: None
        try:
            entry = self.app.web_search
            entry.set_text("keep")
            entry.get_delegate().emit("paste-clipboard")
            self.assertTrue(self.pump_until(lambda: self.notices))
        finally:
            theme_core.clipboard_text = orig
        self.assertEqual(entry.get_text(), "keep")
        self.assertIn("too long", self.notices[-1][1])

    def test_paste_adds_image(self):
        theme = self.make_theme("user", "alpha")
        theme_core.clipboard_image = lambda **kw: ("image/png", PNG)
        self.app.open_wallpapers_for(theme)
        self.app.paste_clipboard()
        dest = list((self.roots.user_backgrounds / "alpha").iterdir())
        self.assertEqual(len(dest), 1)
        self.assertTrue(dest[0].name.startswith("pasted-image"))

    def test_paste_copied_file_adds_image(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "holiday photo.png"
        src.write_bytes(PNG)
        theme_core.clipboard_image = lambda **kw: None
        theme_core.clipboard_files = lambda **kw: [src]
        self.app.open_wallpapers_for(theme)
        self.app.paste_clipboard()
        dest = list((self.roots.user_backgrounds / "alpha").iterdir())
        self.assertEqual(len(dest), 1)
        self.assertTrue(dest[0].name.startswith("holiday"))

    def test_paste_empty_clipboard_notifies(self):
        theme = self.make_theme("user", "alpha")
        theme_core.clipboard_image = lambda **kw: None
        self.app.open_wallpapers_for(theme)
        self.app.paste_clipboard()
        d = self.roots.user_backgrounds / "alpha"
        self.assertFalse(d.exists() and any(d.iterdir()))
        self.assertTrue(any("clipboard" in (t + b).lower() for t, b in self.notices))

    def test_delete_wallpaper_confirmed(self):
        theme = self.make_theme("user", "alpha")
        target = self.make_wallpaper("alpha", "del.png")
        self.app.open_wallpapers_for(theme)
        wallpapers = theme_core.list_backgrounds(None, theme)
        info = next(w for w in wallpapers if w.name == "del.png")
        self.assertTrue(info.deletable)
        self.app.perform_delete_wallpaper(info)
        self.assertFalse(target.exists())
        self.assertTrue(any("removed" in t.lower() or "deleted" in t.lower()
                            for t, _ in self.notices))

    def test_system_shipped_wallpaper_not_deletable(self):
        theme = self.make_theme("system", "gamma", ("g1.png",))
        self.app.open_wallpapers_for(theme)
        wallpapers = theme_core.list_backgrounds(None, theme)
        self.assertEqual(len(wallpapers), 1)
        self.assertFalse(wallpapers[0].deletable)
        # But user can still add their own alongside.
        self.make_wallpaper("gamma", "mine.png")
        wallpapers = theme_core.list_backgrounds(None, theme)
        self.assertEqual({w.name for w in wallpapers}, {"g1.png", "mine.png"})

    def test_apply_theme_action(self):
        self.make_theme("user", "alpha")
        self.make_theme("user", "beta")
        self.app.refresh_themes()
        beta = next(t for t in self.app.theme_list if t.slug == "beta")
        self.app.apply_theme_action(beta)
        self.assertEqual(self.applied, ["beta"])

    def test_apply_and_delete_keep_popup_open(self):
        for slug in ("beta", "gamma", "delta"):
            self.make_theme("user", slug)
        self.app.refresh_themes()
        self.app.window.present()
        quits = []
        self.app.quit = lambda: quits.append(True)
        beta = next(t for t in self.app.theme_list if t.slug == "beta")
        self.app.apply_theme_action(beta)
        self.assertEqual(self.applied, ["beta"])
        self.assertTrue(self.app.window.get_visible())
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "beta")
        gamma = next(t for t in self.app.theme_list if t.slug == "gamma")
        self.app.perform_delete_theme(gamma)
        self.assertFalse((self.roots.user_themes / "gamma").exists())
        self.assertTrue(self.app.window.get_visible())
        self.assertNotIn("gamma", [r.tm_theme.slug for r in self.rows_list()])
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "delta")   # the next one
        self.assertEqual(quits, [])

    def test_delete_theme_user(self):
        theme = self.make_theme("user", "beta", ("b1.png",))
        self.app.perform_delete_theme(theme)
        self.assertFalse((self.roots.user_themes / "beta").exists())

    def test_delete_theme_system_refused_at_core(self):
        theme = self.make_theme("system", "gamma")
        with self.assertRaises(FileNotFoundError):
            theme_core.remove_theme(None, "gamma")
        self.assertTrue((self.roots.system_themes / "gamma").exists())

    def menu_entries(self, theme) -> dict:
        """label -> menu button, for the row's action menu."""
        Gtk = gi.repository.Gtk
        box = self.app.build_theme_menu(Gtk.Popover(), theme)
        entries = {}
        for button in self.children(box):
            if not isinstance(button, Gtk.Button):
                continue
            line = button.get_child()
            label = next(c for c in self.children(line) if isinstance(c, Gtk.Label))
            entries[label.get_label()] = button
        return entries

    def test_font_and_effects_one_save_applies_the_theme_in_use_and_undo(self):
        theme = self.make_theme("user", "alpha")
        other = self.make_theme("user", "beta")
        entries = self.menu_entries(theme)
        self.assertIn("Font & effects…", entries)
        self.assertNotIn("Reapply", entries)          # nothing pending
        self.app.open_look_page(theme)
        self.assertEqual(self.app.stack.get_visible_child_name(), "look")
        model = self.app.lk_font.get_model()
        self.assertEqual([model.get_string(i) for i in range(model.get_n_items())],
                         ["Theme default (yours: Mono A)", "Mono A", "Mono B"])
        self.assertFalse(hasattr(self.app, "lk_reapply"))       # one button: Save (2026-11-09)
        self.assertFalse(self.app.lk_save.get_sensitive())      # enabled once something changed
        self.assertEqual(self.app.lk_save.get_label(), "Apply")
        self.app.lk_font.set_selected(2)
        self.app.lk_windows.set_selected(3)
        self.assertTrue(self.app.lk_save.get_sensitive())
        self.app.save_look()                           # the theme in use: stored and applied
        self.assertEqual(self.looks.look_of("alpha"), {"mono_font": "Mono B", "windows": "glassy"})
        self.assertEqual((self.font, self.windows), ("Mono B", "glassy"))
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
        self.assertNotIn("Reapply", self.menu_entries(theme))
        self.app.undo_delete()                         # the look as it was, applied again
        self.assertEqual(self.looks.look_of("alpha"), {})
        self.assertEqual((self.font, self.windows), ("Mono A", "transparent"))
        self.app.open_look_page(other)                 # another theme: only stored
        self.app.lk_windows.set_selected(1)
        self.app.save_look()
        self.assertEqual(self.looks.look_of("beta"), {"windows": "opaque"})
        self.assertEqual(self.windows, "transparent")
        self.looks.set_look("alpha", {"windows": "glassy"})    # changed elsewhere: the menu offers Reapply
        self.app.refresh_look_pending()
        self.menu_entries(theme)["Reapply"].emit("clicked")
        self.assertEqual(self.windows, "glassy")

    def test_system_menu_item_disabled(self):
        theme = self.make_theme("system", "gamma")
        entries = self.menu_entries(theme)
        self.assertIn("System theme (read-only)", entries)
        self.assertFalse(entries["System theme (read-only)"].get_sensitive())
        self.assertNotIn("Delete theme…", entries)

    def test_user_menu_has_destructive_delete(self):
        theme = self.make_theme("user", "beta")
        entries = self.menu_entries(theme)
        self.assertTrue(entries["Delete theme…"].has_css_class("destructive"))
        self.assertIn("Apply theme", entries)
        self.assertIn("Hide from list", entries)

    def test_right_opens_menu_left_and_backspace_close_it(self):
        Gdk = gi.repository.Gdk
        self.make_theme("user", "alpha")
        self.app.refresh_themes()
        self.assertTrue(self.app.on_key(None, Gdk.KEY_Right, 0, 0))
        popover = self.app.actions_popover
        self.assertIsNotNone(popover)
        self.assertIs(popover.get_parent(), self.rows_list()[0].actions_button)
        for key in (Gdk.KEY_Left, Gdk.KEY_BackSpace):
            self.app.show_actions(self.rows_list()[0].actions_button, self.rows_list()[0].tm_theme)
            popover = self.app.actions_popover
            self.assertTrue(self.app.close_actions_on_key(popover, key))
        self.assertFalse(self.app.close_actions_on_key(popover, Gdk.KEY_Down))

    def test_left_and_backspace_leave_wallpaper_page(self):
        Gdk = gi.repository.Gdk
        theme = self.make_theme("user", "alpha")
        for key in (Gdk.KEY_Left, Gdk.KEY_BackSpace):
            self.app.open_wallpapers_for(theme)
            self.assertTrue(self.app.on_key(None, key, 0, 0))
            self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
            self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "alpha")

    def test_down_and_up_move_the_selection(self):
        Gdk = gi.repository.Gdk
        for slug in ("alpha", "beta", "delta"):
            self.make_theme("user", slug)
        self.app.refresh_themes()
        # Two columns already at the default size: alpha beta / delta.
        self.app.on_key(None, Gdk.KEY_Down, 0, 0)
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "delta")
        self.app.on_key(None, Gdk.KEY_Up, 0, 0)
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "alpha")
        self.app.on_key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "beta")

    def test_wallpaper_page_is_no_wider_than_theme_list(self):
        Gtk = gi.repository.Gtk
        theme = self.make_theme("system", "a-very-long-theme-name-that-goes-on-and-on")
        self.app.open_wallpapers_for(theme)
        pages = {name: self.app.stack.get_child_by_name(name) for name in ("themes", "wallpapers")}
        widths = {name: page.measure(Gtk.Orientation.HORIZONTAL, -1)[1] for name, page in pages.items()}
        self.assertLessEqual(widths["wallpapers"], theme_popup.CARD_WIDTH)
        self.assertLessEqual(widths["themes"], theme_popup.CARD_WIDTH)

    def test_row_activation_opens_wallpapers(self):
        self.make_theme("user", "alpha")
        self.app.refresh_themes()
        row = self.rows_list()[0]
        self.app.rows.emit("child-activated", row)
        self.assertEqual(self.app.stack.get_visible_child_name(), "wallpapers")
        self.assertEqual(self.app.active_theme.slug, "alpha")

    def test_delete_theme_asks_inside_card(self):
        theme = self.make_theme("user", "beta")
        self.app.confirm_delete_theme(theme)
        self.assertEqual(self.app.stack.get_visible_child_name(), "confirm")
        self.app.cancel_confirm()
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
        self.assertTrue((self.roots.user_themes / "beta").exists())
        self.app.confirm_delete_theme(theme)
        self.app.accept_confirm()
        self.assertFalse((self.roots.user_themes / "beta").exists())

    def test_delete_wallpaper_confirm_returns_to_wallpapers(self):
        theme = self.make_theme("user", "alpha")
        target = self.make_wallpaper("alpha", "w.png")
        self.app.open_wallpapers_for(theme)
        info = next(w for w in theme_core.list_backgrounds(None, theme) if w.name == "w.png")
        self.app.confirm_delete_wallpaper(info)
        self.app.accept_confirm()
        self.assertEqual(self.app.stack.get_visible_child_name(), "wallpapers")
        self.assertFalse(target.exists())

    def test_paste_jpeg_keeps_jpeg_extension(self):
        theme = self.make_theme("user", "alpha")
        theme_core.clipboard_image = lambda **kw: ("image/jpeg", jpeg())
        self.app.open_wallpapers_for(theme)
        self.app.paste_clipboard()
        dest = list((self.roots.user_backgrounds / "alpha").iterdir())
        self.assertEqual([d.suffix for d in dest], [".jpg"])

    def test_drop_hint_is_shown(self):
        theme = self.make_theme("system", "gamma")
        self.app.open_wallpapers_for(theme)
        add = self.children(self.app.wp_grid)[0]
        self.assertTrue(add.tm_add)
        self.assertIn("Drop images here", " ".join(w.get_label() for w in self.descendants(add)
                                                    if isinstance(w, gi.repository.Gtk.Label)))
        self.assertIn("Stored in", add.get_tooltip_text())
        self.app.light_add_tile(True)
        self.assertTrue(self.app.add_tile.has_css_class("active"))
        self.app.light_add_tile(False)
        self.assertFalse(self.app.add_tile.has_css_class("active"))

    def test_hide_and_unhide_sync_omarchy_picker(self):
        theme = self.make_theme("system", "gamma")
        previews = self.switcher_dir / "previews"
        (previews / "gamma.png").symlink_to(self.roots.system_themes / "gamma")
        (self.switcher_dir / "fast-signature").write_text("v1")
        self.app.set_theme_hidden(theme, True)
        self.assertFalse((previews / "gamma.png").is_symlink())
        self.assertEqual(self.preloads, [])
        self.app.set_theme_hidden(theme, False)
        self.assertFalse((self.switcher_dir / "fast-signature").exists())
        self.assertEqual(len(self.preloads), 1)
        self.assertTrue(self.preloads[0].endswith("theme-switcher"))


    def test_resting_pointer_does_not_steal_selection(self):
        for slug in ("alpha", "beta"):
            self.make_theme("user", slug)
        self.app.refresh_themes()
        first, second = self.rows_list()
        self.app.select_row(first)
        y = 200  # somewhere over the second row once laid out; get_row_at_y may be None unlaid
        self.app.on_list_pointer(10, y, moved=False)   # enter after a menu closes
        self.app.on_list_pointer(10, y, moved=True)    # same spot: not a real move
        self.assertIs(self.app.rows.get_selected_row(), first)


    def wallpaper_menu(self, wallpaper) -> dict:
        Gtk = gi.repository.Gtk
        box = self.app.build_wallpaper_menu(Gtk.Popover(), wallpaper)
        entries = {}
        for button in self.children(box):
            if isinstance(button, Gtk.Button):
                label = next(c for c in self.children(button.get_child()) if isinstance(c, Gtk.Label))
                entries[label.get_label()] = button
        return entries

    def test_system_wallpaper_menu_offers_hide_not_delete(self):
        theme = self.make_theme("system", "gamma", ("g1.png", "g2.png"))
        self.app.open_wallpapers_for(theme)
        info = next(w for w in theme_core.list_backgrounds(None, theme) if w.name == "g1.png")
        entries = self.wallpaper_menu(info)
        self.assertIn("Hide", entries)
        self.assertNotIn("Delete…", entries)
        self.assertFalse(entries["Can't delete: hide instead"].get_sensitive())

    def test_hiding_wallpaper_removes_tile_until_eye_is_on(self):
        theme = self.make_theme("system", "gamma", ("g1.png", "g2.png"))
        self.app.open_wallpapers_for(theme)
        self.assertEqual(len(self.wallpaper_tiles()), 2)
        info = next(w for w in theme_core.list_backgrounds(None, theme) if w.name == "g1.png")
        self.app.set_wallpaper_hidden(info, True)
        self.assertEqual(len(self.wallpaper_tiles()), 1)
        self.app.toggle_show_hidden()
        tiles = self.wallpaper_tiles()
        self.assertEqual(len(tiles), 2)
        hidden = next(t for t in tiles if t.tm_wallpaper.name == "g1.png")
        self.assertTrue(hidden.tm_wallpaper.hidden)
        self.assertIn("Unhide", self.wallpaper_menu(hidden.tm_wallpaper))

    def test_last_visible_wallpaper_cannot_be_hidden(self):
        theme = self.make_theme("system", "gamma", ("only.png",))
        self.app.open_wallpapers_for(theme)
        info = theme_core.list_backgrounds(None, theme)[0]
        self.app.set_wallpaper_hidden(info, True)
        self.assertFalse(theme_core.list_backgrounds(None, theme)[0].hidden)
        self.assertTrue(any("could not hide" in t.lower() for t, _ in self.notices))


    def catalog_menu(self, entry) -> dict:
        Gtk = gi.repository.Gtk
        box = self.app.build_catalog_menu(Gtk.Popover(), entry)
        entries = {}
        for button in self.children(box):
            if isinstance(button, Gtk.Button):
                label = next(c for c in self.children(button.get_child()) if isinstance(c, Gtk.Label))
                entries[label.get_label()] = button
        return entries

    def test_import_page_lists_catalog_and_marks_installed(self):
        self.make_theme("user", "alpha")
        self.app.open_import()
        self.assertEqual(self.app.stack.get_visible_child_name(), "import")
        tiles = self.children(self.app.import_grid)
        self.assertEqual([t.tm_catalog.slug for t in tiles], ["ash", "alpha", "frost"])
        self.assertEqual({t.tm_catalog.slug: t.tm_installed for t in tiles},
                         {"ash": False, "alpha": True, "frost": False})
        self.assertIn("3 themes", self.app.import_hint.get_label())

    def test_import_menu_install_and_installed(self):
        self.make_theme("user", "alpha")
        self.app.open_import()
        ash, alpha = self.catalog_entries[0], self.catalog_entries[1]
        self.assertIn("Install", self.catalog_menu(ash))
        self.assertIn("Install and apply", self.catalog_menu(ash))
        self.assertIn("Apply theme", self.catalog_menu(alpha))
        self.assertNotIn("Install", self.catalog_menu(alpha))
        self.app.install_catalog(ash, apply=False)
        self.assertEqual(self.installed, ["ash"])
        self.assertTrue(any(t.tm_installed for t in self.children(self.app.import_grid)
                            if t.tm_catalog.slug == "ash"))

    def test_install_and_apply_applies(self):
        self.app.open_import()
        self.app.install_catalog(self.catalog_entries[2], apply=True)
        self.assertEqual(self.applied, ["frost"])

    def test_open_theme_page_and_no_browser_hint(self):
        self.app.open_import()
        self.catalog_menu(self.catalog_entries[0])["Open theme page"].emit("clicked")
        self.assertEqual(self.opened, ["https://github.com/a/omarchy-ash-theme"])
        self.browser = None
        self.app.open_import()
        self.assertIn("No default web browser", self.app.import_hint.get_label())
        entry = self.catalog_menu(self.catalog_entries[0])["Open theme page (no browser)"]
        self.assertFalse(entry.get_sensitive())

    def test_import_search_filters(self):
        Gtk = gi.repository.Gtk
        self.app.open_import()
        self.app.import_search.set_text("fro")
        shown = [t.tm_catalog.slug for t in self.children(self.app.import_grid)
                 if self.app.catalog_filter(t)]
        self.assertEqual(shown, ["frost"])

    def test_backspace_with_empty_search_goes_back(self):
        Gdk = gi.repository.Gdk
        self.app.open_import()
        self.assertTrue(self.app.on_key(None, Gdk.KEY_BackSpace, 0, 0))
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")

    def test_import_page_is_no_wider_than_theme_list(self):
        Gtk = gi.repository.Gtk
        self.app.open_import()
        page = self.app.stack.get_child_by_name("import")
        self.assertLessEqual(page.measure(Gtk.Orientation.HORIZONTAL, -1)[1], theme_popup.CARD_WIDTH)


    def web_menu(self, item) -> dict:
        Gtk = gi.repository.Gtk
        box = self.app.build_web_menu(Gtk.Popover(), item)
        entries = {}
        for button in self.children(box):
            if isinstance(button, Gtk.Button):
                label = next(c for c in self.children(button.get_child()) if isinstance(c, Gtk.Label))
                entries[label.get_label()] = button
        return entries

    def test_web_search_lists_results_and_pages(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        self.app.open_web_search()
        self.assertEqual(self.app.stack.get_visible_child_name(), "websearch")
        self.app.web_search.set_text("misty  forest")
        self.app.run_web_search()
        self.assertEqual(self.searches, [("misty forest", 1)])
        self.assertEqual(len(self.children(self.app.web_grid)), 3)
        self.assertIn("Scroll down for more", self.app.web_hint.get_label())
        self.app.load_more_web_results()
        self.assertEqual(self.searches[-1], ("misty forest", 2))
        self.app.load_more_web_results()          # last page reached: no request
        self.assertEqual(len(self.searches), 2)

    def test_web_add_saves_and_marks_tile(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        self.app.open_web_search()
        self.app.web_search.set_text("city")
        self.app.run_web_search()
        item = self.web_results[0]
        self.assertIn("Add to Alpha", self.web_menu(item))
        self.app.add_web_wallpaper(item, set_background=False)
        self.assertTrue((self.roots.user_backgrounds / "alpha" / item.file_name).exists())
        tile = next(t for t in self.children(self.app.web_grid) if t.tm_web == item)
        self.assertTrue(tile.tm_added)
        self.assertIn("Already added", self.web_menu(item))

    def test_web_open_page_needs_browser(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        self.app.open_web_search()
        item = self.web_results[0]
        self.web_menu(item)["Open on wallhaven.cc"].emit("clicked")
        self.assertEqual(self.opened, [item.page])
        self.browser = None
        self.app.open_web_search()
        self.assertIn("No default web browser", self.app.web_hint.get_label())
        self.assertFalse(self.web_menu(item)["Open page (no browser)"].get_sensitive())

    def test_web_page_backspace_returns_to_wallpapers(self):
        Gdk = gi.repository.Gdk
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        self.app.open_web_search()
        self.app.on_key(None, Gdk.KEY_BackSpace, 0, 0)
        self.assertEqual(self.app.stack.get_visible_child_name(), "wallpapers")

    def test_web_page_is_no_wider_than_theme_list(self):
        Gtk = gi.repository.Gtk
        theme = self.make_theme("user", "a-very-long-theme-name-for-the-title")
        self.app.open_wallpapers_for(theme)
        self.app.open_web_search()
        for name in ("websearch", "wallpapers"):
            page = self.app.stack.get_child_by_name(name)
            self.assertLessEqual(page.measure(Gtk.Orientation.HORIZONTAL, -1)[1], theme_popup.CARD_WIDTH, name)


    def type_keys(self, text):
        Gdk = gi.repository.Gdk
        for ch in text:
            self.app.on_key(None, Gdk.unicode_to_keyval(ord(ch)), 0, 0)

    def test_typing_filters_theme_list_like_simple_launcher(self):
        Gdk = gi.repository.Gdk
        for slug in ("tokyo-night", "nord", "rose-pine"):
            self.make_theme("system", slug)
        self.app.refresh_themes()
        self.type_keys("no")
        self.assertEqual([r.tm_theme.slug for r in self.rows_list()], ["nord"])
        self.assertEqual(self.app.title_label.get_label(), "Theme Manager — no")
        self.app.on_key(None, Gdk.KEY_BackSpace, 0, 0)
        self.assertEqual(self.app.filter_query, "n")
        self.assertEqual(len(self.rows_list()), 3)  # tokyo-night, nord and rose-pine all contain n
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)   # first Esc clears the filter
        self.assertEqual(self.app.filter_query, "")
        self.assertEqual(self.app.title_label.get_label(), "Theme Manager")

    def test_filter_without_match_shows_hint_and_resets_on_open(self):
        self.make_theme("system", "nord")
        self.app.refresh_themes()
        self.type_keys("zzz")
        self.assertEqual(self.rows_list(), [])
        self.assertIn("No theme matches", self.app.no_match.get_label())
        self.app.open()
        self.assertEqual(self.app.filter_query, "")
        self.assertEqual(len(self.rows_list()), 1)

    def test_filter_selects_first_match_for_enter(self):
        Gdk = gi.repository.Gdk
        for slug in ("alpha", "beta"):
            self.make_theme("user", slug)
        self.app.refresh_themes()
        self.type_keys("bet")
        self.app.on_key(None, Gdk.KEY_Return, 0, 0)
        self.assertEqual(self.app.active_theme.slug, "beta")

    def test_typing_filters_wallpapers(self):
        Gdk = gi.repository.Gdk
        theme = self.make_theme("system", "gamma", ("forest.png", "city.png", "forest-2.png"))
        self.app.open_wallpapers_for(theme)
        self.type_keys("for")
        names = [t.tm_wallpaper.name for t in self.children(self.app.wp_grid)]
        self.assertEqual(names, ["forest-2.png", "forest.png"])
        self.assertTrue(self.app.wp_title.get_label().endswith("— for"))
        self.app.on_key(None, Gdk.KEY_BackSpace, 0, 0)       # edits the filter, stays
        self.assertEqual(self.app.stack.get_visible_child_name(), "wallpapers")
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)          # clears the filter, stays
        self.assertEqual(self.app.wp_filter, "")
        self.assertEqual(len(self.wallpaper_tiles()), 3)
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)          # now goes back
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")


    def test_active_theme_cannot_be_deleted(self):
        theme = self.make_theme("user", "alpha")   # setUp makes alpha the current theme
        self.assertTrue(theme.current)
        entries = self.menu_entries(theme)
        self.assertFalse(entries["Delete theme…"].get_sensitive())
        self.app.perform_delete_theme(theme)
        self.assertTrue((self.roots.user_themes / "alpha").exists())
        self.assertTrue(any("could not delete" in t.lower() for t, _ in self.notices))

    def test_delete_and_install_rebuild_picker_cache_filtered(self):
        theme = self.make_theme("user", "beta")
        self.app.perform_delete_theme(theme)
        self.assertFalse((self.roots.user_themes / "beta").exists())
        self.assertEqual(len(self.refreshes), 1)
        self.assertTrue(self.refreshes[0].endswith("theme-switcher"))
        self.app.open_import()
        self.app.install_catalog(self.catalog_entries[0], apply=False)
        self.assertEqual(len(self.refreshes), 2)


    def test_applying_hidden_theme_unhides_it(self):
        theme = self.make_theme("system", "gamma")
        (self.switcher_dir / "fast-signature").write_text("v1")
        self.app.set_theme_hidden(theme, True)
        self.assertIn("gamma", theme_core.load_hidden())
        (self.switcher_dir / "fast-signature").write_text("v1")
        self.app.apply_theme_action(theme)
        self.assertEqual(self.applied, ["gamma"])
        self.assertNotIn("gamma", theme_core.load_hidden())
        self.assertNotIn("gamma", self.app.hidden)
        self.assertFalse((self.switcher_dir / "fast-signature").exists())   # picker rebuilds with it


    def test_hidden_themes_stay_out_and_hint_says_how_many(self):
        for slug in ("nord", "white", "gamma"):
            self.make_theme("system", slug)
        self.app.hidden = {"nord", "white"}
        self.app.refresh_themes()
        self.assertEqual([r.tm_theme.slug for r in self.rows_list()], ["gamma"])
        self.assertEqual(len(self.children(self.app.rows)), 1)    # only themes in the grid
        self.assertTrue(self.app.hidden_hint.get_visible())
        self.assertIn("2 hidden themes", self.app.hidden_hint.get_label())

    def test_active_theme_is_never_hidden(self):
        theme = self.make_theme("user", "alpha")      # current
        self.make_theme("system", "nord")
        theme_core.save_hidden({"alpha", "nord"})     # e.g. a list from before the rule
        self.app.open()
        self.assertNotIn("alpha", theme_core.load_hidden())          # repaired on open
        rows = self.rows_list()
        self.assertEqual([r.tm_theme.slug for r in rows], ["alpha"])
        self.assertFalse(rows[0].has_css_class("tm-row-hidden"))     # normal layout
        entries = self.menu_entries(theme)
        self.assertFalse(entries["Hide from list"].get_sensitive())
        self.app.set_theme_hidden(theme, True)
        self.assertNotIn("alpha", theme_core.load_hidden())

    def test_eye_resets_on_every_open(self):
        self.make_theme("system", "nord")
        theme_core.save_hidden({"nord"})
        self.app.toggle_show_hidden()
        self.assertTrue(self.app.show_hidden)
        self.app.open()
        self.assertFalse(self.app.show_hidden)
        self.assertNotIn("nord", [r.tm_theme.slug for r in self.rows_list()])


    def test_theme_previews_have_one_size_whatever_the_badges(self):
        Gtk = gi.repository.Gtk
        import shutil
        src = "/usr/share/omarchy/themes/tokyo-night/preview.png"
        for scope, slug in (("user", "alpha"), ("system", "nord")):   # alpha: current + no system badge
            tdir = self.make_theme(scope, slug).path
            shutil.copy(src, tdir / "preview.png")
        self.app.refresh_themes()
        window = Gtk.Window()
        window.set_child(self.app.card.get_parent())  # lay the rows out at the real width
        sizes = []
        for row in self.rows_list():
            row.measure(Gtk.Orientation.HORIZONTAL, -1)
            frame = row.get_child().get_first_child()
            picture = frame.get_first_child()
            self.assertIsInstance(picture, theme_popup.FixedPicture)
            sizes.append((picture.measure(Gtk.Orientation.HORIZONTAL, -1)[:2],
                          picture.measure(Gtk.Orientation.VERTICAL, -1)[:2]))
        self.assertEqual(sizes[0], sizes[1])
        w = self.app.theme_tile_w
        self.assertEqual(sizes[0], ((w, w), (w * 9 // 16, w * 9 // 16)))
        self.assertGreater(w, theme_popup.PREVIEW_W)   # larger than the old list rows' previews


    def test_sync_off_leaves_omarchy_alone(self):
        theme_core.save_settings(False)
        theme = self.make_theme("system", "gamma")
        previews = self.switcher_dir / "previews"
        (previews / "gamma.png").symlink_to(self.roots.system_themes / "gamma")
        self.app.set_theme_hidden(theme, True)
        self.assertIn("gamma", theme_core.load_hidden())        # hidden in Theme Manager
        self.assertTrue((previews / "gamma.png").is_symlink())   # Omarchy's picker untouched
        beta = self.make_theme("user", "beta")
        self.app.perform_delete_theme(beta)
        self.assertEqual(self.refreshes, [])
        self.app.hidden = {"gamma"}
        self.app.refresh_themes()
        self.assertIn("Settings", self.app.hidden_hint.get_label())


    # -- 1.3.0: favorites, settings, resizing, undo, previews, workspaces ---------

    def test_favorites_come_first_with_a_star(self):
        for slug in ("alpha", "beta", "gamma"):
            self.make_theme("system", slug)
        self.app.refresh_themes()
        entries = self.menu_entries(next(t for t in theme_core.discover_themes() if t.slug == "gamma"))
        entries["Add to favorites"].emit("clicked")
        self.assertEqual([r.tm_theme.slug for r in self.rows_list()][0], "gamma")
        self.assertEqual(self.auto.load_favorites(), ["gamma"])
        gamma = self.rows_list()[0]
        self.assertTrue(any(isinstance(w, gi.repository.Gtk.Label) and w.get_label() == "★"
                            for w in self.descendants(gamma)))
        self.assertIn("Remove from favorites", self.menu_entries(gamma.tm_theme))

    def test_sort_by_recently_used(self):
        for slug in ("alpha", "beta", "gamma"):
            self.make_theme("system", slug)
        self.auto.record_recent("gamma", when=10.0)
        self.auto.record_recent("beta", when=5.0)
        self.app.save_setting(sort="recent")
        self.assertEqual([r.tm_theme.slug for r in self.rows_list()], ["gamma", "beta", "alpha"])

    def test_applying_records_recent_use(self):
        theme = self.make_theme("system", "gamma")
        self.app.apply_theme_action(theme)
        self.assertIn("gamma", self.auto.load_recent())

    def test_rows_with_star_and_badges_stay_within_the_card(self):
        Gtk = gi.repository.Gtk
        self.make_theme("system", "alpha")   # current (state says alpha), system
        self.auto.set_favorite("alpha", True)
        self.app.refresh_themes()
        page = self.app.stack.get_child_by_name("themes")
        self.assertLessEqual(page.measure(Gtk.Orientation.HORIZONTAL, -1)[1], theme_popup.CARD_WIDTH)

    def test_settings_page_saves_choices(self):
        Gdk = gi.repository.Gdk
        self.make_theme("system", "day-one")
        self.make_theme("system", "night-one")
        self.app.open_settings()
        self.assertEqual(self.app.stack.get_visible_child_name(), "settings")
        self.app.save_setting(slideshow_minutes=15, workspace_wallpapers=True)
        self.app.save_schedule(day="day-one", night="night-one", night_at="20:30")
        self.app.set_schedule_enabled(True)
        self.assertEqual(self.auto.slideshow_minutes(), 15)
        self.assertTrue(self.auto.workspace_wallpapers_enabled())
        plan = self.auto.schedule()
        self.assertEqual((plan["enabled"], plan["day"], plan["night"], plan["night_at"]),
                         (True, "day-one", "night-one", "20:30"))
        # Rebuilt controls show the saved values; building them saves nothing.
        before = theme_core.settings_file().read_text()
        self.app.fill_settings()
        self.assertEqual(theme_core.settings_file().read_text(), before)
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")

    def test_information_page_lists_the_other_plugins(self):
        Gdk = gi.repository.Gdk
        self.app.open_info()
        self.assertEqual(self.app.stack.get_visible_child_name(), "info")
        self.assertFalse(self.app.info_button.get_visible())
        self.assertFalse(self.app.settings_button.get_visible())
        page = self.app.stack.get_child_by_name("info")
        tips = []
        stack = [page]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.Button) and widget.has_css_class("tm-more-card"):
                tips.append(widget.get_tooltip_text())
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        self.assertEqual(len(tips), 12)
        self.assertFalse(any("Theme Manager" in tip for tip in tips), "no card for itself")
        self.assertTrue(any("Maprisma Launcher" in tip for tip in tips))
        self.app.on_key(None, Gdk.KEY_BackSpace, 0, 0)
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
        self.assertTrue(self.app.info_button.get_visible())
        self.app.open_info()
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")

    def test_schedule_theme_from_the_theme_menu(self):
        theme = self.make_theme("system", "gamma")
        self.menu_entries(theme)["Use as night theme"].emit("clicked")
        self.assertEqual(self.auto.schedule()["night"], "gamma")
        self.assertIn("This is the night theme", self.menu_entries(theme))

    def test_resize_changes_columns_and_is_saved(self):
        Gdk = gi.repository.Gdk
        theme = self.make_theme("system", "gamma", tuple(f"w{i}.png" for i in range(6)))
        self.app.open_wallpapers_for(theme)
        self.assertEqual(self.app.wp_grid.get_max_children_per_line(), 2)
        self.app.resize_to(1300, 700, save=True)   # columns about TILE_TARGET wide
        self.assertEqual(self.app.columns, 4)
        self.assertEqual(self.app.wp_grid.get_max_children_per_line(), 4)
        self.assertGreater(self.app.tile_w, theme_popup.TILE)   # larger previews, not only more
        self.assertEqual(theme_core.window_size(), (1300, 700))
        # Left from the middle column moves left instead of leaving the page.
        tiles = self.children(self.app.wp_grid)
        tiles[2].grab_focus()
        self.app.window.set_focus(tiles[2])
        self.app.on_key(None, Gdk.KEY_Left, 0, 0)
        self.assertEqual(self.app.stack.get_visible_child_name(), "wallpapers")
        tiles[4].grab_focus()
        self.app.window.set_focus(tiles[4])          # first tile of the second row
        self.app.on_key(None, Gdk.KEY_Left, 0, 0)
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
        self.app.resize_to(10, 10)                     # never below the minimum
        self.assertEqual((self.app.card_w, self.app.list_h), theme_core.MIN_WINDOW_SIZE)
        self.app.resize_to(theme_popup.CARD_WIDTH, theme_popup.LIST_HEIGHT, save=True)
        self.assertIsNone(theme_core.window_size())    # the default size is not stored

    def test_theme_tiles_show_their_names(self):
        Gtk = gi.repository.Gtk
        for slug in ("alpha", "tokyo-night"):
            self.make_theme("system", slug)
        self.app.resize_to(700, 600)
        self.app.refresh_themes()
        tile = next(r for r in self.rows_list() if r.tm_theme.slug == "tokyo-night")
        name = next(w for w in self.descendants(tile) if isinstance(w, Gtk.Label) and w.has_css_class("tm-tile-name"))
        self.assertEqual(name.get_label(), "Tokyo Night")
        caption = name.get_parent()
        self.assertEqual(caption.get_halign(), Gtk.Align.FILL)   # a centered box gave the name no room

    def test_double_click_on_title_resets_size_and_centers(self):
        self.app.resize_to(900, 700, save=True)
        self.app.move_to((50, 60), save=True)
        self.app.on_move_begin(None, 0, 0)
        self.app.on_move_end(None, 0, 0)
        self.assertEqual(theme_core.window_position(), (50, 60))   # one press: nothing changes
        self.app.on_move_begin(None, 0, 0)                          # the second press
        self.assertEqual((self.app.card_w, self.app.list_h), (theme_popup.CARD_WIDTH, theme_popup.LIST_HEIGHT))
        self.assertIsNone(self.app.position)
        self.assertIsNone(theme_core.window_position())
        self.assertIsNone(theme_core.window_size())

    def test_preview_changes_offer_undo(self):
        from contextlib import nullcontext
        import workspace_state
        import workspace_tiling
        store_file = Path(self.tmp.name) / "workspaces.json"
        with patch.object(workspace_tiling, "store_path", return_value=store_file), \
                patch.object(workspace_tiling, "runtime_lock", lambda: nullcontext()), \
                patch.object(workspace_tiling, "wp_on_bar", return_value=False), \
                patch.object(workspace_tiling, "apply_layout_preset", return_value={"status": "ok"}) as apply, \
                patch.object(workspace_tiling, "hypr", return_value="[]"), \
                patch.object(workspace_tiling, "omarchy_workspace_modes", return_value={}):
            workspace_tiling.save_store({"maxWindows": 3})
            self.app.ws_store = workspace_tiling.load_store()
            self.app.ws_commit("Max 3 · Stacked", 2, workspace_state.set_preset, 2, "stacked")
            self.assertEqual(workspace_tiling.load_store()["workspaceAutoPresets"], {"2": "stacked"})
            self.assertTrue(self.app.undo_bar.get_reveal_child())
            self.app.undo_delete()                                   # the bar's Undo (and Ctrl+Z)
            self.assertNotIn("2", workspace_tiling.load_store().get("workspaceAutoPresets", {}))
            self.assertEqual(apply.call_args_list[-1].args[:2], (2, "master-left"))   # laid out again

    def test_narrow_large_preview_adds_a_window_and_stays_open(self):
        from contextlib import nullcontext
        import workspace_tiling
        store_file = Path(self.tmp.name) / "workspaces.json"
        with patch.object(workspace_tiling, "store_path", return_value=store_file), \
                patch.object(workspace_tiling, "runtime_lock", lambda: nullcontext()), \
                patch.object(workspace_tiling, "wp_on_bar", return_value=False), \
                patch.object(workspace_tiling, "apply_layout_preset", return_value={"status": "ok"}), \
                patch.object(workspace_tiling, "hypr", return_value="[]"), \
                patch.object(workspace_tiling, "omarchy_workspace_modes", return_value={}):
            workspace_tiling.save_store({"maxWindows": 2})
            self.app.ws_store, self.app.ws_modes = workspace_tiling.load_store(), {}
            self.app.presets_tab = "workspaces"
            rects, dashed = self.app.ws_card_rects(3, "workspaces")
            self.app.ws_zoom(3, "workspaces", None, rects, dashed)        # narrow: the large preview
            self.assertTrue(self.app.ws_preview_open())
            self.assertEqual(self.app.ws_selected, 3)                     # it edits that workspace
            picture = self.app.ws_hover[1]
            self.assertTrue(picture.plus_visible)
            cx, cy, _r = picture.plus_box()
            self.app.ws_panel_begin(picture, "workspaces", cx, cy)        # a click on +
            self.assertEqual(self.app.ws_drag["mode"], "add")
            self.app.ws_panel_end(picture, "workspaces", 0, 0)
            self.assertEqual(workspace_tiling.load_store()["workspaceCaps"], {"3": 3})
            self.assertTrue(self.app.ws_preview_open())                   # drawn again, not closed
            self.assertEqual(len(self.app.ws_hover[1].rects), 3)
            # Dragged into the left 30 %: Master Left for 4 windows.
            picture = self.app.ws_hover[1]
            cx, cy, _r = picture.plus_box()
            self.app.ws_panel_begin(picture, "workspaces", cx, cy)
            width = picture.get_width() or picture.get_content_width()
            top, area_h = picture.area()
            self.app.ws_drag["moved"] = True
            self.app.ws_add_update(picture, self.app.ws_drag, width * 0.1, top + area_h * 0.5)
            self.assertEqual(self.app.ws_drag["preset"], "master-left")
            self.app.ws_panel_end(picture, "workspaces", 0, 0)
            store = workspace_tiling.load_store()
            self.assertEqual((store["workspaceCaps"]["3"], store["workspaceAutoPresets"]["3"]), (4, "master-left"))
            self.assertFalse(self.app.ws_hover[1].plus_visible)          # 4 is the most
            # Dropped outside the preview: nothing changes.
            self.app.ws_store["workspaceCaps"]["3"] = 2
            workspace_tiling.save_store(self.app.ws_store)
            store_before = workspace_tiling.load_store()
            picture.plus_visible = True
            self.app.ws_panel_begin(picture, "workspaces", cx, cy)
            self.app.ws_drag["moved"] = True
            self.app.ws_add_update(picture, self.app.ws_drag, -50, -50)
            self.app.ws_panel_end(picture, "workspaces", 0, 0)
            self.assertEqual(workspace_tiling.load_store(), store_before)

    def test_minus_takes_one_window_away_then_the_default_limit(self):
        from contextlib import nullcontext
        import workspace_tiling
        store_file = Path(self.tmp.name) / "workspaces.json"
        with patch.object(workspace_tiling, "store_path", return_value=store_file), \
                patch.object(workspace_tiling, "runtime_lock", lambda: nullcontext()), \
                patch.object(workspace_tiling, "wp_on_bar", return_value=False), \
                patch.object(workspace_tiling, "apply_layout_preset", return_value={"status": "ok"}), \
                patch.object(workspace_tiling, "hypr", return_value="[]"), \
                patch.object(workspace_tiling, "omarchy_workspace_modes", return_value={}):
            workspace_tiling.save_store({"maxWindows": 3, "workspaceCaps": {"4": 2}})
            self.app.ws_store, self.app.ws_modes = workspace_tiling.load_store(), {}
            self.app.presets_tab = "workspaces"
            rects, dashed = self.app.ws_card_rects(4, "workspaces")
            self.app.ws_zoom(4, "workspaces", None, rects, dashed)
            for expected in ({"4": 1}, {}):
                picture = self.app.ws_hover[1]
                self.assertTrue(picture.minus_visible)
                cx, cy, _r = picture.minus_box()
                self.assertFalse(picture.plus_hit(cx, cy))
                self.app.ws_panel_begin(picture, "workspaces", cx, cy)
                self.assertEqual(self.app.ws_drag["mode"], "minus")
                self.app.ws_panel_end(picture, "workspaces", 0, 0)
                self.assertEqual(workspace_tiling.load_store().get("workspaceCaps", {}), expected)

    def test_app_preset_preview_resizes_and_adds_apps(self):
        from contextlib import nullcontext
        import workspace_tiling
        store_file = Path(self.tmp.name) / "workspaces.json"
        windows = [{"class": "a", "at": [0, 0], "size": [950, 1000], "floating": False},
                   {"class": "b", "at": [960, 0], "size": [960, 1000], "floating": False},
                   {"class": "f", "at": [300, 300], "size": [400, 300], "floating": True}]
        with patch.object(workspace_tiling, "store_path", return_value=store_file), \
                patch.object(workspace_tiling, "runtime_lock", lambda: nullcontext()), \
                patch.object(workspace_tiling, "wp_on_bar", return_value=False), \
                patch.object(workspace_tiling, "apply_layout_preset", return_value={"status": "ok"}), \
                patch.object(workspace_tiling, "hypr", return_value="[]"), \
                patch.object(workspace_tiling, "omarchy_workspace_modes", return_value={}):
            workspace_tiling.save_store({"maxWindows": 2, "workspaceModes": {"6": "manual"},
                                         "workspaceLayouts": {"6": {"mode": "manual", "apps": [{"class": "a"}],
                                                                    "windows": windows, "count": 3}}})
            self.app.ws_store, self.app.ws_modes = workspace_tiling.load_store(), {}
            self.app.presets_tab = "apps"
            rects, dashed = self.app.ws_card_rects(6, "apps")
            self.app.ws_zoom(6, "apps", None, rects, dashed)
            picture = self.app.ws_hover[1]
            self.assertTrue(picture.plus_visible)                         # + adds an app here
            width = picture.get_width() or picture.get_content_width()
            top, area_h = picture.area()
            scale = 1 - 2 * 0.04
            line_x = (955 / 1920 * scale + 0.04) * width
            handle = self.app.ws_saved_handle(picture, 6, line_x, top + area_h * 0.9)   # below the floating one
            self.assertEqual(handle, ("cut", 0, 950, 960))
            self.app.ws_panel_begin(picture, "apps", line_x, top + area_h * 0.9)
            self.app.ws_drag["moved"] = True
            target_x = ((1155 / 1920) * scale + 0.04) * width
            self.app.ws_saved_update(picture, self.app.ws_drag, target_x, top + area_h * 0.9)
            self.app.ws_panel_end(picture, "apps", 0, 0)
            saved = workspace_tiling.load_store()["workspaceLayouts"]["6"]["windows"]
            self.assertAlmostEqual(saved[0]["size"][0], 1150, delta=15)
            self.assertEqual(saved[0]["size"][0] + 10, saved[1]["at"][0])     # the other side moved along
            self.assertEqual(saved[2], windows[2])
            self.assertTrue(self.app.undo_bar.get_reveal_child())               # offered for Undo
            # The floating window's corner.
            picture = self.app.ws_hover[1]
            r = picture.rects[2]
            cx, cy = (r[0] + r[2]) * width, top + (r[1] + r[3]) * area_h
            self.assertEqual(self.app.ws_saved_handle(picture, 6, cx, cy), ("corner", 2))
            # + on an App Preset opens the app list (nothing is saved before a choice).
            picked = []
            with patch.object(self.app, "ws_pick_app", side_effect=lambda *a: picked.append(a[1:])):
                px, py, _r = picture.plus_box()
                self.app.ws_panel_begin(picture, "apps", px, py)
                self.app.ws_panel_end(picture, "apps", 0, 0)
            self.assertEqual(picked, [(6, None)])

    def test_app_list_keeps_its_keys_and_esc_closes_only_it(self):
        class FakePopover:
            closed = 0

            def get_visible(self):
                return True

            def popdown(self):
                FakePopover.closed += 1
        self.app.ws_app_popover = FakePopover()
        with patch.object(self.app, "ws_hover_hide") as hide:
            for key in (Gdk.KEY_space, Gdk.KEY_BackSpace, Gdk.KEY_Return, Gdk.KEY_a):
                self.assertFalse(self.app.on_key(None, key, 0, 0))     # to the search field
            self.assertTrue(self.app.on_key(None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(FakePopover.closed, 1)
        hide.assert_not_called()                                         # the preview stays
        self.app.ws_app_popover = None

    def test_presets_page_chooses_the_current_workspace(self):
        import theme_auto
        self.app.ws_selected = 2
        with patch.object(theme_auto, "active_workspace", return_value=7), \
                patch.object(self.app, "ws_reload"), patch.object(self.app, "refresh_presets"):
            self.app.open_presets("workspaces")
        self.assertEqual((self.app.ws_selected, self.app.ws_group), (7, 1))
        with patch.object(theme_auto, "active_workspace", return_value=None), \
                patch.object(self.app, "ws_reload"), patch.object(self.app, "refresh_presets"):
            self.app.open_presets("workspaces")                 # no Hyprland answer: the choice stays
        self.assertEqual(self.app.ws_selected, 7)
        # Closed and opened again from the bar on another workspace: that one.
        with patch.object(theme_auto, "active_workspace", return_value=3), \
                patch.object(self.app, "ws_reload") as reload, \
                patch.object(self.app.stack, "get_visible_child_name", return_value="presets"), \
                patch.object(self.app, "grab_keyboard"), patch.object(self.app.window, "present"):
            self.app.open()
        self.assertEqual((self.app.ws_selected, self.app.ws_group), (3, 0))
        reload.assert_called()

    def test_ask_wallpaper_button_opens_searches_and_closes(self):
        Gtk = gi.repository.Gtk
        walls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        candidate = {"query": "dog night city"}
        made = []
        with patch.object(self.app, "ask_wallpaper_row", side_effect=lambda c, *_a: made.append(c["query"]) or Gtk.Label()):
            self.app.ask_wallpaper_search(walls, candidate, None)          # opens the field
            self.assertIsNotNone(walls.tm_entry)
            walls.tm_entry.set_text("husky snow")
            self.app.ask_wallpaper_search(walls, candidate, None)          # new words: searched
            self.assertEqual(made, ["husky snow"])
            self.app.ask_wallpaper_search(walls, candidate, None)          # same words: closed again
        self.assertIsNone(walls.tm_entry)
        self.assertEqual(len(self.children(walls)), 1)                     # only the pictures row

    def test_ask_card_follows_your_own_wallpaper_search(self):
        import ask_page
        import wallpaper_search
        theme = self.make_theme("system", "gamma")
        colors = {"background": "#101820", "foreground": "#e0e0e0", "accent": "#1e66f5",
                  "selection": "#334455", "muted": "#778899", "red": "#ff0000"}
        new = {"kind": "new", "base": "gamma", "base_name": "Gamma", "name": "Blue Pup", "colors": colors,
               "font": None, "query": "dog night city", "reason": "only needs a dog wallpaper"}
        edit = dict(new, kind="edit", name="Gamma")
        searched = []

        busy_seen = []

        def search(phrase, _page):
            searched.append(phrase)
            busy_seen.append(any(c.get_child().tm_busy.get_visible()
                                 for c in self.children(self.app.ask_results)))
            return ([{"id": "x"}], 1) if phrase == "cute girl" else ([], 0)
        with patch.object(wallpaper_search, "search", side_effect=search), \
                patch.object(self.app, "_async", side_effect=lambda work, done: done(work(), None)), \
                patch.object(wallpaper_search, "fetch_thumb", return_value=None):
            self.app.ask_candidates = [new, edit]
            self.app.ask_show()
            cards = [c.get_child() for c in self.children(self.app.ask_results)]
            card = cards[0]
            self.assertEqual(card.tm_title.get_label(), "Blue Pup")
            self.app.ask_refine_row(card, new)
            self.assertIn("dog night city", card.tm_refine.get_placeholder_text())
            walls = card.tm_reason.get_prev_sibling()
            self.app.ask_wallpaper_search(walls, new, None, card)
            walls.tm_entry.set_text("cute girl portrait dark")
            busy_seen.clear()
            walls.tm_run()
            self.assertTrue(busy_seen and all(busy_seen))                   # a spinner on the card while searching
            self.assertFalse(card.tm_busy.get_visible())                    # and gone when done
            self.assertEqual(new["query"], "cute girl portrait dark")      # the fallback does not shorten it
            self.assertEqual(new["found_query"], "cute girl")
            self.assertTrue(new["query_by_user"])
            self.assertEqual(card.tm_title.get_label(), "Cute girl portrait dark")
            self.assertEqual(new["name"], "Cute girl portrait dark")        # the editor's name for a new theme
            self.assertIn("cute girl portrait dark", card.tm_reason.get_label())
            self.assertNotIn("dog", card.tm_refine.get_placeholder_text())
            self.assertIn("cute girl portrait dark", card.tm_refine.get_placeholder_text())
            card = cards[1]                                                  # edit: the theme keeps its name
            walls = card.tm_reason.get_prev_sibling()
            self.app.ask_wallpaper_search(walls, edit, None, card)
            walls.tm_entry.set_text("husky snow")
            walls.tm_run()
            self.assertEqual(edit["name"], "Gamma")
            self.assertEqual(card.tm_title.get_label(), "Husky snow")
            self.assertEqual(card.tm_kind.get_label(), "Edit Gamma")
            card = [c.get_child() for c in self.children(self.app.ask_results)][0]
            walls = card.tm_reason.get_prev_sibling()
            walls.tm_entry.set_text("猫 夜")                                   # no letters for a slug: the agent's name
            walls.tm_run()
            self.assertEqual(new["query"], "猫 夜")
            self.assertEqual(new["name"], "Blue Pup")
            self.assertEqual(card.tm_title.get_label(), "Blue Pup")
            self.app.ask_rejected = []
            self.app.ask_reject(new)                                          # your search is not "rejected"
            self.assertEqual(self.app.ask_rejected, ["Blue Pup"])
        self.assertEqual(ask_page.AskPageMixin.ask_refine_example({"query": ""}).count("{0}"), 0)

    def test_refine_keeps_the_card_when_nothing_visible_changed_and_reopens_its_field(self):
        import theme_ai
        self.make_theme("system", "gamma")
        colors = {"background": "#101820", "foreground": "#e0e0e0", "accent": "#1e66f5"}
        parent = {"kind": "edit", "base": "gamma", "base_name": "Gamma", "name": "Gamma", "colors": colors,
                  "font": None, "query": "", "reason": "r"}
        self.app.ask_ready = [{"id": "codex", "name": "Codex"}]
        self.app.ask_candidates = [parent]
        self.app.ask_show()
        same = dict(parent, colors=dict(colors, background="#121a22"))
        darker = dict(parent, colors=dict(colors, background="#000000"))
        focused = []
        with patch.object(self.app, "ask_helper_id", return_value="codex"), \
                patch.object(self.app, "_async", side_effect=lambda work, done: done(work(), None)), \
                patch.object(theme_ai, "ask", return_value=("s", [same])) as ask:
            self.app.ask_now(parent, "darker")
            self.assertIs(ask.call_args.args[4], parent)                 # the card goes along as parent
            self.assertIn("Nothing visible changed", self.app.ask_status.get_label())
            self.assertEqual(self.app.ask_candidates, [parent])
            card = self.children(self.app.ask_results)[0].get_child()
            self.assertIsNotNone(card.tm_refine)                         # its field is there again
            ask.return_value = ("s", [])                                 # verify dropped the no-op
            self.app.ask_now(parent, "darker")
            self.assertIn("Nothing visible changed", self.app.ask_status.get_label())
            card = self.children(self.app.ask_results)[0].get_child()
            self.assertFalse(card.tm_busy.get_visible())                 # the no-op ended its spinner
            seen = []
            ask.side_effect = lambda *a: seen.append((card.tm_busy.get_visible(), card.tm_busy_label.get_label())) \
                or ("Made darker.", [darker])
            self.app.ask_now(parent, "much darker")
            self.assertEqual(seen, [(True, "Refining with Codex…")])
        self.assertEqual(self.app.ask_candidates, [darker])
        card = self.children(self.app.ask_results)[0].get_child()
        self.assertIsNotNone(card.tm_refine)                             # refine again right there
        with patch.object(gi.repository.Gtk.Entry, "grab_focus") as grab:
            self.app.ask_refine_row(card, darker)                        # open already: focused again
        grab.assert_called_once()

    def test_ask_helper_hint_has_its_own_line_and_long_names_shorten(self):
        import theme_ai
        long_name = {"id": "pi:llama/Qwen3.8-27B-Q4_K_M", "name": "pi · Qwen3.8-27B-Q4_K_M", "local": True}
        with patch.object(theme_ai, "ready_helpers", return_value=[long_name]):
            self.app.set_ask_ai(True)
            self.app.open_ask()
        self.assertIs(self.app.ask_cost.get_parent(), self.app.ask_helper_drop.get_parent().get_parent())
        self.assertIn("On this PC", self.app.ask_cost.get_label())
        self.assertEqual(self.app.ask_helper_drop.get_valign(), gi.repository.Gtk.Align.CENTER)
        self.assertIsNotNone(self.app.ask_helper_drop.get_list_factory())
        self.app.set_ask_ai(False)

    def test_local_model_memory_is_given_back_on_switch_close_and_after_a_running_ask(self):
        import theme_ai
        qwen = {"provider": "llama", "model": "qwen", "host": "127.0.0.1", "port": 8080}
        released = []
        with patch.object(theme_ai, "owned_models", return_value=[("127.0.0.1", 8080, "qwen")]), \
                patch.object(theme_ai, "local_model", side_effect=lambda h: qwen if h == "pi:llama/qwen" else None), \
                patch.object(theme_ai, "release_local", side_effect=lambda keep=None: released.append(keep)), \
                patch.object(self.app, "_async", side_effect=lambda work, done: done(work(), None)):
            self.app.ask_busy = False
            self.app.ask_release("pi:llama/qwen")                       # the same model again: kept
            self.app.ask_release("claude")                              # an online agent: all of it back
            self.assertEqual(released, [("127.0.0.1", 8080, "qwen"), None])
            released.clear()
            self.app.ask_busy = True                                    # thinking: after the answer
            self.app.ask_release(None)
            self.assertEqual(released, [])
            self.assertEqual(self.app.ask_release_pending, (None,))
            self.app.ask_busy = False
            with patch.object(self.app, "grab_keyboard", create=True):
                self.app.close()                                        # hidden: memory back
            self.assertEqual(released, [None])
        with patch.object(theme_ai, "owned_models", return_value=[]), \
                patch.object(theme_ai, "release_local") as nothing:
            self.app.ask_release(None)                                  # nothing loaded: no work at all
            self.app.ask_busy = True                                    # a first Ask still loading: queued
            self.app.ask_release(None)
            self.assertEqual(self.app.ask_release_pending, (None,))
            self.app.ask_busy = False
            self.app.ask_release_pending = None
        nothing.assert_not_called()

    def test_ask_clear_button_empties_the_results(self):
        self.make_theme("system", "gamma")
        colors = {"background": "#101820", "foreground": "#e0e0e0", "accent": "#1e66f5"}
        card = {"kind": "edit", "base": "gamma", "base_name": "Gamma", "name": "Gamma", "colors": colors,
                "font": None, "query": "", "reason": "r"}
        self.app.ask_busy = False
        self.app.ask_show()
        self.assertFalse(self.app.ask_clear.get_visible())              # nothing to clear
        self.app.ask_candidates = [card]
        self.app.ask_rejected = ["old"]
        self.app.ask_status.set_label("Here are three options.")
        self.app.ask_show()
        self.assertTrue(self.app.ask_clear.get_visible())
        self.app.ask_busy = True
        self.app.ask_clear_results()                                    # not while an Ask runs
        self.assertEqual(len(self.children(self.app.ask_results)), 1)
        self.app.ask_busy = False
        self.app.ask_clear.emit("clicked")
        self.assertEqual((self.app.ask_candidates, self.app.ask_rejected, self.app.ask_status.get_label()), ([], [], ""))
        self.assertEqual(self.children(self.app.ask_results), [])
        self.assertFalse(self.app.ask_clear.get_visible())
        self.app.ask_status.set_label("No answer: timeout")              # an error alone can be cleared too
        self.assertTrue(self.app.ask_clear.get_visible())
        self.app.ask_clear_results()
        self.assertFalse(self.app.ask_clear.get_visible())

    def test_use_on_workspace_menus_start_on_the_current_workspace(self):
        import theme_auto
        from types import SimpleNamespace
        Gtk = gi.repository.Gtk
        theme = self.make_theme("user", "alpha", ("a1.png",))
        self.app.refresh_themes()
        self.app.open_wallpapers_for(theme)
        tiles = self.wallpaper_tiles()
        self.assertEqual(len(tiles), 1)
        popover = SimpleNamespace(tm_focus=None, popdown=lambda: None)
        with patch.object(theme_auto, "active_workspace", return_value=3), \
                patch.object(theme_auto, "load_workspace_themes", return_value={}):
            self.app.build_theme_workspace_menu(popover, theme)
            self.assertIn("3", popover.tm_focus.get_child().get_label())
            self.assertTrue(popover.tm_focus.has_css_class("tm-ws-here"))
            self.assertIn("(current workspace)", popover.tm_focus.get_tooltip_text())
            popover.tm_focus = None
            self.app.build_workspace_menu(popover, tiles[0].tm_wallpaper)   # the wallpaper's menu too
            self.assertEqual(popover.tm_focus.get_label(), "3")
        with patch.object(theme_auto, "active_workspace", return_value=None), \
                patch.object(theme_auto, "load_workspace_themes", return_value={}):
            popover.tm_focus = None
            self.app.build_theme_workspace_menu(popover, theme)
            self.assertEqual(popover.tm_focus.get_child().get_label(), "1")   # not known: starts on 1
            self.assertFalse(popover.tm_focus.has_css_class("tm-ws-here"))
        # ← moves left inside the grid; on the first column it still closes the menu.
        grid = Gtk.Grid()
        left, right = Gtk.Button(), Gtk.Button()
        grid.attach(left, 0, 0, 1, 1)
        grid.attach(right, 1, 0, 1, 1)
        for focus, moves in ((right, True), (left, False)):
            root = SimpleNamespace(get_focus=lambda f=focus: f)
            self.assertEqual(self.app.left_moves_in_grid(SimpleNamespace(get_root=lambda r=root: r)), moves)
        # show_menu starts on the named entry instead of the first one.
        button = Gtk.Button()
        with patch.object(Gtk.Popover, "popup"), patch.object(Gtk.Popover, "set_parent"), \
                patch.object(self.app, "focus_entry") as entry, patch.object(self.app, "focus_first_entry") as first:
            self.app.show_menu(Gtk.Button(), lambda pop: (setattr(pop, "tm_focus", button), Gtk.Box())[1])
            entry.assert_called_once_with(button)
            first.assert_not_called()
        self.app.actions_popover = None

    def test_current_theme_menu_offers_use_on_all_workspaces_with_undo(self):
        import theme_auto
        from types import SimpleNamespace
        Gtk = gi.repository.Gtk
        alpha = self.make_theme("user", "alpha")                         # the current theme
        beta = self.make_theme("user", "beta")

        def labels(theme):
            box = self.app.build_theme_menu(SimpleNamespace(popdown=lambda: None), theme)
            return [l.get_label() for l in self.descendants(box) if isinstance(l, Gtk.Label)]
        self.assertNotIn("Use on all workspaces", labels(alpha))         # no workspace themes yet
        theme_auto.set_workspace_theme(2, "beta")
        self.assertIn("Use on all workspaces", labels(alpha))
        self.assertNotIn("Use on all workspaces", labels(beta))          # only on the current theme
        theme_auto.set_workspace_theme(2, "alpha")
        self.assertIn("Use on all workspaces", labels(alpha))            # its own slug mapped: still clearable
        theme_auto.set_workspace_theme(2, "beta")
        self.app.use_theme_everywhere()
        self.assertEqual(theme_auto.load_workspace_themes(), {})
        self.assertIn("alpha", self.app.undo_label.get_label().lower())
        with patch.object(theme_auto, "active_workspace", return_value=1):
            self.app.undo_delete()
        self.assertEqual(theme_auto.load_workspace_themes(), {2: "beta"})
        theme_auto.set_workspace_theme(2, None)

    def test_autostart_copy_follows_what_the_page_shows(self):
        import workspace_state as state
        apps = [{"desktop_id": "foot", "class": "foot"}, {"desktop_id": "gimp", "class": "gimp"}]
        windows = [{"class": "foot", "at": [0, 0], "size": [900, 1000]}, {"class": "gimp", "at": [910, 0], "size": [900, 1000]}]
        layout = {"mode": "manual", "autostart": False, "apps": apps, "windows": windows}
        self.app.ws_store = {"workspaceLayouts": {"3": layout}, "workspaceModes": {"3": "manual"}}
        on = self.app.ws_autostart_changes(state.toggle_layout_flag, 3, "autostart")
        self.assertEqual([(ws, l["apps"]) for ws, l in on], [(3, apps)])          # switched on: the shown apps
        self.app.ws_store["workspaceLayouts"]["3"]["autostart"] = True
        removed = self.app.ws_autostart_changes(state.remove_app, 3, 1)
        self.assertEqual([(ws, [a["desktop_id"] for a in l["apps"]]) for ws, l in removed], [(3, ["foot"])])
        off = self.app.ws_autostart_changes(state.toggle_layout_flag, 3, "autostart")
        self.assertEqual(off, [(3, None)])                                         # switched off: forgotten
        self.assertEqual(self.app.ws_autostart_changes(state.set_split, 3, 0.6), [])  # nothing about apps

    def test_quick_removals_leave_the_copy_of_the_last_view(self):
        import tempfile
        import workspace_state as state
        import workspace_tiling as tiling
        apps = [{"desktop_id": d, "class": d} for d in ("foot", "gimp", "kitty")]
        windows = [{"class": d, "at": [i * 600, 0], "size": [590, 1000]} for i, d in enumerate(("foot", "gimp", "kitty"))]
        store = {"workspaceLayouts": {"3": {"mode": "manual", "autostart": True, "apps": apps, "windows": windows}},
                 "workspaceModes": {"3": "manual"}}
        disk = {"store": json.loads(json.dumps(store))}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), \
                patch.object(tiling, "runtime_lock"), \
                patch.object(tiling, "load_store", side_effect=lambda: json.loads(json.dumps(disk["store"]))), \
                patch.object(tiling, "save_store", side_effect=lambda s: disk.update(store=json.loads(json.dumps(s))) or s), \
                patch.object(self.app, "_async", side_effect=lambda work, done: done(work(), None)):
            self.app.ws_store = json.loads(json.dumps(store))
            self.app.ws_change(state.remove_app, 3, 2)                      # kitty
            self.app.ws_store = json.loads(json.dumps(store))              # a view one click behind
            self.app.ws_change(state.remove_app, 3, 1)                      # gimp
            copy = tiling.load_snapshots()["3"]
        self.assertEqual([a["desktop_id"] for a in copy["apps"]], ["foot"])   # neither removed app comes back

    def test_maprisma_hints_setting_puts_the_note_behind_a_help_button_or_removes_it(self):
        import theme_ai
        with patch.object(theme_ai, "ready_helpers", return_value=[]):
            self.app.open_ask()
            self.assertFalse(self.app.ask_maprisma.get_visible())        # default on: behind the ?
            self.assertTrue(self.app.ask_help.get_visible())
            self.app.ask_help.set_active(True)
            self.assertTrue(self.app.ask_maprisma.get_visible())
            self.app.open_ask()                                           # opened again: closed
            self.assertFalse(self.app.ask_maprisma.get_visible())
            self.app.set_maprisma_hints(False)
            self.app.open_ask()
        self.assertFalse(self.app.ask_maprisma.get_visible())            # off: no ? and no note
        self.assertFalse(self.app.ask_help.get_visible())
        self.app.set_maprisma_hints(True)

    def test_ask_suggestions_can_be_saved_edited_and_removed(self):
        import ask_page
        import theme_ai
        import theme_auto
        with patch.object(theme_ai, "ready_helpers", return_value=[]):
            self.app.open_ask()
        groups = lambda: self.children(self.app.ask_examples)
        self.assertEqual(len(groups()), len(ask_page.EXAMPLES))          # built-in, no reset button
        self.assertFalse(self.app.ask_prompt_reset.get_visible())
        self.app.ask_entry.set_text("  A   pink  theme ")
        self.app.ask_save_prompt()
        prompts = theme_auto.settings()["ask_prompts"]
        self.assertEqual(prompts[-1], "A pink theme")
        self.assertEqual(len(groups()), len(ask_page.EXAMPLES) + 1)      # + the new row
        self.assertTrue(self.app.ask_prompt_reset.get_visible())          # ↺ below the list
        self.app.ask_save_prompt()                                        # twice: not again
        self.assertEqual(len(theme_auto.settings()["ask_prompts"]), len(prompts))
        last = len(prompts) - 1
        self.app.ask_edit_prompt(last, groups()[last])
        index, entry = self.app.ask_prompt_edit
        entry.set_text("A pink light theme")
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)                       # Esc cancels the edit only
        self.assertEqual(self.app.stack.get_visible_child_name(), "ask")
        self.assertEqual(theme_auto.settings()["ask_prompts"][last], "A pink theme")
        self.app.ask_edit_prompt(last, groups()[last])
        self.app.ask_prompt_edit[1].set_text("A pink light theme")
        self.app.ask_finish_edit(True)
        self.assertEqual(theme_auto.settings()["ask_prompts"][last], "A pink light theme")
        self.app.ask_remove_prompt(0)
        self.assertNotIn(ask_page._(ask_page.EXAMPLES[0]), theme_auto.settings()["ask_prompts"])
        self.app.ask_store_prompts(None)                                  # reset
        self.assertEqual(ask_page.saved_prompts(theme_auto.settings()), [ask_page._(t) for t in ask_page.EXAMPLES])
        self.assertFalse(theme_ai.valid_prompts(["a", "a"]))
        self.assertFalse(theme_ai.valid_prompts(["x"] * 13))
        self.assertFalse(theme_ai.valid_prompts(["two\nlines"]))

    def test_ask_suggestions_reorder_by_drag_and_alt_arrows_in_a_two_row_card(self):
        import ask_page
        import theme_ai
        import theme_auto
        with patch.object(theme_ai, "ready_helpers", return_value=[]):
            self.app.open_ask()
        defaults = [ask_page._(t) for t in ask_page.EXAMPLES]
        self.app.card_w = 550
        self.app.ask_prompt_height()                                      # narrow: two rows tall
        two = 2 * ask_page.PROMPT_ROW + ask_page.PROMPT_GAP
        self.assertEqual(self.app.ask_prompt_scroller.get_max_content_height(), two)
        with patch.object(self.app, "size_limits", return_value=(3000, 2000)):
            self.app.resize_to(1400, 800)                                 # widened: four rows
        self.assertEqual(self.app.ask_prompt_scroller.get_max_content_height(),
                         4 * ask_page.PROMPT_ROW + 3 * ask_page.PROMPT_GAP)

        class Gesture:
            def __init__(self):
                self.reset_called = False

            def set_state(self, _state):
                pass

            def reset(self):
                self.reset_called = True
        # A drag that ends elsewhere moves the row; the refill waits for the drop.
        gesture = Gesture()
        rows = self.app.ask_prompt_rows
        self.app.ask_prompt_drag_begin(gesture, 1, 1, 0, rows[0].get_first_child())
        self.assertIsNotNone(self.app.ask_prompt_drag)
        self.app.ask_fill_prompts()                                       # held while dragging
        self.assertIs(self.app.ask_prompt_rows, rows)
        self.app.ask_prompt_drag["to"] = 2
        self.app.ask_prompt_drag_end(gesture, 0, 0)
        self.assertEqual(theme_auto.settings()["ask_prompts"], [defaults[1], defaults[2], defaults[0], defaults[3]])
        # Esc cancels a drag and nothing moves.
        self.app.ask_prompt_drag_begin(gesture, 1, 1, 0, self.app.ask_prompt_rows[0].get_first_child())
        self.app.ask_prompt_drag["to"] = 3
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)
        self.assertTrue(gesture.reset_called)
        self.assertIsNone(self.app.ask_prompt_drag)
        self.assertEqual(self.app.stack.get_visible_child_name(), "ask")
        self.assertEqual(theme_auto.settings()["ask_prompts"][0], defaults[1])
        # Alt+↓ on a focused row moves it one down.
        chip = self.app.ask_prompt_rows[0].get_first_child().get_next_sibling()
        with patch.object(self.app.window, "get_focus", return_value=chip):
            self.assertTrue(self.app.on_key(None, Gdk.KEY_Down, 0, Gdk.ModifierType.ALT_MASK))
        self.assertEqual(theme_auto.settings()["ask_prompts"][:2], [defaults[2], defaults[1]])
        self.app.ask_store_prompts(None)

    def test_ask_ai_cards_and_use_open_the_editor_prefilled(self):
        import ask_page
        import theme_ai
        theme = self.make_theme("system", "gamma")
        with patch.object(theme_ai, "ready_helpers", return_value=[{"id": "codex", "name": "Codex"}]):
            self.app.open_ask()                                   # off by default: it explains
            self.assertTrue(self.app.ask_off_box.get_visible())
            self.assertFalse(self.app.ask_button.get_sensitive())
            self.app.set_ask_ai(True)
            self.app.open_ask()
        self.assertEqual(self.app.stack.get_visible_child_name(), "ask")
        self.assertFalse(self.app.ask_off_box.get_visible())
        self.assertTrue(self.app.ask_button.get_sensitive())
        colors = {"background": "#101820", "foreground": "#e0e0e0", "accent": "#1e66f5",
                  "selection": "#334455", "muted": "#778899", "red": "#ff0000"}
        self.app.ask_candidates = [{"kind": "new", "base": "gamma", "base_name": "Gamma", "name": "Blue Dog",
                                    "colors": colors, "font": None, "query": "", "reason": "blue borders"}]
        self.app.ask_show()
        self.assertEqual(len(self.children(self.app.ask_results)), 1)
        card = self.children(self.app.ask_results)[0].get_child()
        buttons = [b for b in self.descendants(card) if isinstance(b, gi.repository.Gtk.Button)]
        labels = [l.get_label() for b in buttons for l in self.descendants(b) if isinstance(l, gi.repository.Gtk.Label)]
        self.assertEqual([l for l in labels if l in ("Use", "Not this", "Refine…", "Wallpaper…")],
                         ["Use", "Not this", "Refine…", "Wallpaper…"])
        texts = [l.get_label() for l in self.descendants(card) if isinstance(l, gi.repository.Gtk.Label)]
        self.assertIn("New theme", texts)
        self.assertFalse(any("Gamma" in t for t in texts))    # the base theme is internal (2026-11-09)
        asked = []
        with patch.object(self.app, "ask_now", side_effect=lambda *a, **k: asked.append(True)):
            self.app.ask_entry.grab_focus()
            self.app.on_key(None, Gdk.KEY_Return, 0, 0)       # Enter asks
        self.assertEqual(asked, [True])
        self.app.ask_use(self.app.ask_candidates[0], None)
        self.assertEqual(self.app.stack.get_visible_child_name(), "editor")
        self.assertEqual(self.app.ed["palette"].colors["accent"], "#1e66f5")
        self.assertEqual(self.app.ed["palette"].colors["red"], "#ff0000")
        self.assertIn("Blue Dog", self.app.ed_name.get_text())
        self.assertFalse(self.app.ed["edit"])             # a system theme is never changed: a copy
        self.assertEqual(self.app.ed_title.get_label(), "Blue Dog")          # the card's name comes over
        editor_texts = [l.get_label() for l in self.descendants(self.app.stack.get_child_by_name("editor"))
                        if isinstance(l, gi.repository.Gtk.Label)]
        self.assertFalse(any("Gamma" in t or "Take over its wallpapers" in t for t in editor_texts))
        self.assertTrue(self.app.ed["wallpapers"])        # no card picture: its base's wallpapers, silently
        # Saving downloads the picture the card showed into the new theme.
        import wallpaper_search
        self.app.ed["ask_wallpaper"] = "ITEM"
        fetched = []
        with patch.object(wallpaper_search, "download", side_effect=lambda roots, slug, item: fetched.append((slug, item))):
            self.app.save_editor(apply=False)
        self.assertEqual(fetched[0][1], "ITEM")
        self.assertTrue(fetched[0][0].startswith("blue-dog"))
        # Use with a card picture: the sample shows it (not the base theme's
        # wallpaper) and the base theme's wallpapers are only an extra.
        thumb = Path(self.tmp.name) / "card-thumb.jpg"
        thumb.write_bytes(jpeg())
        with patch.object(wallpaper_search, "thumb_path", return_value=thumb):
            self.app.ask_use(dict(self.app.ask_candidates[0], wallpaper="CARD"), None)
            self.assertEqual(self.app.ed["ask_wallpaper"], "CARD")
            self.assertFalse(self.app.ed["wallpapers"])
            self.assertEqual(self.app.editor_wallpaper(), thumb)
        self.app.close_editor()
        self.app.ask_use(self.app.ask_candidates[0], None)
        self.app.close_editor()                           # Back: to the Ask AI page, results kept
        self.assertEqual(self.app.stack.get_visible_child_name(), "ask")
        self.assertEqual(len(self.children(self.app.ask_results)), 1)
        # Not this: the card goes, and it is never suggested again in this ask.
        candidate = {"kind": "new", "base": "gamma", "base_name": "Gamma", "name": "Other",
                     "colors": colors, "font": None, "query": "", "reason": ""}
        self.app.ask_candidates = [candidate]
        self.app.ask_rejected = []
        self.app.ask_reject(candidate)
        self.assertEqual(self.app.ask_rejected, ["Other"])
        self.assertEqual(self.children(self.app.ask_results), [])
        self.assertFalse(ask_page.enabled({}))             # off by default
        self.assertTrue(self.app.ask_header_glyph.get_label())  # the sparkle is always in the header

    def test_wider_popup_shows_theme_cards_in_columns(self):
        Gdk = gi.repository.Gdk
        for slug in ("alpha", "beta", "gamma", "delta", "eps"):
            self.make_theme("system", slug)
        self.app.refresh_themes()
        self.assertEqual(self.app.rows.get_max_children_per_line(), 2)            # tiles already at the default
        self.assertTrue(self.rows_list()[0].get_child().has_css_class("tm-tile"))
        self.app.resize_to(1300, 600)
        self.assertEqual(self.app.rows.get_max_children_per_line(), 4)            # like the wallpapers
        self.assertTrue(self.rows_list()[0].get_child().has_css_class("tm-tile"))
        self.app.resize_to(480, 600)
        self.assertEqual(self.app.rows.get_max_children_per_line(), 2)
        self.app.select_row(self.rows_list()[0])                 # alpha, left column
        self.app.on_key(None, Gdk.KEY_Right, 0, 0)               # → moves to the right column
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "beta")
        self.assertIsNone(self.app.actions_popover)
        # alpha beta / delta eps / gamma
        self.app.on_key(None, Gdk.KEY_Down, 0, 0)                # ↓ goes one line down
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "eps")
        self.app.on_key(None, Gdk.KEY_Left, 0, 0)
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "delta")
        self.app.on_key(None, Gdk.KEY_Menu, 0, 0)                # the menu key still opens the menu
        self.assertIsNotNone(self.app.actions_popover)
        self.app.actions_popover.popdown()
        self.app.resize_to(theme_popup.CARD_WIDTH, 600)          # default width: two columns of tiles
        self.assertEqual(self.app.rows.get_max_children_per_line(), 2)
        self.assertTrue(self.rows_list()[0].get_child().has_css_class("tm-tile"))

    def test_delete_wallpaper_offers_undo_from_the_trash(self):
        trash = Path(self.tmp.name) / "Trash"
        self.roots = theme_core.Roots(self.roots.user_themes, self.roots.system_themes,
                                      self.roots.user_backgrounds, self.roots.state_current, trash=trash)
        theme = self.make_theme("user", "beta")
        path = self.make_wallpaper("beta", "keep.png")
        self.app.open_wallpapers_for(theme)
        info = next(w for w in theme_core.list_backgrounds(None, theme) if w.name == "keep.png")
        self.app.confirm_delete_wallpaper(info)
        self.assertIn("trash", self.app.confirm_label.get_label())
        self.app.accept_confirm()
        self.assertFalse(path.exists())
        self.assertTrue((trash / "files" / "keep.png").exists())
        self.assertTrue(self.app.undo_bar.get_reveal_child())
        Gdk = gi.repository.Gdk
        self.app.on_key(None, Gdk.KEY_z, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertTrue(path.exists())
        self.assertFalse(self.app.undo_bar.get_reveal_child())
        self.assertEqual([t.tm_wallpaper.name for t in self.wallpaper_tiles()], ["keep.png"])

    def test_delete_theme_offers_undo(self):
        trash = Path(self.tmp.name) / "Trash"
        self.roots = theme_core.Roots(self.roots.user_themes, self.roots.system_themes,
                                      self.roots.user_backgrounds, self.roots.state_current, trash=trash)
        theme = self.make_theme("user", "beta")
        self.app.refresh_themes()
        self.app.perform_delete_theme(theme)
        self.assertFalse(theme.path.exists())
        self.app.undo_delete()
        self.assertTrue(theme.path.is_dir())
        self.assertIn("beta", [r.tm_theme.slug for r in self.rows_list()])

    def test_space_previews_until_released(self):
        Gdk = gi.repository.Gdk
        theme = self.make_theme("system", "gamma", ("g1.png", "g2.png"))
        self.app.open_wallpapers_for(theme)
        tile = self.wallpaper_tiles()[1]
        self.app.window.set_focus(tile)
        self.app.on_key(None, Gdk.KEY_space, 0, 0)
        self.app.on_key(None, Gdk.KEY_space, 0, 0)     # auto-repeat while held
        self.assertEqual(self.previews, ["g2.png"])
        self.assertIsNone(self.app.actions_popover)    # no menu: Space previews now
        self.app.on_key_released(None, Gdk.KEY_space, 0, 0)
        self.assertEqual(self.previews, ["g2.png", "<restore>"])
        # From the menu: shown for a few seconds; closing the popup restores at once.
        self.wallpaper_menu(tile.tm_wallpaper)["Preview on desktop"].emit("clicked")
        self.app.close()
        self.assertEqual(self.previews[-2:], ["g2.png", "<restore>"])
        self.assertIsNone(self.app.preview)

    def test_web_preview_loads_the_full_image(self):
        Gdk = gi.repository.Gdk
        full = Path(self.tmp.name) / "full.jpg"
        full.write_bytes(PNG)
        orig = self.ws.fetch_preview
        self.ws.fetch_preview = lambda item: full
        try:
            theme = self.make_theme("user", "beta")
            self.app.open_wallpapers_for(theme)
            self.app.open_web_search()
            self.app.web_search.set_text("forest")
            self.app.run_web_search()
            tile = self.children(self.app.web_grid)[0]
            self.app.window.set_focus(tile)
            self.app.on_key(None, Gdk.KEY_space, 0, 0)
            self.assertEqual(self.previews, ["full.jpg"])
            self.app.on_key_released(None, Gdk.KEY_space, 0, 0)
            self.assertEqual(self.previews, ["full.jpg", "<restore>"])
        finally:
            self.ws.fetch_preview = orig

    def test_workspace_menu_assigns_and_clears(self):
        theme = self.make_theme("system", "alpha", ("a1.png", "a2.png"))  # the current theme
        self.app.open_wallpapers_for(theme)
        wallpaper = self.wallpaper_tiles()[0].tm_wallpaper
        self.assertIn("Show on workspace…", self.wallpaper_menu(wallpaper))
        self.app.assign_workspace(wallpaper, 3, True)
        self.assertEqual(self.auto.load_workspace_map(), {"alpha": {3: "a1.png"}})
        self.assertTrue(self.auto.workspace_wallpapers_enabled())     # switched on by the first use
        self.assertIn("On workspace 3…", self.wallpaper_menu(self.wallpaper_tiles()[0].tm_wallpaper))
        badge_labels = [w.get_label() for w in self.descendants(self.wallpaper_tiles()[0])
                        if isinstance(w, gi.repository.Gtk.Label)]
        self.assertIn("ws 3", badge_labels)
        self.active_ws = 3
        self.app.assign_workspace(wallpaper, 3, True)                 # in front: shown right away
        self.assertEqual(self.backgrounds_set[-1].rsplit("/", 1)[-1], "a1.png")
        self.app.clear_workspaces(wallpaper)
        self.assertEqual(self.auto.load_workspace_map(), {})

    def test_add_tile_opens_the_file_chooser(self):
        Gdk = gi.repository.Gdk
        theme = self.make_theme("user", "beta")
        self.make_wallpaper("beta", "one.png")
        self.app.open_wallpapers_for(theme)
        chosen = []
        self.app.choose_files = lambda: chosen.append(True)
        add = self.children(self.app.wp_grid)[0]
        self.assertTrue(add.tm_add)
        self.app.window.set_focus(add)
        self.app.on_key(None, Gdk.KEY_Return, 0, 0)
        self.app.wp_grid.emit("child-activated", add)
        self.assertEqual(chosen, [True, True])
        # Filtering hides the add tile.
        self.type_keys("one")
        self.assertFalse(any(getattr(t, "tm_add", False) for t in self.children(self.app.wp_grid)))

    def test_new_theme_from_a_theme(self):
        Gdk = gi.repository.Gdk
        base = self.make_theme("system", "gamma", ("g1.png", "g2.png"))
        (base.path / "colors.toml").write_text('accent = "#112233"\nbackground = "#000000"\n')
        self.menu_entries(base)["New theme from this…"].emit("clicked")
        self.assertEqual(self.app.stack.get_visible_child_name(), "editor")
        self.assertEqual(self.app.ed_name.get_text(), "My Gamma")
        self.assertEqual(self.app.ed["entries"]["accent"].get_text(), "#112233")
        entry = self.app.ed["entries"]["accent"]
        entry.set_text("40c0ff")                      # the # is optional
        entry.emit("activate")
        self.assertEqual(self.app.ed["palette"].colors["accent"], "#40c0ff")
        self.app.set_editor_color("red", "nonsense")
        self.assertIn("not a color", self.app.ed_hint.get_label())
        self.assertEqual(self.app.ed["entries"]["red"].get_text(), self.app.ed["palette"].colors["red"])
        self.app.ed_hint.set_label("")
        self.app.set_editor_font(2)                   # "Mono B"
        self.app.ed_name.set_text("Night Owl")
        self.app.save_editor(apply=True)
        folder = self.roots.user_themes / "night-owl"
        self.assertIn('accent = "#40c0ff"', (folder / "colors.toml").read_text())
        self.assertEqual(sorted(p.name for p in (folder / "backgrounds").iterdir()), ["g1.png", "g2.png"])
        self.assertEqual(self.applied, ["night-owl"])
        self.assertEqual(self.fonts_set, ["Mono B"])  # the theme's font, once applied
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "night-owl")
        # A custom theme can be edited again; other themes can't.
        mine = next(t for t in theme_core.discover_themes() if t.slug == "night-owl")
        self.assertIn("Edit theme…", self.menu_entries(mine))
        self.assertNotIn("Edit theme…", self.menu_entries(base))

    def test_edit_custom_theme_and_escape_cancels(self):
        Gdk = gi.repository.Gdk
        base = self.make_theme("system", "gamma")
        self.custom.create_theme(base, "Mine", self.custom.load_palette(base), None, wallpapers=False)
        mine = next(t for t in theme_core.discover_themes() if t.slug == "mine")
        self.app.open_editor(mine, edit=True)
        self.app.set_editor_color("background", "#ffffff")
        self.app.on_key(None, Gdk.KEY_Escape, 0, 0)
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
        self.assertNotIn("#ffffff", (mine.path / "colors.toml").read_text())
        self.app.open_editor(mine, edit=True)
        self.app.set_editor_color("background", "#ffffff")
        self.app.save_editor(apply=False)
        self.assertIn('background = "#ffffff"', (mine.path / "colors.toml").read_text())
        self.assertIn('mode = "light"', (mine.path / "colors.toml").read_text())
        self.assertEqual(self.applied, [])            # not in use: not applied

    def test_new_theme_from_a_wallpaper(self):
        base = self.make_theme("system", "gamma", ("1-misty-forest.png", "other.png"))
        self.app.open_wallpapers_for(base)
        tile = next(t for t in self.wallpaper_tiles() if t.tm_wallpaper.name == "1-misty-forest.png")
        self.wallpaper_menu(tile.tm_wallpaper)["New theme from this wallpaper…"].emit("clicked")
        self.assertEqual(self.app.stack.get_visible_child_name(), "editor")
        self.assertEqual(self.app.ed_name.get_text(), "Misty Forest")
        self.assertIn("1-misty-forest.png", self.app.ed_title.get_label())
        self.app.save_editor(apply=False)
        folder = self.roots.user_themes / "misty-forest"
        self.assertEqual([p.name for p in (folder / "backgrounds").iterdir()], ["1-misty-forest.png"])
        self.assertTrue((folder / "colors.toml").is_file())

    def test_rename_custom_theme_in_use(self):
        base = self.make_theme("system", "gamma")
        self.custom.create_theme(base, "Old Name", self.custom.load_palette(base), None, wallpapers=False)
        self.make_wallpaper("old-name", "mine.png")
        self.auto.set_favorite("old-name", True)
        self.state_current.write_text("old-name\n")
        mine = next(t for t in theme_core.discover_themes() if t.slug == "old-name")
        self.app.open_editor(mine, edit=True)
        self.assertEqual(self.app.ed_name.get_text(), "Old Name")
        self.app.ed_name.set_text("New Name")
        self.app.save_editor(apply=False)
        self.assertFalse((self.roots.user_themes / "old-name").exists())
        self.assertTrue((self.roots.user_themes / "new-name" / "colors.toml").is_file())
        self.assertTrue((self.roots.user_backgrounds / "new-name" / "mine.png").is_file())
        self.assertEqual(self.auto.load_favorites(), ["new-name"])
        self.assertEqual(self.applied, ["new-name"])            # it was in use: applied again
        self.assertEqual(self.app.rows.get_selected_row().tm_theme.slug, "new-name")

    def test_editor_refuses_a_taken_name(self):
        base = self.make_theme("system", "gamma")
        self.app.open_editor(base)
        self.app.ed_name.set_text("Gamma")
        self.app.save_editor(apply=False)
        self.assertIn("already exists", self.app.ed_hint.get_label())
        self.assertEqual(self.app.stack.get_visible_child_name(), "editor")

    def test_editor_page_fits_the_card(self):
        Gtk = gi.repository.Gtk
        base = self.make_theme("system", "gamma")
        self.app.open_editor(base)
        page = self.app.stack.get_child_by_name("editor")
        self.assertLessEqual(page.measure(Gtk.Orientation.HORIZONTAL, -1)[1], theme_popup.CARD_WIDTH)

    def press(self, keyval, *mods):
        Gdk = gi.repository.Gdk
        masks = {"SUPER": Gdk.ModifierType.SUPER_MASK, "SHIFT": Gdk.ModifierType.SHIFT_MASK,
                 "CTRL": Gdk.ModifierType.CONTROL_MASK, "ALT": Gdk.ModifierType.ALT_MASK}
        state = 0
        for mod in mods:
            state |= masks[mod]
        return self.app.on_key(None, keyval, 0, state)

    def test_theme_shortcut_from_its_menu(self):
        Gdk = gi.repository.Gdk
        theme = self.make_theme("system", "gamma")
        self.app.refresh_themes()
        self.menu_entries(theme)["Shortcut…"].emit("clicked")
        self.assertEqual(self.app.stack.get_visible_child_name(), "shortcut")
        self.assertIn("Apply Gamma", self.app.sc_title.get_label())
        self.assertEqual(self.captures, ["enter"])                     # bound combos reach the page
        self.press(Gdk.KEY_g, "SUPER", "CTRL")                         # held modifiers + a key
        self.assertEqual(self.app.current_combo(), {"mods": ["SUPER", "CTRL"], "key": "G"})
        self.assertIn("free", self.app.sc_status.get_label())
        self.press(Gdk.KEY_Return)                                     # Enter alone assigns
        lua = (self.hypr / "theme-manager-shortcuts.lua").read_text()
        self.assertIn('o.bind("SUPER + CTRL + G", "Theme Manager: Apply Gamma"', lua)
        self.assertIn("theme_auto.py apply gamma", lua)
        self.assertIn(self.sc.REQUIRE_LINE, (self.hypr / "hyprland.lua").read_text())
        self.assertIn("Shortcut: Super + Ctrl + G…", self.menu_entries(theme))
        self.press(Gdk.KEY_Escape)
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")
        self.assertEqual(self.captures[-1], "leave")
        row = next(r for r in self.rows_list() if r.tm_theme.slug == "gamma")
        self.assertIn("Super+Ctrl+G", [w.get_label() for w in self.descendants(row)
                                        if isinstance(w, gi.repository.Gtk.Label)])

    def test_taken_keys_are_coloured_and_can_be_rebound(self):
        Gdk = gi.repository.Gdk
        self.app.open_settings()
        self.app.open_shortcut_page("open")
        self.app.set_shortcut_combo(["SUPER", "CTRL"], None)
        chips = {c.get_child().get_label(): c.get_child() for c in self.children(self.app.sc_letters)}
        self.assertTrue(chips["N"].has_css_class("sc-yours-key"))      # your bindings.lua
        self.assertTrue(chips["T"].has_css_class("sc-free-key"))
        self.app.pick_shortcut_key("N")
        self.assertEqual(self.app.sc_apply.get_label(), "Rebind")
        self.app.apply_shortcut()
        self.assertEqual(self.sc.load_state()["open"]["replaces"], "Notes")
        self.assertIn('hl.unbind("SUPER + CTRL + N")', (self.hypr / "theme-manager-shortcuts.lua").read_text())
        # Omarchy's keys show as such.
        self.app.set_shortcut_combo(["SUPER"], "Return")
        self.assertIn("Omarchy", self.app.sc_status.get_label())
        # Remove gives the key back and takes the file and the line out again.
        self.app.remove_shortcut()
        self.assertEqual(self.sc.load_state(), {})
        self.assertFalse((self.hypr / "theme-manager-shortcuts.lua").exists())
        self.assertNotIn(self.sc.REQUIRE_LINE, (self.hypr / "hyprland.lua").read_text())
        self.press(Gdk.KEY_Escape)
        self.assertEqual(self.app.stack.get_visible_child_name(), "settings")

    def test_shortcut_needs_a_modifier_and_moves_between_actions(self):
        Gdk = gi.repository.Gdk
        self.app.open_shortcut_page("next-wallpaper")
        self.app.set_shortcut_combo([], None)
        self.press(Gdk.KEY_w)
        self.assertFalse(self.app.sc_apply.get_sensitive())
        self.app.set_shortcut_combo(["SUPER", "CTRL"], "W")
        self.app.apply_shortcut()
        self.app.close_shortcut_page()
        self.app.open_shortcut_page("prev-wallpaper")
        self.app.set_shortcut_combo(["SUPER", "CTRL"], "W")
        self.assertEqual(self.app.sc_apply.get_label(), "Move here")
        self.app.apply_shortcut()
        self.assertEqual(list(self.sc.load_state()), ["prev-wallpaper"])

    def test_deleted_theme_key_goes_when_undo_ends(self):
        trash = Path(self.tmp.name) / "Trash"
        self.roots = theme_core.Roots(self.roots.user_themes, self.roots.system_themes,
                                      self.roots.user_backgrounds, self.roots.state_current, trash=trash)
        theme = self.make_theme("user", "beta")
        self.sc.save_and_apply({"apply:beta": self.sc.make_combo(["SUPER", "CTRL"], "B")})
        self.app.refresh_themes()
        self.app.perform_delete_theme(theme)
        self.assertIn("apply:beta", self.sc.load_state())         # Undo could still bring it back
        self.app.undo_delete()
        self.assertIn("apply:beta", self.sc.load_state())
        self.assertIn("Super + Ctrl + B", (self.hypr / "theme-manager-shortcuts.lua").read_text()
                      .replace("SUPER", "Super").replace("CTRL", "Ctrl"))
        self.app.perform_delete_theme(next(t for t in theme_core.discover_themes() if t.slug == "beta"))
        self.app.end_undo()                                        # the undo time is over
        self.assertEqual(self.sc.load_state(), {})
        self.assertIn(("Shortcut removed", "The key of Beta is free again."), self.notices)

    def test_deleted_theme_without_trash_loses_its_key_at_once(self):
        theme = self.make_theme("user", "beta")
        self.sc.save_and_apply({"apply:beta": self.sc.make_combo(["SUPER", "CTRL"], "B")})
        self.app.perform_delete_theme(theme)
        self.assertEqual(self.sc.load_state(), {})

    def test_hidden_theme_key_is_marked(self):
        theme = self.make_theme("system", "gamma")
        self.sc.save_and_apply({"apply:gamma": self.sc.make_combo(["SUPER", "CTRL"], "G")})
        self.app.hidden = {"gamma"}
        self.assertIn("Shortcut: Super + Ctrl + G (works while hidden)…", self.menu_entries(theme))
        self.app.hidden = set()
        self.assertIn("Shortcut: Super + Ctrl + G…", self.menu_entries(theme))

    def test_follows_a_theme_change_made_elsewhere(self):
        self.make_theme("system", "alpha", ("a1.png",))
        gamma = self.make_theme("system", "gamma", ("g1.png",))
        self.app.current_slug = "alpha"
        self.app.refresh_themes()
        self.app.open_wallpapers_for(gamma)
        self.assertFalse(self.app.active_theme.current)
        self.state_current.write_text("gamma\n")          # e.g. gamma's key was pressed
        self.app.follow_theme_change()
        self.assertTrue(self.app.active_theme.current)
        tile = self.wallpaper_tiles()[0]
        self.assertIn("Set as background", self.wallpaper_menu(tile.tm_wallpaper))
        current = [r.tm_theme.slug for r in self.rows_list() if r.tm_theme.current]
        self.assertEqual(current, ["gamma"])

    def test_theme_name_changes_are_watched(self):
        self.app.watch_current_theme()
        self.assertIsNotNone(self.app.state_monitor)
        seen = []
        self.app.follow_theme_change = lambda: seen.append(True) or False
        Gio = gi.repository.Gio
        name = Gio.File.new_for_path(str(self.state_current))
        self.app.on_state_changed(None, Gio.File.new_for_path(str(self.state_current.parent / "other")), None, 0)
        self.assertEqual(self.app.theme_sync, 0)            # other files are ignored
        self.app.on_state_changed(None, name, None, 0)
        self.assertNotEqual(self.app.theme_sync, 0)          # theme.name: follow shortly
        GLib.source_remove(self.app.theme_sync)

    def test_settings_list_every_shortcut(self):
        self.make_theme("system", "gamma")
        self.sc.save_and_apply({"apply:gamma": self.sc.make_combo(["SUPER", "CTRL"], "G")})
        self.app.open_settings()
        labels = [w.get_label() for w in self.descendants(self.app.settings_box)
                  if isinstance(w, gi.repository.Gtk.Label)]
        for text in ("Open Theme Manager", "Next wallpaper", "Previous wallpaper", "Next favorite theme",
                     "Apply Gamma"):
            self.assertIn(text, labels)

    def test_f12_alone_takes_a_screenshot_also_with_the_note_closed_only(self):
        queued = []
        with patch.object(theme_popup.GLib, "idle_add", side_effect=lambda fn, *a: queued.append(fn)):
            self.assertTrue(self.app.on_key(None, Gdk.KEY_F12, 0, 0))
            for mod in (Gdk.ModifierType.SHIFT_MASK, Gdk.ModifierType.CONTROL_MASK,
                        Gdk.ModifierType.ALT_MASK, Gdk.ModifierType.SUPER_MASK):
                self.app.on_key(None, Gdk.KEY_F12, 0, mod)
            self.assertEqual(queued, [self.app.take_screenshot])          # F12 alone, once
            self.app.shot_dialog.set_visible(True)                        # the last note is open
            self.assertFalse(self.app.on_key(None, Gdk.KEY_F12, 0, 0))
            self.app.shot_dialog.set_visible(False)
            self.app.save_setting(screenshot_key=False)
            self.assertFalse(self.app.on_key(None, Gdk.KEY_F12, 0, 0))
        self.assertEqual(len(queued), 1)

    def test_closing_keeps_a_typed_screenshot_note(self):
        picture = Path(self.tmp.name) / "theme-manager-2026-11-10-10-00-00.png"
        picture.write_bytes(PNG)
        self.app.show_screenshot_note(picture)
        self.app.shot_note_entry.set_text("the badge is cut off")
        self.app.close()
        self.assertEqual(picture.with_suffix(".txt").read_text(), "the badge is cut off\n")
        self.assertFalse(self.app.shot_dialog_open())

    def test_corners_choices_and_every_control_in_the_css(self):
        import corners
        self.assertEqual(len(self.app.corner_labels()), len(corners.CHOICES))
        self.assertEqual(self.app.corner_labels()[1], "Slightly rounded (6 px)")
        css = {}
        real = theme_popup.Gtk.CssProvider.load_from_data
        def keep(provider, data, *args):
            css["text"] = bytes(data).decode()
            return real(provider, data, *args)
        with patch.object(theme_popup.Gtk.CssProvider, "load_from_data", keep):
            for choice, card, control, small in (("slight", 6, 6, 4), ("rounded", 12, 8, 4), ("square", 0, 0, 0)):
                self.app.set_corners(choice)
                text = css["text"]
                self.assertIn(f"border: 1px solid {self.app.theme.colors.get('accent', '#89b4fa')}; "
                              f"border-radius: {card}px; padding: 17px;", " ".join(text.split()))
                self.assertIn(f"entry, entry.search, spinbutton {{ border-radius: {control}px; }}", text)
                self.assertIn(f"checkbutton check {{ border-radius: {small}px; }}", text)
                self.assertIn("scale slider { min-width: 14px; min-height: 14px; margin: -6px;", text)
                self.assertEqual("*:not(.tm-ask-dot) { border-radius: 0; }" in text, choice == "square")
                self.assertEqual("tooltip, tooltip * { border-radius: 0; }" in text, choice == "square")
                self.assertEqual(theme_popup.workspaces_page.CORNER_RADIUS, card)

    def descendants(self, widget) -> list:
        out = []
        for child in self.children(widget):
            out.append(child)
            out.extend(self.descendants(child))
        return out


if __name__ == "__main__":
    unittest.main(verbosity=2)
