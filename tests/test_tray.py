"""The tray is glue against Win32 and is not tested in full, but the parts that
are pure logic are: the tooltip text is the only thing the user sees without
opening the menu."""
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
    assert "paused" in _tip_with(FakeApp(paused=True, running=False))


def test_tooltip_says_stopped():
    assert "stopped" in _tip_with(FakeApp(paused=False, running=False)).lower()


def test_tooltip_shows_the_profile_and_the_frame_count():
    tip = _tip_with(FakeApp(paused=False, running=True, profile="Vitals",
                            frames=1234))
    assert "Vitals" in tip and "1234" in tip


def test_tooltip_never_exceeds_the_win32_limit():
    """szTip is WCHAR[128]: longer text either truncates itself or blows up on
    assignment to the structure."""
    tip = _tip_with(FakeApp(paused=False, running=True, profile="P" * 400,
                            frames=1))
    assert len(tip) <= 127


def test_the_tray_does_not_open_a_second_editor(monkeypatch):
    """Two editors on the same profile clobber each other's saves: the last one
    wins and the other believes it saved. It really happened -- two windows were
    left open on vitals.json."""
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
    assert len(lanzados) == 1, "it opened a second editor with one already alive"

    lanzados[0].vivo = False          # the user closed it
    t._default_editor()
    assert len(lanzados) == 2, "it did not reopen after that one closed"


def test_the_icon_asset_ships_and_has_a_small_size_layer():
    """Windows takes the layer closest to the requested size. Without a 16 px layer
    it shrinks the 256 one and the tray gets a grey smudge -- which is how it looked
    before, a blank space."""
    from PIL import Image
    assert tray.ICONO.exists(), f"the asset {tray.ICONO} is missing"
    with Image.open(tray.ICONO) as im:
        tamanos = set(im.info.get("sizes", []))
    assert (16, 16) in tamanos
    assert (32, 32) in tamanos
    assert (256, 256) in tamanos


def test_the_fps_submenu_offers_the_measured_options():
    """Each cadence's cost goes in the label: choosing 60 fps without knowing it is
    37% of one core, continuously, is not choosing."""
    class AppConFps(FakeApp):
        def fps_options(self):
            return [(1, "1 fps · 1% of one core"), (30, "30 fps · 17% of one core"),
                    (60, "60 fps · 37% of one core")]

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
    """The fps ids are CMD_FPS_BASE + index: if they overlap CMD_QUIT, picking an
    fps closes the app."""
    fijos = {tray.CMD_STATE, tray.CMD_TOGGLE, tray.CMD_EDITOR, tray.CMD_PROFILE,
             tray.CMD_LOG, tray.CMD_RESTART, tray.CMD_QUIT}
    for i in range(16):
        assert tray.CMD_FPS_BASE + i not in fijos


