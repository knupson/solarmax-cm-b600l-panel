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


def test_the_fps_submenu_offers_the_measured_options():
    """El costo de cada cadencia va en la etiqueta: elegir 60 fps sin saber
    que son 37% de un nucleo, continuo, no es elegir."""
    class AppConFps(FakeApp):
        def fps_options(self):
            return [(1, "1 fps · 1% de un núcleo"), (30, "30 fps · 17% de un núcleo"),
                    (60, "60 fps · 37% de un núcleo")]

        def fps(self):
            return 30

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppConFps(paused=False, running=True)
    entradas = t._fps_entries()
    assert [cmd for cmd, _, _ in entradas] == [tray.CMD_FPS_BASE + i for i in range(3)]
    assert [marcado for _, _, marcado in entradas] == [False, True, False]
    assert "37%" in entradas[2][1]


def test_picking_an_fps_writes_it_through_the_app(monkeypatch):
    pedidos = []

    class AppConFps(FakeApp):
        def fps_options(self):
            return [(1, "1 fps"), (30, "30 fps"), (60, "60 fps")]

        def fps(self):
            return 1

        def set_fps(self, v):
            pedidos.append(v)
            return []

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppConFps(paused=False, running=True)
    t._editor = None
    t._dispatch(tray.CMD_FPS_BASE + 2)
    assert pedidos == [60]


def test_the_fps_commands_do_not_collide_with_the_other_menu_ids():
    """Los ids de fps son CMD_FPS_BASE + indice: si se solapan con CMD_QUIT,
    elegir un fps cierra la app."""
    fijos = {tray.CMD_STATE, tray.CMD_TOGGLE, tray.CMD_EDITOR, tray.CMD_PROFILE,
             tray.CMD_LOG, tray.CMD_RESTART, tray.CMD_QUIT}
    for i in range(16):
        assert tray.CMD_FPS_BASE + i not in fijos


def test_the_fps_picker_is_refused_while_the_editor_is_open():
    """Los dos escriben el mismo perfil: el editor guarda su copia en memoria y
    se llevaria puesto el fps recien elegido. Mismo motivo por el que la
    bandeja no abre dos editores."""
    pedidos = []

    class AppConFps(FakeApp):
        def fps_options(self):
            return [(1, "1 fps"), (60, "60 fps")]

        def fps(self):
            return 1

        def set_fps(self, v):
            pedidos.append(v)
            return []

    class EditorVivo:
        def poll(self):
            return None            # sigue corriendo

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppConFps(paused=False, running=True)
    t._editor = EditorVivo()
    t._dispatch(tray.CMD_FPS_BASE + 1)
    assert pedidos == [], "escribio el perfil con el editor abierto"
    assert t._editor_abierto() is True


def test_the_profile_submenu_marks_the_current_one(monkeypatch, tmp_path):
    a = tmp_path / "vitals.json"
    b = tmp_path / "apex.json"
    for f in (a, b):
        f.write_text("{}", encoding="utf-8")

    class AppConPerfiles(FakeApp):
        def __init__(self, **st):
            super().__init__(**st)
            self.profile_path = b
            self.pedidos = []

        def profiles(self):
            return [b, a]

        def set_profile(self, p):
            self.pedidos.append(p)
            return []

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppConPerfiles(paused=False, running=True)
    t._editor = None
    entradas = t._profile_entries()
    assert [e[1] for e in entradas] == ["apex", "vitals"]
    assert [e[2] for e in entradas] == [True, False]
    t._dispatch(tray.CMD_PROFILE_BASE + 1)
    assert t.app.pedidos == [a]


def test_the_profile_and_fps_command_ranges_do_not_overlap():
    for i in range(32):
        assert tray.CMD_PROFILE_BASE + i not in range(tray.CMD_FPS_BASE,
                                                      tray.CMD_FPS_BASE + 64)


def test_the_menu_lists_the_problems_reported_by_the_app():
    """Un problema que el usuario no puede ver es un problema que no existe
    hasta que lo confunde: el perfil rechazado quedaba solo en el log."""
    class AppConProblemas(FakeApp):
        def problems(self):
            return ["perfil rechazado: metrica desconocida 'x'",
                    "fuente no encontrada: Bahnschrift",
                    "sin datos: cpu.power"]

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppConProblemas(paused=False, running=True)
    lineas = t._problem_lines()
    assert len(lineas) == 3
    assert any("rechazado" in x for x in lineas)
    for x in lineas:
        assert len(x) <= 74, x       # una entrada de menu no puede ser un parrafo


def test_no_problems_shows_a_single_ok_line():
    class AppSana(FakeApp):
        def problems(self):
            return []

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppSana(paused=False, running=True)
    assert t._problem_lines() == []


def test_too_many_problems_are_capped_with_a_counter():
    """Veinte avisos convierten el menu en un muro ilegible."""
    class AppRota(FakeApp):
        def problems(self):
            return [f"problema {i}" for i in range(20)]

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppRota(paused=False, running=True)
    lineas = t._problem_lines()
    assert len(lineas) <= 5
    assert "16" in lineas[-1] or "mas" in lineas[-1].lower()


def test_the_brightness_submenu_marks_the_current_value():
    class AppConBrillo(FakeApp):
        def brightness_options(self):
            return [(25, "25%"), (50, "50%"), (100, "100%")]

        def brightness(self):
            return 50

        def set_brightness(self, v):
            self.pedido = v
            return []

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppConBrillo(paused=False, running=True)
    t._editor = None
    entradas = t._brightness_entries()
    assert [e[2] for e in entradas] == [False, True, False]
    t._dispatch(tray.CMD_BRIGHT_BASE + 2)
    assert t.app.pedido == 100


def test_the_export_command_reaches_the_app(monkeypatch, tmp_path, capsys):
    """El item de menu tiene que llegar a app.export_profile y, si salio bien, abrir
    la carpeta: la bandeja no tiene ventana donde escribir un mensaje, asi que un
    export silencioso es indistinguible de un boton que no hace nada."""
    llamadas, abiertos = [], []

    class AppQueExporta(FakeApp):
        def export_profile(self):
            llamadas.append(True)
            return tmp_path / "salidas" / "x.vmaxpanel", "exportado a x.vmaxpanel"

    monkeypatch.setattr(tray, "_open_with_shell", abiertos.append)
    t = tray.Tray.__new__(tray.Tray)
    t.app = AppQueExporta(paused=False, running=True)
    t._exportar()
    assert llamadas == [True]
    assert abiertos == [tmp_path / "salidas"]
    assert "x.vmaxpanel" in capsys.readouterr().out


def test_a_failed_export_does_not_open_anything(monkeypatch, capsys):
    abiertos = []

    class AppQueFalla(FakeApp):
        def export_profile(self):
            return None, "no se pudo exportar: el perfil no es valido"

    monkeypatch.setattr(tray, "_open_with_shell", abiertos.append)
    t = tray.Tray.__new__(tray.Tray)
    t.app = AppQueFalla(paused=False, running=True)
    t._exportar()
    assert abiertos == []
    assert "no se pudo" in capsys.readouterr().out
