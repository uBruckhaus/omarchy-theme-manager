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


class MimeTests(unittest.TestCase):
    def test_known_and_unknown_types(self):
        self.assertEqual(theme_core.extension_for_mime("image/jpeg"), ".jpg")
        self.assertEqual(theme_core.extension_for_mime("image/PNG; charset=x"), ".png")
        self.assertIsNone(theme_core.extension_for_mime("image/svg+xml"))
        self.assertIsNone(theme_core.extension_for_mime("text/html"))


class SwitcherSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prune_removes_only_hidden_symlinks(self):
        previews = self.root / "previews"
        previews.mkdir()
        image = self.root / "img.png"
        image.write_bytes(b"x")
        for slug in ("alpha", "beta"):
            (previews / f"{slug}.png").symlink_to(image)
        (previews / "gamma.png").write_bytes(b"real file")
        removed = theme_core.prune_switcher_previews({"alpha", "gamma"}, previews)
        self.assertEqual(removed, ["alpha"])
        self.assertFalse((previews / "alpha.png").is_symlink())
        self.assertTrue((previews / "beta.png").is_symlink())
        self.assertTrue((previews / "gamma.png").exists())  # not a symlink: untouched
        self.assertTrue(image.exists())

    def test_invalidate_removes_fast_signature_only(self):
        (self.root / "fast-signature").write_text("v1")
        (self.root / "signature").write_text("v1")
        theme_core.invalidate_switcher_cache(self.root)
        self.assertFalse((self.root / "fast-signature").exists())
        self.assertTrue((self.root / "signature").exists())

    def test_menu_override_inserted_once_and_updated(self):
        menu = self.root / "omarchy-menu.jsonc"
        menu.write_text('{\n  // a comment\n  "personal": {"label":"P"},\n}\n')
        self.assertTrue(theme_core.ensure_menu_override("/opt/a/theme-switcher", menu))
        self.assertFalse(theme_core.ensure_menu_override("/opt/a/theme-switcher", menu))
        self.assertTrue(theme_core.ensure_menu_override("/opt/b/theme-switcher", menu))
        text = menu.read_text()
        self.assertEqual(text.count('"style.theme"'), 1)
        self.assertIn("/opt/b/theme-switcher", text)
        self.assertIn('"personal"', text)
        import json, re
        stripped = re.sub(r"^\s*//[^\n]*\n", "", text, flags=re.M)
        stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
        parsed = json.loads(stripped)
        self.assertEqual(parsed["style.theme"]["label"], "Theme")

    def test_menu_override_respects_user_entry(self):
        menu = self.root / "omarchy-menu.jsonc"
        original = '{\n  "style.theme": {"label":"Mine","action":"x"},\n}\n'
        menu.write_text(original)
        self.assertFalse(theme_core.ensure_menu_override("/opt/a/theme-switcher", menu))
        self.assertEqual(menu.read_text(), original)

    def test_menu_override_creates_missing_file(self):
        menu = self.root / "ext" / "omarchy-menu.jsonc"
        self.assertTrue(theme_core.ensure_menu_override("/opt/a/theme-switcher", menu))
        self.assertIn('"style.theme"', menu.read_text())

    def test_menu_override_rejects_unsafe_path(self):
        with self.assertRaises(ValueError):
            theme_core.ensure_menu_override('/tmp/x"; rm -rf ~', self.root / "m.jsonc")


class HiddenWallpaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.roots = make_roots(self.root)
        self._orig = (theme_core.hidden_wallpapers_file, theme_core.default_roots)
        theme_core.hidden_wallpapers_file = lambda: self.root / "hidden-wallpapers.json"
        theme_core.default_roots = lambda: self.roots
        self.staged = self.roots.state_current / "theme" / "backgrounds"

    def tearDown(self):
        theme_core.hidden_wallpapers_file, theme_core.default_roots = self._orig
        self.tmp.cleanup()

    def theme(self, slug):
        return next(t for t in theme_core.discover_themes(self.roots) if t.slug == slug)

    def apply(self, slug):
        """What `omarchy theme set` does: copy the theme's backgrounds."""
        (self.roots.state_current / "theme.name").write_text(slug + "\n")
        self.staged.mkdir(parents=True, exist_ok=True)
        for base in (self.roots.system_themes, self.roots.user_themes):
            for f in (base / slug / "backgrounds").glob("*"):
                (self.staged / f.name).write_bytes(f.read_bytes())

    def link_background(self, name):
        link = self.roots.state_current / "background"
        link.unlink(missing_ok=True)
        link.symlink_to(self.staged / name)

    def test_locks_for_system_git_and_plain_user_themes(self):
        make_theme(self.roots, "system", "sys", ("s.png",))
        git_theme = make_theme(self.roots, "user", "fromgit", ("g.png",))
        (git_theme / ".git").mkdir()
        make_theme(self.roots, "user", "mine", ("m.png",))
        locks = {w.name: w.locked_reason for t in ("sys", "fromgit", "mine")
                 for w in theme_core.list_backgrounds(self.roots, self.theme(t))}
        self.assertEqual(locks["s.png"], theme_core.LOCK_SYSTEM)
        self.assertEqual(locks["g.png"], theme_core.LOCK_GIT)
        self.assertEqual(locks["m.png"], "")

    def test_git_theme_wallpaper_delete_refused(self):
        git_theme = make_theme(self.roots, "user", "fromgit", ("g.png",))
        (git_theme / ".git").mkdir()
        with self.assertRaises(PermissionError):
            theme_core.remove_wallpaper(self.roots, git_theme / "backgrounds" / "g.png")
        self.assertTrue((git_theme / "backgrounds" / "g.png").exists())

    def test_hide_shipped_wallpaper_records_name_and_keeps_file(self):
        make_theme(self.roots, "system", "sys", ("a.png", "b.png"))
        theme = self.theme("sys")
        a = next(w for w in theme_core.list_backgrounds(self.roots, theme) if w.name == "a.png")
        theme_core.set_wallpaper_hidden(self.roots, theme, a, True)
        self.assertEqual(theme_core.load_hidden_wallpapers(), {"sys": {"a.png"}})
        self.assertTrue(a.path.exists())
        self.assertEqual(oct((self.root / "hidden-wallpapers.json").stat().st_mode & 0o777), "0o600")

    def test_hide_added_wallpaper_moves_it_out_of_omarchys_folder(self):
        make_theme(self.roots, "system", "sys", ("a.png",))
        theme = self.theme("sys")
        src = self.root / "x.png"
        src.write_bytes(b"\x89PNG fake")
        added = theme_core.add_background_file(self.roots, "sys", src)
        info = next(w for w in theme_core.list_backgrounds(self.roots, theme) if w.name == "x.png")
        theme_core.set_wallpaper_hidden(self.roots, theme, info, True)
        self.assertFalse(added.exists())
        hidden = next(w for w in theme_core.list_backgrounds(self.roots, theme) if w.name == "x.png")
        self.assertTrue(hidden.hidden)
        theme_core.set_wallpaper_hidden(self.roots, theme, hidden, False)
        self.assertTrue(added.exists())

    def test_last_visible_cannot_be_hidden(self):
        make_theme(self.roots, "system", "sys", ("a.png",))
        theme = self.theme("sys")
        only = theme_core.list_backgrounds(self.roots, theme)[0]
        with self.assertRaises(ValueError):
            theme_core.set_wallpaper_hidden(self.roots, theme, only, True)

    def test_sync_removes_restores_and_moves_background(self):
        make_theme(self.roots, "system", "sys", ("a.png", "b.png"))
        theme = self.theme("sys")
        self.apply("sys")
        self.link_background("a.png")
        a = next(w for w in theme_core.list_backgrounds(self.roots, theme) if w.name == "a.png")
        theme_core.set_wallpaper_hidden(self.roots, theme, a, True)
        chosen = []
        removed = theme_core.sync_current_theme(self.roots, set_background=chosen.append)
        self.assertEqual(removed, ["a.png"])
        self.assertFalse((self.staged / "a.png").exists())
        self.assertTrue(a.path.exists())                      # original untouched
        self.assertEqual([p.name for p in chosen], ["b.png"])  # desktop left the hidden one
        a = next(w for w in theme_core.list_backgrounds(self.roots, theme) if w.name == "a.png")
        theme_core.set_wallpaper_hidden(self.roots, theme, a, False)
        theme_core.sync_current_theme(self.roots, set_background=chosen.append)
        self.assertTrue((self.staged / "a.png").exists())      # restored into Omarchy's copy

    def test_sync_ignores_other_themes_and_symlinked_staging(self):
        make_theme(self.roots, "system", "sys", ("a.png", "b.png"))
        theme_core.save_hidden_wallpapers({"other": {"a.png"}})
        self.apply("sys")
        self.assertEqual(theme_core.sync_current_theme(self.roots, set_background=lambda p: None), [])
        self.assertTrue((self.staged / "a.png").exists())

    def test_theme_set_hook_written_once_and_never_over_foreign_file(self):
        hook = self.root / "hooks" / "90-theme-manager"
        self.assertTrue(theme_core.ensure_theme_set_hook("/opt/p/switcher_sync.py", hook))
        self.assertFalse(theme_core.ensure_theme_set_hook("/opt/p/switcher_sync.py", hook))
        self.assertIn("switcher_sync.py' wallpapers", hook.read_text())
        hook.write_text("#!/bin/bash\necho mine\n")
        self.assertFalse(theme_core.ensure_theme_set_hook("/opt/p/switcher_sync.py", hook))
        self.assertEqual(hook.read_text(), "#!/bin/bash\necho mine\n")
        with self.assertRaises(ValueError):
            theme_core.ensure_theme_set_hook("/tmp/x'; rm -rf ~", self.root / "h2")

    def test_hidden_list_rejects_bad_entries(self):
        (self.root / "hidden-wallpapers.json").write_text(
            '{"ok": ["a.png", "../x.png", 3], "../bad": ["b.png"]}')
        self.assertEqual(theme_core.load_hidden_wallpapers(), {"ok": {"a.png"}})


