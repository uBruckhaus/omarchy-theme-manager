"""Tests for wallpaper_search: the wallhaven.cc search, its replies and downloads."""
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_core
import wallpaper_search as ws

from images import jpeg

JPEG = jpeg()


def item(wid="abc123", **over):
    base = {"id": wid, "url": f"https://wallhaven.cc/w/{wid}",
            "path": f"https://w.wallhaven.cc/full/{wid[:2]}/wallhaven-{wid}.jpg",
            "thumbs": {"large": f"https://th.wallhaven.cc/lg/{wid[:2]}/{wid}.jpg"},
            "resolution": "2560x1440", "file_size": 1000, "purity": "sfw"}
    base.update(over)
    return base


def reply(*items, last_page=3):
    return json.dumps({"data": list(items), "meta": {"last_page": last_page}}).encode()


class ParseTests(unittest.TestCase):
    def test_valid_result(self):
        results, last = ws.parse_results(reply(item()))
        self.assertEqual(last, 3)
        self.assertEqual(results[0].file_name, "wallhaven-abc123.jpg")

    def test_drops_anything_off_shape(self):
        bad = [
            item(path="https://evil.example/full/ab/wallhaven-abc123.jpg"),
            item(path="https://w.wallhaven.cc/full/ab/wallhaven-other1.jpg"),       # id mismatch
            item(thumbs={"large": "https://th.wallhaven.cc/lg/ab/../../x.jpg"}),
            item(url="http://wallhaven.cc/w/abc123"),
            item(wid="../etc"),
            item(purity="nsfw"),
            item(resolution="<img src=x>"),
            "not a dict",
        ]
        results, _ = ws.parse_results(reply(*bad))
        self.assertEqual(results, [])
        self.assertEqual(ws.parse_results(b"<html>")[0], [])

    def test_query_is_only_a_parameter(self):
        url = ws.search_url("forest&purity=111\r\n<b>", page=2)
        self.assertTrue(url.startswith(ws.API + "?"))
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(params["purity"], ["100"])                 # the user cannot lift SFW
        self.assertEqual(params["page"], ["2"])
        self.assertNotIn("<", params["q"][0])
        self.assertEqual(len(ws.clean_query("x" * 500)), ws.MAX_QUERY)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.roots = theme_core.Roots(root / "themes", root / "system", root / "bg", root / "state")
        self._orig = (theme_core.fetch_https, ws.cache_dir)
        ws.cache_dir = lambda: root / "cache"
        self.fetched = []

    def tearDown(self):
        theme_core.fetch_https, ws.cache_dir = self._orig
        self.tmp.cleanup()

    def serve(self, answers):
        def fake(url, max_bytes, timeout=15.0):
            self.fetched.append((url, max_bytes))
            return next((v for k, v in answers.items() if k in url), None)
        theme_core.fetch_https = fake

    def test_search_contacts_only_the_api(self):
        self.serve({"api/v1/search": reply(item())})
        results, _ = ws.search("misty forest")
        self.assertEqual(len(results), 1)
        self.assertTrue(self.fetched[0][0].startswith("https://wallhaven.cc/api/v1/search?"))
        self.assertEqual(ws.search("   "), ([], 0))

    def test_download_saves_into_theme_folder_once(self):
        result = ws.parse_results(reply(item()))[0][0]
        self.serve({"w.wallhaven.cc": JPEG})
        path = ws.download(self.roots, "vault", result)
        self.assertEqual(path, self.roots.user_backgrounds / "vault" / "wallhaven-abc123.jpg")
        self.assertEqual(self.fetched[0][1], theme_core.MAX_BACKGROUND_BYTES)
        with self.assertRaises(FileExistsError):
            ws.download(self.roots, "vault", result)

    def test_download_rejects_non_images_and_huge_files(self):
        result = ws.parse_results(reply(item()))[0][0]
        self.serve({"w.wallhaven.cc": b"<html>login</html>"})
        with self.assertRaises(OSError):
            ws.download(self.roots, "vault", result)
        big = ws.parse_results(reply(item(file_size=theme_core.MAX_BACKGROUND_BYTES + 1)))[0][0]
        with self.assertRaises(ValueError):
            ws.download(self.roots, "vault", big)

    def test_thumbs_cached_and_checked(self):
        result = ws.parse_results(reply(item()))[0][0]
        self.serve({"th.wallhaven.cc": b"not an image"})
        self.assertIsNone(ws.fetch_thumb(result))
        self.serve({"th.wallhaven.cc": JPEG})
        path = ws.fetch_thumb(result)
        self.assertTrue(path.is_file())
        self.serve({})
        self.fetched.clear()
        self.assertEqual(ws.fetch_thumb(result), path)   # from cache, no request
        self.assertEqual(self.fetched, [])

    def test_fetch_https_refuses_plain_http(self):
        theme_core.fetch_https = self._orig[0]
        with self.assertRaises(ValueError):
            theme_core.fetch_https("http://wallhaven.cc/", 10)


if __name__ == "__main__":
    unittest.main()
