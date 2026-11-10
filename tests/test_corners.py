"""The Corners setting: Hyprland's rounding, the choices and where it is set."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corners  # noqa: E402


class CornersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_hyprctl(self):
        self.assertEqual(corners.parse_rounding('{"option": "decoration:rounding", "int": 8, "set": true}'), 8)
        self.assertEqual(corners.parse_rounding('{"int": 400}'), corners.MAX_RADIUS)
        self.assertEqual(corners.parse_rounding('{"int": -3}'), 0)
        for bad in ("", "not json", '{"value": 8}', '{"int": "8"}', '{"int": true}', "[1]"):
            self.assertIsNone(corners.parse_rounding(bad), bad)

    def test_radius_per_choice(self):
        self.assertEqual(corners.radius("windows", 8), 8)
        self.assertEqual(corners.radius("windows", None), 0)
        self.assertEqual(corners.radius("rounded", 0), corners.ROUNDED)
        self.assertEqual(corners.radius("square", 8), 0)
        self.assertEqual(corners.radius("bogus", 6), 6)  # Unknown values read as the default.
        self.assertEqual(corners.radius("slight", 0), corners.SLIGHT)
        self.assertEqual(corners.radius("slight", 20), 6)
        self.assertEqual(corners.CHOICES, ("windows", "slight", "rounded", "square"))

    def test_control_sizes_follow_one_radius(self):
        """Cards = radius, buttons and fields at most 8, check boxes and badges at most 4,
        pills square with Square corners (as in AI Playground)."""
        for radius, control, small, pill in ((0, 0, 0, 0), (2, 2, 2, 999), (6, 6, 4, 999), (12, 8, 4, 999),
                                             (24, 8, 4, 999)):
            self.assertEqual((corners.control(radius), corners.small(radius), corners.pill(radius)),
                             (control, small, pill), radius)

    def test_rounded_path_is_a_plain_rectangle_when_square(self):
        calls = []

        class Recorder:
            def __getattr__(self, name):
                return lambda *args: calls.append((name, args))
        corners.rounded_path(Recorder(), 1, 2, 30, 20, 0)
        self.assertEqual(calls, [("rectangle", (1, 2, 30, 20))])
        calls.clear()
        corners.rounded_path(Recorder(), 0, 0, 10, 4, 12)         # never more than half the side
        self.assertEqual([c[0] for c in calls].count("arc"), 4)
        self.assertTrue(all(c[1][2] == 2 for c in calls if c[0] == "arc"))

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path

    def test_source_user_wins_over_theme(self):
        theme = self.write("theme.lua", "hl.config({ decoration = { rounding = 0 } })\n")
        user = self.write("user.lua", "hl.config({\n  decoration = {\n    rounding = 8,\n  },\n})\n")
        self.assertEqual(corners.rounding_source(theme, user), "user")

    def test_commented_example_does_not_count(self):
        theme = self.write("theme.lua", "local x = 1\n")
        user = self.write("user.lua", "-- hl.config({\n--   decoration = {\n--     rounding = 8,\n")
        self.assertEqual(corners.rounding_source(theme, user), "default")
        theme = self.write("theme2.lua", "decoration = { rounding = 4, -- round\n gradient_rounding = 2 }\n")
        self.assertEqual(corners.rounding_source(theme, user), "theme")
        self.assertEqual(corners.lua_rounding(theme), 4)

    def test_other_rounding_keys_do_not_count(self):
        theme = self.write("t.lua", "group = { groupbar = { gradient_rounding = 3, rounding_power = 2 } }\n")
        self.assertIsNone(corners.lua_rounding(theme))

    def test_missing_symlinked_or_huge_files(self):
        self.assertIsNone(corners.lua_rounding(self.dir / "missing.lua"))
        real = self.write("real.lua", "rounding = 5\n")
        link = self.dir / "link.lua"
        link.symlink_to(real)
        self.assertIsNone(corners.lua_rounding(link))
        huge = self.write("huge.lua", "rounding = 5\n" + "-" * (corners.MAX_LUA_BYTES + 10))
        self.assertIsNone(corners.lua_rounding(huge))


if __name__ == "__main__":
    unittest.main()
