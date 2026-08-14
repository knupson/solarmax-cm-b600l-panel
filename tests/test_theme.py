"""The editor's colour palette and light/dark detection.

Pure logic on purpose: deciding the palette and reading the system preference has
nothing to do with Tk, so it is tested without opening a window. What needs Tk --
handing the colours to ttk -- is a thin layer on top.
"""
import pytest

from vmaxpanel import theme


def test_both_palettes_define_every_role():
    """A missing key is a widget painted with whatever ttk had lying around,
    which is exactly the mismatched look this replaces."""
    for dark in (False, True):
        p = theme.palette(dark)
        for role in theme.ROLES:
            assert role in p, f"{role} missing in {'dark' if dark else 'light'}"
            assert p[role].startswith("#") and len(p[role]) == 7, p[role]


def test_dark_and_light_are_really_different():
    assert theme.palette(True)["bg"] != theme.palette(False)["bg"]
    assert theme.palette(True)["text"] != theme.palette(False)["text"]


def _luminance(hex_color):
    """Relative luminance, WCAG. Only needs the sRGB gamma curve."""
    canal = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canal]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("dark", [False, True])
def test_text_is_readable_on_its_background(dark):
    """A palette that looks fine to whoever picked it can still be unreadable.
    4.5:1 is the WCAG AA threshold for body text, and it is a number rather than
    an opinion."""
    p = theme.palette(dark)
    for fondo in ("bg", "surface"):
        c = _contrast(p["text"], p[fondo])
        assert c >= 4.5, f"text on {fondo} is {c:.1f}:1 ({'dark' if dark else 'light'})"


@pytest.mark.parametrize("dark", [False, True])
def test_muted_text_is_still_legible(dark):
    """The hints and secondary labels use `muted`. 3:1 is the AA threshold for
    large text; below that it is decoration, not information."""
    p = theme.palette(dark)
    c = _contrast(p["muted"], p["bg"])
    assert c >= 3.0, f"muted is {c:.1f}:1 ({'dark' if dark else 'light'})"


def test_the_selection_is_readable_too():
    """A selected row that turns unreadable is worse than no highlight: it hides
    exactly the row the user is working on."""
    for dark in (False, True):
        p = theme.palette(dark)
        c = _contrast(p["selection_text"], p["selection"])
        assert c >= 4.5, f"selection is {c:.1f}:1 ({'dark' if dark else 'light'})"


# --- reading the system preference ---


def test_windows_dark_mode_is_detected():
    """AppsUseLightTheme is 0 when Windows is in dark mode. The name is the
    opposite of what it does, which is exactly why this has a test."""
    assert theme.is_dark(leer=lambda: 0) is True
    assert theme.is_dark(leer=lambda: 1) is False


def test_an_unreadable_preference_falls_back_to_light():
    """Another Windows version, a locked-down registry, or not Windows at all.
    Light is the safer default: a light app on a dark desktop is jarring, a dark
    app that should have been light is unreadable on some screens."""
    def explota():
        raise OSError("no such key")

    assert theme.is_dark(leer=explota) is False


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("role", ["ok", "warn", "error"])
def test_status_colours_are_readable_on_the_window(dark, role):
    """The status bar said "no errors" in a dark green that was hardcoded for a
    light window. On a dark one it was nearly invisible -- and the status bar is
    where the editor answers "did that work?"."""
    p = theme.palette(dark)
    c = _contrast(p[role], p["bg"])
    assert c >= 4.5, f"{role} is {c:.1f}:1 ({'dark' if dark else 'light'})"
