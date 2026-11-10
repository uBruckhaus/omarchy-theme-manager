"""Speak instead of typing: a microphone button beside the question fields.

It works like Omarchy's own dictation key: a click puts the cursor at the end of the field and runs
`omarchy-dictation toggle` (the backend chosen in Setup › Defaults › Dictation, e.g. Superwhisper or
voxtype), which starts dictation, or stops it when it runs; the backend writes what is said at the
cursor and shows its own recording indicator. Theme Manager keeps no recording state, records
nothing itself and sends nothing anywhere; where the words are recognised (on this PC or a cloud
service) is the backend's own setting. With no backend set up yet, a click explains how to set one
up; away from Omarchy there is no button."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import subprocess
import time

from gi.repository import GLib, Gtk

from i18n import _

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_cache = {"at": 0.0, "backend": None}
OUTPUT_LIMIT = 64 * 1024          # bytes read from a helper's answer; more ends it


def read_output(argv: list, timeout: float = 3.0, limit: int = OUTPUT_LIMIT) -> tuple[int, str] | None:
    """Run argv (no shell) and read at most `limit` bytes of its output within `timeout` seconds;
    None when it fails, takes too long or says too much (then it is ended). Undecodable bytes are
    replaced, so a broken helper cannot raise here."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    data = bytearray()
    deadline = time.monotonic() + timeout
    sel = selectors.DefaultSelector()
    try:
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not sel.select(left):
                raise TimeoutError
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            data += chunk
            if len(data) > limit:
                raise OverflowError
        code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        return code, data.decode("utf-8", errors="replace")
    except (OSError, ValueError, TimeoutError, OverflowError, subprocess.SubprocessError):
        proc.kill()
        try:
            proc.wait(timeout=1)
        except subprocess.SubprocessError:
            pass
        return None
    finally:
        sel.close()
        proc.stdout.close()


SETUP_ROUTE = "setup.default.dictation"      # Omarchy's menu: Setup › Defaults › Dictation


def supported() -> bool:
    """The desktop has a dictation command to call (Omarchy)."""
    return bool(shutil.which("omarchy-dictation") and shutil.which("omarchy-default-dictation"))


def backend(fresh: bool = False) -> str | None:
    """The configured dictation backend whose own program is installed (as Omarchy's key bindings
    check it), e.g. "superwhisper", or None; asked at most every 30 s unless `fresh`."""
    now = time.monotonic()
    if not fresh and now - _cache["at"] < 30:
        return _cache["backend"]
    found = None
    if shutil.which("omarchy-dictation") and shutil.which("omarchy-default-dictation"):
        answer = read_output(["omarchy-default-dictation"], limit=256)
        if answer is not None:
            code, text = answer
            name = text.strip()
            if code == 0 and NAME.match(name) and shutil.which(f"omarchy-dictation-{name}") and shutil.which(name):
                found = name
    _cache.update(at=now, backend=found)
    return found


def display_name(name: str) -> str:
    return {"superwhisper": "Superwhisper", "voxtype": "Voxtype"}.get(name, name)


def open_setup():
    """Omarchy's menu at Setup › Defaults › Dictation (argv, not waited for)."""
    if supported() and shutil.which("omarchy-menu"):
        subprocess.Popen(["omarchy-menu", "summon", SETUP_ROUTE], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


SETUP_TITLE = "Set up dictation"
SETUP_TEXT = ("To speak instead of typing, choose a dictation program first: in Omarchy's menu under Setup › "
              "Defaults › Dictation, pick Superwhisper or Voxtype; Omarchy installs and sets it up. Then click "
              "the microphone again.")
SETUP_BUTTON = "Open dictation setup"
NOT_SET_UP_TIP = "Speak instead of typing: no dictation program is set up yet. Click for how to set one up."


def toggle():
    """omarchy-dictation toggle, as the dictation key does: argv, no shell, not waited for."""
    if supported():
        subprocess.Popen(["omarchy-dictation", "toggle"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


def setup_popover(button: Gtk.Widget):
    """The hint when no dictation program is set up, as a popover at the microphone (the popup opens
    no windows of its own), with a button to Omarchy's dictation menu."""
    popover = Gtk.Popover()
    popover.add_css_class("tm-popover")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8,
                  margin_start=10, margin_end=10)
    title = Gtk.Label(label=_(SETUP_TITLE), xalign=0)
    title.add_css_class("tm-section-title")
    box.append(title)
    text = Gtk.Label(label=_(SETUP_TEXT), xalign=0, wrap=True, max_width_chars=42)
    box.append(text)
    go = Gtk.Button(label=_(SETUP_BUTTON), halign=Gtk.Align.START)
    go.add_css_class("tm-paste")
    go.add_css_class("suggested")
    go.connect("clicked", lambda *_a: (popover.popdown(), open_setup()))
    box.append(go)
    popover.set_child(box)
    popover.set_parent(button)
    popover.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
    popover.popup()


def mic_button(field) -> Gtk.Button | None:
    """The microphone for a text field (Gtk.Entry or Gtk.TextView); None away from Omarchy. It works
    like the dictation key; with no dictation program set up, a click shows how to set one up."""
    if not supported():
        return None
    button = Gtk.Button(valign=Gtk.Align.CENTER)
    button.set_child(Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic"))
    button.add_css_class("tm-paste")
    button.add_css_class("tm-dictate")

    def tip(name):
        text = _(NOT_SET_UP_TIP) if name is None else _(
            "Speak instead of typing: {0} writes what you say at the cursor. Click again to stop.").format(
            display_name(name))
        button.set_tooltip_text(text)
        button.update_property([Gtk.AccessibleProperty.LABEL], [text])
    tip(backend())

    def clicked(btn):
        name = backend(fresh=True)                      # set up meanwhile? ask again
        tip(name)
        if name is None:
            setup_popover(btn)
            return
        field.grab_focus()                              # the words go where the cursor is
        if isinstance(field, Gtk.TextView):
            field.get_buffer().place_cursor(field.get_buffer().get_end_iter())
        else:
            field.set_position(-1)
        toggle()
    button.connect("clicked", clicked)
    return button
