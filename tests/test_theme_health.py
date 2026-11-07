import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_core
import theme_health


class HealthTests(unittest.TestCase):
    def test_inspect_reports_disabled_without_changing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(theme_core, "sync_enabled", return_value=False), \
                 patch.object(theme_core, "menu_extension_file", return_value=root / "menu"), \
                 patch.object(theme_core, "theme_set_hook_file", return_value=root / "hook"), \
                 patch.object(theme_core, "switcher_cache_dir", return_value=root / "cache"), \
                 patch.object(theme_core, "load_hidden", return_value=set()), \
                 patch.object(theme_core, "current_theme", return_value="nord"):
                report = theme_health.inspect()
                self.assertFalse(report["enabled"])
                self.assertFalse(report["menu"])
                self.assertFalse(report["hook"])
                self.assertFalse((root / "menu").exists())



class BoundedReadTests(unittest.TestCase):
    """Marketplace review 2.0.0: the health check never reads a huge file, a FIFO or a link."""

    def test_read_own_refuses_links_fifos_and_big_files(self):
        import os
        import tempfile
        import theme_health
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "ok").write_text("marker")
            self.assertEqual(theme_health._read_own(base / "ok", 100), "marker")
            (base / "big").write_bytes(b"x" * 2000)
            self.assertEqual(theme_health._read_own(base / "big", 1000), "")
            (base / "link").symlink_to(base / "ok")
            self.assertEqual(theme_health._read_own(base / "link", 100), "")
            os.mkfifo(base / "fifo")                         # would block a plain read
            self.assertEqual(theme_health._read_own(base / "fifo", 100), "")
            self.assertEqual(theme_health._read_own(base / "missing", 100), "")

if __name__ == "__main__":
    unittest.main()
