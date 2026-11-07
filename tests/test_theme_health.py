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


if __name__ == "__main__":
    unittest.main()
