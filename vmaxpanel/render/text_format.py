"""Formatting a text widget's value: template, humanize and colour.

Split out of widgets.py because it touches neither PIL nor fonts: it is pure text
and colour rules, the half of that module that can be read and tested without any
drawing at all. widgets.py re-exports it, so anybody already importing
`widgets.format_value` keeps working.
"""
import math

from ..layout import model
from ..metrics import UNAVAILABLE

DASH = "--"


def _num(value):
    """A number usable for a bar/arc/graph fraction or for evaluating a colour
    rule. It rejects bool (isinstance(True, int) is True in Python) and also
    NaN/Inf: a failed sensor returning nan must not be treated as "just some
    number", because max(0.0, min(1.0, nan)) gives 1.0 given how Python compares
    with NaN, and a garbage reading would end up drawn as if it were a real 100%.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def human_rate(bps) -> str:
    """1258291 -> "1.2 MB/s": something a str.format template cannot reproduce,
    because there is no way to pick the unit by magnitude inside a single
    field."""
    if bps >= 1048576:
        return f"{bps / 1048576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def human_bytes(b) -> str:
    """3221225472 -> "3.0 GiB": the same problem as human_rate() but in binary
    storage units instead of a rate per second."""
    for unit, div in (("GiB", 1073741824), ("MiB", 1048576), ("KiB", 1024)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b:.0f} B"


def human_duration(segundos) -> str:
    """33098 -> "9h 11m": a duration the way a person would say it.

    Two units at most, and always the two most significant: "1d 1h" rather than
    "1d 1h 0m 0s". An uptime in raw seconds tells nobody anything on a panel
    glanced at sideways.
    """
    s = int(max(0, segundos))
    d, resto = divmod(s, 86400)
    h, resto = divmod(resto, 3600)
    m, seg = divmod(resto, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {seg}s"
    return f"{seg}s"


HUMANIZERS = {"rate": human_rate, "bytes": human_bytes,
              "duration": human_duration}


def format_value(w: model.TextWidget, value) -> str:
    """Applies humanize when it applies, otherwise w.format.

    An absent value leaves "--" (see _dashed): with humanize active there is no
    template whose suffix could be preserved, so a bare DASH is returned. An
    unknown humanize mode (an extra defence: schema.validate() already rejects it,
    but format_value does not revalidate) falls back to the normal format instead
    of failing.
    """
    humanizer = HUMANIZERS.get(getattr(w, "humanize", "none"))
    if humanizer is not None:
        v = _num(value)
        return DASH if v is None else humanizer(v)
    if value is None or value is UNAVAILABLE:
        return _dashed(w.format)
    try:
        return w.format.format(value)
    except Exception:
        return _dashed(w.format)


def _dashed(fmt: str) -> str:
    """"{:.0f} MHz" -> "-- MHz": replaces the field with DASH without losing the
    rest of the template."""
    try:
        return fmt.format(_Dash())
    except Exception:
        return DASH


class _Dash:
    """A stand-in that formats itself as "--" whatever the format_spec (width,
    alignment, precision, presentation type).

    __repr__ has to return DASH as well: a "{!r}" or "{!s}" conversion in the
    template calls repr()/str() BEFORE invoking __format__ (and object.__str__
    delegates to __repr__, so defining just one already covers !r, !s and !a).
    Without this, a shared layout with a format like "{!r} MHz" would leak
    Python's default repr ("<...widgets._Dash object at 0x...>") onto the panel
    instead of "-- MHz".
    """

    def __format__(self, spec):
        return DASH

    def __repr__(self):
        return DASH


def color_for(w: model.TextWidget, value) -> str:
    v = _num(value)
    if v is not None:
        for rule in w.rules:
            if rule.matches(v):
                return rule.color
    return w.color
