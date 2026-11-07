"""The Workspaces and Apps tabs of the Presets page: everything the Workspace
Presets plugin's popup did, as GTK (window limits with overflow, layout
presets with previews, Native/Floating/Scrolling, App Presets with restore,
conflicts and autostart).

Workspace cards show the layout as a small picture; hovering one fades it
over to that workspace's theme and wallpaper. The rules live in
workspace_state.py, the desktop work in workspace_tiling.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Graphene, Gtk, Pango  # noqa: E402

import theme_auto  # noqa: E402
import theme_core  # noqa: E402
import theme_custom  # noqa: E402
import workspace_state as state  # noqa: E402
import workspace_tiling as tiling  # noqa: E402
from i18n import _  # noqa: E402

CARD_W, CARD_H = 104, 58          # a workspace card's picture (16:9)
CHOICE_W, CHOICE_H = 52, 30       # a layout choice's picture
CARD_MAX_W, CHOICE_MAX_W = 340, 180   # how large they grow in a wide popup
WIDE_FROM = 900                   # popup width from which the tabs use two columns
GROUPS = ((1, 5), (6, 10))        # workspace groups; anything above 10 joins the second
HOVER_W, HOVER_H = 480, 270       # the large preview (sized to the popup when shown)
GAP = 0.035


def preset_label(preset: str) -> str:
    return {"side-by-side": _("Side-by-Side"), "stacked": _("Stacked"), "master-left": _("Master Left"),
            "master-right": _("Master Right"), "columns": _("Columns"), "grid": _("2×2 Grid"),
            "none": _("Native"), "floating": _("Floating"), "scrolling": _("Scrolling")}.get(preset, preset)


def preset_rects(preset: str, cap: int, split=None) -> list[tuple[float, float, float, float]]:
    """Windows of a layout as (x, y, w, h) in 0…1, as the bar icon of Workspace Presets drew them.
    split: the master's share (Master Left/Right) or the left/top window's
    (Side-by-Side, Stacked); None = even."""
    g = GAP
    half = (1 - 3 * g) / 2
    first = (1 - 3 * g) * (split if split is not None else 0.5)   # left/top part or the master
    second = (1 - 3 * g) - first

    def column(x, w, n):
        h = (1 - (n + 1) * g) / n
        return [(x, g + i * (h + g), w, h) for i in range(n)]

    if cap <= 1 and preset not in ("none", "floating", "scrolling"):
        return [(g, g, 1 - 2 * g, 1 - 2 * g)]
    if preset == "side-by-side":
        return [(g, g, first, 1 - 2 * g), (2 * g + first, g, second, 1 - 2 * g)]
    if preset == "stacked":
        return [(g, g, 1 - 2 * g, first), (g, 2 * g + first, 1 - 2 * g, second)]
    if preset in ("master-left", "master-right"):
        rest = column(0, second, max(1, cap - 1))
        if preset == "master-left":
            return [(g, g, first, 1 - 2 * g)] + [(2 * g + first, y, w, h) for _x, y, w, h in rest]
        return [(g, y, w, h) for _x, y, w, h in rest] + [(2 * g + second, g, first, 1 - 2 * g)]
    if preset == "columns":
        n = max(2, cap)
        if n == 3 and split is not None:    # a centred master: the middle column's share
            part = 1 - 4 * g
            mid = part * min(split, state.COLUMNS_SPLIT_MAX)
            side = (part - mid) / 2
            return [(g, g, side, 1 - 2 * g), (2 * g + side, g, mid, 1 - 2 * g),
                    (3 * g + side + mid, g, side, 1 - 2 * g)]
        w = (1 - (n + 1) * g) / n
        return [(g + i * (w + g), g, w, 1 - 2 * g) for i in range(n)]
    if preset == "grid":
        return [(g, g, half, half), (2 * g + half, g, half, half),
                (g, 2 * g + half, half, half), (2 * g + half, 2 * g + half, half, half)]
    if preset == "floating":
        return [(0.08, 0.12, 0.5, 0.5), (0.3, 0.28, 0.5, 0.5), (0.5, 0.08, 0.42, 0.42)]
    if preset == "scrolling":
        return [(g, g, 0.42, 1 - 2 * g), (2 * g + 0.42, g, 0.42, 1 - 2 * g), (3 * g + 0.84, g, 0.42, 1 - 2 * g)]
    # Native: Hyprland's own dwindle split.
    return [(g, g, half, 1 - 2 * g), (2 * g + half, g, half, half), (2 * g + half, 2 * g + half, half, half)]


SAVED_INSET = 0.04


def window_rects(windows: list, bounds=None) -> list[tuple[float, float, float, float]]:
    """Saved windows (App Preset) scaled into 0…1 by their bounds (or by
    the given (x, y, width, height), so a drag keeps the picture's scale)."""
    boxes = [(w.get("at") or [0, 0], w.get("size") or [800, 600]) for w in windows]
    if not boxes:
        return []
    if bounds is not None:
        min_x, min_y, width, height = bounds
    else:
        min_x = min(at[0] for at, _s in boxes)
        min_y = min(at[1] for at, _s in boxes)
        width = max(1, max(at[0] + s[0] for at, s in boxes) - min_x)
        height = max(1, max(at[1] + s[1] for at, s in boxes) - min_y)
    inset = SAVED_INSET
    scale = 1 - 2 * inset
    return [(inset + (at[0] - min_x) / width * scale, inset + (at[1] - min_y) / height * scale,
             max(0.02, s[0] / width * scale - 0.015), max(0.02, s[1] / height * scale - 0.015))
            for at, s in boxes]


def rgba(hex_color: str, alpha: float = 1.0):
    color = Gdk.RGBA()
    if not color.parse(hex_color or "#888888"):
        color.parse("#888888")
    return color.red, color.green, color.blue, alpha


def rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class LayoutPicture(Gtk.DrawingArea):
    """A mini monitor with the windows of a layout (or of an App Preset)."""

    def __init__(self, width, height, colors, rects=(), dashed=False, scroll_cut=False, deck=False):
        super().__init__()
        self.set_content_width(width)
        self.set_content_height(height)
        self.colors = colors
        self.rects = list(rects)
        self.dashed = dashed
        self.scroll_cut = scroll_cut
        self.deck = deck            # All Workspaces: a stack of screens, never one workspace
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, width, height):
        fg, accent = self.colors.get("foreground", "#cdd6f4"), self.colors.get("accent", "#89b4fa")
        if self.deck:
            # Two screens behind (up right), the picture in front a little smaller.
            off = max(3.0, height * 0.08)
            for step, alpha in ((2, 0.35), (1, 0.6)):
                rounded(cr, 0.5 + step * off, 0.5, width - 1 - 2 * off, height - 1 - 2 * off, 5)
                cr.set_source_rgba(*rgba(accent, 0.10 * alpha))
                cr.fill_preserve()
                cr.set_source_rgba(*rgba(accent, alpha))
                cr.set_line_width(1)
                cr.stroke()
            cr.translate(0, 2 * off)
            width, height = width - 2 * off, height - 2 * off
        rounded(cr, 0.5, 0.5, width - 1, height - 1, 5)
        cr.set_source_rgba(*rgba(fg, 0.06))
        cr.fill_preserve()
        cr.set_source_rgba(*rgba(fg, 0.25))
        cr.set_line_width(1)
        cr.stroke()
        cr.save()
        rounded(cr, 1, 1, width - 2, height - 2, 4)
        cr.clip()
        for x, y, w, h in self.rects:
            rounded(cr, x * width, y * height, w * width, h * height, 3)
            if self.dashed:
                cr.set_source_rgba(*rgba(fg, 0.45))
                cr.set_dash([3, 2])
                cr.stroke()
                cr.set_dash([])
            else:
                cr.set_source_rgba(*rgba(accent, 0.45))
                cr.fill_preserve()
                cr.set_source_rgba(*rgba(accent, 0.9))
                cr.set_line_width(1)
                cr.stroke()
        cr.restore()