def test_the_fps_picker_is_refused_while_the_editor_is_open():
    """Both write the same profile: the editor saves its in-memory copy and would
    wipe out the fps just chosen. Same reason the
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
    assert pedidos == [], "it wrote the profile with the editor open"
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
    """A problem the user cannot see is a problem that does not exist until it
    confuses them: the rejected profile stayed only in the log."""
    class AppConProblemas(FakeApp):
        def problems(self):
            return ["perfil rechazado: metrica desconocida 'x'",
                    "fuente no encontrada: Bahnschrift",
                    "no data: cpu.power"]

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppConProblemas(paused=False, running=True)
    lineas = t._problem_lines()
    assert len(lineas) == 3
    assert any("rechazado" in x for x in lineas)
    for x in lineas:
        assert len(x) <= 74, x       # a menu entry cannot be a paragraph


def test_no_problems_shows_a_single_ok_line():
    class AppSana(FakeApp):
        def problems(self):
            return []

    t = tray.Tray.__new__(tray.Tray)
    t.app = AppSana(paused=False, running=True)
    assert t._problem_lines() == []


def test_too_many_problems_are_capped_with_a_counter():
    """Twenty warnings turn the menu into an illegible wall."""
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
    """The menu item has to reach app.export_profile and, if it went well, open the
    folder: the tray has no window to write a message in, so a silent export is
    indistinguishable from a button that does nothing."""
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
            return None, "could not export: the profile is not valid"

    monkeypatch.setattr(tray, "_open_with_shell", abiertos.append)
    t = tray.Tray.__new__(tray.Tray)
    t.app = AppQueFalla(paused=False, running=True)
    t._exportar()
    assert abiertos == []
    assert "could not" in capsys.readouterr().out


# --- the icon has to really exist ---


class FakeShell:
    """A fake shell32: it counts the NIM_ADDs and can fail the first N."""

    def __init__(self, fallar=0):
        self.adds = 0
        self.fallar = fallar
        self.mensajes = []

    def Shell_NotifyIconW(self, accion, _nid):
        self.mensajes.append(accion)
        if accion == tray.NIM_ADD:
            self.adds += 1
            return 0 if self.adds <= self.fallar else 1
        return 1


def _tray_con(shell, monkeypatch, app=None):
    monkeypatch.setattr(tray, "shell32", shell)
    t = tray.Tray.__new__(tray.Tray)
    t.app = app or FakeApp(paused=False, running=True)
    t._editor = None
    t._hwnd = 1234
    t._nid = None
    t._icono_puesto = False
    return t


def test_a_rejected_icon_is_retried_and_reported(monkeypatch, capsys):
    """Shell_NotifyIcon's return value used to be ignored. Windows really does reject
    it when the tray is not ready yet -- and the scheduled task starts this AT LOGON,
    which is exactly that moment. With no icon there is no menu, no pause, no editor:
    the app is left drawing with no way to manage it."""
    shell = FakeShell(fallar=2)
    t = _tray_con(shell, monkeypatch)
    t._add_icon()
    assert shell.adds >= 3, "no reintento"
    assert t._icono_puesto is True
    assert "tray" in capsys.readouterr().out.lower()


def test_an_icon_that_never_gets_accepted_says_so(monkeypatch, capsys):
    shell = FakeShell(fallar=99)
    t = _tray_con(shell, monkeypatch)
    t._add_icon()
    assert t._icono_puesto is False
    # To stderr, not stdout: it is a failure, and the tray log merges both.
    salida = capsys.readouterr().err.lower()
    assert "could not" in salida
    assert "python -m vmaxpanel.editor" in salida, "with no icon, say the plan B"


def test_the_icon_is_added_again_when_explorer_restarts(monkeypatch):
    """When explorer restarts -- it happens, and it is not rare -- Windows sends
    TaskbarCreated and EACH app has to add its icon again. Without that the panel
    keeps drawing but the icon never comes back: the user is left with no menu until
    the next logon."""
    shell = FakeShell()
    t = _tray_con(shell, monkeypatch)
    t._add_icon()
    assert shell.adds == 1
    t._on_message(t._hwnd, tray.WM_TASKBARCREATED, 0, 0)
    assert shell.adds == 2, "it did not put the icon back"


# --- that the icon is SEEN, not merely that it exists ---


def test_the_icon_promotes_itself_when_windows_hid_it_by_default():
    r"""Windows 11 hides EVERY new icon: it adds it to the overflow menu and not to
    the taskbar. Verified in practice -- the entry existed under
    HKCU\Control Panel\NotifyIconSettings with the tooltip "VMax Panel" and no
    IsPromoted, and the user never saw the icon in months of use. For an app whose
    ONLY interface is that icon, staying hidden means having no interface."""
    entradas = {"111": {"ExecutablePath": r"C:\py\pythonw.exe",
                        "InitialTooltip": "VMax Panel - detenido"},
                "222": {"ExecutablePath": r"C:\another\app.exe",
                        "InitialTooltip": "Otra cosa"}}
    escritos = {}
    puesto = tray.promover_icono(r"C:\py\pythonw.exe", "VMax Panel",
                                 leer=lambda: entradas,
                                 escribir=lambda k, v: escritos.__setitem__(k, v))
    assert puesto is True
    assert escritos == {"111": 1}, "it promoted the wrong entry, or none"


def test_an_icon_the_user_hid_on_purpose_is_left_alone():
    """If the value is 0, the user turned it off by hand in Settings. Turning it back
    on at every start-up would be fighting their decision, which is exactly what
    makes software that thinks it is important unbearable."""
    entradas = {"111": {"ExecutablePath": r"C:\py\pythonw.exe",
                        "InitialTooltip": "VMax Panel", "IsPromoted": 0}}
    escritos = {}
    puesto = tray.promover_icono(r"C:\py\pythonw.exe", "VMax Panel",
                                 leer=lambda: entradas,
                                 escribir=lambda k, v: escritos.__setitem__(k, v))
    assert puesto is False
    assert escritos == {}


def test_an_already_visible_icon_is_not_rewritten():
    entradas = {"111": {"ExecutablePath": r"C:\py\pythonw.exe",
                        "InitialTooltip": "VMax Panel", "IsPromoted": 1}}
    escritos = {}
    assert tray.promover_icono(r"C:\py\pythonw.exe", "VMax Panel",
                               leer=lambda: entradas,
                               escribir=lambda k, v: escritos.__setitem__(k, v)) is False
    assert escritos == {}


def test_promoting_never_raises_if_the_registry_is_not_there():
    """It is cosmetic: a Windows version without that key, or a denied permission,
    must not stop the tray from starting."""
    def leer_roto():
        raise OSError("the key does not exist")
    assert tray.promover_icono("x", "y", leer=leer_roto,
                               escribir=lambda k, v: None) is False


def test_after_promoting_the_icon_is_added_again(monkeypatch):
    """Windows decides whether the icon goes to the taskbar or the overflow menu AT
    THE MOMENT of the NIM_ADD. Promoting it afterwards does not move it on its own,
    so it has to be added again for the taskbar to re-evaluate it: without that the
    change is only seen at the next start-up.
    """
    shell = FakeShell()
    t = _tray_con(shell, monkeypatch)
    monkeypatch.setattr(tray, "promover_icono", lambda *a, **kw: True)
    t._add_icon()
    assert shell.adds == 2, "it did not add the icon again after promoting it"
    assert tray.NIM_DELETE in shell.mensajes, "it did not delete the previous one: there would be two"


def test_without_promotion_the_icon_is_added_once(monkeypatch):
    shell = FakeShell()
    t = _tray_con(shell, monkeypatch)
    monkeypatch.setattr(tray, "promover_icono", lambda *a, **kw: False)
    t._add_icon()
    assert shell.adds == 1
