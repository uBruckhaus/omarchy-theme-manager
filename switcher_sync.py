#!/usr/bin/env python3
"""Theme Manager - keep hidden themes and wallpapers out of Omarchy's pickers.

- `switcher_sync.py prune <preview-dir>`: called by `shim/omarchy-menu-images`;
  drops hidden themes from the theme switcher's own preview cache only.
- `switcher_sync.py wallpapers`: called by the theme-set hook; removes hidden
  wallpapers from Omarchy's copy of the theme just applied and rebuilds the
  theme picker cache through the filtering wrapper.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import theme_core  # noqa: E402
import theme_custom  # noqa: E402


def main(argv: list[str]) -> int:
    if not theme_core.sync_enabled():
        return 0  # the user has not switched on syncing with Omarchy's pickers
    if argv[1:] == ["wallpapers"]:
        # Called by the theme-set hook after `omarchy theme set`, before
        # Omarchy preloads its theme picker: hide wallpapers in the theme just
        # applied, and bring the picker cache up to date through the filtering
        # wrapper (e.g. after `omarchy theme install` added a theme folder).
        # A theme applied from anywhere (e.g. `omarchy theme set` in a
        # terminal) is wanted again: take it off the hidden list.
        theme_core.unhide_theme(theme_core.current_theme())
        theme_core.sync_current_theme()
        # A theme made with Theme Manager brings its font (and other themes
        # bring yours back), also when applied from Omarchy's own picker.
        theme_custom.apply_theme_font(theme_core.current_theme())
        theme_core.prune_switcher_previews(theme_core.load_hidden())
        theme_core.refresh_switcher_cache(Path(__file__).resolve().parent / "theme-switcher")
        return 0
    if len(argv) != 3 or argv[1] != "prune":
        return 2
    target = Path(argv[2])
    expected = theme_core.switcher_cache_dir() / "previews"
    try:
        if target.resolve() != expected.resolve():
            return 0
    except OSError:
        return 0
    theme_core.prune_switcher_previews(theme_core.load_hidden(), expected)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
