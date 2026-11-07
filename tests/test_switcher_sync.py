"""Startup sync for Omarchy's in-process theme picker."""
import sys
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import switcher_sync  # noqa: E402


class StartupSyncTests(unittest.TestCase):
    def test_startup_filters_before_refreshing_native_picker(self):
        calls = []
        with patch.object(switcher_sync.theme_core, "sync_enabled", return_value=True), \
             patch.object(switcher_sync.theme_core, "refresh_switcher_cache",
                   side_effect=lambda wrapper: calls.append(("cache", wrapper))), \
             patch.object(switcher_sync.theme_core, "load_hidden", return_value={"nord"}), \
             patch.object(switcher_sync.theme_core, "prune_switcher_previews",
                   side_effect=lambda hidden: calls.append(("prune", hidden))):
            self.assertEqual(switcher_sync.main(["switcher_sync.py", "startup"]), 0)
        self.assertEqual([name for name, _ in calls], ["prune", "cache"])
        self.assertEqual(calls[0][1], {"nord"})

    def test_startup_is_inert_when_sync_is_off(self):
        with patch.object(switcher_sync.theme_core, "sync_enabled", return_value=False), \
             patch.object(switcher_sync.theme_core, "refresh_switcher_cache") as refresh:
            self.assertEqual(switcher_sync.main(["switcher_sync.py", "startup"]), 0)
        refresh.assert_not_called()

    def test_older_omarchy_without_native_picker_is_supported(self):
        with patch.object(switcher_sync.theme_core, "sync_enabled", return_value=True), \
             patch.object(switcher_sync.theme_core, "refresh_switcher_cache"), \
             patch.object(switcher_sync.theme_core, "load_hidden", return_value=set()), \
             patch.object(switcher_sync.theme_core, "prune_switcher_previews"):
            self.assertEqual(switcher_sync.main(["switcher_sync.py", "startup"]), 0)

    def test_core_cache_refresh_loads_shell_rows_after_wrapper(self):
        calls = []
        with patch.object(switcher_sync.theme_core, "_forget_picker_rows",
                          side_effect=lambda _path: calls.append("forget")), \
             patch.object(switcher_sync.theme_core, "read_bounded_output",
                          side_effect=lambda argv, **_kw: (calls.append("preload") or b"", True)), \
             patch.object(switcher_sync.theme_core, "refresh_shell_theme_rows",
                          side_effect=lambda: calls.append("native")):
            self.assertTrue(switcher_sync.theme_core.refresh_switcher_cache("/opt/theme-switcher"))
        self.assertEqual(calls, ["forget", "preload", "native"])

    def test_menu_id_matches_installed_version(self):
        core = switcher_sync.theme_core
        self.assertEqual(core.menu_entry_id('{"appearance.theme": {}}'), "appearance.theme")
        self.assertEqual(core.menu_entry_id('{"style.theme": {}}'), "style.theme")

    def test_menu_override_uses_installed_menu_id(self):
        core = switcher_sync.theme_core
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default = root / "default/omarchy/omarchy-menu.jsonc"
            default.parent.mkdir(parents=True)
            default.write_text('{"appearance.theme": {}}')
            extension = root / "extension.jsonc"
            with patch.dict(os.environ, {"OMARCHY_PATH": str(root)}), \
                 patch.object(core, "menu_extension_file", return_value=extension):
                self.assertTrue(core.ensure_menu_override("/opt/theme-switcher"))
            self.assertIn('"appearance.theme"', extension.read_text())
            self.assertNotIn('"style.theme"', extension.read_text())

    def test_forget_picker_rows_removes_all_signature_variants(self):
        core = switcher_sync.theme_core
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previews = root / "previews"
            previews.mkdir()
            cache = root / "rows"
            cache.mkdir()
            key = core.hashlib.md5(str(previews).encode()).hexdigest()
            for suffix in ("fast-signature", "signature", "rows"):
                (cache / f"{key}.{suffix}").write_text("stale")
            with patch.object(core, "image_selector_cache", return_value=cache):
                core._forget_picker_rows(previews)
            self.assertEqual(list(cache.iterdir()), [])

    def test_preload_wrapper_keeps_preload_on_both_switcher_versions(self):
        wrapper = Path(__file__).resolve().parents[1] / "theme-switcher"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "omarchy" / "bin"
            bin_dir.mkdir(parents=True)
            result_file = root / "args"
            menu = bin_dir / "omarchy-menu-images"
            menu.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$ARGS_FILE"\n')
            menu.chmod(0o755)
            switcher = bin_dir / "omarchy-theme-switcher"
            env = {**os.environ, "HOME": str(root), "XDG_CACHE_HOME": str(root / "cache"),
                   "OMARCHY_PATH": str(root / "omarchy"), "ARGS_FILE": str(result_file),
                   "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
            for version, script in (("old", '#!/bin/bash\nexec omarchy-menu-images "$@" --selected x /tmp/previews\n'),
                                    ("new", '#!/bin/bash\n[[ ${1:-} == --preload ]] && shift\nexec omarchy-menu-images --selected x /tmp/previews\n')):
                switcher.write_text(script)
                switcher.chmod(0o755)
                result = subprocess.run([str(wrapper), "--preload"], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                self.assertEqual(result.returncode, 0, (version, result.stderr.decode()))
                arguments = result_file.read_text().splitlines()
                self.assertEqual(arguments.count("--preload"), 1, version)
                self.assertEqual(arguments[0], "--preload", version)


if __name__ == "__main__":
    unittest.main()
