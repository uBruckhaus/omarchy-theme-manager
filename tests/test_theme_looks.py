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

    def test_a_switch_while_a_command_runs_gets_the_new_themes_look(self):
        """Codex round 2: the switch happens during Omarchy's font command."""
        theme_looks.set_look("ristretto", {"mono_font": "Mono B", "windows": "glassy"})
        theme_looks.set_look("tokyo-night", {"mono_font": "Mono C", "windows": "opaque"})
        original = theme_custom.set_font

        def slow_font(font):
            original(font)
            if font == "Mono B":
                self.use("tokyo-night")        # `omarchy theme set tokyo-night` meanwhile
            return True
        self.patch(theme_custom, "set_font", slow_font)
        self.use("ristretto")
        theme_looks.apply_look("ristretto")
        self.assertEqual((self.font, self.windows), ("Mono C", "opaque"))   # never ristretto's glassy
        self.assertNotIn("glassy", self.windows_set)
        self.assertEqual(self.settings()["own_font"], "Mono A")             # yours, still kept

    def test_the_lock_can_be_taken_again_by_its_holder(self):
        with theme_core.settings_lock():
            theme_core._update_settings(theme_core.settings_file(), x=1)   # no deadlock
        self.assertEqual(self.settings()["x"], 1)

    def test_a_restore_waits_for_a_running_look_change(self):
        import threading
        import theme_backup
        order = []
        started = threading.Event()

        def slow_change():
            with theme_looks._locked():
                started.set()
                order.append("look start")
                threading.Event().wait(0.3)
                order.append("look end")
        self.patch(theme_backup, "_restore", lambda path, previous=None: order.append("restore") or path)
        import contextlib
        import workspace_tiling
        self.patch(workspace_tiling, "runtime_lock", contextlib.nullcontext)   # never the real one
        worker = threading.Thread(target=slow_change)
        worker.start()
        started.wait(2)
        theme_backup.restore(self.root / "backup.json")
        worker.join()
        self.assertEqual(order, ["look start", "look end", "restore"])

    def test_many_switches_while_commands_run_end_on_the_last_theme(self):
        """Codex round 4: more than three switches during one apply."""
        slugs = [f"t{i}" for i in range(6)]
        for i, slug in enumerate(slugs):
            (self.roots.system_themes / slug).mkdir()
            (self.roots.system_themes / slug / "colors.toml").write_text(COLORS)
            theme_looks.set_look(slug, {"windows": ("glassy", "opaque")[i % 2]})
        theme_looks.set_look("t5", {"windows": "transparent", "mono_font": "Mono C"})
        queue = list(slugs[1:])
        original = theme_looks.set_windows

        def slow_windows(mode):
            ok = original(mode)
            if queue:
                self.use(queue.pop(0))          # the next `omarchy theme set` came in meanwhile
            return ok
        self.patch(theme_looks, "set_windows", slow_windows)
        self.use("t0")
        theme_looks.apply_look("t0")
        self.assertEqual((self.font, self.windows), ("Mono C", "transparent"))   # t5's look, the last one

    def test_a_preset_sets_no_font(self):
        """Uwe 2026-11-09 (after Codex rounds 3-5 on the preset font): an older preset's saved font is ignored."""
        import theme_auto
        import theme_presets
        self.patch(theme_core, "apply_theme", lambda slug, **kw: self.use(slug) or True)
        self.patch(theme_auto, "record_recent", lambda slug: None)
        self.patch(theme_auto, "remember_base_theme", lambda slug: None)
        theme_looks.set_look("tokyo-night", {"windows": "glassy"})
        warnings = theme_presets.apply({"name": "Old", "theme": "tokyo-night", "wallpaper": None, "font": "Mono C"})
        self.assertEqual(warnings, [])
        self.assertEqual((self.font, self.windows), ("Mono A", "glassy"))   # the theme's look, no preset font
        self.assertNotIn("Mono C", self.fonts_set)

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


ALACRITTY_TPL = """[colors.primary]
background = "{{ background }}"
foreground = "{{ foreground }}"

[colors.normal]
white = "{{ foreground }}"
bright_white = "{{ bright_foreground }}"
"""
FOOT_TPL = "[colors]\nforeground={{ foreground_strip }}\ncursor={{ background_strip }} {{ foreground_strip }}\n"
KITTY_TPL = "foreground {{ foreground }}\nbackground {{ background }}\n"


