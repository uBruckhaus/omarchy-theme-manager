import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

import workspace_tiling as tile

# Ported from the Workspace Presets plugin's tests, which wrote its bar entry
# in shell.json. Here that entry stands in for Theme Manager's own
# workspaces.json: load_store reads it every time (as the one-time import
# does) and the plugin never counts as "still on the bar".
WP_ID = tile.WP_PLUGIN_ID
_REAL_LOAD = tile.load_store
_REAL_WP_ON_BAR = tile.wp_on_bar


def _store_from_entry():
    entry = tile.wp_entry()
    if entry is None:
        return {}
    store = tile.clean_store(entry)
    store["active"] = entry.get("active", True) is True
    return store


# Tests never touch this machine's Omarchy: no workspace modes unless a test
# turns them on, and any state goes to a throwaway directory.
_guards = []


def setUpModule():
    state = tempfile.TemporaryDirectory()
    _guards.append(state)
    for patcher in (patch.object(tile, "omarchy_modes_supported", return_value=False),
                    patch.object(tile, "load_store", side_effect=_store_from_entry),
                    patch.object(tile, "wp_on_bar", return_value=False),
                    patch.dict(os.environ, {"XDG_STATE_HOME": state.name})):
        patcher.start()
        _guards.append(patcher)


def tearDownModule():
    for guard in reversed(_guards):
        (guard.stop if hasattr(guard, "stop") else guard.cleanup)()


def client(address="0xaa", workspace=1, **values):
    return dict(address=address, workspace={"id": workspace}, mapped=True,
                floating=False, fullscreen=0, hidden=False, grouped=[], **values)


WORKSPACES = [{"id": 1, "monitor": "A"}, {"id": 2, "monitor": "A"},
              {"id": 3, "monitor": "B"}]


