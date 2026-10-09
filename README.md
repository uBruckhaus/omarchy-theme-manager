# Theme Manager

One list of every Omarchy theme — user and system — in a single popup. Click the paint-brush bar icon to open it: apply a theme, hide it from the list, delete your own themes, and manage the wallpapers that ship with a theme or that you add to it.

## Features

- **All themes in one place.** User themes (`~/.config/omarchy/themes/`) and system themes (`/usr/share/omarchy/themes/`) in a single scrollable list, each with a large preview and badges for current/system/hidden themes.
- **Apply a theme** from its ⋮ menu.
- **Hide themes** from the list via the ⋮ menu. Hidden themes stay installed; the eye button in the header shows them again (dimmed) so you can unhide them. Hidden state is stored in `~/.config/omarchy/theme-manager/hidden.json`. Applying a hidden theme, from Theme Manager or with `omarchy theme set`, unhides it automatically. The theme you are using always stays in Omarchy's picker, even while hidden; otherwise a picker with every theme hidden would be empty, and Omarchy's picker shows its last full list instead of an empty one.
- **Optionally hidden in Omarchy's pickers, too.** Off by default. Switch on **Settings (⚙) → Also hide in Omarchy's pickers** and hidden themes also leave Omarchy's own theme switcher (Menu → Style → Theme), hidden wallpapers its background picker. Unhiding brings them back. See *Syncing with Omarchy's pickers* below.
- **Delete user themes** after an explicit confirmation. Wallpapers you added to the theme are kept. The active theme cannot be deleted: apply another theme first. Only themes in your own themes directory can be deleted; system themes are root-owned and read-only here — hide them instead.
- **Wallpapers per theme.** Open a theme's wallpaper page to see what it ships. Add your own by **dragging an image file into the popup** or by **pasting an image copied from the web**. Each wallpaper's ☰ menu can set it as the background (current theme), **hide** it, or **delete** it. Added wallpapers are stored in `~/.config/omarchy/backgrounds/<theme>/`.
- **Hidden wallpapers** (with the Omarchy sync on) leave Omarchy's background picker, *next background* and the background chosen on theme switch. The files stay where they are, so hiding works for every wallpaper. If the hidden one is on the desktop, the next visible one takes its place. The last visible wallpaper of a theme cannot be hidden.
- **Search wallpapers on the web.** On a theme's wallpaper page, **Search the web** (Ctrl+F) searches [wallhaven.cc](https://wallhaven.cc) for whatever you type: safe-for-work landscape images of at least 1920×1080, with more results as you scroll. A result's ☰ menu adds it to the theme, adds it and sets it as the background (current theme), or opens its page in your default browser.
- **Import themes from [omarchy.org](https://omarchy.org/themes/)** (download button in the header, or Ctrl+I): the whole gallery with previews and search. A theme's ☰ menu installs it, installs and applies it, or opens its GitHub page in your default browser (a hint appears if none is set).

## Deleting versus hiding wallpapers

| Wallpaper | Delete | Hide |
| --- | --- | --- |
| Added by you (`~/.config/omarchy/backgrounds/<theme>/`) | yes | yes (moved to `.hidden/` inside that folder) |
| In a theme you created yourself | yes | yes |
| In a theme installed from git | no: `omarchy theme update` runs `git pull`, which a deleted file would break | yes |
| In a system theme (`/usr/share/omarchy/themes/`, owned by root) | no: needs root, and the next Omarchy update restores it | yes |

Theme Manager never asks for root. Shipped wallpapers are hidden by name in `~/.config/omarchy/theme-manager/hidden-wallpapers.json` and removed only from Omarchy's per-user copy of the current theme (`~/.local/state/omarchy/current/theme/backgrounds/`), which `omarchy theme set` rebuilds on every switch. With the Omarchy sync on, a theme-set hook (`~/.config/omarchy/hooks/theme-set.d/90-theme-manager`) re-applies the hidden list after each switch.

## Install

```sh
omarchy plugin add https://github.com/uBruckhaus/omarchy-theme-manager.git --enable
```

A paint-brush button appears in the bar. Click it to open or close the popup. The popup stays running in the background between opens so it appears instantly; it holds no data of its own.

## Use

1. Click **Theme Manager** in the bar. The list shows every installed theme; your current theme is marked.
2. **Click a theme row** to open its wallpaper page.
3. **⋮ menu** on a row applies the theme, opens its wallpapers, hides or unhides it, or deletes it (user themes only, with confirmation).
4. **Eye button** in the header shows or hides the themes you have hidden.
5. On the **wallpaper page**, drag image files into the popup or paste one from the clipboard (Ctrl+V) to add wallpapers to that theme. ✕ removes a wallpaper you added.
6. **Keyboard**, as in Simple Launcher: ↑/↓, Page Up/Down, Home/End select a theme; **Enter** opens its wallpapers; **→** (or the Menu key) opens its action menu, **←** or **Backspace** closes it. On the wallpaper and import pages the arrows move between tiles, **Enter** opens a tile's menu, and **←** (from the left column), **Backspace** or **Esc** go back. On the import page, typing searches. **Type to filter**, as in Simple Launcher: typing on the theme list or a wallpaper page filters it by name (the title shows the filter, Backspace removes a character, Esc clears it). **Ctrl+I** opens the import page, **Ctrl+F** on a wallpaper page opens the web search (Enter searches, ↓ moves into the results); Esc on the list closes the popup.

## Syncing with Omarchy's pickers (opt-in)

Off until you switch it on in **Settings (⚙)**. Without it, hiding applies to Theme Manager's own lists only and Theme Manager writes nothing into Omarchy's files. When you switch it on:

Omarchy's theme picker shows one preview per theme from its cache in `~/.cache/omarchy/theme-selector/previews/`. Theme Manager keeps hidden themes out of it:

- Hiding a theme removes its preview link from that cache (only the link; no theme file is touched). Unhiding asks Omarchy to rebuild the cache.
- Theme Manager points the menu's **Theme** entry at its `theme-switcher` wrapper by adding one marked line to `~/.config/omarchy/extensions/omarchy-menu.jsonc`. The wrapper runs Omarchy's unchanged `omarchy-theme-switcher` and drops hidden themes again whenever Omarchy rebuilds its cache. If you already override `style.theme` yourself, your entry is left alone.

Deleting or installing a theme changes the theme folders, which makes Omarchy rebuild that cache. Theme Manager rebuilds it right away through the wrapper, and the theme-set hook does the same after `omarchy theme install`, so Omarchy's own rebuild finds nothing to do and hidden themes stay hidden.

- It installs the hook `~/.config/omarchy/hooks/theme-set.d/90-theme-manager`, which re-applies hidden wallpapers and the theme filter after every `omarchy theme set`.

**Switching it off** removes exactly the marked menu line and the hook again, and lets Omarchy rebuild its pickers with everything. `omarchy plugin remove` does not run plugin code, so switch the sync off *before* removing the plugin (or delete the two lines below `// theme-manager:` in that file and the hook by hand).

## Remove

```sh
omarchy plugin remove ubruckhaus.theme-manager
```

This removes the bar button and the popup. Your themes and the wallpapers you added are **not** touched. If you had switched on the Omarchy sync, switch it off first (Settings ⚙), or afterwards remove the marked lines in `~/.config/omarchy/extensions/omarchy-menu.jsonc` and `~/.config/omarchy/hooks/theme-set.d/90-theme-manager`. To also drop Theme Manager's own state and caches:

```sh
rm -rf ~/.config/omarchy/theme-manager ~/.cache/theme-manager
```

## Privacy and capabilities

- Never asks for root or administrator rights; no bundled binaries.
- Network access only for **Import themes**, which contacts only `https://omarchy.org` (the gallery page and its preview images), and **Search the web**, which contacts only Wallhaven: the search API on `wallhaven.cc`, thumbnails on `th.wallhaven.cc` and images on `w.wallhaven.cc`. Every address in an API reply must match its exact expected shape, the safe-for-work filter is fixed, and your search text is only a URL-encoded query parameter. Both go through `curl` with HTTPS only, no redirects, and a size and time limit on every request. The theme list is cached for a day in `~/.cache/theme-manager/`, and at most 400 wallpaper thumbnails in `~/.cache/theme-manager/wallhaven/`. Installing runs `git clone --depth 1` of the theme's `https://github.com/<owner>/<repo>` link after Omarchy's own `omarchy-git-url-check`, without prompts, into `~/.config/omarchy/themes/<name>`. An existing theme is never replaced. Theme pages open with `omarchy-launch-browser`.
- Runs `omarchy theme …` and `hyprctl` (read-only) as argv subprocesses without a shell; every subprocess read is size- and time-bounded.
- Reads and writes only under `~/.config/omarchy/` (themes, backgrounds, `theme-manager/` state, and, only with the opt-in Omarchy sync on, one marked line in `extensions/omarchy-menu.jsonc` and the hook `hooks/theme-set.d/90-theme-manager`) and `~/.cache/theme-manager/`. It removes preview links in `~/.cache/omarchy/theme-selector/previews/` and hidden wallpapers from Omarchy's copy in `~/.local/state/omarchy/current/theme/backgrounds/`. System theme files are never modified.
- Clipboard paste reads only image data via `wl-paste`.
- PID file lives in `$XDG_RUNTIME_DIR` (or a private 0700 fallback); signals are only sent after full process identity checks (uid, interpreter, exact script path, kernel start time).

## Files

| Path | Purpose |
| --- | --- |
| `manifest.json` | Plugin metadata (bar-widget) |
| `BarWidget.qml` | Bar button; runs `theme-toggle` |
| `theme-toggle` | Singleton toggle: start the popup, or signal it to show/hide |
| `theme_popup.py` | GTK4 layer-shell popup (UI, drag-and-drop, paste) |
| `theme-switcher` | Omarchy's theme switcher without hidden themes |
| `shim/omarchy-menu-images` | Drops hidden themes from the picker cache, then runs Omarchy's picker |
| `switcher_sync.py` | Prune helper used by the shim |
| `theme_catalog.py` | omarchy.org gallery: bounded fetch, parsing, previews, git install |
| `wallpaper_search.py` | wallhaven.cc search: bounded API calls, strict reply checks, thumbnails, downloads |
| `theme_core.py` | Theme discovery, apply, hidden state, wallpaper operations |
| `palette.py` | Shared colour helpers |
| `pidfile.py` | Private PID-file handling |
| `tests/` | Unit tests (core logic + GTK view layer) |
