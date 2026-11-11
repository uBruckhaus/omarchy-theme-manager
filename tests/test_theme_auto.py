"""Tests for theme_auto: favorites, recently used, workspace wallpapers,
slideshow, day/night themes and the bar's command line."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_auto
import theme_core
import i18n  # noqa: E402

i18n._lang = "en"  # the assertions below read English texts
from images import png


class AutoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.roots = theme_core.Roots(
            user_themes=root / "themes-user", system_themes=root / "themes-system",
            user_backgrounds=root / "backgrounds", state_current=root / "state")
        for d in (self.roots.user_themes, self.roots.system_themes, self.roots.user_backgrounds,
                  self.roots.state_current):
            d.mkdir(parents=True)
        self.patch(theme_core, "default_roots", lambda: self.roots)
        self.patch(theme_core, "settings_file", lambda: root / "config" / "settings.json")
        self.patch(theme_core, "hidden_file", lambda: root / "config" / "hidden.json")
        self.patch(theme_core, "hidden_wallpapers_file", lambda: root / "config" / "hidden-wallpapers.json")
        self.patch(theme_auto, "state_file", lambda: root / "cache" / "auto-state.json")
        self.patch(theme_auto, "workspace_theme_state_file", lambda: root / "cache" / "workspace-theme-state.json")
        self.shown: list[str] = []

        def set_background(path):
            self.shown.append(Path(path).name)
            link = self.roots.state_current / "background"
            link.unlink(missing_ok=True)
            link.symlink_to(path)
            return True
        self.patch(theme_core, "set_desktop_background", set_background)
        self.applied: list[str] = []

        def apply(slug, binary="omarchy", timeout=120.0):
            self.applied.append(slug)
            (self.roots.state_current / "theme.name").write_text(slug + "\n")
            return True
        self.patch(theme_core, "apply_theme", apply)
        self.workspace = 1
        self.patch(theme_auto, "active_workspace", lambda: self.workspace)
        import theme_custom
        self.font = "Mono A"
        self.fonts_set: list[str] = []
        self.patch(theme_custom, "list_fonts", lambda: ["Mono A", "Mono B"])
        self.patch(theme_custom, "current_font", lambda: self.font)

        def set_font(font):
            self.fonts_set.append(font)
            self.font = font
            return True
        self.patch(theme_custom, "set_font", set_font)
        import theme_looks
        self.patch(theme_looks, "windows_supported", lambda: False)   # never Omarchy's real window style

    def patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def make_theme(self, slug, wallpapers=("a.png", "b.png", "c.png"), current=False):
        folder = self.roots.system_themes / slug / "backgrounds"
        folder.mkdir(parents=True)
        for name in wallpapers:
            (folder / name).write_bytes(png())
        if current:
            (self.roots.state_current / "theme.name").write_text(slug + "\n")
        return folder

    def set_link(self, path):
        link = self.roots.state_current / "background"
        link.unlink(missing_ok=True)
        link.symlink_to(path)


class BoundedStateTests(unittest.TestCase):
    """Codex round 2: state files are read bounded, never from a FIFO."""

    def test_read_json_refuses_fifo_and_oversized_files(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "ok.json").write_text('{"a": 1}')
            self.assertEqual(theme_auto._read_json(base / "ok.json", {}), {"a": 1})
            (base / "link.json").symlink_to(base / "ok.json")             # a dotfiles link is fine
            self.assertEqual(theme_auto._read_json(base / "link.json", {}), {"a": 1})
            os.mkfifo(base / "fifo.json")
            self.assertEqual(theme_auto._read_json(base / "fifo.json", {}), {})
            (base / "big.json").write_bytes(b" " * (theme_auto.STATE_MAX + 1))
            self.assertEqual(theme_auto._read_json(base / "big.json", []), [])


class BarStateTests(AutoTestCase):
    """The bar widget reads its state through the helper (bounded), not by loading files itself."""

    def test_bar_state_summarises_settings_and_workspaces(self):
        theme_auto.update_settings(workspace_themes=True, slideshow_minutes=10, language="de")
        theme_auto._write_json(theme_auto.config_dir() / "workspaces.json",
                               {"active": True, "workspaceLayouts": {"2": {"autostart": True}}})
        state = theme_auto.bar_state()
        self.assertEqual(state, {"workspace_wallpapers": False, "workspace_themes": True, "timed": True,
                                 "language": "de", "tiling_active": True, "has_autostart": True,
                                 "has_workspace_presets": True})
        (theme_auto.config_dir() / "workspaces.json").write_bytes(b" " * (theme_auto.STATE_MAX + 1))
        self.assertFalse(theme_auto.bar_state()["tiling_active"])         # too large: not read


class FavoritesTests(AutoTestCase):
    def test_favorites_keep_their_order_and_reject_bad_slugs(self):
        theme_auto.set_favorite("nord", True)
        theme_auto.set_favorite("alpha", True)
        theme_auto.set_favorite("nord", True)   # no duplicate
        self.assertEqual(theme_auto.load_favorites(), ["alpha", "nord"])
        theme_auto.set_favorite("alpha", False)
        self.assertEqual(theme_auto.load_favorites(), ["nord"])
        with self.assertRaises(ValueError):
            theme_auto.set_favorite("../x", True)
        theme_auto.favorites_file().write_text(json.dumps(["ok", "../bad", 3, "ok"]))
        self.assertEqual(theme_auto.load_favorites(), ["ok"])

    def test_order_favorites_first_then_name_or_recent(self):
        for slug in ("alpha", "beta", "gamma", "delta"):
            self.make_theme(slug)
        themes = theme_core.discover_themes()
        recent = {"gamma": 300.0, "alpha": 100.0}
        by_name = theme_auto.order_themes(themes, ["delta"], recent, "name")
        self.assertEqual([t.slug for t in by_name], ["delta", "alpha", "beta", "gamma"])
        by_use = theme_auto.order_themes(themes, ["delta"], recent, "recent")
        self.assertEqual([t.slug for t in by_use], ["delta", "gamma", "alpha", "beta"])

    def test_recent_is_capped_and_notes_outside_switches(self):
        for i in range(theme_auto.MAX_RECENT + 5):
            theme_auto.record_recent(f"t{i}", when=float(i))
        recent = theme_auto.load_recent()
        self.assertEqual(len(recent), theme_auto.MAX_RECENT)
        self.assertNotIn("t0", recent)
        self.make_theme("nord", current=True)
        theme_auto.note_current_theme()
        self.assertEqual(max(theme_auto.load_recent(), key=theme_auto.load_recent().get), "nord")

    def test_menu_falls_back_to_recent(self):
        self.make_theme("alpha", current=True)
        self.make_theme("beta")
        theme_auto.record_recent("beta", when=2.0)
        theme_auto.record_recent("alpha", when=1.0)
        theme_auto.record_recent("gone", when=3.0)    # no longer installed
        data = theme_auto.menu_data()
        self.assertEqual(data["source"], "recent")
        self.assertEqual([t["slug"] for t in data["themes"]], ["beta", "alpha"])
        theme_auto.set_favorite("alpha", True)
        data = theme_auto.menu_data()
        self.assertEqual(data, {"source": "favorites",
                                "themes": [{"slug": "alpha", "name": "Alpha", "current": True}]})


class WorkspaceTests(AutoTestCase):
    def test_off_by_default(self):
        folder = self.make_theme("nord", current=True)
        theme_auto.set_workspace_wallpaper("nord", 2, "b.png")
        self.set_link(folder / "a.png")
        self.assertIsNone(theme_auto.on_workspace(2))
        self.assertEqual(self.shown, [])

    def test_workspace_wallpaper_and_back_to_base(self):
        folder = self.make_theme("nord", current=True)
        theme_auto.update_settings(workspace_wallpapers=True)
        theme_auto.set_workspace_wallpaper("nord", 2, "b.png")
        self.set_link(folder / "a.png")
        theme_auto.on_workspace(1)       # no own wallpaper: nothing changes
        theme_auto.on_workspace(2)
        theme_auto.on_workspace(3)       # back to what workspace 1 showed
        self.assertEqual(self.shown, ["b.png", "a.png"])

    def test_same_event_twice_is_harmless(self):
        folder = self.make_theme("nord", current=True)
        theme_auto.update_settings(workspace_wallpapers=True)
        theme_auto.set_workspace_wallpaper("nord", 2, "b.png")
        self.set_link(folder / "a.png")
        for ws in (1, 1, 2, 2, 1, 1):
            theme_auto.on_workspace(ws)
        self.assertEqual(self.shown, ["b.png", "a.png"])

    def test_hidden_or_missing_wallpaper_is_skipped(self):
        folder = self.make_theme("nord", current=True)
        theme_auto.update_settings(workspace_wallpapers=True)
        theme_auto.set_workspace_wallpaper("nord", 2, "b.png")
        theme_auto.set_workspace_wallpaper("nord", 3, "nope.png")
        theme_core.save_hidden_wallpapers({"nord": {"b.png"}})
        self.set_link(folder / "a.png")
        theme_auto.on_workspace(2)
        theme_auto.on_workspace(3)
        self.assertEqual(self.shown, [])

    def test_assignments_are_per_theme(self):
        self.make_theme("alpha")
        folder = self.make_theme("nord", current=True)
        theme_auto.update_settings(workspace_wallpapers=True)
        theme_auto.set_workspace_wallpaper("alpha", 2, "c.png")
        self.set_link(folder / "a.png")
        theme_auto.on_workspace(2)
        self.assertEqual(self.shown, [])

    def test_map_rejects_paths_and_bad_workspaces(self):
        with self.assertRaises(ValueError):
            theme_auto.set_workspace_wallpaper("nord", 0, "a.png")
        with self.assertRaises(ValueError):
            theme_auto.set_workspace_wallpaper("nord", 2, "../../etc/passwd")
        theme_auto.workspace_file().parent.mkdir(parents=True, exist_ok=True)
        theme_auto.workspace_file().write_text(json.dumps(
            {"nord": {"2": "a.png", "3": "x/y.png", "abc": "a.png", "500": "a.png"}, "../x": {"1": "a.png"}}))
        self.assertEqual(theme_auto.load_workspace_map(), {"nord": {2: "a.png"}})

    def test_workspaces_of_and_clear(self):
        theme_auto.set_workspace_wallpaper("nord", 2, "a.png")
        theme_auto.set_workspace_wallpaper("nord", 5, "a.png")
        theme_auto.set_workspace_wallpaper("nord", 3, "b.png")
        self.assertEqual(theme_auto.workspaces_of("nord", "a.png"), [2, 5])
        theme_auto.clear_wallpaper_workspaces("nord", "a.png")
        self.assertEqual(theme_auto.load_workspace_map(), {"nord": {3: "b.png"}})


class BackgroundStepTests(AutoTestCase):
    def test_next_and_previous_wrap(self):
        folder = self.make_theme("nord", current=True)
        self.set_link(folder / "c.png")
        theme_auto.step_background(1)
        theme_auto.step_background(-1)
        theme_auto.step_background(-1)
        self.assertEqual(self.shown, ["a.png", "c.png", "b.png"])

    def test_staged_copy_counts_as_the_same_wallpaper(self):
        self.make_theme("nord", current=True)
        staged = self.roots.state_current / "theme" / "backgrounds"
        staged.mkdir(parents=True)
        (staged / "b.png").write_bytes(png())
        self.set_link(staged / "b.png")
        theme_auto.step_background(1)
        self.assertEqual(self.shown, ["c.png"])

    def test_scroll_on_a_workspace_with_its_own_wallpaper_reassigns_it(self):
        folder = self.make_theme("nord", current=True)
        theme_auto.update_settings(workspace_wallpapers=True)
        theme_auto.set_workspace_wallpaper("nord", 2, "b.png")
        self.workspace = 2
        self.set_link(folder / "b.png")
        theme_auto.step_background(1)
        self.assertEqual(theme_auto.load_workspace_map(), {"nord": {2: "c.png"}})


class TickTests(AutoTestCase):
    def test_period_boundaries(self):
        at = lambda h, m: time.struct_time((2026, 10, 12, h, m, 0, 0, 285, -1))
        self.assertEqual(theme_auto.period_at(at(6, 59), "07:00", "19:00"), "night")
        self.assertEqual(theme_auto.period_at(at(7, 0), "07:00", "19:00"), "day")
        self.assertEqual(theme_auto.period_at(at(19, 0), "07:00", "19:00"), "night")
        # Day across midnight (a night-shift plan).
        self.assertEqual(theme_auto.period_at(at(23, 0), "22:00", "06:00"), "day")
        self.assertEqual(theme_auto.period_at(at(12, 0), "22:00", "06:00"), "night")

    def test_schedule_applies_only_at_a_boundary(self):
        self.make_theme("light")
        self.make_theme("dark", current=True)
        theme_auto.save_schedule(enabled=True, day="light", night="dark", day_at="07:00", night_at="19:00")
        noon = time.mktime((2026, 10, 12, 12, 0, 0, 0, 0, -1))
        theme_auto.tick(noon)
        self.assertEqual(self.applied, ["light"])
        (self.roots.state_current / "theme.name").write_text("dark\n")   # picked by hand
        theme_auto.tick(noon + 60)
        self.assertEqual(self.applied, ["light"])                         # stays until the next switch
        theme_auto.tick(time.mktime((2026, 10, 12, 20, 0, 0, 0, 0, -1)))
        self.assertEqual(self.applied, ["light"])                         # already dark: nothing to do
        theme_auto.tick(time.mktime((2026, 10, 13, 7, 0, 0, 0, 0, -1)))
        self.assertEqual(self.applied, ["light", "light"])

    def test_schedule_needs_both_themes_and_valid_settings(self):
        self.make_theme("light", current=True)
        theme_auto.update_settings(schedule={"enabled": True, "day": "../x", "night": "light",
                                             "day_at": "25:00", "night_at": "19:00"})
        plan = theme_auto.schedule()
        self.assertEqual((plan["day"], plan["day_at"]), ("", "07:00"))
        theme_auto.tick(time.mktime((2026, 10, 12, 12, 0, 0, 0, 0, -1)))
        self.assertEqual(self.applied, [])

    def test_slideshow_waits_for_its_interval(self):
        folder = self.make_theme("nord", current=True)
        self.set_link(folder / "a.png")
        theme_auto.update_settings(slideshow_minutes=10)
        start = 1_000_000.0
        theme_auto.tick(start)              # starts the clock
        theme_auto.tick(start + 300)
        self.assertEqual(self.shown, [])
        theme_auto.tick(start + 600)
        self.assertEqual(self.shown, ["b.png"])
        theme_auto.tick(start + 660)
        self.assertEqual(self.shown, ["b.png"])

    def test_slideshow_leaves_workspaces_with_their_own_wallpaper(self):
        folder = self.make_theme("nord", current=True)
        self.set_link(folder / "a.png")
        theme_auto.update_settings(slideshow_minutes=5, workspace_wallpapers=True)
        theme_auto.set_workspace_wallpaper("nord", 2, "a.png")
        self.workspace = 2
        theme_auto.tick(0.0)
        theme_auto.tick(400.0)
        self.assertEqual(self.shown, [])

    def test_bad_slideshow_value_is_off(self):
        theme_auto.update_settings(slideshow_minutes=7)
        self.assertEqual(theme_auto.slideshow_minutes(), 0)
        theme_auto.update_settings(slideshow_minutes=True)
        self.assertEqual(theme_auto.slideshow_minutes(), 0)


class SafetyTests(AutoTestCase):
    def test_oversized_wallpaper_is_never_shown(self):
        folder = self.make_theme("nord", wallpapers=("a.png", "bomb.png"), current=True)
        (folder / "bomb.png").write_bytes(png(60000, 60000))
        theme_auto.update_settings(workspace_wallpapers=True)
        theme_auto.set_workspace_wallpaper("nord", 2, "bomb.png")
        self.set_link(folder / "a.png")
        theme_auto.on_workspace(2)
        theme_auto.step_background(1)
        self.assertEqual(self.shown, [])

    def test_lock_refuses_a_symlink(self):
        lock = theme_auto.state_file().with_suffix(".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        target = Path(self.tmp.name) / "victim"
        target.write_text("keep")
        lock.symlink_to(target)
        with self.assertRaises(OSError):
            with theme_auto.locked():
                pass
        self.assertEqual(target.read_text(), "keep")


class CommandLineTests(AutoTestCase):
    def setUp(self):
        super().setUp()
        devnull = open(os.devnull, "w")
        self.addCleanup(devnull.close)
        self.patch(sys, "stderr", devnull)   # usage text for refused arguments

    def test_commands(self):
        folder = self.make_theme("nord", current=True)
        self.make_theme("alpha")
        self.set_link(folder / "a.png")
        self.assertEqual(theme_auto.main(["bg", "next"]), 0)
        self.assertEqual(self.shown, ["b.png"])
        self.assertEqual(theme_auto.main(["apply", "alpha"]), 0)
        self.assertEqual(self.applied, ["alpha"])
        self.assertIn("alpha", theme_auto.load_recent())

    def test_next_favorite_theme_cycles(self):
        for slug in ("alpha", "beta", "gamma"):
            self.make_theme(slug)
        (self.roots.state_current / "theme.name").write_text("beta\n")
        theme_auto.set_favorite("gamma", True)
        theme_auto.set_favorite("alpha", True)
        self.assertEqual(theme_auto.main(["theme", "next"]), 0)     # beta is no favorite: the first one
        self.assertEqual(self.applied, ["gamma"])
        theme_auto.main(["theme", "next"])
        theme_auto.main(["theme", "next"])
        self.assertEqual(self.applied, ["gamma", "alpha", "gamma"])
        theme_auto.main(["theme", "prev"])
        self.assertEqual(self.applied[-1], "alpha")

    def test_key_of_a_deleted_theme_says_so_and_is_freed(self):
        import theme_shortcuts as sc
        hypr = Path(self.tmp.name) / "hypr"
        hypr.mkdir()
        (hypr / "hyprland.lua").write_text("-- main\n")
        self.patch(sc, "HYPR_DIR", hypr)
        self.patch(sc, "config_errors", lambda: "")
        notices = []
        self.patch(theme_core, "notify", lambda title, body="": notices.append((title, body)))
        self.make_theme("nord", current=True)
        sc.save_and_apply({"apply:gone": sc.make_combo(["SUPER", "CTRL"], "G"),
                           "apply:nord": sc.make_combo(["SUPER", "CTRL"], "N")})
        self.assertEqual(theme_auto.main(["apply", "gone"]), 1)
        self.assertEqual(notices, [("Gone no longer exists", "Its key was removed and is free again.")])
        self.assertEqual(list(sc.load_state()), ["apply:nord"])
        self.assertEqual(self.applied, [])

    def test_bad_arguments_are_refused(self):
        for argv in ([], ["bg", "sideways"], ["apply", "../x"], ["workspace", "-1"],
                     ["workspace", "two"], ["rm", "-rf"]):
            self.assertEqual(theme_auto.main(argv), 2, argv)
        self.assertEqual(self.applied, [])

    def test_apply_refuses_unknown_theme(self):
        with self.assertRaises(FileNotFoundError):
            theme_auto.apply_theme("nope")

    def test_apply_unhides_the_theme(self):
        self.make_theme("alpha")
        theme_core.save_hidden({"alpha"})
        theme_auto.apply_theme("alpha")
        self.assertNotIn("alpha", theme_core.load_hidden())

    def test_workspace_theme_is_opt_in_and_switches_on_focus(self):
        self.make_theme("nord", current=True)
        self.make_theme("alpha")
        theme_auto.set_workspace_theme(2, "alpha")
        self.assertEqual(theme_auto.load_workspace_themes(), {2: "alpha"})
        theme_auto.apply_workspace(2)
        self.assertEqual(self.applied, [])
        theme_auto.update_settings(workspace_themes=True)
        theme_auto.apply_workspace(2)
        self.assertEqual(self.applied, ["alpha"])
        theme_auto.apply_workspace(2)
        self.assertEqual(self.applied, ["alpha"])
        theme_auto.apply_workspace(1)
        self.assertEqual(self.applied, ["alpha", "nord"])
        theme_auto.set_workspace_theme(2, None)
        self.assertEqual(theme_auto.load_workspace_themes(), {})

    def test_use_everywhere_clears_the_workspace_themes_with_a_safe_undo(self):
        self.make_theme("nord", current=True)
        self.make_theme("alpha")
        self.make_theme("beta")
        theme_auto.update_settings(workspace_themes=True)
        theme_auto.set_workspace_theme(2, "alpha")
        theme_auto.set_workspace_theme(3, "beta")
        self.workspace = 2
        theme_auto.apply_workspace(2)                        # on ws 2: alpha is applied
        saved = theme_auto.use_everywhere()
        self.assertEqual(saved["theme"], "alpha")
        self.assertEqual(theme_auto.load_workspace_themes(), {})
        theme_auto.apply_workspace(1)                        # alpha stays everywhere now
        self.assertEqual(self.applied, ["alpha"])
        self.assertTrue(theme_auto.workspace_themes_enabled())
        self.assertIsNone(theme_auto.use_everywhere())       # nothing left to clear
        self.assertTrue(theme_auto.undo_use_everywhere(saved))
        self.assertEqual(theme_auto.load_workspace_themes(), {2: "alpha", 3: "beta"})
        # Changed meanwhile (a new assignment): Undo leaves it alone.
        saved = theme_auto.use_everywhere()
        theme_auto.set_workspace_theme(5, "beta")
        self.assertFalse(theme_auto.undo_use_everywhere(saved))
        self.assertEqual(theme_auto.load_workspace_themes(), {5: "beta"})
        with patch.object(theme_core, "current_theme", return_value=""):
            self.assertIsNone(theme_auto.use_everywhere())          # no current theme: nothing cleared
        self.assertEqual(theme_auto.load_workspace_themes(), {5: "beta"})

    def test_workspace_theme_rejects_missing_theme_and_bad_workspace(self):
        with self.assertRaises(FileNotFoundError):
            theme_auto.set_workspace_theme(2, "missing")
        with self.assertRaises(ValueError):
            theme_auto.set_workspace_theme(0, None)

    def test_schedule_change_supersedes_workspace_theme_on_leave(self):
        self.make_theme("base", current=True)
        self.make_theme("nord")
        self.make_theme("night")
        theme_auto.set_workspace_theme(3, "nord")
        theme_auto.update_settings(workspace_themes=True)
        theme_auto.apply_workspace(3)
        self.assertEqual(self.applied, ["nord"])
        theme_auto.apply_theme("night")  # schedule or user choice while on workspace 3
        theme_auto.apply_workspace(1)
        self.assertEqual(self.applied, ["nord", "night"])
        state = theme_auto._read_json(theme_auto.workspace_theme_state_file(), {})
        self.assertEqual(state["base_theme"], "night")

    def test_manual_change_survives_switch_between_assigned_workspaces(self):
        self.make_theme("base", current=True)
        self.make_theme("north")
        self.make_theme("south")
        self.make_theme("manual")
        theme_auto.set_workspace_theme(2, "north")
        theme_auto.set_workspace_theme(3, "south")
        theme_auto.update_settings(workspace_themes=True)
        theme_auto.apply_workspace(2)
        theme_auto.apply_theme("manual")
        theme_auto.apply_workspace(3)
        theme_auto.apply_workspace(1)
        self.assertEqual(self.applied, ["north", "manual", "south", "manual"])

    def test_wallpaper_workspace_reset_keeps_schedule_and_slideshow_state(self):
        self.make_theme("nord", current=True)
        self.make_theme("other")
        theme_auto.update_settings(workspace_wallpapers=True)
        theme_auto._write_json(theme_auto.state_file(), {"theme": "other", "period": "day", "slide_at": 123})
        theme_auto.on_workspace(2)
        state = theme_auto._read_json(theme_auto.state_file(), {})
        self.assertEqual(state["period"], "day")
        self.assertEqual(state["slide_at"], 123)
        self.assertEqual(state["theme"], "nord")

    def test_night_boundary_updates_base_but_keeps_assigned_theme(self):
        self.make_theme("day", current=True)
        self.make_theme("night")
        self.make_theme("assigned")
        theme_auto.set_workspace_theme(2, "assigned")
        theme_auto.update_settings(workspace_themes=True)
        self.workspace = 2
        theme_auto.apply_workspace(2)
        theme_auto.save_schedule(enabled=True, day="day", night="night", day_at="07:00", night_at="19:00")
        theme_auto._write_json(theme_auto.state_file(), {"period": "day"})
        night_time = time.mktime((2026, 11, 6, 20, 0, 0, 0, 0, -1))
        theme_auto.tick(night_time)
        self.assertEqual(self.applied, ["assigned"])
        self.assertEqual(theme_auto._read_json(theme_auto.workspace_theme_state_file(), {})["base_theme"], "night")
        theme_auto.apply_workspace(1)
        self.assertEqual(self.applied, ["assigned", "night"])

    def test_flip_wallpaper_uses_active_background_when_current_else_first_visible(self):
        self.assertTrue(theme_auto.preview_flip_enabled())
        self.assertTrue(theme_auto.preview_pin_apply_theme_enabled())
        self.assertEqual(theme_auto.preview_flip_delay(), 1)
        theme_auto.store_default_settings()
        self.assertIs(theme_auto.settings().get("preview_flip_3d"), True)
        self.assertIs(theme_auto.settings().get("preview_pin_apply_theme"), True)
        self.assertEqual(theme_auto.settings().get("preview_flip_delay"), 1)
        theme_auto.update_settings(preview_flip_3d=False, preview_pin_apply_theme=False, preview_flip_delay=0)
        self.assertFalse(theme_auto.preview_flip_enabled())
        self.assertFalse(theme_auto.preview_pin_apply_theme_enabled())
        self.assertEqual(theme_auto.preview_flip_delay(), 0)
        theme_auto.store_default_settings()
        self.assertIs(theme_auto.settings().get("preview_flip_3d"), False)  # explicit off stays off
        self.assertIs(theme_auto.settings().get("preview_pin_apply_theme"), False)  # explicit off stays off
        self.assertEqual(theme_auto.settings().get("preview_flip_delay"), 0)  # explicit 0 stays 0

        bg_nord = self.make_theme("nord", ("a.png", "b.png", "c.png"), current=True)
        self.make_theme("alpha", ("first.png", "second.png"))
        empty_dir = self.roots.user_themes / "bare"
        empty_dir.mkdir(parents=True)
        (empty_dir / "colors.toml").write_text("")

        staged = self.roots.state_current / "theme" / "backgrounds"
        staged.mkdir(parents=True)
        staged_c = staged / "c.png"
        staged_c.write_bytes(png())
        self.set_link(staged_c)  # staged copy of c.png is active on desktop

        themes = {t.slug: t for t in theme_core.discover_themes()}
        self.assertEqual(theme_auto.flip_wallpaper(themes["nord"]), bg_nord / "c.png")
        self.assertEqual([p.name for p in theme_auto.flip_wallpapers(themes["nord"])],
                         ["c.png", "a.png", "b.png"])
        self.assertEqual(theme_auto.flip_wallpaper(themes["alpha"]).name, "first.png")
        self.assertEqual([p.name for p in theme_auto.flip_wallpapers(themes["alpha"])],
                         ["first.png", "second.png"])
        self.assertTrue(theme_auto.remember_omarchy_theme_background(
            "alpha", self.roots.user_themes / "alpha" / "backgrounds" / "second.png"))
        self.assertEqual(theme_auto.remembered_omarchy_theme_background("alpha"), "second.png")
        self.assertEqual([p.name for p in theme_auto.flip_wallpapers(themes["alpha"])],
                         ["second.png", "first.png"])
        # The state file is read bounded, never through a link or a FIFO (it runs while a card is built).
        state = self.roots.state_current.parent / "theme-backgrounds" / "alpha"
        state.unlink()
        os.mkfifo(state)
        self.assertIsNone(theme_auto.remembered_omarchy_theme_background("alpha"))
        state.unlink()
        state.write_text("x" * 5000)
        self.assertIsNone(theme_auto.remembered_omarchy_theme_background("alpha"))
        state.unlink()
        (state.parent / "elsewhere").write_text("second.png\n")
        state.symlink_to(state.parent / "elsewhere")
        self.assertIsNone(theme_auto.remembered_omarchy_theme_background("alpha"))
        theme_core.save_hidden_wallpapers({"alpha": {"first.png"}})
        self.assertEqual(theme_auto.flip_wallpaper(themes["alpha"]).name, "second.png")
        self.assertEqual([p.name for p in theme_auto.flip_wallpapers(themes["alpha"])],
                         ["second.png"])
        self.assertIsNone(theme_auto.flip_wallpaper(themes["bare"]))
        self.assertEqual(theme_auto.flip_wallpapers(themes["bare"]), [])

        with unittest.mock.patch.object(theme_core, "set_desktop_background", return_value=True) as mock_bg:
            self.assertTrue(theme_auto.pin_current_wallpaper("nord", bg_nord / "b.png"))
            mock_bg.assert_called_once_with(bg_nord / "b.png")
        remembered = (self.roots.state_current.parent / "theme-backgrounds" / "nord").read_text().strip()
        self.assertTrue(remembered.endswith("/b.png"))


class LockDeadlineTests(AutoTestCase):
    def test_a_step_waiting_too_long_for_the_lock_gives_up(self):
        import fcntl
        lock = theme_auto.state_file().with_suffix(".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)                    # another step keeps it
        try:
            self.patch(theme_auto, "AUTOMATION_LOCK_SECONDS", 0.2)
            self.patch(theme_auto, "tick", lambda: self.fail("ran without the lock"))
            self.assertEqual(theme_auto.main(["tick"]), 1)
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
