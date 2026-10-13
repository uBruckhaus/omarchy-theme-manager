"""Tests for theme_custom: palettes, creating and editing custom themes,
previews and the theme font."""
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import theme_core
import theme_custom
import i18n  # noqa: E402

i18n._lang = "en"  # the assertions below read English texts
from images import png

REAL_SET_FONT = theme_custom.set_font

RISTRETTO = """mode = "dark"
accent = "#f38d70"
selection = "#403e41"
muted = "#72696a"
background = "#2c2525"
foreground = "#e6d9db"
red = "#fd6883"
yellow = "#f9cc6c"
green = "#adda78"
cyan = "#85dacc"
blue = "#f38d70"
magenta = "#a8a9eb"
hyprland_active_border = "rgba(f38d70ff) rgba(a8a9ebff) 45deg"
hairline = "#3a4360"
"""


class CustomTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.roots = theme_core.Roots(
            user_themes=root / "themes-user", system_themes=root / "themes-system",
            user_backgrounds=root / "backgrounds", state_current=root / "state")
        for d in (self.roots.user_themes, self.roots.system_themes, self.roots.user_backgrounds,
                  self.roots.state_current):
            d.mkdir(parents=True)
        self.patch(theme_core, "default_roots", lambda: self.roots)
        self.patch(theme_core, "settings_file", lambda: root / "config" / "settings.json")
        self.patch(theme_core, "hidden_wallpapers_file", lambda: root / "config" / "hidden-wallpapers.json")
        self.font = "Mono A"
        self.fonts_set = []
        self.patch(theme_custom, "list_fonts", lambda: ["Mono A", "Mono B"])
        self.patch(theme_custom, "current_font", lambda: self.font)

        def set_font(font):
            self.fonts_set.append(font)
            self.font = font
            return True
        self.patch(theme_custom, "set_font", set_font)

    def patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def base_theme(self, slug="ristretto", wallpapers=("a.png", "b.png")):
        folder = self.roots.system_themes / slug
        (folder / "backgrounds").mkdir(parents=True)
        (folder / "colors.toml").write_text(RISTRETTO)
        (folder / "icons.theme").write_text("Yaru-red\n")
        (folder / "hyprland.lua").write_text("os.execute('evil')\n")
        (folder / "alacritty.toml").write_text("[terminal.shell]\nprogram = 'evil'\n")
        for name in wallpapers:
            (folder / "backgrounds" / name).write_bytes(png())
        return next(t for t in theme_core.discover_themes() if t.slug == slug)


class PaletteTests(CustomTestCase):
    def test_parse_fills_every_known_key_and_keeps_extras(self):
        palette = theme_custom.parse_colors(RISTRETTO)
        self.assertEqual(set(palette.colors), set(theme_custom.KNOWN_KEYS))
        self.assertEqual(palette.colors["accent"], "#f38d70")
        self.assertEqual(palette.extras["hyprland_active_border"], "rgba(f38d70ff) rgba(a8a9ebff) 45deg")
        self.assertEqual(palette.editable_extras(), ["hairline"])

    def test_bad_values_are_dropped(self):
        palette = theme_custom.parse_colors('accent = "red"\nevil = "$(rm -rf ~)"\nbackground = "#12345"\n')
        self.assertEqual(palette.colors["accent"], theme_custom.DEFAULTS["accent"])
        self.assertNotIn("evil", palette.extras)
        with self.assertRaises(ValueError):
            theme_custom.parse_colors("not toml = = =")

    def test_changing_a_main_color_moves_its_shades_unless_edited(self):
        palette = theme_custom.parse_colors(RISTRETTO)
        palette.set("bright_red", "#ff0000", edited={"bright_red"})
        palette.set("background", "#000000", edited={"bright_red"})
        self.assertNotEqual(palette.colors["dark_background"], "#2c2525")
        palette.set("red", "#00ff00", edited={"bright_red"})
        self.assertEqual(palette.colors["bright_red"], "#ff0000")
        with self.assertRaises(ValueError):
            palette.set("accent", "blue")
        with self.assertRaises(KeyError):
            palette.set("hyprland_active_border", "#000000")   # a gradient is not editable

    def test_mode_follows_the_background(self):
        palette = theme_custom.parse_colors(RISTRETTO)
        self.assertEqual(palette.mode, "dark")
        palette.set("background", "#f8f8f8")
        self.assertEqual(palette.mode, "light")

    def test_rendered_file_is_valid_toml(self):
        palette = theme_custom.parse_colors(RISTRETTO)
        data = tomllib.loads(theme_custom.render_colors(palette, "Mine", "Ristretto"))
        self.assertEqual(data["mode"], "dark")
        self.assertEqual(data["hairline"], "#3a4360")
        for key in theme_custom.KNOWN_KEYS:
            self.assertRegex(data[key], theme_custom.HEX_RE)

    def test_slugs(self):
        self.assertEqual(theme_custom.slug_for("  My Ristretto! "), "my-ristretto")
        self.assertEqual(theme_custom.slug_for("../../etc"), "etc")
        with self.assertRaises(ValueError):
            theme_custom.slug_for("!!!")


