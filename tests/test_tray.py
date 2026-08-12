"""La bandeja es pegamento contra Win32 y no se testea entera, pero las
partes que son logica pura si: el texto del tooltip es lo unico que el
usuario ve sin abrir el menu."""
from vmaxpanel import tray


class FakeApp:
    def __init__(self, **state):
        self.profile_path = "perfil.json"
        self._state = state

    def state(self):
        return dict(self._state)


def _tip_with(app):
    t = tray.Tray.__new__(tray.Tray)
    t.app = app
    return t._tip()


def test_tooltip_says_paused():
    assert "pausa" in _tip_with(FakeApp(paused=True, running=False))


def test_tooltip_says_stopped():
    assert "detenido" in _tip_with(FakeApp(paused=False, running=False)).lower()


def test_tooltip_shows_the_profile_and_the_frame_count():
    tip = _tip_with(FakeApp(paused=False, running=True, profile="Vitals",
                            frames=1234))
    assert "Vitals" in tip and "1234" in tip


def test_tooltip_never_exceeds_the_win32_limit():
    """szTip es WCHAR[128]: un texto mas largo se corta solo o revienta al
    asignarlo a la estructura."""
    tip = _tip_with(FakeApp(paused=False, running=True, profile="P" * 400,
                            frames=1))
    assert len(tip) <= 127
