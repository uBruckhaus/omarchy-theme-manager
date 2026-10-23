"""The Information page's cards: the marketplace list merged with the built-in one (more_catalog)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import more_catalog  # noqa: E402
import more_plugins  # noqa: E402


class MoreCatalogTests(unittest.TestCase):
    def test_nothing_read_yet_shows_the_built_in_list(self):
        self.assertEqual(more_catalog.cards([]), list(more_plugins.PLUGINS))

    def test_known_plugins_keep_glyph_and_text_new_ones_get_a_summary(self):
        found = [{"id": "ubruckhaus.wlan-monitor", "name": "WLAN Monitor", "description": "x"},
                 {"id": "ubruckhaus.brand-new", "name": "Brand New", "description": "Does new things. More."}]
        wlan = next(p for p in more_plugins.PLUGINS if p[0] == "ubruckhaus.wlan-monitor")
        self.assertEqual(more_catalog.cards(found),
                         [wlan, ("ubruckhaus.brand-new", "Brand New", more_catalog.NEW_GLYPH, "Does new things.")])

    def test_a_plugin_no_longer_listed_is_left_out(self):
        found = [{"id": "ubruckhaus.hue-lights", "name": "Hue Lights", "description": ""}]
        self.assertEqual([c[0] for c in more_catalog.cards(found)], ["ubruckhaus.hue-lights"])

    def test_cache_is_the_plugins_own_private_folder(self):
        self.assertEqual(more_catalog.cache_path(),
                         Path.home() / ".cache/ubruckhaus.theme-manager/plugin-catalog.json")

    def test_cached_reads_without_the_network(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(more_catalog, "cache_path", return_value=Path(tmp) / "c" / "plugin-catalog.json"), \
                mock.patch.object(more_catalog.plugin_catalog, "refresh", side_effect=AssertionError("network")), \
                mock.patch.object(more_catalog.plugin_catalog, "read_bounded", side_effect=AssertionError("network")):
            self.assertEqual(more_catalog.cached(), [])


class RefreshOnThePageTests(unittest.TestCase):
    """theme_popup.ThemeManager.refresh_info_cards: worker → main loop, never stuck refreshing."""

    def setUp(self):
        import theme_popup  # GTK; imported here so the merge tests above need no display
        self.popup = theme_popup
        self.app = SimpleNamespace(filled=[])
        self.app.fill_info_cards = self.app.filled.append
        sync_thread = lambda target, daemon=True: SimpleNamespace(start=target)
        self.main_loop = []                          # idle callbacks, run only by drain()
        self.patches = [mock.patch.object(theme_popup.threading, "Thread", sync_thread),
                        mock.patch.object(theme_popup.GLib, "idle_add", lambda fn, *a: self.main_loop.append((fn, a)))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def refresh(self):
        self.popup.ThemeManager.refresh_info_cards(self.app)

    def drain(self):
        while self.main_loop:
            fn, args = self.main_loop.pop(0)
            fn(*args)

    def test_result_reaches_the_page_on_the_main_loop(self):
        with mock.patch.object(self.popup.more_catalog, "refresh", return_value=[{"id": "ubruckhaus.x"}]):
            self.refresh()
        self.assertEqual(self.app.filled, [])            # nothing touched from the worker
        self.assertTrue(self.app.info_refreshing)
        self.drain()
        self.assertEqual(self.app.filled, [[{"id": "ubruckhaus.x"}]])
        self.assertFalse(self.app.info_refreshing)

    def test_a_failure_does_not_leave_the_page_refreshing(self):
        with mock.patch.object(self.popup.more_catalog, "refresh", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.refresh()
        self.drain()
        self.assertEqual(self.app.filled, [])
        self.assertFalse(self.app.info_refreshing)

    def test_no_second_request_while_one_runs(self):
        self.app.info_refreshing = True
        with mock.patch.object(self.popup.more_catalog, "refresh", side_effect=AssertionError("second request")):
            self.refresh()
        self.assertEqual(self.app.filled, [])


if __name__ == "__main__":
    unittest.main()