class TextColourTests(LooksTestCase):
    """The text colour of a theme's look in the configs Omarchy generated."""

    def setUp(self):
        super().setUp()
        themed = self.root / "default" / "themed"          # $OMARCHY_PATH/default/themed (beside themes/)
        themed.mkdir(parents=True)
        (themed / "alacritty.toml.tpl").write_text(ALACRITTY_TPL)
        (themed / "foot.ini.tpl").write_text(FOOT_TPL)
        (themed / "kitty.conf.tpl").write_text(KITTY_TPL)
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.root / "home-config")   # no templates of your own
        self.addCleanup(lambda: os.environ.__setitem__("XDG_CONFIG_HOME", old) if old is not None
                        else os.environ.pop("XDG_CONFIG_HOME", None))
        self.reloaded = []
        self.patch(theme_looks, "reload_apps", lambda changed: self.reloaded.append(sorted(changed)) or [])
        self.switch("ristretto")

    def switch(self, slug):
        """omarchy theme set: the theme's configs rendered fresh, a new generation."""
        state = self.roots.state_current
        current = state / "theme"
        if current.exists():
            for f in current.iterdir():
                f.unlink()
            current.rmdir()
        current.mkdir()
        colors = {"background": "#2c2525", "foreground": "#e6d9db", "bright_foreground": "#e6d9db"}
        render = lambda tpl: tpl.replace("{{ background }}", colors["background"]) \
            .replace("{{ foreground }}", colors["foreground"]) \
            .replace("{{ bright_foreground }}", colors["bright_foreground"]) \
            .replace("{{ foreground_strip }}", colors["foreground"][1:]) \
            .replace("{{ background_strip }}", colors["background"][1:])
        (current / "alacritty.toml").write_text(render(ALACRITTY_TPL))
        (current / "foot.ini").write_text(render(FOOT_TPL))
        # The theme ships its own kitty.conf: not rendered from the template, never touched.
        (current / "kitty.conf").write_text("# my own kitty\nforeground #e6d9db\n")
        (current / "colors.toml").write_text('background = "#2c2525"\nforeground = "#e6d9db"\n')
        self.use(slug)
        self.generation = getattr(self, "generation", 0) + 1
        (state / "theme.generation").write_text(f"gen-{self.generation}\n")

    def read(self, name):
        return (self.roots.state_current / "theme" / name).read_text()

    def test_the_colour_is_stored_checked_and_backed_up(self):
        theme_looks.set_look("ristretto", {"text_color": "#AABBCC"})
        self.assertEqual(theme_looks.look_of("ristretto"), {"text_color": "#aabbcc"})
        for bad in ("red", "#abc", "#aabbcc; rm", 3, "aabbcc"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                theme_looks.set_look("ristretto", {"text_color": bad})
        # A backup's looks.json is checked the same way: a bad colour is dropped.
        self.assertEqual(theme_looks.check_looks({"ristretto": {"text_color": "#AABBCC"},
                                                  "tokyo-night": {"text_color": "url(evil)"}}),
                         {"ristretto": {"text_color": "#aabbcc"}})
        self.assertIn("text_color", theme_looks.caps())

    def test_applying_swaps_only_the_foreground_lines_of_generated_configs(self):
        theme_looks.set_look("ristretto", {"text_color": "#ffcc00"})
        self.assertEqual(theme_looks.pending("ristretto"), ["text_color"])      # Reapply would bring it
        self.assertEqual(theme_looks.apply_look("ristretto"), ["text_color"])
        alacritty = self.read("alacritty.toml")
        self.assertIn('foreground = "#ffcc00"', alacritty)
        self.assertIn('white = "#ffcc00"', alacritty)
        self.assertIn('bright_white = "#e6d9db"', alacritty)         # another key, same hex: kept
        self.assertIn('background = "#2c2525"', alacritty)
        self.assertEqual(self.read("foot.ini"), "[colors]\nforeground=ffcc00\ncursor=2c2525 ffcc00\n")
        self.assertEqual(self.read("kitty.conf"), "# my own kitty\nforeground #e6d9db\n")   # the theme's own file
        self.assertIn('foreground = "#ffcc00"', self.read("colors.toml"))
        self.assertEqual(self.reloaded, [["alacritty.toml", "colors.toml", "foot.ini"]])
        self.assertEqual((self.roots.system_themes / "ristretto" / "colors.toml").read_text(), COLORS)
        self.assertEqual(theme_looks.pending("ristretto"), [])
        self.assertEqual(theme_looks.apply_look("ristretto"), [])     # already shown
        self.assertNotIn("own_text_color", json.dumps(self.settings()))   # nothing kept in settings

    def test_reapply_changes_it_and_without_a_colour_the_theme_has_its_own_back(self):
        theme_looks.set_look("ristretto", {"text_color": "#ffcc00"})
        theme_looks.apply_look("ristretto")
        theme_looks.set_look("ristretto", {"text_color": "#00ff88"})
        self.assertEqual(theme_looks.apply_look("ristretto"), ["text_color"])
        self.assertIn('foreground = "#00ff88"', self.read("alacritty.toml"))
        theme_looks.set_look("ristretto", {})
        self.assertEqual(theme_looks.pending("ristretto"), ["text_color"])
        self.assertEqual(theme_looks.apply_look("ristretto"), ["text_color"])
        self.assertIn('foreground = "#e6d9db"', self.read("alacritty.toml"))
        self.assertEqual(self.read("foot.ini"), "[colors]\nforeground=e6d9db\ncursor=2c2525 e6d9db\n")
        self.assertFalse(theme_looks.text_state_file().exists())

    def test_a_new_switch_starts_from_the_theme_colours_again(self):
        theme_looks.set_look("ristretto", {"text_color": "#ffcc00"})
        theme_looks.apply_look("ristretto")
        self.switch("ristretto")                                          # Omarchy rendered it fresh
        theme_looks.set_look("ristretto", {"text_color": "#123456"})
        self.assertEqual(theme_looks.apply_look("ristretto"), ["text_color"])
        self.assertIn('foreground = "#123456"', self.read("alacritty.toml"))
        self.switch("ristretto")
        theme_looks.set_look("ristretto", {})
        self.assertEqual(theme_looks.apply_look("ristretto"), [])          # its own already

    def test_only_the_theme_in_use_and_whole_values(self):
        theme_looks.set_look("tokyo-night", {"text_color": "#ffcc00"})
        self.assertEqual(theme_looks.apply_look("tokyo-night"), [])        # not in place: nothing generated
        self.assertEqual(theme_looks.pending("tokyo-night"), [])
        data = theme_looks.look_data("tokyo-night")
        self.assertEqual(data["own"]["text_color"], "#e6d9db")
        self.assertEqual(data["colors"]["background"], "#2c2525")
        self.assertEqual(theme_looks._swap("rgb = 130,30,46 30,30,46", "rgb = {{ foreground_rgb }} {{ x }}",
                                           "#1e1e2e", "#ffffff"), "rgb = 130,30,46 255,255,255")
        self.assertLess(theme_looks.contrast_ratio("#2a2a3a", "#1e1e2e"), theme_looks.MIN_CONTRAST)
        self.assertAlmostEqual(theme_looks.contrast_ratio("#ffffff", "#000000"), 21.0)



class ShellPayloadTests(unittest.TestCase):
    def test_only_small_own_regular_files_are_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "colors.toml").write_text('foreground = "#ffffff"\n')
            self.assertEqual(theme_looks._own_config_bytes(folder / "colors.toml"), b'foreground = "#ffffff"\n')
            secret = folder / "secret"
            secret.write_text("private")
            (folder / "shell.toml").symlink_to(secret)
            self.assertEqual(theme_looks._own_config_bytes(folder / "shell.toml"), b"")   # a link: never followed
            big = folder / "big.toml"
            big.write_bytes(b"x" * (theme_looks.MAX_CONFIG_BYTES + 1))
            self.assertEqual(theme_looks._own_config_bytes(big), b"")
            os.mkfifo(folder / "fifo.toml")
            self.assertEqual(theme_looks._own_config_bytes(folder / "fifo.toml"), b"")   # never waited on
            self.assertEqual(theme_looks._own_config_bytes(folder / "missing.toml"), b"")


if __name__ == "__main__":
    unittest.main()
