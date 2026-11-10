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

    def test_backend_without_its_program_counts_as_not_set_up(self):
        def which(name):
            return None if name == "superwhisper" else "/usr/bin/" + name
        with mock.patch.object(dictation.shutil, "which", side_effect=which), \
                mock.patch.object(dictation, "read_output", return_value=(0, "superwhisper\n")):
            self.assertIsNone(dictation.backend(fresh=True))

    def test_toggle_runs_omarchy_dictation_toggle_only_on_omarchy(self):
        with mock.patch.object(dictation, "supported", return_value=True), \
                mock.patch.object(dictation.subprocess, "Popen") as popen:
            dictation.toggle()
        self.assertEqual(popen.call_args.args[0], ["omarchy-dictation", "toggle"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        with mock.patch.object(dictation, "supported", return_value=False), \
                mock.patch.object(dictation.subprocess, "Popen") as popen:
            dictation.toggle()
            dictation.open_setup()
        popen.assert_not_called()

    def test_open_setup_summons_omarchys_dictation_menu(self):
        with mock.patch.object(dictation.shutil, "which", return_value="/usr/bin/omarchy-menu"), \
                mock.patch.object(dictation, "supported", return_value=True), \
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


if __name__ == "__main__":
    unittest.main()
