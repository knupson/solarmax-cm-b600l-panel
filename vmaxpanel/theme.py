"""The editor's look: one palette, light or dark, handed to ttk.

**Why `clam` and not the native theme.** On Windows, ttk defaults to `vista`,
which draws its widgets with the OS theme engine and *ignores* almost every
colour you configure. That is why the editor looked like a different decade: the
parts that are ttk followed Windows, the parts that are classic Tk did not, and
nothing could be restyled. `clam` is drawn by Tk itself, so every colour here
actually lands.

**Why the palette is a plain dict.** Deciding colours is not a Tk problem, so it
is a pure function that can be tested -- including the contrast between text and
its background, which is a number rather than an opinion.

Nothing new is imported: `winreg` and `ctypes` ship with Python.
"""
import ctypes
import sys

# Every role a widget can need. Kept explicit so a palette missing one fails a
# test instead of silently leaving some control painted by whatever ttk had.
ROLES = ("bg", "surface", "field", "text", "muted", "accent", "border",
         "selection", "selection_text", "ok", "warn", "error")

_LIGHT = {
    "bg": "#F3F3F3",          # the window
    "surface": "#FFFFFF",     # panels that sit on it: the tree, the preview
    "field": "#FFFFFF",       # entries and combos
    "text": "#1A1A1A",
    "muted": "#5C5C5C",       # hints and secondary labels
    "accent": "#0F62C4",      # focus, selection, the active tab
    "border": "#C8C8C8",
    "selection": "#0F62C4",
    "selection_text": "#FFFFFF",
    # The status bar. Contrast against `bg` is checked by a test rather than
    # eyeballed: the previous green was picked for a light window and became
    # nearly invisible the moment there was a dark one.
    "ok": "#106B2F",
    "warn": "#8A4B00",
    "error": "#B3261E",
}

_DARK = {
    "bg": "#1E1F22",
    "surface": "#26282C",
    "field": "#2E3035",
    "text": "#E8E8EA",
    "muted": "#A8ADB6",
    "accent": "#4C9DF5",
    "border": "#3A3D44",
    "selection": "#2F5D96",
    "selection_text": "#FFFFFF",
    "ok": "#7EE0A0",
    "warn": "#F0B23C",
    "error": "#FF8A80",
}

# HKCU. 0 means dark -- the value is named for the light theme, so the reading is
# inverted, which is the whole reason is_dark() exists instead of an inline call.
_CLAVE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_VALOR = "AppsUseLightTheme"


def palette(dark: bool) -> dict:
    return dict(_DARK if dark else _LIGHT)


def _leer_preferencia() -> int:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE) as k:
        return winreg.QueryValueEx(k, _VALOR)[0]


def is_dark(leer=None) -> bool:
    """Whether Windows is set to dark mode. False for anything unreadable.

    Light is the safer fallback: a light app on a dark desktop is jarring, but a
    dark app on a machine that never asked for one can be unreadable on a screen
    calibrated for daylight. `leer` is injected so the decision is testable
    without touching the registry.
    """
    leer = leer or _leer_preferencia
    try:
        return int(leer()) == 0
    except Exception:
        return False


def _barra_de_titulo_oscura(root) -> bool:
    """Paints the window's title bar dark. Best effort.

    Without this the window is dark and its title bar is white, which looks more
    broken than not theming at all. DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on
    current Windows 10/11 and was 19 on early builds, so both are tried.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        valor = ctypes.c_int(1)
        for atributo in (20, 19):
            ok = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, atributo, ctypes.byref(valor), ctypes.sizeof(valor))
            if ok == 0:
                return True
    except Exception:
        pass
    return False


def apply(root, ttk, dark=None) -> dict:
    """Themes `root` and returns the palette that was used.

    `ttk` is passed in rather than imported so this module keeps working on a
    machine without Tk -- the same reason editor.py imports Tkinter inside the
    window class.
    """
    dark = is_dark() if dark is None else dark
    p = palette(dark)
    estilo = ttk.Style(root)
    # clam and not the native theme: see the module docstring. If it is missing
    # (a stripped Tk), whatever is active stays and the colours below mostly do
    # not land -- but nothing breaks.
    if "clam" in estilo.theme_names():
        estilo.theme_use("clam")

    root.configure(background=p["bg"])
    estilo.configure(".", background=p["bg"], foreground=p["text"],
                     fieldbackground=p["field"], bordercolor=p["border"],
                     lightcolor=p["border"], darkcolor=p["border"],
                     focuscolor=p["accent"], troughcolor=p["surface"],
                     insertcolor=p["text"])

    estilo.configure("TFrame", background=p["bg"])
    estilo.configure("TLabel", background=p["bg"], foreground=p["text"])
    estilo.configure("TLabelframe", background=p["bg"], bordercolor=p["border"])
    estilo.configure("TLabelframe.Label", background=p["bg"], foreground=p["muted"])

    # padding, not just colour: the cramped buttons are half of why it looked
    # like an old dialog box.
    estilo.configure("TButton", background=p["surface"], foreground=p["text"],
                     bordercolor=p["border"], padding=(10, 5), relief="flat")
    estilo.map("TButton",
               background=[("pressed", p["accent"]), ("active", p["field"])],
               foreground=[("pressed", p["selection_text"])],
               bordercolor=[("focus", p["accent"])])

    for clase in ("TEntry", "TCombobox", "TSpinbox"):
        estilo.configure(clase, fieldbackground=p["field"], foreground=p["text"],
                         bordercolor=p["border"], arrowcolor=p["text"],
                         insertcolor=p["text"], padding=4)
        estilo.map(clase, bordercolor=[("focus", p["accent"])],
                   fieldbackground=[("readonly", p["field"])],
                   foreground=[("readonly", p["text"])])
    # The Combobox's drop-down list is a classic Tk listbox behind the scenes and
    # ttk cannot reach it: it is configured through the option database or it
    # stays white on a dark window.
    for opcion, color in (("*TCombobox*Listbox.background", p["field"]),
                          ("*TCombobox*Listbox.foreground", p["text"]),
                          ("*TCombobox*Listbox.selectBackground", p["selection"]),
                          ("*TCombobox*Listbox.selectForeground", p["selection_text"])):
        root.option_add(opcion, color)

    estilo.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
    estilo.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"],
                     padding=(14, 7), bordercolor=p["border"])
    estilo.map("TNotebook.Tab",
               background=[("selected", p["surface"])],
               foreground=[("selected", p["text"])])

    estilo.configure("Treeview", background=p["surface"], fieldbackground=p["surface"],
                     foreground=p["text"], bordercolor=p["border"], rowheight=22)
    estilo.map("Treeview",
               background=[("selected", p["selection"])],
               foreground=[("selected", p["selection_text"])])
    estilo.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
    estilo.map("TCheckbutton", background=[("active", p["bg"])])
    estilo.configure("TScrollbar", background=p["surface"], troughcolor=p["bg"],
                     bordercolor=p["border"], arrowcolor=p["muted"])

    if dark:
        _barra_de_titulo_oscura(root)
    return p
