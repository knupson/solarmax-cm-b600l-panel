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


def test_the_tray_does_not_open_a_second_editor(monkeypatch):
    """Dos editores sobre el mismo perfil se pisan los guardados: gana el
    ultimo y el otro cree que guardo. Aparecio de verdad -- quedaron dos
    ventanas abiertas sobre vitals.json."""
    lanzados = []

    class FakeProc:
        def __init__(self):
            self.vivo = True

        def poll(self):
            return None if self.vivo else 0

    def fake_popen(*args, **kw):
        p = FakeProc()
        lanzados.append(p)
        return p

    monkeypatch.setattr(tray.subprocess, "Popen", fake_popen)
    t = tray.Tray.__new__(tray.Tray)
    t.app = FakeApp(paused=False, running=True)
    t._editor = None

    t._default_editor()
    assert len(lanzados) == 1
    t._default_editor()
    assert len(lanzados) == 1, "abrio un segundo editor con uno ya vivo"

    lanzados[0].vivo = False          # el usuario lo cerro
    t._default_editor()
    assert len(lanzados) == 2, "no volvio a abrir despues de que se cerro"


def test_the_icon_asset_ships_and_has_a_small_size_layer():
    """Windows toma la capa que mas se acerca al tamano pedido. Sin una capa
    de 16 px reduce la de 256 y en la bandeja queda un borron gris -- que es
    como se veia antes, un espacio en blanco."""
    from PIL import Image
    assert tray.ICONO.exists(), f"falta el asset {tray.ICONO}"
    with Image.open(tray.ICONO) as im:
        tamanos = set(im.info.get("sizes", []))
    assert (16, 16) in tamanos
    assert (32, 32) in tamanos
    assert (256, 256) in tamanos
