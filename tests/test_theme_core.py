"""Tests for theme_core: discovery, hidden list, wallpaper and theme safety."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_core
from images import bmp, gif, jpeg, png, webp


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
        src.write_bytes(png())
        dest = theme_core.add_background_file(self.roots, "nord", src)
        self.assertTrue(dest.is_relative_to(self.roots.user_backgrounds / "nord"))
        self.assertEqual(dest.name.lower(), "photo.png")
        self.assertEqual(dest.read_bytes(), png())
        # Original untouched
        self.assertEqual(src.read_bytes(), png())

    @unittest.skipUnless(shutil.which("heif-enc") and shutil.which("heif-dec")
                         and shutil.which("magick"), "needs libheif and ImageMagick")
    def test_add_heic_converts_to_jpeg(self):
        png = Path(self.tmp.name) / "src.png"
        subprocess.run(["magick", "-size", "64x64", "xc:red", str(png)], check=True)
        src = Path(self.tmp.name) / "IMG_0001.HEIC"
        subprocess.run(["heif-enc", "-o", str(src), str(png)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dest = theme_core.add_background_file(self.roots, "nord", src)
        self.assertEqual(dest.name, "IMG_0001.jpg")
        self.assertEqual(dest.read_bytes()[:3], b"\xff\xd8\xff")

    def test_add_data_takes_type_from_bytes(self):
        dest = theme_core.add_background_data(self.roots, "nord", png(), name="photo.jpg")
        self.assertEqual(dest.name, "photo.png")
        dest = theme_core.add_background_data(self.roots, "nord", gif(),
                                              name="../../evil")
        self.assertEqual(dest.parent, self.roots.user_backgrounds / "nord")
        self.assertEqual(dest.suffix, ".gif")
        with self.assertRaises(ValueError):
            theme_core.add_background_data(self.roots, "nord", b"<svg></svg>", name="x.png")

    def test_image_size_reads_headers_of_every_format(self):
        cases = {"png": png(1920, 1080), "jpeg": jpeg(1920, 1080, app_padding=60000),
                 "gif": gif(1920, 1080), "webp": webp(1920, 1080), "bmp": bmp(1920, -1080)}
        for name, data in cases.items():
            with self.subTest(name):
                self.assertIsNone(theme_core.image_size_problem(data))
                path = Path(self.tmp.name) / f"ok.{name}"
                path.write_bytes(data)
                self.assertIsNone(theme_core.image_size_problem(path))

    def test_decompression_bombs_are_refused_before_decoding(self):
        bombs = {"png": png(60000, 60000), "png-wide": png(100000, 10),
                 "jpeg": jpeg(65000, 65000), "gif": gif(16000, 16000),
                 "gif-frames": gif(1920, 1080, frames=40), "webp": webp(16000, 16000),
                 "webp-frames": webp(1920, 1080, frames=40), "bmp": bmp(1 << 20, 1 << 20)}
        for name, data in bombs.items():
            with self.subTest(name):
                self.assertLess(len(data), 4096)   # tiny files, huge canvases
                self.assertIn("too large", theme_core.image_size_problem(data))

    def test_malformed_or_unknown_headers_are_refused(self):
        for data in (b"", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"\xff\xd8\xff\xe0\x00\x00",
                     b"\xff\xd8\xff\xda\x00\x08", b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x3b",
                     b"RIFF\x00\x00\x00\x00WEBP", b"<svg></svg>", png(0, 10)):
            with self.subTest(data=data[:12]):
                self.assertIsNotNone(theme_core.image_size_problem(data))

    def test_imports_refuse_huge_canvases_and_store_nothing(self):
        bomb = png(60000, 60000)
        src = Path(self.tmp.name) / "bomb.png"
        src.write_bytes(bomb)
        with self.assertRaises(ValueError):
            theme_core.add_background_file(self.roots, "nord", src)
        with self.assertRaises(ValueError):
            theme_core.add_background_bytes(self.roots, "nord", bomb, name="pasted.png")
        with self.assertRaises(ValueError):
            theme_core.add_background_data(self.roots, "nord", gif(1920, 1080, frames=40))
        nord = self.roots.user_backgrounds / "nord"
        self.assertFalse(nord.exists() and any(nord.iterdir()))

    def test_clipboard_text_is_bounded_and_flattened(self):
        calls = []
        replies = {}

        def fake(argv, max_bytes=0, timeout=0, env=None):
            calls.append((argv, max_bytes, timeout))
            return replies[argv[-1]]

        orig = theme_core.read_bounded_output
        theme_core.read_bounded_output = fake
        try:
            replies.update({"--list-types": (b"image/png\ntext/plain;charset=utf-8\n", True),
                            "text/plain;charset=utf-8": (b"misty\r\nforest\x1b[31m\x00 x", True)})
            self.assertEqual(theme_core.clipboard_text(), "misty forest [31m x")
            argv, max_bytes, timeout = calls[-1]
            self.assertEqual(max_bytes, theme_core.MAX_PASTE_TEXT_BYTES)
            self.assertLessEqual(timeout, 2.0)
            self.assertEqual(argv[:2], ["wl-paste", "--no-newline"])
            # An endless or slow stream: the bounded reader gives up, nothing is pasted.
            replies["text/plain;charset=utf-8"] = (b"x" * theme_core.MAX_PASTE_TEXT_BYTES, False)
            self.assertIsNone(theme_core.clipboard_text())
            replies["--list-types"] = (b"", False)   # empty clipboard
            self.assertEqual(theme_core.clipboard_text(), "")
            replies["--list-types"] = (b"image/png\n", True)
            self.assertEqual(theme_core.clipboard_text(), "")
            replies.update({"--list-types": (b"text/plain\n", True), "text/plain": (b"y" * 4000, True)})
            self.assertEqual(len(theme_core.clipboard_text()), theme_core.MAX_FIELD_CHARS)
        finally:
            theme_core.read_bounded_output = orig

    def test_offered_file_name(self):
        self.assertEqual(theme_core.offered_file_name('application/octet-stream;name="43lk53.jpg"'),
                         "43lk53.jpg")
        self.assertEqual(theme_core.offered_file_name('application/octet-stream; name=a/b/c.png'), "c.png")
        self.assertEqual(theme_core.offered_file_name("application/octet-stream"), "")

    def test_heic_decoder_output_is_size_capped(self):
        src = Path(self.tmp.name) / "big.heic"
        src.write_bytes(b"x")
        seen = []
        orig = theme_core.read_bounded_output
        theme_core.read_bounded_output = lambda argv, **kw: seen.append(argv) or (b"", False)
        try:
            with self.assertRaises(ValueError):
                theme_core.add_background_file(self.roots, "nord", src)
        finally:
            theme_core.read_bounded_output = orig
        self.assertTrue(seen[0][0].endswith("prlimit"))
        self.assertEqual(seen[0][1], f"--fsize={theme_core.MAX_BACKGROUND_BYTES}")
        self.assertTrue(seen[0][3].endswith("heif-dec"))

    def test_add_broken_heic_is_rejected(self):
        src = Path(self.tmp.name) / "broken.heic"
        src.write_bytes(b"not a heic")
        with self.assertRaises(ValueError):
            theme_core.add_background_file(self.roots, "nord", src)
        nord = self.roots.user_backgrounds / "nord"
        self.assertFalse(nord.exists() and any(nord.iterdir()))

    def test_add_file_unique_names(self):
        src = Path(self.tmp.name) / "a.png"
        src.write_bytes(png())
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
        dest = theme_core.add_background_bytes(self.roots, "nord", png(),
                                               name="pasted-image.png")
        self.assertEqual(dest.read_bytes(), png())
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


class HyprlandSocketTests(unittest.TestCase):
    def serve(self, reply_chunks, delay):
        import socket as socketlib
        import threading
        import time as timelib
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / ".socket.sock"
        server = socketlib.socket(socketlib.AF_UNIX)
        server.bind(str(path))
        server.listen(1)
        self.addCleanup(server.close)

        def run():
            conn, _ = server.accept()
            with conn:
                conn.recv(64)
                try:
                    for chunk in reply_chunks:
                        conn.sendall(chunk)
                        timelib.sleep(delay)
                except OSError:
                    pass
        threading.Thread(target=run, daemon=True).start()
        orig = theme_core._hyprland_socket
        theme_core._hyprland_socket = lambda: path
        self.addCleanup(setattr, theme_core, "_hyprland_socket", orig)

    def test_cursor_position(self):
        self.serve([b'{"x": 12, "y": 34}'], 0)
        self.assertEqual(theme_core.cursor_position(), (12, 34))

    def test_drip_feed_hits_one_overall_deadline(self):
        import time as timelib
        self.serve([b" "] * 1000, 0.05)
        start = timelib.monotonic()
        self.assertIsNone(theme_core._hyprland_query(b"j/cursorpos", timeout=0.4))
        self.assertLess(timelib.monotonic() - start, 1.0)

    def test_oversized_reply_is_dropped(self):
        self.serve([b"x" * 4096] * 4, 0)
        self.assertIsNone(theme_core._hyprland_query(b"j/cursorpos", max_bytes=4096))


class FileListTests(unittest.TestCase):
    def test_parses_uri_list_gnome_copied_files_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "my photo.png"
            b = Path(tmp) / "b.jpg"
            a.write_bytes(b"x")
            b.write_bytes(b"x")
            uri_a = "file://" + str(a).replace(" ", "%20")
            self.assertEqual(theme_core.parse_file_list(f"# comment\r\n{uri_a}\r\n"), [a])
            self.assertEqual(theme_core.parse_file_list(f"copy\n{uri_a}\nfile://{b}"), [a, b])
            self.assertEqual(theme_core.parse_file_list(f"{a}\n{b}\n"), [a, b])

    def test_ignores_remote_relative_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(theme_core.parse_file_list(
                f"https://example.com/x.png\nrelative.png\n{tmp}/missing.png\n{tmp}"), [])


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
        src.write_bytes(png())
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

    def test_window_position_kept_next_to_sync_setting(self):
        self.assertIsNone(theme_core.window_position())
        theme_core.save_settings(True)
        theme_core.save_window_position((120, 340))
        self.assertEqual(theme_core.window_position(), (120, 340))
        self.assertTrue(theme_core.sync_enabled())
        theme_core.save_settings(False)
        self.assertEqual(theme_core.window_position(), (120, 340))
        theme_core.save_window_position(None)
        self.assertIsNone(theme_core.window_position())
        self.assertFalse(theme_core.sync_enabled())

    def test_window_position_rejects_junk(self):
        for junk in ('{"position": [1, "2"]}', '{"position": [-5, 3]}', '{"position": [true, 3]}',
                     '{"position": {"x": 1}}', '[1, 2]', 'not json'):
            theme_core.settings_file().write_text(junk)
            self.assertIsNone(theme_core.window_position(), junk)

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
