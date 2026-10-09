# Theme Manager

One list of every Omarchy theme — user and system — in a single popup. Click the paint-brush bar icon to open it: apply a theme, hide it from the list, delete your own themes, and manage the wallpapers that ship with a theme or that you add to it.

## Features

- **All themes in one place.** User themes (`~/.config/omarchy/themes/`) and system themes (`/usr/share/omarchy/themes/`) in a single scrollable list, each with its accent colour as a swatch and a badge for current/system themes.
- **Apply a theme** with one click.
- **Hide themes** with the eye button on a row. Hidden themes stay installed and reappear when you switch on **Show hidden**. Hidden state is stored in `~/.config/omarchy/theme-manager/hidden.json`.
- **Delete user themes** after an explicit confirmation. Only themes in your own themes directory can be deleted; system themes are root-owned and read-only here — hide them instead.
- **Wallpapers per theme.** Open a theme's wallpaper page to see what it ships. Add your own by **dragging an image file into the popup** or by **pasting an image copied from the web**. Delete added wallpapers with the ✕ button; wallpapers that come with a theme are read-only. Added wallpapers are stored in `~/.config/omarchy/backgrounds/<theme>/`.

## Install

```sh
omarchy plugin add https://github.com/uBruckhaus/omarchy-theme-manager.git --enable
```

A paint-brush button appears in the bar. Click it to open or close the popup. The popup stays running in the background between opens so it appears instantly; it holds no data of its own.

## Use

1. Click **Theme Manager** in the bar. The list shows every installed theme; your current theme is marked.
2. **Click a theme row** to apply it.
3. **Eye button** on a row hides or unhides that theme; the **Show hidden** switch reveals everything that is hidden.
4. **⋮ menu** on a row opens the wallpaper page, hides the theme, or deletes it (user themes only, with confirmation).
5. On the **wallpaper page**, click a wallpaper to set it. Drag an image file into the popup or paste one from the clipboard (Ctrl+V) to add a wallpaper to that theme. ✕ removes a wallpaper you added.

## Remove

```sh
omarchy plugin remove ubrickhaus.theme-manager
```

This removes the bar button and the popup. Your themes and the wallpapers you added are **not** touched. To also drop Theme Manager's own state:

```sh
rm -rf ~/.config/omarchy/theme-manager
```

## Privacy and capabilities

- No network access, no `sudo`, no bundled binaries.
- Runs `omarchy theme …` and `hyprctl` (read-only) as argv subprocesses without a shell; every subprocess read is size- and time-bounded.
- Reads and writes only under `~/.config/omarchy/` (themes, backgrounds, `theme-manager/` state). System theme files are never modified.
- Clipboard paste reads only image data via `wl-paste`.
- PID file lives in `$XDG_RUNTIME_DIR` (or a private 0700 fallback); signals are only sent after full process identity checks (uid, interpreter, exact script path, kernel start time).

## Files

| Path | Purpose |
| --- | --- |
| `manifest.json` | Plugin metadata (bar-widget) |
| `BarWidget.qml` | Bar button; runs `theme-toggle` |
| `theme-toggle` | Singleton toggle: start the popup, or signal it to show/hide |
| `theme_popup.py` | GTK4 layer-shell popup (UI, drag-and-drop, paste) |
| `theme_core.py` | Theme discovery, apply, hidden state, wallpaper operations |
| `palette.py` | Shared colour helpers |
| `pidfile.py` | Private PID-file handling |
| `tests/` | Unit tests (core logic + GTK view layer) |
