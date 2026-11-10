"""Every interface text in the sources has a translation in every language."""
import ast
import json
import os
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import i18n  # noqa: E402

SOURCES = ("theme_popup.py", "workspaces_page.py", "ask_page.py", "theme_core.py", "theme_catalog.py", "wallpaper_search.py", "web_images.py",
           "theme_auto.py", "theme_custom.py", "theme_shortcuts.py")


def is_translate(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"


def msgids():
    ids, plural = set(), set()
    for name in SOURCES:
        for node in ast.walk(ast.parse((ROOT / name).read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if node.func.id in ("_", "N_"):
                        ids.add(first.value)
                    elif node.func.id == "ngettext":
                        plural.add(first.value)
    return ids, plural


class TranslationTests(unittest.TestCase):
    def test_every_text_is_translated_with_the_same_placeholders(self):
        ids, plural = msgids()
        holes = lambda text: sorted(re.findall(r"\{\d*\}", text))
        for code, _name in i18n.LANGUAGES[1:]:
            table = i18n.STRINGS[code]
            for msgid in ids:
                self.assertIn(msgid, table, f"{code}: {msgid!r}")
                self.assertEqual(holes(table[msgid]), holes(msgid), f"{code}: {msgid!r}")
            for msgid in plural:
                self.assertIsInstance(table.get(i18n.PLURAL_PREFIX + msgid), list, f"{code}: {msgid!r}")

    def test_no_function_hides_the_translate_function(self):
        for name in SOURCES:
            for fn in ast.walk(ast.parse((ROOT / name).read_text())):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                if not any(is_translate(n) for n in ast.walk(fn)):
                    continue
                args = fn.args
                local = [a.arg for a in args.args + args.kwonlyargs + [args.vararg, args.kwarg] if a]
                local += [n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
                self.assertNotIn("_", local, f"{name}:{fn.lineno}")

    def test_polish_plurals(self):
        i18n._lang = "pl"
        try:
            self.assertEqual(i18n.ngettext("{0} hidden theme not shown.", "{0} hidden themes not shown.", 3).format(3),
                             "3 ukryte motywy nie są pokazane.")
            self.assertEqual(i18n.ngettext("{0} hidden theme not shown.", "{0} hidden themes not shown.", 5).format(5),
                             "5 ukrytych motywów nie jest pokazanych.")
        finally:
            i18n._lang = None


class LanguageSettingTests(unittest.TestCase):
    """The language goes through the same settings lock, reader and writer."""

    def setUp(self):
        import tempfile
        import theme_core  # noqa: F401  first: importing it points i18n at the real settings file
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.old = i18n.SETTINGS_FILE
        i18n.SETTINGS_FILE = self.path
        real = Path.home() / ".config/omarchy/theme-manager/settings.json"
        self.assertNotEqual(Path(i18n.SETTINGS_FILE), real)   # never the user's own file

    def tearDown(self):
        i18n.SETTINGS_FILE = self.old
        self.tmp.cleanup()

    def test_overlapping_updates_are_never_lost(self):
        import threading
        import theme_core
        self.path.write_text('{"sort": "name"}')
        errors = []

        def languages():
            try:
                for code in ("de", "fr", "auto") * 10:
                    i18n.save_language(code)
            except Exception as error:                    # noqa: BLE001
                errors.append(error)

        def counters():
            try:
                for n in range(30):
                    theme_core._update_settings(self.path, counter=n)
            except Exception as error:                    # noqa: BLE001
                errors.append(error)
        threads = [threading.Thread(target=languages), threading.Thread(target=counters)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        data = json.loads(self.path.read_text())
        self.assertEqual(data["counter"], 29)             # the other writer's last value stays
        self.assertEqual(data["sort"], "name")
        self.assertNotIn("language", data)                # "auto" last

    def test_a_non_regular_settings_file_is_never_waited_on(self):
        os.mkfifo(self.path)
        self.assertEqual(i18n.language_setting(), "auto")  # a FIFO: not read, no hang

    def test_a_held_settings_lock_ends_in_an_error_not_a_hang(self):
        import fcntl
        import theme_core
        lock = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with unittest.mock.patch.object(theme_core, "SETTINGS_LOCK_SECONDS", 0.2):
                with self.assertRaises(TimeoutError):
                    i18n.save_language("de")
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