class CreateTests(CustomTestCase):
    def test_create_copies_only_colors_icons_and_wallpapers(self):
        base = self.base_theme()
        palette = theme_custom.load_palette(base)
        palette.set("accent", "#40c0ff")
        folder = theme_custom.create_theme(base, "My Ristretto", palette, "Mono B")
        self.assertEqual(folder, self.roots.user_themes / "my-ristretto")
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["backgrounds", "colors.toml", "icons.theme", "preview.png", "theme-manager.json"])
        self.assertEqual(sorted(p.name for p in (folder / "backgrounds").iterdir()), ["a.png", "b.png"])
        self.assertEqual(tomllib.loads((folder / "colors.toml").read_text())["accent"], "#40c0ff")
        self.assertEqual(json.loads((folder / "theme-manager.json").read_text())["font"], "Mono B")
        self.assertEqual((folder / "preview.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        theme = next(t for t in theme_core.discover_themes() if t.slug == "my-ristretto")
        self.assertTrue(theme_custom.is_custom(theme))
        self.assertEqual(theme_custom.font_of(theme), "Mono B")

    def test_hidden_and_oversized_wallpapers_are_left_out(self):
        base = self.base_theme(wallpapers=("a.png", "b.png", "bomb.png"))
        (base.path / "backgrounds" / "bomb.png").write_bytes(png(60000, 60000))
        theme_core.save_hidden_wallpapers({"ristretto": {"b.png"}})
        folder = theme_custom.create_theme(base, "Mine", theme_custom.load_palette(base), None)
        self.assertEqual([p.name for p in (folder / "backgrounds").iterdir()], ["a.png"])

    def test_without_wallpapers(self):
        base = self.base_theme()
        folder = theme_custom.create_theme(base, "Mine", theme_custom.load_palette(base), None, wallpapers=False)
        self.assertFalse((folder / "backgrounds").exists())

    def test_existing_names_are_refused_and_nothing_is_written(self):
        base = self.base_theme()
        with self.assertRaises(FileExistsError):
            theme_custom.create_theme(base, "Ristretto", theme_custom.load_palette(base), None)
        (self.roots.user_themes / "taken").mkdir()
        (self.roots.user_themes / "taken" / "keep").write_text("x")
        with self.assertRaises(FileExistsError):
            theme_custom.create_theme(base, "Taken", theme_custom.load_palette(base), None)
        self.assertEqual(list((self.roots.user_themes / "taken").iterdir()), [self.roots.user_themes / "taken" / "keep"])
        with self.assertRaises(ValueError):
            theme_custom.create_theme(base, "Mine", theme_custom.load_palette(base), "Mono; rm -rf ~")

    def test_suggested_name_avoids_taken_ones(self):
        base = self.base_theme()
        self.assertEqual(theme_custom.suggested_name("ristretto"), "My Ristretto")
        theme_custom.create_theme(base, "My Ristretto", theme_custom.load_palette(base), None)
        self.assertEqual(theme_custom.suggested_name("ristretto"), "My Ristretto 2")


class EditTests(CustomTestCase):
    def test_update_saves_colors_font_and_preview(self):
        base = self.base_theme()
        theme_custom.create_theme(base, "Mine", theme_custom.load_palette(base), None)
        theme = next(t for t in theme_core.discover_themes() if t.slug == "mine")
        palette = theme_custom.load_palette(theme)
        palette.set("background", "#000000")
        theme_custom.update_theme(theme, palette, "Mono A")
        self.assertEqual(tomllib.loads((theme.path / "colors.toml").read_text())["background"], "#000000")
        self.assertEqual(theme_custom.font_of(theme), "Mono A")
        self.assertEqual(json.loads((theme.path / "theme-manager.json").read_text())["base"], "ristretto")

    def test_only_custom_themes_can_be_edited(self):
        base = self.base_theme()
        with self.assertRaises(PermissionError):
            theme_custom.update_theme(base, theme_custom.load_palette(base), None)
        other = self.roots.user_themes / "from-git"
        (other / ".git").mkdir(parents=True)
        (other / "theme-manager.json").write_text('{"base": "x"}')
        theme = next(t for t in theme_core.discover_themes() if t.slug == "from-git")
        self.assertFalse(theme_custom.is_custom(theme))
        with self.assertRaises(PermissionError):
            theme_custom.update_theme(theme, theme_custom.load_palette(theme), None)


class RenameTests(CustomTestCase):
    def setUp(self):
        super().setUp()
        import theme_auto
        self.auto = theme_auto
        self.patch(theme_auto, "state_file", lambda: Path(self.tmp.name) / "cache" / "state.json")
        self.patch(theme_core, "hidden_file", lambda: Path(self.tmp.name) / "config" / "hidden.json")

    def test_rename_moves_folder_wallpapers_and_settings(self):
        base = self.base_theme()
        theme_custom.create_theme(base, "Old", theme_custom.load_palette(base), None)
        added = self.roots.user_backgrounds / "old"
        added.mkdir()
        (added / "x.png").write_bytes(png())
        theme_core.save_hidden({"old", "other"})
        theme_core.save_hidden_wallpapers({"old": {"a.png"}})
        self.auto.set_favorite("old", True)
        self.auto.record_recent("old", when=5.0)
        self.auto.set_workspace_wallpaper("old", 2, "a.png")
        self.auto.save_schedule(day="old", night="ristretto")
        old = next(t for t in theme_core.discover_themes() if t.slug == "old")
        self.assertEqual(theme_custom.rename_theme(old, "Brand New"), "brand-new")
        self.assertFalse((self.roots.user_themes / "old").exists())
        self.assertTrue((self.roots.user_themes / "brand-new" / "theme-manager.json").is_file())
        self.assertTrue((self.roots.user_backgrounds / "brand-new" / "x.png").is_file())
        self.assertEqual(theme_core.load_hidden(), {"brand-new", "other"})
        self.assertEqual(theme_core.load_hidden_wallpapers(), {"brand-new": {"a.png"}})
        self.assertEqual(self.auto.load_favorites(), ["brand-new"])
        self.assertEqual(self.auto.load_recent(), {"brand-new": 5.0})
        self.assertEqual(self.auto.load_workspace_map(), {"brand-new": {2: "a.png"}})
        self.assertEqual((self.auto.schedule()["day"], self.auto.schedule()["night"]), ("brand-new", "ristretto"))

    def test_rename_refuses_other_themes_and_taken_names(self):
        base = self.base_theme()
        with self.assertRaises(PermissionError):
            theme_custom.rename_theme(base, "Mine")
        theme_custom.create_theme(base, "Old", theme_custom.load_palette(base), None)
        old = next(t for t in theme_core.discover_themes() if t.slug == "old")
        with self.assertRaises(FileExistsError):
            theme_custom.rename_theme(old, "Ristretto")
        (self.roots.user_backgrounds / "free").mkdir()
        with self.assertRaises(FileExistsError):   # a wallpaper folder with that name already exists
            theme_custom.rename_theme(old, "Free")
        self.assertTrue((self.roots.user_themes / "old").is_dir())
        self.assertEqual(theme_custom.rename_theme(old, "old"), "old")


class FontTests(CustomTestCase):
    def make_custom(self, font):
        base = self.base_theme() if not (self.roots.system_themes / "ristretto").exists() else \
            next(t for t in theme_core.discover_themes() if t.slug == "ristretto")
        theme_custom.create_theme(base, f"with {font or 'none'}", theme_custom.load_palette(base), font)

    def test_theme_font_and_back_to_your_own(self):
        self.make_custom("Mono B")
        self.assertEqual(theme_custom.apply_theme_font("with-mono-b"), "Mono B")
        self.assertEqual(self.font, "Mono B")
        self.assertEqual(theme_custom.apply_theme_font("with-mono-b"), None)   # already set
        self.assertEqual(theme_custom.apply_theme_font("ristretto"), "Mono A")  # yours again
        self.assertEqual(self.fonts_set, ["Mono B", "Mono A"])
        self.assertIsNone(theme_core._load_settings(theme_core.settings_file()).get("own_font"))

    def test_themes_without_font_leave_it_alone(self):
        self.make_custom(None)
        self.assertIsNone(theme_custom.apply_theme_font("with-none"))
        self.assertIsNone(theme_custom.apply_theme_font("ristretto"))
        self.assertEqual(self.fonts_set, [])

    def test_font_from_a_tampered_marker_is_ignored(self):
        self.make_custom("Mono B")
        marker = self.roots.user_themes / "with-mono-b" / "theme-manager.json"
        marker.write_text(json.dumps({"base": "ristretto", "font": "x\"; rm -rf ~ #"}))
        self.assertIsNone(theme_custom.apply_theme_font("with-mono-b"))
        self.assertEqual(self.fonts_set, [])

    def test_set_font_only_passes_listed_fonts(self):
        calls = []
        self.patch(theme_core, "read_bounded_output", lambda argv, **kw: calls.append(argv) or (b"", True))
        self.patch(theme_custom, "list_fonts", lambda: ["Mono A"])
        self.assertFalse(REAL_SET_FONT("Not Listed"))
        self.assertFalse(REAL_SET_FONT("Mono A; rm"))
        self.assertTrue(REAL_SET_FONT("Mono A"))
        self.assertEqual(calls, [["omarchy-font-set", "Mono A"]])


def picture(path, *bands):
    """A test image of horizontal color bands: (hex, share) pairs."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    width, height = 200, 100
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    y = 0
    for color, share in bands:
        rows = round(height * share)
        if rows:
            image.new_subpixbuf(0, y, width, min(rows, height - y)).fill(int(color[1:], 16) << 8 | 0xFF)
        y += rows
    image.savev(str(path), "png", [], [])
    return path


class WallpaperPaletteTests(CustomTestCase):
    def test_dark_picture_gives_a_dark_theme_with_its_vivid_accent(self):
        path = picture(Path(self.tmp.name) / "night.png", ("#0b1530", 0.8), ("#ff8800", 0.2))
        palette = theme_custom.palette_from_image(path)
        self.assertEqual(palette.mode, "dark")
        h, l, s = theme_custom._hls(palette.colors["background"])
        self.assertLess(l, 0.2)
        self.assertAlmostEqual(h * 360, 223, delta=15)            # the picture's blue
        ah, _al, _as = theme_custom._hls(palette.colors["accent"])
        self.assertAlmostEqual(ah * 360, 32, delta=15)            # the orange stands out
        self.assertEqual(palette.colors["orange"][:3], palette.colors["orange"][:3])
        # Every terminal color is readable on the background.
        for key in ("red", "yellow", "green", "cyan", "blue", "magenta", "orange"):
            self.assertGreater(theme_custom.luminance(palette.colors[key]) -
                               theme_custom.luminance(palette.colors["background"]), 0.3, key)

    def test_bright_picture_gives_a_light_theme(self):
        path = picture(Path(self.tmp.name) / "snow.png", ("#f4f1ea", 0.9), ("#2a6fdb", 0.1))
        palette = theme_custom.palette_from_image(path)
        self.assertEqual(palette.mode, "light")
        self.assertLess(theme_custom.luminance(palette.colors["foreground"]), 0.3)

    def test_grey_picture_still_gives_every_color(self):
        path = picture(Path(self.tmp.name) / "grey.png", ("#555555", 1.0))
        palette = theme_custom.palette_from_image(path)
        for key in theme_custom.KNOWN_KEYS:
            self.assertRegex(palette.colors[key], theme_custom.HEX_RE)
        self.assertTrue(tomllib.loads(theme_custom.render_colors(palette, "G", "x")))

    def test_oversized_picture_is_refused(self):
        path = Path(self.tmp.name) / "bomb.png"
        path.write_bytes(png(60000, 60000))
        with self.assertRaises(ValueError):
            theme_custom.palette_from_image(path)

    def test_names_from_files(self):
        self.assertEqual(theme_custom.name_from_file("2-coffee-beans.jpg"), "Coffee Beans")
        self.assertEqual(theme_custom.name_from_file("3-industrial-moon.jpg"), "Industrial Moon")
        for meaningless in ("wallhaven-xlqrwv.jpg", "wallhaven-abc123.png", "IMG_0937_1_original_4k.jpg",
                            "pasted-image.png", "dropped-image.png", "___.png", "DSC01234.jpg"):
            self.assertIsNone(theme_custom.name_from_file(meaningless), meaningless)
        self.assertEqual(theme_custom.unique_name(theme_custom.DEFAULT_NAME), "New Custom Theme")
        (self.roots.user_themes / "new-custom-theme").mkdir()
        self.assertEqual(theme_custom.unique_name(theme_custom.DEFAULT_NAME), "New Custom Theme 2")

    def test_create_with_only_the_chosen_wallpaper(self):
        base = self.base_theme(wallpapers=("a.png", "b.png"))
        palette = theme_custom.palette_from_image(base.path / "backgrounds" / "b.png")
        folder = theme_custom.create_theme(base, "From B", palette, None, wallpapers=False,
                                           extra_wallpapers=[base.path / "backgrounds" / "b.png"])
        self.assertEqual([p.name for p in (folder / "backgrounds").iterdir()], ["b.png"])


if __name__ == "__main__":
    unittest.main()