class PlanningTests(unittest.TestCase):
    def test_overflow_skips_app_preset_destination(self):
        clients = [client(), client("0xbb"), client("0xcc")]
        plan = tile.plan_move(clients, WORKSPACES, "0xcc", 2,
                              workspace_modes={2: "manual"})
        self.assertEqual(plan["target"], 4)

    def test_new_workspace_skips_inactive_app_preset(self):
        plan = tile.plan_move([client(), client("0xbb"), client("0xcc")],
                              WORKSPACES, "0xcc", 2,
                              workspace_modes={2: "manual", 4: "manual"})
        self.assertEqual(plan["target"], 5)

    def test_exact_cap_does_not_move(self):
        self.assertIsNone(tile.plan_move([client(), client("0xbb")], WORKSPACES, "0xaa", 2))

    def test_native_workspace_has_no_limit_even_with_a_saved_cap(self):
        clients = [client(f"0x{i:x}") for i in range(1, 9)]
        self.assertIsNone(tile.plan_move(clients, WORKSPACES, "0x8", 2,
                                        workspace_caps={1: 1},
                                        workspace_auto_presets={1: "none"}))

    def test_native_default_has_no_limit_but_explicit_preset_does(self):
        clients = [client(), client("0xbb"), client("0xcc")]
        self.assertIsNone(tile.plan_move(clients, WORKSPACES, "0xcc", 2,
                                        default_auto_presets={"2": "none"}))
        plan = tile.plan_move(clients, WORKSPACES, "0xcc", 2,
                              workspace_auto_presets={1: "side-by-side"},
                              default_auto_presets={"2": "none"})
        self.assertEqual(plan["target"], 2)

    def test_native_overflow_destination_is_not_bound_to_its_saved_cap(self):
        self.assertEqual(tile.pick_target_ws(WORKSPACES, {1: 3, 2: 8}, 1, 2,
                                             {2: 1}, {2: "none"}), 2)

    def test_only_new_window_is_targeted(self):
        plan = tile.plan_move([client(), client("0xbb"), client("0xcc")], WORKSPACES, "0xcc", 2)
        self.assertEqual(plan, {"address": "0xcc", "source": 1, "target": 2})

    def test_float_fullscreen_hidden_and_unmapped_do_not_count(self):
        for flag, value in [("floating", True), ("fullscreen", 2),
                            ("mapped", False), ("hidden", True)]:
            with self.subTest(flag=flag):
                ignored = client("0xbb")
                ignored[flag] = value
                self.assertEqual(tile.tiling_counts([client(), ignored]), {1: 1})

    def test_all_negative_workspaces_are_ignored(self):
        for workspace in [-1, -98, -1337, 0]:
            self.assertFalse(tile.is_tiled(client(workspace=workspace)))
            self.assertIsNone(tile.pick_target_ws(WORKSPACES, {}, workspace, 2))

    def test_grouped_window_is_not_moved(self):
        grouped = client()
        grouped["grouped"] = ["0xaa", "0xbb"]
        self.assertIsNone(tile.plan_move([grouped, client("0xbb")], WORKSPACES, "0xaa", 1))

    def test_new_workspace_skips_other_monitor(self):
        self.assertEqual(tile.pick_target_ws(WORKSPACES, {1: 3, 2: 2}, 1, 2), 4)

    def test_existing_room_on_same_monitor(self):
        self.assertEqual(tile.pick_target_ws(WORKSPACES, {1: 3, 2: 1}, 1, 2), 2)

    def test_never_scans_backwards(self):
        self.assertEqual(tile.pick_target_ws(WORKSPACES, {}, 2, 2), 4)

    def test_unknown_workspace_is_ignored(self):
        self.assertIsNone(tile.pick_target_ws(WORKSPACES, {}, 99, 2))

    def test_address_validation_blocks_code_injection(self):
        self.assertEqual(tile.normalize_addr("0xAB12"), "0xab12")
        for value in ["", 'aa\"}); os.exit()', "address:0xaa", "../aa"]:
            with self.assertRaises(ValueError):
                tile.move_expression(value, 2, False)

    def test_dispatch_targets_address_without_focusing(self):
        code = tile.move_expression("aa", 4, False)
        self.assertIn('window="address:0xaa"', code)
        self.assertIn('workspace="4"', code)
        self.assertIn("follow=false", code)
        self.assertNotIn("focus(", code)


    def test_workspace_specific_cap_allows_more_windows(self):
        clients = [client(), client("0xbb"), client("0xcc")]
        # Global cap is 2, but workspace 1 has custom cap 3. 3 windows should not move.
        self.assertIsNone(tile.plan_move(clients, WORKSPACES, "0xcc", 2, {1: 3}))

    def test_workspace_specific_cap_moves_earlier(self):
        clients = [client(workspace=2), client("0xbb", workspace=2)]
        # Global cap is 2, but workspace 2 has custom cap 1. 2nd window should move.
        plan = tile.plan_move(clients, WORKSPACES, "0xbb", 2, {2: 1})
        self.assertEqual(plan, {"address": "0xbb", "source": 2, "target": 4})

    def test_target_workspace_skips_full_workspace_with_custom_cap(self):
        # Workspaces: 1 (source), 2 (same monitor, 1 window), 3 (monitor B)
        # Workspace 2 has custom cap of 1, so with 1 window it is already full.
        counts = {1: 3, 2: 1}
        target = tile.pick_target_ws(WORKSPACES, counts, 1, 2, {2: 1})
        # Should skip workspace 2 and allocate next available ID on monitor A (4).
        self.assertEqual(target, 4)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.directory.name,
                                                    "XDG_CONFIG_HOME": self.directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.path = Path(self.directory.name) / "omarchy/shell.json"
        self.path.parent.mkdir()
        self.write_settings({"id": WP_ID})

    def write_settings(self, entry):
        self.path.write_text(json.dumps({"bar": {"layout": {"right": [entry]}}}))

    def test_invalid_caps_fall_back(self):
        for cap in [0, 5, 8, 9, "3", True, None]:
            self.write_settings({"id": WP_ID, "maxWindows": cap})
            self.assertEqual(tile.read_settings()["cap"], 2)

    def test_workspace_caps_parsing(self):
        self.write_settings({
            "id": WP_ID,
            "maxWindows": 3,
            "workspaceCaps": {
                "1": 4,
                "2": 1,
                "invalid": 2,
                "0": 2,
                "-1": 2,
                "3": 0,
                "4": 9,
                "5": "2",
                "6": True,
                "7": 5,
            }
        })
        settings = tile.read_settings()
        self.assertEqual(settings["cap"], 3)
        self.assertEqual(settings["workspace_caps"], {1: 4, 2: 1})

    def test_paused_and_removed_plugins_do_not_query_or_move(self):
        for entry in [{"id": WP_ID, "active": False}, {"id": "another.plugin"}]:
            self.write_settings(entry)
            with patch.object(tile, "hypr") as hypr:
                self.assertEqual(tile.handle_open("0xaa")["status"], "paused")
                hypr.assert_not_called()

    def test_dry_run_never_dispatches(self):
        clients = [client(), client("0xbb"), client("0xcc")]
        with patch.object(tile, "hypr", side_effect=[json.dumps(clients), json.dumps(WORKSPACES)]) as hypr:
            self.assertEqual(tile.handle_open("0xaa", True)["status"], "planned")
            self.assertEqual(hypr.call_count, 2)

    def test_native_open_does_not_move_or_reapply_layout(self):
        self.write_settings({"id": WP_ID, "maxWindows": 1,
                             "workspaceCaps": {"1": 2},
                             "workspaceAutoPresets": {"1": "none"}})
        clients = [client(f"0x{i:x}") for i in range(1, 9)]
        with patch.object(tile, "hypr", side_effect=[json.dumps(clients), json.dumps(WORKSPACES)]) as hypr, \
                patch.object(tile, "apply_layout_preset") as apply:
            self.assertEqual(tile.handle_open("0x8")["status"], "unchanged")
            self.assertFalse(any(call.args[0] == "eval" for call in hypr.call_args_list))
            apply.assert_not_called()

    def test_workspace_caps_applied_in_handle_open(self):
        # Global cap 2, workspace 1 cap 3. 3 windows on workspace 1.
        self.write_settings({"id": WP_ID, "maxWindows": 2, "workspaceCaps": {"1": 3}})
        clients = [client(), client("0xbb"), client("0xcc")]
        with patch.object(tile, "hypr", side_effect=[json.dumps(clients), json.dumps(WORKSPACES)]):
            self.assertEqual(tile.handle_open("0xcc", True)["status"], "unchanged")

    def test_missing_window_retry_is_bounded(self):
        with patch.object(tile, "hypr", return_value="[]") as hypr, patch.object(tile.time, "sleep"):
            self.assertEqual(tile.handle_open("0xaa")["status"], "unchanged")
            self.assertEqual(hypr.call_count, 5)

    def test_unconfirmed_move_is_an_error(self):
        clients = [client(), client("0xbb"), client("0xcc")]
        results = [json.dumps(clients), json.dumps(WORKSPACES), "error", json.dumps(clients)]
        with patch.object(tile, "hypr", side_effect=results):
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                tile.handle_open("0xaa")

    def test_overflow_always_follows_even_with_legacy_follow_disabled(self):
        self.write_settings({"id": WP_ID, "maxWindows": 2, "follow": False,
                             "workspaceAutoPresets": {"1": "side-by-side"}})
        before = [client(), client("0xbb"), client("0xcc")]
        after = [client(workspace=2), client("0xbb"), client("0xcc")]
        results = [json.dumps(before), json.dumps(WORKSPACES), "ok", json.dumps(after)]
        with patch.object(tile, "hypr", side_effect=results) as hypr:
            self.assertEqual(tile.handle_open("0xaa")["status"], "moved")
            dispatch = next(call.args[1] for call in hypr.call_args_list if call.args[0] == "eval")
            self.assertIn('workspace="2", follow=true', dispatch)

    def test_success_is_verified_and_duplicate_event_does_nothing(self):
        before = [client(), client("0xbb"), client("0xcc")]
        after = [client(workspace=2), client("0xbb"), client("0xcc")]
        results = [json.dumps(before), json.dumps(WORKSPACES), "ok", json.dumps(after),
                   json.dumps(after), json.dumps(WORKSPACES)]
        with patch.object(tile, "hypr", side_effect=results) as hypr:
            self.assertEqual(tile.handle_open("0xaa")["status"], "moved")
            self.assertEqual(tile.handle_open("0xaa")["status"], "unchanged")
            self.assertEqual(sum(call.args[0] == "eval" for call in hypr.call_args_list), 1)


class LockSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime_dir = Path(self.directory.name) / "runtime"
        self.runtime_dir.mkdir(mode=0o700)
        self.config_dir = Path(self.directory.name) / "config"
        self.config_dir.mkdir(mode=0o700)
        self.environment = patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": str(self.runtime_dir),
            "XDG_CONFIG_HOME": str(self.config_dir)
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        shell_path = self.config_dir / "omarchy/shell.json"
        shell_path.parent.mkdir(parents=True, exist_ok=True)
        shell_path.write_text(json.dumps({"bar": {"layout": {"right": [{"id": WP_ID, "active": True, "maxWindows": 2}]}}}))

    def test_lock_does_not_truncate_existing_content(self):
        lock_file = self.runtime_dir / "omarchy-simple-tile.lock"
        lock_file.write_bytes(b"EXISTING_LOCK_CONTENT_PRESERVE")
        clients = [client(), client("0xbb"), client("0xcc")]
        with patch.object(tile, "hypr", side_effect=[json.dumps(clients), json.dumps(WORKSPACES)]):
            res = tile.handle_open("0xaa", dry_run=True)
            self.assertEqual(res["status"], "planned")
        self.assertEqual(lock_file.read_bytes(), b"EXISTING_LOCK_CONTENT_PRESERVE")

    def test_reject_symlink_lock_path(self):
        target_file = Path(self.directory.name) / "secret.txt"
        target_file.write_text("SENSITIVE_USER_DATA")
        lock_file = self.runtime_dir / "omarchy-simple-tile.lock"
        lock_file.symlink_to(target_file)
        with self.assertRaisesRegex(RuntimeError, "unsafe pre-existing"):
            with tile.runtime_lock():
                pass
        self.assertEqual(target_file.read_text(), "SENSITIVE_USER_DATA")

    def test_reject_symlinked_runtime_directory(self):
        real_dir = Path(self.directory.name) / "real_dir"
        real_dir.mkdir(mode=0o700)
        symlink_runtime = Path(self.directory.name) / "symlink_runtime"
        symlink_runtime.symlink_to(real_dir)
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(symlink_runtime)}):
            with self.assertRaisesRegex(RuntimeError, "unsafe symlinked"):
                tile.get_runtime_dir()

    def test_fallback_runtime_directory_symlink_rejection(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            with patch.object(Path, "lstat") as mock_lstat, patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "unsafe pre-existing symlinked"):
                    tile.get_runtime_dir()

    def test_fallback_runtime_directory_insecure_permissions(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            mock_stat = type("Stat", (), {"st_mode": 0o777, "st_uid": os.getuid()})()
            with patch.object(Path, "lstat", return_value=mock_stat), patch.object(Path, "is_symlink", return_value=False), patch("stat.S_ISDIR", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "unsafe runtime directory permissions"):
                    tile.get_runtime_dir()


class ManualModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime_dir = Path(self.directory.name) / "runtime"
        self.runtime_dir.mkdir(mode=0o700)
        self.config_dir = Path(self.directory.name) / "config"
        self.config_dir.mkdir(mode=0o700)
        self.environment = patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": str(self.runtime_dir),
            "XDG_CONFIG_HOME": str(self.config_dir)
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.shell_path = self.config_dir / "omarchy/shell.json"
        self.shell_path.parent.mkdir(parents=True, exist_ok=True)

    def write_settings(self, entry):
        self.shell_path.write_text(json.dumps({"bar": {"layout": {"right": [entry]}}}))

    def test_manual_mode_in_plan_move_blocks_moves(self):
        clients = [client(workspace=2), client("0xbb", workspace=2), client("0xcc", workspace=2)]
        # Workspace 2 has cap 1, but is in manual mode
        plan = tile.plan_move(clients, WORKSPACES, "0xcc", 1, workspace_modes={2: "manual"})
        self.assertIsNone(plan)

    def test_manual_mode_in_handle_open(self):
        self.write_settings({
            "id": WP_ID,
            "active": True,
            "maxWindows": 1,
            "workspaceModes": {"2": "manual"}
        })
        clients = [client(workspace=2), client("0xbb", workspace=2)]
        with patch.object(tile, "hypr", side_effect=[json.dumps(clients), json.dumps(WORKSPACES)]):
            res = tile.handle_open("0xbb", dry_run=True)
            self.assertEqual(res, {"status": "manual", "workspace": 2})

    def test_settings_parses_workspace_modes_and_layouts(self):
        self.write_settings({
            "id": WP_ID,
            "maxWindows": 2,
            "workspaceModes": {"1": "auto", "2": "manual", "bad": "manual"},
            "workspaceLayouts": {
                "2": {
                    "mode": "manual",
                    "saveApps": True,
                    "autostart": True,
                    "apps": [{"name": "TestApp", "class": "test", "icon": "test"}]
                }
            }
        })
        settings = tile.read_settings()
        self.assertEqual(settings["workspace_modes"], {1: "auto", 2: "manual"})
        self.assertIn(2, settings["workspace_layouts"])
        self.assertEqual(settings["workspace_layouts"][2]["apps"][0]["name"], "TestApp")


class DesktopResolutionTests(unittest.TestCase):
    def test_resolve_app_by_startup_class(self):
        desktop_apps = [{
            "name": "Editor",
            "startup_class": "code-oss",
            "desktop_id": "code",
            "icon": "code",
            "desktop_path": "/usr/share/applications/code.desktop"
        }]
        c = {"class": "code-oss", "initialClass": "code-oss", "pid": 0}
        app = tile.resolve_app_for_client(c, desktop_apps)
        self.assertEqual(app["name"], "Editor")
        self.assertEqual(app["icon"], "code")

    def test_resolve_app_by_desktop_id(self):
        desktop_apps = [{
            "name": "Browser",
            "startup_class": "",
            "desktop_id": "firefox",
            "icon": "firefox",
            "desktop_path": "/usr/share/applications/firefox.desktop"
        }]
        c = {"class": "firefox", "initialClass": "firefox", "pid": 0}
        app = tile.resolve_app_for_client(c, desktop_apps)
        self.assertEqual(app["name"], "Browser")

    def test_resolve_app_fallback(self):
        c = {"class": "custom-tool", "initialClass": "custom-tool", "pid": 0}
        app = tile.resolve_app_for_client(c, [])
        self.assertEqual(app["name"], "Custom-tool")
        self.assertEqual(app["class"], "custom-tool")
        self.assertEqual(app["icon"], "custom-tool")

    def test_capture_workspace(self):
        clients = [
            {"address": "0x11", "workspace": {"id": 1}, "mapped": True, "class": "term",
             "title": "Terminal", "at": [0, 0], "size": [800, 600], "floating": False, "pid": 0},
            {"address": "0x22", "workspace": {"id": 2}, "mapped": True, "class": "browser",
             "title": "Web", "at": [0, 0], "size": [800, 600], "floating": False, "pid": 0},
        ]
        with patch.object(tile, "hypr", return_value=json.dumps(clients)), \
                patch.object(tile, "parse_desktop_files", return_value=[]), \
                patch.object(tile, "read_settings", return_value={}), \
                patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": ""}):
            res = tile.capture_workspace(1)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["workspace"], 1)
            self.assertEqual(res["count"], 1)
            self.assertEqual(res["windows"][0]["address"], "0x11")
            self.assertEqual(res["apps"][0]["class"], "term")


