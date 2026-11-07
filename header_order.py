"""Validated order and insertion math for the popup's header buttons."""

DEFAULT = ("import", "hidden", "presets", "ask", "info")


def normalize(value) -> list[str]:
    """Keep known IDs once, then append buttons added since the order was saved."""
    if not isinstance(value, list):
        return list(DEFAULT)
    result = []
    for item in value:
        if isinstance(item, str) and item in DEFAULT and item not in result:
            result.append(item)
    return result + [item for item in DEFAULT if item not in result]


def insertion_index(bounds: list[tuple[float, float]], x: float) -> int:
    """Return the gap under x from ordered (left, width) button bounds."""
    for index, (left, width) in enumerate(bounds):
        if x < left + width / 2:
            return index
    return len(bounds)


def move_to_visible_slot(order: list[str], dragged: str,
                         visible: list[str], slot: int) -> list[str]:
    """Move a button to a visible gap, preserving hidden buttons' order."""
    if dragged not in order or dragged not in visible or not 0 <= slot <= len(visible):
        return list(order)
    current_slot = visible.index(dragged)
    if slot in (current_slot, current_slot + 1):
        return list(order)
    remaining = [item for item in order if item != dragged]
    if slot < len(visible):
        anchor = visible[slot]
        if anchor == dragged:
            return list(order)
        index = remaining.index(anchor)
    else:
        anchor = visible[-1]
        index = remaining.index(anchor) + 1 if anchor != dragged else len(remaining)
    remaining.insert(index, dragged)
    return remaining
