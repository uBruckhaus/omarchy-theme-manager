"""Header order persistence and pointer insertion math."""
import unittest

import header_order


class HeaderOrderTests(unittest.TestCase):
    def test_normalize_unknown_duplicate_and_missing_ids(self):
        self.assertEqual(header_order.normalize(["info", "future", "info", "hidden"]),
                         ["info", "hidden", "import", "presets", "ask"])

    def test_normalize_wrong_types(self):
        for value in (None, "info", {"info": True}, 7, [None, 3, ["hidden"]]):
            self.assertEqual(header_order.normalize(value), list(header_order.DEFAULT))

    def test_insertion_index_uses_button_midpoints(self):
        bounds = [(4, 28), (34, 28), (64, 28), (94, 28)]   # 4 visible (Ask AI may be hidden)
        self.assertEqual([header_order.insertion_index(bounds, x) for x in
                          (0, 17, 18, 47, 48, 122)], [0, 0, 1, 1, 2, 4])

    def test_move_to_visible_slot_and_no_op(self):
        order = ["import", "hidden", "presets", "info"]
        self.assertEqual(header_order.move_to_visible_slot(order, "hidden", order, 2), order)
        self.assertEqual(header_order.move_to_visible_slot(order, "hidden", order, 4),
                         ["import", "presets", "info", "hidden"])
        self.assertEqual(header_order.move_to_visible_slot(order, "hidden", ["import", "hidden"], 0),
                         ["hidden", "import", "presets", "info"])
        self.assertEqual(header_order.move_to_visible_slot(order, "hidden", ["import", "hidden"], 2), order)

    def test_keyboard_move_skips_hidden_neighbours(self):
        # Alt+→ uses slot index + 2, Alt+← index − 1 over the visible buttons.
        order = ["import", "hidden", "presets", "info"]
        visible = ["import", "hidden"]   # presets and info hidden on a sub-page
        self.assertEqual(header_order.move_to_visible_slot(order, "import", visible, 2),
                         ["hidden", "import", "presets", "info"])
        self.assertEqual(header_order.move_to_visible_slot(order, "hidden", visible, 0),
                         ["hidden", "import", "presets", "info"])
        visible = ["import", "info"]     # a hidden button between two visible ones
        order = ["import", "presets", "info", "hidden"]
        self.assertEqual(header_order.move_to_visible_slot(order, "import", visible, 2),
                         ["presets", "info", "import", "hidden"])


if __name__ == "__main__":
    unittest.main()
