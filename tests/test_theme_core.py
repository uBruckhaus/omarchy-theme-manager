"""Tests for theme_core: discovery, hidden list, wallpaper and theme safety."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_core


def make_roots(tmp: Path) -> theme_core.Roots:
    roots = theme_core.Roots(
        user_themes=tmp / "themes" / "user",
        system_themes=tmp / "themes" / "system",
        user_backgrounds=tmp / "backgrounds",
        state_current=tmp / "state" / "current",
    )
    for part in (roots.user_themes, roots.system_themes, roots.user_backgrounds,
                 roots.state_current):
        part.mkdir(parents=True, exist_ok=True)
    return roots


def make_theme(roots: theme_core.Roots, scope: str, slug: str,
               backgrounds: tuple[str, ...] = ()) -> Path:
    base = roots.user_themes if scope == "user" else roots.system_themes
    theme = base / slug
    theme.mkdir(parents=True, exist_ok=True)
    (theme / "colors.toml").write_text("[color]\naccent = \"#000000\"\n")
    if backgrounds:
        bg = theme / "backgrounds"
        bg.mkdir(exist_ok=True)
        for name in backgrounds:
            (bg / name).write_bytes(b"\x89PNG fake")
    (theme / "preview.png").write_bytes(b"\x89PNG fake")
    return theme


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.roots = make_roots(Path(self.tmp.name))

    def test_user_and_system_listed_and_sorted(self):
        make_theme(self.roots, "user", "b-user")
        make_theme(self.roots, "system", "a-system")
        themes = theme_core.discover_themes(self.roots)
        self.assertEqual([t.slug for t in themes], ["a-system", "b-user"])
        self.assertEqual({t.slug: t.scope for t in themes},
                         {"a-system": "system", "b-user": "user"})

    def test_user_copy_shadows_system(self):
        make_theme(self.roots, "user", "retro-82")
        make_theme(self.roots, "system", "retro-82")
        themes = theme_core.discover_themes(self.roots)
        self.assertEqual([t.slug for t in themes], ["retro-82"])
        self.assertEqual(themes[0].scope, "user")
        self.assertTrue(themes[0].path.is_relative_to(self.roots.user_themes))

    def test_current_theme_detected(self):
        make_theme(self.roots, "user", "vault")
        (self.roots.state_current / "theme.name").write_text("vault\n")
        themes = theme_core.discover_themes(self.roots)
        self.assertTrue(themes[0].current)

    def test_display_name(self):
        self.assertEqual(theme_core.display_name("japan-night"), "Japan Night")
        self.assertEqual(theme_core.display_name("retro-82"), "Retro 82")
        self.assertEqual(theme_core.display_name("vault"), "Vault")

    def test_preview_found_case_insensitive(self):
        make_theme(self.roots, "user", "t1")
        themes = theme_core.discover_themes(self.roots)
        self.assertIsNotNone(themes[0].preview)


class HiddenListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hidden_path = Path(self.tmp.name) / "hidden.json"

    def test_roundtrip(self):
        theme_core.save_hidden({"vault", "retro-82"}, self.hidden_path)
        self.assertEqual(theme_core.load_hidden(self.hidden_path),
                         {"vault", "retro-82"})

    def test_load_ignores_garbage(self):
        for garbage in (b"{ not json", b'"a string"', b"null", b"42"):
            self.hidden_path.write_bytes(garbage)
            self.assertEqual(theme_core.load_hidden(self.hidden_path), set())

    def test_load_ignores_traversal_slugs(self):
        self.hidden_path.write_text('["ok", "../evil", "..", ".", "a/b", "x y"]')
        self.assertEqual(theme_core.load_hidden(self.hidden_path), {"ok"})

    def test_write_is_atomic_0600(self):
        theme_core.save_hidden({"a"}, self.hidden_path)
        self.assertEqual(os.stat(self.hidden_path).st_mode & 0o777, 0o600)
        # A pre-planted symlink at the target must not be followed:
        # save_hidden replaces the link itself.
        link = Path(self.tmp.name) / "other" / "link"
        link.parent.mkdir()
        target = Path(self.tmp.name) / "other" / "victim"
        target.write_text("precious")
        link.symlink_to(target)
        theme_core.save_hidden({"b"}, link)
        self.assertTrue(link.is_file())
        self.assertFalse(link.is_symlink())
        self.assertEqual(target.read_text(), "precious")


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.roots = make_roots(Path(self.tmp.name))
        self.theme = make_theme(self.roots, "system", "nord",
                                backgrounds=("0-black-moon.jpg", "1-city-view.png"))
        self.user_theme = make_theme(self.roots, "user", "vault",
                                     backgrounds=("1-gate.jpg",))

    def find(self, slug: str) -> theme_core.ThemeInfo:
        return next(t for t in theme_core.discover_themes(self.roots)
                    if t.slug == slug)

    def test_list_merges_user_and_theme_folders(self):
        wall = self.find("nord")
        (self.roots.user_backgrounds / "nord").mkdir(parents=True)
        (self.roots.user_backgrounds / "nord" / "9-new.webp").write_bytes(b"x")
        names = [w.name for w in theme_core.list_backgrounds(self.roots, wall)]
        self.assertEqual(names, ["0-black-moon.jpg", "1-city-view.png", "9-new.webp"])

    def test_user_file_shadows_same_name(self):
        wall = self.find("nord")
        (self.roots.user_backgrounds / "nord").mkdir(parents=True)
        (self.roots.user_backgrounds / "nord" / "0-black-moon.jpg").write_bytes(b"x")
        infos = {w.name: w for w in theme_core.list_backgrounds(self.roots, wall)}
        self.assertEqual(len(infos), 2)  # shadowed, not duplicated
        self.assertTrue(infos["0-black-moon.jpg"].deletable)      # the user copy wins
        self.assertEqual(infos["0-black-moon.jpg"].source, "user")
        self.assertFalse(infos["1-city-view.png"].deletable)      # theme copy stays read-only

    def test_system_theme_wallpapers_not_deletable(self):
        wall = self.find("nord")
        for info in theme_core.list_backgrounds(self.roots, wall):
            if info.source == "theme":
                self.assertFalse(info.deletable)

    def test_user_theme_wallpapers_deletable(self):
        wall = self.find("vault")
        infos = theme_core.list_backgrounds(self.roots, wall)
        self.assertTrue(infos[0].deletable)

    def test_add_file_copies_into_user_folder(self):
        src = Path(self.tmp.name) / "photo.PNG"
        src.write_bytes(b"imagedata")
        dest = theme_core.add_background_file(self.roots, "nord", src)
        self.assertTrue(dest.is_relative_to(self.roots.user_backgrounds / "nord"))
        self.assertEqual(dest.name.lower(), "photo.png")
        self.assertEqual(dest.read_bytes(), b"imagedata")
        # Original untouched
        self.assertEqual(src.read_bytes(), b"imagedata")

    def test_add_file_unique_names(self):
        src = Path(self.tmp.name) / "a.png"
        src.write_bytes(b"img")
        first = theme_core.add_background_file(self.roots, "nord", src)
        second = theme_core.add_background_file(self.roots, "nord", src)
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, "a-2.png")

    def test_add_rejects_non_images_and_paths(self):
        evil = Path(self.tmp.name) / "evil.txt"
        evil.write_text("nope")
        with self.assertRaises(ValueError):
            theme_core.add_background_file(self.roots, "nord", evil)
        with self.assertRaises(ValueError):
            theme_core.add_background_file(self.roots, "../escape", src := Path("x.png"))
        with self.assertRaises(ValueError):
            theme_core.add_background_file(self.roots, "a/b", Path("x.png"))
        with self.assertRaises(ValueError):
            theme_core.add_background_file(self.roots, "..", Path("x.png"))

    def test_add_bytes_roundtrip(self):
        dest = theme_core.add_background_bytes(self.roots, "nord", b"\x89PNG",
                                               name="pasted-image.png")
        self.assertEqual(dest.read_bytes(), b"\x89PNG")
        with self.assertRaises(ValueError):
            theme_core.add_background_bytes(self.roots, "nord", b"", name="x.png")

    def test_remove_user_wallpaper_ok(self):
        wall = self.find("nord")
        (self.roots.user_backgrounds / "nord").mkdir(parents=True)
        user_file = self.roots.user_backgrounds / "nord" / "9-new.webp"
        user_file.write_bytes(b"x")
        theme_core.remove_wallpaper(self.roots, user_file)
        self.assertFalse(user_file.exists())

    def test_remove_system_theme_wallpaper_refused(self):
        wall = self.find("nord")
        system_file = wall.path / "backgrounds" / "0-black-moon.jpg"
        with self.assertRaises(PermissionError):
            theme_core.remove_wallpaper(self.roots, system_file)
        self.assertTrue(system_file.exists())

    def test_remove_outside_roots_refused(self):
        victim = Path(self.tmp.name) / "outside.png"
        victim.write_bytes(b"x")
        with self.assertRaises(PermissionError):
            theme_core.remove_wallpaper(self.roots, victim)
        self.assertTrue(victim.exists())

    def test_remove_symlink_unlinks_link_not_target(self):
        wall = self.find("nord")
        (self.roots.user_backgrounds / "nord").mkdir(parents=True)
        outside = Path(self.tmp.name) / "outside.png"
        outside.write_bytes(b"x")
        link = self.roots.user_backgrounds / "nord" / "link.png"
        link.symlink_to(outside)
        theme_core.remove_wallpaper(self.roots, link)
        self.assertFalse(link.exists())
        self.assertTrue(outside.exists())

    def test_remove_directory_refused(self):
        (self.roots.user_backgrounds / "nord").mkdir(parents=True)
        with self.assertRaises(PermissionError):
            theme_core.remove_wallpaper(self.roots, self.roots.user_backgrounds / "nord")


class RemoveThemeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.roots = make_roots(Path(self.tmp.name))

    def test_user_theme_removed(self):
        theme = make_theme(self.roots, "user", "vault", backgrounds=("1.jpg",))
        theme_core.remove_theme(self.roots, "vault")
        self.assertFalse(theme.exists())

    def test_system_theme_refused(self):
        theme = make_theme(self.roots, "system", "nord")
        with self.assertRaises((FileNotFoundError, PermissionError)):
            theme_core.remove_theme(self.roots, "nord")
        self.assertTrue(theme.exists())

    def test_symlinked_user_theme_unlinked_not_followed(self):
        real = make_theme(self.roots, "user", "real")
        link = self.roots.user_themes / "link"
        link.symlink_to(real)
        theme_core.remove_theme(self.roots, "link")
        self.assertFalse(link.is_symlink())
        self.assertTrue(real.exists())  # only the link was removed

    def test_bad_slugs_refused(self):
        for slug in ("", "..", "../x", "a/b", "a b", ".hidden"):
            with self.assertRaises(ValueError):
                theme_core.remove_theme(self.roots, slug)


class BoundedProcessTests(unittest.TestCase):
    def test_normal_output(self):
        out, ok = theme_core.read_bounded_output(["echo", "hello"])
        self.assertTrue(ok)
        self.assertEqual(out, b"hello\n")

    def test_cap_kills_and_flags(self):
        # 1 MiB of output; cap at 64 KiB.
        out, ok = theme_core.read_bounded_output(
            ["python3", "-c", "import sys; sys.stdout.buffer.write(b'A' * 1048576)"],
            max_bytes=65536, timeout=10.0)
        self.assertFalse(ok)
        self.assertLessEqual(len(out), 65536)

    def test_timeout(self):
        out, ok = theme_core.read_bounded_output(["sleep", "5"], timeout=0.5)
        self.assertFalse(ok)

    def test_missing_binary(self):
        out, ok = theme_core.read_bounded_output(["definitely-not-a-command-xyz"])
        self.assertFalse(ok)
        self.assertEqual(out, b"")


class PlainTextTests(unittest.TestCase):
    def test_strips_markup_and_controls(self):
        # Angle brackets and ampersands must never survive: notification
        # daemons render markup, so they are dropped, not interpreted.
        for text in ("<b>hi</b>", "<img src=x>", "a & b"):
            self.assertNotIn("<", theme_core.plain_text(text))
            self.assertNotIn(">", theme_core.plain_text(text))
            self.assertNotIn("&", theme_core.plain_text(text))
        self.assertEqual(theme_core.plain_text("a\tb\nc"), "a b c")
        self.assertEqual(len(theme_core.plain_text("x" * 500)), 300)


if __name__ == "__main__":
    unittest.main()
