"""Workspace presets inside a theme preset, and in backups."""
from contextlib import nullcontext
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_backup
import theme_core
import theme_presets
import workspace_tiling as tile

SNAPSHOT = {"maxWindows": 3, "workspaceAutoPresets": {"2": "grid"}, "workspaceModes": {"4": "manual"},
            "workspaceLayouts": {"4": {"mode": "manual", "apps": [
                {"name": "Foot", "argv": ["foot", "nvim"], "argv_source": "terminal"}],
                "windows": [{"class": "foot", "at": [0, 0], "size": [960, 1080], "app": {"argv": ["foot", "nvim"]}}]}}}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for patcher in (patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root), "HOME": str(root / "home"),
                                                 "XDG_CACHE_HOME": str(root / "cache")}),
                        patch.object(theme_core, "refresh_shell_theme_rows"),
                        patch.object(theme_core, "settings_file", return_value=root / "omarchy/theme-manager/settings.json"),
                        patch.object(tile, "runtime_lock", lambda: nullcontext()),
                        patch.object(tile, "omarchy_modes_supported", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        (root / "omarchy/theme-manager").mkdir(parents=True)

    def test_theme_preset_keeps_snapshot_of_the_store(self):
        tile.save_store(dict(SNAPSHOT, active=True, autoMoveOnRestore=True))
        self.assertEqual(tile.snapshot(), SNAPSHOT)          # no on/off switches in a preset
        base = {"name": "Work", "theme": "nord", "wallpaper": None, "font": None}
        self.assertEqual(theme_presets._valid(dict(base, workspaces=SNAPSHOT))["workspaces"], SNAPSHOT)
        for bad in ([], {"active": True}):
            self.assertIsNone(theme_presets._valid(dict(base, workspaces=bad)))
        # An older snapshot: its App Preset only marked in the layout. Kept, normalized.
        old = {"workspaceLayouts": {"3": {"mode": "manual", "apps": []}}}
        self.assertEqual(theme_presets._valid(dict(base, workspaces=old))["workspaces"]["workspaceModes"], {"3": "manual"})

    def test_apply_snapshot_saves_lays_out_and_restores(self):
        tile.save_store({"active": True, "maxWindows": 2})
        with patch.object(tile, "hypr", return_value=json.dumps([{"id": 1}, {"id": 4}])), \
                patch.object(tile, "apply_layout_preset", return_value={"status": "ok"}) as apply, \
                patch.object(tile, "restore_workspace", return_value={"status": "ok", "failed": []}) as restore:
            result = tile.apply_snapshot(SNAPSHOT)
        store = tile.load_store()
        self.assertTrue(store["active"])                    # kept
        self.assertEqual(store["maxWindows"], 3)
        self.assertEqual(sorted(call.args[:2] for call in apply.call_args_list), [(1, "master-left"), (2, "grid")])
        restore.assert_called_once_with(4, False, "move")
        self.assertEqual(result["restored"], [{"workspace": 4, "status": "ok", "failed": 0}])
        with patch.object(tile, "apply_snapshot", return_value={"layouts": [], "restored": [
                {"workspace": 4, "status": "partial", "failed": 1}]}):
            self.assertEqual(theme_presets.apply_workspaces(SNAPSHOT), ["some workspace apps failed"])

    def test_snapshot_replaces_every_preset_key(self):
        tile.save_store({"active": True, "autoMoveOnRestore": True, "workspaceCaps": {"3": 1},
                         "workspaceModes": {"5": "manual"}, "workspaceLayouts": {"5": {"apps": []}}})
        with patch.object(tile, "hypr", return_value="[]"), \
                patch.object(tile, "apply_layout_preset", return_value={"status": "ok"}):
            tile.apply_snapshot({"maxWindows": 2}, restore_apps=False)
        self.assertEqual(tile.load_store(), {"maxWindows": 2, "active": True, "autoMoveOnRestore": True})

    def test_saved_commands_are_never_run_whatever_their_label(self):
        """A forged entry in Workspace Presets' shell.json (marketplace review, 2.0.0):
        argv_source "terminal" plus a real terminal is not trusted either."""
        app = {"argv_source": "terminal", "desktop_id": "foot.desktop"}
        for saved in (["foot", "nvim"], ["/usr/bin/foot", "-e", "sh", "-c", "touch /tmp/x"],
                      ["/usr/bin/kitty", "-e", "top"], ["/tmp/foot", "x"], ["sh", "-c", "x"]):
            self.assertEqual(tile.trusted_argv(dict(app, argv=saved)), [])
            self.assertNotIn("argv", tile.restorable(dict(app, argv=saved)))      # the launcher is used

    def test_autostart_waits_while_the_plugin_is_on_the_bar(self):
        with patch.object(tile, "wp_on_bar", return_value=True), patch.object(tile, "_autostart_all") as run, \
                patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.temp.name}):
            self.assertEqual(tile.autostart_all()["status"], "skipped")
        run.assert_not_called()

    def test_autostart_only_app_presets_in_use(self):
        tile.save_store({"workspaceModes": {"2": "manual", "3": "auto"},
                         "workspaceLayouts": {"2": {"autostart": True, "apps": []}, "3": {"autostart": True, "apps": []}}})
        both = {str(ws): tile.app_fingerprint({"apps": []}) for ws in (2, 3)}       # confirmed in the panel
        with patch.object(tile, "_restore_workspace", return_value={"status": "ok"}) as restore, \
                patch.object(tile, "load_approvals", return_value=both), patch.object(tile, "send_notification"):
            tile._autostart_all(False)
        self.assertEqual([call.args[0] for call in restore.call_args_list], [2])

    def test_presets_file_never_grows_past_what_load_reads(self):
        big = {"workspaceLayouts": {str(n): {"mode": "manual", "apps": [{"name": "x" * 1000} for _ in range(30)]}
                                    for n in range(1, 3)}}
        presets = [{"name": f"P{n}", "theme": "nord", "wallpaper": None, "font": None, "workspaces": big}
                   for n in range(20)]
        with self.assertRaises(ValueError):
            theme_presets.save(presets)

    def test_damaged_current_file_does_not_block_a_restore(self):
        base = theme_core.settings_file().parent
        (base / "favorites.json").write_text("{not json")
        backup = base.parent / "good.json"
        backup.write_text(json.dumps({"format": "theme-manager-backup", "version": 1,
                                      "files": {"favorites.json": ["nord"]}}))
        os.chmod(backup, 0o600)
        with patch.object(theme_core, "set_sync_enabled"), patch.object(theme_core, "sync_enabled", return_value=False):
            theme_backup.restore(backup, base.parent / "prev.json")
        self.assertEqual(json.loads((base / "favorites.json").read_text()), ["nord"])

    def test_failed_restore_puts_a_damaged_file_back_byte_for_byte(self):
        base = theme_core.settings_file().parent
        damaged = b"{\xff not json"
        (base / "settings.json").write_bytes(damaged)
        backup = base.parent / "good2.json"
        backup.write_text(json.dumps({"format": "theme-manager-backup", "version": 1, "files": {"favorites.json": ["nord"]}}))
        os.chmod(backup, 0o600)
        with patch.object(theme_core, "set_sync_enabled", side_effect=[RuntimeError("boom"), None]), \
                patch.object(theme_core, "sync_enabled", return_value=False):
            with self.assertRaises(RuntimeError):
                theme_backup.restore(backup, base.parent / "prev2.json")
        self.assertEqual((base / "settings.json").read_bytes(), damaged)

    def test_swap_slots_swaps_the_nearest_live_windows(self):
        clients = [{"address": "0xa", "workspace": {"id": 2}, "mapped": True, "floating": False, "at": [0, 0], "size": [960, 1080]},
                   {"address": "0xb", "workspace": {"id": 2}, "mapped": True, "floating": False, "at": [960, 0], "size": [960, 1080]},
                   {"address": "0xc", "workspace": {"id": 3}, "mapped": True, "floating": False, "at": [0, 0], "size": [10, 10]}]
        calls = []
        def hypr(*args):
            calls.append(args)
            return json.dumps(clients) if args[0] == "clients" else ""
        with patch.object(tile, "hypr", side_effect=hypr):
            result = tile.swap_slots(2, [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 1.0)], 0, 1)
        self.assertEqual(result["status"], "ok")
        self.assertIn('window="address:0xa", with="address:0xb"', calls[-1][1])
        with patch.object(tile, "hypr", side_effect=hypr):
            self.assertEqual(tile.swap_slots(2, [(0, 0, 1, 1)], 0, 0)["status"], "noop")

    def test_omarchy_free_mode_kept_without_own_preset(self):
        tile.save_store({"active": True})
        with patch.object(tile, "hypr", return_value=json.dumps([{"id": 1}, {"id": 2}])), \
                patch.object(tile, "omarchy_workspace_modes", return_value={1: "floating", 2: "dwindle"}), \
                patch.object(tile, "apply_layout_preset", return_value={"status": "ok"}) as apply:
            tile.apply_snapshot({"defaultAutoPresets": {"2": "stacked"}}, restore_apps=False)
        self.assertEqual([call.args[:2] for call in apply.call_args_list], [(2, "stacked")])

    def test_backups_drop_saved_commands(self):
        preset = [{"name": "Work", "theme": "nord", "wallpaper": None, "font": None, "workspaces": SNAPSHOT}]
        cleaned = theme_backup._validate_value("presets.json", json.loads(json.dumps(preset)))
        layout = cleaned[0]["workspaces"]["workspaceLayouts"]["4"]
        self.assertNotIn("argv", layout["apps"][0])
        self.assertNotIn("\"argv\"", json.dumps(cleaned))
        self.assertEqual(layout["windows"], [{"class": "foot", "at": [0, 0], "size": [960, 1080]}])   # an app left empty is dropped
        store = theme_backup._validate_value("workspaces.json", dict(SNAPSHOT, active=True, junk=1))
        self.assertNotIn("junk", store)
        self.assertTrue(store["active"])
        self.assertNotIn("argv", store["workspaceLayouts"]["4"]["apps"][0])


if __name__ == "__main__":
    unittest.main()