class AppLaunchTests(unittest.TestCase):
    def setUp(self):
        lock = patch.object(tile, "runtime_lock")
        lock.start()
        self.addCleanup(lock.stop)

    def test_launch_standalone_argv(self):
        app = {"argv": ["/opt/bin/my-app", "--flag"]}
        with patch.object(tile.subprocess, "Popen") as popen:
            self.assertTrue(tile.launch_app(app))
            popen.assert_called_once()
            self.assertEqual(popen.call_args[0][0], ["/opt/bin/my-app", "--flag"])

    def test_launch_desktop_with_uwsm(self):
        app = {"desktop_path": "/usr/share/applications/test.desktop"}
        installed = [{"desktop_path": "/usr/share/applications/test.desktop", "desktop_id": "test"}]
        with patch.object(tile.shutil, "which", return_value="/usr/bin/uwsm-app"), \
                patch.object(tile, "parse_desktop_files", return_value=installed), \
                patch.object(tile.subprocess, "Popen") as popen:
            self.assertTrue(tile.launch_app(app))
            popen.assert_called_once_with(
                ["uwsm-app", "--", "/usr/share/applications/test.desktop"],
                stdout=tile.subprocess.DEVNULL,
                stderr=tile.subprocess.DEVNULL,
                start_new_session=True,
                env=popen.call_args[1]["env"]
            )

    def test_launch_app_rejects_raw_exec(self):
        # A raw exec command without desktop_path or argv must never execute
        app = {"exec": "rm -rf /"}
        with patch.object(tile.subprocess, "Popen") as popen:
            self.assertFalse(tile.launch_app(app))
            popen.assert_not_called()

    def test_restore_workspace_skips_already_running(self):
        app1 = {"name": "Term", "class": "term", "desktop_path": "/app/term.desktop"}
        app2 = {"name": "Editor", "class": "editor", "desktop_path": "/app/editor.desktop"}
        settings = {
            "workspace_layouts": {
                2: {
                    "mode": "manual",
                    "apps": [app1, app2]
                }
            }
        }
        # Term is already running on workspace 2
        clients = [{"address": "0x1", "workspace": {"id": 2}, "mapped": True, "class": "term"}]
        editor = {"address": "0x2", "workspace": {"id": 2}, "mapped": True, "class": "editor"}
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", side_effect=[json.dumps(clients), json.dumps({"id": 1}), json.dumps(clients), json.dumps(clients), "ok", json.dumps(clients + [editor]), "ok"]), \
                patch.object(tile, "wait_for_window", return_value=editor), \
                patch.object(tile, "is_app_available", return_value=(True, "ok")), \
                patch.object(tile, "launch_app", return_value=True) as launch:
            res = tile.restore_workspace(2, dry_run=False)
            self.assertEqual(res["status"], "ok")
            self.assertIn("Term", res["already_running"])
            self.assertIn("Editor", res["launched"])
            launch.assert_called_once_with(app2)

    def test_autostart_all_triggers_enabled_workspaces(self):
        settings = {
            "workspace_modes": {1: "manual", 2: "manual", 3: "manual"},
            "workspace_layouts": {
                1: {"autostart": False, "apps": []},
                2: {"autostart": True, "apps": [{"name": "App2", "class": "app2"}]},
                3: {"autostart": True, "apps": [{"name": "App3", "class": "app3"}]},
            }
        }
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "restore_workspace", return_value={"status": "ok"}) as restore:
            res = tile.autostart_all(dry_run=True)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(len(res["workspaces"]), 2)
            restore.assert_any_call(2, dry_run=True)
            restore.assert_any_call(3, dry_run=True)


    def test_is_app_available_checks_command_and_desktop(self):
        # Binary in argv exists
        with patch.object(tile.shutil, "which", return_value="/usr/bin/python3"):
            avail, reason = tile.is_app_available({"argv": ["python3"]})
            self.assertTrue(avail)
            self.assertEqual(reason, "ok")

        # Missing binary in argv
        with patch.object(tile.shutil, "which", return_value=None):
            avail, reason = tile.is_app_available({"argv": ["nonexistent_xyz_app"]})
            self.assertFalse(avail)
            self.assertEqual(reason, "command_not_found")

        # Missing desktop file
        avail, reason = tile.is_app_available({"desktop_path": "/nonexistent/path.desktop"})
        self.assertFalse(avail)
        self.assertEqual(reason, "desktop_file_not_found")

    def test_restore_workspace_handles_missing_app_gracefully(self):
        valid_app = {"name": "GoodApp", "class": "good", "desktop_path": "/app/good.desktop"}
        missing_app = {"name": "MissingApp", "class": "missing", "desktop_path": "/nonexistent/bad.desktop"}
        settings = {
            "workspace_layouts": {
                2: {
                    "mode": "manual",
                    "apps": [missing_app, valid_app]
                }
            }
        }
        clients = []
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", side_effect=["[]", json.dumps({"id": 1}), "[]", "ok", json.dumps([{"address": "0x2", "workspace": {"id": 2}, "mapped": True, "class": "good"}]), "ok"]), \
                patch.object(tile, "wait_for_window", return_value={"address": "0x2", "workspace": {"id": 2}, "mapped": True, "class": "good"}), \
                patch.object(tile, "is_app_available", side_effect=[(False, "desktop_file_not_found"), (True, "ok")]), \
                patch.object(tile, "launch_app", return_value=True) as launch:
            res = tile.restore_workspace(2, dry_run=False)
            self.assertEqual(res["status"], "partial")
            self.assertEqual(res["launched"], ["GoodApp"])
            self.assertEqual(len(res["failed"]), 1)
            self.assertEqual(res["failed"][0]["name"], "MissingApp")
            self.assertEqual(res["failed"][0]["reason"], "desktop_file_not_found")
            # launch_app must only be called for GoodApp, not for MissingApp
            launch.assert_called_once_with(valid_app)

    def test_restore_workspace_all_failed_returns_error(self):
        missing_app = {"name": "MissingApp", "class": "missing", "desktop_path": "/nonexistent/bad.desktop"}
        settings = {
            "workspace_layouts": {
                2: {
                    "mode": "manual",
                    "apps": [missing_app]
                }
            }
        }
        clients = []
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", side_effect=[json.dumps(clients), json.dumps({"id": 1})]), \
                patch.object(tile, "is_app_available", return_value=(False, "desktop_file_not_found")):
            res = tile.restore_workspace(2, dry_run=False)
            self.assertEqual(res["status"], "error")
            self.assertEqual(len(res["failed"]), 1)
            self.assertEqual(res["launched"], [])

    def test_apply_layout_preset_side_by_side_and_stacked(self):
        # 2 windows stacked vertically (dy > dx)
        c1 = {"workspace": {"id": 1}, "mapped": True, "at": [0, 0]}
        c2 = {"workspace": {"id": 1}, "mapped": True, "at": [0, 500]}
        clients = [c1, c2]

        with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr:
            res = tile.apply_layout_preset(1, "side-by-side", dry_run=False)
            self.assertEqual(res["status"], "ok")
            # Should have called togglesplit because it was stacked
            called_lua = [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1]
            self.assertTrue(any('togglesplit' in lua for lua in called_lua))

        # 2 windows side-by-side (dx > dy)
        c1_side = {"workspace": {"id": 1}, "mapped": True, "at": [0, 0]}
        c2_side = {"workspace": {"id": 1}, "mapped": True, "at": [960, 0]}
        clients_side = [c1_side, c2_side]

        with patch.object(tile, "hypr", return_value=json.dumps(clients_side)) as mock_hypr:
            res = tile.apply_layout_preset(1, "stacked", dry_run=False)
            self.assertEqual(res["status"], "ok")
            # Should have called togglesplit because it was side-by-side
            called_lua = [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1]
            self.assertTrue(any('togglesplit' in lua for lua in called_lua))

    def test_apply_layout_preset_master_orientations(self):
        c1 = {"workspace": {"id": 1}, "mapped": True, "at": [0, 0]}
        c2 = {"workspace": {"id": 1}, "mapped": True, "at": [960, 0]}
        c3 = {"workspace": {"id": 1}, "mapped": True, "at": [960, 500]}
        clients = [c1, c2, c3]

        with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr:
            res = tile.apply_layout_preset(1, "master-right", dry_run=False)
            self.assertEqual(res["status"], "ok")
            called_lua = [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1]
            self.assertTrue(any('orientationright' in lua for lua in called_lua))

        with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr:
            res = tile.apply_layout_preset(1, "columns", dry_run=False)
            self.assertEqual(res["status"], "ok")
            called_lua = [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1]
            self.assertTrue(any('orientationcenter' in lua for lua in called_lua))

    def test_split_from_the_preview_is_sent_after_the_layout(self):
        c1 = {"workspace": {"id": 1}, "mapped": True, "at": [0, 0]}
        c2 = {"workspace": {"id": 1}, "mapped": True, "at": [960, 0]}
        c3 = {"workspace": {"id": 1}, "mapped": True, "at": [960, 500]}

        def lua(preset, clients, settings):
            with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr, \
                    patch.object(tile, "read_settings", return_value=settings):
                tile.apply_layout_preset(1, preset, dry_run=False)
            return [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1]

        own = {"workspace_splits": {1: 0.6}, "default_split": 0.4}
        self.assertIn('hl.dispatch(hl.dsp.layout("mfact exact 0.60"))', lua("master-right", [c1, c2, c3], own))
        self.assertIn('hl.dispatch(hl.dsp.layout("splitratio 0.80 exact"))',
                      lua("side-by-side", [c1, c2], {"default_split": 0.4}))       # the default, ratio = 2 × share
        self.assertIn('hl.dispatch(hl.dsp.layout("mfact exact 0.50"))', lua("master-left", [c1, c2, c3], {}))   # none stored = even
        self.assertFalse(any("mfact" in l or "splitratio" in l for l in lua("grid", [c1, c2, c3], own)))
        self.assertIn('hl.dispatch(hl.dsp.layout("mfact exact 0.50"))',
                      lua("master-left", [c1, c2, c3], {"workspace_splits": {1: 5}}))     # invalid = even
        # Columns with 3 windows is a centred master: the middle column's share, at most 0.6.
        three = {"cap": 3, "workspace_splits": {1: 0.75}}
        self.assertIn('hl.dispatch(hl.dsp.layout("mfact exact 0.60"))', lua("columns", [c1, c2, c3], three))
        self.assertFalse(any("mfact" in l for l in lua("columns", [c1, c2, c3], dict(three, cap=4))))
        # Saved and cleaned like the other preset keys.
        cleaned = tile.clean_presets({"workspaceSplits": {"1": 0.62, "2": 0.95, "x": 0.5}, "defaultSplit": "0.4"})
        self.assertEqual(cleaned["workspaceSplits"], {"1": 0.62})
        self.assertNotIn("defaultSplit", cleaned)

    def test_apply_layout_preset_columns_4_windows(self):
        c1 = {"workspace": {"id": 1}, "mapped": True, "address": "0x1", "at": [0, 0], "size": [960, 1080]}
        c2 = {"workspace": {"id": 1}, "mapped": True, "address": "0x2", "at": [960, 0], "size": [960, 540]}
        c3 = {"workspace": {"id": 1}, "mapped": True, "address": "0x3", "at": [960, 540], "size": [480, 540]}
        c4 = {"workspace": {"id": 1}, "mapped": True, "address": "0x4", "at": [1440, 540], "size": [480, 540]}
        clients = [c1, c2, c3, c4]

        with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr:
            res = tile.apply_layout_preset(1, "columns", dry_run=False)
            self.assertEqual(res["status"], "ok")
            called_lua = [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1]
            # Must configure dwindle layout rule
            self.assertTrue(any('layout = "dwindle"' in lua for lua in called_lua))
            # Must NOT use orientationcenter (which would stack slaves)
            self.assertFalse(any('orientationcenter' in lua for lua in called_lua))
            # Must invoke togglesplit on stacked windows
            self.assertTrue(any('togglesplit' in lua for lua in called_lua))

    def test_apply_layout_preset_columns_cap4_empty_workspace(self):
        clients = []
        settings = {"cap": 2, "workspace_caps": {1: 4}}
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr:
            res = tile.apply_layout_preset(1, "columns", dry_run=False)
            self.assertEqual(res["status"], "ok")
            called_lua = [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1]
            self.assertTrue(any('layout = "dwindle"' in lua for lua in called_lua))

    def test_apply_preset_cli_flag(self):
        clients = []
        with patch.object(tile, "hypr", return_value=json.dumps(clients)), \
                patch("sys.stdout") as mock_stdout:
            exit_code = tile.main(["--apply-preset", "3", "master-left", "--dry-run"])
            self.assertEqual(exit_code, 0)

    def test_apply_layout_preset_none(self):
        # 3 clients present; preset none should only set dwindle workspace rule and perform no layout adjustments
        c1 = {"workspace": {"id": 2}, "mapped": True, "at": [0, 0]}
        c2 = {"workspace": {"id": 2}, "mapped": True, "at": [960, 0]}
        c3 = {"workspace": {"id": 2}, "mapped": True, "at": [960, 500]}
        clients = [c1, c2, c3]

        with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr:
            res = tile.apply_layout_preset(2, "none", dry_run=False)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["preset"], "none")
            called_lua = [call.args[1] for call in mock_hypr.call_args_list if len(call.args) > 1 and call.args[0] == "eval"]
            self.assertEqual(len(called_lua), 1)
            self.assertIn('layout = "dwindle"', called_lua[0])
            self.assertFalse(any("togglesplit" in lua for lua in called_lua))
            self.assertFalse(any("orientation" in lua for lua in called_lua))

    def test_floating_workspace_has_no_limit(self):
        clients = [client(f"0x{i:x}") for i in range(1, 9)]
        self.assertIsNone(tile.plan_move(clients, WORKSPACES, "0x8", 2,
                                        workspace_auto_presets={1: "floating"}))

    def test_floating_geometry_is_centred_and_cascades(self):
        monitor = {"id": 0, "x": 0, "y": 0, "width": 3840, "height": 2160, "scale": 2, "reserved": [0, 30, 0, 0]}
        w, h, x, y = tile.floating_geometry(monitor, 2)
        self.assertEqual((w, h), (1152, 735))
        self.assertEqual((x, y), (384, 30 + 158))
        first = tile.floating_geometry(monitor, 0)
        self.assertEqual((x - first[2], y - first[3]), (64, 64))

    def test_apply_floating_floats_and_tags_only_tiled_windows(self):
        clients = [client("0x1", 2, monitor=0), client("0x2", 2, monitor=0),
                   dict(client("0x3", 2, monitor=0), floating=True), client("0x4", 1, monitor=0)]
        monitors = [{"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1, "reserved": [0, 0, 0, 0]}]
        calls = []

        def fake(*args):
            calls.append(args)
            return json.dumps(monitors if args[0] == "monitors" else clients)
        with patch.object(tile, "hypr", side_effect=fake):
            res = tile.apply_layout_preset(2, "floating")
        lua = [a[1] for a in calls if a[0] == "eval"]
        self.assertEqual(res["preset"], "floating")
        self.assertEqual(sum('action="on"' in code for code in lua), 2)
        self.assertTrue(all("0x3" not in code and "0x4" not in code for code in lua))
        self.assertEqual(sum('tag="+%s"' % tile.FLOAT_TAG in code for code in lua), 2)
        self.assertEqual(sum("window.resize" in code for code in lua), 2)
        self.assertFalse(any("workspace_rule" in code for code in lua))

    def test_leaving_floating_tiles_only_windows_it_floated(self):
        clients = [dict(client("0x1", 2), floating=True, tags=[tile.FLOAT_TAG]),
                   dict(client("0x2", 2), floating=True, tags=[]),
                   dict(client("0x3", 1), floating=True, tags=[tile.FLOAT_TAG])]
        with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr:
            tile.apply_layout_preset(2, "none")
        lua = [c.args[1] for c in mock_hypr.call_args_list if c.args[0] == "eval"]
        off = [code for code in lua if 'action="off"' in code]
        self.assertEqual(len(off), 1)
        self.assertIn("0x1", off[0])
        self.assertTrue(any('tag="-%s"' % tile.FLOAT_TAG in code for code in lua))

    def test_remove_preset_cli_flag(self):
        clients = []
        with patch.object(tile, "hypr", return_value=json.dumps(clients)) as mock_hypr, \
                patch("sys.stdout"):
            exit_code = tile.main(["--remove-preset", "2", "--dry-run"])
            self.assertEqual(exit_code, 0)

    def test_remove_all_presets(self):
        workspaces = [{"id": 1}, {"id": 2}]
        with patch.object(tile, "hypr", side_effect=[json.dumps(workspaces), "[]", "[]"]):
            res = tile.remove_all_presets(dry_run=True)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["action"], "remove_all_presets")
            self.assertEqual(res["workspaces"], [1, 2])

    def test_reset_all_presets(self):
        workspaces = [{"id": 1}, {"id": 2}]
        settings = {"cap": 2, "default_auto_presets": {"2": "side-by-side"}}
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", side_effect=[json.dumps(workspaces), "[]", "[]"]):
            res = tile.reset_all_presets(dry_run=True)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["action"], "reset_all_presets")
            self.assertEqual(res["default_preset"], "side-by-side")