class ActiveThemeTests(unittest.TestCase):
    def test_active_theme_delete_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = make_roots(Path(tmp))
            make_theme(roots, "user", "vesper")
            (roots.state_current / "theme.name").write_text("vesper\n")
            with self.assertRaises(PermissionError):
                theme_core.remove_theme(roots, "vesper")
            self.assertTrue((roots.user_themes / "vesper").exists())
            (roots.state_current / "theme.name").write_text("other\n")
            theme_core.remove_theme(roots, "vesper")
            self.assertFalse((roots.user_themes / "vesper").exists())


class UnhideOnApplyTests(unittest.TestCase):
    def test_unhide_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            hidden = Path(tmp) / "hidden.json"
            cache = Path(tmp) / "cache"
            cache.mkdir()
            (cache / "fast-signature").write_text("v1")
            orig = theme_core.switcher_cache_dir
            theme_core.switcher_cache_dir = lambda: cache
            orig_sync = theme_core.sync_enabled
            theme_core.sync_enabled = lambda path=None: True
            try:
                theme_core.save_hidden({"nord", "white"}, hidden)
                self.assertTrue(theme_core.unhide_theme("nord", hidden))
                self.assertEqual(theme_core.load_hidden(hidden), {"white"})
                self.assertFalse((cache / "fast-signature").exists())
                self.assertFalse(theme_core.unhide_theme("nord", hidden))   # not hidden: no-op
            finally:
                theme_core.switcher_cache_dir = orig
                theme_core.sync_enabled = orig_sync


class PickerKeepsCurrentThemeTests(unittest.TestCase):
    def test_current_theme_never_pruned_and_row_cache_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previews = root / "previews"
            previews.mkdir()
            image = root / "img.png"
            image.write_bytes(b"x")
            for slug in ("nord", "tokyo-night"):
                (previews / f"{slug}.png").symlink_to(image)
            rows = root / "image-selector"
            rows.mkdir()
            import hashlib
            stamp = rows / (hashlib.md5(str(previews).encode()).hexdigest() + ".fast-signature")
            stamp.write_text("v2")
            orig = theme_core.image_selector_cache
            theme_core.image_selector_cache = lambda: rows
            try:
                removed = theme_core.prune_switcher_previews({"nord", "tokyo-night"}, previews, keep="tokyo-night")
            finally:
                theme_core.image_selector_cache = orig
            self.assertEqual(removed, ["nord"])
            self.assertTrue((previews / "tokyo-night.png").is_symlink())   # never an empty picker
            self.assertFalse(stamp.exists())                                # stale rows forgotten


class OptInSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._orig = (theme_core.settings_file, theme_core.theme_set_hook_file, theme_core.menu_extension_file)
        theme_core.settings_file = lambda: self.root / "settings.json"
        theme_core.theme_set_hook_file = lambda: self.root / "hooks" / "90-theme-manager"
        theme_core.menu_extension_file = lambda: self.root / "omarchy-menu.jsonc"

    def tearDown(self):
        (theme_core.settings_file, theme_core.theme_set_hook_file, theme_core.menu_extension_file) = self._orig
        self.tmp.cleanup()

    def test_off_by_default_and_on_for_existing_installs(self):
        self.assertFalse(theme_core.sync_enabled())
        theme_core.ensure_theme_set_hook("/opt/p/switcher_sync.py")   # installed by an older version
        self.assertTrue(theme_core.sync_enabled())
        theme_core.save_settings(False)
        self.assertFalse(theme_core.sync_enabled())

    def test_removal_takes_only_our_lines_and_hook(self):
        menu = self.root / "omarchy-menu.jsonc"
        menu.write_text('{\n  // mine\n  "personal": {"label":"P"},\n}\n')
        theme_core.ensure_menu_override("/opt/p/theme-switcher", menu)
        theme_core.ensure_theme_set_hook("/opt/p/switcher_sync.py")
        self.assertTrue(theme_core.remove_menu_override(menu))
        self.assertEqual(menu.read_text(), '{\n  // mine\n  "personal": {"label":"P"},\n}\n')
        self.assertTrue(theme_core.remove_theme_set_hook())
        self.assertFalse((self.root / "hooks" / "90-theme-manager").exists())
        foreign = self.root / "hooks" / "90-theme-manager"
        foreign.write_text("#!/bin/bash\necho mine\n")
        self.assertFalse(theme_core.remove_theme_set_hook())
        self.assertTrue(foreign.exists())


if __name__ == "__main__":
    unittest.main()