class WorkspacesPageMixin:
    """Mixed into ThemeManager; uses its _async, decoder, theme and ask_confirm."""

    # --- state ---------------------------------------------------------------------------

    def ws_init(self):
        self.ws_store = {}
        self.ws_modes = {}
        self.ws_existing = []
        self.ws_selected = 0
        self.ws_conflict = None
        self.ws_failed = {}
        self.ws_message = ""
        self.ws_busy = False
        self.ws_wp_on_bar = False
        self.ws_group = 0
        self.ws_wallpapers = {}

    def ws_colors(self):
        colors = dict(getattr(getattr(self, "theme", None), "colors", {}) or {})
        return colors

    def ws_reload(self, then=None):
        """Settings, Omarchy's modes (Super + L) and the open workspaces, off the main loop."""
        def work():
            store = tiling.load_store()
            try:
                import json
                existing = [w["id"] for w in json.loads(tiling.hypr("workspaces", "-j"))
                            if type(w.get("id")) is int and w["id"] > 0]
            except Exception:  # noqa: BLE001 - no Hyprland: just the default ten
                existing = []
            ids = state.workspace_list(store, existing)
            try:
                modes = tiling.omarchy_workspace_modes(ids)
            except Exception:  # noqa: BLE001
                modes = {}
            return store, {str(k): v for k, v in modes.items()}, existing, tiling.wp_on_bar()

        def done(result, error):
            if error is None:
                self.ws_store, self.ws_modes, self.ws_existing, self.ws_wp_on_bar = result
                if getattr(self, "ws_banner", None) is not None:
                    self.ws_fill_banner(self.ws_banner)
            self.ws_render()
            if then:
                then()
        self._async(work, done)

    def ws_change(self, rule, *args, then=None):
        """Apply one rule and save under the lock every other writer takes
        (a restore, a theme preset, the bar's helper), off the main loop so a
        long restore holding it never freezes the popup; then show it and do
        the desktop part."""
        autostart = self.ws_autostart_changes(rule, *args)

        def work():
            with tiling.runtime_lock():
                store = tiling.load_store()
                action = rule(store, *args)
                saved = tiling.save_store(store)
                # Under the same lock, right after this save: copies are written in the
                # order of the saves, and keep only what the view and the store agree on.
                failed = []
                for ws, shown in autostart:
                    real = state.layout_for(saved, ws)
                    agreed = tiling.agreed_layout(shown, real) if shown is not None else None
                    try:
                        kept = tiling.approve_autostart(ws, agreed is not None and real is not None
                                                        and real.get("autostart") is True, agreed, locked=True)
                    except (OSError, RuntimeError, ValueError):
                        kept = False
                    if shown is not None and not kept:
                        failed.append(ws)
                return saved, action, failed

        def done(result, error):
            if error is not None:
                theme_core.notify(_("Could not save the setting"), str(error))
                return
            self.ws_store, action, failed = result
            for ws in failed:
                self.ws_message = _("Autostart could not be confirmed for workspace {0}.").format(ws)
            self.ws_render()
            if action:
                self.ws_run(action, then)
            elif then:
                then()
        self._async(work, done)

    def ws_autostart_changes(self, rule, *args):
        """What a change means for autostart, worked out on the layouts the page
        shows (never on a store reread meanwhile): [(ws, layout to keep as the
        private copy, or None to forget it)]. A workspace whose autostart is on
        and whose apps or windows change (or whose autostart is switched on)
        gets the result the user sees as its copy."""
        shown = json.loads(json.dumps(self.ws_store or {}))
        expected = json.loads(json.dumps(shown))
        try:
            rule(expected, *args)
        except Exception:  # noqa: BLE001 - the real change reports its own error
            return []
        before = shown.get("workspaceLayouts") if isinstance(shown.get("workspaceLayouts"), dict) else {}
        after = expected.get("workspaceLayouts") if isinstance(expected.get("workspaceLayouts"), dict) else {}
        changes = []
        for key in sorted(set(before) | set(after)):
            if not key.isdigit():
                continue
            old, new = before.get(key), after.get(key)
            on_before = isinstance(old, dict) and old.get("autostart") is True
            on_after = isinstance(new, dict) and new.get("autostart") is True
            if on_after and (not on_before or any(old.get(k) != new.get(k) for k in ("apps", "windows", "saveApps"))):
                changes.append((int(key), new))
            elif on_before and not on_after:
                changes.append((int(key), None))
        return changes

    def ws_run(self, action, then=None):
        def work():
            kind = action[0]
            if kind == "apply":
                return tiling.apply_layout_preset(action[1], action[2])
            if kind == "remove":
                return tiling.apply_layout_preset(action[1], "none")
            if kind == "remove_all":
                return tiling.remove_all_presets()
            if kind == "apply_many":
                failed = []
                for ws, preset in action[1]:     # every workspace, failures reported together
                    try:
                        tiling.apply_layout_preset(ws, preset)
                    except Exception as error:  # noqa: BLE001 - shown in the status line
                        failed.append(f"{ws}: {error}")
                if failed:
                    raise RuntimeError("; ".join(failed))
                return {"status": "ok"}
            if kind == "tile":
                # An App Preset in use again: Omarchy's floating/scrolling mode
                # back to tiling (dwindle), as a tiling preset does.
                if tiling.omarchy_workspace_modes([action[1]]).get(action[1]) in tiling.FREE_PRESETS:
                    tiling.set_omarchy_mode(action[1], "dwindle")
                    # Checked, not assumed: a restore follows only a real switch.
                    if tiling.omarchy_workspace_modes([action[1]]).get(action[1]) in tiling.FREE_PRESETS:
                        raise RuntimeError(_("Workspace {0} still floats or scrolls.").format(action[1]))
                return {"status": "ok"}
            return tiling.reset_all_presets()

        def done(result, error):
            if error is not None:
                self.ws_message = str(error)
            elif action[0] in ("tile", "apply_many"):
                pass
            elif action[0] == "apply":
                self.ws_message = _("Applied {0} to Workspace {1}").format(preset_label(action[2]), action[1])
            elif action[0] == "remove":
                self.ws_message = _("Preset removed (Native layout) on Workspace {0}").format(action[1])
            elif action[0] == "remove_all":
                self.ws_message = _("All workspaces set to Native layout (no presets)")
            else:
                self.ws_message = _("All workspaces reset to default layout ({0})").format(
                    preset_label((result or {}).get("default_preset", "")))
            # The next step (a restore) only after the desktop part worked;
            # a failure stays in the status line and nothing more happens.
            ok = error is None and isinstance(result, dict) and result.get("status") == "ok"
            self.ws_reload(then if ok else None)
        self._async(work, done)

    # --- page ----------------------------------------------------------------------------

    def build_ws_tab(self, kind: str) -> Gtk.Widget:
        """kind: "workspaces" or "apps"."""
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_end(6)
        scroller.set_child(box)
        # Two groups of workspaces, as Workspace Presets had pages, so the
        # controls of the chosen one stay in view below the cards.
        groups = Gtk.Box(spacing=8)
        groups.add_css_class("tm-ws-groups")
        groups.set_homogeneous(True)            # as wide as the cards, two equal halves
        buttons = []
        for index, (first, last) in enumerate(GROUPS):
            button = Gtk.ToggleButton(label=_("Workspaces {0}–{1}").format(first, last), hexpand=True)
            button.add_css_class("tm-ws-group")
            if buttons:
                button.set_group(buttons[0])
            button.connect("toggled", lambda b, i=index: b.get_active() and self.ws_show_group(i))
            groups.append(button)
            buttons.append(button)
        setattr(self, f"ws_{kind}_groups", buttons)
        cards = Gtk.FlowBox()
        cards.set_selection_mode(Gtk.SelectionMode.SINGLE)
        cards.set_homogeneous(True)
        cards.set_column_spacing(6)
        cards.set_row_spacing(6)
        cards.set_max_children_per_line(6)
        cards.set_min_children_per_line(2)
        cards.set_activate_on_single_click(True)
        cards.set_valign(Gtk.Align.START)
        cards.connect("child-activated", lambda _f, child: self.ws_select(child.ws_id))
        cards.update_property([Gtk.AccessibleProperty.LABEL], [_("Workspaces")])
        # Narrow: cards above the controls. Wide (ws_set_width): cards on the
        # left, the chosen workspace's controls on the right.
        # Left: the cards, then the chosen workspace's controls in group cards.
        # Right (wide popup only): that workspace as it looks, large.
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.append(groups)
        left.append(cards)
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        left.append(controls)
        status = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        status.add_css_class("tm-hint")
        left.append(status)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.add_css_class("tm-group-card")
        right.set_valign(Gtk.Align.START)
        panel_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        panel_title.add_css_class("tm-ws-heading")
        right.append(panel_title)
        panel = LookAlike(HOVER_W, HOVER_H)
        right.append(panel)
        self.ws_panel_gestures(panel, kind)
        panel_caption = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        panel_caption.add_css_class("tm-hint")
        right.append(panel_caption)
        right.set_visible(False)
        body.append(left)
        body.append(right)
        box.append(body)
        setattr(self, f"ws_{kind}_panel", (right, panel_title, panel, panel_caption))
        self.ws_bodies = getattr(self, "ws_bodies", []) + [(body, left, right, cards)]
        setattr(self, f"ws_{kind}_parts", (None, cards, controls, status))
        self.ws_sizes = None
        self.ws_set_width(getattr(self, "card_w", 420))   # the popup may open wide
        return scroller

    def ws_set_width(self, card_w):
        """Lay the tabs out for the popup's width: two columns from WIDE_FROM
        on; card pictures and layout choices grow with their column."""
        wide = card_w >= WIDE_FROM
        cards_w = round(card_w * 0.56) if wide else card_w
        picture_w = max(CARD_W, min(CARD_MAX_W, (cards_w - 2 * 6) // 3 - 18))
        # Four choices per row must fit the controls' column: its width less
        # the gaps (3 × 6) and each button's padding and border (about 16).
        controls_w = cards_w - 40 if wide else card_w - 40      # inside a group card
        choice_w = max(CHOICE_W, min(CHOICE_MAX_W, round(picture_w * 0.55), (controls_w - 3 * 6) // 4 - 18))
        changed = (wide, picture_w, choice_w) != getattr(self, "ws_sizes", None)
        self.ws_sizes = (wide, picture_w, choice_w)
        panel_w = card_w - cards_w - 18 - 40
        self.ws_panel_size = (max(200, panel_w), round(max(200, panel_w) * 9 / 16))
        for body, left, right, cards in getattr(self, "ws_bodies", []):
            body.set_orientation(Gtk.Orientation.HORIZONTAL if wide else Gtk.Orientation.VERTICAL)
            body.set_spacing(18 if wide else 10)
            left.set_size_request(cards_w if wide else -1, -1)
            left.set_hexpand(not wide)
            right.set_hexpand(wide)
            right.set_visible(wide)
            cards.set_max_children_per_line(3 if wide else 6)
            cards.set_min_children_per_line(3 if wide else 2)
        if changed and getattr(self, "ws_store", None) is not None and getattr(self, "ws_bodies", None):
            self.ws_render()

    def ws_picture_size(self):
        _wide, picture_w, choice_w = getattr(self, "ws_sizes", (False, CARD_W, CHOICE_W))
        return (picture_w, round(picture_w * 9 / 16)), (choice_w, round(choice_w * 9 / 16))

    def ws_select(self, ws_id):
        self.ws_selected = ws_id
        self.ws_conflict = None
        self.ws_render()
        self.ws_focus_selected()

    def ws_focus_selected(self):
        """The cards are new after a render: keyboard focus goes back to the
        chosen one, so arrow keys and Enter keep working."""
        kind = "apps" if getattr(self, "presets_tab", "") == "apps" else "workspaces"
        parts = getattr(self, f"ws_{kind}_parts", None)
        if parts is None:
            return
        for child in parts[1].get_selected_children():
            child.grab_focus()

    def ws_render(self):
        # The large preview stays open over a change made in it (it is drawn
        # again below); the cards under it are rebuilt.
        reopen = getattr(self, "ws_hover_ws", None) \
            if self.ws_preview_open() and not getattr(self, "ws_sizes", (False,))[0] else None
        if reopen is None:
            self.ws_hover_hide()
        else:
            self.ws_hover_small = None      # that card picture is replaced
        self.ws_ghost_hide()   # and the dragged choice may be gone
        for kind in ("workspaces", "apps"):
            parts = getattr(self, f"ws_{kind}_parts", None)
            if parts is None:
                continue
            _banner, cards, controls, status = parts
            self.ws_fill_cards(cards, kind)
            clear(controls)
            if kind == "workspaces":
                self.ws_fill_layout_controls(Sections(controls))
            else:
                self.ws_fill_app_controls(Sections(controls))
            self.ws_show_panel(kind, self.ws_current(kind))
            status.set_label(self.ws_message)
            status.set_visible(bool(self.ws_message))
        if reopen is not None:
            rects, dashed = self.ws_card_rects(reopen, self.ws_tab_kind())
            self.ws_preview_show(reopen, None, rects, dashed)

    def build_ws_banner(self) -> Gtk.Widget:
        """The hand-over note (Save/Load Presets tab): shown while the
        Workspace Presets plugin is still on the bar."""
        banner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        banner.add_css_class("tm-ws-banner")
        banner.set_visible(False)
        self.ws_banner = banner
        return banner

    def ws_refresh_banner(self):
        def done(on_bar, error):
            self.ws_wp_on_bar = bool(on_bar) and error is None
            self.ws_fill_banner(self.ws_banner)
        self._async(tiling.wp_on_bar, done)

    def ws_fill_banner(self, banner):
        clear(banner)
        if not getattr(self, "ws_wp_on_bar", False):
            banner.set_visible(False)
            return
        banner.set_visible(True)
        text = Gtk.Label(xalign=0, wrap=True, max_width_chars=1, label=_(
            "The Workspace Presets plugin is on the bar and arranges the windows. "
            "Both plugins share its settings: changes here take effect there at once."))
        banner.append(text)

    # --- cards ---------------------------------------------------------------------------

    def ws_current(self, kind) -> int:
        """The chosen workspace; on the App Presets tab "All" means the group's first."""
        if kind == "apps" and self.ws_selected <= 0:
            return GROUPS[self.ws_group][0]
        return self.ws_selected

    def ws_group_of(self, ws) -> int:
        return 0 if ws <= GROUPS[0][1] else 1

    def ws_choose_current(self) -> bool:
        """The workspace you are on becomes the chosen one (and the preview
        shows it). True if Hyprland told which one it is."""
        current = theme_auto.active_workspace()
        if current is None:
            return False
        self.ws_selected = current
        self.ws_group = self.ws_group_of(current)
        self.ws_conflict = None
        return True

    def ws_show_group(self, index):
        if index == self.ws_group:
            return
        self.ws_group = index
        if self.ws_selected != 0 and self.ws_group_of(self.ws_selected) != index:
            self.ws_selected = GROUPS[index][0]
            self.ws_conflict = None
        self.ws_render()

    def ws_fill_cards(self, cards, kind):
        clear(cards)
        store = self.ws_store
        if self.ws_selected > 0:
            self.ws_group = self.ws_group_of(self.ws_selected)
        for index, button in enumerate(getattr(self, f"ws_{kind}_groups", [])):
            if button.get_active() != (index == self.ws_group):
                button.set_active(index == self.ws_group)
        ids = [ws for ws in state.workspace_list(store, self.ws_existing) if self.ws_group_of(ws) == self.ws_group]
        ids = ([0] if kind == "workspaces" else []) + ids
        for ws in ids:
            child = Gtk.FlowBoxChild()
            child.ws_id = ws
            child.add_css_class("tm-ws-card")
            if ws == 0:
                child.add_css_class("tm-ws-all")      # the defaults, not a workspace: framed apart
            child.set_child(self.ws_card(ws, kind))
            cards.append(child)
            if ws == self.ws_current(kind):
                cards.select_child(child)

    def ws_card_rects(self, ws, kind):
        store, modes = self.ws_store, self.ws_modes
        if ws > 0 and state.is_manual(store, ws):
            windows = state.saved_windows(store, ws)
            return window_rects(windows) if windows else [], not windows
        if kind == "apps":
            return [], True
        if ws == 0:
            cap = state.global_cap(store)
            return preset_rects(state.default_preset_for(store, cap), cap, state.split_for(store, 0)), False
        preset = state.preset_for(store, ws, modes)
        return preset_rects(preset, state.effective_cap(store, ws), state.split_for(store, ws)), preset == "none"

    def ws_card_summary(self, ws, kind):
        store, modes = self.ws_store, self.ws_modes
        if ws == 0:
            return _("Default: {0} windows").format(state.global_cap(store))
        if state.is_manual(store, ws):
            apps = state.saved_apps(store, ws)
            text = _("{0} apps saved").format(len(apps)) if apps else _("App Preset")
            if state.autostart_on(store, ws) and state.has_saved_layout(store, ws):
                text += _(" · Autostart on")
            return text
        if kind == "apps":
            return (_("App Preset") + " · " + _("not in use")) if state.has_saved_layout(store, ws) \
                else _("No App Preset")
        preset = state.preset_for(store, ws, modes)
        if preset in ("none", "floating", "scrolling"):
            return preset_label(preset)
        return _("Max {0}").format(state.effective_cap(store, ws)) + " · " + preset_label(preset)

    def ws_card(self, ws, kind) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(label=_("All Workspaces") if ws == 0 else _("Workspace {0}").format(ws),
                          xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        title.set_hexpand(True)
        title.add_css_class("tm-ws-title")
        if ws > 0 and ws == (theme_auto.active_workspace() or 0):
            title.add_css_class("tm-ws-current")
        box.append(title)
        rects, dashed = self.ws_card_rects(ws, kind)
        (card_w, card_h), _choice = self.ws_picture_size()
        layout = LayoutPicture(card_w, card_h, self.ws_colors(), rects, dashed, deck=ws == 0)
        if not rects:
            layout = Gtk.Overlay(child=layout)
            empty = Gtk.Label(label="—")
            empty.add_css_class("tm-hint")
            layout.add_overlay(empty)
        # A small preview button on the picture: the picture fades out and the
        # workspace opens large as it looks (wallpaper, theme colors, windows).
        frame = Gtk.Overlay(child=layout)
        zoom = Gtk.Button(icon_name="zoom-in-symbolic")
        zoom.add_css_class("flat")
        zoom.add_css_class("tm-ws-zoom")
        zoom.set_halign(Gtk.Align.END)
        zoom.set_valign(Gtk.Align.END)     # bottom-right corner of the picture
        zoom.set_focus_on_click(False)
        zoom.set_tooltip_text(_("Preview"))
        zoom.update_property([Gtk.AccessibleProperty.LABEL],
                             [_("Preview") + ": " + title.get_label()])
        zoom.connect("clicked", lambda *_args: self.ws_zoom(ws, kind, layout, rects, dashed))
        frame.add_overlay(zoom)
        box.append(frame)
        summary = Gtk.Label(label=self.ws_card_summary(ws, kind), xalign=0,
                            ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        summary.add_css_class("tm-hint")
        box.append(summary)
        box.set_tooltip_text(summary.get_label())
        box.update_property([Gtk.AccessibleProperty.LABEL],
                            [title.get_label() + ": " + summary.get_label()])
        return box

    def ws_zoom(self, ws, kind, small, rects, dashed):
        """Wide popup: the preview panel on the right shows it (and it is
        chosen); narrow: the large preview over the popup."""
        if getattr(self, "ws_sizes", (False,))[0]:
            self.ws_select(ws)
        else:
            # The large preview edits this workspace: it becomes the chosen one.
            if ws != getattr(self, "ws_selected", None):
                self.ws_selected = ws
                self.ws_render()
                rects, dashed = self.ws_card_rects(ws, kind)
                small = None
            self.ws_preview_show(ws, small, rects, dashed)

    # --- drag and drop on the preview (inside the popup: real Wayland drags
    # don't work while it has exclusive keyboard focus) -------------------------

    EDGE = 0.92            # a drag starting right of this share of the width moves the edge

    def ws_tab_kind(self):
        return "apps" if getattr(self, "presets_tab", "") == "apps" else "workspaces"

    def ws_panel_gestures(self, panel, fixed_kind):
        """fixed_kind None: the large preview over the narrow popup, shared by
        both tabs (it follows the open one)."""
        def K():
            return fixed_kind or self.ws_tab_kind()
        motion = Gtk.EventControllerMotion()

        def moved(_c, x, y):
            kind = K()
            hot = panel.minus_hit(x, y)
            if hot != panel.minus_hot:
                panel.minus_hot = hot
                panel.queue_draw()
            if hot:
                panel.set_cursor_from_name("pointer")
                return
            if (panel.plus_hit(x, y) or self.ws_saved_handle(panel, self.ws_current(kind), x, y) is not None) \
                    and (panel.split_hover is not None or panel.edge_hover):
                panel.split_hover, panel.edge_hover = None, False
                panel.queue_draw()
            if panel.plus_hit(x, y):
                panel.set_cursor_from_name("pointer")
                if not panel.plus_hot:
                    panel.plus_hot = True
                    panel.queue_draw()
                return
            if panel.plus_hot:
                panel.plus_hot = False
                panel.queue_draw()
            saved = self.ws_saved_handle(panel, self.ws_current(kind), x, y)
            if saved is not None:
                panel.set_cursor_from_name("nwse-resize" if saved[0] == "corner" else
                                           "col-resize" if saved[1] == 0 else "row-resize")
                return
            divider = self.ws_on_divider(panel, self.ws_current(kind), x, y)
            if divider is not None:
                panel.set_cursor_from_name("col-resize" if divider[0] == "x" else "row-resize")
                if panel.split_hover != divider[0]:
                    panel.split_hover = divider[0]
                    panel.queue_draw()
                return
            if panel.split_hover is not None:
                panel.split_hover = None
                panel.queue_draw()
            on_window = panel.hit(x, y) is not None
            gap = not on_window and x > (panel.get_width() or panel.get_content_width()) * self.EDGE \
                and not (self.ws_current(kind) > 0 and state.is_manual(self.ws_store, self.ws_current(kind)))
            panel.set_cursor_from_name("grab" if on_window else "ew-resize" if gap else None)
            if gap != panel.edge_hover:
                panel.edge_hover = gap
                panel.queue_draw()

        def left(_c):
            if panel.edge_hover or panel.split_hover is not None or panel.plus_hot or panel.minus_hot:
                panel.edge_hover, panel.split_hover, panel.plus_hot, panel.minus_hot = False, None, False, False
                panel.queue_draw()

        def pressed(_g, n_press, x, y):
            # Double-click on the divider: an even split again.
            ws = self.ws_current(K())
            if n_press == 2 and self.ws_on_divider(panel, ws, x, y) is not None:
                self.ws_set_split(ws, None)
        click = Gtk.GestureClick()
        click.connect("pressed", pressed)
        panel.add_controller(click)
        motion.connect("motion", moved)
        motion.connect("leave", left)
        panel.add_controller(motion)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda g, x, y: self.ws_panel_begin(panel, K(), x, y))
        drag.connect("drag-update", lambda g, dx, dy: self.ws_panel_update(g, panel, K(), dx, dy))
        drag.connect("drag-end", lambda g, dx, dy: self.ws_panel_end(panel, K(), dx, dy))
        drag.connect("cancel", lambda *_a: self.ws_drag_reset())     # GTK ended it (unmapped, grab lost)
        panel.add_controller(drag)
        # Keyboard: ←/→ choose a window, Shift+←/→ swap it with its neighbour,
        # + and − change the window limit.
        panel.set_focusable(True)
        panel.set_focus_on_click(False)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, keyval, _code, mods: self.ws_panel_key(panel, K(), keyval, mods))
        panel.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_a: (setattr(panel, "highlight", panel.highlight or 0), panel.queue_draw()))
        focus.connect("leave", lambda *_a: (setattr(panel, "highlight", None), panel.queue_draw()))
        panel.add_controller(focus)
        panel.update_property([Gtk.AccessibleProperty.LABEL], [_("Workspace preview")])
        panel.set_tooltip_text(_("Drag a window onto another to swap them, or onto a layout in the row that appears "
                                 "at the bottom · the master window over the others moves it to that side · out of the "
                                 "preview for one window fewer · the gap at the right edge sets the window limit. "
                                 "The line between the windows sets their sizes (double-click: even). "
                                 "Keys: ←/→, Shift+←/→ swap, ↑/↓ layout, [ and ] size, + and −.")
                              + "\n" + _("+ at the right edge: click for one window more, or drag it into the "
                                          "preview: where you drop it chooses the layout.")
                              + "\n" + _("− at the left edge: one window fewer; with one window, back to the default limit.")
                              + "\n" + _("App Preset: + adds an app (dropped on a window: it halves that one), "
                                          "the edge two windows share and a floating window's corner set their sizes."))

    def ws_tray_targets(self, ws, cap):
        """What a dragged window can be dropped on, besides another window:
        each layout for this limit (the current one marked) and one window
        fewer. None for an App Preset (its windows only swap)."""
        if cap <= 1:
            return ()
        current = self.ws_shown_preset(ws)
        order = ("master-left", "master-right", "columns", "grid", "side-by-side", "stacked")
        layouts = [p for p in order if p in state.TILING_BY_CAP.get(cap, ())]
        targets = [(p, cap, preset_label(p), p == current) for p in layouts]
        fewer = self.ws_preset_for_cap(ws, cap - 1)
        targets.append((fewer, cap - 1, _("− 1 window"), False))
        return tuple(targets)

    def ws_panel_cap(self, ws):
        return state.global_cap(self.ws_store) if ws == 0 else state.effective_cap(self.ws_store, ws)

    def ws_panel_begin(self, panel, kind, x, y):
        ws = self.ws_current(kind)
        manual = ws > 0 and state.is_manual(self.ws_store, ws)
        base = {"ws": ws, "manual": manual, "rects": list(panel.rects), "dashed": panel.dashed, "panel": panel}
        if panel.minus_hit(x, y):
            self.ws_drag = dict(base, mode="minus", moved=False)
            return
        if panel.plus_hit(x, y):
            cap = self.ws_panel_cap(ws)
            self.ws_drag = dict(base, mode="add", x=x, y=y, cap=cap, preset=None, moved=False, inside=False,
                                target=None)
            return
        saved = self.ws_saved_handle(panel, ws, x, y)
        if saved is not None:
            windows = state.saved_windows(self.ws_store, ws)
            self.ws_drag = dict(base, mode="saved", handle=saved, x=x, y=y, windows=windows,
                                bounds=state.saved_bounds(windows), result=None, moved=False)
            return
        divider = self.ws_on_divider(panel, ws, x, y)
        if divider is not None:
            axis, _pos, preset, split = divider
            self.ws_drag = dict(base, mode="split", axis=axis, preset=preset, start=split, value=split, x0=x, y0=y)
            return
        on_window = panel.hit(x, y) is not None
        if x > (panel.get_width() or panel.get_content_width()) * self.EDGE and not manual and not on_window:
            cap = self.ws_panel_cap(ws)
            self.ws_drag = dict(base, mode="edge", x=x, cap=cap, new=cap)
        else:
            index = panel.hit(x, y)
            self.ws_drag = dict(base, mode="window", **{"from": index}, x=x, y=y, to=None, preset=None, out=False,
                                tray=() if manual else self.ws_tray_targets(ws, self.ws_panel_cap(ws))) \
                if index is not None else None

    def ws_panel_preview(self, panel, rects, badge, dashed=False):
        panel.rects, panel.badge, panel.dashed = rects, badge, dashed
        panel.queue_draw()

    def ws_panel_update(self, gesture, panel, kind, dx, dy):
        drag = getattr(self, "ws_drag", None)
        if not drag:
            return
        if abs(dx) + abs(dy) > 6:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            drag["moved"] = True
        else:
            return
        store, ws = self.ws_store, drag["ws"]
        if drag["mode"] == "minus":
            return                      # a click only
        if drag["mode"] == "add":
            self.ws_add_update(panel, drag, drag["x"] + dx, drag["y"] + dy)
            return
        if drag["mode"] == "saved":
            self.ws_saved_update(panel, drag, drag["x"] + dx, drag["y"] + dy)
            return
        if drag["mode"] == "split":
            value = self.ws_split_at(panel, drag["axis"], drag["preset"], drag["x0"] + dx, drag["y0"] + dy)
            drag["value"] = value
            panel.split_line = drag["axis"]
            self.ws_panel_preview(panel, preset_rects(drag["preset"], self.ws_panel_cap(ws), value),
                                  self.ws_split_label(drag["preset"], value))
            return
        if drag["mode"] == "edge":
            step = max(1, (panel.get_width() or panel.get_content_width()) / 4)
            new = max(1, min(4, drag["cap"] + round(dx / step)))
            drag["new"] = new
            preset = self.ws_preset_for_cap(ws, new)
            panel.edge = True
            self.ws_panel_preview(panel, preset_rects(preset, new), self.ws_result_label(new, preset),
                                  preset == "none")
            return
        width, height = panel.get_width() or panel.get_content_width(), panel.get_height() or panel.get_content_height()
        px, py = drag["x"] + dx, drag["y"] + dy
        drag["to"], drag["preset"], drag["out"] = None, None, False
        panel.tray, panel.tray_hot, panel.source = drag["tray"], None, None
        outside = px < 0 or py < 0 or px > width or py > height
        cap = self.ws_panel_cap(ws)
        hot = None if outside else panel.tray_hit(px, py)
        if (outside or (hot is not None and drag["tray"][hot][1] < cap)) and not drag["manual"] and cap > 1:
            # Out of the preview, or onto "− 1 window": one window fewer.
            drag["out"] = True
            preset = self.ws_preset_for_cap(ws, cap - 1)
            panel.highlight, panel.drop, panel.tray_hot = None, False, hot
            self.ws_panel_preview(panel, preset_rects(preset, cap - 1), self.ws_result_label(cap - 1, preset))
            return
        if hot is not None:
            preset, _cap, _label, current = drag["tray"][hot]
            drag["preset"] = None if current else preset
            panel.highlight, panel.drop, panel.tray_hot = None, not current, hot
            self.ws_panel_preview(panel, preset_rects(preset, cap),
                                  _("Already {0}").format(preset_label(preset)) if current else
                                  self.ws_result_label(cap, preset))
            return
        flipped = None if drag["manual"] else self.ws_master_flip(ws, cap, drag["from"], px, width)
        if flipped is not None:
            drag["preset"] = flipped
            panel.highlight, panel.drop = None, True
            self.ws_panel_preview(panel, preset_rects(flipped, cap), self.ws_result_label(cap, flipped))
            return
        target = panel.hit(px, py, drag["rects"])       # the windows as they were at the start
        drag["to"] = target if target != drag["from"] else None
        panel.drop, panel.source = False, drag["from"]
        self.ws_panel_preview(panel, drag["rects"], _("Swap these two windows") if drag["to"] is not None else "",
                              drag["dashed"])
        panel.highlight = drag["to"]
        panel.queue_draw()

    def ws_add_update(self, panel, drag, px, py):
        """The + handle dragged into the preview: where it is chooses the
        layout for one window more (shown live); outside, nothing happens."""
        ws = drag["ws"]
        new = state.added_cap(self.ws_store, ws, self.ws_modes) or drag["cap"]
        width = panel.get_width() or panel.get_content_width()
        height = panel.get_height() or panel.get_content_height()
        top, area_h = panel.area()
        panel.plus_at = (px, py)
        inside = 0 <= px <= width and top <= py <= height
        drag["inside"] = inside
        if inside and drag["manual"]:
            hit = panel.hit(px, py, drag["rects"])
            windows = state.saved_windows(self.ws_store, ws)
            target = state.split_target(windows, hit)
            drag["target"] = target
            panel.highlight, panel.drop = target, False
            self.ws_panel_preview(panel, drag["rects"], _("Halve this window for a new app"), drag["dashed"])
            panel.highlight = target
            return
        if not inside:
            drag["preset"] = None
            panel.drop = False
            self.ws_panel_preview(panel, drag["rects"], _("Drop it in the preview"), drag["dashed"])
            return
        preset = state.add_zone_preset(new, px / width, (py - top) / area_h) or self.ws_preset_for_cap(ws, new)
        drag["preset"] = preset
        panel.drop = True
        self.ws_panel_preview(panel, preset_rects(preset, new, state.split_for(self.ws_store, ws)),
                              self.ws_add_label(ws, new, preset))

    def ws_add_label(self, ws, new, preset):
        if state.tiles_freely(self.ws_store, ws, self.ws_modes):
            return _("Tile with {0} windows").format(new) + " · " + preset_label(preset)
        return self.ws_result_label(new, preset)

    def ws_add_window(self, ws, preset=None):
        new = state.added_cap(self.ws_store, ws, self.ws_modes)
        if new is None:
            return
        # A click keeps the layout the workspace follows; only a drop chooses one.
        shown = preset or self.ws_preset_for_cap(ws, new)
        self.ws_commit(self.ws_add_label(ws, new, shown), ws, state.add_window, ws, preset, self.ws_modes)

    def ws_saved_handle(self, panel, ws, x, y):
        """On an App Preset: ("cut", axis, edge, start) for a shared edge of two
        tiled windows under the pointer, ("corner", index) for a floating
        window's bottom right corner; None otherwise."""
        if ws <= 0 or not state.is_manual(self.ws_store, ws) or panel.tray:
            return None
        windows = state.saved_windows(self.ws_store, ws)
        bounds = state.saved_bounds(windows)
        if bounds is None or len(panel.rects) != len(windows):
            return None
        width = panel.get_width() or panel.get_content_width()
        top, area_h = panel.area()
        scale = 1 - 2 * SAVED_INSET
        reach = max(6, GAP * width * 0.5)
        for index, (window, rect) in enumerate(zip(windows, panel.rects)):
            if window.get("floating"):
                cx, cy = (rect[0] + rect[2]) * width, top + (rect[1] + rect[3]) * area_h
                if abs(x - cx) <= reach and abs(y - cy) <= reach:
                    return ("corner", index)
        for axis, edge, start in state.saved_cuts(windows):
            mid = ((edge + start) / 2 - bounds[axis]) / bounds[2 + axis] * scale + SAVED_INSET
            line = mid * width if axis == 0 else top + mid * area_h
            pointer = x if axis == 0 else y
            if abs(pointer - line) > reach:
                continue
            # Only along the windows that share this edge.
            other = 1 - axis
            facing = [w for w in windows if not w.get("floating") and w.get("at") and w.get("size") and
                      (abs(w["at"][axis] + w["size"][axis] - edge) <= 1 or abs(w["at"][axis] - start) <= 1)]
            lo = min(w["at"][other] for w in facing)
            hi = max(w["at"][other] + w["size"][other] for w in facing)
            lo_n = (lo - bounds[other]) / bounds[2 + other] * scale + SAVED_INSET
            hi_n = (hi - bounds[other]) / bounds[2 + other] * scale + SAVED_INSET
            along = (y - top) / area_h if axis == 0 else x / width
            if lo_n <= along <= hi_n:
                return ("cut", axis, edge, start)
        return None

    def ws_saved_update(self, panel, drag, px, py):
        """A shared edge or a floating corner of an App Preset dragged: the
        saved windows as they would be (the picture keeps its scale)."""
        bounds, handle, windows = drag["bounds"], drag["handle"], drag["windows"]
        width = panel.get_width() or panel.get_content_width()
        top, area_h = panel.area()
        scale = 1 - 2 * SAVED_INSET

        def to_px(value, axis):
            return bounds[axis] + (value - SAVED_INSET) / scale * bounds[2 + axis]
        u, v = px / width, (py - top) / area_h
        if handle[0] == "cut":
            _kind, axis, edge, start = handle
            result = state.move_saved_cut(windows, axis, edge, start, to_px(u if axis == 0 else v, axis))
            if result is not None:
                try:        # Restore must still be able to rebuild it as a split tree
                    tiling.layout_tree([w for w in result if not w.get("floating")])
                except ValueError:
                    result = None
        else:
            index = handle[1]
            at = windows[index]["at"]
            result = state.resize_saved_floating(windows, index, to_px(u, 0) - at[0], to_px(v, 1) - at[1])
        if result is not None:
            drag["result"] = result
        shown = drag["result"] or windows
        index = handle[1] if handle[0] == "corner" else None
        size = shown[index]["size"] if index is not None else None
        badge = _("{0} × {1} px").format(*size) if size else _("Drag to change both windows")
        self.ws_panel_preview(panel, window_rects(shown, bounds), badge, drag["dashed"])

    def ws_pick_app(self, panel, ws, index):
        """+ on an App Preset: choose an app from the launchers; it is saved
        into the preset (halving the chosen or the largest window), not started."""
        popover = Gtk.Popover()
        popover.add_css_class("tm-popover")
        popover.set_parent(panel)
        popover.set_autohide(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(280, -1)
        title = Gtk.Label(label=_("Add an app to the App Preset"), xalign=0)
        title.add_css_class("tm-ws-heading")
        box.append(title)
        search = Gtk.SearchEntry(placeholder_text=_("Search apps"))
        if hasattr(self, "guard_text_field"):
            self.guard_text_field(search)          # bounded paste, like every text field here
        box.append(search)
        scroller = Gtk.ScrolledWindow(min_content_height=240, max_content_height=320, propagate_natural_height=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rows = Gtk.ListBox()
        rows.set_selection_mode(Gtk.SelectionMode.BROWSE)
        scroller.set_child(rows)
        box.append(scroller)
        hint = Gtk.Label(label=_("Saved only: Restore App Preset starts it."), xalign=0, wrap=True, max_width_chars=1)
        hint.add_css_class("tm-hint")
        box.append(hint)
        popover.set_child(box)
        self.ws_app_popover = popover
        apps = []

        def chosen(app):
            popover.popdown()
            name = theme_core.plain_text(str(app.get("name") or app.get("desktop_id")), 40)
            self.ws_commit(_("{0} added to the App Preset.").format(name), ws, state.add_saved_app, ws, app, index)

        def fill(*_args):
            needle = search.get_text().strip().lower()
            child = rows.get_first_child()
            while child is not None:
                rows.remove(child)
                child = rows.get_first_child()
            shown = [a for a in apps if needle in str(a.get("name", "")).lower()][:200]
            for app in shown:
                row = Gtk.ListBoxRow()
                line = Gtk.Box(spacing=8)
                icon_name = app.get("icon") or "application-x-executable"
                icon = Gtk.Image.new_from_icon_name(icon_name if not str(icon_name).startswith("/")
                                                    else "application-x-executable")
                icon.set_pixel_size(18)
                line.append(icon)
                line.append(Gtk.Label(label=theme_core.plain_text(str(app.get("name", "")), 60), xalign=0,
                                      ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, hexpand=True))
                row.set_child(line)
                row.tm_app = app
                rows.append(row)
            first = rows.get_row_at_index(0)
            if first is not None:
                rows.select_row(first)

        def loaded(result, error):
            apps[:] = sorted((result or []) if error is None else [], key=lambda a: str(a.get("name", "")).lower())
            fill()
        rows.connect("row-activated", lambda _l, row: chosen(row.tm_app))
        search.connect("search-changed", fill)
        search.connect("activate", lambda *_a: rows.get_selected_row() and chosen(rows.get_selected_row().tm_app))

        def closed(*_args):
            if getattr(self, "ws_app_popover", None) is popover:
                self.ws_app_popover = None
            GLib.idle_add(lambda: popover.unparent() if popover.get_parent() is not None else None)
        popover.connect("closed", closed)
        cx, cy, _r = panel.plus_box()
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(cx), int(cy), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()
        search.grab_focus()
        self._async(tiling.parse_desktop_files, loaded)

    def ws_remove_window(self, ws):
        """− (and the − key): one window fewer; with one window the default limit again."""
        store = self.ws_store
        new = state.removed_cap(store, ws, self.ws_modes)
        if new is None:
            return
        one_left = (state.global_cap(store) if ws <= 0 else state.effective_cap(store, ws)) == 1
        label = (_("Reset Limit to Default") + f" ({new})") if one_left else _("Window limit: {0}").format(new)
        self.ws_commit(label, ws, state.remove_window, ws, self.ws_modes)

    def ws_panel_flags(self, panel, ws, kind):
        """What the preview offers for this workspace: the + handle while one
        more window fits, or on an App Preset: one more app."""
        store = self.ws_store
        panel.all_label = _("All workspaces · default") if ws == 0 else ""
        if ws > 0 and state.is_manual(store, ws):
            panel.plus_visible = len(state.saved_windows(store, ws)) < 8     # one more app
        else:
            panel.plus_visible = kind == "workspaces" and state.can_add_window(store, ws, self.ws_modes)
        panel.plus_hot = False
        panel.minus_visible = kind == "workspaces" and state.can_remove_window(store, ws, self.ws_modes)
        panel.minus_hot = False
        panel.queue_draw()

    def ws_divider(self, panel, ws):
        """The one divider of the shown layout: (axis, pixel position, preset,
        split) or None. Master Left/Right and Side-by-Side: a vertical line,
        Stacked: a horizontal one."""
        if ws > 0 and state.is_manual(self.ws_store, ws):
            return None
        preset = self.ws_shown_preset(ws)
        if not state.has_divider(preset, self.ws_panel_cap(ws)):
            return None
        width = panel.get_width() or panel.get_content_width()
        top, area_h = panel.area()
        if preset == "columns":
            # Two lines around the middle column; either sets its share.
            low, high = state.split_range(preset)
            split = min(high, max(low, state.split_for(self.ws_store, ws) or 1 / 3))
            side = ((1 - 4 * GAP) - (1 - 4 * GAP) * split) / 2
            return "x", (GAP + side + GAP / 2) * width, preset, split
        split = state.split_for(self.ws_store, ws) or 0.5
        g, part = GAP, (1 - 3 * GAP)
        if preset == "stacked":
            return "y", top + (g + part * split + g / 2) * area_h, preset, split
        share = 1 - split if preset == "master-right" else split
        return "x", (g + part * share + g / 2) * width, preset, split

    def ws_on_divider(self, panel, ws, x, y):
        divider = self.ws_divider(panel, ws)
        if divider is None:
            return None
        width = panel.get_width() or panel.get_content_width()
        top, _area_h = panel.area()
        reach = max(6, GAP * width * 0.7)
        axis, pos = divider[0], divider[1]
        places = (pos, width - pos) if divider[2] == "columns" else (pos,)
        if y > top and any(abs((x if axis == "x" else y) - place) <= reach for place in places):
            return divider
        return None

    def ws_split_at(self, panel, axis, preset, x, y):
        """The split a pointer position on the divider stands for (0.2…0.8,
        snapping to even near the middle)."""
        width = panel.get_width() or panel.get_content_width()
        top, area_h = panel.area()
        if preset == "columns":
            # Mirror round the centre: the middle column is twice the distance wide.
            low, high = state.split_range(preset)
            value = (2 * abs(x / width - 0.5) - GAP) / (1 - 4 * GAP)
            value = min(high, max(low, value))
            return 0.33 if abs(value - 1 / 3) < 0.025 else round(value, 2)
        share = ((x / width) if axis == "x" else (y - top) / area_h) - 1.5 * GAP
        share /= (1 - 3 * GAP)
        value = 1 - share if preset == "master-right" else share
        value = min(state.SPLIT_MAX, max(state.SPLIT_MIN, value))
        return 0.5 if abs(value - 0.5) < 0.025 else round(value, 2)

    @staticmethod
    def ws_split_label(preset, value):
        if preset == "columns":
            return _("Middle column {0} %").format(round(value * 100))
        left = 1 - value if preset == "master-right" else value
        return _("Split {0} % · {1} %").format(round(left * 100), 100 - round(left * 100))

    def ws_set_split(self, ws, value):
        label = _("Even split") if value is None else self.ws_split_label(self.ws_shown_preset(ws), value)
        self.ws_commit(label, ws, state.set_split, ws, value)

    def ws_shown_preset(self, ws):
        if ws == 0:
            return state.default_preset_for(self.ws_store, state.global_cap(self.ws_store))
        return state.preset_for(self.ws_store, ws, self.ws_modes)

    def ws_master_flip(self, ws, cap, index, x, width):
        """Master Left/Right: the master window dragged into the other half
        (over the stack) moves the master to that side; None otherwise."""
        if cap < 3 or index is None:
            return None
        preset = self.ws_shown_preset(ws)
        if preset == "master-left" and index == 0 and x > width / 2:
            return "master-right"
        if preset == "master-right" and index == cap - 1 and x < width / 2:
            return "master-left"
        return None

    def ws_preset_for_cap(self, ws, cap):
        """The layout a workspace would have with another window limit."""
        store = self.ws_store
        own = state.own_preset(store, ws) if ws > 0 else None
        if own and own in state.TILING_BY_CAP.get(cap, ()):
            return own
        return state.default_preset_for(store, cap) if cap > 1 else "side-by-side"

    def ws_result_label(self, cap, preset):
        return _("Max {0}").format(cap) + " · " + preset_label(preset)

    def ws_panel_end(self, panel, kind, _dx, _dy):
        drag, self.ws_drag = getattr(self, "ws_drag", None), None
        self.ws_drag_ended = GLib.get_monotonic_time()
        panel.highlight, panel.edge, panel.drop, panel.badge = None, False, False, ""
        panel.tray, panel.tray_hot, panel.source = (), None, None
        if not drag:
            return
        ws = drag["ws"]

        def back():
            self.ws_panel_preview(panel, drag["rects"], "", drag["dashed"])
        panel.split_line = None
        panel.plus_at = None
        if drag["mode"] == "minus":
            return self.ws_remove_window(ws)
        if drag["mode"] == "add":
            if drag["moved"] and not drag["inside"]:
                return back()               # dropped outside: nothing changes
            if drag["manual"]:
                back()
                return self.ws_pick_app(panel, ws, drag["target"] if drag["moved"] else None)
            return self.ws_add_window(ws, drag["preset"] if drag["moved"] else None)
        if drag["mode"] == "saved":
            if not drag["result"]:
                return back()
            label = _("Window size changed in the App Preset; Restore App Preset applies it.")
            return self.ws_commit(label, ws, state.set_saved_windows, ws, drag["result"])
        if drag["mode"] == "split":
            if drag["value"] == drag["start"]:
                return back()
            return self.ws_set_split(ws, drag["value"])
        if drag["mode"] == "edge" or drag.get("out"):
            new = drag["new"] if drag["mode"] == "edge" else self.ws_panel_cap(ws) - 1
            if new == self.ws_panel_cap(ws):
                return back()
            label = _("Window limit: {0}").format(new)
            if ws == 0:
                self.ws_commit(label, ws, state.set_global_cap, new)
            else:
                self.ws_commit(label, ws, state.set_cap, ws, new, self.ws_modes)
            return
        if drag.get("preset"):
            preset = drag["preset"]
            label = self.ws_result_label(self.ws_panel_cap(ws), preset)
            if ws == 0:
                self.ws_commit(label, ws, state.set_default_preset, state.global_cap(self.ws_store), preset)
            else:
                self.ws_commit(label, ws, state.set_preset, ws, preset)
            return
        if drag["to"] is None:
            return back()
        self.ws_swap(ws, list(drag["rects"]), drag["from"], drag["to"])

    def ws_swap(self, ws, rects, first, second):
        if ws > 0 and state.is_manual(self.ws_store, ws):
            self.ws_message = _("Swapped two windows of the App Preset.")
            self.ws_commit(_("Swapped two windows of the App Preset."), ws, state.swap_saved, ws, first, second)
        elif ws > 0:
            self._async(lambda: tiling.swap_slots(ws, rects, first, second),
                        lambda result, error: self.ws_swapped(result, error, ws, (rects, first, second)))

    def ws_commit(self, label, ws, rule, *args):
        """A change made in the preview, offered for Undo (the bar and Ctrl+Z):
        the settings as they were come back, and that workspace is laid out
        again."""
        undo = WorkspaceUndo(self, None, ws)

        def recorded(store, *rule_args):
            # Under the lock, on the settings as saved: what the change found and left.
            undo.before = json.loads(json.dumps(store))
            action = rule(store, *rule_args)
            undo.after = tiling.clean_store(json.loads(json.dumps(store)))   # as it is saved
            return action
        self.ws_message = label
        self.ws_change(recorded, *args)
        if hasattr(self, "offer_undo"):
            self.offer_undo(label, [undo])

    def ws_panel_key(self, panel, kind, keyval, mods) -> bool:
        ws = self.ws_current(kind)
        count = len(panel.rects)
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and count:
            step = -1 if keyval == Gdk.KEY_Left else 1
            current = panel.highlight or 0
            target = (current + step) % count
            if shift and count > 1:
                self.ws_swap(ws, list(panel.rects), current, target)
            panel.highlight = target
            panel.queue_draw()
            return True
        if keyval in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):
            divider = self.ws_divider(panel, ws)
            if divider is not None:
                step = 0.05 if keyval == Gdk.KEY_bracketright else -0.05
                low, high = state.split_range(divider[2])
                value = round(min(high, max(low, divider[3] + step)), 2)
                if value != divider[3]:
                    self.ws_set_split(ws, value)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and not (ws > 0 and state.is_manual(self.ws_store, ws)):
            # The layouts of the drag tray, one after the other.
            cap = self.ws_panel_cap(ws)
            layouts = [t[0] for t in self.ws_tray_targets(ws, cap) if t[1] == cap]
            if layouts:
                current = self.ws_shown_preset(ws)
                step = 1 if keyval == Gdk.KEY_Down else -1
                preset = layouts[(layouts.index(current) + step) % len(layouts)] if current in layouts else layouts[0]
                label = self.ws_result_label(cap, preset)
                if ws == 0:
                    self.ws_commit(label, ws, state.set_default_preset, cap, preset)
                else:
                    self.ws_commit(label, ws, state.set_preset, ws, preset)
            return True
        if keyval in (Gdk.KEY_plus, Gdk.KEY_KP_Add, Gdk.KEY_minus, Gdk.KEY_KP_Subtract) and \
                not (ws > 0 and state.is_manual(self.ws_store, ws)):
            cap = self.ws_panel_cap(ws)
            if keyval in (Gdk.KEY_plus, Gdk.KEY_KP_Add):
                self.ws_add_window(ws)
                return True
            self.ws_remove_window(ws)
            return True
        return False

    def ws_swapped(self, result, error, ws, swap=None):
        if error is not None:
            self.ws_message = str(error)
        elif (result or {}).get("status") == "ok":
            self.ws_message = _("Swapped two windows on Workspace {0}.").format(ws)
            if swap is not None and hasattr(self, "offer_undo"):
                self.offer_undo(self.ws_message, [LiveSwapUndo(ws, *swap)])
        else:
            self.ws_message = _("Workspace {0} has no two open windows there to swap.").format(ws)
        self.ws_render()

    def build_drag_ghost(self) -> Gtk.Widget:
        """The picture that follows the pointer while a layout choice is
        dragged: one drawing layer over the whole card, so moving it costs
        no layout pass (real Wayland drags can't be used here)."""
        ghost = Gtk.DrawingArea()
        ghost.set_can_target(False)
        ghost.set_hexpand(True)
        ghost.set_vexpand(True)
        ghost.set_visible(False)
        ghost.tm_state = None
        ghost.set_draw_func(self.ws_ghost_draw)
        self.ws_ghost = ghost
        return ghost

    def ws_ghost_draw(self, _area, cr, _width, _height):
        info = self.ws_ghost.tm_state
        if not info:
            return
        x, y, w, h = info["x"], info["y"], info["w"], info["h"]
        accent = self.ws_colors().get("accent", "#89b4fa")
        cr.save()
        cr.translate(x, y)
        cr.push_group()
        info["picture"].draw(None, cr, w, h)
        rounded(cr, 0.5, 0.5, w - 1, h - 1, 6)
        cr.set_source_rgba(*rgba(accent, 1))
        cr.set_line_width(2)
        cr.stroke()
        cr.pop_group_to_source()
        cr.paint_with_alpha(info["alpha"])
        cr.restore()

    def ws_ghost_show(self, preset, cap, source, x, y, grab, alpha):
        """grab: where in the source the pointer took it (0…1), kept under the pointer."""
        ghost = getattr(self, "ws_ghost", None)
        frame = getattr(self, "ws_hover_frame", None)
        if ghost is None or frame is None:
            return
        found, point = source.compute_point(frame, Graphene.Point().init(x, y))
        if not found:
            self.ws_ghost_hide()
            return
        info = ghost.tm_state
        if info is None or info["preset"] != preset:
            _card, (cw, ch) = self.ws_picture_size()
            w, h = round(cw * 1.4), round(ch * 1.4)
            picture = LayoutPicture(w, h, self.ws_colors(), preset_rects(preset, 3 if preset == "none" else cap),
                                    dashed=preset == "none")
            info = ghost.tm_state = {"preset": preset, "picture": picture, "w": w, "h": h}
        info.update(x=point.x - grab[0] * info["w"], y=point.y - grab[1] * info["h"], alpha=alpha)
        ghost.set_visible(True)
        ghost.queue_draw()

    def ws_ghost_hide(self):
        ghost = getattr(self, "ws_ghost", None)
        if ghost is not None:
            ghost.tm_state = None
            ghost.set_visible(False)
        self.ws_choice_cancel = None

    def ws_drag_cancel(self) -> bool:
        """Esc during a drag: everything back as it was. True if one was running."""
        cancel = getattr(self, "ws_choice_cancel", None)
        panel_drag = getattr(self, "ws_drag", None)
        if cancel is None and panel_drag is None:
            return False
        if cancel is not None:
            cancel()
        if panel_drag is not None:
            self.ws_drag_reset()
        self.ws_ghost_hide()
        self.ws_render()
        return True

    def ws_drag_reset(self):
        """A preview drag dropped without a change (Esc, the preview closed,
        another tab): the picture as at its start, no drag marks left."""
        panel_drag, self.ws_drag = getattr(self, "ws_drag", None), None
        if not panel_drag:
            return
        panel = panel_drag.get("panel")
        if panel is not None:
            panel.tray, panel.tray_hot, panel.badge, panel.source = (), None, "", None
            panel.highlight, panel.edge, panel.drop, panel.plus_at = None, False, False, None
            panel.split_line, panel.split_hover, panel.edge_hover, panel.plus_hot = None, None, False, False
            self.ws_panel_preview(panel, panel_drag["rects"], "", panel_drag["dashed"])

    def ws_choice_drag(self, button, preset, kind):
        """A layout choice can be dragged onto the preview or onto a workspace
        card: it applies there (on All Workspaces: the default). The preview
        shows it live while the ghost is over it; Esc cancels."""
        drag = Gtk.GestureDrag()
        info = {}

        def panel():
            parts = getattr(self, f"ws_{kind}_panel", None)
            return parts[2] if parts and parts[0].get_visible() else None

        def cards():
            parts = getattr(self, f"ws_{kind}_parts", None)
            return parts[1] if parts else None

        def restore():
            target = panel()
            if target is not None and "rects" in info:
                target.rects, target.dashed = info["rects"], info["dashed"]
            if target is not None:
                target.drop = False
                target.queue_draw()
            marked = info.get("card")
            if marked is not None:
                marked.remove_css_class("tm-ws-drop")
            info.clear()

        def begin(_g, x, y):
            info.clear()
            width, height = max(1, button.get_width()), max(1, button.get_height())
            info.update(x=x, y=y, over=False, card=None, grab=(x / width, y / height))

        def update(g, dx, dy):
            if not info or abs(dx) + abs(dy) < 8:
                return
            g.set_state(Gtk.EventSequenceState.CLAIMED)     # a drag, not a click
            self.ws_choice_cancel = restore
            px, py = info["x"] + dx, info["y"] + dy
            ws = self.ws_current(kind)
            cap = max(2, state.global_cap(self.ws_store) if ws == 0 else state.effective_cap(self.ws_store, ws))
            target = panel()
            over = False
            if target is not None:
                found, point = button.compute_point(target, Graphene.Point().init(px, py))
                over = found and 0 <= point.x <= target.get_width() and 0 <= point.y <= target.get_height()
            if over != info["over"]:
                info["over"] = over
                if over:
                    info["rects"], info["dashed"] = list(target.rects), target.dashed
                    target.rects = preset_rects(preset, 3 if preset == "none" else cap)
                    target.dashed = preset == "none"
                elif "rects" in info:
                    target.rects, target.dashed = info["rects"], info["dashed"]
                target.drop = over
                target.queue_draw()
            # A workspace card under the pointer is a drop place too.
            grid, card = cards(), None
            if grid is not None and not over:
                found, point = button.compute_point(grid, Graphene.Point().init(px, py))
                child = grid.get_child_at_pos(point.x, point.y) if found else None
                card = child if child is not None and getattr(child, "ws_id", None) is not None else None
            if card is not info.get("card"):
                if info.get("card") is not None:
                    info["card"].remove_css_class("tm-ws-drop")
                if card is not None:
                    card.add_css_class("tm-ws-drop")
                info["card"] = card
            # Over the preview the ghost steps back: the preview shows the result.
            self.ws_ghost_show(preset, cap, button, px, py, info["grab"], 0.3 if over else 0.9)

        def end(_g, _dx, _dy):
            over, card = info.get("over"), info.get("card")
            self.ws_ghost_hide()
            if card is not None:
                card.remove_css_class("tm-ws-drop")
            if over:
                target_ws = self.ws_current(kind)
            elif card is not None:
                target_ws = card.ws_id
            else:
                restore()
                return
            info.clear()
            if target_ws == 0:
                self.ws_change(state.set_default_preset, state.global_cap(self.ws_store), preset)
            else:
                self.ws_selected = target_ws
                self.ws_change(state.set_preset, target_ws, preset)
        drag.connect("drag-begin", begin)
        drag.connect("drag-update", update)
        drag.connect("drag-end", end)
        drag.connect("cancel", lambda *_args: (restore(), self.ws_ghost_hide()))
        button.add_controller(drag)

    def ws_look_data(self, ws):
        """(theme, wallpaper path or None, colors, image source) of a workspace."""
        try:
            theme, wallpaper = self.ws_look(ws)
            colors = dict(theme_custom.load_palette(theme).colors) if theme else self.ws_colors()
        except (OSError, ValueError):
            theme, wallpaper, colors = None, None, self.ws_colors()
        source = str(wallpaper) if wallpaper else (theme.preview if theme else None)
        return theme, wallpaper, colors, source

    def ws_show_panel(self, kind, ws):
        """The right-hand panel: the chosen workspace as it looks, faded in."""
        parts = getattr(self, f"ws_{kind}_panel", None)
        if parts is None or not getattr(self, "ws_sizes", (False,))[0]:
            return
        right, title, panel, caption = parts
        width, height = getattr(self, "ws_panel_size", (HOVER_W, HOVER_H))
        panel.set_content_width(width)
        panel.set_content_height(height)
        theme, wallpaper, colors, source = self.ws_look_data(ws)
        rects, dashed = self.ws_card_rects(ws, kind)
        key = (source, width)
        self.ws_panel_flags(panel, ws, kind)
        panel.show(rects, dashed, colors, self.ws_wallpapers.get(key))
        if source and key not in self.ws_wallpapers:
            def ready(pixbuf, _error, k=key, p=panel):
                self.ws_wallpapers[k] = pixbuf
                p.wallpaper = pixbuf
                p.queue_draw()
            self._async(lambda: load_cover(source, width, height), ready)
        title.set_label((_("All Workspaces") if ws == 0 else _("Workspace {0}").format(ws)).upper())
        parts_text = [theme_core.plain_text(theme.name, 40) if theme else _("Theme missing"),
                      Path(source).name if wallpaper and ws > 0 else "", self.ws_card_summary(ws, kind)]
        caption.set_label(" · ".join(part for part in parts_text if part))
        if getattr(right, "tm_shown", None) != ws:
            right.tm_shown = ws
            panel.set_opacity(0)
            fade(panel, 1.0, duration_ms=240)

    # --- large preview ---------------------------------------------------------------------

    def build_ws_hover(self) -> Gtk.Widget:
        """The large preview over the whole card (added by the popup): a dimmed
        backdrop with the workspace centred as large as the popup allows.
        A click anywhere or Esc closes it."""
        backdrop = Gtk.Box()
        backdrop.add_css_class("tm-dialog-backdrop")
        backdrop.set_visible(False)
        backdrop.set_opacity(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      accessible_role=Gtk.AccessibleRole.DIALOG)
        box.add_css_class("tm-ws-hover")
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_hexpand(True)
        box.set_vexpand(True)
        picture = LookAlike(HOVER_W, HOVER_H)
        box.append(picture)
        self.ws_panel_gestures(picture, None)      # drag and drop as in the wide preview
        caption = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        caption.add_css_class("tm-ws-hover-caption")
        box.append(caption)
        backdrop.append(box)
        click = Gtk.GestureClick()

        def released(_g, _n, x, y):
            # Not after a drag in the preview (it may end outside it), and
            # not on the preview itself: a click beside it closes it.
            if GLib.get_monotonic_time() - getattr(self, "ws_drag_ended", 0) < 400_000:
                return
            found, bounds = picture.compute_bounds(backdrop)
            if found and bounds.contains_point(Graphene.Point().init(x, y)):
                return
            self.ws_hover_hide()
        click.connect("released", released)
        backdrop.add_controller(click)
        self.ws_hover = (backdrop, picture, caption)
        self.ws_hover_for = None
        self.ws_hover_small = None
        self.ws_wallpapers = {}
        return backdrop

    def ws_preview_open(self) -> bool:
        hover = getattr(self, "ws_hover", None)
        return hover is not None and hover[0].get_visible() and self.ws_hover_for is not None

    def ws_preview_show(self, ws, small, rects, dashed):
        hover = getattr(self, "ws_hover", None)
        frame = getattr(self, "ws_hover_frame", None)
        if hover is None or frame is None:
            return
        backdrop, picture, caption = hover
        self.ws_hover_ws = ws
        self.ws_panel_flags(picture, ws, self.ws_tab_kind())
        # As large as the popup allows, 16:9, with room for the caption.
        width = max(240, frame.get_width() - 64)
        height = round(width * 9 / 16)
        room = frame.get_height() - 110
        if height > room:
            height = max(135, room)
            width = round(height * 16 / 9)
        picture.set_content_width(width)
        picture.set_content_height(height)
        caption.set_size_request(width, -1)
        theme, wallpaper, colors, source = self.ws_look_data(ws)
        key = (source, width)
        picture.show(rects, dashed, colors, self.ws_wallpapers.get(key))
        if source and key not in self.ws_wallpapers:
            self._async(lambda: load_cover(source, width, height),
                        lambda pixbuf, _e, k=key: self.ws_wallpaper_ready(k, pixbuf))
        title = _("All Workspaces") if ws == 0 else _("Workspace {0}").format(ws)
        parts = [title, theme_core.plain_text(theme.name, 40) if theme else _("Theme missing"),
                 Path(source).name if wallpaper and ws > 0 else "",
                 self.ws_card_summary(ws, getattr(self, "presets_tab", "workspaces"))]
        caption.set_label(" · ".join(part for part in parts if part))
        backdrop.update_property([Gtk.AccessibleProperty.LABEL], [caption.get_label()])
        if self.ws_hover_small is not None and self.ws_hover_small is not small:
            fade(self.ws_hover_small, 1.0)
        self.ws_hover_small = small
        self.ws_hover_for = key
        if small is not None:
            fade(small, 0.0)
        fade(backdrop, 1.0)
        picture.grab_focus()      # its keys: ←/→, Shift+←/→, ↑/↓, [ ], + and −

    def ws_wallpaper_ready(self, key, pixbuf):
        self.ws_wallpapers[key] = pixbuf
        hover = getattr(self, "ws_hover", None)
        if hover is not None and self.ws_hover_for == key:
            hover[1].wallpaper = pixbuf
            hover[1].queue_draw()

    def ws_app_list_open(self) -> bool:
        popover = getattr(self, "ws_app_popover", None)
        return popover is not None and popover.get_visible()

    def ws_hover_hide(self, small=None):
        hover = getattr(self, "ws_hover", None)
        if self.ws_app_list_open():
            self.ws_app_popover.popdown()
        drag = getattr(self, "ws_drag", None)
        if hover is not None and drag and drag.get("panel") is hover[1]:
            self.ws_drag_reset()          # a drag in it ends with it, changing nothing
        if hover is None or self.ws_hover_for is None and not hover[0].get_visible():
            return
        self.ws_hover_for = None
        if self.ws_hover_small is not None:
            fade(self.ws_hover_small, 1.0)
            self.ws_hover_small = None
        fade(hover[0], 0.0, hide=True)

    def ws_look(self, ws):
        """(theme, wallpaper path or None) this workspace shows."""
        themes = {item.slug: item for item in theme_core.discover_themes()}
        slug = theme_core.current_theme()
        if ws > 0 and theme_auto.workspace_themes_enabled():
            slug = theme_auto.load_workspace_themes().get(ws) or slug
        theme = themes.get(slug)
        wallpaper = None
        if theme is not None and ws > 0 and theme_auto.workspace_wallpapers_enabled():
            name = theme_auto.load_workspace_map().get(slug, {}).get(ws)
            wallpaper = theme_auto.wallpaper_named(theme, name) if name else None
        if wallpaper is None and theme is not None and slug == theme_core.current_theme():
            wallpaper = theme_auto.current_background()
        return theme, wallpaper

    # --- Workspaces tab controls -----------------------------------------------------------

    def ws_heading(self, box, text):
        if isinstance(box, Sections):
            box.section(text)          # every section in its own group card
            return
        label = Gtk.Label(label=text.upper(), xalign=0)
        label.add_css_class("tm-ws-heading")
        box.append(label)

    def ws_button_row(self, box, items, columns=4):
        """items: (label, selected, on_click, tooltip) as one row of equal buttons."""
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)
        for index, (label, selected, on_click, tooltip) in enumerate(items):
            button = Gtk.Button(label=label)
            button.add_css_class("tm-ws-choice")
            if selected:
                button.add_css_class("tm-nav-active")
                button.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE)])
            if tooltip:
                button.set_tooltip_text(tooltip)
            button.connect("clicked", lambda *_a, f=on_click: f())
            grid.attach(button, index % columns, index // columns, 1, 1)
        box.append(grid)

    def ws_preset_choices(self, box, cap, selected, on_pick, extra=()):
        presets = list(state.TILING_BY_CAP.get(cap, ())) + list(extra)
        _card, (choice_w, choice_h) = self.ws_picture_size()
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)
        grid.set_halign(Gtk.Align.START)    # choices keep their shape, never full-width strips
        for index, preset in enumerate(presets):
            button = Gtk.Button()
            button.add_css_class("tm-ws-preset")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            inner.append(LayoutPicture(choice_w, choice_h, self.ws_colors(),
                                       preset_rects(preset, cap if preset not in ("none",) else 3),
                                       dashed=preset == "none"))
            name = Gtk.Label(label=preset_label(preset), ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
            name.add_css_class("tm-ws-preset-name")
            inner.append(name)
            button.set_child(inner)
            button.set_tooltip_text(preset_label(preset))
            button.update_property([Gtk.AccessibleProperty.LABEL], [preset_label(preset)])
            if preset == selected:
                button.add_css_class("tm-nav-active")
                button.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE)])
            button.connect("clicked", lambda *_a, p=preset: on_pick(p))
            self.ws_choice_drag(button, preset, "workspaces")
            grid.attach(button, index % 4, index // 4, 1, 1)
        box.append(grid)

    def ws_fill_layout_controls(self, box):
        store, modes, ws = self.ws_store, self.ws_modes, self.ws_selected
        if ws == 0:
            row = Gtk.Box(spacing=10)
            label = Gtk.Label(label=_("Tiling active"), xalign=0, hexpand=True)
            row.append(label)
            switch = Gtk.Switch(active=store.get("active") is True)
            switch.set_tooltip_text(_("Move new windows when full · Manual moves override"))
            switch.update_property([Gtk.AccessibleProperty.LABEL], [_("Tiling active")])
            switch.connect("notify::active", lambda sw, _p: sw.get_active() != (self.ws_store.get("active") is True)
                           and self.ws_change(lambda s, on: s.update(active=on), sw.get_active()))
            row.append(switch)
            box.append(row)
            self.ws_heading(box, _("Default window limit (all workspaces)"))
            cap = state.global_cap(store)
            self.ws_button_row(box, [(str(n), cap == n, lambda n=n: self.ws_change(state.set_global_cap, n),
                                      _("Default: {0} windows").format(n)) for n in range(1, 5)])
            if cap > 1:
                self.ws_heading(box, _("Default layout preset"))
                self.ws_preset_choices(box, cap, state.default_preset_for(store, cap),
                                       lambda p: self.ws_change(state.set_default_preset, cap, p))
            self.ws_heading(box, _("Preset & App Preset management (all workspaces)"))
            actions = ws_wrap()
            actions.append(self.ws_action(_("↺ Reset All to Default"), "edit-undo-symbolic",
                                          lambda: self.ws_change(state.reset_all),
                                          _("Reset all workspaces to global default limit and preset")))
            actions.append(self.ws_action(_("✕ Remove All Presets"), "edit-clear-symbolic",
                                          lambda: self.ws_change(state.remove_all_presets),
                                          _("Set all workspaces to Native Hyprland tiling (no presets or overflow)")))
            actions.append(self.ws_action(_("Clear All Presets & App Presets"), "user-trash-symbolic",
                                          lambda: self.ask_confirm(
                                              _("Clear All Presets & App Presets") + "?", _("Delete"),
                                              lambda: self.ws_change(state.clear_everything),
                                              _("Clear all workspace layouts, limits, saved apps and autostart settings")),
                                          _("Clear all workspace layouts, limits, saved apps and autostart settings"),
                                          destructive=True))
            box.append(actions)
            return
        manual = state.is_manual(store, ws)
        if manual:
            note = Gtk.Label(xalign=0, wrap=True, max_width_chars=1, label=_(
                "Workspace {0} is in App Presets mode ({1}). Custom window geometry is preserved and overflow moves are disabled.")
                .format(ws, _("{0} apps saved").format(len(state.saved_apps(store, ws)))))
            note.add_css_class("tm-ws-banner")
            box.append(note)
        self.ws_heading(box, _("Window limit"))
        cap = state.effective_cap(store, ws)
        custom = state.has_custom_cap(store, ws)
        native = state.is_native(store, ws, modes) or state.is_free(store, ws, modes)
        items = [(str(n), not manual and not native and cap == n,
                  lambda n=n: self.ws_change(state.set_cap, ws, n, modes),
                  _("Set Workspace {0} limit to {1} windows").format(ws, n)) for n in range(1, 5)]
        self.ws_button_row(box, items)
        if custom:
            box.append(self.ws_action(_("Reset Limit to Default") + f" ({state.global_cap(store)})",
                                      "edit-undo-symbolic", lambda: self.ws_change(state.reset_cap, ws, modes)))
        self.ws_heading(box, _("Layout preset"))
        if cap <= 1 and not native:
            hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=1, label=_(
                "Keeps one tiled window on this workspace. Newly opened windows move to the next workspace with room."))
            hint.add_css_class("tm-hint")
            box.append(hint)
        shown = "" if manual else state.preset_for(store, ws, modes)
        selected = shown if (state.has_custom_preset(store, ws) or state.mode_from_omarchy(store, ws, modes)) else ""
        self.ws_preset_choices(box, max(cap, 2) if cap > 1 else 1, selected,
                               lambda p: self.ws_change(state.toggle_preset, ws, p, modes),
                               extra=("none", "floating", "scrolling"))
        if not manual and selected and not state.mode_from_omarchy(store, ws, modes):
            hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=1,
                             label=_("Click again or Reset to remove preset."))
            hint.add_css_class("tm-hint")
            box.append(hint)
        box.append(self.ws_action(_("Reset Workspace to Default"), "edit-undo-symbolic",
                                  lambda: self.ws_change(state.reset_workspace, ws)))

    # --- Apps tab controls ---------------------------------------------------------------

    def ws_fill_app_controls(self, box):
        store, ws = self.ws_store, self.ws_current("apps")
        if ws <= 0:
            return
        has_layout = state.has_saved_layout(store, ws)
        summary = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        in_use = has_layout and state.is_manual(store, ws)
        if has_layout:
            count = (state.layout_for(store, ws) or {}).get("count") or len(state.saved_windows(store, ws))
            summary.set_label(_("App Preset") + " · " + _("Workspace {0}").format(ws) + " · " +
                              _("{0} windows").format(count))
        else:
            summary.set_label(_("No App Preset saved yet"))
        summary.add_css_class("tm-ws-heading")
        box.append(summary)
        if has_layout and not in_use:
            # Kept after a reset or a chosen layout: saved, but not used and
            # not started at login until it is used again.
            quiet = Gtk.Box(spacing=8)
            quiet.add_css_class("tm-ws-banner")
            note = Gtk.Label(label=_("Saved, not in use: the workspace tiles with its layout and nothing starts at login."),
                             xalign=0, wrap=True, max_width_chars=1, hexpand=True)
            quiet.append(note)
            use = Gtk.Button(label=_("Use App Preset"))
            use.add_css_class("tm-paste")
            use.set_valign(Gtk.Align.CENTER)
            use.connect("clicked", lambda *_args: self.ws_change(state.use_app_preset, ws))
            quiet.append(use)
            box.append(quiet)
        apps = state.saved_apps(store, ws)
        if apps:
            chips = Gtk.FlowBox()
            chips.set_selection_mode(Gtk.SelectionMode.NONE)
            chips.set_max_children_per_line(4)
            chips.set_column_spacing(6)
            chips.set_row_spacing(6)
            failed = {str(item.get("name") or item.get("class") or "").lower()
                      for item in self.ws_failed.get(ws, [])}
            for index, app in enumerate(apps):
                chips.append(self.ws_app_chip(ws, index, app,
                                              str(app.get("name") or app.get("class") or "").lower() in failed))
            box.append(chips)
            hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=1,
                             label=_("× removes an app from the App Preset; it does not close its open windows."))
            hint.add_css_class("tm-hint")
            box.append(hint)
        if self.ws_failed.get(ws):
            failure = Gtk.Label(xalign=0, wrap=True, max_width_chars=1, label=self.ws_failure_text(ws))
            failure.add_css_class("tm-ws-error")
            box.append(failure)
        if self.ws_conflict and self.ws_conflict.get("workspace") == ws:
            box.append(self.ws_conflict_card(ws))
        row = ws_wrap()
        row.append(self.ws_action(_("Save App Preset"), "document-save-symbolic", lambda: self.ws_capture(ws),
                                  _("Saves the open windows of Workspace {0}, where they are, as its App Preset.").format(ws)))
        if has_layout and apps:
            row.append(self.ws_action(_("Restore App Preset"), "view-refresh-symbolic",
                                      lambda: self.ws_restore(ws, use=True),
                                      _("Relaunches missing apps and restores windows into their exact saved arrangement.")))
        box.append(row)
        if has_layout:
            box.append(self.ws_action(_("✕ Remove App Preset"), "user-trash-symbolic",
                                      lambda: self.ws_change(state.remove_app_preset, ws), destructive=True))
            for key, title, hint, on in (
                    ("saveApps", _("Save apps & show icons"), _("Display app icons in the workspace bar card"),
                     state.save_apps_on(store, ws)),
                    ("autostart", _("Autostart on session startup"), _("Automatically launch saved apps on startup"),
                     state.autostart_on(store, ws))):
                box.append(self.ws_toggle(title, hint, on, lambda k=key, was=on: self.ws_toggle_flag(ws, k, was)))
        box.append(Gtk.Separator())
        box.append(self.ws_toggle(_("Auto-move on restore"), _("Move existing windows to next free workspace without asking"),
                                  store.get("autoMoveOnRestore") is True,
                                  lambda: self.ws_change(lambda s: s.update(autoMoveOnRestore=s.get("autoMoveOnRestore") is not True))))
        box.append(self.ws_action(
            _("✕ Remove All App Presets"), "user-trash-symbolic",
            lambda: self.ask_confirm(
                _("Remove All App Presets") + "?", _("Delete"),
                lambda: (self.ws_change(state.remove_all_app_presets), setattr(self, "ws_failed", {})),
                _("Deletes all saved app presets and autostart settings across all workspaces. All workspaces return to Presets mode.")),
            _("Delete all saved app presets across all workspaces"), destructive=True))

    def ws_action(self, label, icon, on_click, tooltip="", destructive=False) -> Gtk.Button:
        """An action as in Maprisma: outlined, natural width, accent icon
        (the symbol some texts start with is dropped: the icon says it)."""
        button = Gtk.Button()
        button.add_css_class("tm-action")
        if destructive:
            button.add_css_class("tm-destructive")
        button.set_halign(Gtk.Align.START)
        text = label.lstrip("↺✕◻ ")
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Image.new_from_icon_name(icon))
        inner.append(Gtk.Label(label=text))
        button.set_child(inner)
        button.update_property([Gtk.AccessibleProperty.LABEL], [text])
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda *_args: on_click())
        return button

    def ws_toggle(self, title, hint, on, flip) -> Gtk.Widget:
        row = Gtk.Box(spacing=10)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=1))
        small = Gtk.Label(label=hint, xalign=0, wrap=True, max_width_chars=1)
        small.add_css_class("tm-hint")
        text.append(small)
        row.append(text)
        switch = Gtk.Switch(active=on, valign=Gtk.Align.CENTER)
        switch.update_property([Gtk.AccessibleProperty.LABEL], [title])
        switch.connect("notify::active", lambda sw, _p: sw.get_active() != on and flip())
        row.append(switch)
        return row

    def ws_app_chip(self, ws, index, app, failed) -> Gtk.Widget:
        chip = Gtk.Box(spacing=4)
        chip.add_css_class("tm-ws-chip")
        if failed:
            chip.add_css_class("tm-ws-chip-failed")
        # A saved icon is only ever an icon-theme name: a path (from the shared store, a
        # backup or a launcher) is never opened or decoded here, on the UI thread.
        icon_name = app.get("icon") if isinstance(app.get("icon"), str) else ""
        if not icon_name or "/" in icon_name:
            icon_name = "application-x-executable"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        chip.append(icon)
        name = str(app.get("name") or app.get("class") or _("App"))
        chip.append(Gtk.Label(label=name, ellipsize=Pango.EllipsizeMode.END, max_width_chars=12))
        remove = Gtk.Button(label="✕")
        remove.add_css_class("flat")
        remove.add_css_class("tm-ws-chip-remove")
        remove.set_tooltip_text(_("Remove {0} from the App Preset").format(name))
        remove.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove {0} from the App Preset").format(name)])
        remove.connect("clicked", lambda *_args: self.ws_remove_app(ws, index, name))
        chip.append(remove)
        return chip

    def ws_remove_app(self, ws, index, name):
        self.ws_message = _("Removed {0} from the saved layout.").format(name)
        self.ws_change(state.remove_app, ws, index)

    def ws_failure_text(self, ws) -> str:
        reasons = {
            "window_timeout": _("No matching window appeared before the restore timed out."),
            "close_timeout": _("The window did not close. Check Workspace {0} for a save or close-confirmation dialog, then retry Restore.").format(ws),
            "launch_failed": _("The app could not be launched."),
            "command_not_found": _("The saved launch command could not be found."),
            "desktop_file_not_found": _("The saved app launcher could not be found."),
            "no_launcher": _("No installed app launcher matches this window, so it is not started. Open the app and save the preset again."),
            "workspace_move_failed": _("The window could not be moved to this workspace."),
            "app_launching_disabled": _("Launching missing apps is disabled for this preset."),
        }
        return "\n".join(f"{item.get('name') or item.get('class') or _('App')}: "
                         f"{reasons.get(item.get('reason'), item.get('reason') or _('Restore failed without a reason.'))}"
                         for item in self.ws_failed.get(ws, []))

    def ws_conflict_card(self, ws) -> Gtk.Widget:
        conflict = self.ws_conflict
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("tm-ws-banner")
        card.append(Gtk.Label(xalign=0, wrap=True, max_width_chars=1, label=_(
            "Workspace {0} has {1} open window(s) not in this preset.").format(ws, conflict.get("conflict_count", 0))))
        for item in conflict.get("conflicts", [])[:8]:
            line = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                             label="• " + str(item.get("title") or item.get("class") or _("Window")))
            line.add_css_class("tm-hint")
            card.append(line)
        card.append(Gtk.Label(xalign=0, wrap=True, max_width_chars=1,
                              label=_("How would you like to handle them before restoring?")))
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)
        target = conflict.get("next_free_workspace")
        for index, (label, action, tip) in enumerate((
                (_("Move Conflicting Windows"), "move", _("Move conflicting windows to the next empty workspace (with desktop notification)")
                 + (f" ({target})" if target else "")),
                (_("✕ Close"), "close", _("Close existing windows and restore the saved layout")),
                (_("◻ Keep (Float)"), "keep", _("Keep existing windows on this workspace as floating windows")),
                (_("Cancel"), None, _("Cancel restoring this preset")))):
            button = Gtk.Button(label=label.lstrip("✕◻ "))
            button.add_css_class("tm-action")
            button.set_tooltip_text(tip)
            button.connect("clicked", lambda *_a, a=action: self.ws_restore(ws, a) if a else self.ws_cancel_restore())
            grid.attach(button, index % 2, index // 2, 1, 1)
        card.append(grid)
        return card

    def ws_cancel_restore(self):
        self.ws_conflict = None
        self.ws_message = _("Restore cancelled.")
        self.ws_render()

    def ws_capture(self, ws):
        def done(capture, error):
            if error is not None or not isinstance(capture, dict) or capture.get("status") != "ok":
                self.ws_message = _("Capture failed: {0}").format(str(error) if error else _("Unknown error"))
                self.ws_render()
                return
            self.ws_failed.pop(ws, None)
            self.ws_change(state.save_capture, ws, capture)   # a new capture switches autostart off (copy forgotten)
            self.ws_message = _("Saved App Preset for Workspace {0} ({1} windows)").format(ws, capture.get("count") or 0)
            self.ws_render()
        self._async(lambda: tiling.capture_workspace(ws), done)

    def ws_toggle_flag(self, ws, key, was):
        """A switch of an App Preset. For autostart, the layout shown right now
        (when the switch was clicked) is what will start at login."""
        self.ws_change(state.toggle_layout_flag, ws, key)     # ws_change keeps or forgets the copy

    def ws_restore(self, ws, action=None, use=False):
        if self.ws_busy:
            return
        if use and not state.is_manual(self.ws_store, ws):
            # Restoring a quiet App Preset puts it back into use first, and
            # only then restores (after the mode change and its desktop part).
            self.ws_change(state.use_app_preset, ws, then=lambda: self.ws_restore(ws, action))
            return
        action = action or ("move" if self.ws_store.get("autoMoveOnRestore") is True else "ask")
        self.ws_busy = True
        self.ws_conflict = None

        def done(result, error):
            self.ws_busy = False
            result = result if isinstance(result, dict) else {}
            if error is not None:
                self.ws_message = _("Workspace {0}: {1}").format(ws, error)
            elif result.get("status") == "conflict":
                self.ws_conflict = result
                self.ws_message = _("Workspace {0} has {1} open window(s) not in this preset.").format(
                    ws, result.get("conflict_count", 0))
            elif result.get("failed"):
                self.ws_failed[ws] = list(result["failed"])
                launched = ", ".join(result.get("launched") or [])
                failure = self.ws_failure_text(ws)
                self.ws_message = (_("Launched: {0} · Could not restore: {1}").format(launched, failure) if launched
                                   else _("Could not restore: {0}").format(failure))
            else:
                self.ws_failed.pop(ws, None)
                if result.get("launched"):
                    self.ws_message = _("Launched: {0}").format(", ".join(result["launched"]))
                elif result.get("already_running"):
                    self.ws_message = (_("Restored layout: ") if result.get("geometry_restored") else
                                       _("Already open: ")) + ", ".join(result["already_running"])
                else:
                    self.ws_message = _("Action completed.")
            self.ws_render()
        self._async(lambda: tiling.restore_workspace(ws, False, action), done)


class LookAlike(Gtk.DrawingArea):
    """A workspace as it looks: the wallpaper, a bar, and the windows of its
    layout in the theme's colors (the first one with the active border)."""

    def __init__(self, width, height):
        super().__init__()
        self.set_content_width(width)
        self.set_content_height(height)
        self.rects, self.dashed, self.colors, self.wallpaper = [], False, {}, None
        self.set_draw_func(self.draw)

    highlight = None       # a window drawn as the drop target (swap) or chosen by keyboard
    drop = False           # the whole preview marked as a drop place (a layout)
    edge = False           # the right edge marked (window limit)
    badge = ""             # the exact result of the drop, shown while dragging
    tray = ()              # while a window is dragged: (preset, cap, label, current) drop targets
    tray_hot = None        # the tray target under the pointer

    source = None          # the window being dragged (drawn dashed in its old place)
    edge_hover = False     # the pointer is on the limit gap at the right edge
    split_hover = None     # "x"/"y": the pointer is on the divider between the windows
    split_line = None      # "x"/"y": the divider is being dragged
    plus_visible = False   # the + handle at the right edge (one window more fits)
    all_label = ""         # All Workspaces: the defaults, drawn as a template (no wallpaper)
    plus_hot = False       # the pointer is on it
    plus_at = None         # (x, y) while it is dragged into the preview
    minus_visible = False  # the − handle at the left edge (one window fewer / default limit)
    minus_hot = False

    TRAY_SHARE = 0.18      # of the preview's height: a pill along its bottom

    def plus_box(self):
        """(cx, cy, r) of the + handle: in the gap at the right edge, centred."""
        width = self.get_width() or self.get_content_width()
        k = max(1.0, width / 272)
        top, area_h = self.area()
        r = max(10, 9 * k)
        return width - r - 3, top + area_h / 2, r

    def minus_box(self):
        """(cx, cy, r) of the − handle: the left edge, opposite the +."""
        cx, cy, r = self.plus_box()
        return r + 3, cy, r

    def minus_hit(self, x, y):
        if not self.minus_visible or self.tray:
            return False
        cx, cy, r = self.minus_box()
        return (x - cx) ** 2 + (y - cy) ** 2 <= (r + 4) ** 2

    def plus_hit(self, x, y):
        if not self.plus_visible or self.tray:
            return False
        cx, cy, r = self.plus_box()
        return (x - cx) ** 2 + (y - cy) ** 2 <= (r + 4) ** 2

    def tray_boxes(self):
        """(x, y, w, h) of each tray target's picture, centred in a band
        along the bottom; the label goes under the picture."""
        if not self.tray:
            return []
        width = self.get_width() or self.get_content_width()
        height = self.get_height() or self.get_content_height()
        band = height * self.TRAY_SHARE
        label = max(11, band * 0.2)
        pad = band * 0.1
        thumb_h = band - 2 * pad - label
        thumb_w = thumb_h * 16 / 10
        gap = thumb_w * 0.25
        count = len(self.tray)
        scale = min(1.0, (width - 2 * pad) / (count * thumb_w + (count - 1) * gap))
        thumb_w, thumb_h, gap = thumb_w * scale, thumb_h * scale, gap * scale
        x = (width - (count * thumb_w + (count - 1) * gap)) / 2
        y = height - band - pad
        return [(x + i * (thumb_w + gap), y, thumb_w, thumb_h) for i in range(count)]

    def tray_pill(self):
        """(x, y, w, h) of the pill holding the tray, or None."""
        boxes = self.tray_boxes()
        if not boxes:
            return None
        height = self.get_height() or self.get_content_height()
        band = height * self.TRAY_SHARE
        pad = band * 0.1
        x0, y0 = boxes[0][0] - 2 * pad, boxes[0][1] - pad
        x1 = boxes[-1][0] + boxes[-1][2] + 2 * pad
        return x0, y0, x1 - x0, band + pad

    def tray_hit(self, x, y):
        """Which tray target is under (x, y) (picture or label), or None."""
        pill = self.tray_pill()
        if pill is None or not (pill[0] <= x <= pill[0] + pill[2] and pill[1] <= y <= pill[1] + pill[3]):
            return None
        for index, (bx, _by, bw, _bh) in enumerate(self.tray_boxes()):
            if bx - bw * 0.13 <= x <= bx + bw * 1.13:
                return index
        return None

    def show(self, rects, dashed, colors, wallpaper):
        self.rects, self.dashed, self.colors, self.wallpaper = list(rects), dashed, colors, wallpaper
        self.queue_draw()

    def area(self):
        """(top, height) of the windows' part, below the bar."""
        height = self.get_height() or self.get_content_height()
        width = self.get_width() or self.get_content_width()
        k = max(1.0, width / 272)
        bar = max(7, round(height * 0.055))
        top = bar + 2 * k
        return top, height - top

    def hit(self, x, y, rects=None):
        """Which window is under (x, y), or None (in `rects`, default: those shown)."""
        width = self.get_width() or self.get_content_width()
        top, area_h = self.area()
        for index, (rx, ry, rw, rh) in enumerate(self.rects if rects is None else rects):
            if rx * width <= x <= (rx + rw) * width and top + ry * area_h <= y <= top + (ry + rh) * area_h:
                return index
        return None

    def draw(self, _area, cr, width, height):
        bg = self.colors.get("background", "#1e1e2e")
        fg = self.colors.get("foreground", "#cdd6f4")
        accent = self.colors.get("accent", "#89b4fa")
        rounded(cr, 0, 0, width, height, 8)
        cr.clip()
        if self.all_label:
            # Not a desktop: a striped template in the theme's colors.
            cr.set_source_rgba(*rgba(bg, 1))
            cr.paint()
            cr.set_source_rgba(*rgba(accent, 0.07))
            step = max(10.0, width / 30)
            cr.set_line_width(step * 0.35)
            x = -height
            while x < width:
                cr.move_to(x, height)
                cr.line_to(x + height, 0)
                x += step
            cr.stroke()
        elif self.wallpaper is not None:
            scale = max(width / self.wallpaper.get_width(), height / self.wallpaper.get_height())
            cr.save()
            cr.translate((width - self.wallpaper.get_width() * scale) / 2,
                         (height - self.wallpaper.get_height() * scale) / 2)
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(cr, self.wallpaper, 0, 0)
            cr.paint()
            cr.restore()
        else:
            cr.set_source_rgba(*rgba(bg, 1))
            cr.paint()
            cr.set_source_rgba(*rgba(accent, 0.12))
            cr.paint()
        k = max(1.0, width / 272)                               # details grow with the preview
        bar = max(7, round(height * 0.055))
        cr.set_source_rgba(*rgba(bg, 0.92))
        cr.rectangle(0, 0, width, bar)
        cr.fill()
        for i in range(3):                                      # workspace dots
            cr.set_source_rgba(*rgba(accent if i == 0 else fg, 0.9 if i == 0 else 0.5))
            cr.arc((6 + i * 7) * k, bar / 2, 2 * k, 0, 2 * math.pi)
            cr.fill()
        cr.set_source_rgba(*rgba(fg, 0.6))                      # the clock
        cr.rectangle(width / 2 - 8 * k, bar / 2 - 1.2 * k, 16 * k, 2.4 * k)
        cr.fill()
        top = bar + 2 * k
        area_h = height - top
        for index, (x, y, w, h) in enumerate(self.rects):
            rx, ry, rw, rh = x * width, top + y * area_h, w * width, h * area_h
            rounded(cr, rx, ry, rw, rh, 4 * k)
            if self.dashed or index == self.source:
                cr.set_source_rgba(*rgba(bg, 0.55))
                cr.fill_preserve()
                cr.set_source_rgba(*rgba(fg, 0.6))
                cr.set_dash([4 * k, 3 * k])
                cr.set_line_width(1.2 * k)
                cr.stroke()
                cr.set_dash([])
                continue
            cr.set_source_rgba(*rgba(bg, 0.8))   # the wallpaper shows through a little
            cr.fill_preserve()
            cr.set_source_rgba(*rgba(accent if index == 0 else fg, 0.95 if index == 0 else 0.35))
            cr.set_line_width((2 if index == 0 else 1.2) * k)
            cr.stroke()
            if index == self.highlight:          # where a dragged window would go
                rounded(cr, rx + 3 * k, ry + 3 * k, rw - 6 * k, rh - 6 * k, 3 * k)
                cr.set_source_rgba(*rgba(accent, 0.25))
                cr.fill_preserve()
                cr.set_source_rgba(*rgba(accent, 1))
                cr.set_dash([5 * k, 3 * k])
                cr.set_line_width(2 * k)
                cr.stroke()
                cr.set_dash([])
            # A title line and a few text lines, so it reads as a window.
            pad = max(3, rw * 0.08)
            line = 2.5 * k
            cr.set_source_rgba(*rgba(accent if index == 0 else fg, 0.55))
            cr.rectangle(rx + pad, ry + pad, max(4, rw * 0.35), line)
            cr.fill()
            cr.set_source_rgba(*rgba(fg, 0.25))
            line_y = ry + pad + line * 3
            for width_part in (0.75, 0.6, 0.68, 0.4, 0.7, 0.55, 0.62, 0.3):
                if line_y + line > ry + rh - pad:
                    break
                cr.rectangle(rx + pad, line_y, max(4, (rw - 2 * pad) * width_part), line * 0.8)
                cr.fill()
                line_y += line * 2.6
        if self.drop:                           # a layout is held over the preview
            rounded(cr, 2, 2, width - 4, height - 4, 8)
            cr.set_source_rgba(*rgba(accent, 1))
            cr.set_dash([8, 5])
            cr.set_line_width(3)
            cr.stroke()
            cr.set_dash([])
        axis = self.split_line or self.split_hover
        if axis and len(self.rects) >= 2 and not self.tray:      # the divider, lit
            self.draw_divider(cr, axis, width, top, area_h, k, accent)
        if self.tray:                           # a window is dragged: where it can go
            self.draw_tray(cr, width, height, k, bg, fg, accent)
        if self.plus_visible and not self.tray and not self.edge:
            cx, cy, r = self.plus_box()
            if self.plus_at is not None:        # dragged: a faint place holder stays
                self.draw_plus(cr, cx, cy, r, bg, fg, accent, 0.35, False)
                cx, cy = self.plus_at
            self.draw_plus(cr, cx, cy, r, bg, fg, accent, 1.0, self.plus_hot or self.plus_at is not None)
        if self.minus_visible and not self.tray and not self.edge and self.plus_at is None:
            cx, cy, r = self.minus_box()
            self.draw_plus(cr, cx, cy, r, bg, fg, accent, 1.0, self.minus_hot, minus=True)
        if self.edge_hover and not self.edge and not self.tray and not self.plus_hot:   # the limit gap
            cr.set_source_rgba(*rgba(fg, 0.55))
            rounded(cr, width - 6, top + area_h * 0.3, 3, area_h * 0.4, 1.5)
            cr.fill()
        if self.edge:                           # the window limit is being dragged
            cr.set_source_rgba(*rgba(accent, 0.9))
            cr.rectangle(width - 6, top, 4, height - top - 4)
            cr.fill()
        if self.badge:                          # what the drop will do, exactly
            cr.select_font_face("sans-serif", 0, 1)
            cr.set_font_size(max(11, 12 * k))
            extents = cr.text_extents(self.badge)
            pad = 8 * k
            bw, bh = extents.width + 2 * pad, extents.height + 2 * pad * 0.8
            bx, by = (width - bw) / 2, top + 8 * k
            rounded(cr, bx, by, bw, bh, bh / 2)
            cr.set_source_rgba(*rgba(bg, 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*rgba(accent, 1))
            cr.set_line_width(1.5)
            cr.stroke()
            cr.move_to(bx + pad - extents.x_bearing, by + bh / 2 - extents.height / 2 - extents.y_bearing)
            cr.set_source_rgba(*rgba(fg, 1))
            cr.show_text(self.badge)
        if self.all_label and not self.tray:
            # A dashed accent frame and a tag: these are the defaults for every workspace.
            rounded(cr, 2, 2, width - 4, height - 4, 8)
            cr.set_source_rgba(*rgba(accent, 0.9))
            cr.set_dash([7 * k, 4 * k])
            cr.set_line_width(2 * k)
            cr.stroke()
            cr.set_dash([])
            cr.select_font_face("sans-serif", 0, 1)
            cr.set_font_size(max(10, 10 * k))
            extents = cr.text_extents(self.all_label)
            pad = 6 * k
            tw, th = extents.width + 2 * pad, extents.height + 1.4 * pad
            tx, ty = width - tw - 8 * k, bar + 4 * k
            rounded(cr, tx, ty, tw, th, th / 2)
            cr.set_source_rgba(*rgba(accent, 1))
            cr.fill()
            cr.move_to(tx + pad - extents.x_bearing, ty + th / 2 - extents.height / 2 - extents.y_bearing)
            cr.set_source_rgba(*rgba(bg, 1))
            cr.show_text(self.all_label)
        if self.has_focus() and not self.tray:  # keyboard: which window is chosen
            rounded(cr, 1.5, 1.5, width - 3, height - 3, 8)
            cr.set_source_rgba(*rgba(accent, 0.8))
            cr.set_line_width(2)
            cr.stroke()


    @staticmethod
    def draw_plus(cr, cx, cy, r, bg, fg, accent, alpha, hot, minus=False):
        """A round + or − (drawn lines: the font may lack the glyph)."""
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgba(*rgba(accent if hot else bg, (0.95 if hot else 0.9) * alpha))
        cr.fill_preserve()
        cr.set_source_rgba(*rgba(accent, alpha))
        cr.set_line_width(1.5)
        cr.stroke()
        arm = r * 0.5
        cr.set_source_rgba(*rgba(bg if hot else accent, alpha))
        cr.set_line_width(max(2.0, r * 0.18))
        cr.move_to(cx - arm, cy)
        cr.line_to(cx + arm, cy)
        if not minus:
            cr.move_to(cx, cy - arm)
            cr.line_to(cx, cy + arm)
        cr.stroke()

    def draw_divider(self, cr, axis, width, top, area_h, k, accent):
        """A bar in the gap the windows share (between the first window and
        the next, or the master and the others)."""
        a = self.rects[0]
        if axis == "x":
            lines = sorted({round(((r[0] + r[2]) + b[0]) / 2, 4) for r in self.rects for b in self.rects
                            if b[0] > r[0] + r[2] - 0.001 and b[0] - (r[0] + r[2]) < 0.1})
            if not lines:
                return
            for line in lines:
                cr.rectangle(line * width - 1.5 * k, top + area_h * 0.06, 3 * k, area_h * 0.88)
        else:
            b = next((r for r in self.rects if r[1] > a[1] + a[3] - 0.001), None)
            if b is None:
                return
            y = top + ((a[1] + a[3]) + b[1]) / 2 * area_h
            cr.rectangle(width * 0.06, y - 1.5 * k, width * 0.88, 3 * k)
        cr.set_source_rgba(*rgba(accent, 0.95 if self.split_line else 0.7))
        cr.fill()

    def draw_tray(self, cr, width, height, k, bg, fg, accent):
        """The drop targets of a window drag: a dashed frame (out of the
        preview = one window fewer) and a band of layout pictures."""
        rounded(cr, 1.5, 1.5, width - 3, height - 3, 8)
        cr.set_source_rgba(*rgba(fg, 0.45))
        cr.set_dash([6, 4])
        cr.set_line_width(1.5)
        cr.stroke()
        cr.set_dash([])
        band = height * self.TRAY_SHARE
        px, py, pw, ph = self.tray_pill()
        rounded(cr, px, py, pw, ph, 10 * k * 0.6)
        cr.set_source_rgba(*rgba(bg, 0.93))
        cr.fill_preserve()
        cr.set_source_rgba(*rgba(fg, 0.25))
        cr.set_line_width(1)
        cr.stroke()
        cr.select_font_face("sans-serif", 0, 0)
        cr.set_font_size(max(10, band * 0.13))
        for index, ((preset, cap, label, current), (bx, by, bw, bh)) in enumerate(zip(self.tray, self.tray_boxes())):
            hot = index == self.tray_hot
            rounded(cr, bx, by, bw, bh, 3 * k)
            cr.set_source_rgba(*rgba(accent if hot else fg, 0.22 if hot else 0.08))
            cr.fill_preserve()
            cr.set_source_rgba(*rgba(accent if hot or current else fg, 1 if hot else 0.8 if current else 0.35))
            cr.set_line_width((2.5 if hot else 1.5 if current else 1) * k * 0.7)
            if not hot and not current:
                cr.set_dash([3 * k, 2 * k])
            cr.stroke()
            cr.set_dash([])
            inset = bw * 0.06
            for rx, ry, rw, rh in preset_rects(preset, cap):
                rounded(cr, bx + inset + rx * (bw - 2 * inset), by + inset + ry * (bh - 2 * inset),
                        rw * (bw - 2 * inset), rh * (bh - 2 * inset), 1.5 * k)
                cr.set_source_rgba(*rgba(accent if hot else fg, 0.7 if hot else 0.45))
                cr.fill()
            extents = cr.text_extents(label)
            mark = extents.height * 1.3 if current else 0     # a drawn check: the toy font may lack ✓
            tx = bx + (bw - extents.width - mark) / 2 + mark
            ty = by + bh + band * 0.04 - extents.y_bearing
            cr.set_source_rgba(*rgba(accent if hot or current else fg, 1 if hot or current else 0.75))
            if current:
                size = extents.height
                cr.set_line_width(max(1.5, size * 0.18))
                cr.move_to(tx - mark, ty - size * 0.45)
                cr.line_to(tx - mark + size * 0.35, ty - size * 0.1)
                cr.line_to(tx - mark + size * 0.95, ty - size * 0.95)
                cr.stroke()
            cr.move_to(tx - extents.x_bearing, ty)
            cr.show_text(label)


def fade(widget, target: float, hide: bool = False, duration_ms: int = 180):
    """Opacity to target over duration_ms; hide=True hides it at 0."""
    token = object()
    widget.tm_fade = token
    start = widget.get_opacity()
    if target > 0 and not widget.get_visible():
        widget.set_visible(True)
    began = GLib.get_monotonic_time()

    def step():
        if getattr(widget, "tm_fade", None) is not token:
            return GLib.SOURCE_REMOVE
        done = min(1.0, (GLib.get_monotonic_time() - began) / (duration_ms * 1000))
        widget.set_opacity(start + (target - start) * done)
        if done >= 1.0:
            if hide and target == 0:
                widget.set_visible(False)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(16, step)


def load_cover(path: str, width: int, height: int):
    """A wallpaper decoded at twice the preview size (header checked first)."""
    if theme_core.image_size_problem(path):
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width * 2, height * 2, True)
    except (GLib.Error, TypeError):
        return None


class WorkspaceUndo:
    """Undo of a preview change: the workspace settings as they were, and
    the workspace laid out by them again."""

    def __init__(self, host, before, ws, after=None):
        self.host, self.before, self.ws, self.after = host, before, ws, after

    def undo(self):
        before, after, ws = self.before, self.after, self.ws

        def restore(store):
            if before is None or after is None:
                return None           # the change never got saved
            # Only what this change did goes back; later changes (also in the
            # Workspace Presets plugin) stay. Nothing left to take back: nothing to lay out.
            if not state.undo_merge(store, before, after):
                return None
            if ws <= 0:
                return None
            preset = state.preset_for(store, ws, self.host.ws_modes)
            return ("apply", ws, preset or "none") if not state.is_manual(store, ws) else None
        self.host.ws_message = _("Undone.")
        self.host.ws_change(restore)


class LiveSwapUndo:
    """Undo of a swap of two real windows: the same swap again."""

    def __init__(self, ws, rects, first, second):
        self.ws, self.rects, self.first, self.second = ws, rects, first, second

    def undo(self):
        try:
            tiling.swap_slots(self.ws, self.rects, self.second, self.first)
        except Exception as error:  # noqa: BLE001 - shown as a notification
            theme_core.notify(_("Could not undo"), str(error))


class Sections:
    """A controls box whose content goes into group cards: each heading
    (ws_heading) starts a new card; anything before the first one gets an
    untitled card."""

    def __init__(self, container):
        self.container = container
        self.card = None

    def section(self, title=None):
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.card.add_css_class("tm-group-card")
        if title:
            label = Gtk.Label(label=title.upper(), xalign=0)
            label.add_css_class("tm-ws-heading")
            self.card.append(label)
        self.container.append(self.card)
        return self.card

    def append(self, widget):
        if self.card is None:
            self.section()
        self.card.append(widget)


def ws_wrap() -> Gtk.FlowBox:
    """A row of natural-width buttons that wraps on a narrow popup."""
    row = Gtk.FlowBox()
    row.set_selection_mode(Gtk.SelectionMode.NONE)
    row.set_column_spacing(8)
    row.set_row_spacing(8)
    row.set_max_children_per_line(6)
    row.set_homogeneous(False)
    row.set_valign(Gtk.Align.START)
    return row


def clear(box):
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following