class OmarchyModeTests(unittest.TestCase):
    """Omarchy 4.5 workspace modes (Super + L) and the Floating/Scrolling presets."""

    def setUp(self):
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.dir = Path(self.state.name) / "omarchy/workspace-layouts"
        for patcher in (patch.object(tile, "omarchy_modes_supported", return_value=True),
                        patch.dict(os.environ, {"XDG_STATE_HOME": self.state.name})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def save(self, name, text):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(text)

    def fake_hypr(self, clients=(), plugins=()):
        calls = []

        def fake(*args):
            calls.append(args)
            if args[0] == "getoption":
                return json.dumps({"str": "dwindle"})
            if args[0] == "plugin":
                return json.dumps(list(plugins))
            if args[0] == "eval":
                return "ok"
            return json.dumps(list(clients))
        return calls, fake

    def test_modes_read_saved_files_and_default(self):
        self.save("3.lua", 'o.workspace_mode({ workspace = "3", mode = "floating" })\n')
        self.save("4.lua", 'o.workspace_mode({ workspace = "4", mode = "scrolling" })\n')
        self.save("5.lua", 'o.workspace_mode({ workspace = "5", mode = "bogus" })\n')
        self.save("default.lua", 'o.workspace_mode({ default = "scrolling" })\n')
        modes = tile.omarchy_workspace_modes([1, 3, 4, 5, 0, -2])
        self.assertEqual(modes, {1: "scrolling", 3: "floating", 4: "scrolling", 5: "scrolling"})

    def test_modes_ignore_symlinks_and_huge_files(self):
        target = Path(self.state.name) / "elsewhere.lua"
        target.write_text('mode = "floating"')
        self.dir.mkdir(parents=True)
        (self.dir / "2.lua").symlink_to(target)
        self.save("3.lua", " " * tile.MODE_FILE_MAX + 'mode = "floating"')
        with patch.object(tile, "hypr", return_value=json.dumps({"str": "dwindle"})):
            self.assertEqual(tile.omarchy_workspace_modes([2, 3]), {2: "dwindle", 3: "dwindle"})

    def test_no_modes_without_omarchy_4_5(self):
        with patch.object(tile, "omarchy_modes_supported", return_value=False):
            self.assertEqual(tile.omarchy_workspace_modes([1, 2]), {})
            self.assertFalse(tile.set_omarchy_mode(2, "floating"))

    def test_set_mode_applies_saves_and_reloads_for_titlebars(self):
        calls, fake = self.fake_hypr()
        with patch.object(tile, "hypr", side_effect=fake), patch.object(tile.subprocess, "run") as run:
            self.assertTrue(tile.set_omarchy_mode(7, "floating"))
        self.assertIn(("eval", 'o.workspace_mode({ workspace = "7", mode = "floating" })'), calls)
        self.assertEqual((self.dir / "7.lua").read_text(),
                         'o.workspace_mode({ workspace = "7", mode = "floating" })\n')
        self.assertIn(("reload",), calls)
        refreshed = [c.args[0] for c in run.call_args_list]
        self.assertIn(["omarchy-shell", "-q", "omarchy.workspaces", "refresh"], refreshed)

    def test_set_mode_to_default_removes_the_saved_file(self):
        self.save("7.lua", 'o.workspace_mode({ workspace = "7", mode = "scrolling" })\n')
        calls, fake = self.fake_hypr(plugins=[{"name": "hyprbars"}])
        with patch.object(tile, "hypr", side_effect=fake), patch.object(tile.subprocess, "run"):
            tile.set_omarchy_mode(7, "dwindle")
        self.assertFalse((self.dir / "7.lua").exists())
        self.assertNotIn(("reload",), calls)

    def test_set_mode_rejects_bad_input_before_any_command(self):
        with patch.object(tile, "hypr") as hypr:
            for ws, mode in ((0, "floating"), (-1, "dwindle"), ("2", "floating"), (2, "master"),
                             (2, 'floating" }) os.execute("x') ):
                with self.assertRaises(ValueError):
                    tile.set_omarchy_mode(ws, mode)
        hypr.assert_not_called()

    def test_scrolling_preset_is_the_omarchy_mode(self):
        clients = [dict(client("0x1", 2), floating=True, tags=[tile.FLOAT_TAG])]
        calls, fake = self.fake_hypr(clients)
        with patch.object(tile, "hypr", side_effect=fake), patch.object(tile.subprocess, "run"):
            res = tile.apply_layout_preset(2, "scrolling")
        self.assertEqual(res["preset"], "scrolling")
        lua = [a[1] for a in calls if a[0] == "eval"]
        self.assertIn('o.workspace_mode({ workspace = "2", mode = "scrolling" })', lua)
        self.assertTrue(any('action="off"' in code and "0x1" in code for code in lua))
        self.assertFalse(any("workspace_rule" in code for code in lua))

    def test_floating_preset_is_the_omarchy_mode_not_plugin_floating(self):
        calls, fake = self.fake_hypr([client("0x1", 2, monitor=0)], plugins=[{"name": "hyprbars"}])
        with patch.object(tile, "hypr", side_effect=fake), patch.object(tile.subprocess, "run"):
            tile.apply_layout_preset(2, "floating")
        lua = [a[1] for a in calls if a[0] == "eval"]
        self.assertEqual(lua, ['o.workspace_mode({ workspace = "2", mode = "floating" })'])

    def test_scrolling_without_omarchy_modes_uses_a_layout_rule(self):
        calls, fake = self.fake_hypr()
        with patch.object(tile, "omarchy_modes_supported", return_value=False), \
                patch.object(tile, "hypr", side_effect=fake):
            tile.apply_layout_preset(2, "scrolling")
        self.assertIn(("eval", 'hl.workspace_rule({ workspace = "2", layout = "scrolling" })'), calls)

    def test_tiling_preset_tiles_an_omarchy_floating_workspace(self):
        self.save("2.lua", 'o.workspace_mode({ workspace = "2", mode = "floating" })\n')
        calls, fake = self.fake_hypr(plugins=[{"name": "hyprbars"}])
        with patch.object(tile, "hypr", side_effect=fake), patch.object(tile.subprocess, "run"):
            tile.apply_layout_preset(2, "master-left")
        lua = [a[1] for a in calls if a[0] == "eval"]
        self.assertEqual(lua[0], 'o.workspace_mode({ workspace = "2", mode = "dwindle" })')
        self.assertTrue(any('orientation = "left"' in code for code in lua))
        self.assertFalse((self.dir / "2.lua").exists())

    def test_window_open_never_changes_the_omarchy_mode(self):
        self.save("2.lua", 'o.workspace_mode({ workspace = "2", mode = "floating" })\n')
        calls, fake = self.fake_hypr()
        with patch.object(tile, "hypr", side_effect=fake):
            tile.apply_layout_preset(2, "master-left", sync_mode=False)
        self.assertFalse(any("workspace_mode" in str(a) for a in calls))
        self.assertTrue((self.dir / "2.lua").exists())

    def test_open_on_omarchy_floating_workspace_neither_moves_nor_tiles(self):
        self.save("1.lua", 'o.workspace_mode({ workspace = "1", mode = "floating" })\n')
        clients = [client(f"0x{i:x}") for i in range(1, 6)]
        settings = {"enabled": True, "cap": 2, "workspace_caps": {}, "workspace_modes": {},
                    "workspace_layouts": {}, "workspace_auto_presets": {1: "side-by-side"},
                    "default_auto_presets": {}}
        calls, fake = self.fake_hypr(clients)

        def hypr(*args):
            return json.dumps(WORKSPACES) if args[0] == "workspaces" else fake(*args)
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", side_effect=hypr), \
                patch.object(tile, "runtime_lock"):
            self.assertEqual(tile.handle_open("0x5")["status"], "unchanged")
        self.assertFalse(any(a[0] == "eval" for a in calls))

    def test_scrolling_workspace_has_no_limit(self):
        clients = [client(f"0x{i:x}") for i in range(1, 9)]
        self.assertIsNone(tile.plan_move(clients, WORKSPACES, "0x8", 2,
                                        workspace_auto_presets={1: "scrolling"}))

    def test_start_plan_presets_here_win_others_keep_omarchy(self):
        settings = {"workspace_auto_presets": {1: "floating", 2: "master-left", 3: "none",
                                               5: "scrolling", 6: "scrolling"},
                    "workspace_modes": {7: "manual"}, "workspace_layouts": {8: {"mode": "manual"}}}
        omarchy = {1: "dwindle", 2: "floating", 3: "scrolling", 4: "floating",
                   5: "floating", 6: "scrolling", 7: "scrolling", 8: "dwindle", 9: "dwindle"}
        self.assertEqual(tile.mode_sync_plan(settings, omarchy),
                         [(1, "floating"), (2, "master-left"), (5, "scrolling"), (7, "dwindle")])

    def test_sync_read_reports_modes_and_changes_nothing(self):
        self.save("12.lua", 'o.workspace_mode({ workspace = "12", mode = "scrolling" })\n')
        settings = {"enabled": True, "workspace_auto_presets": {2: "master-left"}}
        self.save("2.lua", 'o.workspace_mode({ workspace = "2", mode = "floating" })\n')
        calls, fake = self.fake_hypr()
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", side_effect=fake):
            res = tile.sync_modes(start=False)
        self.assertEqual(res["modes"]["12"], "scrolling")
        self.assertEqual(res["modes"]["2"], "floating")
        self.assertEqual(res["applied"], [])
        self.assertFalse(any(a[0] == "eval" for a in calls))

    def test_sync_start_lines_omarchy_up_with_the_presets(self):
        settings = {"enabled": True, "workspace_auto_presets": {3: "scrolling"},
                    "workspace_modes": {}, "workspace_layouts": {}}
        calls, fake = self.fake_hypr()
        with patch.object(tile, "read_settings", return_value=settings), \
                patch.object(tile, "hypr", side_effect=fake), \
                patch.object(tile.subprocess, "run"), patch.object(tile, "runtime_lock"):
            res = tile.sync_modes(start=True)
        self.assertEqual(res["applied"], [{"workspace": 3, "preset": "scrolling"}])
        self.assertEqual(res["modes"]["3"], "scrolling")
        self.assertTrue((self.dir / "3.lua").exists())

    def test_sync_without_omarchy_modes(self):
        with patch.object(tile, "omarchy_modes_supported", return_value=False):
            self.assertEqual(tile.sync_modes(start=True),
                             {"status": "ok", "supported": False, "modes": {}, "applied": []})


class StoreTests(unittest.TestCase):
    """Theme Manager's own workspaces.json, the one-time import and the cleanup commands."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "run").mkdir(mode=0o700)
        for patcher in (patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(root / "run"), "XDG_CONFIG_HOME": str(root)}),
                        patch.object(tile, "load_store", side_effect=_REAL_LOAD),
                        patch.object(tile, "wp_on_bar", side_effect=_REAL_WP_ON_BAR)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.shell = root / "omarchy/shell.json"
        self.shell.parent.mkdir(parents=True)

    def wp(self, **entry):
        self.shell.write_text(json.dumps({"bar": {"layout": {"right": [dict(entry, id=WP_ID)]}}}))

    def test_plugin_entry_rules_while_on_the_bar_and_is_mirrored_under_the_lock(self):
        self.wp(active=True, maxWindows=3, language="de", workspaceModes={"2": "manual"},
                workspaceLayouts={"2": {"apps": [{"name": "Foot"}]}})
        store = tile.load_store()
        self.assertEqual(store["maxWindows"], 3)
        self.assertTrue(store["active"])
        self.assertNotIn("language", store)
        self.assertFalse(tile.store_path().exists())          # loading never writes
        with tile.runtime_lock():
            tile.mirror_wp_entry()                            # at shell start, under the lock
        self.assertEqual(oct(tile.store_path().stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(json.loads(tile.store_path().read_text())["maxWindows"], 3)
        self.wp(active=True, maxWindows=1)                    # changed in the plugin
        self.assertEqual(tile.load_store()["maxWindows"], 1)
        with tile.runtime_lock():
            tile.mirror_wp_entry()
        self.assertEqual(json.loads(tile.store_path().read_text())["maxWindows"], 1)

    def test_save_while_the_plugin_is_on_the_bar_edits_its_entry(self):
        config = {"theme": "x", "bar": {"layout": {"left": [{"id": "other", "k": 1}],
                  "right": [{"id": WP_ID, "language": "de", "maxWindows": 3,
                             "workspaceCaps": {"1": 3}, "workspaceAutoPresets": {"1": "master-right"}}]}}}
        self.shell.write_text(json.dumps(config))
        self.shell.chmod(0o600)
        with tile.runtime_lock():
            store = tile.load_store()
            store["workspaceCaps"] = {"1": 2}
            store["workspaceAutoPresets"] = {"1": "side-by-side"}
            store["workspaceSplits"] = {"1": 0.6}
            tile.save_store(store)
        saved = json.loads(self.shell.read_text())
        entry = saved["bar"]["layout"]["right"][0]
        self.assertEqual(entry["workspaceCaps"], {"1": 2})
        self.assertEqual(entry["workspaceAutoPresets"], {"1": "side-by-side"})
        self.assertEqual(entry["workspaceSplits"], {"1": 0.6})
        self.assertEqual(entry["language"], "de")                      # not ours: kept
        self.assertEqual(saved["theme"], "x")
        self.assertEqual(saved["bar"]["layout"]["left"], [{"id": "other", "k": 1}])
        self.assertEqual(oct(self.shell.stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(json.loads(tile.store_path().read_text())["workspaceCaps"], {"1": 2})
        with tile.runtime_lock():
            store = tile.load_store()
            store.pop("workspaceSplits")
            tile.save_store(store)
        self.assertNotIn("workspaceSplits", json.loads(self.shell.read_text())["bar"]["layout"]["right"][0])

    def test_wp_entry_write_keeps_a_symlinked_shell_json(self):
        real = self.shell.parent / "dotfiles-shell.json"
        real.write_text(json.dumps({"bar": {"layout": {"right": [{"id": WP_ID, "maxWindows": 2}]}}}))
        real.chmod(0o600)
        self.shell.unlink(missing_ok=True)
        self.shell.symlink_to(real)
        with tile.runtime_lock():
            store = tile.load_store()
            store["maxWindows"] = 3
            tile.save_store(store)
        self.assertTrue(self.shell.is_symlink())                       # still the user's link
        self.assertEqual(json.loads(real.read_text())["bar"]["layout"]["right"][0]["maxWindows"], 3)
        self.assertEqual(oct(real.stat().st_mode & 0o777), oct(0o600))

    def test_init_store_takes_the_plugin_entry_at_shell_start(self):
        self.wp(active=False, maxWindows=4, workspaceModes={"2": "manual"}, workspaceLayouts={"2": {"apps": []}})
        with patch("builtins.print"):
            self.assertEqual(tile.main(["--init-store"]), 0)
        self.shell.write_text(json.dumps({"bar": {"layout": {"right": []}}}))   # plugin removed later
        store = tile.load_store()
        self.assertEqual((store["maxWindows"], store["active"]), (4, False))
        self.assertIn("2", store["workspaceLayouts"])

    def test_plugin_on_the_bar_keeps_moving_windows_not_this(self):
        tile.save_store({"active": True})
        self.assertTrue(tile.read_settings()["enabled"])
        self.wp(active=True)
        self.assertFalse(tile.read_settings()["enabled"])

    def test_off_without_settings(self):
        self.assertFalse(tile.read_settings()["enabled"])

    def test_unsafe_store_reads_as_empty(self):
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text(json.dumps({"active": True}))
        tile.store_path().parent.mkdir(parents=True, exist_ok=True)
        tile.store_path().symlink_to(target)
        self.assertEqual(tile.load_store(), {})

    def test_clear_app_presets(self):
        tile.save_store({"workspaceLayouts": {"2": {"apps": []}, "3": {"apps": []}},
                         "workspaceModes": {"2": "manual", "3": "manual", "1": "auto"}})
        self.assertTrue(tile.clear_app_preset(2)["cleared"])
        self.assertEqual(sorted(tile.load_store()["workspaceLayouts"]), ["3"])
        tile.clear_all_app_presets()
        store = tile.load_store()
        self.assertEqual((store["workspaceLayouts"], store["workspaceModes"]), ({}, {"1": "auto"}))
        with patch("builtins.print") as out:
            self.assertEqual(tile.main(["--clear-app-preset", "3"]), 0)
        self.assertEqual(json.loads(out.call_args_list[0].args[0])["status"], "ok")

    def test_clean_presets_drops_foreign_keys_and_bad_values(self):
        cleaned = tile.clean_presets({
            "maxWindows": 9, "language": "de",
            "workspaceCaps": {"1": 3, "2": 7, "x": 2, "0": 2},
            "workspaceAutoPresets": {"1": "grid", "2": "rm -rf"},
            "workspaceModes": {"3": "manual", "4": "weird"},
            "defaultAutoPresets": {"2": "stacked", "9": "grid"},
            "workspaceLayouts": {"3": {"mode": "x", "autostart": True, "evil": 1,
                                       "apps": [{"name": "Foot", "argv": ["foot", 3]}, "junk"],
                                       "windows": [{"class": "foot", "at": [0, 0], "size": [1, "a"]}]}},
        })
        self.assertEqual(cleaned, {
            "workspaceCaps": {"1": 3}, "workspaceAutoPresets": {"1": "grid"},
            "workspaceModes": {"3": "manual"}, "defaultAutoPresets": {"2": "stacked"},
            "workspaceLayouts": {"3": {"autostart": True, "apps": [{"name": "Foot"}],
                                       "windows": []}},          # no valid size: not kept
        })



class HostileOutputTests(unittest.TestCase):
    """Marketplace review 2.0.0: hyprctl output and notification text."""

    def test_bounded_run_caps_bytes_and_time(self):
        import subprocess
        import workspace_tiling as wt
        self.assertEqual(wt.bounded_run([sys.executable, "-c", "print('ok')"]), "ok\n")
        with self.assertRaises(subprocess.CalledProcessError):            # an endless stream
            wt.bounded_run([sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x'*65536)"],
                           timeout=10, max_bytes=1024 * 1024)
        with self.assertRaises(subprocess.TimeoutExpired):                 # a silent hang
            wt.bounded_run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        with self.assertRaises(subprocess.CalledProcessError):
            wt.bounded_run([sys.executable, "-c", "raise SystemExit(3)"])

    def test_notifications_carry_no_markup(self):
        import workspace_tiling as wt
        sent = []
        with patch.object(wt.shutil, "which", return_value="/usr/bin/notify-send"), \
                patch.object(wt.subprocess, "Popen", side_effect=lambda argv, **k: sent.append(argv)):
            wt.send_notification("T", "Moved (<img src='http://x/y.png'>\nA&B)")
        body = sent[0][-1]
        self.assertNotIn("<", body)
        self.assertNotIn("&", body)
        self.assertNotIn("\n", body)

if __name__ == "__main__":
    unittest.main()
