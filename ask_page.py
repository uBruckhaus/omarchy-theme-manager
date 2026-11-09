"""The Ask AI page: a plain request ("blue borders, red text and a nice dog
wallpaper", "the font of Kanagawa", "a warm dark theme from the catalog")
answered with up to three candidates, each shown as it would look.

Every card is checked data from theme_ai.verify: the look-alike preview in the
candidate's colors (wallpaper from the real wallhaven search for its phrase,
‹ › for the next picture, no tokens), its palette and font, and Use (theme
editor prefilled, nothing saved before you save; a catalog theme opens its
install menu), Not this (gone, never suggested again in this ask) and
Refine… (only that candidate plus your words go back to the agent).
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

import theme_ai  # noqa: E402
import theme_auto  # noqa: E402
import theme_core  # noqa: E402
import theme_custom  # noqa: E402
import wallpaper_search  # noqa: E402
import theme_catalog  # noqa: E402
from i18n import _, N_  # noqa: E402
from workspaces_page import LookAlike, load_cover, preset_rects, rgba, clear, ws_wrap  # noqa: E402

EXAMPLES = (
    N_("Blue borders, red text and a nice dog wallpaper"),
    N_("A calm light theme for daytime"),
    N_("Find a warm dark theme in the catalog"),
    N_("Another font for the current theme"),
)


PROMPT_ROW = 36          # a suggestion row's height before it is drawn (CSS min-height + padding)
PROMPT_GAP = 2


def saved_prompts(settings: dict) -> list[str]:
    """The suggestions under the prompt: your own list once you saved,
    edited or removed one; until then the built-in examples."""
    value = settings.get("ask_prompts")
    return list(value) if theme_ai.valid_prompts(value) else [_(t) for t in EXAMPLES]


def enabled(settings: dict) -> bool:
    return settings.get("ask_ai") is True


DESCRIPTION = N_("Find or make a theme from a few words with a coding agent on this computer (Claude Code, "
               "Codex, pi or Hermes) or a local model, using your plan or tokens only when you "
               "click Ask; a local model uses none and nothing leaves the computer. Each Ask sends your "
               "words with the names and main colors of your installed themes, the omarchy.org theme names "
               "and your installed font names; nothing else.")
NO_HELPER = N_("No helper ready: install Claude Code, Codex, pi or Hermes and sign in, "
             "or connect pi or Hermes to a local model.")
MAPRISMA_URL = "https://plugins.omarchy.org/plugin.html?id=ubruckhaus.maprisma"
MAPRISMA_TEXT = N_("Maprisma sets up AI on Omarchy for you: online agents and their providers, and local models "
                 "with llama.cpp or Ollama, connected on one map. Every agent it sets up can answer here too.")


def helper_name_factory(ellipsize: bool) -> Gtk.SignalListItemFactory:
    """Labels for the helper drop-down's string items."""
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item):
        label = Gtk.Label(xalign=0)
        if ellipsize:
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_width_chars(8)           # at least this much when the row is narrow
            label.set_max_width_chars(40)      # the whole name when there is room
        item.set_child(label)

    def bind(_factory, item):
        text = item.get_item().get_string() if item.get_item() is not None else ""
        item.get_child().set_label(text)
        item.get_child().set_tooltip_text(text if ellipsize else None)
    factory.connect("setup", setup)
    factory.connect("bind", bind)
    return factory


def maprisma_hints(settings: dict) -> bool:
    """Settings → Maprisma hints: on unless switched off."""
    return settings.get("maprisma_hints") is not False


