"""Tests for theme_looks: a font and window style per theme, your own values back."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_core  # noqa: E402
import theme_custom  # noqa: E402
import theme_looks  # noqa: E402
import theme_backup  # noqa: E402

COLORS = 'accent = "#f38d70"\nbackground = "#2c2525"\nforeground = "#e6d9db"\n'


class LooksTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root = root
        self.roots = theme_core.Roots(
            user_themes=root / "themes-user", system_themes=root / "themes-system",
            user_backgrounds=root / "backgrounds", state_current=root / "state")
        for d in (self.roots.user_themes, self.roots.system_themes, self.roots.user_backgrounds,
                  self.roots.state_current):
            d.mkdir(parents=True)
        self.patch(theme_core, "default_roots", lambda: self.roots)
        self.patch(theme_core, "settings_file", lambda: root / "config" / "settings.json")
        for slug in ("ristretto", "tokyo-night"):
            (self.roots.system_themes / slug).mkdir()
            (self.roots.system_themes / slug / "colors.toml").write_text(COLORS)
        self.use("ristretto")
        self.font, self.fonts_set = "Mono A", []
        self.patch(theme_custom, "list_fonts", lambda: ["Mono A", "Mono B", "Mono C"])
        self.patch(theme_custom, "current_font", lambda: self.font)

        def set_font(font):
            self.fonts_set.append(font)
            self.font = font
            return True
        self.patch(theme_custom, "set_font", set_font)
        self.windows, self.windows_set, self.windows_ok = "transparent", [], True
        self.patch(theme_looks, "windows_supported", lambda: True)
        self.patch(theme_looks, "current_windows", lambda: self.windows)

        def set_windows(mode):
            self.windows_set.append(mode)
            if self.windows_ok:
                self.windows = mode
            return self.windows_ok
        self.patch(theme_looks, "set_windows", set_windows)

    def patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def use(self, slug):
        (self.roots.state_current / "theme.name").write_text(slug + "\n")

    def apply(self, slug):
        """As after `omarchy theme set <slug>`: that theme is in use, then its look."""
        self.use(slug)
        return theme_looks.apply_look(slug)

    def settings(self):
        return theme_core._load_settings(theme_core.settings_file())


class StoreTests(LooksTestCase):
    def test_set_returns_previous_and_empty_removes(self):
        self.assertEqual(theme_looks.set_look("ristretto", {"mono_font": "Mono B"}), {})
        self.assertEqual(theme_looks.set_look("ristretto", {"windows": "glassy", "mono_font": None}),
                         {"mono_font": "Mono B"})
        self.assertEqual(theme_looks.look_of("ristretto"), {"windows": "glassy"})
        theme_looks.set_look("ristretto", {})
        self.assertFalse(theme_looks.looks_file().exists())
        self.assertEqual(theme_looks.look_of("ristretto"), {})

    def test_bad_looks_are_refused_or_dropped(self):
        for bad in ({"mono_font": "x\"; rm -rf ~ #"}, {"windows": "blurry"}, {"colors": 1}, "Mono B"):
            with self.assertRaises(ValueError):
                theme_looks.set_look("ristretto", bad)
        with self.assertRaises(ValueError):
            theme_looks.set_look("../evil", {"mono_font": "Mono B"})
        path = theme_looks.looks_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ristretto": {"mono_font": "Mono B"}, "bad slug!": {"windows": "glassy"},
                                    "tokyo-night": {"windows": "nope"}}))
        self.assertEqual(theme_looks.load_looks(), {"ristretto": {"mono_font": "Mono B"}})

    def test_a_fifo_is_never_waited_on(self):
        path = theme_looks.looks_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path)
        self.assertEqual(theme_looks.load_looks(), {})

    def test_rename_and_prune(self):
        theme_looks.set_look("ristretto", {"mono_font": "Mono B"})
        theme_looks.set_look("gone", {"windows": "opaque"})
        theme_looks.rename("ristretto", "mine")
        self.assertEqual(theme_looks.look_of("mine"), {"mono_font": "Mono B"})
        self.assertEqual(sorted(theme_looks.prune_missing()), ["gone", "mine"])
        self.assertEqual(theme_looks.load_looks(), {})


class ApplyTests(LooksTestCase):
    def test_look_then_your_own_values_back(self):
        theme_looks.set_look("tokyo-night", {"mono_font": "Mono B", "windows": "glassy"})
        self.use("tokyo-night")
        self.assertEqual(self.apply("tokyo-night"), ["mono_font", "windows"])
        self.assertEqual((self.font, self.windows), ("Mono B", "glassy"))
        self.assertEqual((self.settings()["own_font"], self.settings()["own_windows"]), ("Mono A", "transparent"))
        self.assertEqual(self.apply("tokyo-night"), [])        # already on the desktop
        self.use("ristretto")
        self.assertEqual(self.apply("ristretto"), ["mono_font", "windows"])
        self.assertEqual((self.font, self.windows), ("Mono A", "transparent"))
        self.assertIsNone(self.settings()["own_font"])
        self.assertIsNone(self.settings()["own_windows"])
        self.assertEqual(self.apply("ristretto"), [])          # nothing to give back

    def test_your_own_value_is_kept_until_it_is_really_back(self):
        theme_looks.set_look("tokyo-night", {"windows": "glassy"})
        self.apply("tokyo-night")
        self.windows_ok = False
        self.assertEqual(self.apply("ristretto"), [])
        self.assertEqual(self.settings()["own_windows"], "transparent")
        self.windows_ok = True
        self.assertEqual(self.apply("ristretto"), ["windows"])
        self.assertIsNone(self.settings()["own_windows"])

    def test_the_look_wins_over_a_custom_themes_font(self):
        base = next(t for t in theme_core.discover_themes() if t.slug == "ristretto")
        theme_custom.create_theme(base, "Mine", theme_custom.load_palette(base), "Mono B")
        self.assertEqual(self.apply("mine"), ["mono_font"])
        self.assertEqual(self.font, "Mono B")
        theme_looks.set_look("mine", {"mono_font": "Mono C"})
        self.assertEqual(self.apply("mine"), ["mono_font"])
        self.assertEqual(self.font, "Mono C")
        self.assertEqual(self.settings()["own_font"], "Mono A")
        self.assertEqual(self.apply("ristretto"), ["mono_font"])
        self.assertEqual(self.font, "Mono A")

    def test_a_custom_themes_own_window_style(self):
        base = next(t for t in theme_core.discover_themes() if t.slug == "ristretto")
        theme_custom.create_theme(base, "Frost", theme_custom.load_palette(base), None, windows="glassy")
        frost = next(t for t in theme_core.discover_themes() if t.slug == "frost")
        self.assertEqual(theme_custom.windows_of(frost), "glassy")
        self.assertEqual(self.apply("frost"), ["windows"])
        self.assertEqual(self.windows, "glassy")
        theme_custom.update_theme(frost, theme_custom.load_palette(frost), None, None)
        self.assertIsNone(theme_custom.windows_of(frost))
        with self.assertRaises(ValueError):
            theme_custom.update_theme(frost, theme_custom.load_palette(frost), None, "blurry")
        self.apply("ristretto")
        self.assertEqual(self.windows, "transparent")

    def test_no_window_style_without_omarchys_command(self):
        self.patch(theme_looks, "windows_supported", lambda: False)
        theme_looks.set_look("tokyo-night", {"windows": "glassy"})
        self.assertEqual(self.apply("tokyo-night"), [])
        self.assertEqual(self.windows_set, [])
        self.assertEqual(theme_looks.caps(), ["mono_font"])

    def test_pending_only_for_the_theme_in_use(self):
        theme_looks.set_look("ristretto", {"windows": "glassy"})
        self.assertEqual(theme_looks.pending("ristretto"), ["windows"])
        self.assertEqual(theme_looks.pending("tokyo-night"), [])
        self.apply("ristretto")
        self.assertEqual(theme_looks.pending("ristretto"), [])
        theme_looks.set_look("ristretto", {})            # yours still to come back
        self.assertEqual(theme_looks.pending("ristretto"), ["windows"])
        self.windows = "transparent"                     # someone switched it back meanwhile
        self.assertEqual(theme_looks.pending("ristretto"), [])

    def test_look_data(self):
        theme_looks.set_look("tokyo-night", {"windows": "opaque"})
        data = theme_looks.look_data("tokyo-night")
        self.assertEqual(data["look"], {"windows": "opaque"})
        self.assertEqual(data["own"], {"mono_font": "Mono A", "windows": "transparent"})
        self.assertEqual(data["theme"], {"mono_font": None, "windows": None})
        self.assertEqual((data["in_use"], data["pending"], data["caps"]), (False, [], ["mono_font", "windows"]))
        with self.assertRaises(FileNotFoundError):
            theme_looks.look_data("nope")


class RaceTests(LooksTestCase):
    """Codex round 1 on 2.1.0 (2026-11-09)."""

    def test_a_late_apply_for_a_theme_no_longer_in_use_does_nothing(self):
        theme_looks.set_look("tokyo-night", {"windows": "glassy", "mono_font": "Mono B"})
        self.use("ristretto")                       # switched away before the Save/Reapply job ran
        self.assertEqual(theme_looks.apply_look("tokyo-night"), [])
        self.assertEqual((self.font, self.windows, self.windows_set), ("Mono A", "transparent", []))
        self.assertNotIn("own_windows", self.settings())

    def test_concurrent_saves_lose_nothing(self):
        import threading
        slugs = [f"theme-{i}" for i in range(12)]
        threads = [threading.Thread(target=theme_looks.set_look, args=(slug, {"windows": "glassy"})) for slug in slugs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(theme_looks.load_looks()), sorted(slugs))

    def test_concurrent_settings_updates_lose_nothing(self):
        import threading
        path = theme_core.settings_file()
        keys = [f"k{i}" for i in range(12)]
        threads = [threading.Thread(target=theme_core._update_settings, args=(path,), kwargs={k: True}) for k in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(self.settings().get(k) for k in keys))

    def test_a_failing_look_never_fails_the_theme_switch(self):
        import theme_auto
        self.patch(theme_core, "apply_theme", lambda slug, **kw: self.use(slug) or True)
        self.patch(theme_looks, "apply_look", lambda slug: (_ for _ in ()).throw(OSError("disk full")))
        self.patch(theme_auto, "record_recent", lambda slug: None)
        self.patch(theme_auto, "remember_base_theme", lambda slug: None)
        self.assertTrue(theme_auto.apply_theme("tokyo-night"))


class BackupTests(LooksTestCase):
    def test_looks_and_own_windows_in_backups(self):
        self.assertIn("looks.json", theme_backup.FILES)
        good = {"ristretto": {"mono_font": "Mono B", "windows": "glassy"}}
        self.assertEqual(theme_backup._validate_value("looks.json", good), good)
        with self.assertRaises(ValueError):
            theme_backup._validate_value("looks.json", {"ristretto": {"windows": "x"}})
        theme_backup._validate_settings({"own_windows": "glassy"})
        with self.assertRaises(ValueError):
            theme_backup._validate_settings({"own_windows": "x; rm"})


if __name__ == "__main__":
    unittest.main()
