"""The Workspaces and Apps tabs' rules (ported from Workspace Presets' BarWidget.qml)."""
import unittest

import workspace_state as ws


class WorkspaceStateTests(unittest.TestCase):
    def test_preview_drag_flips_master_and_window_beats_limit_strip(self):
        import gi
        gi.require_version("Gtk", "4.0")
        import workspaces_page as page

        class Gesture:
            def set_state(self, *_a):
                pass

        class Host(page.WorkspacesPageMixin):
            def __init__(self, preset):
                self.ws_store = {"maxWindows": 2, "workspaceAutoPresets": {"1": preset}, "workspaceCaps": {"1": 4}}
                self.ws_modes, self.log = {}, []

            def ws_current(self, _kind):
                return 1

            def ws_commit(self, _label, _ws, rule, *args):
                self.log.append((rule.__name__, args))

            def ws_swap(self, _ws, _rects, a, b):
                self.log.append(("swap", (a, b)))

        W, H = 800, 450

        def drag(preset, index, to_x, start_x=None, to_y=None, tray=None):
            host, panel = Host(preset), page.LookAlike(W, H)
            panel.rects = page.preset_rects(preset, 4)
            top, area_h = panel.area()
            x, y, w, h = panel.rects[index]
            x0, y0 = (start_x if start_x is not None else (x + w / 2) * W), top + (y + h / 2) * area_h
            host.ws_panel_begin(panel, "ws", x0, y0)
            if tray is not None:                     # onto the tray target with this label
                host.ws_panel_update(Gesture(), panel, "ws", 1 - x0, H - y0)   # past the threshold
                labels = [t[2] for t in panel.tray]
                bx, by, bw, bh = panel.tray_boxes()[labels.index(tray)]
                to_x, to_y = bx + bw / 2, by + bh / 2
            host.ws_panel_update(Gesture(), panel, "ws", to_x - x0, (to_y if to_y is not None else y0) - y0)
            shown = list(panel.tray)
            host.ws_panel_end(panel, "ws", 0, 0)
            self.assertEqual(panel.tray, ())         # gone after the drop
            host.shown = shown
            return host.log

        # The master over the stack moves it to that side.
        self.assertEqual(drag("master-right", 3, 0.25 * W), [("set_preset", (1, "master-left"))])
        self.assertEqual(drag("master-left", 0, 0.75 * W), [("set_preset", (1, "master-right"))])
        # A stack window onto the master still swaps the two.
        self.assertEqual(drag("master-right", 0, 0.75 * W), [("swap", (0, 3))])
        # Grabbed at its far right (the limit strip), the window still moves:
        # dragged out of the preview it is one window fewer.
        self.assertEqual(drag("master-right", 3, W + 40, start_x=0.94 * W), [("set_cap", (1, 3, {}))])
        # The tray: every layout for the limit and one window fewer, visible while dragging.
        self.assertEqual(drag("master-right", 0, 0, tray="Columns"), [("set_preset", (1, "columns"))])
        self.assertEqual(drag("master-right", 0, 0, tray="2×2 Grid"), [("set_preset", (1, "grid"))])
        self.assertEqual(drag("master-right", 0, 0, tray="− 1 window"), [("set_cap", (1, 3, {}))])
        self.assertEqual(drag("master-right", 0, 0, tray="Master Right"), [])   # already that
        # No invisible edge bands any more: the left edge over a window is a swap.
        self.assertEqual(drag("master-right", 3, 0.02 * W), [("set_preset", (1, "master-left"))])  # the flip
        self.assertEqual(drag("grid", 1, 0.05 * W), [("swap", (1, 0))])
        # The tray is a pill: beside it, the bottom windows still take a swap.
        host, panel = Host("grid"), page.LookAlike(W, H)
        panel.rects = page.preset_rects("grid", 4)
        panel.tray = host.ws_tray_targets(1, 4)
        pill = panel.tray_pill()
        self.assertGreater(pill[0], 0)
        self.assertIsNone(panel.tray_hit(pill[0] - 5, pill[1] + pill[3] / 2))
        # Keyboard: ↑/↓ go through the layouts of the limit.
        from gi.repository import Gdk
        host.ws_panel_key(panel, "ws", Gdk.KEY_Down, 0)
        self.assertEqual(host.log, [("set_preset", (1, "master-left"))])      # after grid: back to the first
        host.log.clear()
        host.ws_panel_key(panel, "ws", Gdk.KEY_Up, 0)
        self.assertEqual(host.log, [("set_preset", (1, "columns"))])

    def test_preview_divider_sets_the_split(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk
        import workspaces_page as page

        class Gesture:
            def set_state(self, *_a):
                pass

        class Host(page.WorkspacesPageMixin):
            def __init__(self, preset, split=None):
                self.ws_store = {"maxWindows": 2, "workspaceAutoPresets": {"1": preset}, "workspaceCaps": {"1": 3}}
                if split is not None:
                    self.ws_store["workspaceSplits"] = {"1": split}
                self.ws_modes, self.log = {}, []

            def ws_current(self, _kind):
                return 1

            def ws_commit(self, label, _ws, rule, *args):
                self.log.append((label, rule.__name__, args))

        W, H = 800, 450
        for preset, split, to, expect in (("master-left", None, 0.6, 0.6),
                                          ("master-right", None, 0.6, 0.4),   # the master is on the right
                                          ("stacked", 0.3, 0.7, 0.7)):
            host, panel = Host(preset, split), page.LookAlike(W, H)
            panel.rects = page.preset_rects(preset, 3, split)
            axis, pos, _p, _s = host.ws_divider(panel, 1)
            top, area_h = panel.area()
            x0, y0 = (pos, top + area_h / 2) if axis == "x" else (W / 2, pos)
            self.assertIsNotNone(host.ws_on_divider(panel, 1, x0, y0))
            host.ws_panel_begin(panel, "ws", x0, y0)
            self.assertEqual(host.ws_drag["mode"], "split")
            # Pointer to the place where the left/top part has the share `to`.
            target = (1.5 * page.GAP + to * (1 - 3 * page.GAP))
            x1, y1 = (target * W, y0) if axis == "x" else (x0, top + target * area_h)
            host.ws_panel_update(Gesture(), panel, "ws", x1 - x0, y1 - y0)
            host.ws_panel_end(panel, "ws", 0, 0)
            self.assertEqual(host.log[-1][1:], ("set_split", (1, expect)), preset)
        # Grid has no single divider; double-click and [ ] on Master Left.
        self.assertIsNone(Host("grid").ws_divider(page.LookAlike(W, H), 1))
        host, panel = Host("master-left", 0.5), page.LookAlike(W, H)
        host.ws_panel_key(panel, "ws", Gdk.KEY_bracketright, 0)
        self.assertEqual(host.log[-1][1:], ("set_split", (1, 0.55)))
        host.ws_set_split(1, None)
        self.assertEqual(host.log[-1][:2], ("Even split", "set_split"))
        # Even within a small distance of the middle.
        axis, pos, preset, _s = host.ws_divider(panel, 1)
        self.assertEqual(host.ws_split_at(panel, axis, preset, pos + 3, 100), 0.5)

    def test_split_per_workspace_and_default(self):
        store = {"maxWindows": 3, "workspaceAutoPresets": {"1": "master-right", "2": "grid"}}
        self.assertIsNone(ws.split_for(store, 1))
        self.assertEqual(ws.set_split(store, 1, 0.637), ("apply", 1, "master-right"))
        self.assertEqual(ws.split_for(store, 1), 0.64)
        self.assertEqual(ws.set_split(store, 1, 0.95), ("apply", 1, "master-right"))
        self.assertEqual(ws.split_for(store, 1), 0.8)                    # kept in 0.2…0.8
        self.assertIsNone(ws.set_split(store, 2, 0.6))                   # grid has no single divider
        self.assertIsNone(ws.set_split(store, 0, 0.3))                   # the default: applied as windows open
        self.assertEqual(ws.split_for(store, 3), 0.3)
        ws.set_split(store, 1, None)                                     # even again: the default shows
        self.assertEqual(ws.split_for(store, 1), 0.3)

    def test_preset_shown_own_omarchy_default(self):
        store = {"maxWindows": 3, "workspaceAutoPresets": {"2": "grid", "3": "none"}}
        self.assertEqual(ws.preset_for(store, 1), "master-left")           # built-in default for 3
        self.assertEqual(ws.preset_for(store, 2), "grid")
        self.assertEqual(ws.preset_for(store, 3, {"3": "floating"}), "floating")  # Super + L wins over Native
        self.assertTrue(ws.mode_from_omarchy(store, 3, {"3": "floating"}))
        self.assertEqual(ws.preset_for(store, 3), "none")
        store["defaultAutoPresets"] = {"3": "columns"}
        self.assertEqual(ws.preset_for(store, 1), "columns")

    def test_toggle_twice_returns_to_default(self):
        store = {"maxWindows": 2}
        self.assertEqual(ws.toggle_preset(store, 4, "stacked"), ("apply", 4, "stacked"))
        self.assertEqual(ws.toggle_preset(store, 4, "stacked"), ("apply", 4, "side-by-side"))
        self.assertNotIn("4", store["workspaceAutoPresets"])
        self.assertEqual(ws.toggle_preset(store, 4, "none"), ("remove", 4))

    def test_cap_on_native_workspace_picks_default_layout(self):
        store = {"maxWindows": 2, "workspaceAutoPresets": {"1": "none"}}
        self.assertEqual(ws.set_cap(store, 1, 3), ("apply", 1, "master-left"))
        self.assertEqual(store["workspaceCaps"], {"1": 3})
        self.assertIsNone(ws.reset_cap(store, 1))
        self.assertEqual(store["workspaceCaps"], {})

    def test_app_preset_save_remove_and_leaving_it(self):
        store = {"maxWindows": 2}
        capture = {"apps": [{"name": "Foot", "class": "foot"}, {"name": "Web", "class": "chromium"}],
                   "windows": [{"class": "foot"}, {"class": "chromium"}, {"class": "foot"}], "count": 3}
        ws.save_capture(store, 5, capture)
        self.assertTrue(ws.is_manual(store, 5))
        self.assertEqual(ws.effective_cap(store, 5), 3)        # as many as were saved
        ws.remove_app(store, 5, 0)
        self.assertEqual(store["workspaceLayouts"]["5"]["count"], 1)
        ws.remove_app(store, 5, 0)
        self.assertFalse(store["workspaceLayouts"]["5"]["autostart"])   # nothing left to start
        self.assertEqual(ws.set_preset(store, 5, "stacked"), ("apply", 5, "stacked"))
        self.assertFalse(ws.is_manual(store, 5))
        self.assertEqual(store["workspaceCaps"]["5"], 2)   # empty preset: the global limit

    def test_remove_app_preset_reapplies_layout(self):
        store = {"maxWindows": 4}
        ws.save_capture(store, 2, {"apps": [], "windows": [], "count": 0})
        self.assertEqual(ws.remove_app_preset(store, 2), ("apply", 2, "grid"))
        self.assertFalse(ws.is_manual(store, 2))

    def test_reset_workspace_leaves_app_preset_unused(self):
        store = {"workspaceModes": {"4": "manual"}, "workspaceLayouts": {"4": {"mode": "manual", "apps": []}},
                 "workspaceCaps": {"4": 3}}
        ws.reset_workspace(store, 4)
        self.assertFalse(ws.is_manual(store, 4))
        self.assertIn("4", store["workspaceLayouts"])
        self.assertEqual(ws.use_app_preset(store, 4), ("tile", 4))
        self.assertTrue(ws.is_manual(store, 4))

    def test_remove_all_app_presets_lays_them_out_again(self):
        store = {"maxWindows": 2, "workspaceModes": {"2": "manual", "3": "auto"},
                 "workspaceLayouts": {"2": {"apps": []}}, "workspaceAutoPresets": {"2": "stacked"}}
        store["workspaceLayouts"]["4"] = {"mode": "manual", "apps": []}     # manual by its own mode only
        store["workspaceAutoPresets"]["4"] = "none"
        self.assertEqual(ws.remove_all_app_presets(store), ("apply_many", [(2, "stacked"), (4, "none")]))
        self.assertEqual((store["workspaceLayouts"], store["workspaceModes"]), ({}, {"3": "auto"}))

    def test_swap_saved_windows_trade_places(self):
        store = {"workspaceModes": {"3": "manual"}, "workspaceLayouts": {"3": {"windows": [
            {"class": "a", "at": [0, 0], "size": [100, 50]}, {"class": "b", "at": [100, 0], "size": [60, 50]}]}}}
        ws.swap_saved(store, 3, 0, 1)
        a, b = store["workspaceLayouts"]["3"]["windows"]
        self.assertEqual((a["class"], a["at"], a["size"]), ("a", [100, 0], [60, 50]))
        self.assertEqual((b["class"], b["at"]), ("b", [0, 0]))
        self.assertIsNone(ws.swap_saved(store, 3, 0, 5))

    def test_bulk_actions(self):
        store = {"workspaceCaps": {"1": 3}, "workspaceModes": {"2": "manual", "3": "auto"},
                 "workspaceLayouts": {"2": {"apps": []}}}
        self.assertEqual(ws.reset_all(store), ("reset_all",))
        # the App Preset of 2 is kept but no longer in use (no autostart)
        self.assertEqual((store["workspaceCaps"], store["workspaceModes"]), ({}, {"3": "auto", "2": "auto"}))
        self.assertFalse(ws.is_manual(store, 2))
        self.assertEqual(ws.remove_all_presets(store), ("remove_all",))
        self.assertEqual(store["workspaceAutoPresets"]["10"], "none")
        ws.clear_everything(store)
        self.assertEqual(store["defaultAutoPresets"]["2"], "none")
        self.assertEqual(ws.workspace_list(store, [12]), list(range(1, 11)) + [12])


class AddAndResizeTests(unittest.TestCase):
    """The + handle in the preview and the Columns divider (2026-11-07)."""

    def test_drop_zone_picks_the_layout_for_the_new_limit(self):
        self.assertEqual(ws.add_zone_preset(2, 0.5, 0.05), "stacked")
        self.assertEqual(ws.add_zone_preset(2, 0.05, 0.5), "side-by-side")
        self.assertEqual(ws.add_zone_preset(3, 0.1, 0.5), "master-left")
        self.assertEqual(ws.add_zone_preset(3, 0.9, 0.5), "master-right")
        self.assertEqual(ws.add_zone_preset(3, 0.5, 0.1), "columns")
        self.assertEqual(ws.add_zone_preset(3, 0.1, 0.1), "master-left")    # no grid with 3
        self.assertEqual(ws.add_zone_preset(4, 0.1, 0.1), "grid")
        self.assertEqual(ws.add_zone_preset(4, 0.9, 0.5), "master-right")
        self.assertEqual(ws.add_zone_preset(4, -3, 9), "grid")              # clamped into the area
        self.assertIsNone(ws.add_zone_preset(1, 0.5, 0.5))
        for cap in (2, 3, 4):
            for u in (0, 0.2, 0.5, 0.8, 1):
                for v in (0, 0.2, 0.5, 0.8, 1):
                    self.assertIn(ws.add_zone_preset(cap, u, v), ws.TILING_BY_CAP[cap])

    def test_add_window_raises_the_limit_with_the_dropped_layout(self):
        store = {"maxWindows": 2, "workspaceCaps": {"3": 2}}
        self.assertEqual(ws.add_window(store, 3, "columns"), ("apply", 3, "columns"))
        self.assertEqual(store["workspaceCaps"]["3"], 3)
        self.assertEqual(store["workspaceAutoPresets"]["3"], "columns")
        # A layout that does not exist for the new limit is not taken.
        store = {"maxWindows": 2, "workspaceCaps": {"3": 1}}
        ws.add_window(store, 3, "grid")
        self.assertEqual(store["workspaceCaps"]["3"], 2)
        self.assertNotEqual(store.get("workspaceAutoPresets", {}).get("3"), "grid")

    def test_click_keeps_following_the_default_but_replaces_an_own_layout_without_that_limit(self):
        store = {"maxWindows": 2, "workspaceCaps": {"3": 2}}
        self.assertIsNone(ws.add_window(store, 3))                      # click: no layout pinned
        self.assertNotIn("3", store.get("workspaceAutoPresets", {}))
        store = {"maxWindows": 2, "workspaceCaps": {"3": 2}, "workspaceAutoPresets": {"3": "stacked"}}
        self.assertEqual(ws.add_window(store, 3), ("apply", 3, "master-left"))   # no Stacked with 3
        self.assertEqual(store["workspaceAutoPresets"]["3"], "master-left")
        store = {"maxWindows": 2, "workspaceCaps": {"3": 3}, "workspaceAutoPresets": {"3": "columns"}}
        ws.add_window(store, 3)
        self.assertEqual(store["workspaceAutoPresets"]["3"], "columns")          # exists with 4: kept

    def test_add_window_stops_at_four_and_on_app_presets(self):
        store = {"maxWindows": 2, "workspaceCaps": {"1": 4}}
        self.assertFalse(ws.can_add_window(store, 1))
        self.assertIsNone(ws.add_window(store, 1, "grid"))
        self.assertEqual(store["workspaceCaps"]["1"], 4)
        manual = {"maxWindows": 2, "workspaceModes": {"2": "manual"}, "workspaceLayouts": {"2": {"mode": "manual"}}}
        self.assertFalse(ws.can_add_window(manual, 2))
        self.assertIsNone(ws.add_window(manual, 2))
        self.assertNotIn("workspaceCaps", manual)

    def test_add_window_on_all_workspaces_sets_the_default(self):
        store = {"maxWindows": 2}
        self.assertTrue(ws.can_add_window(store, 0))
        self.assertIsNone(ws.add_window(store, 0, "master-right"))
        self.assertEqual(store["maxWindows"], 3)
        self.assertEqual(store["defaultAutoPresets"]["3"], "master-right")

    def test_columns_divider_only_with_three_windows_and_its_own_range(self):
        self.assertTrue(ws.has_divider("columns", 3))
        self.assertFalse(ws.has_divider("columns", 4))
        self.assertFalse(ws.has_divider("grid", 4))
        self.assertTrue(ws.has_divider("side-by-side", 2))
        store = {"maxWindows": 3, "workspaceAutoPresets": {"5": "columns"}}
        self.assertEqual(ws.set_split(store, 5, 0.9), ("apply", 5, "columns"))
        self.assertEqual(store["workspaceSplits"]["5"], ws.COLUMNS_SPLIT_MAX)
        store = {"maxWindows": 3, "workspaceAutoPresets": {"5": "master-left"}}
        ws.set_split(store, 5, 0.9)
        self.assertEqual(store["workspaceSplits"]["5"], ws.SPLIT_MAX)


class AppPresetGeometryTests(unittest.TestCase):
    """Resizing and adding saved windows in the preview (2026-11-07)."""

    def windows(self):
        # Master left (0..950) and a stack of two on the right, 10 px gaps.
        return [{"class": "a", "at": [0, 0], "size": [950, 1000], "floating": False},
                {"class": "b", "at": [960, 0], "size": [960, 495], "floating": False},
                {"class": "c", "at": [960, 505], "size": [960, 495], "floating": False},
                {"class": "f", "at": [300, 300], "size": [400, 300], "floating": True}]

    def test_cuts_are_the_shared_edges_of_tiled_windows(self):
        cuts = ws.saved_cuts(self.windows())
        self.assertIn((0, 950, 960), cuts)        # master | stack
        self.assertIn((1, 495, 505), cuts)        # between the two stack windows
        self.assertEqual(len(cuts), 2)            # the floating window takes no part

    def test_moving_a_cut_changes_both_sides(self):
        moved = ws.move_saved_cut(self.windows(), 0, 950, 960, 1155)
        self.assertEqual(moved[0]["size"], [1150, 1000])
        self.assertEqual((moved[1]["at"], moved[1]["size"]), ([1160, 0], [760, 495]))
        self.assertEqual((moved[2]["at"], moved[2]["size"]), ([1160, 505], [760, 495]))
        self.assertEqual(moved[3], self.windows()[3])                     # floating: untouched
        self.assertIsNone(ws.move_saved_cut(self.windows(), 0, 950, 960, 1900))   # too small
        moved = ws.move_saved_cut(self.windows(), 1, 495, 505, 300)
        self.assertEqual((moved[1]["size"], moved[2]["at"], moved[2]["size"]), ([960, 295], [960, 305], [960, 695]))
        self.assertEqual(moved[0], self.windows()[0])

    def test_floating_corner_resize_keeps_the_corner_and_a_minimum(self):
        out = ws.resize_saved_floating(self.windows(), 3, 600, 500)
        self.assertEqual((out[3]["at"], out[3]["size"]), ([300, 300], [600, 500]))
        out = ws.resize_saved_floating(self.windows(), 3, 5, 5)
        self.assertEqual(out[3]["size"], [192, 100])
        self.assertIsNone(ws.resize_saved_floating(self.windows(), 0, 600, 500))   # tiled: not by corner

    def test_add_app_halves_the_chosen_or_largest_tiled_window(self):
        store = {"workspaceLayouts": {"4": {"mode": "manual", "apps": [{"class": "a"}], "windows": self.windows()}},
                 "workspaceModes": {"4": "manual"}}
        app = {"name": "Files", "desktop_id": "org.gnome.Nautilus", "class": "org.gnome.Nautilus",
               "desktop_path": "/usr/share/applications/org.gnome.Nautilus.desktop", "icon": "x", "exec": "nautilus"}
        ws.add_saved_app(store, 4, app)
        layout = store["workspaceLayouts"]["4"]
        windows = layout["windows"]
        self.assertEqual(windows[0]["size"], [950, 495])                 # largest = master, halved top/bottom
        self.assertEqual((windows[-1]["at"], windows[-1]["size"]), ([0, 505], [950, 495]))
        self.assertEqual(windows[-1]["app"]["desktop_id"], "org.gnome.Nautilus")
        self.assertNotIn("exec", windows[-1]["app"])                     # never a saved command line
        self.assertEqual(layout["count"], 5)
        self.assertEqual(layout["apps"][-1]["name"], "Files")
        ws.add_saved_app(store, 4, app, index=1)                         # dropped on the top right one
        windows = store["workspaceLayouts"]["4"]["windows"]
        self.assertEqual(windows[1]["size"], [475, 495])
        self.assertEqual(len(store["workspaceLayouts"]["4"]["apps"]), 2)  # the same app is listed once
        self.assertEqual(ws.saved_cuts(windows) and True, True)
        launcher = {"name": "Web", "desktop_id": "chromium", "startup_class": "Chromium", "exec": "chromium"}
        ws.add_saved_app(store, 4, launcher)
        self.assertEqual(store["workspaceLayouts"]["4"]["windows"][-1]["class"], "Chromium")   # StartupWMClass
        self.assertIsNone(ws.add_saved_app(store, 5, app))               # no App Preset there
        self.assertIsNone(ws.add_saved_app(store, 4, {"name": "no id"}))


class UndoAndFreeModeTests(unittest.TestCase):
    """Codex review round 1 (2026-11-07)."""

    def test_undo_takes_back_only_this_change(self):
        before = {"maxWindows": 2, "workspaceCaps": {"1": 2}, "workspaceAutoPresets": {"1": "side-by-side"}}
        after = {"maxWindows": 2, "workspaceCaps": {"1": 3}, "workspaceAutoPresets": {"1": "columns"}}
        # Meanwhile the Workspace Presets plugin changed workspace 2 and the default limit.
        current = {"maxWindows": 4, "workspaceCaps": {"1": 3, "2": 4}, "workspaceAutoPresets": {"1": "columns"}}
        self.assertTrue(ws.undo_merge(current, before, after))
        self.assertEqual(current, {"maxWindows": 4, "workspaceCaps": {"1": 2, "2": 4},
                                   "workspaceAutoPresets": {"1": "side-by-side"}})
        # Changed again since: that entry stays.
        current = {"maxWindows": 2, "workspaceCaps": {"1": 1}, "workspaceAutoPresets": {"1": "columns"}}
        ws.undo_merge(current, before, after)
        self.assertEqual(current["workspaceCaps"], {"1": 1})
        # A key the change added goes away again.
        current = {"workspaceSplits": {"1": 0.6}, "defaultSplit": 0.3}
        ws.undo_merge(current, {}, {"workspaceSplits": {"1": 0.6}, "defaultSplit": 0.3})
        self.assertEqual(current, {})

    def test_undo_of_a_map_the_change_created_keeps_entries_added_since(self):
        before = {"maxWindows": 2}
        after = {"maxWindows": 2, "workspaceCaps": {"1": 3}}
        current = {"maxWindows": 2, "workspaceCaps": {"1": 3, "2": 4}}
        self.assertTrue(ws.undo_merge(current, before, after))
        self.assertEqual(current, {"maxWindows": 2, "workspaceCaps": {"2": 4}})
        current = {"maxWindows": 2, "workspaceCaps": {"1": 3}}
        ws.undo_merge(current, before, after)
        self.assertEqual(current, {"maxWindows": 2})                      # emptied: gone again
        # A map the change removed comes back.
        current = {"maxWindows": 2}
        self.assertTrue(ws.undo_merge(current, {"workspaceSplits": {"1": 0.6}}, {}))
        self.assertEqual(current["workspaceSplits"], {"1": 0.6})
        self.assertFalse(ws.undo_merge({"maxWindows": 4}, {"maxWindows": 2}, {"maxWindows": 3}))  # changed since

    def test_plus_on_a_free_workspace_tiles_at_its_limit(self):
        store = {"maxWindows": 2, "workspaceAutoPresets": {"3": "none"}}
        self.assertTrue(ws.tiles_freely(store, 3))
        self.assertEqual(ws.added_cap(store, 3), 2)                    # not 3: "Tile with 2 windows"
        ws.add_window(store, 3, "stacked")
        self.assertEqual((store["workspaceCaps"]["3"], store["workspaceAutoPresets"]["3"]), (2, "stacked"))
        store = {"maxWindows": 1, "workspaceAutoPresets": {"3": "floating"}}
        self.assertEqual(ws.added_cap(store, 3), 2)
        store = {"maxWindows": 4, "workspaceAutoPresets": {"3": "none"}}
        self.assertEqual(ws.added_cap(store, 3), 2)                    # also from 4: tile with 2
        self.assertEqual(ws.added_cap({"maxWindows": 4}, 3), None)


class MinusTests(unittest.TestCase):
    """− at the left edge of the preview (Uwe's F12 note, 2026-11-07)."""

    def test_one_fewer_then_back_to_the_default(self):
        store = {"maxWindows": 3, "workspaceCaps": {"4": 2}}
        self.assertEqual(ws.removed_cap(store, 4), 1)
        ws.remove_window(store, 4)
        self.assertEqual(store["workspaceCaps"]["4"], 1)
        self.assertEqual(ws.removed_cap(store, 4), 3)                 # one window: the default again
        store["workspaceAutoPresets"] = {"4": "side-by-side"}         # no Side-by-Side with 3
        self.assertEqual(ws.remove_window(store, 4), ("apply", 4, "master-left"))
        self.assertNotIn("4", store["workspaceCaps"])
        self.assertEqual(store["workspaceAutoPresets"]["4"], "master-left")
        self.assertIsNone(ws.removed_cap({"maxWindows": 1}, 4))       # already the default: nothing

    def test_all_workspaces_and_where_minus_does_nothing(self):
        store = {"maxWindows": 2}
        ws.remove_window(store, 0)
        self.assertEqual(store["maxWindows"], 1)
        self.assertEqual(ws.removed_cap(store, 0), 2)
        ws.remove_window(store, 0)
        self.assertNotIn("maxWindows", store)                          # back to the built-in 2
        self.assertIsNone(ws.removed_cap({"maxWindows": 2, "workspaceAutoPresets": {"3": "none"}}, 3))
        manual = {"workspaceModes": {"2": "manual"}, "workspaceLayouts": {"2": {"mode": "manual"}}}
        self.assertIsNone(ws.removed_cap(manual, 2))


if __name__ == "__main__":
    unittest.main()
