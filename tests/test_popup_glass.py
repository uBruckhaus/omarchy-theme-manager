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
        self.assertNotIn("omarchy-", lua.split("window-glassy.lua")[1].split("end")[0].replace("omarchy-keyboard", ""))

    def test_set_enabled_writes_and_removes(self):
        main = popup_glass.lua_file().parent / "hyprland.lua"
        main.parent.mkdir(parents=True, exist_ok=True)
        main.write_text('require("default.hypr.omarchy")\n')
        import theme_shortcuts
        with mock.patch.object(theme_shortcuts, "HYPR_DIR", main.parent), \
                mock.patch.object(theme_shortcuts, "config_errors", return_value=""):
            popup_glass.set_enabled(True)
            self.assertTrue(glass_style.enabled())
            self.assertIn(popup_glass.REQUIRE_LINE, main.read_text())
            popup_glass.set_enabled(False)
            self.assertFalse(glass_style.enabled())
            self.assertEqual(main.read_text(), 'require("default.hypr.omarchy")\n')


if __name__ == "__main__":
    unittest.main()
