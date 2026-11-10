"""The 🎤 beside the question fields: shown only with a configured dictation backend, no shell."""
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dictation  # noqa: E402


def reset():
    dictation._cache.update(at=0.0, backend=None)


class BackendTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_configured_backend_is_found(self):
        with mock.patch.object(dictation.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(dictation, "read_output", return_value=(0, "superwhisper\n")):
            self.assertEqual(dictation.backend(), "superwhisper")

    def test_no_backend_configured(self):
        with mock.patch.object(dictation.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(dictation, "read_output", return_value=(1, "")):
            self.assertIsNone(dictation.backend())

    def test_not_omarchy(self):
        with mock.patch.object(dictation.shutil, "which", return_value=None):
            self.assertIsNone(dictation.backend())

    def test_odd_backend_name_refused(self):
        with mock.patch.object(dictation.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(dictation, "read_output", return_value=(0, "../evil; rm\n")):
            self.assertIsNone(dictation.backend())

    def test_run_uses_argv_and_only_known_actions(self):
        with mock.patch.object(dictation.subprocess, "Popen") as popen:
            dictation.run("start")
            dictation.run("rm -rf")
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.args[0], ["omarchy-dictation", "start"])
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_recording_from_superwhisper_status(self):
        with mock.patch.object(dictation.shutil, "which", return_value="/usr/bin/superwhisper"), \
                mock.patch.object(dictation, "read_output", return_value=(0, '{"phase": "recording"}')):
            self.assertTrue(dictation.recording("superwhisper"))
        self.assertIsNone(dictation.recording("voxtype"))

    def test_backend_without_its_program_counts_as_not_set_up(self):
        def which(name):
            return None if name == "superwhisper" else "/usr/bin/" + name
        with mock.patch.object(dictation.shutil, "which", side_effect=which), \
                mock.patch.object(dictation, "read_output", return_value=(0, "superwhisper\n")):
            self.assertIsNone(dictation.backend(fresh=True))

    def test_open_setup_summons_omarchys_dictation_menu(self):
        with mock.patch.object(dictation.shutil, "which", return_value="/usr/bin/omarchy-menu"), \
                mock.patch.object(dictation.subprocess, "Popen") as popen:
            dictation.open_setup()
        self.assertEqual(popen.call_args.args[0], ["omarchy-menu", "summon", "setup.default.dictation"])

    def test_no_button_away_from_omarchy(self):
        with mock.patch.object(dictation.shutil, "which", return_value=None):
            self.assertFalse(dictation.supported())

    def test_read_output_is_bounded_and_survives_bad_bytes(self):
        sh = "/bin/sh"
        self.assertIsNone(dictation.read_output([sh, "-c", "yes x"], timeout=3, limit=1000))   # too much: ended
        self.assertIsNone(dictation.read_output([sh, "-c", "sleep 5"], timeout=0.3))           # too slow: ended
        code, text = dictation.read_output([sh, "-c", "printf 'a\\377b'"])
        self.assertEqual((code, text), (0, "a\ufffdb"))                                       # no UnicodeDecodeError
        self.assertIsNone(dictation.read_output(["/nonexistent/program"]))

    def test_garbage_status_is_unknown(self):
        with mock.patch.object(dictation.shutil, "which", return_value="/usr/bin/superwhisper"), \
                mock.patch.object(dictation, "read_output", return_value=(0, "[1, 2]")):
            self.assertIsNone(dictation.recording("superwhisper"))

    def test_stop_all_stops_the_active_recorder(self):
        calls = []
        dictation._active["off"] = lambda: calls.append("off")
        dictation.stop_all()
        self.assertEqual(calls, ["off"])
        dictation._active["off"] = None
        dictation.stop_all()                                  # nothing active: nothing happens


if __name__ == "__main__":
    unittest.main()
