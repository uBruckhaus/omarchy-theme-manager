"""F12 screenshot paths and capture failures, without a compositor."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import screenshots  # noqa: E402


class ScreenshotTests(TestCase):
    def test_f12_is_on_until_turned_off(self):
        self.assertTrue(screenshots.key_enabled({}))
        self.assertTrue(screenshots.key_enabled({"screenshot_key": True}))
        self.assertFalse(screenshots.key_enabled({"screenshot_key": False}))

    def test_folder_setting_accepts_only_absolute_paths(self):
        with patch.object(screenshots, "default_folder", return_value=Path("/tmp/default")):
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "/tmp/shots"}),
                             Path("/tmp/shots"))
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "../shots"}),
                             Path("/tmp/default"))
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "/tmp/../etc"}),
                             Path("/tmp/default"))
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "/tmp/bad\nname"}),
                             Path("/tmp/default"))

    def test_folder_setting_rejects_symlink_component(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real")
            with patch.object(screenshots, "default_folder", return_value=root / "fallback"):
                self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": str(root / "link" / "shots")}),
                                 root / "fallback")

    def test_capture_uses_popup_geometry_and_unique_name(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = datetime(2026, 11, 6, 9, 30, 0)
            (folder / "theme-manager-2026-11-06-09-30-00.png").write_bytes(b"old")
            commands = []

            def fake_grim(argv, **_kwargs):
                commands.append(argv)
                Path(argv[-1]).write_bytes(b"PNG")
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with patch.object(screenshots.subprocess, "run", side_effect=fake_grim):
                path = screenshots.capture((100, 200, 700, 500), folder, now)
            self.assertEqual(path.name, "theme-manager-2026-11-06-09-30-00-2.png")
            self.assertEqual(path.read_bytes(), b"PNG")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(commands[0][0:3], ["grim", "-g", "100,200 700x500"])

    def test_failed_capture_removes_temporary_file(self):
        with TemporaryDirectory() as tmp:
            with patch.object(screenshots.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"failed")):
                with self.assertRaises(OSError):
                    screenshots.capture((0, 0, 400, 300), Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_capture_skips_broken_symlink_and_existing_note(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = datetime(2026, 11, 6, 9, 30, 0)
            (folder / "theme-manager-2026-11-06-09-30-00.png").symlink_to(folder / "missing")
            (folder / "theme-manager-2026-11-06-09-30-00-2.txt").write_text("old")
            def fake_grim(argv, **_kwargs):
                Path(argv[-1]).write_bytes(b"PNG")
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            with patch.object(screenshots.subprocess, "run", side_effect=fake_grim):
                path = screenshots.capture((0, 0, 400, 300), folder, now)
            self.assertEqual(path.name, "theme-manager-2026-11-06-09-30-00-3.png")
            self.assertTrue((folder / "theme-manager-2026-11-06-09-30-00.png").is_symlink())

    def test_custom_folder_failure_uses_default(self):
        geometry = (0, 0, 400, 300)
        with patch.object(screenshots, "default_folder", return_value=Path("/tmp/default")), \
             patch.object(screenshots, "capture", side_effect=[OSError("unwritable"), Path("/tmp/default/a.png")]) as capture:
            self.assertEqual(screenshots.capture_to_settings(geometry, {"screenshot_folder": "/tmp/custom"}),
                             Path("/tmp/default/a.png"))
        self.assertEqual([call.args[1] for call in capture.call_args_list],
                         [Path("/tmp/custom"), Path("/tmp/default")])

    def test_optional_note_is_private_and_never_overwritten(self):
        with TemporaryDirectory() as tmp:
            picture = Path(tmp) / "shot.png"
            self.assertIsNone(screenshots.save_note(picture, "  "))
            note = screenshots.save_note(picture, "  wrong color  ")
            self.assertEqual(note.read_text(), "wrong color\n")
            self.assertEqual(note.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                screenshots.save_note(picture, "another")
