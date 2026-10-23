"""The Information page's cards from the marketplace catalog (plugin_catalog, the shared reader the
QML plugins run as a script; here it is imported and runs on a thread when the page opens).

A plugin this version knows keeps its glyph and translated one-liner (more_plugins.PLUGINS, in that
order); one it does not know yet gets a generic glyph and the first sentence of its catalog
description. Without a list (nothing read yet, offline), the built-in list is shown.
"""

from __future__ import annotations

import more_plugins
import plugin_catalog

OWN_ID = "ubruckhaus.theme-manager"
NEW_GLYPH = "\U000F03D6"                   # a package, for a plugin this version has no glyph for


def cache_path():
    return plugin_catalog.cache_path(OWN_ID)


def cached() -> list:
    """The plugins from the cache, without asking the network (for building the page)."""
    return plugin_catalog.load(cache_path()).get("plugins", [])


def refresh() -> list:
    """The plugins after asking the marketplace if the last attempt is over an hour old (blocking)."""
    return plugin_catalog.refresh(cache_path()).get("plugins", [])


def cards(plugins: list) -> list:
    """(id, name, glyph, text) for more_plugins.build_cards."""
    if not plugins:
        return list(more_plugins.PLUGINS)
    known = {p[0] for p in more_plugins.PLUGINS}
    listed = {p["id"]: p for p in plugins}
    out = [(pid, listed[pid]["name"] or name, glyph, text) for pid, name, glyph, text in more_plugins.PLUGINS
           if pid in listed]
    out += [(p["id"], p["name"], NEW_GLYPH, plugin_catalog.first_sentence(p["description"]) or p["name"])
            for p in plugins if p["id"] not in known]
    return out
