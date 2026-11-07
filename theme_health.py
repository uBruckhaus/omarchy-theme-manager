"""Read-only sync diagnostics and an explicit repair action."""
from __future__ import annotations

from pathlib import Path

import switcher_sync
import theme_core


def inspect() -> dict:
    enabled = theme_core.sync_enabled()
    menu = theme_core.menu_extension_file()
    hook = theme_core.theme_set_hook_file()
    try:
        menu_text = menu.read_text()[:1024 * 1024]
    except OSError:
        menu_text = ""
    try:
        hook_text = hook.read_text()[:64 * 1024] if not hook.is_symlink() else ""
    except OSError:
        hook_text = ""
    hidden = theme_core.load_hidden() - {theme_core.current_theme()}
    previews = theme_core.switcher_cache_dir() / "previews"
    leaked = []
    try:
        for entry in previews.iterdir():
            if entry.is_symlink() and entry.stem in hidden:
                leaked.append(entry.stem)
    except OSError:
        pass
    return {
        "enabled": enabled,
        "menu": theme_core.MENU_MARKER in menu_text and "theme-switcher" in menu_text
                and f'"{theme_core.menu_entry_id(installed=True)}"' in menu_text,
        "menu_custom": any(f'"{entry_id}"' in menu_text for entry_id in
                           (theme_core.MENU_ENTRY_ID, theme_core.NEW_MENU_ENTRY_ID))
                       and theme_core.MENU_MARKER not in menu_text,
        "hook": theme_core.HOOK_MARKER in hook_text and "switcher_sync.py" in hook_text,
        "leaked": sorted(set(leaked)),
        "hidden": len(hidden),
    }


def repair() -> dict:
    """Reinstall owned integration files and refresh both picker caches."""
    if not theme_core.sync_enabled():
        return inspect()
    plugin = Path(__file__).resolve().parent
    theme_core.ensure_menu_override(plugin / "theme-switcher")
    theme_core.ensure_theme_set_hook(plugin / "switcher_sync.py")
    switcher_sync.main(["switcher_sync.py", "startup"])
    return inspect()
