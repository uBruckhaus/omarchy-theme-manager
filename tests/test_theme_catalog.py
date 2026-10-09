"""Tests for theme_catalog: parsing the omarchy.org gallery, fetching and installing."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_catalog
import theme_core

CARD = '<li><a href="{repo}" class="x"><img src="{img}" alt="a" width="1"/><span class="y">{name}</span></a></li>'


def page(*cards):
    return "<html><ul>" + "".join(CARD.format(repo=r, img=i, name=n) for r, i, n in cards) + "</ul></html>"


class ParseTests(unittest.TestCase):
    def test_accepts_github_cards(self):
        themes = theme_catalog.parse_catalog(page(
            ("https://github.com/bjarneo/omarchy-ash-theme", "/assets/themes/ash.webp", "Ash"),
            ("https://github.com/ankur311sudo/black_arch", "/assets/themes/black-arch.webp", "Black Arch")))
        self.assertEqual([(t.name, t.slug, t.preview) for t in themes],
                         [("Ash", "ash", "ash.webp"), ("Black Arch", "black_arch", "black-arch.webp")])

    def test_rejects_hostile_cards(self):
        themes = theme_catalog.parse_catalog(page(
            ("https://evil.example/x/omarchy-a-theme", "/assets/themes/a.webp", "Other host"),
            ("ext::sh -c id", "/assets/themes/b.webp", "Helper"),
            ("https://github.com/x/y", "https://evil.example/c.webp", "Remote image"),
            ("https://github.com/x/z", "/assets/themes/../../etc/passwd", "Traversal"),
            ("https://github.com/x/..", "/assets/themes/d.webp", "Dot repo"),
            ("https://github.com/x/omarchy-ok-theme", "/assets/themes/ok.webp",
             "Ok &lt;img src=x&gt;")))
        self.assertEqual(len(themes), 1)
        self.assertEqual(themes[0].slug, "ok")
        self.assertNotIn("<", themes[0].name)

    def test_slug_matches_omarchy_theme_install(self):
        cases = {"https://github.com/a/omarchy-tokyo-night-theme": "tokyo-night",
                 "https://github.com/a/Omarchy-Retro-82-Theme.git": "retro-82",
                 "https://github.com/a/aetheria/": "aetheria",
                 "https://github.com/a/-dash": None,
                 "http://github.com/a/b": None}
        for repo, slug in cases.items():
            self.assertEqual(theme_catalog.slug_for_repo(repo), slug, repo)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._orig = (theme_catalog.cache_dir, theme_core.read_bounded_output)
        theme_catalog.cache_dir = lambda: self.root / "cache"
        self.calls = []

    def tearDown(self):
        theme_catalog.cache_dir, theme_core.read_bounded_output = self._orig
        self.tmp.cleanup()

    def stub(self, responses):
        def fake(argv, max_bytes=0, timeout=0, env=None):
            self.calls.append(argv)
            for key, value in responses.items():
                if key in argv[-1] or key == argv[0]:
                    return value
            return b"", False
        theme_core.read_bounded_output = fake

    def test_refuses_other_hosts(self):
        with self.assertRaises(ValueError):
            theme_catalog.https_get("https://evil.example/", 10)
        with self.assertRaises(ValueError):
            theme_catalog.https_get("https://omarchy.org.evil.example/", 10)

    def test_curl_is_https_only_without_redirects(self):
        self.stub({"/themes/": (b"x", True)})
        theme_catalog.https_get(theme_catalog.CATALOG_URL, 100)
        argv = self.calls[0]
        self.assertEqual(argv[0], "curl")
        self.assertIn("=https", argv)
        self.assertEqual(argv[argv.index("--max-redirs") + 1], "0")
        self.assertEqual(argv[-2:], ["--", theme_catalog.CATALOG_URL])

    def test_catalog_is_cached_and_survives_a_failed_refresh(self):
        html = page(("https://github.com/a/omarchy-ash-theme", "/assets/themes/ash.webp", "Ash")).encode()
        self.stub({"/themes/": (html, True)})
        themes, _ = theme_catalog.load_catalog()
        self.assertEqual([t.slug for t in themes], ["ash"])
        self.stub({})  # offline
        themes, _ = theme_catalog.load_catalog(force=True)
        self.assertEqual([t.slug for t in themes], ["ash"])
        self.assertEqual(self.calls[-1][0], "curl")

    def test_poisoned_cache_entries_are_dropped(self):
        cache = self.root / "cache" / "catalog.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({"fetched_at": time.time(), "themes": [
            {"name": "Bad", "repo": "https://evil.example/a/b", "slug": "b", "preview": "b.webp"},
            {"name": "Ok", "repo": "https://github.com/a/ok", "slug": "ok", "preview": "ok.webp"}]}))
        self.stub({})
        themes, _ = theme_catalog.load_catalog()
        self.assertEqual([t.slug for t in themes], ["ok"])

    def test_preview_must_be_image_data(self):
        entry = theme_catalog.CatalogTheme("Ash", "https://github.com/a/ash", "ash", "ash.webp")
        self.stub({"ash.webp": (b"<html>not an image</html>", True)})
        self.assertIsNone(theme_catalog.fetch_preview(entry))
        self.stub({"ash.webp": (b"RIFF\x00\x00\x00\x00WEBPVP8 ", True)})
        path = theme_catalog.fetch_preview(entry)
        self.assertTrue(path.is_file())


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.roots = theme_core.Roots(root / "themes", root / "system", root / "bg", root / "state")
        self._orig = theme_core.read_bounded_output
        self.calls = []

        def fake(argv, max_bytes=0, timeout=0, env=None):
            self.calls.append((argv, env))
            if argv[0] == "git":
                Path(argv[-1]).mkdir(parents=True)
            return b"", True
        theme_core.read_bounded_output = fake
        self.entry = theme_catalog.CatalogTheme("Ash", "https://github.com/a/omarchy-ash-theme", "ash", "ash.webp")

    def tearDown(self):
        theme_core.read_bounded_output = self._orig
        self.tmp.cleanup()

    def test_clone_argv_checks_url_and_disables_prompts(self):
        dest = theme_catalog.install(self.entry, self.roots)
        self.assertEqual(dest, self.roots.user_themes / "ash")
        self.assertEqual(self.calls[0][0], ["omarchy-git-url-check", self.entry.repo])
        argv, env = self.calls[1]
        self.assertEqual(argv[:2], ["git", "clone"])
        self.assertEqual(argv[-3:], ["--", self.entry.repo, str(dest)])
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_never_replaces_an_installed_theme(self):
        (self.roots.user_themes / "ash").mkdir(parents=True)
        (self.roots.user_themes / "ash" / "mine.txt").write_text("keep")
        with self.assertRaises(FileExistsError):
            theme_catalog.install(self.entry, self.roots)
        self.assertTrue((self.roots.user_themes / "ash" / "mine.txt").exists())
        self.assertEqual(self.calls, [])

    def test_mismatched_slug_refused(self):
        bad = theme_catalog.CatalogTheme("X", "https://github.com/a/omarchy-ash-theme", "other", "x.webp")
        with self.assertRaises(ValueError):
            theme_catalog.install(bad, self.roots)

    def test_failed_clone_cleans_up(self):
        def failing(argv, max_bytes=0, timeout=0, env=None):
            if argv[0] == "git":
                Path(argv[-1]).mkdir(parents=True)
                return b"", False
            return b"", True
        theme_core.read_bounded_output = failing
        with self.assertRaises(OSError):
            theme_catalog.install(self.entry, self.roots)
        self.assertFalse((self.roots.user_themes / "ash").exists())

    def test_browser_only_opens_github_repos_or_gallery(self):
        self.assertFalse(theme_catalog.open_in_browser("file:///etc/passwd"))
        self.assertFalse(theme_catalog.open_in_browser("https://evil.example/"))


if __name__ == "__main__":
    unittest.main()