class AskPageMixin:
    """Mixed into ThemeManager; uses its _async, back_button, stack, decoder."""

    def maprisma_help_button(self, card_of) -> Gtk.Widget:
        """A small ? that shows or hides the Maprisma note (while Maprisma hints is on)."""
        button = Gtk.ToggleButton(icon_name="help-about-symbolic")
        button.add_css_class("flat")
        button.add_css_class("sc-small")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(_("Easy AI setup with Maprisma"))
        button.update_property([Gtk.AccessibleProperty.LABEL], [_("Easy AI setup with Maprisma")])
        button.connect("toggled", lambda b: card_of().set_visible(b.get_active()))
        return button

    def maprisma_hint(self) -> Gtk.Widget:
        """Opened by the ? (Ask AI page and Settings): Maprisma for an easy AI
        setup, online and local, from the Omarchy Plugin Marketplace."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("tm-group-card")
        title = Gtk.Label(label=_("Easy AI setup with Maprisma"), xalign=0)
        title.add_css_class("tm-ws-heading")
        card.append(title)
        text = Gtk.Label(label=_(MAPRISMA_TEXT), xalign=0, wrap=True, max_width_chars=1)
        text.add_css_class("tm-hint")
        card.append(text)
        get = Gtk.Button(label=_("Get Maprisma in the Omarchy Plugin Marketplace"))
        get.add_css_class("tm-action")
        get.set_halign(Gtk.Align.START)
        get.set_tooltip_text(MAPRISMA_URL)
        get.connect("clicked", lambda *_args: theme_core.launch_browser(MAPRISMA_URL))
        card.append(get)
        return card

    def build_ask_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.track_width(page)
        header = Gtk.Box(spacing=8)
        back = self.back_button()
        back.set_tooltip_text(_("Back to themes (Backspace, Esc)"))
        back.connect("clicked", lambda *_args: self.show_themes())
        header.append(back)
        title = Gtk.Label(label=_("Ask AI").upper(), xalign=0, hexpand=True)
        title.add_css_class("tm-section-title")
        header.append(title)
        page.append(header)
        # While Ask AI is off: what it does and a button to turn it on.
        off = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        off.add_css_class("tm-group-card")
        off_text = Gtk.Label(xalign=0, wrap=True, max_width_chars=1, label=_(DESCRIPTION))
        off.append(off_text)
        turn_on = Gtk.Button(label=_("Turn Ask AI on"))
        turn_on.add_css_class("tm-paste")
        turn_on.add_css_class("suggested")
        turn_on.set_halign(Gtk.Align.START)
        turn_on.connect("clicked", lambda *_args: (self.set_ask_ai(True), self.open_ask()))
        off.append(turn_on)
        page.append(off)
        self.ask_off_box = off
        # Easy AI setup with Maprisma: behind the ? next to the helper while
        # Settings → Maprisma hints is on (the default); off: no ? and no note.
        self.ask_maprisma = self.maprisma_hint()
        page.append(self.ask_maprisma)
        # Helper: [name ▾] … [?], and what it costs on its own small line below
        # (beside the name it squeezed into a narrow column in a small popup).
        helper_row = Gtk.Box(spacing=8)
        helper_row.append(Gtk.Label(label=_("Helper:")))
        self.ask_helper_drop = Gtk.DropDown.new_from_strings([])
        self.ask_helper_drop.set_valign(Gtk.Align.CENTER)
        self.ask_helper_drop.set_factory(helper_name_factory(ellipsize=True))     # the button: "…" when long
        self.ask_helper_drop.set_list_factory(helper_name_factory(ellipsize=False))  # the list: full names
        self.ask_helper_drop.update_property([Gtk.AccessibleProperty.LABEL], [_("Helper:")])
        self.ask_helper_drop.connect("notify::selected", lambda *_args: self.ask_helper_changed())
        helper_row.append(self.ask_helper_drop)
        helper_row.append(Gtk.Box(hexpand=True))
        self.ask_help = self.maprisma_help_button(lambda: self.ask_maprisma)
        helper_row.append(self.ask_help)
        page.append(helper_row)
        self.ask_cost = Gtk.Label(xalign=0, wrap=True, max_width_chars=1, hexpand=True)
        self.ask_cost.add_css_class("tm-hint")
        page.append(self.ask_cost)
        prompt_row = Gtk.Box(spacing=6)
        entry = Gtk.Entry(hexpand=True, placeholder_text=_("What do you want? e.g. “blue borders, red text and a dog wallpaper”"))
        entry.set_max_length(theme_ai.MAX_REQUEST)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Ask AI")])
        self.guard_text_field(entry)
        entry.connect("activate", lambda *_args: self.ask_now())
        prompt_row.append(entry)
        self.ask_entry = entry
        ask = Gtk.Button()
        ask.add_css_class("tm-paste")
        ask.add_css_class("suggested")
        face = Gtk.Box(spacing=6)
        face.append(Gtk.Image.new_from_icon_name("mail-send-symbolic"))
        face.append(Gtk.Label(label=_("Ask")))
        ask.set_child(face)
        ask.set_tooltip_text(_("Asks once; uses your plan or tokens only now."))
        ask.connect("clicked", lambda *_args: self.ask_now())
        prompt_row.append(ask)
        self.ask_button = ask
        keep = Gtk.Button.new_from_icon_name("bookmark-new-symbolic")
        keep.add_css_class("tm-action")
        keep.set_tooltip_text(_("Save as suggestion"))
        keep.update_property([Gtk.AccessibleProperty.LABEL], [_("Save as suggestion")])
        keep.connect("clicked", lambda *_args: self.ask_save_prompt())
        prompt_row.append(keep)
        page.append(prompt_row)
        # The suggestions: one card, one row each (same layout at every width),
        # two rows tall in a narrow popup and scrolling; ⠿ drags a row to reorder.
        prompts_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        prompts_card.add_css_class("tm-group-card")
        prompts_card.add_css_class("tm-ask-prompts")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.ask_examples = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=PROMPT_GAP)
        scroller.set_child(self.ask_examples)
        prompts_card.append(scroller)
        self.ask_prompt_scroller = scroller
        self.ask_prompt_reset = Gtk.Button.new_from_icon_name("edit-undo-symbolic")
        self.ask_prompt_reset.add_css_class("flat")
        self.ask_prompt_reset.set_halign(Gtk.Align.END)
        self.ask_prompt_reset.set_tooltip_text(_("Back to the built-in suggestions"))
        self.ask_prompt_reset.update_property([Gtk.AccessibleProperty.LABEL], [_("Back to the built-in suggestions")])
        self.ask_prompt_reset.connect("clicked", lambda *_args: self.ask_store_prompts(None))
        prompts_card.append(self.ask_prompt_reset)
        self.ask_prompt_edit = None
        self.ask_prompt_drag = None
        self.ask_prompt_refill = False
        self.ask_prompt_rows = []
        page.append(prompts_card)
        self.ask_fill_prompts()
        status_row = Gtk.Box(spacing=8)
        self.ask_spinner = Gtk.Spinner()
        self.ask_spinner.set_visible(False)
        status_row.append(self.ask_spinner)
        self.ask_status = Gtk.Label(xalign=0, wrap=True, max_width_chars=1, hexpand=True)
        self.ask_status.add_css_class("tm-hint")
        status_row.append(self.ask_status)
        # Clears the summary and all result cards (shown while there are some and no Ask runs).
        self.ask_clear = Gtk.Button.new_from_icon_name("edit-clear-all-symbolic")
        self.ask_clear.add_css_class("flat")
        self.ask_clear.set_valign(Gtk.Align.START)
        self.ask_clear.set_tooltip_text(_("Clear the results"))
        self.ask_clear.update_property([Gtk.AccessibleProperty.LABEL], [_("Clear the results")])
        self.ask_clear.connect("clicked", lambda *_args: self.ask_clear_results())
        self.ask_clear.set_visible(False)
        status_row.append(self.ask_clear)
        # Any status text (a summary, an error, "Nothing visible changed") can be cleared too.
        self.ask_status.connect("notify::label", lambda *_args: self.ask_update_clear())
        page.append(status_row)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        results = Gtk.FlowBox()
        results.set_selection_mode(Gtk.SelectionMode.NONE)
        results.set_homogeneous(True)
        results.set_column_spacing(12)
        results.set_row_spacing(12)
        results.set_valign(Gtk.Align.START)
        scroller.set_child(results)
        page.append(scroller)
        self.ask_results = results
        self.ask_candidates = []
        self.ask_rejected = []
        self.ask_busy = False
        self.ask_generation = 0
        return page

    # --- suggestions ----------------------------------------------------------------------

    def ask_fill_prompts(self):
        """One row per suggestion: ⠿ drags it to another place, a click on the
        text asks, ✎ edits it in place, × removes it."""
        if self.ask_prompt_drag is not None:
            self.ask_prompt_refill = True        # a rebuild mid-drag would cancel it: after the drop
            return
        self.ask_prompt_edit = None
        clear(self.ask_examples)
        self.ask_prompt_rows = []
        settings = theme_auto.settings()
        for index, text in enumerate(saved_prompts(settings)):
            row = Gtk.Box(spacing=2)
            row.add_css_class("tm-ask-prompt")
            handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
            handle.add_css_class("tm-ask-handle")
            handle.set_cursor(Gdk.Cursor.new_from_name("grab"))
            handle.set_tooltip_text(_("Drag to reorder (Alt+↑/↓)"))
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self.ask_prompt_drag_begin, index, handle)
            drag.connect("drag-update", self.ask_prompt_drag_update)
            drag.connect("drag-end", self.ask_prompt_drag_end)
            handle.add_controller(drag)
            row.append(handle)
            chip = Gtk.Button(hexpand=True)
            chip.add_css_class("flat")
            label = Gtk.Label(label=text, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
            chip.set_child(label)
            chip.set_tooltip_text(text)
            chip.connect("clicked", lambda _b, t=text: (self.ask_entry.set_text(t), self.ask_now()))
            row.append(chip)
            for icon, tip, handler in (("document-edit-symbolic", _("Edit suggestion"), self.ask_edit_prompt),
                                       ("window-close-symbolic", _("Remove suggestion"), self.ask_remove_prompt)):
                button = Gtk.Button.new_from_icon_name(icon)
                button.add_css_class("flat")
                button.set_tooltip_text(tip)
                button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
                button.connect("clicked", lambda _b, i=index, g=row, h=handler: h(i, g))
                row.append(button)
            self.ask_examples.append(row)
            self.ask_prompt_rows.append(row)
        self.ask_prompt_reset.set_visible("ask_prompts" in settings)
        self.ask_prompt_height()

    def ask_prompt_height(self):
        """Two rows tall in a narrow popup, up to four in a wide one; the rest scrolls."""
        rows = 2 if getattr(self, "card_w", 420) < 900 else 4
        first = self.ask_prompt_rows[0] if self.ask_prompt_rows else None
        row_h = first.get_height() if first is not None and first.get_height() > 0 else PROMPT_ROW
        count = min(rows, max(1, len(self.ask_prompt_rows)))
        height = count * row_h + (count - 1) * PROMPT_GAP
        self.ask_prompt_scroller.set_min_content_height(-1)      # GTK wants min <= max at every step
        self.ask_prompt_scroller.set_max_content_height(height)
        self.ask_prompt_scroller.set_min_content_height(height)
        if first is not None and first.get_height() <= 0 and not getattr(first, "tm_measuring", False):
            # Not drawn yet: measure again on the first frame it has a height.
            first.tm_measuring = True

            def measured(widget, _clock):
                if widget.get_height() <= 0:
                    return GLib.SOURCE_CONTINUE
                if widget.get_height() != row_h and widget is (self.ask_prompt_rows or [None])[0]:
                    self.ask_prompt_height()
                return GLib.SOURCE_REMOVE
            first.add_tick_callback(measured)

    # --- reorder by mouse (a gesture, not Wayland drag & drop: the popup holds the keyboard)

    def ask_prompt_drag_begin(self, gesture, x, y, index, handle):
        if self.ask_prompt_edit is not None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        found, point = handle.compute_point(self.ask_examples, Graphene.Point().init(x, y))
        if not found:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        vadj = self.ask_prompt_scroller.get_vadjustment()
        # The pointer in the scroller's view (it stays while the list scrolls under it).
        self.ask_prompt_drag = {"from": index, "to": index, "start": point.y, "view": point.y - vadj.get_value(),
                                "gesture": gesture, "timer": None}
        self.ask_prompt_rows[index].add_css_class("tm-ask-dragging")
        self.ask_prompt_drag["timer"] = GLib.timeout_add(40, self.ask_prompt_autoscroll)

    def ask_prompt_drag_update(self, _gesture, _dx, dy):
        drag = self.ask_prompt_drag
        if drag is None:
            return
        vadj = self.ask_prompt_scroller.get_vadjustment()
        drag["view"] = drag["start"] + dy - vadj.get_value()
        self.ask_prompt_target()

    def ask_prompt_autoscroll(self):
        """Near the card's top or bottom edge the list scrolls on its own, also
        while the pointer rests there."""
        drag = self.ask_prompt_drag
        if drag is None:
            return GLib.SOURCE_REMOVE
        vadj = self.ask_prompt_scroller.get_vadjustment()
        page = vadj.get_page_size()
        step = 0
        if drag["view"] < 14:
            step = -8
        elif drag["view"] > page - 14:
            step = 8
        if step:
            vadj.set_value(max(vadj.get_lower(), min(vadj.get_upper() - page, vadj.get_value() + step)))
            self.ask_prompt_target()
        return GLib.SOURCE_CONTINUE

    def ask_prompt_target(self):
        """Where the dragged row would go: a line above or below that row."""
        drag = self.ask_prompt_drag
        y = drag["view"] + self.ask_prompt_scroller.get_vadjustment().get_value()
        target = len(self.ask_prompt_rows) - 1
        for i, row in enumerate(self.ask_prompt_rows):
            ok, bounds = row.compute_bounds(self.ask_examples)
            if ok and y < bounds.get_y() + bounds.get_height() / 2:
                target = i if i <= drag["from"] else i - 1
                break
        drag["to"] = max(0, target)
        for i, row in enumerate(self.ask_prompt_rows):
            row.remove_css_class("tm-ask-drop-above")
            row.remove_css_class("tm-ask-drop-below")
        if drag["to"] != drag["from"]:
            row = self.ask_prompt_rows[drag["to"]]
            row.add_css_class("tm-ask-drop-above" if drag["to"] < drag["from"] else "tm-ask-drop-below")

    def ask_prompt_drag_end(self, _gesture, _dx, _dy):
        drag = self.ask_prompt_drag
        if drag is None:
            return
        self.ask_prompt_stop_drag()
        if drag["to"] != drag["from"]:
            self.ask_move_prompt(drag["from"], drag["to"])
        elif self.ask_prompt_refill:
            self.ask_prompt_refill = False
            self.ask_fill_prompts()

    def ask_prompt_stop_drag(self):
        drag, self.ask_prompt_drag = self.ask_prompt_drag, None
        if drag is None:
            return False
        if drag["timer"]:
            GLib.source_remove(drag["timer"])
        for row in self.ask_prompt_rows:
            for name in ("tm-ask-dragging", "tm-ask-drop-above", "tm-ask-drop-below"):
                row.remove_css_class(name)
        return True

    def ask_prompt_drag_cancel(self) -> bool:
        """Esc during a drag: the row stays where it was."""
        drag = self.ask_prompt_drag
        if not self.ask_prompt_stop_drag():
            return False
        drag["gesture"].reset()
        if self.ask_prompt_refill:
            self.ask_prompt_refill = False
            self.ask_fill_prompts()
        return True

    def ask_move_prompt(self, source, target) -> bool:
        prompts = saved_prompts(theme_auto.settings())
        if not (0 <= source < len(prompts) and 0 <= target < len(prompts)) or source == target:
            return False
        prompts.insert(target, prompts.pop(source))
        self.ask_prompt_refill = False
        self.ask_store_prompts(prompts)
        return True

    def ask_prompt_key(self, keyval, state) -> bool:
        """Alt+↑/↓ on a focused suggestion moves it; the focus follows."""
        if not state & Gdk.ModifierType.ALT_MASK or keyval not in (Gdk.KEY_Up, Gdk.KEY_Down) or \
                self.ask_prompt_edit is not None:
            return False
        focus = self.window.get_focus()
        index = next((i for i, row in enumerate(self.ask_prompt_rows)
                      if focus is not None and (focus is row or focus.is_ancestor(row))), None)
        if index is None:
            return False
        target = index + (-1 if keyval == Gdk.KEY_Up else 1)
        if self.ask_move_prompt(index, target):
            chip = self.ask_prompt_rows[target].get_first_child().get_next_sibling()
            chip.grab_focus()
            self.ask_prompt_scroller.get_vadjustment().clamp_page(
                *(lambda ok, b: (b.get_y(), b.get_y() + b.get_height()))(
                    *self.ask_prompt_rows[target].compute_bounds(self.ask_examples)))
        return True

    def ask_store_prompts(self, prompts):
        """Saves the list (None: back to the built-in examples) and redraws it."""
        self.save_setting(ask_prompts=prompts)
        self.ask_fill_prompts()

    def ask_save_prompt(self):
        text = " ".join(self.ask_entry.get_text().split())[:theme_ai.MAX_REQUEST]
        prompts = saved_prompts(theme_auto.settings())
        if not text:
            self.ask_status.set_label(_("Write a request first, then save it as a suggestion."))
        elif text in prompts:
            self.ask_status.set_label(_("That is already a suggestion."))
        elif len(prompts) >= theme_ai.MAX_PROMPTS:
            self.ask_status.set_label(_("At most {0} suggestions: remove one first.").format(theme_ai.MAX_PROMPTS))
        else:
            self.ask_store_prompts(prompts + [text])
            self.ask_status.set_label(_("Saved as suggestion."))

    def ask_remove_prompt(self, index, _group=None):
        prompts = saved_prompts(theme_auto.settings())
        if 0 <= index < len(prompts):
            del prompts[index]
            self.ask_store_prompts(prompts)

    def ask_edit_prompt(self, index, group):
        """Swaps the chip for a field: Enter or ✓ saves, Esc cancels."""
        prompts = saved_prompts(theme_auto.settings())
        if not 0 <= index < len(prompts):
            return
        if self.ask_prompt_edit is not None:
            self.ask_fill_prompts()
            return self.ask_edit_prompt(index, self.ask_prompt_group(index))
        clear(group)
        entry = Gtk.Entry(text=prompts[index], hexpand=True)
        entry.set_max_length(theme_ai.MAX_REQUEST)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Edit suggestion")])
        self.guard_text_field(entry)
        entry.connect("activate", lambda *_args: self.ask_finish_edit(True))
        group.append(entry)
        done = Gtk.Button.new_from_icon_name("object-select-symbolic")
        done.add_css_class("flat")
        done.set_tooltip_text(_("Save"))
        done.connect("clicked", lambda *_args: self.ask_finish_edit(True))
        group.append(done)
        self.ask_prompt_edit = (index, entry)
        entry.grab_focus()

    def ask_prompt_group(self, index):
        return self.ask_prompt_rows[index] if 0 <= index < len(self.ask_prompt_rows) else None

    def ask_finish_edit(self, save: bool) -> bool:
        """Ends an edit; False when none was open (Esc then does its usual)."""
        if self.ask_prompt_edit is None:
            return False
        index, entry = self.ask_prompt_edit
        text = " ".join(entry.get_text().split())[:theme_ai.MAX_REQUEST]
        prompts = saved_prompts(theme_auto.settings())
        if save and 0 <= index < len(prompts) and text != prompts[index]:
            if not text:
                del prompts[index]
            elif text in prompts:
                self.ask_status.set_label(_("That is already a suggestion."))
                return True
            else:
                prompts[index] = text
            self.ask_store_prompts(prompts)
        else:
            self.ask_fill_prompts()
        self.ask_entry.grab_focus()
        return True

    # --- open, helper ------------------------------------------------------------------------

    def open_ask(self):
        self.active_theme = None
        on = enabled(theme_auto.settings())
        self.ask_off_box.set_visible(not on)
        # Maprisma hints on: a ? by the helper opens the note; off: neither.
        hints = maprisma_hints(theme_auto.settings())
        self.ask_help.set_active(False)
        self.ask_maprisma.set_visible(False)
        self.ask_help.set_visible(hints)
        self.ask_ready = theme_ai.ready_helpers() if on else []
        names = [item["name"] + (" · " + _("on this PC") if item.get("local") else "") for item in self.ask_ready]
        model = Gtk.StringList.new(names)
        self.ask_helper_drop.set_model(model)
        chosen = theme_auto.settings().get("ask_ai_helper")
        ids = [item["id"] for item in self.ask_ready]
        if ids:
            self.ask_helper_drop.set_selected(ids.index(chosen) if chosen in ids else 0)
        self.ask_helper_changed()
        self.ask_layout()
        self.stack.set_visible_child_name("ask")
        self.ask_entry.grab_focus()

    def ask_release(self, keep_helper=None):
        """Give GPU memory back: unload the local models Ask AI loaded, except
        the one `keep_helper` runs on. Never during an Ask (it runs after it)."""
        if getattr(self, "ask_busy", False):
            self.ask_release_pending = (keep_helper,)   # a first Ask may still be loading its model
            return
        if not theme_ai.owned_models():
            return
        self.ask_release_pending = None
        keep = theme_ai.local_key(theme_ai.local_model(keep_helper))
        self._async(lambda: theme_ai.release_local(keep), lambda *_args: None)

    def ask_helper_id(self):
        ready = getattr(self, "ask_ready", [])
        index = self.ask_helper_drop.get_selected()
        return ready[index]["id"] if 0 <= index < len(ready) else None

    def ask_helper_changed(self):
        helper = self.ask_helper_id()
        self.ask_release(helper)              # another model or an online agent: the old model's memory back
        ready = getattr(self, "ask_ready", [])
        if helper is None:
            self.ask_cost.set_label(_(NO_HELPER))
            self.ask_button.set_sensitive(False)
            return
        item = next(item for item in ready if item["id"] == helper)
        if item.get("local"):
            self.ask_cost.set_label(_("On this PC: no tokens, nothing leaves it. Enter asks."))
        else:
            self.ask_cost.set_label(_("Uses your {0} plan or tokens, only when you click Ask. Enter asks.").format(item["name"]))
        self.ask_button.set_sensitive(not self.ask_busy)
        if theme_auto.settings().get("ask_ai_helper") != helper:
            self.save_setting(ask_ai_helper=helper)

    # --- asking ---------------------------------------------------------------------------

    def ask_now(self, previous=None, instruction=None):
        helper = self.ask_helper_id()
        text = (instruction if instruction is not None else self.ask_entry.get_text()).strip()
        if self.ask_busy or helper is None or not text:
            return
        self.ask_busy = True
        self.ask_update_clear()
        self.ask_button.set_sensitive(False)
        self.ask_generation += 1
        generation = self.ask_generation
        if previous is None:
            self.ask_rejected = []
        name = next(item["name"] for item in self.ask_ready if item["id"] == helper)
        self.ask_progress(True, name)
        rejected = list(self.ask_rejected)
        sent = theme_ai.for_refine(previous) if previous else None
        refining = self.ask_card_of(previous) if previous is not None else None
        self.ask_card_busy(refining, "refine", _("Refining with {0}…").format(name))

        def done(result, error):
            self.ask_card_busy(refining, "refine")
            if generation != self.ask_generation:
                return
            self.ask_busy = False
            self.ask_update_clear()
            pending, self.ask_release_pending = getattr(self, "ask_release_pending", None), None
            if pending is not None:
                self.ask_release(*pending)    # closed or switched while it was thinking
            self.ask_button.set_sensitive(True)
            self.ask_progress(False)
            if error is not None or not result:
                self.ask_status.set_label(_("No answer: {0}").format(error))
                return
            summary, candidates = result
            if previous is not None and all(theme_ai.looks_same(c, previous) for c in candidates):
                # Nothing you could see changed (verify drops such answers): the card stays as it
                # was, and its refine field gets the keys again.
                self.ask_status.set_label(_("Nothing visible changed. Try clearer words, e.g. “much darker”."))
                card = self.ask_card_of(previous)
                if card is not None:
                    self.ask_refine_row(card, previous)
                return
            if not candidates:
                self.ask_status.set_label(_("Nothing usable came back. Try other words."))
                return
            self.ask_status.set_label(summary)
            self.ask_candidates = candidates
            self.ask_show()
            if previous is not None:
                # Refine again right where you are (typing never lands in the request above).
                card = self.ask_results.get_child_at_index(0)
                if card is not None:
                    self.ask_refine_row(card.get_child(), candidates[0])
        self._async(lambda: theme_ai.ask(helper, text, sent, rejected, previous), done)

    def ask_progress(self, on, name=""):
        """A spinner and the seconds so far while the helper thinks."""
        self.ask_spinner.set_visible(on)
        if on:
            self.ask_spinner.start()
            started = GLib.get_monotonic_time()
            generation = self.ask_generation

            def tick():
                if not self.ask_busy or generation != self.ask_generation:
                    return GLib.SOURCE_REMOVE
                seconds = (GLib.get_monotonic_time() - started) // 1_000_000
                self.ask_status.set_label(_("Asking {0}… {1} s").format(name, seconds))
                return GLib.SOURCE_CONTINUE
            tick()
            GLib.timeout_add_seconds(1, tick)
        else:
            self.ask_spinner.stop()

    # --- results --------------------------------------------------------------------------

    def ask_layout(self):
        width = getattr(self, "card_w", 420)
        if getattr(self, "ask_prompt_scroller", None) is not None:
            self.ask_prompt_height()
        columns = 3 if width >= 900 else 1
        self.ask_results.set_min_children_per_line(columns)
        self.ask_results.set_max_children_per_line(columns)
        preview_w = (width - 24 * columns) // columns - 24
        self.ask_preview = (max(240, preview_w), round(max(240, preview_w) * 9 / 16))

    def ask_show(self):
        self.ask_layout()
        clear(self.ask_results)
        for candidate in self.ask_candidates:
            self.ask_results.append(self.ask_card(candidate))
        self.ask_update_clear()

    def ask_update_clear(self):
        if getattr(self, "ask_clear", None) is None or not hasattr(self, "ask_busy"):
            return                      # still building the page
        shown = bool(self.ask_candidates) or bool(self.ask_status.get_label())
        self.ask_clear.set_visible(shown and not self.ask_busy)

    def ask_clear_results(self):
        """The summary and every card go; what was rejected is forgotten too
        (a new Ask starts clean). Not while an Ask runs."""
        if self.ask_busy:
            return
        self.ask_candidates = []
        self.ask_rejected = []
        clear(self.ask_results)
        self.ask_status.set_label("")
        self.ask_update_clear()
        self.ask_entry.grab_focus()

    def ask_card(self, candidate) -> Gtk.Widget:
        width, height = self.ask_preview
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("tm-ask-card")
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        title.add_css_class("tm-ws-title")
        card.append(title)
        kind = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        kind.add_css_class("tm-hint")
        card.append(kind)
        card.tm_title, card.tm_kind = title, kind
        # Every card the same: the look-alike, its colors and font, and the
        # wallpapers of its search phrase. A catalog theme is not installed,
        # so its colors come from its omarchy.org preview picture (which is
        # also its wallpaper while it has no search phrase).
        look = LookAlike(width, height)
        look.show(preset_rects("master-left", 3), False, candidate.get("colors") or self.ws_colors(), None)
        # While Wallpaper… searches or Refine… asks: a spinner with what it does, on the preview.
        overlay = Gtk.Overlay()
        overlay.set_child(look)
        busy = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        busy.add_css_class("tm-ask-busy")
        busy.set_can_target(False)
        busy_spinner = Gtk.Spinner()
        busy.append(busy_spinner)
        busy_label = Gtk.Label()
        busy.append(busy_label)
        busy.set_visible(False)
        overlay.add_overlay(busy)
        card.append(overlay)
        card.tm_busy, card.tm_busy_spinner, card.tm_busy_label, card.tm_busy_jobs = busy, busy_spinner, busy_label, {}
        strip = Gtk.Box()
        card.append(strip)
        if candidate["kind"] == "catalog":
            def fetch(entry=candidate["entry"], wants_picture=not candidate["query"]):
                path = theme_catalog.fetch_preview(entry)
                if path is None:
                    return None
                colors = dict(theme_custom.palette_from_image(path).colors)
                return colors, (load_cover(str(path), width, height) if wants_picture else None)

            def fetched(result, _error):
                if not result:
                    return
                colors, picture = result
                candidate["colors"] = theme_ai.readable(colors)
                look.colors = candidate["colors"]
                if picture is not None:
                    look.wallpaper = picture
                look.queue_draw()
                strip.append(self.ask_palette_strip(candidate))
            self._async(fetch, fetched)
        else:
            strip.append(self.ask_palette_strip(candidate))
        walls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.append(walls)
        if candidate["query"]:
            walls.append(self.ask_wallpaper_row(candidate, look, card))
        reason = Gtk.Label(xalign=0, wrap=True, max_width_chars=1)
        reason.add_css_class("tm-hint")
        card.append(reason)
        card.tm_reason = reason
        card.tm_refine = None
        self.ask_card_texts(card, candidate)
        actions = ws_wrap()
        use = self.ask_button_with(_("Use"), "object-select-symbolic", lambda b: self.ask_use(candidate, b))
        use.add_css_class("tm-action-primary")
        actions.append(use)
        actions.append(self.ask_button_with(_("Not this"), "window-close-symbolic",
                                            lambda _b: self.ask_reject(candidate)))
        actions.append(self.ask_button_with(_("Refine…"), "document-edit-symbolic",
                                            lambda _b: self.ask_refine_row(card, candidate)))
        actions.append(self.ask_button_with(_("Wallpaper…"), "image-x-generic-symbolic",
                                            lambda _b: self.ask_wallpaper_search(walls, candidate, look, card)))
        card.append(actions)
        return card

    @staticmethod
    def ask_card_busy(card, job, text=None):
        """Start (text) or end (None) one job on a card; the newest running
        job's text shows. Safe for a card that is gone or has no overlay."""
        jobs = getattr(card, "tm_busy_jobs", None) if card is not None else None
        if jobs is None:
            return
        jobs.pop(job, None)
        if text is not None:
            jobs[job] = text
        if jobs:
            card.tm_busy_label.set_label(list(jobs.values())[-1])
            card.tm_busy.set_visible(True)
            card.tm_busy_spinner.start()
        else:
            card.tm_busy_spinner.stop()
            card.tm_busy.set_visible(False)

    def ask_card_of(self, candidate):
        index = next((i for i, c in enumerate(self.ask_candidates) if c is candidate), None)
        child = self.ask_results.get_child_at_index(index) if index is not None else None
        return child.get_child() if child is not None else None

    def ask_card_texts(self, card, candidate):
        """Title, subtitle, reason and the Refine example of a card. After your
        own wallpaper search the title is that search (a new theme takes it as
        its name), the theme's own name moves to the subtitle and the reason
        says it is your search: nothing on the card still talks about the
        picture the agent first had in mind."""
        query = candidate.get("query") or ""
        mine = bool(candidate.get("query_by_user") and query)
        base = candidate.get("base_name", "")
        # A new theme is new for you: the theme the agent started from is internal.
        kind = {"catalog": _("From omarchy.org"), "new": _("New theme"),
                "edit": _("Edit {0}").format(base)}[candidate["kind"]]
        if mine:
            title = query[:1].upper() + query[1:]
            agent_name = candidate.setdefault("agent_name", candidate["name"])
            if candidate["kind"] == "new":
                # The editor's name, within its rules; else the agent's name stays.
                name = title[:theme_custom.MAX_NAME].strip()
                try:
                    theme_custom.slug_for(name)
                    candidate["name"] = title = name
                except ValueError:
                    candidate["name"] = title = agent_name
            elif candidate["kind"] == "catalog":
                kind = "{0} · {1}".format(kind, candidate["name"])       # the theme's name stays
            reason = _("Wallpaper: your search “{0}”").format(query)
        else:
            title, reason = candidate["name"], candidate.get("reason") or ""
        card.tm_title.set_label(title)
        card.tm_kind.set_label(kind)
        card.tm_reason.set_label(reason)
        card.tm_reason.set_visible(bool(reason))
        if card.tm_refine is not None:
            card.tm_refine.set_placeholder_text(self.ask_refine_example(candidate))

    @staticmethod
    def ask_refine_example(candidate) -> str:
        query = candidate.get("query") or ""
        if query:
            return _("How should it change? e.g. “warmer”, “another picture like {0}”").format(query)
        return _("How should it change? e.g. “warmer”, “another wallpaper”")

    def ask_button_with(self, label, icon, on_click) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("tm-action")
        face = Gtk.Box(spacing=6)
        face.append(Gtk.Image.new_from_icon_name(icon))
        face.append(Gtk.Label(label=label))
        button.set_child(face)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", on_click)
        return button

    def ask_palette_strip(self, candidate) -> Gtk.Widget:
        """Background and text, the eight terminal colors and the font: what
        the preview's windows can't show clearly."""
        row = Gtk.Box(spacing=4)
        colors = candidate["colors"]
        frame = colors.get("foreground", "#cdd6f4")
        # Background and text first (outlined, so black on black still shows), then the terminal colors.
        for key in ("background", "foreground") + tuple(theme_custom.PALETTE_KEYS):
            value = colors.get(key)
            if not value:
                continue
            swatch = Gtk.DrawingArea()
            swatch.set_content_width(16)
            swatch.set_content_height(16)

            def paint(_a, cr, w, h, v=value, outline=key in ("background", "foreground")):
                cr.set_source_rgba(*rgba(v, 1))
                cr.rectangle(0, 0, w, h)
                cr.fill()
                if outline:
                    cr.set_source_rgba(*rgba(frame, 0.5))
                    cr.set_line_width(1)
                    cr.rectangle(0.5, 0.5, w - 1, h - 1)
                    cr.stroke()
            swatch.set_draw_func(paint)
            swatch.set_tooltip_text(f"{theme_custom.label(key)} {value}")
            if key == "foreground":
                swatch.set_margin_end(6)
            row.append(swatch)
        text = candidate["font"] or _("Keep the current font")
        if candidate.get("windows"):
            text += " · " + _(self.WINDOW_LABELS[candidate["windows"]])
        font = Gtk.Label(label=text, xalign=0,
                         ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, hexpand=True)
        font.add_css_class("tm-hint")
        font.set_margin_start(6)
        row.append(font)
        return row

    def ask_wallpaper_row(self, candidate, look, card=None) -> Gtk.Widget:
        """The phrase goes to the wallhaven search only; ‹ › show its pictures."""
        row = Gtk.Box(spacing=6)
        prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        next_btn = Gtk.Button(icon_name="go-next-symbolic")
        for button, text in ((prev_btn, _("Previous wallpaper")), (next_btn, _("Next wallpaper"))):
            button.add_css_class("flat")
            button.set_tooltip_text(text)
            button.update_property([Gtk.AccessibleProperty.LABEL], [text])
        label = Gtk.Label(label=_("Searching wallpapers for “{0}”…").format(candidate["query"]), xalign=0,
                          ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, hexpand=True)
        label.add_css_class("tm-hint")
        row.append(prev_btn)
        row.append(label)
        row.append(next_btn)
        state = {"results": [], "index": 0}
        width, height = self.ask_preview

        query = candidate["query"]          # this row's search; a newer row replaces it
        token = candidate["search_token"] = object()

        def current() -> bool:
            return candidate.get("search_token") is token

        def show(index):
            results = state["results"]
            if not results or not current():
                return
            state["index"] = index % len(results)
            item = results[state["index"]]
            candidate["wallpaper"] = item
            label.set_label(f"“{state.get('phrase') or query}” · {state['index'] + 1}/{len(results)}")

            job = ("picture", object())         # one per picture: quick ‹ › clicks each end their own
            self.ask_card_busy(card, job, _("Loading the picture…"))

            def picture(pix, _error):
                self.ask_card_busy(card, job)
                if pix is not None and current() and candidate.get("wallpaper") is item:
                    look.wallpaper = pix
                    look.queue_draw()

            def thumb(path, _error):
                if path and current():
                    self._async(lambda: load_cover(str(path), width, height), picture)
                else:
                    self.ask_card_busy(card, job)
            self._async(lambda: wallpaper_search.fetch_thumb(item), thumb)

        def found(result, error):
            self.ask_card_busy(card, ("search", token))
            if not current():
                return                       # another search took over meanwhile
            if error is not None or not result or not result[0]:
                label.set_label(_("No wallpapers found for “{0}”.").format(query))
                return
            state["results"], _last, state["phrase"] = result
            candidate["found_query"] = state["phrase"]
            # The picture chosen before (kept by a refine) stays chosen.
            chosen = (candidate.get("wallpaper") or {}).get("id")
            show(next((i for i, item in enumerate(state["results"]) if chosen and item.get("id") == chosen), 0))
        prev_btn.connect("clicked", lambda *_args: show(state["index"] - 1))
        next_btn.connect("clicked", lambda *_args: show(state["index"] + 1))
        def search():
            # Long phrases often find nothing on wallhaven: then fewer words.
            words = query.split()
            tries = []
            for count in (len(words), 3, 2, 1):
                phrase = " ".join(words[:count])
                if phrase and phrase not in tries:
                    tries.append(phrase)
            for phrase in tries:
                results, last = wallpaper_search.search(phrase, 1)
                if results:
                    # The phrase that found pictures; your words stay the query (Refine, title).
                    return results, last, phrase
            return [], 0, query
        self.ask_card_busy(card, ("search", token), _("Searching wallpapers…"))
        self._async(search, found)
        return row

    def ask_wallpaper_search(self, walls, candidate, look, card=None):
        """Your own words for this card's wallpaper: the wallhaven search
        only, no tokens; ‹ › then show its pictures in the preview."""
        if getattr(walls, "tm_entry", None) is not None:
            # Open already: new words in it are searched, else the button
            # closes it again (a second click always does something).
            entry = walls.tm_entry
            if wallpaper_search.clean_query(entry.get_text()) not in ("", candidate.get("query")):
                walls.tm_run()
            else:
                walls.remove(walls.tm_row)
                walls.tm_entry = walls.tm_row = walls.tm_run = None
            return
        row = Gtk.Box(spacing=6)
        entry = Gtk.Entry(hexpand=True, text=candidate.get("query") or "",
                          placeholder_text=_("Search wallpapers, e.g. “husky snow”"))
        entry.set_max_length(wallpaper_search.MAX_QUERY)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Wallpaper…")])
        self.guard_text_field(entry)
        go = Gtk.Button(icon_name="system-search-symbolic")
        go.add_css_class("tm-paste")
        go.set_tooltip_text(_("Search wallpapers (no tokens)"))
        go.update_property([Gtk.AccessibleProperty.LABEL], [_("Search wallpapers (no tokens)")])

        def run(*_args):
            query = wallpaper_search.clean_query(entry.get_text())
            if not query:
                return
            candidate["query"] = query
            candidate["query_by_user"] = True
            candidate.pop("found_query", None)
            candidate.pop("wallpaper", None)
            if card is not None:
                self.ask_card_texts(card, candidate)
            child = walls.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                if child is not row:
                    walls.remove(child)
                child = following
            walls.append(self.ask_wallpaper_row(candidate, look, card))
        entry.connect("activate", run)
        go.connect("clicked", run)
        row.append(entry)
        row.append(go)
        walls.prepend(row)
        walls.tm_entry, walls.tm_row, walls.tm_run = entry, row, run
        entry.grab_focus()

    def ask_reject(self, candidate):
        self.ask_rejected.append(candidate.get("slug") or candidate.get("agent_name") or candidate.get("name") or "")
        if candidate.get("query") and not candidate.get("query_by_user"):
            self.ask_rejected.append(candidate["query"])          # your own search is never "rejected"
        self.ask_candidates = [c for c in self.ask_candidates if c is not candidate]
        self.ask_show()

    def ask_refine_row(self, card, candidate):
        if getattr(card, "tm_refine", None) is not None:
            card.tm_refine.grab_focus()
            return
        row = Gtk.Box(spacing=6)
        entry = Gtk.Entry(hexpand=True, placeholder_text=self.ask_refine_example(candidate))
        entry.set_max_length(theme_ai.MAX_REQUEST)
        self.guard_text_field(entry)
        go = Gtk.Button(icon_name="mail-send-symbolic")
        go.add_css_class("tm-paste")
        go.set_tooltip_text(_("Refine…"))
        go.update_property([Gtk.AccessibleProperty.LABEL], [_("Refine…")])
        send = lambda *_args: entry.get_text().strip() and self.ask_now(candidate, entry.get_text())
        entry.connect("activate", send)
        go.connect("clicked", send)
        row.append(entry)
        row.append(go)
        card.append(row)
        card.tm_refine = entry
        entry.grab_focus()

    # --- use ------------------------------------------------------------------------------

    def ask_use(self, candidate, button):
        if candidate["kind"] == "catalog":
            self.show_catalog_menu(button, candidate["entry"])   # install, install and apply, page
            return
        base = next((t for t in theme_core.discover_themes() if t.slug == candidate["base"]), None)
        if base is None:
            theme_core.notify(_("Theme missing"), candidate["base"])
            return
        edit = candidate["kind"] == "edit" and theme_custom.is_custom(base)
        self.open_editor(base, edit=edit)        # a system theme is never changed: a copy
        if self.ed is None:
            return
        self.ed_return_page = "ask"              # Back comes here again, results kept
        if not edit and candidate["kind"] == "new":
            # Its base theme is internal: no "from Tokyo Night", no "Also Tokyo Night's wallpapers".
            self.ed["from_ask"] = True
            # The card's name comes over, as the editor's title and in the name field.
            self.ed_title.set_label(theme_core.plain_text(candidate["name"]) if candidate["name"] else _("New theme"))
        for key, value in candidate["colors"].items():
            try:
                self.ed["palette"].set(key, value, self.ed["edited"])
            except (KeyError, ValueError):
                continue
        if candidate["font"]:
            self.ed["font"] = candidate["font"]
        if candidate.get("windows"):
            self.ed["windows"] = candidate["windows"]
        # The picture shown on the card comes along: the sample shows it,
        # saving downloads it into the theme (Save and apply makes it the
        # background), and the base theme's own wallpapers become an extra.
        self.ed["ask_wallpaper"] = candidate.get("wallpaper")
        if self.ed["ask_wallpaper"] is not None and not edit:
            self.ed["wallpapers"] = False
        self.fill_editor()
        if not edit and candidate["kind"] == "new" and candidate["name"]:
            try:
                self.ed_name.set_text(theme_custom.unique_name(candidate["name"]))
            except ValueError:
                pass          # keep the editor's own suggestion
