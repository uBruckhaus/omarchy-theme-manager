import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import glass_style
import popup_glass


class PopupGlassTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "config"),
                                                "XDG_STATE_HOME": str(root / "state")})
        self.env.start()
        self.toggles = root / "state/omarchy/toggles/hypr"
        self.toggles.mkdir(parents=True)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def switch_on(self):
        popup_glass.lua_file().parent.mkdir(parents=True, exist_ok=True)
        popup_glass.lua_file().write_text(popup_glass.render_lua())

    def test_off_keeps_popups_solid(self):
        (self.toggles / "window-glassy.lua").write_text("")
        self.assertFalse(glass_style.enabled())
        self.assertEqual(glass_style.popup_alpha(), 1.0)

    def test_levels_follow_the_window_style(self):
        self.switch_on()
        self.assertEqual(glass_style.popup_alpha(), glass_style.TRANSPARENT_ALPHA)
        (self.toggles / "window-glassy.lua").write_text("")
        self.assertEqual(glass_style.popup_alpha(), glass_style.GLASSY_ALPHA)
        (self.toggles / "window-opaque.lua").write_text("")    # opaque wins, as in Omarchy's command
        self.assertEqual(glass_style.popup_alpha(), 1.0)

    def test_lua_names_our_surfaces_only(self):
        lua = popup_glass.render_lua()
        self.assertIn('namespace = "^(theme-manager|simple-launcher-for-fools|app-manager-dock|bar-organizer)$"', lua)
        self.assertIn('o.transparent_window("^(org\\\\.omarchy\\\\.aiagentsmap|org\\\\.omarchy\\\\.settingsmanager)$")', lua)
        self.assertNotIn('"^(omarchy', lua)          # never one of Omarchy's own surfaces

    def hypr(self, text='require("default.hypr.omarchy")\n'):
        main = popup_glass.lua_file().parent / "hyprland.lua"
        main.parent.mkdir(parents=True, exist_ok=True)
        main.write_text(text)
        import theme_shortcuts
        self.addCleanup(mock.patch.object(theme_shortcuts, "HYPR_DIR", main.parent).stop)
        mock.patch.object(theme_shortcuts, "HYPR_DIR", main.parent).start()
        return main

    def test_set_enabled_writes_and_removes(self):
        main = self.hypr()
        with mock.patch.object(popup_glass, "_check", return_value=""):
            popup_glass.set_enabled(True)
            self.assertTrue(glass_style.enabled())
            self.assertIn(popup_glass.REQUIRE_LINE, main.read_text())
            popup_glass.set_enabled(True)                     # twice: still one block
            self.assertEqual(main.read_text().count(popup_glass.REQUIRE_LINE), 1)
            popup_glass.set_enabled(False)
            self.assertFalse(glass_style.enabled())
            self.assertEqual(main.read_text(), 'require("default.hypr.omarchy")\n')

    def test_foreign_file_and_line_are_left_alone(self):
        own = 'require("default.hypr.omarchy")\n' + popup_glass.REQUIRE_LINE + "\n"
        main = self.hypr(own)
        lua = popup_glass.lua_file()
        lua.write_text("-- my own rules\n")
        with mock.patch.object(popup_glass, "_check", return_value=""):
            for on in (True, False):
                with self.assertRaises(RuntimeError):
                    popup_glass.set_enabled(on)
        self.assertEqual(lua.read_text(), "-- my own rules\n")
        self.assertEqual(main.read_text(), own)

    def test_failed_write_puts_both_files_back(self):
        main = self.hypr()
        before = main.read_text()
        import theme_shortcuts
        real = theme_shortcuts._atomic_write

        def flaky(path, text):
            if path == main:
                raise OSError("disk full")
            real(path, text)
        with mock.patch.object(popup_glass, "_check", return_value=""), \
                mock.patch.object(theme_shortcuts, "_atomic_write", side_effect=flaky):
            with self.assertRaises(OSError):
                popup_glass.set_enabled(True)
        self.assertFalse(popup_glass.lua_file().exists())
        self.assertEqual(main.read_text(), before)

    def test_unavailable_check_changes_nothing(self):
        main = self.hypr()
        with mock.patch.object(popup_glass, "_check", side_effect=RuntimeError("no hyprctl")):
            with self.assertRaises(RuntimeError):
                popup_glass.set_enabled(True)
        self.assertFalse(popup_glass.lua_file().exists())
        self.assertNotIn(popup_glass.REQUIRE_LINE, main.read_text())

    def test_lua_opens_no_file(self):
        self.assertNotIn("io.open", popup_glass.render_lua())

    def test_hyprland_lua_changed_meanwhile_is_left_alone(self):
        main = self.hypr()
        import theme_shortcuts
        real = theme_shortcuts._bounded_read
        reads = []

        def other_writer(path):
            if path == main:
                reads.append(1)
                if len(reads) == 2:
                    main.write_text("-- edited by hand\n")
            return real(path)
        with mock.patch.object(popup_glass, "_check", return_value=""), \
                mock.patch.object(theme_shortcuts, "_bounded_read", side_effect=other_writer):
            with self.assertRaises(RuntimeError):
                popup_glass.set_enabled(True)
        self.assertEqual(main.read_text(), "-- edited by hand\n")
        self.assertFalse(popup_glass.lua_file().exists())

    def test_new_config_error_rolls_back(self):
        main = self.hypr()
        before = main.read_text()
        with mock.patch.object(popup_glass, "_check", side_effect=["", "line 3: bad", "", ""]):
            with self.assertRaises(RuntimeError):
                popup_glass.set_enabled(True)
        self.assertFalse(popup_glass.lua_file().exists())
        self.assertEqual(main.read_text(), before)

if __name__ == "__main__":
    unittest.main()
