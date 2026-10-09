#!/usr/bin/env python3
"""Functional tests of the theme_popup view layer.

Builds the real GTK window (never presented) and drives the same handler
methods the UI would, asserting on widget state and filesystem effects.
All core state is isolated in a temp dir by patching theme_core's
default_roots()/hidden_file(); theme mutations that would touch the real
system (apply_theme, clipboard) are stubbed.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import theme_core  # noqa: E402
import theme_popup  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


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
        theme_core.default_roots = lambda: self.roots
        theme_core.hidden_file = lambda: self.hidden_file
        self.applied: list[str] = []
        theme_core.apply_theme = lambda slug, binary="omarchy", timeout=120.0: \
            self.applied.append(slug) or True
        self.notices: list[tuple[str, str]] = []
        theme_core.notify = lambda title, body="": self.notices.append((title, body))

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
        theme_popup.ThemeManager._async = self._orig_async
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

    def rows_list(self) -> list:
        return self.children(self.app.rows)

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
        self.assertTrue(beta_row.has_css_class("tm-row-hidden"))

        # Eye toggles back off: hidden row disappears again.
        self.app.toggle_show_hidden()
        visible = {row.tm_theme.slug for row in self.rows_list()}
        self.assertEqual(visible, {"alpha"})
        # The hidden set itself is unchanged.
        self.assertEqual(theme_core.load_hidden(), {"beta"})

    def test_menu_hide_unhide_persists(self):
        self.make_theme("user", "alpha")
        theme_core.save_hidden(set())
        self.app.refresh_themes()
        self.app.set_theme_hidden(self.app.theme_list[0], True)
        self.assertEqual(theme_core.load_hidden(), {"alpha"})
        self.app.set_theme_hidden(self.app.theme_list[0], False)
        self.assertEqual(theme_core.load_hidden(), set())

    def test_wallpapers_page_lists_theme_and_user_wallpapers(self):
        theme = self.make_theme("user", "alpha", ("ship1.png", "ship2.png"))
        self.make_wallpaper("alpha", "user1.png")
        self.app.open_wallpapers_for(theme)
        self.assertEqual(self.app.stack.get_visible_child_name(), "wallpapers")
        tiles = [t.get_child() if isinstance(t, gi.repository.Gtk.FlowBoxChild) else t
                 for t in self.children(self.app.wp_grid)]
        names = sorted(tile.get_last_child().get_property("label")
                       for tile in tiles if isinstance(tile.get_last_child(), gi.repository.Gtk.Label))
        self.assertEqual(names, ["ship1.png", "ship2.png", "user1.png"])
        # Back button returns to the list.
        self.app.show_themes()
        self.assertEqual(self.app.stack.get_visible_child_name(), "themes")

    def test_empty_wallpaper_state(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        tiles = self.children(self.app.wp_grid)
        self.assertEqual(len(tiles), 1)  # the empty-state tile
        self.assertEqual(self.app.wp_title.get_property("label"), "Alpha — wallpapers")

    def test_on_drop_adds_image(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "dropped.png"
        src.write_bytes(PNG)
        self.app.open_wallpapers_for(theme)
        uri = GLib.filename_to_uri(str(src), None)
        self.app.on_drop(None, GLib.Variant("s", uri), 0, 0)
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
        self.app.on_drop(None, GLib.Variant("s", uris), 0, 0)
        self.assertEqual(len(list((self.roots.user_backgrounds / "alpha").iterdir())), 3)

    def test_on_drop_ignores_plain_text(self):
        theme = self.make_theme("user", "alpha")
        self.app.open_wallpapers_for(theme)
        self.app.on_drop(None, GLib.Variant("s", "hello world, no uris"), 0, 0)
        d = self.roots.user_backgrounds / "alpha"
        self.assertFalse(d.exists() and any(d.iterdir()))
        self.assertIn("nothing", " ".join(t for t, _ in self.notices).lower())

    def test_on_drop_rejects_non_image(self):
        theme = self.make_theme("user", "alpha")
        src = Path(self.tmp.name) / "notes.txt"
        src.write_text("not an image")
        self.app.open_wallpapers_for(theme)
        self.app.on_drop(None, GLib.Variant("s",
                         GLib.filename_to_uri(str(src), None)), 0, 0)
        d = self.roots.user_backgrounds / "alpha"
        self.assertFalse(d.exists() and any(d.iterdir()))
        self.assertTrue(any("skipped" in t.lower() for t, _ in self.notices))

    def test_paste_adds_image(self):
        theme = self.make_theme("user", "alpha")
        theme_core.clipboard_image = lambda **kw: ("image/png", PNG)
        self.app.open_wallpapers_for(theme)
        self.app.paste_clipboard()
        dest = list((self.roots.user_backgrounds / "alpha").iterdir())
        self.assertEqual(len(dest), 1)
        self.assertTrue(dest[0].name.startswith("pasted-image"))

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

    def test_delete_theme_user(self):
        theme = self.make_theme("user", "beta", ("b1.png",))
        self.app.perform_delete_theme(theme)
        self.assertFalse((self.roots.user_themes / "beta").exists())

    def test_delete_theme_system_refused_at_core(self):
        theme = self.make_theme("system", "gamma")
        with self.assertRaises(FileNotFoundError):
            theme_core.remove_theme(None, "gamma")
        self.assertTrue((self.roots.system_themes / "gamma").exists())

    def test_system_menu_item_disabled(self):
        self.make_theme("system", "gamma")
        self.app.refresh_themes()
        gamma_row = next(r for r in self.rows_list()
                         if r.tm_theme.slug == "gamma")
        popover = None
        for child in self.children(gamma_row.get_child()):
            if isinstance(child, gi.repository.Gtk.MenuButton):
                popover = child.get_popover()
        self.assertIsNotNone(popover)
        buttons = self.children(popover.get_child())
        labels = [b.get_property("label") for b in buttons
                  if isinstance(b, gi.repository.Gtk.Button)]
        self.assertIn("System theme — read-only", labels)
        read_only = next(b for b in buttons
                         if isinstance(b, gi.repository.Gtk.Button)
                         and b.get_property("label") == "System theme — read-only")
        self.assertFalse(read_only.get_property("sensitive"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
