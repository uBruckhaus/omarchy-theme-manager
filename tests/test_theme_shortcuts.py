"""Tests for theme_shortcuts: combos, who has a key, the generated Lua file,
the require line in hyprland.lua and rollback on Hyprland errors."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_core
import theme_shortcuts as sc

LIVE = """Bind
	modmask: 64
	submap:
	key: Return
	description: Terminal

Bind
	modmask: 65
	submap:
	key: B
	description: Browser

Bind
	modmask: 64
	submap: resize
	key: L
	description: grow
"""


class ShortcutTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.hypr = root / "hypr"
        self.hypr.mkdir()
        (self.hypr / "hyprland.lua").write_text('require("default.hypr.omarchy")\n')
        (self.hypr / "bindings.lua").write_text(
            'o.bind("SUPER + CTRL + N", "Notes", "obsidian")\n'
            '-- o.bind("SUPER + CTRL + X", "commented out", "x")\n'
            'hl.unbind("SUPER + SHIFT + B")\n')
        self.patch(sc, "HYPR_DIR", self.hypr)
        self.patch(theme_core, "settings_file", lambda: root / "config" / "settings.json")
        self.patch(theme_core, "default_roots", lambda: theme_core.Roots(
            root / "themes-user", root / "themes-system", root / "bg", root / "state"))
        (root / "themes-system" / "nord").mkdir(parents=True)
        self.errors = [""]
        self.patch(sc, "config_errors", lambda: self.errors[0])

    def patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)


class ComboTests(ShortcutTestCase):
    def test_parse_and_label(self):
        combo = sc.parse_combo("super + shift + b")
        self.assertEqual(combo, {"mods": ["SUPER", "SHIFT"], "key": "B"})
        self.assertEqual(sc.combo_label(combo), "Super + Shift + B")
        self.assertEqual(sc.combo_string(sc.make_combo(["ALT", "SUPER"], "Return")), "SUPER + ALT + Return")
        self.assertIsNone(sc.parse_combo("HYPER + B"))
        self.assertIsNone(sc.make_combo(["SUPER"], "a b"))
        self.assertIsNone(sc.make_combo(["SUPER"], 'x"); os.exit('))

    def test_actions(self):
        self.assertTrue(sc.valid_action("open"))
        self.assertTrue(sc.valid_action("apply:nord"))
        self.assertFalse(sc.valid_action("apply:../x"))
        self.assertFalse(sc.valid_action("rm"))
        self.assertEqual(sc.action_label("apply:tokyo-night"), "Apply Tokyo Night")

    def test_bad_state_is_ignored(self):
        sc.state_file().parent.mkdir(parents=True)
        sc.state_file().write_text(json.dumps({
            "open": {"mods": ["SUPER"], "key": "T"}, "evil": {"mods": [], "key": "A"},
            "apply:../x": {"mods": ["SUPER"], "key": "Y"}, "next-wallpaper": {"mods": ["SUPER"], "key": "a;b"}}))
        self.assertEqual(sc.load_state(), {"open": {"mods": ["SUPER"], "key": "T"}})


class ClassifyTests(ShortcutTestCase):
    def test_who_has_a_key(self):
        state = {"open": sc.make_combo(["SUPER", "CTRL"], "T")}
        b = sc.Bindings(state, live=sc.parse_live_binds(LIVE))
        self.assertEqual(b.classify(sc.make_combo(["SUPER", "CTRL"], "T"), "open")[0], sc.SAME)
        self.assertEqual(b.classify(sc.make_combo(["SUPER", "CTRL"], "T"), "next-wallpaper"),
                         (sc.OURS, "Open Theme Manager"))
        self.assertEqual(b.classify(sc.make_combo(["SUPER", "CTRL"], "N"), "open"), (sc.CUSTOM, "Notes"))
        self.assertEqual(b.classify(sc.make_combo(["SUPER"], "Return"), "open"), (sc.OMARCHY, "Terminal"))
        self.assertEqual(b.classify(sc.make_combo(["SUPER"], "L"), "open")[0], sc.FREE)   # only in a submap
        self.assertEqual(b.classify(sc.make_combo(["SUPER", "CTRL"], "X"), "open")[0], sc.FREE)  # commented out


class WriteTests(ShortcutTestCase):
    def test_save_writes_lua_and_require_line_once(self):
        state = {"open": sc.make_combo(["SUPER", "CTRL"], "T"),
                 "apply:nord": {**sc.make_combo(["SUPER", "CTRL"], "N"), "replaces": "Notes"}}
        sc.save_and_apply(state)
        sc.save_and_apply(state)
        lua = (self.hypr / "theme-manager-shortcuts.lua").read_text()
        self.assertIn('hl.unbind("SUPER + CTRL + N")', lua)
        self.assertIn('o.bind("SUPER + CTRL + N", "Theme Manager: Apply Nord", '
                      f'"/usr/bin/python3 {sc.PLUGIN_DIR}/theme_auto.py apply nord")', lua)
        self.assertIn(f'"{sc.PLUGIN_DIR}/theme-toggle")', lua)
        self.assertIn(sc.CAPTURE_SUBMAP, lua)
        main = (self.hypr / "hyprland.lua").read_text()
        self.assertEqual(main.count(sc.REQUIRE_LINE), 1)
        self.assertTrue(main.startswith('require("default.hypr.omarchy")'))
        self.assertTrue((self.hypr / "hyprland.lua.before-theme-manager-shortcuts").is_file())
        self.assertEqual(sc.load_state()["apply:nord"]["replaces"], "Notes")

    def test_removing_the_last_key_removes_file_and_line(self):
        sc.save_and_apply({"open": sc.make_combo(["SUPER", "CTRL"], "T")})
        sc.save_and_apply({})
        self.assertFalse((self.hypr / "theme-manager-shortcuts.lua").exists())
        self.assertEqual((self.hypr / "hyprland.lua").read_text(), 'require("default.hypr.omarchy")\n')

    def test_rejected_config_is_rolled_back(self):
        sc.save_and_apply({"open": sc.make_combo(["SUPER", "CTRL"], "T")})
        before = (self.hypr / "theme-manager-shortcuts.lua").read_text()
        calls = iter(["", "error: bad bind", ""])
        self.patch(sc, "config_errors", lambda: next(calls))
        with self.assertRaises(RuntimeError):
            sc.save_and_apply({"open": sc.make_combo(["SUPER", "CTRL"], "Y")})
        self.assertEqual((self.hypr / "theme-manager-shortcuts.lua").read_text(), before)
        self.assertEqual(sc.load_state()["open"]["key"], "T")

    def test_first_save_rejected_leaves_no_trace(self):
        calls = iter(["", "error", ""])
        self.patch(sc, "config_errors", lambda: next(calls))
        with self.assertRaises(RuntimeError):
            sc.save_and_apply({"open": sc.make_combo(["SUPER", "CTRL"], "T")})
        self.assertFalse((self.hypr / "theme-manager-shortcuts.lua").exists())
        self.assertNotIn(sc.REQUIRE_LINE, (self.hypr / "hyprland.lua").read_text())
        self.assertEqual(sc.load_state(), {})

    def test_deleted_theme_key_is_left_out(self):
        sc.save_and_apply({"apply:gone": sc.make_combo(["SUPER", "CTRL"], "G")})
        self.assertNotIn("apply gone", (self.hypr / "theme-manager-shortcuts.lua").read_text())

    def test_rename_keeps_the_key(self):
        sc.save_and_apply({"apply:nord": sc.make_combo(["SUPER", "CTRL"], "N")})
        (Path(self.tmp.name) / "themes-system" / "frost").mkdir()
        sc.rename_theme("nord", "frost")
        self.assertEqual(list(sc.load_state()), ["apply:frost"])

    def test_prune_removes_only_keys_of_missing_themes(self):
        state = {"open": sc.make_combo(["SUPER", "CTRL"], "T"),
                 "apply:nord": sc.make_combo(["SUPER", "CTRL"], "N"),
                 "apply:gone": {**sc.make_combo(["SUPER"], "Return"), "replaces": "Terminal"}}
        sc.save_and_apply(state)
        self.assertEqual(sc.prune_missing(), ["gone"])
        self.assertEqual(sorted(sc.load_state()), ["apply:nord", "open"])
        self.assertNotIn('"SUPER + Return"', (self.hypr / "theme-manager-shortcuts.lua").read_text())
        self.assertEqual(sc.prune_missing(), [])

    def test_drop_theme_reports_whether_it_had_a_key(self):
        sc.save_and_apply({"apply:nord": sc.make_combo(["SUPER", "CTRL"], "N")})
        self.assertTrue(sc.drop_theme("nord"))
        self.assertFalse(sc.drop_theme("nord"))
        self.assertFalse((self.hypr / "theme-manager-shortcuts.lua").exists())   # the last key

    def test_unsafe_plugin_path_is_refused(self):
        self.patch(sc, "PLUGIN_DIR", Path("/tmp/a b;rm -rf ~"))
        with self.assertRaises(ValueError):
            sc.action_command("next-wallpaper")


if __name__ == "__main__":
    unittest.main()
