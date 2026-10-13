"""Every interface text in the sources has a translation in every language."""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import i18n  # noqa: E402

SOURCES = ("theme_popup.py", "theme_core.py", "theme_catalog.py", "wallpaper_search.py",
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


if __name__ == "__main__":
    unittest.main()
