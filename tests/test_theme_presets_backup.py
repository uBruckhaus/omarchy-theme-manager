"""Preset and backup behavior without a running desktop."""
import json
import os
import ast
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_auto
import theme_backup
import theme_core
import theme_presets
import theme_shortcuts
import i18n
import extra_i18n


class PresetBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "config"
        self.config.mkdir()
        # Never touch the real home: restore() switches Omarchy sync, which
        # writes the theme-set hook, the menu override and the picker cache.
        home = self.base / "home"
        home.mkdir()
        for patcher in (patch.dict(os.environ, {"HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache")}),
                        patch.object(theme_core, "settings_file", return_value=self.config / "settings.json"),
                        patch.object(theme_core, "refresh_shell_theme_rows")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_presets_round_trip_and_duplicate_name(self):
        record = {"name": "Night", "theme": "nord", "wallpaper": "a.png", "font": None}
        theme_presets.save([record])
        self.assertEqual(theme_presets.load(), [record])
        self.assertTrue(theme_presets.delete("night"))
        self.assertEqual(theme_presets.load(), [])

    def test_preset_rejects_unsafe_names_and_duplicate_records(self):
        for name in ("bad\nname", "bad\tname", "bad\x7fname"):
            with self.assertRaises(ValueError):
                theme_presets.clean_name(name)
        for wallpaper in ("..", "bad\\name.png", "bad\x7fname.png", "text.txt"):
            self.assertIsNone(theme_presets._valid({"name": "Safe", "theme": "nord",
                                                     "wallpaper": wallpaper, "font": None}))
        record = {"name": "Same", "theme": "nord", "wallpaper": None, "font": None}
        with self.assertRaises(ValueError):
            theme_presets.save([record, {**record, "name": "same"}])

    def test_wallpaper_only_requires_its_theme(self):
        record = {"name": "Night", "theme": "nord", "wallpaper": "a.png", "font": None}
        fake = type("Theme", (), {"slug": "nord"})()
        with patch.object(theme_core, "discover_themes", return_value=[fake]), \
             patch.object(theme_auto, "wallpaper_named", return_value=Path("a.png")), \
             patch.object(theme_core, "current_theme", return_value="other"):
            with self.assertRaises(ValueError):
                theme_presets.assign_workspace(record, 3, theme=False, wallpaper=True)
        self.assertEqual(theme_auto.load_workspace_map(), {})

    def test_replace_preset_keeps_its_place(self):
        first = {"name": "Night", "theme": "nord", "wallpaper": None, "font": None}
        second = {"name": "Day", "theme": "light", "wallpaper": None, "font": None}
        theme_presets.save([first, second])
        with patch.object(theme_presets, "capture_current", return_value={
                "name": "night", "theme": "new", "wallpaper": "a.png", "font": None}):
            theme_presets.replace_current("night")
        self.assertEqual([item["name"] for item in theme_presets.load()], ["night", "Day"])
        self.assertEqual(theme_presets.load()[0]["theme"], "new")

    def test_theme_rename_updates_presets(self):
        theme_presets.save([{"name": "Night", "theme": "old", "wallpaper": "a.png", "font": None}])
        theme_presets.rename_theme("old", "new")
        self.assertEqual(theme_presets.load()[0]["theme"], "new")

    def test_assign_parts_of_preset(self):
        record = {"name": "Night", "theme": "nord", "wallpaper": "a.png", "font": None}
        fake = type("Theme", (), {"slug": "nord"})()
        with patch.object(theme_core, "discover_themes", return_value=[fake]), \
             patch.object(theme_auto, "wallpaper_named", return_value=Path("a.png")):
            theme_presets.assign_workspace(record, 3, theme=True, wallpaper=False)
            self.assertEqual(theme_auto.load_workspace_themes(), {3: "nord"})
            self.assertEqual(theme_auto.load_workspace_map(), {})
            theme_presets.assign_workspace(record, 3, theme=False, wallpaper=True)
        self.assertEqual(theme_auto.load_workspace_map(), {"nord": {3: "a.png"}})
        self.assertTrue(theme_auto.workspace_themes_enabled())
        self.assertTrue(theme_auto.workspace_wallpapers_enabled())

    def test_backup_export_restore_keeps_prior_copy(self):
        theme_core._atomic_write(self.config / "settings.json", '{"workspace_themes": true}\n')
        theme_presets.save([{"name": "One", "theme": "nord", "wallpaper": None, "font": None}])
        target = self.base / "backup.json"
        theme_backup.export(target)
        self.assertEqual(set(theme_backup.preview(target)), {"settings.json", "presets.json"})
        theme_core._atomic_write(self.config / "settings.json", '{"workspace_themes": false}\n')
        previous = theme_backup.restore(target)
        self.assertTrue(json.loads((self.config / "settings.json").read_text())["workspace_themes"])
        self.assertFalse(json.loads(previous.read_text())["files"]["settings.json"]["workspace_themes"])

    def test_backup_rejects_unknown_files_before_restore(self):
        target = self.base / "backup.json"
        target.write_text(json.dumps({"format": "theme-manager-backup", "version": 1,
                                      "files": {"../../bad": {}}}))
        with self.assertRaises(ValueError):
            theme_backup.restore(target)

    def test_backup_rejects_symlink_and_bad_presets(self):
        target = self.base / "backup.json"
        target.symlink_to(self.base / "elsewhere.json")
        with self.assertRaises(OSError):
            theme_backup.preview(target)
        target.unlink()
        target.write_text(json.dumps({"format": "theme-manager-backup", "version": 1,
                                      "files": {"presets.json": [{"name": "x", "theme": "../bad",
                                                                 "wallpaper": None, "font": None}]}}))
        with self.assertRaises(ValueError):
            theme_backup.restore(target)
        self.assertFalse((self.config / "presets.json").exists())

    def test_restore_removes_files_missing_from_backup(self):
        theme_core._atomic_write(self.config / "settings.json", '{"sync_omarchy": false}\n')
        target = self.base / "backup.json"
        theme_backup.export(target)
        theme_presets.save([{"name": "Later", "theme": "nord", "wallpaper": None, "font": None}])
        theme_core._atomic_write(self.config / "shortcuts.json", '{}\n')
        def apply_shortcuts(state):
            theme_core._atomic_write(self.config / "shortcuts.json", json.dumps(state) + "\n")
        with patch.object(theme_shortcuts, "save_and_apply", side_effect=apply_shortcuts), \
             patch.object(theme_core, "set_sync_enabled"):
            theme_backup.restore(target)
        self.assertFalse((self.config / "presets.json").exists())
        self.assertFalse((self.config / "shortcuts.json").exists())

    def test_empty_backup_restores_empty_state(self):
        target = self.base / "empty-backup.json"
        theme_backup.export(target)
        self.assertEqual(theme_backup.preview(target), {})
        theme_core._atomic_write(self.config / "settings.json", '{"sync_omarchy": false}\n')
        with patch.object(theme_core, "set_sync_enabled"):
            theme_backup.restore(target)
        self.assertFalse((self.config / "settings.json").exists())

    def test_sync_failure_rolls_back_shortcuts_and_settings(self):
        old = {"open": {"mods": ["SUPER"], "key": "T"}}
        new = {"open": {"mods": ["SUPER"], "key": "Y"}}
        theme_core._atomic_write(self.config / "settings.json", '{"sync_omarchy": true}\n')
        theme_core._atomic_write(self.config / "shortcuts.json", json.dumps(new) + "\n")
        target = self.base / "backup.json"
        theme_backup.export(target)
        theme_core._atomic_write(self.config / "settings.json", '{"sync_omarchy": false}\n')
        theme_core._atomic_write(self.config / "shortcuts.json", json.dumps(old) + "\n")
        sync_calls = []
        def sync(on, *_args):
            sync_calls.append(on)
            if len(sync_calls) == 1:
                raise OSError("sync failed")
        def apply_shortcuts(state):
            theme_core._atomic_write(self.config / "shortcuts.json", json.dumps(state) + "\n")
        with patch.object(theme_shortcuts, "save_and_apply", side_effect=apply_shortcuts), \
             patch.object(theme_core, "set_sync_enabled", side_effect=sync):
            with self.assertRaisesRegex(OSError, "sync failed"):
                theme_backup.restore(target)
        self.assertEqual(sync_calls, [True, False])
        self.assertEqual(json.loads((self.config / "shortcuts.json").read_text()), old)
        self.assertFalse(json.loads((self.config / "settings.json").read_text())["sync_omarchy"])

    def test_backup_rejects_duplicate_shortcuts_and_unsafe_settings(self):
        target = self.base / "backup.json"
        records = {"open": {"mods": ["SUPER"], "key": "T"},
                   "next-wallpaper": {"mods": ["SUPER"], "key": "T"}}
        for files in ({"shortcuts.json": records},
                      {"settings.json": {"screenshot_folder": "/tmp/../etc"}},
                      {"presets.json": [{"name": "Same", "theme": "nord", "wallpaper": None, "font": None},
                                        {"name": "same", "theme": "nord", "wallpaper": None, "font": None}]}):
            target.write_text(json.dumps({"format": "theme-manager-backup", "version": 1, "files": files}))
            with self.assertRaises(ValueError):
                theme_backup.preview(target)

    def test_backup_normalizes_header_order(self):
        target = self.base / "backup.json"
        target.write_text(json.dumps({"format": "theme-manager-backup", "version": 1,
                                      "files": {"settings.json": {"header_order":
                                      ["info", "unknown", "info", "hidden"]}}}))
        self.assertEqual(theme_backup.preview(target)["settings.json"]["header_order"],
                         ["info", "hidden", "import", "presets", "ask"])
        target.write_text(json.dumps({"format": "theme-manager-backup", "version": 1,
                                      "files": {"settings.json": {"header_order": 42}}}))
        self.assertEqual(theme_backup.preview(target)["settings.json"]["header_order"],
                         ["import", "hidden", "presets", "ask", "info"])

    def test_backup_names_are_dated_and_safety_copy_does_not_reuse_path(self):
        from datetime import datetime
        moment = datetime(2026, 11, 6, 9, 30, 15)
        self.assertEqual(theme_backup.default_export_name(moment), "theme-manager-backup-2026-11-06.json")
        first = theme_backup.safety_copy_path(moment)
        first.write_text("old")
        second = theme_backup.safety_copy_path(moment)
        self.assertNotEqual(first, second)
        self.assertIn("2026-11-06-09-30-15", second.name)

    def test_new_interface_texts_cover_every_language(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / "theme_popup.py").read_text())
        keys = {node.args[0].value for node in ast.walk(source)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
        for code in ("de", "fr", "es", "it", "pt", "nl", "pl"):
            table = i18n.STRINGS[code]
            for key in keys:
                self.assertIn(key, table, f"{code}: {key}")
                self.assertEqual(sorted(re.findall(r"\{\d+\}", key)),
                                 sorted(re.findall(r"\{\d+\}", table[key])), f"{code}: {key}")
        self.assertEqual(set(extra_i18n.REVIEW_TRANSLATIONS),
                         set(extra_i18n.REVIEW_TRANSLATIONS) & keys)

    def test_new_translations_use_existing_terms(self):
        tables = (extra_i18n.EXTRA, extra_i18n.NEW_TRANSLATIONS,
                  extra_i18n.REVIEW_TRANSLATIONS)
        disallowed = {
            "de": ("Arbeitsfläche", "Design", "Hintergrundbild"),
            "nl": ("werkruimte", "Werkruimte"),
            "pt": ("Guardar", "Eliminar", "ficheiro", "definições", "restauro"),
            "pl": ("Ustawienia wstępne",),
        }
        for code, terms in disallowed.items():
            for table in tables:
                for key, translations in table.items():
                    for term in terms:
                        self.assertNotIn(term, translations.get(code, ""), f"{code}: {key}")


if __name__ == "__main__":
    unittest.main()
