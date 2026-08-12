"""Tests de la ventana del editor.

La UI es pegamento, pero el pegamento tambien se rompe: seleccionar un widget
en la lista no actualizaba el panel de propiedades porque el metodo del
callback no existia, y Tkinter se come la AttributeError del callback e
imprime a stderr -- que bajo pythonw no va a ninguna parte. Estos tests
construyen la ventana de verdad y ejercitan los callbacks.
"""
import json

import pytest

from vmaxpanel.editor import EditorState

PROFILE = "vmaxpanel/profiles/vitals.json"


ORIGINAL = open(PROFILE, encoding="utf-8").read()


@pytest.fixture(scope="module")
def _ventana(tmp_path_factory):
    """UNA sola raiz de Tk para todo el modulo.

    Crear y destruir un Tk() por test hacia fallar dos de siete con "Tcl
    wasn't installed properly" -- que es lo que Tkinter dice cuando se abusa
    de las raices, no un problema de instalacion. Peor todavia: con el skip
    defensivo, los tests que fallaban se SALTEABAN, o sea que la cobertura se
    perdia en silencio. Una raiz sola y el estado restaurado entre tests da
    aislamiento sin tocar como se construye la ventana en produccion.
    """
    pytest.importorskip("tkinter")
    from vmaxpanel.editor import EditorWindow

    path = tmp_path_factory.mktemp("editor") / "editando.json"
    path.write_text(ORIGINAL, encoding="utf-8")
    w = EditorWindow(EditorState(path))
    w.root.update()
    yield w
    try:
        w.root.destroy()
    except Exception:
        pass


@pytest.fixture
def ventana(_ventana):
    """Devuelve la ventana con el perfil como recien abierto."""
    _ventana.state.path.write_text(ORIGINAL, encoding="utf-8")
    _ventana._discard()
    _ventana.root.update()
    return _ventana


def seleccionar(w, wid):
    ids = w.state.widget_ids()
    w.lista.selection_clear(0, "end")
    w.lista.selection_set(ids.index(wid))
    w.lista.event_generate("<<ListboxSelect>>")
    w.root.update()


def test_selecting_a_widget_shows_its_own_properties(ventana):
    seleccionar(ventana, "ssd-2")
    assert ventana._selected() == "ssd-2"
    assert ventana._fields["metric"].get() == "disk.temp.2"
    assert ventana._fields["x"].get() == "162"


def test_switching_selection_replaces_the_properties(ventana):
    seleccionar(ventana, "clock")
    assert ventana._fields["metric"].get() == "clock.time"
    seleccionar(ventana, "cpu-bar")
    assert ventana._fields["metric"].get() == "cpu.load"
    assert "format" not in ventana._fields          # una barra no tiene format
    assert ventana._fields["w"].get() == "272"


def test_editing_a_field_reaches_the_state(ventana):
    seleccionar(ventana, "cpu-load")
    ventana._fields["x"].set("99")
    ventana._apply("x")
    assert ventana.state.widget("cpu-load")["x"] == 99
    assert ventana.state.dirty is True


def test_moving_with_the_buttons_moves_the_selected_widget(ventana):
    seleccionar(ventana, "cpu-load")
    x0 = ventana.state.widget("cpu-load")["x"]
    ventana._move(10, -5)
    assert ventana.state.widget("cpu-load")["x"] == x0 + 10


def test_a_broken_callback_is_reported_instead_of_swallowed(ventana):
    """Tkinter imprime las excepciones de callback a stderr y sigue. Bajo
    pythonw eso es un fallo invisible: la ventana queda sin responder y nada
    lo dice."""
    ventana.root.report_callback_exception(ValueError, ValueError("boom"), None)
    assert "boom" in ventana.estado.cget("text")


def test_adding_a_widget_selects_it_and_shows_its_properties(ventana):
    ventana._add("rect")
    assert ventana._selected() == "rect-nuevo"
    assert "fill" in ventana._fields


def test_saving_from_the_window_persists(ventana):
    seleccionar(ventana, "cpu-load")
    ventana._fields["color"].set("#00FF00")
    ventana._apply("color")
    ventana._save()
    en_disco = json.loads(ventana.state.path.read_text(encoding="utf-8"))
    assert [w for w in en_disco["widgets"]
            if w["id"] == "cpu-load"][0]["color"] == "#00FF00"


def test_a_real_mouse_click_on_the_list_selects_that_widget(ventana):
    """El camino del usuario, con un clic de verdad en vez de un
    <<ListboxSelect>> sintetico.

    Importa la diferencia: event_generate de un evento virtual NO se despacha
    si la ventana todavia no esta mapeada, asi que un test que solo usa el
    evento sintetico pasa o no segun cuando se llamo a update() -- y una
    verificacion a mano con ese metodo me hizo creer que el fix no andaba
    cuando en realidad andaba.
    """
    ventana.lista.selection_clear(0, "end")
    ventana.lista.see(0)
    ventana.root.update()

    fila = ventana.state.widget_ids().index("cpu-load")
    ventana.lista.see(fila)
    ventana.root.update()
    caja = ventana.lista.bbox(fila)
    assert caja is not None, "la fila no esta visible; see() no alcanzo"
    x, y = caja[0] + 5, caja[1] + 2
    ventana.lista.event_generate("<Button-1>", x=x, y=y)
    ventana.lista.event_generate("<ButtonRelease-1>", x=x, y=y)
    ventana.root.update()

    assert ventana._selected() == "cpu-load"
    assert ventana._fields["metric"].get() == "cpu.load"


def test_arrows_move_the_widget_only_when_not_editing_a_field(ventana):
    """Las flechas estan bindeadas a la ventana para poder empujar un widget
    de a 1 px. Pero con el foco en un campo de texto tienen que mover el
    cursor y NADA MAS: escribir "182" en x y que la flecha izquierda
    desplace el widget mientras corriges un digito es corrupcion silenciosa
    del layout."""
    seleccionar(ventana, "cpu-load")
    x0 = ventana.state.widget("cpu-load")["x"]

    campos = [c for c in ventana.props.winfo_children()
              if c.winfo_class() in ("TEntry", "Entry")]
    campos[0].focus_set()
    ventana.root.update()
    campos[0].event_generate("<Left>")
    ventana.root.update()
    assert ventana.state.widget("cpu-load")["x"] == x0, \
        "la flecha movio el widget mientras se editaba un campo"

    ventana.lista.focus_set()
    ventana.root.update()
    ventana.lista.event_generate("<Left>")
    ventana.root.update()
    assert ventana.state.widget("cpu-load")["x"] == x0 - 1
